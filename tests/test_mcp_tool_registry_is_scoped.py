"""Every MCP tool is scoped to the KBs its caller may read -- structurally.

The REST side has `tests/test_read_scoping_is_structural.py`, which walks the
app's route table and fails on any `/api` route without a scoping dependency.
That walk **cannot see `/mcp`**: it is a `Mount`, a whole sub-application with
no FastAPI `dependant` tree, so it is recorded in that file's
`UNREACHABLE_BY_THIS_WALK` rather than reviewed. This file is the mechanism
that covers it (#201, criterion 8).

**What this gate walks instead: the tool registry.** MCP has no per-tool
dependency graph either. What it does have is `PyriteMCPServer.tools` -- a
registry of every tool, core *and plugin*, with its declared JSON input
schema -- and exactly one runtime chokepoint they all pass through,
`_dispatch_tool`. So the gate reads each tool's schema, collects the
parameters by which a caller can name a KB, and asserts each one is a name
`_dispatch_tool` actually checks.

**The failure this prevents.** A new tool -- or a new plugin's tool -- that
takes its KB under a name nobody added to the chokepoint would serve private
KB content to a scoped caller and every behavioural test would still pass,
because no test names a tool that does not exist yet. That is precisely how
`/mcp` came to be the one KB-content surface with no scoping at all. This is
the MCP counterpart of `KB_PARAM_NAMES` pinning the REST resolver.

**Two halves, as on the REST side.** `KB_BEARING_PARAMETERS` is the set of
parameter names that name a KB; the gate fails if a tool declares a
KB-looking parameter outside it (a *new name* nobody wired up), and fails
if the chokepoint stops checking one that is in it (scoping *removed*).
`CROSS_KB_TOOLS` covers the other shape: a tool that takes no KB parameter
at all spans every KB by construction, so it must filter by the readable
set instead of refusing. Each entry carries a reason, exactly like
`ALLOWLIST` in the REST file -- an entry without one is a hole nobody can
review.
"""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer

TIERS = ("read", "write", "admin")

# Parameter names by which a caller can name a KB. `_dispatch_tool` must
# check **every** value found under these names -- not the first -- so that
# naming a readable KB alongside a private one buys nothing (the
# `_resolve_kb_names` rule from #180).
#
# `kb_names` is plural -- a *list* of KB names, on the
# journalism-investigation plugin's `investigation_search_all` and
# `investigation_find_duplicates`. This gate is what found it: the spike's
# hand inventory classified tools by `kb_name`/`kb` and missed it entirely,
# which is the whole argument for enumerating the registry rather than
# trusting a list.
KB_BEARING_PARAMETERS = {"kb_name", "kb", "source_kb", "target_kb", "center_kb", "kb_names"}

# Parameters whose name contains "kb" but which do not name a KB. Listed so
# the heuristic below stays sharp: without this, the gate either fires on
# them forever or has to be loosened until it stops catching real ones.
NOT_KB_NAMES = {
    "kb_type": "a KB *type* (generic/software/...), not a KB name -- kb_registry_add",
}

