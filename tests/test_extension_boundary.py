"""Pin how far the bundled extensions reach into core (ratchet 1 of #1042).

ADR-0040 says an extension is a package that depends on a small, stable
surface of Pyrite. Today the bundled extensions also depend on core
internals directly: they construct the database class, load the process
config, resolve KBs through a core helper and use a private connection.
#384 holds the refactor that moves those calls behind PluginContext
services; this test holds only the measurement, so the refactor (and any
other change) can shrink the list but never grow it silently.

A site is one call (or, for `_raw_conn`, one attribute access) in one
function of one file under `extensions/*/src`, found by an AST walk. Sites
are keyed by file and function, never by line, so moving a call inside its
function is not a change. The count per key is pinned too: removing one of
two identical calls in a function is a shrink and must be recorded.

Every site in BASELINE carries a classification:

- "allowed-fallback": a `load_config` or `PyriteDB` call inside a plugin's
  `_get_db` / `_get_config` context fallback. The CLI has no PluginContext,
  so the fallback builds what the context would have handed it; this is
  the pattern tests/test_plugin_handlers_use_the_servers_config.py pins.
- "remove-384": a reach around the ADR-0040 boundary, to be removed under
  #384. The entry leaves BASELINE in the same change that removes the call.

The test fails in both directions: a site that is not listed (the boundary
grew, or an extension was added and reached into core), and a listed site
that is gone or changed count without its entry being updated (a shrink
that was not recorded). To shrink the ratchet, delete the call and its
BASELINE entry together.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SRC_FILES = "extensions/*/src/**/*.py"

# Call names that reach into core, mapped to the pattern they are pinned
# under. `_load_config` is a plugin-local alias of the same call and counts
# with it, as in tests/test_plugin_handlers_use_the_servers_config.py.
_CALL_PATTERNS = {
    "PyriteDB": "PyriteDB",
    "load_config": "load_config",
    "_load_config": "load_config",
    "require_kb": "require_kb",
    "Set": "Set",
    "Unset": "Unset",
}


def _scan_source(source: str) -> Counter[tuple[str, str]]:
    """Count boundary sites in one module's source, keyed by (function, pattern)."""
    tree = ast.parse(source)
    # Local name -> the dotted name it was imported from, so an aliased
    # import (`PyriteDB as DB`, then `DB(...)`) counts as the same site.
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module or ''}.{alias.name}".strip(".")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name

    sites: Counter[tuple[str, str]] = Counter()

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            scope = stack
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                scope = stack + [child.name]
            elif isinstance(child, ast.Lambda):
                scope = stack + ["<lambda>"]
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Name):
                    name = aliases.get(func.id, func.id).rsplit(".", 1)[-1]
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                else:
                    name = ""
                if name in _CALL_PATTERNS:
                    sites[(".".join(scope) if scope else "<module>", _CALL_PATTERNS[name])] += 1
            if isinstance(child, ast.Attribute) and child.attr == "_raw_conn":
                sites[(".".join(scope) if scope else "<module>", "_raw_conn")] += 1
            visit(child, scope)

    visit(tree, [])
    return sites


def _scan_repo() -> dict[tuple[str, str, str], int]:
    found: dict[tuple[str, str, str], int] = {}
    for path in sorted(REPO.glob(SRC_FILES)):
        rel = str(path.relative_to(REPO))
        for (function, pattern), count in _scan_source(path.read_text()).items():
            found[(rel, function, pattern)] = count
    return found


