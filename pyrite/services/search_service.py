"""
Search Service

Unified search operations with FTS5 query sanitization and hybrid search.
Used by API, CLI, and UI layers.
"""

import logging
import re
import sqlite3
import time
from enum import StrEnum
from typing import Any

from ..exceptions import (
    QuerySyntaxError,
    QueryTooLongError,
    StorageBusyError,
    StorageError,
    ValidationError,
)
from ..storage.database import PyriteDB

logger = logging.getLogger(__name__)

#: The longest search query, in characters, that any surface accepts.
MAX_SEARCH_QUERY_LENGTH = 1000

#: Substrings SQLite/FTS5 actually use for a *parse* failure -- as opposed to
#: a storage failure ("database is locked", a disk I/O error, a missing
#: table, a file that can't be opened) that also happens to raise
#: sqlite3.OperationalError. Checked with `in` against the lowercased error
#: text; anything that doesn't match one of these is not the caller's query
#: being bad.
_QUERY_SYNTAX_ERROR_MARKERS = (
    "fts5:",  # "fts5: syntax error near ...", "fts5: parser stack overflow"
    "unterminated string",
    "unknown special query",
    "expected integer",  # NEAR(a b, <not an int>)
)


def _looks_like_query_syntax_error(message: str, query: str) -> bool:
    """Is this OperationalError message a parse error in the query that was sent?

    Whitelist, not blacklist: only the shapes SQLite is known to use for a
    query the caller actually wrote wrong are reclassified as
    QuerySyntaxError. Everything else (a locked database, disk I/O, a
    missing table, a file that can't be opened) stays a storage fault the
    caller did nothing to cause.

    ``no such column: <name>`` is the one ambiguous shape: FTS5 uses it for a
    ``col:term`` filter in the query, and SQLite uses it for a column the SQL
    itself names that the schema lacks. The caller's fault iff ``<name>``
    appears in the query as a whole token or a separator-delimited piece of
    one (``party`` in ``third-party``, ``a.b`` in ``"a.b":y``). A name the
    query never mentions (``e.fips`` for the query ``fips``) is schema drift
    (#431).
    """
    lowered = message.lower()
    if lowered.startswith("no such column:"):
        name = lowered.split(":", 1)[1].strip()
        if not name:
            return False
        pattern = r"(?<![\w.])" + re.escape(name) + r"(?![\w.])"
        return re.search(pattern, query.lower()) is not None
    return any(marker in lowered for marker in _QUERY_SYNTAX_ERROR_MARKERS)


#: SQLite's primary result codes for a transient lock: another connection
#: holds the database (SQLITE_BUSY) or a table (SQLITE_LOCKED).
_TRANSIENT_SQLITE_CODES = frozenset({sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED})

#: The same faults by message, for an error that lost ``sqlite_errorcode``
#: (constructed by a wrapper rather than raised by SQLite).
_TRANSIENT_SQLITE_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


def _is_transient(error: sqlite3.Error) -> bool:
    """Could the same call succeed if made again? Only for a lock (#431)."""
    code = getattr(error, "sqlite_errorcode", None)
    if code is not None:
        # Extended codes (SQLITE_BUSY_SNAPSHOT, ...) carry the primary code in
        # the low byte.
        return (code & 0xFF) in _TRANSIENT_SQLITE_CODES
    lowered = str(error).lower()
    return any(marker in lowered for marker in _TRANSIENT_SQLITE_MARKERS)


def storage_error_from(error: sqlite3.Error, what: str = "Search failed") -> StorageError:
    """The ``StorageError`` for a sqlite failure that is not the caller's query.

    A lock becomes ``StorageBusyError`` (retryable); schema drift, a missing
    table and corruption stay a plain ``StorageError`` (not retryable).
    """
    cls = StorageBusyError if _is_transient(error) else StorageError
    return cls(f"{what}: {error}")


def check_query_length(query: str) -> None:
    """Refuse a search query longer than ``MAX_SEARCH_QUERY_LENGTH``.

    Called before anything else looks at the query, so every later step --
    sanitizing, expansion, the backend -- sees a bounded input. Refused, never
    truncated: a caller must know their query was not the one that ran.
    """
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise QueryTooLongError(
            f"Search query is {len(query)} characters; the maximum is "
            f"{MAX_SEARCH_QUERY_LENGTH}. Shorten the query."
        )


_FTS_OPERATORS = frozenset({"AND", "OR", "NOT"})


def clip_semantic_text(text: str) -> str:
    """Bound text for the semantic leg, which embeds it and never parses it.

    A prefix of at most ``MAX_SEARCH_QUERY_LENGTH`` characters ending on a
    word boundary (a single over-long word is cut). No FTS5 cleanup: quotes,
    parens and operators mean nothing to an embedding, and cutting at them
    would throw away the words ("1) first 2) second" must not become "1").
    """
    if len(text) <= MAX_SEARCH_QUERY_LENGTH:
        return text
    clipped = text[:MAX_SEARCH_QUERY_LENGTH]
    if not text[MAX_SEARCH_QUERY_LENGTH].isspace():
        clipped = clipped[: _last_word_start(clipped)] or clipped
    return clipped.rstrip()


#: Words too common to help an OR-joined query find related entries. Dropped
#: only when not written in capitals: "IT", "US" and "OR" are acronyms, "it",
#: "us" and "or" are not.
_STOP_WORDS = frozenset(
    """a about after all also an and any are as at be been but by can could did
    do does for from had has have he her his how i if in into is it its just me
    my no not of on or our she should so than that the their them then there
    these they this those to up us was we were what when where which who why
    will with would you your""".split()
)


def build_or_query(text: str, extra_terms: list[str] | tuple[str, ...] = ()) -> str:
    """An FTS5 query that cannot fail to parse, built from text nobody wrote as one.

    For an entry title (link suggestions) or a chat message (retrieval): the
    text's words, each double-quoted so FTS5 reads it as a literal (never an
    operator or a ``column:`` filter), OR-joined so an entry sharing any of
    them matches and FTS5 ranks by overlap. ``extra_terms`` (tags) are quoted
    and added whole, with any NUL removed.

    Words are split on non-word characters. Single characters are dropped, and
    so are lowercase stop words (``_STOP_WORDS``); a two-letter word such as
    "AI" or "UX" is kept (#431). Whole terms only, and no more than
    ``MAX_SEARCH_QUERY_LENGTH``: a term that would not fit is dropped whole,
    and a shorter one after it may still fit. The result may be ``""`` --
    nothing to search -- which a caller must not send as a MATCH.
    """
    tokens = [
        w
        for w in re.split(r"\W+", text or "")
        if len(w) > 1 and not (w.lower() in _STOP_WORDS and not w.isupper())
    ]
    # A NUL ends FTS5's quoted string early ("unterminated string"), and the
    # quoting below is the whole guarantee, so strip it from terms taken whole.
    tokens.extend(t.replace("\x00", "") for t in extra_terms if t and t.replace("\x00", ""))
    seen: set[str] = set()
    query = ""
    for token in tokens:
        if token.lower() in seen:
            continue
        seen.add(token.lower())
        term = '"' + token.replace('"', '""') + '"'
        candidate = f"{query} OR {term}" if query else term
        if len(candidate) > MAX_SEARCH_QUERY_LENGTH:
            continue
        query = candidate
    return query