# Tools that declare no KB-bearing parameter at all. Each therefore spans
# every KB by construction and cannot be covered by refusing a named KB: it
# must *filter* to the readable set instead. EVERY entry carries a reason,
# and says which of the two it is. A tool that serves KB content and neither
# refuses nor filters does not belong here -- scope it.
CROSS_KB_TOOLS: dict[str, str] = {
    # -- filters: takes the readable set and narrows its own results -------
    "kb_list": (
        "filters. Spans every KB: returns the KB inventory itself, where the "
        "listing *is* the leak -- the spike's live run had it naming "
        "private-kb to a plain read-tier peer."
    ),
    "kb_timeline": (
        "filters. Spans every KB: dated events across the index, via "
        "KBService.get_timeline(kb_names=...). count/has_more computed after."
    ),
    "kb_batch_read": (
        "filters. Names its KBs inside the `entries` array, not by a KB "
        "parameter, so the chokepoint cannot see them. Unreadable pairs are "
        "dropped into the existing not_found list -- an *error* would itself "
        "reveal that the KB exists."
    ),
    "kb_stats": (
        "filters. Spans every KB: per-KB rows and totals (entries, tags, "
        "links, type counts), via IndexManager.get_index_stats(kb_names=...) "
        "-- the same scoping as REST's /api/stats, so a private KB adds "
        "neither its name nor its rows."
    ),
    "social_reputation": (
        "filters. Spans every KB: a user's score is summed from votes on "
        "their entries and per-KB log adjustments, so a private KB's votes "
        "are derived data from it; narrowed with kb_scope_clause, and log "
        "rows with no KB count only for an unscoped caller."
    ),
    # -- serves no KB content ----------------------------------------------
    "kb_index_job_status": "serves no KB content: background index job state, keyed by job id.",
    "kb_registry_add": "serves no KB content: admin-tier KB registration.",
    "kb_registry_remove": "serves no KB content: admin-tier KB registration.",
    "kb_registry_reindex": "serves no KB content: admin-tier reindex trigger.",
    "kb_registry_health": "serves no KB content: admin-tier registry health.",
}

# Tools whose KB parameter is **optional**: they name a KB when given one
# (the chokepoint refuses it if unreadable) but span every KB when it is
# omitted. That second case is not covered by refusing a name, so each must
# either filter or be refused outright for a scoped caller.
#
# `_dispatch_tool` fails closed here: a scoped caller that names no KB and
# reaches a handler which cannot filter is refused, rather than served
# across the whole index. An unscoped caller -- global admin, operator API
# key, local stdio -- is unaffected.
# This list is longer than the spike expected, and that is the finding. The
# spike classified tools by whether they *declare* a KB parameter and
# measured 63 of 70 read-tier tools as "covered by the chokepoint". But 45
# plugin tools declare `kb_name` **without requiring it**, and with it
# omitted they span the whole index: `sw_board` shows every KB's backlog,
# `wiki_stubs` every KB's stubs, `investigation_entities` every KB's
# entities. Declaring a KB parameter is not the same as being scoped by one.
#
# Their handlers live in extensions/, outside this theme's footprint, so
# they are refused rather than filtered. Giving each a readable set is the
# follow-up; refusing is what makes 0.24.2 safe without touching six
# extensions in a security release.
_PLUGIN_REFUSAL = (
    "refused for a scoped caller. Declares `kb_name` but does not require "
    "it, so it spans every KB when omitted, and its handler in {module} "
    "takes no readable set. Filtering it means widening that plugin, which "
    "is outside #201's footprint; refused meanwhile so it cannot serve "
    "across KBs the caller may not read."
)

OPTIONAL_KB_TOOLS: dict[str, str] = {
    # -- core, admin tier ---------------------------------------------------
    # Unreachable by a scoped caller in practice: the admin tier is only
    # served to a caller whose global role is "admin", and a global admin is
    # unscoped (readable_kbs is None). Listed because that reasoning is a
    # property of the tier model, which could change, and the gate should
    # notice if it does.
    "kb_index_sync": (
        "refused for a scoped caller. Admin tier, and reachable only by a "
        "global admin, who is unscoped -- so the refusal is unreachable "
        "today. Syncs every KB when kb_name is omitted."
    ),
    "kb_manage": (
        "refused for a scoped caller. Admin tier, same reasoning as "
        "kb_index_sync; its `discover` action lists KBs on disk."
    ),
    # -- plugins ------------------------------------------------------------
    **{
        name: _PLUGIN_REFUSAL.format(module=module)
        for module, names in {
            "extensions/journalism-investigation": (
                # The write-path tools keep the fail-closed listing: a caller
                # who may write a KB can read it, so the tier guard covers the
                # read. The cross-KB and single-KB read tools filter by the
                # readable set now.
                "investigation_bulk_edges",
                "investigation_create_claim",
                "investigation_create_entity",
                "investigation_create_event",
                "investigation_ftm_import",
                "investigation_log_source",
                "investigation_promote_claim",
                "investigation_start",
            ),
        }.items()
        for name in names
    },
}


