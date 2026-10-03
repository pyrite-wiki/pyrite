"""Which tests are experimental: the one mapping (#657).

Source of truth for what is experimental: kb/designs/alpha-supported-surface.md
(approved by the maintainer, 2026-10-02). Each `Surface` below names a row of
that list and the tests that test it. The root conftest.py marks every
collected test whose node id matches here with ``@pytest.mark.experimental``;
nothing else applies the marker, and no test file carries it by hand.

What the marker means: the gating CI job (`test`, which `gate` needs) runs
``-m "not experimental"``; the `experimental` job runs the rest on every PR and
on `dev`, and fails only on a failure that is not in
tests/experimental_known_failures.txt. On `dev` a new failure files an
`experimental-broken` issue. The pre-push hook runs the gating set only.

Rules for this file:

- A test is experimental by **what it tests**, not by the surface it goes
  through. `test_write_surface_parity.py` drives REST, but tests the one
  write pipeline, so it is core.
- **Security properties are never experimental** (acceptance 2 of #657):
  authorization, read scoping, containment of paths and names inside their
  root, credential handling, the characterization oracle, and output
  escaping / request forgery defences. `NEVER_EXPERIMENTAL` lists every test
  of one, found by reading the files (docstrings and test bodies), and wins
  over any experimental pattern. A mixed file is split here, by node id: the
  feature cases are experimental, its security cases are listed below.
- When unsure, leave a test core: wrongly core costs a red merge, which is
  today's behaviour; wrongly experimental loses the gate.
- ``@pytest.mark.core`` tests (scripts/test-affected's smoke set) are never
  experimental.

Patterns: a pattern without ``::`` matches a test file path; one with ``::``
matches the whole node id. ``*`` is the only wildcard (brackets are literal,
so ``[postgres]`` matches a parameter id). tests/test_experimental_surface.py
checks every pattern still matches a collected test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache, cache

SURFACE_LIST = "kb/designs/alpha-supported-surface.md"


@dataclass(frozen=True)
class Surface:
    name: str
    section: str  # where the surface list places it
    paths: tuple[str, ...]


EXPERIMENTAL: tuple[Surface, ...] = (
    # -- Section 5 and 2: extensions ---------------------------------------
    Surface(
        "Extensions: software-kb, journalism-investigation, social, encyclopedia,"
        " zettelkasten; cascade (unsupported)",
        "5, 2 (`sw`, `investigation`, `social`, `wiki`, `zettel`, `cascade`)",
        (
            "extensions/*/tests/*",
            # Core-tree tests whose subject is an extension's behaviour.
            "tests/test_ji_edge_type_integration.py",
            "tests/test_orient_software.py",
            "tests/test_gates.py",  # software-kb's Definition of Ready / Done
        ),
    ),
    # -- Section 2 and 3: tasks --------------------------------------------
    Surface(
        "Tasks: `task` CLI group and `task_*` MCP tools",
        "2, 3",
        ("tests/test_task.py", "tests/test_task_*.py"),
    ),
    # -- Section 4: REST and the web UI -------------------------------------
    Surface(
        "REST (135 operations) and the web UI it serves",
        "4",
        (
            "tests/test_api_wikilinks.py",
            "tests/test_blocks_api.py",
            "tests/test_branding_endpoints.py",
            "tests/test_branding_service.py",
            "tests/test_endpoint_errors.py",
            "tests/test_entries_batch_read.py",
            "tests/test_link_discovery_endpoints.py",
            "tests/test_plugin_endpoints.py",
            "tests/test_qa_api.py",
            "tests/test_rest_put_echo.py",
            "tests/test_review_endpoints.py",
            "tests/test_server_write_embedding_debt.py",
            "tests/test_starred_entries.py",
            "tests/test_static_routes_with_a_built_dist.py",
            "tests/test_type_schemas_declared.py",
            "tests/test_web_clipper.py",
            "tests/test_settings.py::TestSettingsAPI::*",
            "tests/test_kb_commit.py::TestRESTCommitEndpoints::*",
            # test_rest_api.py's core-marked classes (TestKBEndpoints,
            # TestEntryEndpoints) are the app-factory smoke set and stay core.
            "tests/test_rest_api.py::TestCentralExceptionHandler::*",
            "tests/test_rest_api.py::TestSearchEndpoints::*",
            "tests/test_rest_api.py::TestTimelineEndpoints::*",
            "tests/test_rest_api.py::TestTagsAndActors::*",
            "tests/test_rest_api.py::TestAdminEndpoints::*",
        ),
    ),
    Surface(
        "Web UI, multi-user parts: collaboration, changes and review, usage tiers",
        "4",
        (
            "tests/test_collaboration_integration.py",
            "tests/test_collaboration_models.py",
            "tests/test_pending_changes.py",
            "tests/test_personal_kb.py",
            "tests/test_quota_service.py",
        ),
    ),
    Surface(
        "Streamlit (`ui_streamlit.py`, `pyrite/ui`), unsupported",
        "4",
        ("tests/test_ui_data.py",),
    ),
    Surface(
        "AI endpoints (`/api/ai/*`) and the LLM service behind them",
        "4",
        (
            "tests/test_ai_endpoints.py",
            "tests/test_ai_quota_enforcement.py",
            "tests/test_llm_service.py",
            "tests/test_llm_usage_service.py",
            "tests/test_llm_usage_endpoints.py",
            "tests/test_llm_rubric_evaluator.py",
        ),
    ),
    Surface(
        "`/site` static HTML cache and the sitemap",
        "4",
        (
            "tests/test_site_cache.py",
            "tests/test_sync_wait_rerenders_site_cache.py",
            "tests/test_sitemap.py::TestRobotsTxt::*",
        ),
    ),
    Surface(
        "Live updates (`/ws`)",
        "4",
        ("tests/test_websocket_delivery.py",),
    ),
    # -- Section 3: MCP beyond the small core --------------------------------
    Surface(
        "MCP prompts and resources",
        "3",
        ("tests/test_mcp_prompts.py", "tests/test_mcp_resources_session.py"),
    ),
    Surface(
        "MCP tools outside the core 15: finders, QA, bulk create, vocabulary, admin",
        "3",
        (
            "tests/test_mcp_finders.py",
            "tests/test_mcp_list_edge_types.py",
            "tests/test_kb_bulk_create_description.py",
            "tests/test_mcp_server.py::TestMCPQATools::*",
            "tests/test_mcp_server.py::TestKBRegistryAddDuplicateName::*",
            "tests/test_kb_commit.py::TestMCPCommitTools::*",
        ),
    ),
    # -- Section 5: backends and deployment ---------------------------------
    Surface(
        "Postgres backend",
        "5",
        (
            "tests/backends/test_postgres_*.py",
            "tests/backends/test_backend_conformance.py::*[postgres]",
        ),
    ),
    Surface(
        "Overlay backend and worktrees (ADR-0024, ADR-0044)",
        "5",
        ("tests/test_overlay_backend.py", "tests/test_worktree_service.py"),
    ),
    Surface(
        "Docker and one-click deploys (the demo's seed)",
        "5",
        # test_demo_public_kbs_upgrade.py is all security (see below).
        ("tests/test_demo_seed_public_kbs.py",),
    ),
    # `.claude-plugin/plugin.json`: no test reads it.
    Surface("Claude Code plugin", "5", ()),
    # -- Section 2: experimental CLI groups ---------------------------------
    Surface(
        "`repo` CLI group and the repo service",
        "2",
        (
            "tests/test_repo_service.py",
            "tests/test_repo_endpoints.py",
            "tests/test_admin_cli.py::TestRepo*",
            "tests/test_cli_json_output.py::test_repo_*",
        ),
    ),
    Surface(
        "`auth` CLI group",
        "2",
        ("tests/test_cli_json_output.py::test_auth_*",),
    ),
    Surface(
        "`extension` CLI group",
        "2",
        ("tests/test_extension_commands.py", "tests/test_admin_cli.py::TestExtension*"),
    ),
    Surface(
        "`export` CLI group and the renderers",
        "2",
        (
            "tests/test_export_service.py",
            "tests/test_export_to_repo.py",
            "tests/test_collection_export.py",
            "tests/test_quartz_renderer.py",
            "tests/test_notebooklm_renderer.py",
            "tests/test_import_export.py::TestExportRoundTrip::*",
        ),
    ),
    Surface(
        "`collections` CLI group",
        "2",
        (
            "tests/test_collections.py",
            "tests/test_collection_query.py",
            "tests/test_collection_views.py",
            "tests/test_collections_plugin.py",
        ),
    ),
    # The protocol *type system* (tests/test_protocol*.py) is core (principle
    # 6); only the `protocol` CLI group is experimental, and no test drives it.
    Surface("`protocol` CLI group", "2", ()),
    Surface("`mcp-setup` (experimental until #582)", "2", ()),
)


# The kinds of security property acceptance 2 names, plus the output and
# request defences that are security properties by the same reasoning.
SECURITY_PROPERTIES = (
    "authorization",
    "read scoping",
    "containment",
    "credential handling",
    "characterization oracle",
    "output escaping and request forgery",
)
AUTHZ, SCOPE, CONTAIN, CRED, ORACLE, ESCAPE = SECURITY_PROPERTIES

# Every test of a security property, found by reading the files. Whole files
# first, then the security cases inside files that are otherwise experimental.
NEVER_EXPERIMENTAL: dict[str, str] = {
    # -- the characterization oracle (ADR-0037 theme 0) --
    "tests/characterization/*": ORACLE,
    # -- authorization --
    "tests/test_access_policy.py": AUTHZ,
    "tests/test_anonymous_and_missing_kb_writes.py": AUTHZ,
    "tests/test_api_authorization_coverage.py": AUTHZ,
    "tests/test_api_key_role_with_auth_enabled.py": AUTHZ,
    "tests/test_api_security.py": AUTHZ,
    "tests/test_api_tiers.py": AUTHZ,
    "tests/test_auth_bootstrap.py": AUTHZ,
    "tests/test_auth_user_management.py": AUTHZ,
    "tests/test_authz_authorize.py": AUTHZ,
    "tests/test_daily_endpoints.py": AUTHZ,  # a read-tier view never writes
    "tests/test_default_role_change_takes_effect.py": AUTHZ,
    "tests/test_deploy_templates_require_auth.py": AUTHZ,
    "tests/test_every_entry_point_passes_the_policy.py": AUTHZ,
    "tests/test_kb_access_control.py": AUTHZ,
    "tests/test_kb_permissions.py": AUTHZ,
    "tests/test_kb_policy_availability.py": AUTHZ,
    "tests/test_kb_scoped_writes.py": AUTHZ,
    "tests/test_kb_write_guard_body.py": AUTHZ,
    "tests/test_kb_write_guard_is_structural.py": AUTHZ,
    "tests/test_local_container_loopback.py": AUTHZ,
    "tests/test_mcp_routes.py": AUTHZ,  # SSE transport authentication
    "tests/test_mcp_tiers.py": AUTHZ,
    "tests/test_mcp_write_scoping.py": AUTHZ,
    "tests/test_read_cli.py": AUTHZ,  # pyrite-read exposes no write
    "tests/test_repo_kb_authorization.py": AUTHZ,
    "tests/test_repo_subscribe_never_adopts_existing_kb.py": AUTHZ,
    "tests/test_request_guard.py": AUTHZ,
    "tests/test_role_ladder_has_one_home.py": AUTHZ,
    "tests/test_settings_secret_authz.py": AUTHZ,
    "tests/test_untrusted_local_config.py": AUTHZ,
    "tests/test_repo_endpoints.py::*requires_github_token*": AUTHZ,
    # A tier's tool list is what an HTTP MCP credential of that tier may call.
    "extensions/software-kb/tests/test_software_kb.py::*read_tier_no_write_tools*": AUTHZ,
    "extensions/software-kb/tests/test_software_kb.py::*read_tier_does_not_have*": AUTHZ,
    "extensions/journalism-investigation/tests/test_mcp_tools.py::*read_tier_tools_registered*": AUTHZ,
    # The social extension's author-only edit rule.
    "extensions/social/tests/test_social.py::*blocks_non_author*": AUTHZ,
    "extensions/social/tests/test_social.py::*abort_on_permission_error*": AUTHZ,
    # A refusal keeps its status when the error class is not listed.
    "tests/test_rest_api.py::*falls_back_to_the_base_status*": AUTHZ,
    # -- read scoping --
    "tests/test_blank_kb_name_is_no_kb.py": SCOPE,
    "tests/test_entry_type_routes_read_scoped.py": SCOPE,
    "tests/test_ephemeral_kb_privacy_persists.py": SCOPE,
    "tests/test_kb_export_read_scoping.py": SCOPE,
    "tests/test_link_read_scope_mcp.py": SCOPE,
    "tests/test_link_read_scope_plugins.py": SCOPE,
    "tests/test_link_read_scope_rest.py": SCOPE,
    "tests/test_link_read_scope_storage.py": SCOPE,
    "tests/test_link_reads_pass_a_scope.py": SCOPE,
    "tests/test_mcp_read_scoping.py": SCOPE,
    "tests/test_mcp_sse_kb_policy.py": SCOPE,
    "tests/test_mcp_tool_registry_is_scoped.py": SCOPE,
    "tests/test_plugin_kb_scoping.py": SCOPE,
    "tests/test_private_kb_read_scoping.py": SCOPE,
    "tests/test_read_scope_queries.py": SCOPE,
    "tests/test_read_scoping_is_structural.py": SCOPE,
    "tests/test_site_cache_follows_deletes.py": SCOPE,  # P-S3: only the public set
    "tests/test_site_public_only.py": SCOPE,
    "tests/test_sitemap.py::TestSitemapXml::*": SCOPE,  # public KBs only
    "tests/test_stats_read_scoping.py": SCOPE,
    "tests/test_websocket_kb_policy_lifetime.py": SCOPE,
    "tests/test_websocket_scoping.py": SCOPE,
    "tests/test_websocket_delivery.py::*private_entry_reaches_granted_socket_only*": SCOPE,
    "tests/test_websocket_delivery.py::*reaches_unscoped_sockets_only*": SCOPE,
    "tests/test_llm_usage_service.py::*scopes_to_requested_user_only*": SCOPE,
    "extensions/cascade/tests/test_cascade_readable_set.py": SCOPE,
    "extensions/journalism-investigation/tests/test_cross_kb_readable_set.py": SCOPE,
    "extensions/journalism-investigation/tests/test_single_kb_readable_set.py": SCOPE,
    "extensions/journalism-investigation/tests/test_dedup.py::*readable*": SCOPE,
    "extensions/software-kb/tests/test_sw_readable_set.py": SCOPE,
    "extensions/encyclopedia/tests/test_encyclopedia.py::TestWikiListsNarrowToTheReadableSet::*": SCOPE,
    "extensions/social/tests/test_social.py::TestReadableSetFiltering::*": SCOPE,
    "extensions/zettelkasten/tests/test_zettelkasten.py::TestInboxNarrowsToTheReadableSet::*": SCOPE,
    "tests/test_mcp_resources_session.py::TestResourcesAreScopedOverARealSession::*": SCOPE,
    # Restricted sources stay out of a public export; a demo upgrade never
    # makes a KB public that its kb.yaml or operator made private.
    "tests/test_notebooklm_renderer.py::*restricted_sources*": SCOPE,
    "tests/test_demo_public_kbs_upgrade.py": SCOPE,
    "tests/test_demo_seed_public_kbs.py::*default_role_wins*": SCOPE,
    # -- containment of paths and names inside their root --
    "tests/test_config_isolation.py": CONTAIN,
    "tests/test_entry_id_path_safety.py": CONTAIN,
    "tests/test_entry_version_integrity.py": CONTAIN,
    "tests/test_ephemeral_kb_name_is_not_a_path.py": CONTAIN,
    "tests/test_find_file_stays_inside_the_kb.py": CONTAIN,
    "tests/test_git_ref_arguments.py": CONTAIN,  # a caller's name is never a git option
    "tests/test_version_service.py": CONTAIN,  # commit ids validated, foreign commits refused
    "tests/test_export_service.py::TestPathTraversalPrevention::*": CONTAIN,
    "tests/test_export_service.py::*does_not_inject_fields*": CONTAIN,
    "tests/test_quartz_renderer.py::*traversal*": CONTAIN,
    "tests/test_notebooklm_renderer.py::*traversal*": CONTAIN,
    "tests/test_site_cache.py::*traversal*": CONTAIN,
    "tests/test_branding_endpoints.py::*traversal*": CONTAIN,
    "tests/test_branding_service.py::*traversal*": CONTAIN,
    # -- credential handling --
    "tests/test_auth_endpoints.py": CRED,
    "tests/test_auth_service.py": CRED,
    "tests/test_auth_service_oauth.py": CRED,
    "tests/test_github_token_storage.py": CRED,
    "tests/test_mcp_sse_session.py": CRED,
    "tests/test_oauth_providers.py": CRED,
    "tests/test_oauth_state_binding.py": CRED,
    "tests/test_save_config_keeps_environment_out.py": CRED,
    "tests/test_security_ip_privacy.py": CRED,
    "tests/test_security_token_encryption.py": CRED,
    "tests/test_session_cap_concurrency.py": CRED,
    "tests/test_user_service.py": CRED,
    "tests/test_websocket_credential_lifetime.py": CRED,
    "tests/test_branding_service.py::*omits_secrets*": CRED,
    # -- output escaping and request forgery --
    "tests/test_clipper.py": ESCAPE,  # SSRF defence for the web clipper
    "tests/test_repo_error_disclosure.py": ESCAPE,  # no git stderr or server paths
    "tests/test_site_xss.py": ESCAPE,
    "tests/test_spa_csp_headers.py": ESCAPE,
    "tests/test_site_cache.py::*escaped*": ESCAPE,
    "tests/test_site_cache.py::*javascript_url_blocked*": ESCAPE,
    "tests/test_site_cache.py::TestXSSPrevention::*": ESCAPE,
    "tests/test_site_cache.py::*does_not_leak_path*": ESCAPE,
    "tests/test_site_cache.py::TestBrokenBrandingDoesNotLeakOnPublicRoutes::*": ESCAPE,
    "tests/test_site_cache.py::TestBrandingFailureCacheDoesNotLeakTracebackFrames::*": ESCAPE,
    "tests/test_web_clipper.py::*strips_scripts*": ESCAPE,
    # An error body carries the public message, never the raw exception.
    "tests/test_rest_api.py::*public_message_replaces_str_exc*": ESCAPE,
    "extensions/social/tests/test_social.py::*public_message_not_the_raw_detail*": ESCAPE,
}


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(".*".join(re.escape(part) for part in pattern.split("*")) + r"\Z")


def matches(nodeid: str, pattern: str) -> bool:
    """Does ``pattern`` select the test ``nodeid``? See the module docstring."""
    subject = nodeid if "::" in pattern else nodeid.split("::", 1)[0]
    return _compile(pattern).match(subject) is not None


def is_security(nodeid: str) -> bool:
    return any(matches(nodeid, p) for p in NEVER_EXPERIMENTAL)


def is_experimental(nodeid: str) -> bool:
    """The one decision the root conftest applies to every collected test."""
    if is_security(nodeid):
        return False
    return any(matches(nodeid, p) for s in EXPERIMENTAL for p in s.paths)