#: What makes a query "explicit syntax" the sanitizer leaves alone. FTS5's
#: operators are case-sensitive: lowercase "and"/"or"/"not" are plain words,
#: so the check is too (#431).
_EXPLICIT_SYNTAX_MARKERS = (" AND ", " OR ", " NOT ", '"')


def _has_explicit_syntax(query: str) -> bool:
    return any(marker in query for marker in _EXPLICIT_SYNTAX_MARKERS)


def _last_word_start(text: str) -> int:
    """Index where the last whitespace-separated word of `text` begins."""
    i = len(text)
    while i and not text[i - 1].isspace():
        i -= 1
    return i


class SearchMode(StrEnum):
    """Search mode for queries."""

    KEYWORD = "keyword"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"


#: The valid search modes. An unknown mode is refused with a
#: ``ValidationError`` naming these, never silently answered as keyword
#: (#917).
VALID_SEARCH_MODES = ("keyword", "semantic", "hybrid")


def _validate_search_mode(mode: str | SearchMode) -> None:
    if mode not in VALID_SEARCH_MODES:
        raise ValidationError(
            f"Unknown search mode: {mode!r}. Valid modes: {', '.join(VALID_SEARCH_MODES)}."
        )


#: How an agent reads the verdict. The sentence in ``SearchService.search``'s
#: docstring (item 4), the ``kb_search`` tool description and ``pyrite search
#: --help``; `tests/test_search_verdict_on_every_surface.py` holds the three
#: to these words.
HOW_TO_READ_MATCH = (
    "`match: none` means the KB does not contain an answer; say so, do not build "
    "an answer from these results. `weak` means verify the entries before using "
    "them. `weak` with no results means the search could not run in full or found "
    "nothing strong, not that the KB lacks an answer: read `match_basis` and "
    "`legs_run`, then try keyword mode or other words. "
    "`similarity` compares results of the same model; it is not a probability."
)

#: The margin in the semantic rule is the top hit's similarity minus the mean
#: of this many results after it. The thresholds were measured with nine (#668),
#: so the vector leg always fetches the top hit and nine more, whatever
#: ``limit`` and ``offset`` the caller asked for.
VERDICT_NEIGHBOURS = 9

_KEYWORD_BASIS = {
    ("all_terms", None): "every term matched",
    ("some_terms", None): "no entry has every term; these match at least one",
    ("some_terms", "query_or"): "the query uses OR, so a hit may match only one side of it",
    ("some_terms", "expansion"): "only the expansion's terms matched, not the query's own",
    ("none", None): "nothing matched",
}
_KEYWORD_VERDICT = {"all_terms": "strong", "some_terms": "weak", "none": "none"}


#: Why the vector leg did not run, in the words ``match_basis`` uses. The codes
#: are the ones ``_semantic_search`` records (and ``semantic_unavailable``'s).
_SEMANTIC_SKIP_BASIS = {
    "extra_missing": "the leg did not run: sentence-transformers is not installed",
    "sqlite_vec_missing": "the leg did not run: the vector index is not available",
    "sqlite_vec_not_loaded": "the leg did not run: the vector index is not available",
    "no_embeddings": "the leg did not run: nothing in the KBs searched is embedded yet",
    "filter_unsupported": "the leg did not run: this backend cannot filter it",
    "nothing_indexed": "nothing is indexed in the KBs searched",
}


def _semantic_verdict(
    similarities: list[float], model: str | None, capped: bool = False
) -> tuple[str, str]:
    """The vector leg's verdict: ``strong``, ``weak``, ``none`` or ``unjudged``.

    ``none`` is returned only when the leg looked at every embedded entry in
    scope with a model whose thresholds are known. ``unjudged`` is everything
    it cannot stand behind: an uncalibrated model, with or without rows, and a
    result the backend marked ``capped``.
    """
    from .embedding_service import match_thresholds_for_model

    thresholds = match_thresholds_for_model(model)
    if thresholds is None:
        # Checked before anything else: no verdict is borrowed from another
        # model's row, and that includes "nothing reached the floor", which is
        # a threshold too.
        found = f"best similarity {similarities[0]:.2f}" if similarities else "no result"
        return "unjudged", f"{found}, not judged: uncalibrated model {model}"
    verdict, said = _calibrated_verdict(similarities, thresholds)
    if capped and verdict == "none":
        return "unjudged", (
            f"{said}, not judged: the vector index could not reach every entry in the KBs searched"
        )
    return verdict, said


def _calibrated_verdict(similarities: list[float], thresholds: Any) -> tuple[str, str]:
    if not similarities:
        return "none", "no entry reached the similarity floor"
    best = similarities[0]
    if best >= thresholds.strong:
        return "strong", f"best similarity {best:.2f}, at or above {thresholds.strong:.2f}"
    if best < thresholds.floor:
        return "none", f"best similarity {best:.2f}, below {thresholds.floor:.2f}"
    rest = similarities[1 : 1 + VERDICT_NEIGHBOURS]
    said = f"best similarity {best:.2f}, below {thresholds.strong:.2f}"
    if not rest:
        # One row, so no crowd to stand out from. The middle band without its
        # second test is not evidence of absence: weak, and say why.
        return "weak", f"{said}, with no other result to compare it with"
    # Rounded before the comparison: 0.42 - 0.32 is 0.0999... in floats.
    margin = round(best - sum(rest) / len(rest), 3)
    others = f"the next {len(rest)} result{'' if len(rest) == 1 else 's'}"
    if margin < thresholds.margin:
        return "none", (
            f"{said} and only {margin:.2f} above {others} (under {thresholds.margin:.2f})"
        )
    return "weak", f"{said} but {margin:.2f} above {others}"