@pytest.fixture(scope="module")
def servers():
    """One real `PyriteMCPServer` per tier, with plugin tools registered.

    Plugins are the reason this gate exists in this form: 40 of 41 read-tier
    plugin tools declare a primary `kb_name`/`kb`, so an extension shipping
    an unscoped tool is a live risk, and only a walk over the real registry
    sees it.
    """
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "gate-kb").mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="gate-kb", path=tmp / "gate-kb", kb_type="generic")],
            settings=Settings(index_path=tmp / "index.db"),
        )
        built = {t: PyriteMCPServer(config=config, tier=t) for t in TIERS}
        try:
            yield built
        finally:
            for s in built.values():
                s.close()


def _declared_parameters(meta):
    return set((meta.get("inputSchema") or {}).get("properties", {}) or {})


def _claiming(kind):
    """Every listed tool whose reason opens with this claim.

    Each entry in either list says which of three things it is -- "filters",
    "serves no KB content", or "refused for a scoped caller" -- and the tests
    below hold it to that claim against the code, so the reason is enforced
    rather than decorative.
    """
    return {
        name
        for name, reason in (*CROSS_KB_TOOLS.items(), *OPTIONAL_KB_TOOLS.items())
        if reason.startswith(kind)
    }


def _optional_kb_tools(servers):
    """Tools whose KB parameter is declared but not required: they span every
    KB when the caller omits it."""
    found = {}
    for _tier, name, meta in _all_tools(servers):
        schema = meta.get("inputSchema") or {}
        props = set(schema.get("properties") or {})
        required = set(schema.get("required") or [])
        if (props & KB_BEARING_PARAMETERS) and not (required & KB_BEARING_PARAMETERS):
            found[name] = meta
    return found


def _all_tools(servers):
    """(tier, tool name, meta) for every tool at every tier."""
    for tier in TIERS:
        for name, meta in servers[tier].tools.items():
            yield tier, name, meta


def test_no_tool_names_a_kb_by_a_parameter_the_chokepoint_ignores(servers):
    """The gate proper: a KB-bearing parameter name nobody wired up.

    A tool taking its KB as, say, `knowledge_base` or `kb_id` would sail
    past `_dispatch_tool`'s check and serve private content. Fails here
    instead of shipping.
    """
    unknown = {}
    for tier, name, meta in _all_tools(servers):
        for param in _declared_parameters(meta):
            if param in KB_BEARING_PARAMETERS or param in NOT_KB_NAMES:
                continue
            if "kb" in param.lower().split("_") or param.lower().endswith("_kb"):
                unknown.setdefault(param, []).append(f"{tier}:{name}")
    assert not unknown, (
        "tools declare KB-bearing parameters that _dispatch_tool does not "
        f"check: {unknown}. Either add the name to KB_BEARING_PARAMETERS *and* "
        "to pyrite.server.mcp_server.KB_ARGUMENT_NAMES so the chokepoint "
        "enforces it, or record it in NOT_KB_NAMES with a reason if it does "
        "not name a KB."
    )


def test_the_chokepoint_checks_every_name_this_file_claims():
    """`KB_BEARING_PARAMETERS` above is the contract the previous test
    enforces; here it is checked against the chokepoint itself, so the two
    cannot drift apart silently. The REST counterpart is
    `test_the_resolver_reads_every_location_this_file_claims`."""
    from pyrite.server.mcp_server import KB_ARGUMENT_NAMES

    assert set(KB_ARGUMENT_NAMES) == KB_BEARING_PARAMETERS


def test_every_kb_content_tool_either_names_a_kb_or_is_a_listed_cross_kb_tool(servers):
    """A tool with no KB parameter spans every KB, so refusing a named KB
    cannot cover it -- it must filter, and it must be listed here saying so."""
    unlisted = sorted(
        {
            f"{tier}:{name}"
            for tier, name, meta in _all_tools(servers)
            if not (_declared_parameters(meta) & KB_BEARING_PARAMETERS)
            and name not in CROSS_KB_TOOLS
        }
    )
    assert not unlisted, (
        f"tools that name no KB and are not listed as cross-KB tools: {unlisted}. "
        "Such a tool spans every KB by construction: filter it by the readable "
        "set and add it to CROSS_KB_TOOLS with a reason."
    )


