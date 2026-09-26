"""AI endpoints: summarize, auto-tag, suggest-links, chat."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ...config import PyriteConfig
from ...exceptions import QuerySyntaxError
from ...services.access_policy import KB, UNSCOPED, Action, ReadScope
from ...services.auth_service import AuthService
from ...services.kb_service import KBService
from ...services.link_discovery_service import LinkDiscoveryService
from ...services.llm_service import LLMService
from ...services.llm_usage_service import LLMUsageService
from ...services.quota_service import QuotaService
from ...services.search_service import SearchService, build_or_query, clip_semantic_text
from ..api import (
    get_auth_service,
    get_config,
    get_kb_service,
    get_llm_service,
    get_llm_usage_service,
    get_search_service,
    get_user_llm_context,
    limiter,
    requires_tier,
)
from ..authz import authorize
from ..schemas import (
    AIAutoTagResponse,
    AIChatRequest,
    AIEntryRequest,
    AILinkSuggestion,
    AILinkSuggestResponse,
    AISummarizeResponse,
    AITagSuggestion,
)

logger = logging.getLogger(__name__)

# authorize(Action.KB_READ, KB) on the router, not per-route: every route
# here names a KB in its body (`kb_name`, or `kb` for chat), and all four both read
# an entry and feed retrieval. The write-tier check alone was not enough
# -- a caller with global write tier but no grant on a private KB passed
# it, then had the entry's body summarised back to them.
#
# Because the KB is named in the body, the dependency reads the request
# body to find it. That is safe: Starlette caches the body on the request,
# so the handler's own parsing of `req` sees the same bytes. A body the
# dependency cannot parse is refused (400) rather than treated as naming
# no KB at all -- "names none" is exactly what lets a request through.
router = APIRouter(
    prefix="/ai",
    tags=["AI"],
    dependencies=[Depends(requires_tier("write")), Depends(authorize(Action.KB_READ, KB))],
)


def _resolve_llm(llm: LLMService, user_ctx: dict | None) -> LLMService:
    """Apply user BYOK context to the LLM service if available."""
    if user_ctx and user_ctx.get("api_key"):
        return llm.with_user_key(
            api_key=user_ctx["api_key"],
            provider=user_ctx.get("provider"),
            model=user_ctx.get("model") or None,
        )
    return llm


def _require_configured(llm: LLMService) -> None:
    """Raise 503 if AI provider is not configured."""
    status = llm.status()
    if not status["configured"]:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "AI_NOT_CONFIGURED",
                "message": "AI provider is not configured",
                "hint": "Configure an AI provider in Settings → AI Provider, or set your own key in Settings → My API Keys",
            },
        )


def _get_entry(svc: KBService, entry_id: str, kb_name: str) -> dict:
    """Fetch an entry or raise 404."""
    # Named KB, authorized by the route; only body, title and tags are used.
    entry = svc.get_entry(entry_id, kb_name=kb_name, readable_kbs=UNSCOPED)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Entry '{entry_id}' not found"},
        )
    return entry


def _enforce_llm_quota(
    request: Request,
    config: PyriteConfig,
    auth_service: AuthService,
    usage_svc: LLMUsageService,
    kind: str,
) -> None:
    """Raise 429 if the current user is over their tier's daily LLM
    quota. No-op (unlimited) for anonymous requests (auth disabled) or
    when no usage_tiers are configured -- matches QuotaService's
    existing fail-open-on-no-config convention."""
    auth_user = getattr(request.state, "auth_user", None)
    if not auth_user:
        return

    user = auth_service.get_user(auth_user["id"])
    if not user:
        return

    quota_svc = QuotaService(config)
    allowed, message = quota_svc.check_llm_quota(
        user_id=auth_user["id"],
        kind=kind,
        user_tier=user["usage_tier"],
        usage_service=usage_svc,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail={"code": "QUOTA_EXCEEDED", "message": message})


@router.post("/summarize", response_model=AISummarizeResponse)
@limiter.limit("30/minute")
async def ai_summarize(
    request: Request,
    req: AIEntryRequest,
    llm: LLMService = Depends(get_llm_service),
    svc: KBService = Depends(get_kb_service),
    user_ctx: dict | None = Depends(get_user_llm_context),
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
    usage_svc: LLMUsageService = Depends(get_llm_usage_service),
):
    """Generate an AI summary for an entry."""
    llm = _resolve_llm(llm, user_ctx)
    _require_configured(llm)
    _enforce_llm_quota(request, config, auth_service, usage_svc, kind="summarize")
    entry = _get_entry(svc, req.entry_id, req.kb_name)

    body = entry.get("body", "") or ""
    title = entry.get("title", "")
    if not body.strip():
        return AISummarizeResponse(summary="(No content to summarize)")

    system = "You are a knowledge management assistant. Summarize the following entry concisely in 2-3 sentences. Focus on the key points and purpose."
    prompt = f"Title: {title}\n\n{body}"

    try:
        summary = await llm.complete(prompt, system=system, max_tokens=256, kind="summarize")
        return AISummarizeResponse(summary=summary.strip())
    except Exception as e:
        logger.exception("AI summarize failed")
        raise HTTPException(status_code=500, detail={"code": "AI_ERROR", "message": str(e)})


@router.post("/auto-tag", response_model=AIAutoTagResponse)
@limiter.limit("30/minute")
async def ai_auto_tag(
    request: Request,
    req: AIEntryRequest,
    llm: LLMService = Depends(get_llm_service),
    svc: KBService = Depends(get_kb_service),
    user_ctx: dict | None = Depends(get_user_llm_context),
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
    usage_svc: LLMUsageService = Depends(get_llm_usage_service),
):
    """Suggest tags for an entry using AI."""
    llm = _resolve_llm(llm, user_ctx)
    _require_configured(llm)
    _enforce_llm_quota(request, config, auth_service, usage_svc, kind="auto-tag")
    entry = _get_entry(svc, req.entry_id, req.kb_name)

    body = entry.get("body", "") or ""
    title = entry.get("title", "")
    existing_tags = entry.get("tags", [])

    # Get the tag vocabulary
    all_tags_raw = svc.get_tags(kb_name=req.kb_name)
    tag_vocab = [t["name"] for t in all_tags_raw][:200]  # limit to 200 tags

    system = """You are a knowledge management assistant. Suggest relevant tags for the entry.
