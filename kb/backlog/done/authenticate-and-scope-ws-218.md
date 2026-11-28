---
id: authenticate-and-scope-ws-218
title: Authenticate and scope /ws (#218)
type: backlog_item
tags:
- security
importance: 5
kind: bug
status: done
priority: high
assignee: agent:pyrite-worker-218
effort: M
rank: 0
---

# #218 — /ws is unauthenticated and broadcasts private-KB names and entry ids to every connected socket

Found while spiking #201, which asks for `/ws` to be "checked and either scoped or documented with a reason". This is that check, filed separately so #201 stays one theme.

## Finding

`/ws` (`pyrite/server/api.py` :1038-1048) has **no authentication and no scoping of any kind**:

```python
@application.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    from .websocket import manager
    await manager.connect(ws)
```

`manager.broadcast` fans every event out to every connected socket. The payloads carry KB identity:

```
pyrite/server/endpoints/entries.py:777  broadcast_event("entry_created", entry_id=entry.id, kb_name=req.kb)
pyrite/server/endpoints/entries.py:828  broadcast_event("entry_updated", entry_id=entry_id, kb_name=req.kb)
pyrite/server/endpoints/entries.py:906  broadcast_event("entry_deleted", entry_id=entry_id, kb_name=kb)
pyrite/server/endpoints/clipper.py:85   broadcast_event("entry_created", entry_id=entry.id, kb_name=req.kb)
pyrite/server/endpoints/admin.py:90     broadcast_event("kb_synced", entry_id="", kb_name="")
pyrite/server/api.py:919                broadcast_event("index_progress", job_id=..., current=..., total=...)
```

## Exposure

**Metadata, not content.** An unauthenticated caller who opens `/ws` learns the *names* of private KBs and the *ids* of entries created, updated or deleted in them, in real time. No titles, no bodies. This is strictly smaller than #201 (which leaks entry bodies) but it is the same class: the existence of a private KB is itself private, which is exactly why `kb_not_found` is a 404 and not a 403 (`pyrite/server/api.py` :692).

It is also a live-activity oracle: a peer can watch when a private KB is being edited.

## Acceptance

- `/ws` requires authentication, or documents why it does not.
- An event naming a KB reaches only sockets whose owner may read that KB. The readable set is already computable off the request without FastAPI's dependant tree — see the #201 spike comment, which proves `readable_kbs`' core loop is `(config, db, user_id)` only.
- A test: two connected sockets, one with a grant to a private KB and one without; an `entry_created` in that KB reaches only the granted socket.
- `/ws`'s entry in `UNREACHABLE_BY_THIS_WALK` in `tests/test_read_scoping_is_structural.py` is rewritten to say what now covers it, or why nothing needs to.

## Files

- `pyrite/server/api.py` (`websocket_endpoint` :1038)
- `pyrite/server/websocket.py` (`manager`, `broadcast_event` :49)
- `tests/test_read_scoping_is_structural.py` (`UNREACHABLE_BY_THIS_WALK`)



---

## Groom 2026-09-23

**Still reproduces on dev 6e505be: yes.** The line numbers have moved; the code has not changed.
- `pyrite/server/api.py:1205-1214`: `websocket_endpoint` calls `manager.connect(ws)`. There is still no credential check and no Origin check.
- `pyrite/server/websocket.py:27-39`: `broadcast` sends every event to every socket in `_connections`.
- Emitters now: `endpoints/entries.py:856, 907, 985`, `endpoints/clipper.py:85` (all carry `kb_name`), and `endpoints/admin.py:101` (`kb_synced`, `kb_name=""`).
- `tests/test_read_scoping_is_structural.py:118-125` still lists `/ws` as "still NOT scoped".
- 0.25's read scoping (#201) and per-request sessions (#203) did not touch `/ws`.

**Not a spike.** The two pieces this needs both exist on dev and read only headers and cookies, so they work on a `WebSocket` as well as a `Request`:
- `mcp_routes._resolve_credential` (`mcp_routes.py:66`) handles Bearer, `X-API-Key` and the session cookie.
- `api.readable_kbs_for_user` (`api.py:698`) is the single framework-free rule.

The web client (`web/src/lib/api/websocket.ts:29,57`) authenticates by the `pyrite_session` cookie only (SameSite=lax, `auth_endpoints.py:91`). A browser cannot set headers on a WebSocket.

### Acceptance
1. **Handshake auth, same outcomes as REST's `verify_api_key` (`api.py:420`):**
   - an operator API key (header, or `api_key` query param, because browsers cannot set WS headers) is unscoped;
   - a valid `pyrite_session` cookie is scoped to that user;
   - with `auth.enabled` and an `anonymous_tier`, a visitor with no credential is admitted, scoped to what an anonymous caller may read;
   - with auth disabled and no keys configured, the socket is unscoped (the default local install keeps receiving everything, unchanged);
   - otherwise the handshake is rejected (closed before `accept`) and the socket is never added to `_connections`.
2. Identity resolution reuses `_resolve_credential` (or a shared extraction of it) and `readable_kbs_for_user`. **There is no third implementation of the rule** (see the docstring at `api.py:707-712`). The synchronous session lookup runs off the event loop (`run_in_threadpool`, as in `verify_api_key` for #131).
3. **Filtering:**
   - Each connection stores its readable set (`None` = unscoped), resolved once at connect.
   - An event with a non-empty `kb_name` goes only to sockets whose set is `None` or contains that KB.
   - An event with no `kb_name` (`kb_synced`) goes to every accepted socket.
   - `broadcast` itself does no per-event DB work.
4. **Cross-site handshake:** when the `Origin` header is present, the handshake is rejected unless Origin matches the request's own host or is in `settings.cors_origins`. CORS does not apply to WebSockets, so this check is the only cross-origin gate once cookies authenticate the socket.
5. **Tests** (new `tests/test_websocket_scoping.py`, `TestClient.websocket_connect`, no live server):
   - (a) Two sockets, one user with a read grant on a private KB and one without. An `entry_created` in that KB reaches only the granted socket.
   - (b) With auth enabled, no anonymous tier and no credential, the handshake is refused.
   - (c) With auth disabled, a bare socket receives the event.
   - (d) A foreign `Origin` with a valid cookie is refused.
   - (e) `kb_synced` reaches a scoped socket.
6. `/ws`'s entry in `UNREACHABLE_BY_THIS_WALK` (`tests/test_read_scoping_is_structural.py:118`) is rewritten to name `tests/test_websocket_scoping.py` as its cover. The module docstring line "`/ws` is still unscoped and tracked as #218" (:37) is updated to match.
7. A changelog fragment in `changelog.d/`.

**Stated default, which the maintainer may override without blocking dispatch:** the readable set is fixed for the life of the connection. A grant revoked, or a session logged out, takes effect when the socket reconnects. The exposure is metadata only (KB name and entry id), and re-resolving per event would put a DB walk per socket per event on the loop. The PR body must state this.

### Touches
- existing: `pyrite/server/api.py` (`websocket_endpoint`), `pyrite/server/websocket.py` (`ConnectionManager`: per-connection scope, filtered `broadcast`), `pyrite/server/mcp_routes.py` (only if `_resolve_credential` is lifted to a shared spot), `tests/test_read_scoping_is_structural.py`
- new: `tests/test_websocket_scoping.py`, `changelog.d/<n>.security.md` (or whichever fragment type the README names)

### Sequence
Independent of #221 and #207: no shared files. Within the server lane it touches `api.py`, so it runs after any in-flight PR that edits `api.py`'s app factory. None of the three groomed today does.

### Model / weight / review
- **Model: opus.** Auth on a server surface, the choice of shared resolver, and the Origin rule.
- **heavy: no.** TestClient only; no server, browser or model.
- **Cold read: yes.** Auth plus a change to what a public endpoint accepts. The reviewer should check that the no-auth default is unchanged and that no path adds a socket before auth.

### Out of scope
- `index_progress` (`api.py:1049-1056`). Its callback runs on the IndexWorker thread, where `get_running_loop()` raises, so it is **never actually delivered** today. Do not fix that here. If it is ever fixed, it carries whole-index counts and should go to unscoped sockets only; file it separately.
- Changing the web client's reconnect loop on an auth close (`websocket.ts` backs off to 30 s and keeps retrying). That is a web change for another PR.
- Re-checking grants per event or pushing revocations to open sockets.
- Adding titles or bodies to events, or any new event types.