# (file, function, pattern) -> (count, classification). Measured on dev at
# f95f4718; the per-pattern totals are PyriteDB 43, load_config 53,
# _raw_conn 51, require_kb 52, Set 1, Unset 1.
BASELINE: dict[tuple[str, str, str], tuple[int, str]] = {
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_quality", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_quality", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_quality", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_review", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_review", "_raw_conn"): (
        2,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_review", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_review", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stats", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stats", "_raw_conn"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stats", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stats", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stubs", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stubs", "_raw_conn"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stubs", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stubs", "require_kb"): (
        1,
        "remove-384",
    ),
    (
        "extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py",
        "EncyclopediaPlugin._get_db",
        "PyriteDB",
    ): (1, "allowed-fallback"),
    (
        "extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py",
        "EncyclopediaPlugin._get_db",
        "load_config",
    ): (1, "allowed-fallback"),
    (
        "extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py",
        "EncyclopediaPlugin._mcp_quality_stats",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py",
        "EncyclopediaPlugin._mcp_review_queue",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py",
        "EncyclopediaPlugin._mcp_stubs",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/encyclopedia/src/pyrite_encyclopedia/plugin.py",
        "EncyclopediaPlugin._mcp_submit_review",
        "_raw_conn",
    ): (2, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "_get_plugin_with_db",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "_get_plugin_with_db",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "_get_plugin_with_db",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "bulk_edges",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "bulk_edges",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "bulk_edges",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "dedup",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "dedup",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "dedup",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "export_pack",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "export_pack",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "export_pack",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ftm_export",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ftm_export",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ftm_export",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ftm_import",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ftm_import",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ftm_import",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "investigation_status",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "investigation_status",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "investigation_status",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "money_flow",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "money_flow",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "money_flow",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ownership_chain",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ownership_chain",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "ownership_chain",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "promote_claim",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "promote_claim",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "promote_claim",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "qa_report",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "qa_report",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "qa_report",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "search_all",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "search_all",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "search_all",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "start_investigation",
        "PyriteDB",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "start_investigation",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "start_investigation",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/investigation_setup.py",
        "create_investigation",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/plugin.py",
        "JournalismInvestigationPlugin._get_config",
        "load_config",
    ): (1, "allowed-fallback"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/plugin.py",
        "JournalismInvestigationPlugin._get_db",
        "PyriteDB",
    ): (1, "allowed-fallback"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/plugin.py",
        "JournalismInvestigationPlugin._mcp_investigation_start",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/utils.py",
        "require_writable_kb",
        "require_kb",
    ): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_newest", "PyriteDB"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_newest", "_raw_conn"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_newest", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_newest", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_reputation", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_reputation", "_raw_conn"): (
        2,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_reputation", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_top", "PyriteDB"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_top", "_raw_conn"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_top", "load_config"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_top", "require_kb"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_vote", "PyriteDB"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_vote", "_raw_conn"): (2, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_vote", "load_config"): (1, "remove-384"),
    ("extensions/social/src/pyrite_social/cli.py", "social_vote", "require_kb"): (1, "remove-384"),
    (
        "extensions/social/src/pyrite_social/hooks.py",
        "after_delete_adjust_reputation",
        "_raw_conn",
    ): (3, "remove-384"),
    ("extensions/social/src/pyrite_social/hooks.py", "after_save_update_counts", "_raw_conn"): (
        2,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/plugin.py", "SocialPlugin._get_config", "load_config"): (
        1,
        "allowed-fallback",
    ),
    ("extensions/social/src/pyrite_social/plugin.py", "SocialPlugin._get_db", "PyriteDB"): (
        1,
        "allowed-fallback",
    ),
    ("extensions/social/src/pyrite_social/plugin.py", "SocialPlugin._mcp_newest", "_raw_conn"): (
        1,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/plugin.py", "SocialPlugin._mcp_post", "require_kb"): (
        1,
        "remove-384",
    ),
    (
        "extensions/social/src/pyrite_social/plugin.py",
        "SocialPlugin._mcp_reputation",
        "_raw_conn",
    ): (2, "remove-384"),
    ("extensions/social/src/pyrite_social/plugin.py", "SocialPlugin._mcp_top", "_raw_conn"): (
        1,
        "remove-384",
    ),
    ("extensions/social/src/pyrite_social/plugin.py", "SocialPlugin._mcp_vote", "_raw_conn"): (
        2,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "_query_entries", "_raw_conn"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_adrs", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_adrs", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_adrs", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_backlog", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_backlog", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_board_cmd", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_board_cmd", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_board_cmd", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_check_ready", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_check_ready", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_claim", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_claim", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_claim", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_components", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_components", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_components", "require_kb"): (
        1,
        "remove-384",
    ),
    (
        "extensions/software-kb/src/pyrite_software_kb/cli.py",
        "sw_context_for_item",
        "load_config",
    ): (1, "remove-384"),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_context_for_item", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_conventions", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_conventions", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_conventions", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_epic_detail", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_epic_detail", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_epics", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_epics", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_log_cmd", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_log_cmd", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_migrate_standards", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_migrate_standards", "Set"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_migrate_standards", "Unset"): (
        1,
        "remove-384",
    ),
    (
        "extensions/software-kb/src/pyrite_software_kb/cli.py",
        "sw_migrate_standards",
        "load_config",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/cli.py",
        "sw_migrate_standards",
        "require_kb",
    ): (1, "remove-384"),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_milestones", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_milestones", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_milestones", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_new_adr", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_new_adr", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_new_adr", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_prioritize", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_prioritize", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_pull_next", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_pull_next", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_refine_cmd", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_refine_cmd", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_review_cmd", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_review_cmd", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_review_queue", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_review_queue", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_review_queue", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_standards", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_standards", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_standards", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_submit", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_submit", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_submit", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_transition", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_transition", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_transition", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_validations", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_validations", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_validations", "require_kb"): (
        1,
        "remove-384",
    ),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._get_config",
        "load_config",
    ): (1, "allowed-fallback"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._get_db",
        "PyriteDB",
    ): (1, "allowed-fallback"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_adrs",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_backlog",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_board",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_check_ready",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_claim",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_component",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_conventions",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_epic_detail",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_epics",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_log",
        "require_kb",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_milestones",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_prioritize",
        "_raw_conn",
    ): (2, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_pull_next",
        "_raw_conn",
    ): (2, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_refine",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_review_queue",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_review",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_standards",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_submit",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_transition",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin._mcp_validations",
        "_raw_conn",
    ): (1, "remove-384"),
    (
        "extensions/software-kb/src/pyrite_software_kb/plugin.py",
        "SoftwareKBPlugin.get_orient_supplement",
        "_raw_conn",
    ): (3, "remove-384"),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_inbox", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_inbox", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_inbox", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_maturity", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_maturity", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_maturity", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_new", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_new", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_new", "require_kb"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_orphans", "PyriteDB"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_orphans", "load_config"): (
        1,
        "remove-384",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_orphans", "require_kb"): (
        1,
        "remove-384",
    ),
    (
        "extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py",
        "ZettelkastenPlugin._get_db",
        "PyriteDB",
    ): (1, "allowed-fallback"),
    (
        "extensions/zettelkasten/src/pyrite_zettelkasten/plugin.py",
        "ZettelkastenPlugin._get_db",
        "load_config",
    ): (1, "allowed-fallback"),
}