Return a JSON array of objects with keys: "name" (string), "is_new" (boolean), "reason" (string).
- Prefer existing tags from the vocabulary when they fit.
- Mark tags not in the vocabulary as is_new=true.
- Suggest 3-7 tags total.
- Do not suggest tags the entry already has.
Return ONLY the JSON array, no other text."""

    prompt = f"""Title: {title}

Content:
{body[:3000]}

Existing tags on this entry: {json.dumps(existing_tags)}
Tag vocabulary: {json.dumps(tag_vocab[:100])}"""

    try:
        result = await llm.complete(prompt, system=system, max_tokens=512, kind="auto-tag")
        # Parse JSON from response
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        suggestions = json.loads(result)
        tags = [AITagSuggestion(**s) for s in suggestions if isinstance(s, dict)]
        return AIAutoTagResponse(suggested_tags=tags)
    except (json.JSONDecodeError, TypeError):
        logger.warning("AI auto-tag returned non-JSON: %s", result[:200])
        return AIAutoTagResponse(suggested_tags=[])
    except Exception as e:
        logger.exception("AI auto-tag failed")
        raise HTTPException(status_code=500, detail={"code": "AI_ERROR", "message": str(e)})


@router.post("/suggest-links", response_model=AILinkSuggestResponse)
@limiter.limit("30/minute")
async def ai_suggest_links(
    request: Request,
    req: AIEntryRequest,
    llm: LLMService = Depends(get_llm_service),
    svc: KBService = Depends(get_kb_service),
    search_svc: SearchService = Depends(get_search_service),
    user_ctx: dict | None = Depends(get_user_llm_context),
    config: PyriteConfig = Depends(get_config),
    auth_service: AuthService = Depends(get_auth_service),
    usage_svc: LLMUsageService = Depends(get_llm_usage_service),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Suggest wikilinks for an entry using AI + search.

    Retrieval is scoped: a suggestion names a target entry's id and
    title, so candidates may only come from KBs the caller may read.
    """
    llm = _resolve_llm(llm, user_ctx)
    _require_configured(llm)
    _enforce_llm_quota(request, config, auth_service, usage_svc, kind="suggest-links")
    entry = _get_entry(svc, req.entry_id, req.kb_name)

    body = entry.get("body", "") or ""
    title = entry.get("title", "")

    kb_names = None if req.kb_name else scope.as_set()
    # Build the query from the title's own words, quoted and OR-joined
    # (same helper LinkDiscoveryService.suggest_links uses) instead of
    # handing the raw title to search(): a real title routinely carries
    # punctuation FTS5 reads as operator syntax -- AND, quotes, a hyphen,
    # a colon -- and a title is not a search the caller wrote, so it must
    # not be able to fail to parse (round-2 cold read on #414/#428; #361).
    derived_query = LinkDiscoveryService.build_suggest_query({"title": title})
    if not derived_query.strip():
        # No words to search on (or an empty title) -- nothing to search,
        # not an empty MATCH.
        related = []
    else:
        try:
            # The semantic leg embeds the title itself, not the OR string
            # (#431).
            related = search_svc.search(
                query=derived_query,
                semantic_query=clip_semantic_text(title),
                kb_name=req.kb_name,
                kb_names=kb_names,
                limit=15,
                mode="hybrid",
            )
        except QuerySyntaxError:
            # Should not happen -- build_suggest_query's quoted-OR tokens
            # can't fail to parse -- but if it ever does, it is
            # deterministic and not retryable in keyword mode either.
            # Kept as defense in depth; propagates to the central handler,
            # which maps it to 400 QUERY_SYNTAX.
            raise
        except Exception:
            # Recovered, but not silently: a storage fault here is the
            # server's problem (#437 cold read).
            logger.warning(
                "suggest-links hybrid search failed; retrying keyword-only", exc_info=True
            )
            related = search_svc.search(
                query=derived_query,
                kb_name=req.kb_name,
                kb_names=kb_names,
                limit=15,
                mode="keyword",
            )

    # Filter out self
    related = [r for r in related if r.get("id") != req.entry_id][:10]

    if not related:
        return AILinkSuggestResponse(suggestions=[])

    candidates = "\n".join(
        f"- [{r['id']}] {r.get('title', '')} ({r.get('entry_type', '')}): {(r.get('snippet') or '')[:100]}"
        for r in related
    )

    system = """You are a knowledge management assistant. Given an entry and a list of candidate entries, suggest which ones should be linked using [[wikilinks]].
Return a JSON array of objects with keys: "target_id" (string), "target_title" (string), "reason" (string).
Only suggest links that are genuinely relevant. Return ONLY the JSON array."""

    prompt = f"""Entry: {title}
Content:
{body[:2000]}

Candidate entries to link:
{candidates}"""

    try:
        result = await llm.complete(prompt, system=system, max_tokens=512, kind="suggest-links")
        result = result.strip()
        if result.startswith("```"):
            result = result.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        suggestions_raw = json.loads(result)
        suggestions = []
        for s in suggestions_raw:
            if isinstance(s, dict) and "target_id" in s:
                suggestions.append(
                    AILinkSuggestion(
                        target_id=s["target_id"],
                        target_kb=req.kb_name,
                        target_title=s.get("target_title", ""),
                        reason=s.get("reason", ""),
                    )
                )
        return AILinkSuggestResponse(suggestions=suggestions)
    except (json.JSONDecodeError, TypeError):
        logger.warning("AI suggest-links returned non-JSON: %s", result[:200])
        return AILinkSuggestResponse(suggestions=[])
    except Exception as e:
        logger.exception("AI suggest-links failed")
        raise HTTPException(status_code=500, detail={"code": "AI_ERROR", "message": str(e)})


