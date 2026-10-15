"""Normalise the volatile fields out of a golden case before it is compared
or recorded (ADR-0037 theme 0's "say what you normalised").

Three kinds of value in a response body are not stable across a
regeneration, a machine, or a run, and would make every golden fail for a
reason that has nothing to do with authorization or error-shape behaviour:

1. **Timestamps** -- ``created_at``/``updated_at``/``date``-shaped ISO 8601
   strings (ADR-0037's world always seeds fixed dates for entry ``date``, but
   a handler may still report `datetime.now()` for a write's timestamp).
2. **Generated ids** -- job ids, session tokens, commit hashes: strings this
   harness's own fixture setup does not choose (contrast with entry ids like
   ``READABLE_ENTRY``, which are fixed and NOT normalised, because a
   changed entry id in a body would be a real behaviour change to catch).
3. **Filesystem paths** -- the world's `tmp_path_factory` directory appears
   verbatim in some error messages (`ConfigSaveRefusedError.config_file`,
   `BrandingInvalidError`'s str()) and changes on every run.

The list is short and explicit on purpose: a normaliser that is too clever
(e.g. "replace anything that looks like a number") would launder a real
status-code or error-code change into a false pass, which is exactly the
failure mode the mutation-check in the report proves does not happen.
"""

from __future__ import annotations

import re
from typing import Any

_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")

# `ClipperBlockedHostError`'s DNS-failure message (pyrite/services/clipper.py:
# f"Could not resolve host {host!r}: {exc}") embeds `socket.gaierror`'s str(),
# which is the OS RESOLVER'S own text -- different on every platform for the
# identical failure (macOS: "[Errno 8] nodename nor servname provided, or not
# known"; Linux/CI: "[Errno -2] Name or service not known"). Found when CI
# (Linux) failed on a golden recorded on macOS with only this text differing
# -- the code (CLIPPER_BLOCKED_HOST) and status (400) matched. Matched by the
# stable prefix every platform shares (`Could not resolve host '<host>': `),
# normalising only what follows it, so a real change to the host named or the
# surrounding message is still caught.
_RESOLVER_ERROR = re.compile(r"(Could not resolve host '[^']*': ).*")

# `QAService._maybe_validate` (pyrite/services/qa_service.py) mints an
# auto-assessment id of the shape `qa-{entry_id}-{ms epoch timestamp}` on
# every create -- e.g. `qa-private-note-1790384135890` -- a real product
# behaviour (an id that must be unique across repeated assessments of the
# same entry), not a bug, but the millisecond timestamp inside it makes the
# id itself, and so any body that names it (a cross-KB tool's pair list,
# `kb_batch_suggest`'s `source_id`/`target_id`), different on every run.
# Matched by shape rather than by key, because it appears embedded inside
# `source_id`/`target_id`/free-text `snippet` fields, not only as a bare id.
_QA_ASSESSMENT_ID = re.compile(r"qa-[a-zA-Z0-9_-]+?-\d{13}")
_JOB_OR_TOKEN_KEYS = {
    "job_id",
    "id",  # only ever normalised when the key name says "job"/"token"/"hash", see below
    "token",
    "session_token",
    "commit_hash",
    "commit",
}

# Field names whose value is a generated id/token, wherever they appear in a
# body -- normalised by KEY, not by shape, because a shape-based rule (any
# hex string) would also swallow a stable, meaningful entry id.
GENERATED_ID_KEYS = {"job_id", "token", "session_token", "commit_hash"}

# Field names whose numeric value is a computed score that legitimately
# varies in its low-order digits between index builds (FTS5 BM25's `rank` on
# a search hit) -- not authorization-relevant, and not reproducible byte for
# byte across a rebuilt index. Normalised to a fixed placeholder rather than
# rounded, so the golden diff never has to be re-tuned for a rounding change.
VOLATILE_SCORE_KEYS = {"rank"}