def test_extension_boundary_only_shrinks():
    actual = _scan_repo()
    expected = {key: count for key, (count, _classification) in BASELINE.items()}
    problems = []
    for key in sorted(set(actual) - set(expected)):
        problems.append(
            f"new site not in the pinned list: {key[0]} :: {key[1]} calls {key[2]} "
            f"x{actual[key]}. Route it through PluginContext (ADR-0040), or add it "
            "to BASELINE in tests/test_extension_boundary.py with a classification."
        )
    for key in sorted(set(expected) - set(actual)):
        problems.append(
            f"pinned site is gone: {key[0]} :: {key[1]} no longer calls {key[2]}. "
            "Remove its BASELINE entry in this change; the ratchet only shrinks."
        )
    for key in sorted(set(actual) & set(expected)):
        if actual[key] != expected[key]:
            problems.append(
                f"site count changed: {key[0]} :: {key[1]} calls {key[2]} "
                f"x{actual[key]}, pinned x{expected[key]}. Update the BASELINE "
                "count in this change so the shrink (or growth) is recorded."
            )
    assert problems == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        # A plain call by name.
        ("def f(db):\n    return PyriteDB('x')\n", {("f", "PyriteDB"): 1}),
        # The fully qualified call form.
        (
            "def f():\n    return pyrite.storage.database.PyriteDB('x')\n",
            {("f", "PyriteDB"): 1},
        ),
        # An aliased import counts as the same site.
        (
            "from pyrite.storage.database import PyriteDB as DB\n\ndef f():\n    return DB('x')\n",
            {("f", "PyriteDB"): 1},
        ),
        # A call at module level is keyed to the module.
        ("cfg = load_config()\n", {("<module>", "load_config"): 1}),
        # load_config with arguments is the same site as without.
        ("def f(p):\n    return load_config(p)\n", {("f", "load_config"): 1}),
        # A call inside a nested function is keyed by the full path.
        (
            "def outer():\n    def inner(kb):\n        return require_kb(kb)\n",
            {("outer.inner", "require_kb"): 1},
        ),
        # A call inside a lambda is keyed through the lambda.
        (
            "def f():\n    return lambda: load_config()\n",
            {("f.<lambda>", "load_config"): 1},
        ),
        # _raw_conn counts attribute access, including a chained call.
        (
            "def f(db):\n    return db._raw_conn.execute('select 1')\n",
            {("f", "_raw_conn"): 1},
        ),
        # Set/Unset count when constructed, not when only imported.
        (
            "from pyrite.storage.file_operations import Set, Unset\n",
            {},
        ),
        (
            "def f():\n    return [Set('a', 1), Unset('b')]\n",
            {("f", "Set"): 1, ("f", "Unset"): 1},
        ),
        # Two identical calls in one function are a count of two.
        (
            "def f(db):\n    db._raw_conn\n    db._raw_conn\n",
            {("f", "_raw_conn"): 2},
        ),
    ],
    ids=[
        "plain-name",
        "fully-qualified",
        "aliased-import",
        "module-level",
        "load-config-with-args",
        "nested-function",
        "lambda",
        "raw-conn-access",
        "set-unset-imported-only",
        "set-unset-constructed",
        "count-per-function",
    ],
)
def test_scan_source_shapes(source, expected):
    assert dict(_scan_source(source)) == expected