CLAIMS = ("filters", "serves no KB content", "refused for a scoped caller")


def test_every_optional_kb_tool_either_filters_or_is_listed_as_refused(servers):
    """The subtle half of this work, and the one a behavioural test misses.

    A tool that *declares* `kb_name` looks scoped -- the chokepoint refuses
    an unreadable value. But the schemas make it optional on 20-odd tools,
    and with it omitted they span the whole index: `kb_get` walked every KB
    in config order, `kb_read_body` served body text from wherever it
    landed, `list_edge_types` named every KB and counted its entries. Each
    must take the readable set, or be listed in OPTIONAL_KB_TOOLS as refused.
    """
    import inspect

    from pyrite.server.mcp_server import READABLE_KBS_KWARG

    server = servers["admin"]
    unhandled = []
    for name in sorted(_optional_kb_tools(servers)):
        if name in OPTIONAL_KB_TOOLS or name in CROSS_KB_TOOLS:
            continue
        params = inspect.signature(server.tools[name]["handler"]).parameters
        if READABLE_KBS_KWARG not in params:
            unhandled.append(name)
    assert not unhandled, (
        f"tools whose KB parameter is optional, so they span every KB when it "
        f"is omitted, and whose handler cannot filter: {unhandled}. Give each a "
        f"`{READABLE_KBS_KWARG}` keyword and apply it, or list it in "
        "OPTIONAL_KB_TOOLS with a reason."
    )


def test_optional_kb_entries_are_not_stale(servers):
    live = set(_optional_kb_tools(servers))
    gone = sorted(n for n in OPTIONAL_KB_TOOLS if n not in live)
    assert not gone, (
        f"listed optional-KB tools that no longer exist or no longer have an "
        f"optional KB parameter: {gone}"
    )


def test_cross_kb_entries_all_carry_a_reason_that_makes_a_checkable_claim():
    """An entry without a reason is a hole nobody can review -- and a reason
    that does not say *which* of the three things the tool is cannot be
    held to anything. Each must open with one of CLAIMS, which the tests
    above then enforce against the code."""
    listed = {**CROSS_KB_TOOLS, **OPTIONAL_KB_TOOLS}
    missing = [name for name, reason in listed.items() if not (reason or "").strip()]
    assert not missing, f"listed tools with no reason: {missing}"

    vague = sorted(name for name, reason in listed.items() if not reason.startswith(CLAIMS))
    assert not vague, (
        f"cross-KB reasons that open with no checkable claim: {vague}. Begin "
        f"each with one of {CLAIMS}."
    )


def test_cross_kb_list_has_no_stale_entries(servers):
    """A listed tool that no longer exists -- or that has since grown a KB
    parameter -- must leave the list, so it stays an honest inventory."""
    live = {}
    for _tier, name, meta in _all_tools(servers):
        live[name] = _declared_parameters(meta)
    gone = sorted(n for n in CROSS_KB_TOOLS if n not in live)
    now_named = sorted(n for n in CROSS_KB_TOOLS if n in live and (live[n] & KB_BEARING_PARAMETERS))
    assert not gone, f"listed cross-KB tools that no longer exist: {gone}"
    assert not now_named, (
        f"listed cross-KB tools that now declare a KB parameter -- remove them: {now_named}"
    )


def test_not_kb_names_entries_all_carry_a_reason_and_still_exist(servers):
    missing = [n for n, reason in NOT_KB_NAMES.items() if not (reason or "").strip()]
    assert not missing, f"NOT_KB_NAMES entries with no reason: {missing}"

    declared = set()
    for _tier, _name, meta in _all_tools(servers):
        declared |= _declared_parameters(meta)
    stale = sorted(n for n in NOT_KB_NAMES if n not in declared)
    assert not stale, (
        f"NOT_KB_NAMES lists parameters no tool declares any more: {stale}. "
        "Remove them, or the exemption outlives what it exempted."
    )