def match_verdict(
    mode: str,
    *,
    keyword_match: str | None,
    similarities: list[float] | None,
    model: str | None = None,
    keyword_reason: str | None = None,
    semantic_skip: str | None = None,
    capped: bool = False,
) -> tuple[str, str]:
    """What a page of results is worth: ``(match, match_basis)`` (#668).

    The one definition. ``SearchService.search_response`` is its only caller,
    and no surface computes or rewords what it returns.

    ``keyword_match`` is the keyword leg's signal (``all_terms``,
    ``some_terms``, ``none``), or ``None`` when no keyword leg ran.
    ``similarities`` are the cosine similarities of the vector leg's top
    results that the caller may read, best first, from before any ``offset``
    was applied; ``[]`` when the leg ran and returned nothing; ``None`` when it
    did not run, with ``semantic_skip`` saying why. ``model`` is the embedding
    model that produced them. ``capped``: the backend could not consider every
    candidate in scope (``SemanticRows``).

    - keyword: strong when every term matched, weak when only some did
      (OR-relaxed, or an OR in the query), none with no hits.
    - semantic: the model's row in ``_MODEL_MATCH_THRESHOLDS`` decides. Strong
      at or above ``strong``; none below ``floor``, or between the two with
      less than ``margin`` over the mean of the next ``VERDICT_NEIGHBOURS``
      results; weak otherwise.
    - hybrid: strong if either leg is strong; none if the vector leg says none
      and the keyword leg is not strong; weak otherwise.

    **``none`` is a claim, and every path to it has to carry the evidence.**
    It means the mode searched as it promises and nothing answered. So a leg
    that did not run cannot contribute to it, nor can a model with no
    thresholds, nor a capped result: each of those is ``weak`` at worst,
    whatever the other leg found, and the basis says which it was. The one
    skip that is an absence is ``nothing_indexed``: there was nothing to look
    at. `tests/test_search_match_verdict.py::TestEveryPathToNone` lists the
    paths.

    ``none`` is a statement about the query, never a reason to return fewer
    rows: the two measured false ``none`` verdicts were real answers.
    """
    keyword_verdict = _KEYWORD_VERDICT.get(keyword_match or "")
    keyword_said = _KEYWORD_BASIS.get(
        (keyword_match, keyword_reason), _KEYWORD_BASIS.get((keyword_match, None), "")
    )
    if mode == SearchMode.KEYWORD:
        # No signal at all cannot happen (the leg ran or the search raised);
        # if it ever does, it is a leg that did not run.
        return keyword_verdict or "weak", f"keyword: {keyword_said or 'the leg did not run'}"

    if similarities is not None:
        semantic_verdict, semantic_said = _semantic_verdict(similarities, model, capped)
    elif semantic_skip == "nothing_indexed":
        semantic_verdict, semantic_said = "none", _SEMANTIC_SKIP_BASIS[semantic_skip]
    else:
        semantic_verdict = "unjudged"
        semantic_said = _SEMANTIC_SKIP_BASIS.get(semantic_skip or "", "the leg did not run")

    if mode == SearchMode.SEMANTIC:
        # What nobody can judge is worth checking, never worth trusting, and
        # never an absence.
        alone = "weak" if semantic_verdict == "unjudged" else semantic_verdict
        return alone, f"semantic: {semantic_said}"

    basis = f"keyword: {keyword_said or 'the leg did not run'}; semantic: {semantic_said}"
    if "strong" in (keyword_verdict, semantic_verdict):
        return "strong", basis
    if semantic_verdict == "none":
        return "none", basis
    # Weak, or a vector leg nobody can judge (not run, uncalibrated, capped)
    # beside a keyword leg that is not strong. The keyword leg alone cannot
    # say none in hybrid: the mode promised a vector search too.
    return "weak", basis