@router.post("/chat")
@limiter.limit("30/minute")
async def ai_chat(
    request: Request,
    req: AIChatRequest,
    llm: LLMService = Depends(get_llm_service),
    svc: KBService = Depends(get_kb_service),
    search_svc: SearchService = Depends(get_search_service),
    user_ctx: dict | None = Depends(get_user_llm_context),
    scope: ReadScope = Depends(authorize(Action.KB_READ, KB)),
):
    """Chat with your knowledge base using RAG. Returns SSE stream.

    With no ``kb`` the RAG step searches every KB and quotes what it
    finds into the prompt and the `sources` event, so retrieval is scoped
    to the readable set. The per-hit entry fetch is checked too: the
    search already filtered, but a fetch that trusts a search result is
    exactly the shape that regresses.
    """
    llm = _resolve_llm(llm, user_ctx)
    _require_configured(llm)

    if not req.messages:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_REQUEST", "message": "No messages provided"}
        )

    last_msg = req.messages[-1].content

    # RAG: search KB for context
    sources = []
    context_text = ""
    kb_names = None if req.kb else scope.as_set()
    try:
        # The message is not a query the caller wrote: search its words,
        # quoted and OR-joined like suggest-links', so it cannot fail to
        # parse, and let the semantic leg embed the message itself (#431).
        # Parsing the message as FTS5 made ordinary English ("hooks and
        # CI?") a syntax error, and clipping it as FTS5 cut "1) ... 2) ..."
        # at the first ")"; the except below then hid both.
        derived_query = build_or_query(last_msg)
        if not derived_query:
            # No words to search on -- nothing to search, not an empty MATCH.
            results = []
        else:
            try:
                results = search_svc.search(
                    query=derived_query,
                    semantic_query=clip_semantic_text(last_msg),
                    kb_name=req.kb,
                    kb_names=kb_names,
                    limit=5,
                    mode="hybrid",
                )
            except QuerySyntaxError:
                # Deterministic and not retryable in keyword mode either --
                # same rationale as suggest-links. The outer except below
                # still catches this, so chat proceeds without context
                # rather than failing the request.
                raise
            except Exception:
                # Recovered, but not silently (#437 cold read).
                logger.warning("chat hybrid search failed; retrying keyword-only", exc_info=True)
                results = search_svc.search(
                    query=derived_query,
                    kb_name=req.kb,
                    kb_names=kb_names,
                    limit=5,
                    mode="keyword",
                )

        for r in results:
            if not scope.permits(r.get("kb_name")):
                continue
            sources.append(
                {
                    "id": r.get("id", ""),
                    "kb_name": r.get("kb_name", ""),
                    "title": r.get("title", ""),
                    "snippet": (r.get("snippet") or "")[:200],
                }
            )
            # Fetch full entry for richer context
            full = svc.get_entry(r["id"], kb_name=r.get("kb_name"), readable_kbs=scope.as_set())
            if full:
                body_preview = (full.get("body") or "")[:500]
                context_text += f"\n---\n[[{r['id']}]] {r.get('title', '')}\n{body_preview}\n"
    except Exception:
        logger.exception("RAG search failed, proceeding without context")

    # If chatting about a specific entry, include it
    if req.entry_id and req.kb:
        entry = svc.get_entry(req.entry_id, kb_name=req.kb, readable_kbs=scope.as_set())
        if entry:
            entry_body = (entry.get("body") or "")[:1500]
            context_text = (
                f"\n---\nCurrent entry [[{req.entry_id}]] {entry.get('title', '')}\n{entry_body}\n"
                + context_text
            )

    system = f"""You are a research assistant for a knowledge base. Answer the user's question using the provided context from the KB.
Cite entries using [[entry-id]] notation. Be concise and helpful.
If the context doesn't contain enough information to fully answer, say so.

KB Context:
{context_text}"""

    # Build full prompt from message history
    history = ""
    for msg in req.messages[:-1]:
        role_label = "User" if msg.role == "user" else "Assistant"
        history += f"{role_label}: {msg.content}\n\n"
    prompt = f"{history}User: {last_msg}" if history else last_msg

    async def event_stream():
        try:
            async for token in llm.stream(prompt, system=system):
                data = json.dumps({"type": "token", "content": token})
                yield f"data: {data}\n\n"

            if sources:
                data = json.dumps({"type": "sources", "entries": sources})
                yield f"data: {data}\n\n"

            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            logger.exception("AI chat stream error")
            data = json.dumps({"type": "error", "message": str(e)})
            yield f"data: {data}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