def test_the_cross_kb_filtering_handlers_actually_accept_the_readable_set(servers):
    """A cross-KB tool that *says* it filters must have a handler that can.

    `_dispatch_tool` hands the readable set only to handlers that declare
    they take it. A listed tool whose handler does not accept the keyword is
    silently unfiltered -- the listing above would be a claim with nothing
    behind it.
    """
    import inspect

    from pyrite.server.mcp_server import READABLE_KBS_KWARG

    server = servers["admin"]
    not_wired = []
    for name in sorted(_claiming("filters")):
        handler = server.tools[name]["handler"]
        params = inspect.signature(handler).parameters
        if READABLE_KBS_KWARG not in params:
            not_wired.append(name)
    assert not not_wired, (
        f"cross-KB tools listed as filtering whose handler cannot receive the "
        f"readable set: {not_wired}. Give the handler a "
        f"`{READABLE_KBS_KWARG}: set[str] | None = None` keyword, or change its "
        "reason in CROSS_KB_TOOLS to say why it needs none."
    )


def test_tools_listed_as_refused_are_actually_refused(servers):
    """Fail closed, proven. A tool listed as refused for a scoped caller
    must really be refused -- otherwise the listing is a comment claiming
    safety while the tool serves the whole index."""
    server = servers["admin"]
    for name in sorted(_claiming("refused")):
        # A distinct client per tool: the rate limiter runs before scoping
        # (deliberately -- a probing caller should still be throttled), so a
        # shared client id would exhaust the admin-tier budget partway down
        # this list and report RATE_LIMITED instead of the refusal.
        result = server._dispatch_tool(
            name, {"query": "zebra"}, client_id=f"gate-{name}", readable_kbs={"gate-kb"}
        )
        assert result.get("error_code") == "NOT_FOUND", (
            f"{name} is listed as refused for a scoped caller but was not refused: {result}"
        )


def test_tools_listed_as_serving_no_kb_content_are_not_refused(servers):
    """The other direction: a tool declared content-free must still work for
    a scoped caller. Fail-closed that swallows these would be over-blocking
    dressed up as security."""
    from pyrite.server.mcp_server import NON_KB_CONTENT_TOOLS

    declared_free = _claiming("serves no KB content")
    missing = sorted(declared_free - set(NON_KB_CONTENT_TOOLS))
    assert not missing, (
        f"tools listed here as serving no KB content that "
        f"mcp_server.NON_KB_CONTENT_TOOLS does not exempt: {missing}. A scoped "
        "caller is refused them by the fail-closed rule."
    )


def test_the_dispatcher_refuses_an_unreadable_kb_under_every_checked_name(servers):
    """End-to-end over the chokepoint: for each name in the contract, a call
    naming an unreadable KB under that name is refused.

    The behavioural file drives real MCP sessions for a handful of tools;
    this covers the *names* exhaustively, so a name in the contract that the
    chokepoint reads but never acts on is caught here.
    """
    server = servers["admin"]
    for param in sorted(KB_BEARING_PARAMETERS):
        result = server._dispatch_tool(
            "kb_search",
            {"query": "x", param: "unreadable-kb"},
            client_id="test",
            readable_kbs={"gate-kb"},
        )
        assert result.get("error_code") == "NOT_FOUND", (
            f"naming an unreadable KB under `{param}` was not refused: {result}"
        )
        assert result["error"] == "KB 'unreadable-kb' not found", (
            f"`{param}`'s refusal is not the standard not-found payload: {result}"
        )


def test_an_unscoped_caller_is_unaffected(servers):
    """`readable_kbs=None` is the unscoped case -- a global admin, an
    operator API key, auth disabled, and local stdio. The chokepoint must
    not touch those calls at all."""
    server = servers["admin"]
    result = server._dispatch_tool(
        "kb_search", {"query": "x", "kb_name": "gate-kb"}, client_id="test"
    )
    assert result.get("error_code") != "NOT_FOUND"