# There used to be a global, always-applied, bare-key-name rule here
# (`VOLATILE_CONTENT_KEYS`, covering `entries`/`indexed`/`last_indexed`, and
# before blocker 6 also `source`) -- removed because it was itself an
# instance of exactly the bug it was meant to fix: `normalize()` runs on
# every body before the enumeration-aware projection below ever sees it, so
# a key literally named `entries` ANYWHERE (which is most listings:
# `kb_list_entries`, `GET /api/entries`, `kb_find_by_*`, ...) had its real
# content replaced by one fixed string wholesale -- a private KB's row
# leaking into a listing was invisible, the same failure mode as blocker 2's
# `knowledge_bases` bug, just one level further down (`normalize_mcp_result`/
# `normalize_rest_body` called this global `normalize()` first, so by the
# time `_project_identity_only` ran on the listed field, its list had
# already been replaced by the string "<CONTENT_STATE>"). There is no
# global, key-name-only content rule left: `GET /api/kbs`'s and `GET
# /api/kbs/{kb_name}`'s specific `entries`/`indexed`/`last_indexed` need is
# handled per-route by `REST_ENUMERATION_SENSITIVE_FIELDS` below, scoped to
# exactly those routes' own shape.
NORMALISED_TIMESTAMP = "<TIMESTAMP>"
NORMALISED_ID = "<GENERATED_ID>"
NORMALISED_PATH = "<TMPDIR>"
NORMALISED_SCORE = "<SCORE>"
NORMALISED_QA_ID = "qa-<GENERATED_ID>"
NORMALISED_CONTENT = "<CONTENT_STATE>"
NORMALISED_RESOLVER_DETAIL = "<OS_RESOLVER_DETAIL>"


