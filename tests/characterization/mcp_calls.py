"""Build a minimal, schema-valid argument dict for every KB-bearing MCP tool,
targeting one KB name (ADR-0037 theme 0).

Unlike REST's 66 routes (`rest_calls.py`, one handwritten spec per route:
FastAPI body models vary too much to fill generically and safely), MCP's 106
KB-bearing tools are overwhelmingly regular: `kb_name` is the KB argument,
required scalar fields get a placeholder, and required object/array fields
get the smallest structurally valid value. A handful of tools are irregular
enough (a differently-named KB argument, a KB argument nested inside a list
item, no KB argument at all because they filter a cross-KB `readable_kbs`,
or a required field that must be a specific enum value to reach anything
past the handler's own dispatch) to need an explicit override; every other
tool goes through the generic builder. `build_arguments` never silently
mistargets a tool: every KB-bearing tool either goes through `_EXPLICIT` or
is proven (by the module's own smoke check, run at import in tests) to
route its KB argument through the generic path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tests.characterization.world import World

# A tool whose generic build should route the KB into a non-"kb_name" key,
# or into more than one key ("defaults to source_kb" pairs).
_KB_ARG_OVERRIDE: dict[str, tuple[str, ...]] = {
    "kb_commit": ("kb",),
    "kb_push": ("kb",),
    "kb_batch_suggest": ("source_kb", "target_kb"),
    "kb_discover_neighbors": ("kb_name", "target_kb"),
    "kb_link": ("source_kb", "target_kb"),
}

# Tools with no top-level KB argument at all: the readable-set filters them
# (`_handler_takes_readable_kbs`), so "target one KB" means "restrict the
# call to a world where only that KB is readable" -- which the harness does
# by choosing which principal's `readable_kbs` to call with, not by an
# argument. These get an explicit, tool-specific argument builder instead of
# the generic one, so the golden still names a specific KB per case.
_EXPLICIT: dict[str, Callable[[World, str, str], dict[str, Any]]] = {
    "kb_list": lambda world, kb, call_key: {},
    "kb_timeline": lambda world, kb, call_key: {},
    "kb_stats": lambda world, kb, call_key: {},
    "social_reputation": lambda world, kb, call_key: {"user_id": "characterization-author"},
    "kb_batch_read": lambda world, kb, call_key: {
        "entries": [{"entry_id": _entry_for(world, kb), "kb_name": kb}]
    },
    "investigation_search_all": lambda world, kb, call_key: {"query": "zebra", "kb_names": [kb]},
    "investigation_find_duplicates": lambda world, kb, call_key: {"kb_names": [kb]},
    # A generic "action" placeholder does not reach the KB check: kb_manage
    # dispatches on `action` first, and only "validate" (among the actions
    # this world's fixtures can support without creating a schema) reaches
    # the per-KB read that matters here.
    "kb_manage": lambda world, kb, call_key: {"action": "validate", "kb_name": kb},
}


# Tools that mutate the entry their `entry_id` names, rather than only
# reading it -- these must NEVER be pointed at the world's permanently
# seeded fixture entries (READABLE_ENTRY/PRIVATE_ENTRY/...), which every
# read-only tool's golden also depends on for the rest of the session (the
# world is session-scoped for speed; nothing resets it between cases). Each
# gets its own disposable entry, created fresh per call_key, so a delete in
# one case can never make a later, unrelated case's read golden go from
# "found" to "not found" depending on execution order.
_MUTATES_ITS_ENTRY = {
    "kb_delete",
    "kb_update",
    "social_vote",
    "wiki_assess_quality",
    "wiki_protect",
    "wiki_submit_review",
}


def _entry_for(world: World, kb: str, tool_name: str = "", call_key: str = "") -> str:
    """The entry id to name for `kb`.

    Read-only tools (or any tool against a KB with no seeded entry) get the
    fixed id the world seeded -- `_entry_for`'s only job there is picking the
    right fixed id per KB, same as before. A tool in `_MUTATES_ITS_ENTRY`
    against a KB that HAS a seeded entry instead gets its own disposable
    entry, created here on first use and cached per (kb, call_key) so the
    same case's read-back (if any) sees what it just wrote.
    """
    from tests.characterization import world as w

    fixed = {
        w.READABLE: w.READABLE_ENTRY,
        w.PRIVATE: w.PRIVATE_ENTRY,
        w.READ_ONLY: w.READ_ONLY_ENTRY,
        w.NO_DEFAULT_ROLE: w.NO_DEFAULT_ROLE_ENTRY,
    }
    if tool_name not in _MUTATES_ITS_ENTRY or kb not in fixed:
        return fixed.get(kb, "characterization-missing-entry")
    return _disposable_entry(world, kb)


_DISPOSABLE_ENTRY_ID = "characterization-disposable-mutation-target"


def _disposable_entry(world: World, kb: str) -> str:
    """ONE disposable entry per KB (`_DISPOSABLE_ENTRY_ID`), shared by every
    mutating tool and every principal -- recreated if missing on every call,
    not created once and cached.

    Not one per (tool, principal, call_key): a KB-wide enumeration tool
    (`kb_batch_suggest`, `kb_list_entries`, `kb_stats`, ...) sees every entry
    in the KB, disposable or not, so its golden would grow every time a new
    disposable entry appeared -- one per case would mean 6 mutating tools x
    7 principals = up to 42 extra entries in READABLE alone, each shifting
    every enumeration tool's count and pair list. One disposable entry per
    KB adds a fixed, small, predictable amount of content instead -- and
    every mutating-tool case targets the SAME row, which is exactly what
    "can principal X write to KB Y" needs (the row's identity is not what
    is being characterized; whether the call is let through is).

    Recreated rather than cached-and-trusted: `kb_delete`'s own successful
    case (some principal in the matrix CAN delete it) would otherwise leave
    it missing for every later case that expects to find it -- ANY row's
    delete, coming from `_MUTATES_ITS_ENTRY`, would age out the shared
    fixture the same way the original shared fixture entries did; this
    closes that hole at its source rather than re-introducing it one level
    down.
    """
    from pyrite.services.kb_service import KBService

    svc = KBService(world.config, world.db)
    if svc.get_entry(_DISPOSABLE_ENTRY_ID, kb_name=kb) is None:
        try:
            svc.create_entry(
                kb,
                _DISPOSABLE_ENTRY_ID,
                "Disposable",
                "note",
                "created for a characterization case",
            )
        except Exception:
            pass  # a concurrent xdist worker created it first -- fine, reused as-is
    return _DISPOSABLE_ENTRY_ID


# Field names whose value must be UNIQUE per call, not just schema-valid: a
# create/write tool run against the same shared, session-scoped world under
# two different (principal, kb_state) cases would otherwise collide on its
# second run (kb_create's second call with the same title hits
# EntryExistsError instead of repeating the same golden outcome) -- the
# world is shared for speed (its docstring), so a case's own identity has to
# make it collide-proof rather than the world resetting between cases.
# `call_key` (passed in by the caller, unique per tool/principal/kb-state)
# is appended to these.
_UNIQUE_PER_CALL = {"title", "field", "location"}

_PLACEHOLDER_BY_NAME = {
    "title": "characterization-title",
    "assertion": "characterization-assertion",
    "message": "characterization-message",
    "summary": "characterization-summary",
    "location": "characterization-location",
    "assignee": "characterization-assignee",
    "status": "open",
    "to_status": "open",
    "outcome": "approved",
    "reviewer": "characterization-reviewer",
    "field": "title",
    "value": "characterization-value",
    "level": "protected",
    "quality": "good",
    "action": "add",
    # A valid value for every enum-checked field this world's tools
    # actually validate: an invalid one is fine for *most* tools (the KB
    # scoping check runs first regardless), but a few validate the enum
    # before the KB check and render the invalid-value error by interpolating
    # an unordered set/dict literal (e.g. "Must be one of {'a', 'b', 'c'}"),
    # whose iteration order is randomised per process (PYTHONHASHSEED) and
    # would make the golden flaky across runs for a reason that has nothing
    # to do with authorization -- itself a real, reported oddity; using a
    # valid value sidesteps it rather than masking it with a normaliser that
    # could just as easily launder a real status/code change.
    "edge_type": "ownership",  # investigation_promote_claim's VALID_EDGE_TYPES
    "reviewer_id": "characterization-reviewer",
    "entity_type": "organization",  # investigation_create_entity's valid_types
    "event_type": "investigation_event",  # investigation_create_event's valid_types
    "date": "2021-01-01",
    "query": "zebra",
    "reason": "characterization",
}


def _placeholder(name: str, schema: dict, call_key: str) -> Any:
    if name in ("entry_id", "task_id", "item_id", "epic_id", "claim_id", "source_id", "target_id"):
        return f"characterization-placeholder-id-{call_key}"
    if name in _UNIQUE_PER_CALL:
        base = _PLACEHOLDER_BY_NAME.get(name, f"characterization-{name}")
        return f"{base}-{call_key}"
    if name in _PLACEHOLDER_BY_NAME:
        return _PLACEHOLDER_BY_NAME[name]
    type_ = schema.get("type")
    if type_ == "array":
        item_schema = schema.get("items") or {}
        if item_schema.get("type") == "object":
            item_required = item_schema.get("required", [])
            return [
                {
                    k: _placeholder(k, item_schema.get("properties", {}).get(k, {}), call_key)
                    for k in item_required
                }
            ]
        return []
    if type_ == "object":
        return {}
    if type_ == "integer" or type_ == "number":
        return 1
    if type_ == "boolean":
        return False
    enum = schema.get("enum")
    if enum:
        return enum[0]
    return "characterization-placeholder"


def _generic_arguments(
    world: World, tool_name: str, kb: str, schema: dict, call_key: str
) -> dict[str, Any]:
    props: dict = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []
    kb_args = _KB_ARG_OVERRIDE.get(tool_name, ("kb_name",) if "kb_name" in props else ())

    args: dict[str, Any] = {}
    for name in required:
        if name in kb_args:
            continue  # filled below
        if name in ("entry_id",) and name not in kb_args:
            args[name] = _entry_for(world, kb, tool_name, call_key)
            continue
        args[name] = _placeholder(name, props.get(name, {}), call_key)
    for name in kb_args:
        args[name] = kb
    # A tool whose schema takes an optional `mode` (kb_search,
    # kb_discover_neighbors, kb_batch_suggest) defaults to "semantic" or
    # "hybrid" in its handler -- not in the schema, so a required-fields-only
    # fill never sets it. That path lazily loads a real sentence-transformers
    # model on first use; observed while building this harness, reusing that
    # loaded model from a later, unrelated call hung indefinitely (a
    # tokenizer-parallelism/fork interaction, not an authorization
    # behaviour). "keyword" is pinned explicitly wherever the schema offers
    # the choice, since the embedding path is irrelevant to what this
    # harness characterizes.
    if "mode" in props:
        args["mode"] = "keyword"
    return args


@dataclass(frozen=True)
class ToolCallSpec:
    tool_name: str
    build: Callable[[World, str], dict[str, Any]] = field(repr=False)


def build_arguments(
    world: World, tool_name: str, kb: str, *, call_key: str = "shared"
) -> dict[str, Any]:
    """The MCP call arguments for `tool_name` targeting `kb`. Every KB-bearing
    tool (`surfaces.kb_bearing_mcp_tool_names`) resolves here -- either
    through `_EXPLICIT` or the generic builder, verified (in
    `test_mcp_matrix.py`, run once per module) to produce a call that
    actually exercises `kb`'s scoping rather than short-circuiting on an
    unrelated validation failure first.

    `call_key` must be unique per (tool, principal, kb_state) case when the
    tool is a write: see `_UNIQUE_PER_CALL`. Read-only tools ignore it."""
    if tool_name in _EXPLICIT:
        return _EXPLICIT[tool_name](world, kb, call_key)
    schema = world.mcp_server.tools[tool_name]["inputSchema"]
    return _generic_arguments(world, tool_name, kb, schema, call_key)