class SearchService:
    """
    Service for search operations.

    Provides:
    - FTS5 query sanitization (handles hyphens, special chars)
    - Full-text search with filters
    - AI-powered query expansion
    """

    def __init__(self, db: PyriteDB, settings: Any | None = None):
        self.db = db
        self._settings = settings
        self._expansion_service = None

    def _embedder(self):
        """The embedder for the query: the configured model, like the entries'.

        Built with no model name it embedded every query with the default model
        while `pyrite index embed` embedded the entries with the configured
        one (#668). The verdict reads its thresholds for this same name.
        """
        from .embedding_service import EmbeddingService, configured_embedding_model

        return EmbeddingService(self.db, model_name=configured_embedding_model(self._settings))

    def _get_expansion_service(self):
        """Lazy-load QueryExpansionService from settings."""
        if self._expansion_service is not None:
            return self._expansion_service

        if self._settings is None:
            return None

        from .query_expansion_service import QueryExpansionService, is_available

        provider = getattr(self._settings, "ai_provider", "stub")
        if not is_available(provider):
            return None

        self._expansion_service = QueryExpansionService(
            provider=provider,
            model=getattr(self._settings, "ai_model", ""),
            api_key=getattr(self._settings, "ai_api_key", ""),
            api_base=getattr(self._settings, "ai_api_base", ""),
        )
        return self._expansion_service

    # =========================================================================
    # Query Sanitization
    # =========================================================================

    @staticmethod
    def sanitize_fts_query(query: str) -> str:
        """
        Sanitize a search query for FTS5.

        FTS5 treats many punctuation characters as special syntax:
        - Hyphens as NOT operators
        - Dots as column filters (column.term)
        - Colons as column prefixes
        - @, #, /, !, ~, = as syntax errors

        This method:
        - Quotes tokens containing special characters to treat them as literals
        - Preserves explicit FTS5 operators (AND, OR, NOT -- uppercase only;
          FTS5 reads lowercase and/or/not as plain words)
        - Preserves already-quoted phrases

        Examples:
            "alex-jones" -> '"alex-jones"'
            "0.6 milestone" -> '"0.6" milestone'
            "alex jones" -> "alex jones" (unchanged)
            'alex AND "not-here"' -> 'alex AND "not-here"' (preserved)

        Raises ``QueryTooLongError`` for a query over
        ``MAX_SEARCH_QUERY_LENGTH``, before any other work.
        """
        check_query_length(query)

        # If query already contains FTS5 operators or quotes, assume user knows
        # what they're doing. Uppercase only: FTS5 reads a lowercase "and" as a
        # word, so it must not switch quoting off (#431).
        if _has_explicit_syntax(query):
            return query

        # Quote any token containing FTS5-special characters
        # Matches tokens with at least one non-alphanumeric, non-space, non-underscore char
        sanitized = re.sub(r"(\S*[^\w\s]\S*)", r'"\1"', query)
        return sanitized

    @staticmethod
    def _restrict(
        results: list[dict[str, Any]], kb_names: set[str] | list[str] | None, limit: int
    ) -> list[dict[str, Any]]:
        """Drop results from KBs the caller may not read: a backstop.

        Both legs take the readable set inside their own query (the FTS
        ``WHERE``; the KNN's candidate filter), so nothing should reach here
        that this removes. It stays so that a leg which one day forgets the
        set cannot return a row the caller may not read.
        """
        if kb_names is None:
            return results
        allowed = set(kb_names)
        return [r for r in results if r.get("kb_name") in allowed][:limit]

    def _db_search(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Call ``self.db.search`` and classify any sqlite failure.

        ``sanitize_fts_query`` skips quoting when the query already contains
        an FTS5 operator (uppercase AND/OR/NOT) or a quote — it assumes the
        caller knows what they're doing. A bare special-char token like
        `cross-link` mixed into such a query then reaches SQLite's MATCH
        unquoted and raises ``sqlite3.OperationalError`` (e.g. "no such
        column: link"), because the hyphen/colon is parsed as column-filter
        syntax. That's a deterministic, non-retryable query problem, not an
        internal error — it is reclassified as ``QuerySyntaxError``.

        Only a parse error the caller caused is reclassified: an error whose
        text matches ``_QUERY_SYNTAX_ERROR_MARKERS`` ("fts5:", "unterminated
        string", "unknown special query", "expected integer"), or a "no such
        column:" naming something the query itself contains (see
        ``_looks_like_query_syntax_error``). Everything else -- "database is
        locked", disk I/O, a missing table or column, a corrupt file (a
        ``sqlite3.DatabaseError``, not an OperationalError) -- is a storage
        fault and is raised as ``StorageError`` (``StorageBusyError`` for a
        lock). Not logged here: each surface logs it once, with a traceback
        (REST's central handler, MCP's dispatcher), and the CLI prints it.
        """
        try:
            return self.db.search(**kwargs)
        except sqlite3.DatabaseError as e:
            query = str(kwargs.get("query") or "")
            if not _looks_like_query_syntax_error(str(e), query):
                raise storage_error_from(e) from e
            token = self._offending_token(query, e)
            if token:
                raise QuerySyntaxError(
                    f"Query could not be parsed: the token '{token}' was read as a "
                    f'column reference. Quote it — "{token}" — or use AND/OR/NOT '
                    "only between plain words (sanitization is skipped once you "
                    "use operators or quotes)."
                ) from e
            raise QuerySyntaxError(
                f"Query could not be parsed: {e}. If your query uses AND/OR/NOT "
                "or phrase quotes, quote any tokens containing - : . yourself "
                "(sanitization is skipped once you use operators or quotes)."
            ) from e

    @staticmethod
    def _offending_token(query: str, error: sqlite3.OperationalError) -> str | None:
        """Name the query token SQLite read as a column reference, or ``None``.

        SQLite reports the *fragment* it kept after splitting a token on one of
        the separators the guidance names — ``no such column: party`` for
        ``third-party-doctrine`` — which is not something the caller wrote. A
        token is named only when the fragment is one of its separator-delimited
        segments (or the whole token): guessing would mis-attribute the error to
        an innocent term, so anything else keeps the previous message.
        """
        match = re.search(r"no such column:\s*(\S+)", str(error))
        if not match or not query:
            return None
        fragment = match.group(1).strip("\"'(),;").lower()
        if not fragment:
            return None
        for raw in query.split():
            token = raw.strip("\"'()")
            if token.lower() == fragment:
                return token
            segments = [s for s in re.split(r"[-:.]", token.lower()) if s]
            if fragment in segments:
                return token
        return None

    @staticmethod
    def _relax_to_or(query: str) -> str | None:
        """OR-combine the terms of a bare multi-term query.

        FTS5 `MATCH` is implicit-AND, so a query like "orange county florida
        quarterly" requires *every* term — one absent word zeroes the result
        set. When an AND search finds nothing, retrying with the terms
        OR-combined ("orange OR county OR florida OR quarterly") recovers the
        near-misses.

        Each term is sanitized (quoted if it contains an FTS5-special
        character) before joining. `sanitize_fts_query` can't do this for
        us: it skips quoting entirely once it sees the query already
        contains an operator, and joining with " OR " is exactly what
        introduces one. Without this, a perfectly ordinary plain-word query
        like "pre-push selection" or "section 230(c) reform" relaxes to
        `pre-push OR selection`, and the un-quoted hyphenated/parenthesized
        term then reaches FTS5 raw and raises -- turning a normal zero-hit
        search into a QUERY_SYNTAX error that blames the user for a query
        they never wrote wrong (#361).

        Returns the OR-combined query, or ``None`` when relaxation does not
        apply: a single term (nothing to relax), an empty query, or a query the
        user already wrote with explicit operators or quoted phrases (we honor
        their intent rather than widening it).
        """
        if not query or not query.strip():
            return None
        # Respect explicit operators / quoted phrases — same guard the
        # sanitizer uses to decide "the user knows what they want."
        if _has_explicit_syntax(query):
            return None
        terms = query.split()
        if len(terms) < 2:
            return None
        quoted_terms = [re.sub(r"(\S*[^\w\s]\S*)", r'"\1"', term) for term in terms]
        return " OR ".join(quoted_terms)

    # =========================================================================
    # Search Operations
    # =========================================================================

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sanitize: bool = True,
        mode: str | SearchMode = SearchMode.KEYWORD,
        expand: bool = False,
        include_archived: bool = False,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        trace: dict[str, Any] | None = None,
        kb_names: set[str] | list[str] | None = None,
        warnings: list[str] | None = None,
        semantic_query: str | None = None,
        non_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Search across entries.

        Args:
            query: Search query
            kb_name: Filter to specific KB (None for all)
            entry_type: Filter by type (event, actor, etc.)
            tags: Filter by tags (AND logic)
            date_from: Filter from date (YYYY-MM-DD)
            date_to: Filter to date (YYYY-MM-DD)
            limit: Max results. A positive integer; anything else raises
                ``ValueError`` naming the value.
            offset: Pagination offset
            sanitize: Whether to sanitize query for FTS5 (default True)
            mode: Search mode - keyword, semantic, or hybrid
            expand: Whether to use AI query expansion for additional terms
            status: Filter to entries with this lifecycle status (e.g.
                "unprocessed").
            warnings: Optional list the caller passes in to receive
                human-readable notes about anything the search could not do
                as asked — today, a filter a backend's vector leg cannot
                honour, which costs the semantic leg entirely rather than
                returning rows that violate the filter. An empty list (or a
                list the search leaves untouched) means every filter was
                applied on every leg that ran. Never populated on the happy
                path; a caller that ignores it still gets correctly filtered
                results, just without knowing a leg was dropped.
            non_empty: Leave out entries whose body and summary are both blank
                after trimming whitespace (#200). Applied inside each leg's own
                query, so ``limit`` rows come back when that many qualify. Off
                by default: search itself hides nothing; link discovery turns
                it on.
            semantic_query: The text the semantic leg embeds, when it should
                differ from ``query``. A feature that derives an OR query from
                a title or a message passes the query as ``query`` and the
                text itself here, so the vector leg embeds words, not
                ``"a" OR "b"`` (#431). ``None`` (the default): both legs use
                ``query``. Refused over ``MAX_SEARCH_QUERY_LENGTH`` like
                ``query``; ignored in keyword mode.

        **What a search response owes its caller** (#56) — the one statement
        of the convention; the REST schema, the REST route and the ``kb_search``
        tool description point here rather than restating it:

        1. Every filter — ``entry_type``, ``tags``, ``date_from``/``date_to``,
           ``fips``, ``state``, ``status``, ``include_archived`` — is applied on
           **every** leg of every mode. Before #56 the vector leg ran unfiltered
           and the fused result silently contained entries the filter excluded.
        2. A leg that cannot honour a filter is dropped, never run unfiltered.
        3. A dropped leg is always named in ``warnings``. Silence means every
           filter was applied on every leg that ran, so an empty ``warnings``
           is *absent* on every surface — the MCP payload omits the key, REST
           omits it (``response_model_exclude_none``), the CLI prints nothing.
           A caller may therefore test presence, never truthiness of a null.
        4. Every response says what its page is worth (#668): ``match`` is
           ``strong``, ``weak`` or ``none``, and ``match_basis`` gives the
           reason in words. How to read it (``HOW_TO_READ_MATCH``, the words
           the ``kb_search`` description and ``pyrite search --help`` repeat):
           `match: none` means the KB does not contain an answer; say so, do
           not build an answer from these results. `weak` means verify the
           entries before using them. `weak` with no results means the search
           could not run in full or found nothing strong, not that the KB
           lacks an answer: read `match_basis` and `legs_run`, then try
           keyword mode or other words. `similarity` compares results of the
           same model; it is not a probability.
           The rows are returned whatever the verdict.
        5. The verdict belongs to the query, not the page: it is the same for
           every ``limit`` and ``offset``. Every input to it is computed from
           the KBs the caller may read: the rows of both legs, whether
           anything there is embedded, the warning. ``match_basis`` never
           quotes a row the caller cannot read. The readable set is a
           candidate filter inside each leg's own query, on SQLite inside
           the KNN itself (sqlite-vec considers at most 4096 nearest
           vectors, and they are the 4096 nearest *readable* ones), so a
           scoped search answers as a search of an index holding only the
           readable KBs would, however many nearer rows sit elsewhere.
        6. ``keyword_match`` is present when a keyword leg ran: ``all_terms``,
           ``some_terms`` (the OR-relaxed retry, an ``OR`` in the query, or a
           hit on ``expand``'s terms only) or ``none``. Hybrid's keyword leg
           relaxes only when its vector leg did not run (then it is a keyword
           search); beside a vector leg that ran, ``none`` means no entry has
           every term.
        9. ``legs_run`` at the top level names the legs that ran: ``keyword``,
           ``semantic``, both, or neither. A hybrid search whose ``legs_run``
           is ``["keyword"]`` was a keyword search; ``warnings`` says why. It
           is not the per-row ``legs`` (item 7), which says which legs
           returned that row.
        10. ``none`` is only said with the evidence for it: the mode searched
           as it promises and nothing answered. A vector leg that did not
           run, a model with no thresholds, or a result sqlite-vec's ceiling
           cut short makes the verdict ``weak`` at worst, and ``match_basis``
           says which. What ``none`` does not account for: entries still
           waiting in the embed queue are not seen by the vector leg.
        7. Every row the vector leg returned carries ``similarity``: cosine
           similarity, 3 places, the same number on every backend. A hybrid
           row carries ``legs``, the legs that returned it. ``distance`` (the
           backend's own measure), ``rank`` (raw BM25) and ``rrf_score`` (rank
           arithmetic) order results and are not scores; ``distance`` leaves
           the response after one release.
        8. The thresholds behind ``match`` belong to an embedding model. With a
           model that has no row in ``_MODEL_MATCH_THRESHOLDS`` the vector leg
           is not judged: ``match_basis`` says ``uncalibrated model <name>``,
           and ``match`` is ``strong`` only from the keyword leg.

        Items 4 to 10 are attached by ``search_response``, which the CLI, MCP
        and REST all return; this method returns the rows alone, for callers
        that gather context and show no page.

        Returns:
            List of matching entries with snippets and rank
        """
        # Observability trace — a caller may pass a dict to receive the
        # mode/fallback/latency decisions; we always keep a local one so the
        # structured log line below is emitted on every search. Held locally
        # (not on self) because the service instance is shared across requests
        # on the server/MCP side.
        tr: dict[str, Any] = trace if trace is not None else {}
        # What each leg learned about the query, for ``search_response``'s
        # verdict: ``keyword_match``, ``similarities``, ``model``. A key is
        # absent when its leg did not run.
        signal: dict[str, Any] = {}
        tr["match_signal"] = signal

        # Before expansion, mode selection or sanitizing: every mode refuses an
        # over-long query the same way.
        check_query_length(query)
        if semantic_query is not None:
            check_query_length(semantic_query)
        semantic_text = query if semantic_query is None else semantic_query

        # Validate `mode` up front: an unknown mode is a caller mistake and
        # must be refused, not silently answered as keyword (#917). The
        # caller's actual input reaches this function on every surface (CLI,
        # REST, MCP), so one check here covers all of them.
        _validate_search_mode(mode)

        # Validate `limit` once, here, rather than letting whatever arithmetic
        # reaches it first decide the error. `limit=None` used to surface as a
        # bare TypeError from `limit * 3` deep inside the hybrid leg -- and,
        # with a filter active, the old dropped-leg rescue caught it and
        # reported "this backend cannot filter". `limit=-1` reached SQLite and
        # came back as OperationalError, which the REST layer turns into HTTP
        # 400 SEARCH_FAILED. Both are the caller's mistake; say so, and name
        # the value (#56).
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError(f"limit must be a positive integer, got {limit!r}")

        # Normalize mode (already validated above, so SearchMode(...) cannot fail)
        if isinstance(mode, str):
            mode = SearchMode(mode)

        # Normalize "All KBs" to None
        if kb_name == "All KBs":
            kb_name = None

        signal["mode"] = mode.value
        tr["requested_mode"] = mode.value
        tr["actual_mode"] = mode.value
        tr["reason"] = ""
        tr["relaxed"] = False
        tr["query_len"] = len(query)
        tr["kb"] = kb_name

        results: list[dict[str, Any]] = []
        start = time.perf_counter()
        try:
            # Apply query expansion to the FTS5 query (keyword leg only)
            expanded_query = self._expand_query(query) if expand else query

            if mode == SearchMode.SEMANTIC:
                skip: dict[str, Any] = {}
                # Semantic uses original natural language query, not expanded
                results = self._semantic_search(
                    semantic_text,
                    kb_name,
                    limit,
                    offset=offset,
                    kb_names=kb_names,
                    signal=signal,
                    filters=self._leg_filters(
                        entry_type=entry_type,
                        tags=tags,
                        date_from=date_from,
                        date_to=date_to,
                        fips=fips,
                        state=state,
                        status=status,
                        include_archived=include_archived,
                        non_empty=non_empty,
                    ),
                    warnings=warnings,
                    trace=skip,
                )
                results = self._restrict(results, kb_names, limit)
                if skip.get("semantic_skip"):
                    # The leg never ran, so nothing did: a pure semantic search
                    # does not fall back to keyword. The reason is the same
                    # cause the warning names.
                    tr["actual_mode"] = "none"
                    tr["reason"] = self._skip_reason("semantic", skip["semantic_skip"])
            elif mode == SearchMode.HYBRID:
                results = self._hybrid_search(
                    query,
                    kb_name,
                    entry_type,
                    tags,
                    date_from,
                    date_to,
                    limit,
                    offset,
                    sanitize,
                    expanded_query=expanded_query,
                    semantic_query=semantic_text,
                    fips=fips,
                    state=state,
                    status=status,
                    trace=tr,
                    warnings=warnings,
                    include_archived=include_archived,
                    kb_names=kb_names,
                    signal=signal,
                    non_empty=non_empty,
                )
                results = self._restrict(results, kb_names, limit)
            else:
                # Default: keyword search
                kw_query = expanded_query
                if sanitize:
                    kw_query = self.sanitize_fts_query(kw_query)

                def _run(q: str, page: tuple[int, int] = (limit, offset)) -> list[dict[str, Any]]:
                    return self._db_search(
                        query=q,
                        kb_name=kb_name,
                        kb_names=kb_names,
                        entry_type=entry_type,
                        tags=tags,
                        date_from=date_from,
                        date_to=date_to,
                        limit=page[0],
                        offset=page[1],
                        include_archived=include_archived,
                        fips=fips,
                        state=state,
                        status=status,
                        non_empty=non_empty,
                    )

                def _hits(q: str, page: list[dict[str, Any]] | None = None) -> bool:
                    """Does ``q`` match anything at all, whatever page was asked for?

                    The first page answers it when it has rows or is the first
                    page; otherwise one row from offset 0 does.
                    """
                    if page or (page is not None and offset == 0):
                        return bool(page)
                    return bool(_run(q, (1, 0)))

                results = _run(kw_query)
                matched = _hits(kw_query, results)

                # Implicit-AND zeroes out when one term is absent. When the AND
                # query matches nothing, retry once with the terms OR-combined
                # so a near-miss still surfaces. Skip when the user already
                # used operators/quotes — _relax_to_or returns None then.
                #
                # Decided on the query, not on this page (#668): an empty page
                # past the end of the AND matches used to be refilled with
                # OR-relaxed rows from that offset, so page 2 was not the
                # continuation of page 1, and the two disagreed about what
                # matched.
                if not matched:
                    relaxed = self._relax_to_or(expanded_query)
                    if relaxed:
                        logger.debug("keyword search 0 hits; retrying OR-relaxed: %r", relaxed)
                        results = _run(relaxed)
                        tr["relaxed"] = True
                        if results:
                            tr["reason"] = "or_relaxation_recovered"
                        if _hits(relaxed, results):
                            signal["keyword_match"] = "some_terms"
                if matched:
                    signal["keyword_match"], signal["keyword_reason"] = self._keyword_signal(
                        query, kw_query, sanitize, _hits
                    )
                signal.setdefault("keyword_match", "none")
        finally:
            tr["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            tr["result_count"] = len(results)

        logger.info(
            "search.query kb=%s mode=%s actual=%s reason=%s query_len=%d "
            "result_count=%d latency_ms=%s relaxed=%s",
            tr["kb"],
            tr["requested_mode"],
            tr["actual_mode"],
            tr["reason"] or "-",
            tr["query_len"],
            tr["result_count"],
            tr["latency_ms"],
            tr["relaxed"],
        )

        return results

    def _keyword_signal(
        self, query: str, ran_query: str, sanitize: bool, hits: Any
    ) -> tuple[str, str | None]:
        """``(keyword_match, reason)`` for a keyword query that returned rows.

        FTS5 ANDs bare terms, so a hit means every term matched, unless the
        query that ran has an ``OR`` in it. One the caller wrote: a hit may
        match one side only. One ``expand`` added: ask whether the caller's own
        query matches anything (``hits``, one row).
        """
        if " OR " not in ran_query:
            return "all_terms", None
        if " OR " in query:
            return "some_terms", "query_or"
        own = self.sanitize_fts_query(query) if sanitize else query
        return ("all_terms", None) if hits(own) else ("some_terms", "expansion")

    def search_response(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """The response every agent-facing surface returns for a search (#668).

        Takes what ``search`` takes. Returns a mapping with ``results`` (the
        rows ``search`` returns), ``match`` and ``match_basis``, ``legs_run`` (the
        legs that ran), ``keyword_match`` when a keyword leg ran, and ``warnings`` when there
        are any (absent otherwise; see ``search``'s docstring, which states
        what each key means).

        The CLI, MCP ``kb_search`` and REST ``GET /api/search`` build their
        payload from this mapping and add only their envelope (``query``,
        ``count``, ``has_more``): they shape the rows (projection, body
        bounds) and pass every other key through, so a key added here reaches
        all three and none of them can reword the verdict.
        `tests/test_search_verdict_on_every_surface.py` walks them.
        """
        warnings: list[str] = kwargs.pop("warnings", None)
        if warnings is None:
            warnings = []
        trace: dict[str, Any] = kwargs.pop("trace", None)
        if trace is None:
            trace = {}
        results = self.search(query, warnings=warnings, trace=trace, **kwargs)

        signal = trace.get("match_signal") or {}
        match, basis = match_verdict(
            signal.get("mode", SearchMode.KEYWORD),
            keyword_match=signal.get("keyword_match"),
            similarities=signal.get("similarities"),
            model=signal.get("model"),
            keyword_reason=signal.get("keyword_reason"),
            semantic_skip=signal.get("semantic_skip"),
            capped=signal.get("capped", False),
        )
        # Which legs ran, as a fact a caller can read without parsing the
        # basis or guessing from `warnings`: a hybrid search whose `legs_run` is
        # ["keyword"] was a keyword search.
        legs_run = [
            leg
            for leg, ran in (
                ("keyword", "keyword_match" in signal),
                ("semantic", signal.get("similarities") is not None),
            )
            if ran
        ]
        response: dict[str, Any] = {
            "results": results,
            "match": match,
            "match_basis": basis,
            # Not `legs`: that is the per-row key (which legs returned the row).
            "legs_run": legs_run,
        }
        if "keyword_match" in signal:
            response["keyword_match"] = signal["keyword_match"]
        if warnings:
            response["warnings"] = warnings
        return response

    @staticmethod
    def _skip_reason(mode: str, code: str | None) -> str:
        """Trace reason for a vector leg that returned nothing, in ``mode``.

        Keeps the two reasons published before #584 (``semantic_empty_no_embeddings``,
        ``hybrid_no_embeddings``); the install causes are named for what they are.
        """
        if code in (None, "no_embeddings"):
            return "semantic_empty_no_embeddings" if mode == "semantic" else "hybrid_no_embeddings"
        return f"{mode}_{code}"

    def _expand_query(self, query: str) -> str:
        """Expand query with AI-generated terms, returning OR-combined FTS5 query."""
        svc = self._get_expansion_service()
        if svc is None:
            return query

        terms = svc.expand(query)
        if not terms:
            return query

        # Combine: original query OR term1 OR term2 ... The terms are derived
        # text, so they only take the room the caller's query leaves under the
        # cap; a term that does not fit is dropped whole, and a shorter one
        # after it may still fit. Only the caller's own query is ever refused.
        expanded = query
        for term in terms:
            candidate = f"{expanded} OR {term}"
            if len(candidate) <= MAX_SEARCH_QUERY_LENGTH:
                expanded = candidate
        return expanded

    def _semantic_search(
        self,
        query: str,
        kb_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
        filters: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        keyword_leg_ran: bool = False,
        trace: dict[str, Any] | None = None,
        kb_names: set[str] | list[str] | None = None,
        signal: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Pure semantic vector search, with the keyword leg's filters applied.

        ``kb_names``, the caller's readable set, goes to the backend with the
        filters, so the rows and the similarities in ``signal`` are those of a
        search over the readable KBs alone. ``signal`` receives ``model`` and,
        when the leg ran, ``similarities``: the top hit's and the next
        ``VERDICT_NEIGHBOURS``', taken before ``offset`` is applied.

        ``keyword_leg_ran`` is True when a hybrid search calls this beside a
        keyword leg; it only changes how a skipped leg is worded, because "only
        the keyword leg ran" is false in pure semantic mode, where nothing ran.

        ``filters`` goes to the backend's ``search_semantic``, which applies it
        inside the KNN query. Whether a backend can do that is a *declared*
        capability, ``FILTERED_SEMANTIC``, checked before the leg runs: a
        backend that does not declare it never sees the filter, and the leg is
        dropped with the offending filters named in ``warnings`` (#56 — a
        filter is honoured or reported, never silently dropped).

        Declared rather than probed on purpose. An earlier version called the
        backend and read a ``TypeError`` as "cannot filter", but that wraps the
        whole vector leg — embedding the query, the backend call, snippet
        generation — so any genuine ``TypeError`` anywhere inside it was
        relabelled as a missing feature and turned into a silently empty
        semantic leg. Bugs now propagate.
        """
        from .embedding_service import semantic_unavailable

        # Checked before anything is encoded: no model is loaded for a search
        # that can only come back empty. Without the extra or the sqlite-vec
        # extension the leg cannot run, and that is a degraded answer, so it is
        # named (#43), not returned as a silent [].
        if unavailable := semantic_unavailable(
            self.db.vec_available, getattr(self.db, "vec_load_error", None)
        ):
            code, cause, remedy = unavailable
            if trace is not None:
                trace["semantic_skip"] = code
            if signal is not None:
                signal["semantic_skip"] = code
            if warnings is not None:
                outcome = "only the keyword leg ran" if keyword_leg_ran else "it returned nothing"
                warnings.append(f"semantic leg skipped: {cause}, so {outcome}; {remedy}")
            return []

        svc = self._embedder()
        if signal is not None:
            signal["model"] = svc.model_name
        # Asked of the KBs this search covers, not of the index (#668): whether
        # some other KB has vectors must not change what a scoped caller is
        # told, and a KB with none has not been searched by this leg even when
        # the index as a whole has plenty.
        scoped = kb_name is not None or kb_names is not None
        try:
            has_embeddings = (
                svc.has_embeddings(kb_name=kb_name, kb_names=kb_names)
                if scoped
                else svc.has_embeddings()
            )
        except sqlite3.DatabaseError as e:
            raise storage_error_from(e, "Semantic search failed") from e
        if not has_embeddings:
            # ADR-0035 §5. Under "writes are eventually-embedded" this is the
            # ordinary state of a KB nobody has embedded yet, and an empty
            # result set is indistinguishable from "searched, found nothing".
            # Say which it is and name the command that fixes it -- without
            # this, a fresh install's semantic search is a silent [].
            #
            # Only when the KB has entries: on a genuinely empty index the
            # answer is `pyrite index build`, and sending someone to `index
            # embed` would be the wrong advice confidently given.
            if trace is not None:
                trace["semantic_skip"] = "no_embeddings"
            has_entries = self._index_has_entries(kb_name, kb_names)
            if signal is not None:
                # Nothing indexed at all is an absence, not a leg that failed
                # to look: there was nothing for it to look at.
                signal["semantic_skip"] = "no_embeddings" if has_entries else "nothing_indexed"
            if warnings is not None and has_entries:
                outcome = "only the keyword leg ran" if keyword_leg_ran else "it returned nothing"
                where = "the KBs searched" if scoped else "this index"
                warnings.append(
                    f"semantic leg skipped: no embeddings exist for {where} yet, so "
                    f"{outcome}; run `pyrite index embed` to build them"
                )
            return []

        # ``include_archived`` is a default *exclusion*, not a value filter: it
        # must reach the backend even when False (that is when it does its
        # work), and it is not what a warning should name — the caller did not
        # ask for it. Every other filter is sent only when set.
        supplied = {k: v for k, v in (filters or {}).items() if k != "include_archived" and v}
        active = dict(supplied)
        if filters and "include_archived" in filters:
            active["include_archived"] = filters["include_archived"]

        if active and not self._backend_filters_semantic():
            named = sorted(supplied) or ["the archived-entry exclusion"]
            if warnings is not None:
                warnings.append(
                    "semantic leg dropped: this backend cannot filter vector search by "
                    + ", ".join(named)
                    + "; results come from the keyword leg only"
                )
            logger.warning("semantic leg dropped — backend cannot filter by %s", named)
            if trace is not None:
                trace["semantic_skip"] = "filter_unsupported"
            if signal is not None:
                signal["semantic_skip"] = "filter_unsupported"
            return []

        # sqlite-vec KNN doesn't support SQL OFFSET, so fetch limit+offset
        # and slice in Python. Never fewer than the verdict reads: the margin
        # is over the next nine results whatever page the caller asked for.
        fetch = max(limit + offset, 1 + VERDICT_NEIGHBOURS)
        # The embedded text is never parsed, so no sqlite failure here is the
        # caller's query: every one is a storage fault, classified like the
        # keyword leg's (#431). Before this, an OperationalError escaped as a
        # non-PyriteError -- a plain-text 500 on REST, INTERNAL on MCP.
        try:
            results = svc.search_similar(
                query,
                kb_name=kb_name,
                limit=fetch,
                kb_names=kb_names,
                **active,
            )
        except sqlite3.DatabaseError as e:
            raise storage_error_from(e, "Semantic search failed") from e
        if signal is not None:
            signal["similarities"] = [r["similarity"] for r in results[: 1 + VERDICT_NEIGHBOURS]]
            # Read before the slice makes a plain list of it (see SemanticRows).
            signal["capped"] = bool(getattr(results, "capped", False))
        return results[offset : offset + limit]

    @staticmethod
    def _leg_filters(
        *,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
        non_empty: bool = False,
    ) -> dict[str, Any]:
        """The one filter set every leg receives (#56).

        Built in one place so the semantic and hybrid call sites cannot drift
        apart — a filter present in one dict and missing from the other is
        exactly the class of bug this change exists to close. Adding a filter
        to search means adding it here, and both legs get it.
        """
        return {
            "entry_type": entry_type,
            "tags": tags,
            "date_from": date_from,
            "date_to": date_to,
            "fips": fips,
            "state": state,
            "status": status,
            "include_archived": include_archived,
            "non_empty": non_empty,
        }

    def _index_has_entries(
        self, kb_name: str | None = None, kb_names: set[str] | list[str] | None = None
    ) -> bool:
        """Is there anything indexed that *could* have been embedded?

        Distinguishes "indexed but not embedded" (tell them `pyrite index
        embed`) from "nothing indexed at all" (they need `pyrite index build`,
        and an embed warning would send them the wrong way). Best-effort: a
        backend that cannot answer cheaply gets the benefit of the doubt,
        because a missing warning is a smaller harm than a wrong one.
        """
        kwargs: dict[str, Any] = {"kb_name": kb_name} if kb_name else {}
        if kb_names is not None:
            # The caller's readable set: entries elsewhere are not theirs to
            # be warned about (#668).
            kwargs["kb_names"] = kb_names
        try:
            return self.db.count_entries(**kwargs) > 0
        except Exception:
            logger.debug("Could not count entries for the embed warning", exc_info=True)
            return False

    def _backend_filters_semantic(self) -> bool:
        """Does this backend's vector leg honour the keyword leg's filters?

        Read from the backend's declared capability set. A backend that
        declares nothing (or is a stand-in that never declared) is assumed not
        to filter: the safe reading is to drop the leg and say so, never to
        return rows that violate the caller's filter.
        """
        from ..storage.backends.capabilities import BackendCapability

        declared = getattr(self.db.backend, "capabilities", set()) or set()
        return BackendCapability.FILTERED_SEMANTIC in declared

    def _hybrid_search(
        self,
        query: str,
        kb_name: str | None = None,
        entry_type: str | None = None,
        tags: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sanitize: bool = True,
        expanded_query: str | None = None,
        semantic_query: str | None = None,
        fips: str | None = None,
        state: str | None = None,
        status: str | None = None,
        trace: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        include_archived: bool = False,
        kb_names: set[str] | list[str] | None = None,
        signal: dict[str, Any] | None = None,
        non_empty: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search using Reciprocal Rank Fusion (RRF).

        Combines FTS5 keyword results with vector similarity results.
        Falls back to keyword-only if no embeddings exist.

        Both legs take the same filter set: the fused result is only as
        trustworthy as its least-filtered leg (#56).
        """
        # Get keyword results — use expanded query for FTS5 leg if available
        # Fetch enough candidates from each leg to cover offset + limit after fusion
        fetch_size = max(limit * 2, offset + limit)
        fts_query = expanded_query if expanded_query else query
        kw_query = self.sanitize_fts_query(fts_query) if sanitize else fts_query

        def _keyword(q: str, size: int) -> list[dict[str, Any]]:
            # Both legs take the caller's readable set in their own query
            # (#668): filtering the fused list afterwards let nearer rows the
            # caller cannot read use up the fetch.
            return self._db_search(
                query=q,
                kb_name=kb_name,
                kb_names=kb_names,
                entry_type=entry_type,
                tags=tags,
                date_from=date_from,
                date_to=date_to,
                limit=size,
                offset=0,
                fips=fips,
                state=state,
                status=status,
                include_archived=include_archived,
                non_empty=non_empty,
            )

        keyword_results = _keyword(kw_query, fetch_size)
        if signal is not None:
            # This leg never relaxes to OR: a hit is every term, unless the
            # query itself (or ``expand``) carries an OR.
            signal["keyword_match"], signal["keyword_reason"] = (
                self._keyword_signal(query, kw_query, sanitize, lambda q: bool(_keyword(q, 1)))
                if keyword_results
                else ("none", None)
            )

        skip: dict[str, Any] = {}
        # Try to get semantic results — filtered on the vector leg itself, so
        # the fused set can never contain an entry the caller's filter excluded.
        semantic_results = self._semantic_search(
            query if semantic_query is None else semantic_query,
            kb_name,
            limit=fetch_size,
            filters=self._leg_filters(
                entry_type=entry_type,
                tags=tags,
                date_from=date_from,
                date_to=date_to,
                fips=fips,
                state=state,
                status=status,
                include_archived=include_archived,
                non_empty=non_empty,
            ),
            warnings=warnings,
            keyword_leg_ran=True,
            trace=skip,
            kb_names=kb_names,
            signal=signal,
        )

        if not semantic_results:
            # The vector leg was skipped (or found nothing): keyword only. The
            # reason is the cause the warning names, not always "no embeddings".
            if trace is not None:
                trace["actual_mode"] = "keyword"
                trace["reason"] = self._skip_reason("hybrid", skip.get("semantic_skip"))
            if not keyword_results and skip.get("semantic_skip"):
                # The vector leg did not run, so this is a keyword search and
                # behaves as one: when no entry has every term, retry with the
                # terms OR-combined, as keyword mode does (#668). Without it
                # the default `kb_search` on an install with no embeddings
                # returned nothing for a query keyword mode answers. Not when
                # the vector leg ran: its near neighbours are the near misses.
                relaxed = self._relax_to_or(fts_query)
                if relaxed:
                    keyword_results = _keyword(relaxed, fetch_size)
                    if trace is not None:
                        trace["relaxed"] = True
                    if keyword_results and signal is not None:
                        signal["keyword_match"] = "some_terms"
            page = keyword_results[offset : offset + limit]
            for row in page:
                row["legs"] = ["keyword"]
            return page

        if trace is not None:
            trace["actual_mode"] = "hybrid"

        # Reciprocal Rank Fusion
        k = 60  # RRF constant
        scores: dict[tuple[str, str], float] = {}
        entries: dict[tuple[str, str], dict[str, Any]] = {}

        for rank, result in enumerate(keyword_results):
            key = (result["id"], result["kb_name"])
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
            entries[key] = result
            result["legs"] = ["keyword"]

        for rank, result in enumerate(semantic_results):
            key = (result["id"], result["kb_name"])
            scores[key] = scores.get(key, 0) + 1.0 / (k + rank)
            if key not in entries:
                entries[key] = result
                result["legs"] = ["semantic"]
            else:
                # The keyword leg's row is the one kept (it has the FTS
                # snippet). What the vector leg measured goes with it: before
                # #668 a row both legs returned lost its score.
                kept = entries[key]
                kept["legs"] = ["keyword", "semantic"]
                kept["similarity"] = result["similarity"]
                kept["distance"] = result.get("distance")

        # Sort by RRF score descending
        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)

        results = []
        for key in sorted_keys[offset : offset + limit]:
            entry = entries[key]
            entry["rrf_score"] = scores[key]
            results.append(entry)

        return results

    def search_by_tag(
        self, tag: str, kb_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Search entries by tag."""
        if kb_name == "All KBs":
            kb_name = None
        return self.db.search_by_tag(tag, kb_name, limit)