def normalize(value: Any, *, tmpdir: str) -> Any:
    """Recursively normalise `value` (a JSON-shaped dict/list/str/scalar).

    No key-name-only rule touches a list or a nested content field here --
    see the module docstring and the note above `NORMALISED_TIMESTAMP` for
    why that used to exist and why it was actively dangerous. Only scalar,
    unambiguously-volatile fields (a generated id/token by exact key name, a
    search rank score) are touched at this level; everything list-shaped
    that needs projecting goes through `normalize_mcp_result`/
    `normalize_rest_body`'s enumeration-aware path instead, which projects
    BEFORE calling this function, not after.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in GENERATED_ID_KEYS and isinstance(v, str):
                out[k] = NORMALISED_ID
            elif k in VOLATILE_SCORE_KEYS and isinstance(v, int | float):
                out[k] = NORMALISED_SCORE
            else:
                out[k] = normalize(v, tmpdir=tmpdir)
        return out
    if isinstance(value, list):
        return [normalize(v, tmpdir=tmpdir) for v in value]
    if isinstance(value, str):
        text = value.replace(tmpdir, NORMALISED_PATH)
        text = _ISO_TIMESTAMP.sub(NORMALISED_TIMESTAMP, text)
        text = _QA_ASSESSMENT_ID.sub(NORMALISED_QA_ID, text)
        text = _RESOLVER_ERROR.sub(lambda m: m.group(1) + NORMALISED_RESOLVER_DETAIL, text)
        return text
    return value


NORMALISATION_LIST = [
    "timestamps: any ISO-8601-shaped date/time substring anywhere in a string value, "
    "including inside a message -> '<TIMESTAMP>'",
    "generated ids: the value of a 'job_id', 'token', 'session_token' or 'commit_hash' "
    "key, anywhere in the body -> '<GENERATED_ID>' (entry ids and KB names are NOT "
    "normalised -- they are fixed by the world's fixtures and a change in them is real)",
    "paths: the session's tmp_path_factory directory, verbatim, wherever it appears "
    "in a string (e.g. ConfigSaveRefusedError.config_file, a branding.yaml parse "
    "error) -> '<TMPDIR>'",
    "search rank: the value of a 'rank' key (FTS5 BM25 score on a search hit), which "
    "varies in its low-order digits between index builds -> '<SCORE>'",
    "QA auto-assessment ids: any 'qa-<id>-<13-digit ms timestamp>' substring "
    "(QAService's auto-assessment id, minted fresh on every create) -> 'qa-<GENERATED_ID>'",
    "content state: the value of an 'entries', 'indexed' or 'last_indexed' key "
    "(how much content a KB holds right now, not an authorization signal) -> "
    "'<CONTENT_STATE>' -- these change with every write any case in this shared-world "
    "suite makes, not only the case whose golden shows them. 'source' is deliberately "
    "NOT normalised here (#476 blocker 6): it is the exact field #491's bug corrupts.",
    "REST/MCP enumeration fields: for a route/tool in "
    "REST_ENUMERATION_SENSITIVE_FIELDS/ENUMERATION_SENSITIVE_FIELDS (a "
    "count/list/search/validate/health-style route or tool whose success body "
    "enumerates 'everything in a KB' rather than naming one row), each listed "
    "top-level field is PROJECTED, not blanked wholesale (#476 blocker 2): a list of "
    "KB dicts keeps only a sorted list of {'name': ...}; a list of entry/row dicts "
    "keeps only a sorted list of {'kb_name': ..., 'id': ...}; anything else (a bare "
    "count, or a list shape neither of those matches) -> '<ENUMERATION_CONTENT>'. "
    "Applied only to a success body -- a refusal's detail/code/message (REST) or "
    "error/error_code/retryable/suggestion (MCP) is never touched. This is what "
    "still catches e.g. MCP kb_list or REST GET /api/kbs leaking a private KB's "
    "name into the list, which wholesale blanking could not.",
    "a review row's auto-increment id: POST /api/reviews's 'id' field (a DB primary "
    "key, not a caller-chosen or world-fixture id) -> '<ENUMERATION_CONTENT>', since its "
    "value depends on how many other reviews any other case already created in the "
    "same process, not on this case's own authorization outcome.",
    "OS resolver detail: the text after \"Could not resolve host '<host>': \" in a "
    "ClipperBlockedHostError message (socket.gaierror's own str(), platform-specific -- "
    "macOS: '[Errno 8] nodename nor servname provided, or not known'; Linux/CI: "
    "'[Errno -2] Name or service not known') -> '<OS_RESOLVER_DETAIL>'. The stable "
    "prefix (the host named, and the fact that resolution failed) is left untouched, so "
    "a real change to which host is refused or why is still caught; only the OS's own "
    "wording for 'DNS lookup failed' is normalised.",
]

# -- MCP enumeration-sensitive tools -----------------------------------------
#
# A KB-bearing MCP tool's success body is either IDENTITY-BASED (its content
# depends only on the one row/entry/task the call names -- kb_get, kb_update,
# task_status, zettel_graph, every _MUTATES_ITS_ENTRY tool, ...) or
# ENUMERATION-SENSITIVE (its content is a count, list or aggregate over
# "everything in a KB" or "everything matching a filter" -- kb_search,
# kb_stats, kb_batch_suggest, investigation_find_duplicates, task_list, ...).
#
# The matrix's own write-tool cases (kb_create, task_create, social_post, ...)
# permanently add rows to the shared world for the rest of the test session,
# and each one also triggers QAService's auto-assessment side effect (another
# new row). An enumeration-sensitive tool's result is therefore a function of
# *which other cases already ran in the same process* -- not just of
# (tool, principal, kb_state) -- so its COUNT and the exact CONTENT of any
# volatile per-item field (a timestamp, a rank, an auto-assessment id) cannot
# be pinned byte for byte.
#
# What CAN and MUST be pinned: **which rows a caller can see at all** -- the
# whole reason this harness exists. Blanking an entire list field to one
# fixed marker (what this table used to do) makes that invisible: MCP
# `kb_list` leaking a private KB's name into the response passed every test,
# because `normalize_mcp_result` replaced the whole `knowledge_bases` list --
# leaked or not -- with the same `<ENUMERATION_CONTENT>` string (#476 blocker
# 2, cold review). So instead of blanking a listed field wholesale, this
# module PROJECTS it down to identity only: a sorted list of KB names (for a
# list of KB dicts), or a sorted list of `(kb_name, id)` pairs (for a list of
# entry/row dicts) -- keeping exactly the two things that answer "did this
# caller see something they should not have" -- and drops every other,
# volatile per-item key. A field that is a bare int/float count (not a list)
# is still blanked, since a count is real content-state noise with no
# identity of its own to preserve.
NORMALISED_ENUMERATION_CONTENT = "<ENUMERATION_CONTENT>"

# Per-item keys that identify a KB in a listing (kept, sorted by, never
# blanked) vs. an entry/row (kept as (kb_name, id), sorted, never blanked).
_KB_IDENTITY_KEYS = ("name",)
_ENTRY_IDENTITY_KEYS = ("kb_name", "id")


def _project_identity_only(items: list) -> list | None:
    """If `items` is a list of dicts that share an identity shape (every
    item has both `kb_name` and `id` -- an entry/row listing -- or every
    item has a `name` -- a KB listing), return the sorted list of THOSE
    identity keys only (dropping every other, volatile per-item field --
    a timestamp, a rank, a body). Returns None if `items` isn't shaped like
    either (a list of scalars, or dicts with neither shape), so the caller
    falls back to blanking it wholesale.

    An EMPTY list stays `[]` here, not None (#476 round-2 blocker 1): an
    earlier version of this harness special-cased emptiness to "blank it"
    because a SHARED, session-scoped world made a KB's row count itself
    accumulated, order-dependent noise across write-tool cases (a listing
    that was empty when one worker built its world and non-empty in
    another's was indistinguishable from a real scoping difference). Round
    2 splits read cases onto their own world that no write case ever
    touches (`conftest.py`'s `world` fixture, `write_world` for writes), so
    a read case's listing is now a pure, deterministic function of what
    `world.py` seeded plus that ONE case's own scoping -- "zero rows here"
    is real, stable information again, and keeping it as `[]` is what lets
    a private ENTRY (not just a private KB) leaking into an otherwise-
    correct, non-empty-by-seeding listing still show up as a mismatch.
    """
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return None
    if not items:
        return []
    if all("kb_name" in i and "id" in i for i in items):
        return sorted(
            ({"kb_name": i["kb_name"], "id": i["id"]} for i in items),
            key=lambda i: (i["kb_name"], i["id"]),
        )
    if all("name" in i for i in items):
        return sorted(({"name": i["name"]} for i in items), key=lambda i: i["name"])
    return None


# Tools whose success body has at least one enumeration-sensitive field.
# Each field is either a list (projected to identity-only by
# `_project_identity_only`, falling back to `NORMALISED_ENUMERATION_CONTENT`
# if its shape isn't recognised) or a scalar count/aggregate (always blanked
# -- it carries no identity of its own).
ENUMERATION_SENSITIVE_FIELDS: dict[str, tuple[str, ...]] = {
    "investigation_claims": ("count", "claims", "summary"),
    "investigation_entities": ("count", "entities", "summary"),
    "investigation_export_pack": ("content",),
    "investigation_find_duplicates": ("duplicates",),
    "investigation_ftm_export": ("entities",),
    "investigation_qa_report": ("source_tiers", "claims", "quality_score", "warnings"),
    "investigation_search_all": ("total_count", "groups"),
    "investigation_sources": ("count", "sources", "summary"),
    "investigation_status": (
        "summary",
        "entity_count",
        "event_count",
        "claim_count",
        "source_count",
        "claim_breakdown",
        "unverified_claims",
    ),
    "investigation_timeline": ("count", "events", "summary"),
    "kb_backlinks": ("backlink_count", "has_more", "backlinks"),
    "kb_batch_suggest": ("count", "pairs"),
    "kb_discover_neighbors": ("count", "discoveries"),
    "kb_find_by_assignee": ("entries", "count"),
    "kb_find_by_location": ("entries", "count"),
    "kb_find_by_status": ("entries", "count"),
    "kb_find_overdue": ("entries", "count"),
    "kb_index_sync": ("synced", "added", "updated", "removed"),
    "kb_list": ("knowledge_bases",),
    "kb_list_entries": ("entries", "total", "has_more"),
    "kb_orient": ("total_entries", "types", "top_tags", "recent"),
    "kb_qa_assess": ("assessed", "skipped", "results"),
    "kb_qa_status": (
        "total_entries",
        "total_issues",
        "issues_by_severity",
        "issues_by_rule",
        "coverage",
    ),
    "kb_qa_validate": ("issues", "count", "truncated"),
    "kb_recent": ("entries", "count"),
    "kb_search": ("count", "has_more", "results"),
    "kb_stats": ("kbs", "total_entries", "total_tags", "total_links", "type_counts"),
    "kb_tags": ("tag_count", "has_more", "tags"),
    "kb_timeline": ("count", "has_more", "events"),
    "social_newest": ("count", "newest"),
    "social_top": ("count", "top"),
    "sw_adrs": ("count", "total", "has_more", "adrs"),
    "sw_backlog": ("count", "total", "has_more", "items"),
    "sw_board": ("lanes",),
    "sw_component": ("count", "total", "has_more", "components"),
    "sw_epics": ("count", "epics"),
    "sw_milestones": ("count", "milestones"),
    "sw_pull_next": ("recommendation", "reason", "wip_status"),
    "sw_refine": ("summary", "items"),
    "sw_review_queue": ("items", "count", "over_limit"),
    "task_list": ("count", "tasks"),
    "wiki_quality_stats": (
        "total_articles",
        "quality_distribution",
        "review_status_distribution",
        "review_queue_size",
    ),
    "wiki_review_queue": ("count", "queue"),
    "wiki_stubs": ("count", "stubs"),
    "zettel_inbox": ("count", "inbox"),
}


def _blank_enumeration_field(value: Any) -> Any:
    """A list field is projected to identity-only (sorted KB names, or
    sorted (kb_name, id) pairs); anything else (a bare count, or a list
    whose shape `_project_identity_only` doesn't recognise) is blanked to
    the fixed marker.

    ALWAYS tries to project now (#476 round-2 blocker 1) -- there is no
    more `unscoped`/`project_identity` conditional. That conditional
    existed only because a per-KB read case's listing, run against the
    SAME shared world every write-tool case also mutated, had a row
    presence/count that was accumulated, order-dependent noise, not a
    stable authorization fact. With read cases isolated onto their own,
    never-written-to `world` (see `conftest.py`), that is no longer true:
    every read case's listing is now a pure function of what `world.py`
    seeded plus that principal's own scoping, so identity projection is
    always safe, and skipping it for a per-KB call (as round 1 did) was
    exactly what let mutation 6 (a search naming one KB but returning every
    KB's rows) pass silently.
    """
    if isinstance(value, list):
        projected = _project_identity_only(value)
        if projected is not None:
            return projected
    return NORMALISED_ENUMERATION_CONTENT


def _project_enumeration_fields(body: Any, fields: tuple[str, ...] | None) -> Any:
    """Replace each of `fields` (a top-level key of `body`, if `body` is a
    dict) with its identity-only projection or blanked marker -- run on the
    RAW body, before `normalize()` ever sees it. Order matters (#476 blocker
    2's second bug, found fixing the first): `normalize()` used to run
    first, so by the time this projection ran, a field whose real value was
    a private KB's row had often already been destroyed by a global,
    key-name-only rule (now removed). Projecting first means this function
    always sees the real list the handler returned."""
    if not fields or not isinstance(body, dict):
        return body
    out = dict(body)
    for field in fields:
        if field in out:
            out[field] = _blank_enumeration_field(out[field])
    return out


def normalize_mcp_result(tool_name: str, result: Any, *, tmpdir: str) -> Any:
    """For a tool in `ENUMERATION_SENSITIVE_FIELDS`, each listed top-level
    field is projected to identity-only (a list) or blanked (a scalar
    count) FIRST, against the raw result -- then `normalize()` runs over
    what's left (timestamps, generated ids, paths, scores). Projecting
    first is required, not cosmetic: `normalize()` has no rule left that
    would destroy a list, but the ORDER is still the fix for #476 blocker
    2's root cause, and future-proofs against a new global rule being added
    to `normalize()` without checking whether it runs before or after this.

    Only touches a SUCCESS body: a refusal (`_dispatch_tool`'s
    `error`/`error_code`/`retryable`/`suggestion` envelope) never has any of
    the enumeration-sensitive field names, so the authorization outcome
    (was this call allowed, and *which rows* it was allowed to see) is
    exactly what stays pinned either way.
    """
    fields = ENUMERATION_SENSITIVE_FIELDS.get(tool_name)
    if fields and isinstance(result, dict) and "error" in result:
        fields = None  # a refusal envelope: nothing to project
    projected = _project_enumeration_fields(result, fields)
    return normalize(projected, tmpdir=tmpdir)


# -- REST enumeration-sensitive routes ---------------------------------------
#
# The same problem as the MCP table above, for REST: a route's success body
# is either IDENTITY-BASED (depends only on the one row the call names --
# `GET /api/entries/{entry_id}` against `rest_calls._entry_for`'s fixed
# fixture id, `PUT`/`PATCH` against `_writable_target`'s disposable-but-
# stable-per-case id) or ENUMERATION-SENSITIVE (a count, list or aggregate
# over "everything in a KB" -- `GET /api/entries`, `GET /api/tags`, `GET
# /api/stats`, every QA/search/link-discovery route). 33 of the 66 KB-bearing
# REST routes are enumeration-sensitive by this test; keyed by `(method,
# path)`, the same granularity `rest_calls.REST_CALL_SPECS` already uses, so
# the two tables can be read side by side.
#
# Identity-based routes are deliberately absent from this table: their body
# should stay exactly as pinned, because a change in the ONE row's content
# (not its count, its presence) is real information (`GET
# /api/entries/{entry_id}`, `GET /api/daily/{date_str}`, `POST /api/entries`
# and its `PUT`/`PATCH`/`DELETE` siblings, `POST /api/starred`, `POST
# /api/entries/resolve-batch`, `GET /api/entries/resolve`, `GET
# /api/kbs/{kb_name}/schema`, `GET /api/kbs/{kb_name}/orient`'s non-list
# fields, `POST /api/kbs/{kb_name}/export`, `POST /api/tasks/{task_id}/claim`
# -- each names its OWN row/target, not "everything").
NORMALISED_REST_ENUMERATION_CONTENT = "<ENUMERATION_CONTENT>"

REST_ENUMERATION_SENSITIVE_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("GET", "/api/collections"): ("collections", "total"),
    ("GET", "/api/daily/dates"): ("dates",),
    ("GET", "/api/entries"): ("entries", "total"),
    ("GET", "/api/entries/export"): ("entries", "total"),
    ("GET", "/api/entries/titles"): ("entries",),
    ("GET", "/api/entries/wanted"): ("count", "pages"),
    ("GET", "/api/entries/{entry_id}/blocks"): ("blocks", "total"),
    ("GET", "/api/entries/{entry_id}/versions"): ("count", "versions"),
    ("GET", "/api/graph"): ("nodes", "edges"),
    # "total" was wrong here -- this route's top-level list field is "kbs",
    # not "total" (which doesn't exist in its body at all); found while
    # fixing #476 blocker 2. "kbs" is now projected to identity-only (sorted
    # KB names) by _project_identity_only, so a private KB's NAME leaking
    # into this list still shows up as a mismatch -- VOLATILE_CONTENT_KEYS
    # alone (entries/indexed/last_indexed/source) never caught that, since
    # it blanks only those four keys' VALUES, not whether an extra KB dict
    # is present in the list at all.
    ("GET", "/api/kbs"): ("kbs",),
    # NOT "source" -- see VOLATILE_CONTENT_KEYS's comment (#476 blocker 6):
    # source is exactly what #491's bug corrupts, and must stay pinned.
    ("GET", "/api/kbs/{kb_name}"): ("entries", "indexed", "last_indexed"),
    ("GET", "/api/kbs/{kb_name}/changes"): ("changes", "summary"),
    ("GET", "/api/kbs/{kb_name}/health"): ("entry_count", "file_count", "healthy", "last_indexed"),
    ("GET", "/api/kbs/{kb_name}/orient"): ("recent", "top_tags", "total_entries", "types"),
    ("GET", "/api/kbs/{kb_name}/templates"): ("templates", "total"),
    ("GET", "/api/links/batch-suggest"): ("count", "pairs"),
    ("GET", "/api/links/discover-neighbors"): ("count", "discoveries"),
    ("GET", "/api/qa/coverage"): ("assessed", "by_status", "coverage_pct", "total", "unassessed"),
    ("GET", "/api/qa/status"): (
        "issues_by_rule",
        "issues_by_severity",
        "total_entries",
        "total_issues",
    ),
    ("GET", "/api/qa/validate"): ("checked", "issues", "total"),
    ("GET", "/api/qa/validate/{entry_id}"): ("issues",),
    ("GET", "/api/repos"): ("repos",),
    ("GET", "/api/search"): ("count", "results"),
    ("GET", "/api/starred"): ("count", "starred"),
    ("GET", "/api/stats"): ("kbs", "total_entries", "total_links", "total_tags", "type_counts"),
    ("GET", "/api/tags"): ("count", "tags"),
    ("GET", "/api/tags/tree"): ("tree",),
    ("GET", "/api/tasks"): ("count", "tasks"),
    ("GET", "/api/timeline"): ("count", "events"),
    ("POST", "/api/collections/query-preview"): ("entries", "total"),
    ("POST", "/api/entries/batch"): ("entries", "found", "not_found"),
    ("POST", "/api/entries/import"): ("entries", "error_details", "errors", "imported"),
    # Not enumeration content, but the same fix: a DB auto-increment primary
    # key, not a caller-chosen or world-fixture id (contrast READABLE_ENTRY),
    # so its value depends on how many other reviews any other case already
    # created in the same process -- found via a 1-vs-3 mismatch under
    # pytest-xdist -n4 (worker composition changes how many earlier POSTs
    # ran first).
    ("POST", "/api/reviews"): ("id",),
}


def _kbs_route_identity_only(kbs: Any) -> Any:
    """`GET /api/kbs`'s `kbs` field, projected: keep each item's `name`
    (identity -- a private KB appearing here at all is the leak this whole
    harness exists to catch), blank its volatile per-item fields
    (`entries`, `indexed`, `last_indexed`), drop the rest (`path`, already
    `<TMPDIR>`-normalised by `normalize()` but still noisy; `description`,
    `read_only`, `shortname`, `default_role` are stable config, kept)."""
    if not isinstance(kbs, list) or not all(isinstance(k, dict) for k in kbs):
        return NORMALISED_ENUMERATION_CONTENT
    return sorted(
        (
            {
                k: (NORMALISED_CONTENT if k in ("entries", "indexed", "last_indexed") else v)
                for k, v in kb.items()
                if k != "path"
            }
            for kb in kbs
        ),
        key=lambda kb: kb.get("name", ""),
    )


def _blank_rest_field(method: str, path: str, field: str, value: Any) -> Any:
    if (method, path) == ("GET", "/api/kbs") and field == "kbs":
        return _kbs_route_identity_only(value)
    return _blank_enumeration_field(value)


def normalize_rest_body(method: str, path: str, body: Any, *, tmpdir: str) -> Any:
    """For `(method, path)` in `REST_ENUMERATION_SENSITIVE_FIELDS`, each
    listed top-level field is projected to identity-only (a list of KB
    names, or (kb_name, id) pairs) or blanked (a scalar count) FIRST,
    against the raw body -- then `normalize()` runs over what's left
    (timestamps, generated ids, paths, scores). Projecting first is the
    fix for #476 blocker 2: `normalize()` used to run first and destroy a
    list field via a global, key-name-only rule before this projection
    ever saw it (see the module and `normalize()` docstrings).

    ALWAYS projects now (#476 round-2 blocker 1) -- see
    `_blank_enumeration_field`'s docstring for why the old
    `unscoped`/`project_identity` conditional is gone: read cases run
    against their own, never-written-to world now, so every listing's row
    presence is a stable fact, not accumulated noise.

    Only touches a body that is not a refusal shape. A refusal is
    `{"detail": ...}` (`HTTPException`) or `{"code", "message"}` (the
    central `PyriteError` handler); neither ever carries any of the
    enumeration-sensitive field names above (they are all success-envelope
    fields), so checking for `"detail"`/`"code"` at the top level is enough
    to tell the two apart without a second lookup table.
    """
    fields = REST_ENUMERATION_SENSITIVE_FIELDS.get((method, path))
    if (
        fields
        and isinstance(body, dict)
        and "detail" not in body
        and not ("code" in body and "message" in body)
    ):
        projected = dict(body)
        for field in fields:
            if field in projected:
                projected[field] = _blank_rest_field(method, path, field, projected[field])
    else:
        projected = body
    return normalize(projected, tmpdir=tmpdir)
