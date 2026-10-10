"""Tests for CLI --format json output across all command groups.

Verifies that commands with --format json return valid JSON with expected keys.
"""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click import unstyle
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli import context as cli_context_module
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository

runner = CliRunner()


@pytest.fixture
def cli_env():
    """Environment for CLI JSON output tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"
        events_path = tmpdir / "events"
        events_path.mkdir()

        events_kb = KBConfig(name="test-events", path=events_path, kb_type=KBType.EVENTS)
        config = PyriteConfig(
            knowledge_bases=[events_kb],
            settings=Settings(index_path=db_path),
        )

        # Create sample data
        events_repo = KBRepository(events_kb)
        event = EventEntry.create(
            date="2025-01-10", title="Test Event", body="Body text.", importance=5
        )
        event.tags = ["test"]
        events_repo.save(event)

        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()

        yield {"config": config, "tmpdir": tmpdir}


def _patch_config(module_path, cli_env):
    """Patch load_config in both the given module and cli_context.

    After the cli_context refactor, commands that go through cli_context
    call load_config from pyrite.cli.context, not from their own module.
    We patch both locations to keep tests working.
    """
    import contextlib

    @contextlib.contextmanager
    def _multi_patch():
        # Always patch the context module (used by all refactored commands)
        with patch("pyrite.cli.context.load_config", return_value=cli_env["config"]):
            # Also patch the original module path (may no longer exist for some
            # modules after refactoring, so guard with a try/except)
            try:
                with patch(module_path, return_value=cli_env["config"]):
                    yield
            except AttributeError:
                yield

    return _multi_patch()


@pytest.mark.cli
def test_kb_list_json(cli_env):
    """kb list --format json returns valid JSON with 'kbs' key."""
    with _patch_config("pyrite.cli.kb_commands.load_config", cli_env):
        result = runner.invoke(app, ["kb", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "kbs" in data
    assert isinstance(data["kbs"], list)
    assert len(data["kbs"]) > 0
    assert "name" in data["kbs"][0]


@pytest.mark.cli
def test_kb_discover_json(cli_env):
    """kb discover --format json returns valid JSON with 'discovered' key."""
    # Create a kb.yaml in the tmpdir so discover finds something
    kb_yaml = cli_env["tmpdir"] / "events" / "kb.yaml"
    kb_yaml.write_text("name: discovered-kb\ntype: events\n")

    with _patch_config("pyrite.cli.kb_commands.load_config", cli_env):
        result = runner.invoke(app, ["kb", "discover", str(cli_env["tmpdir"]), "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "discovered" in data
    assert isinstance(data["discovered"], list)


@pytest.mark.cli
def test_kb_validate_json(cli_env):
    """kb validate --format json returns valid JSON with 'kbs' and 'all_valid' keys."""
    with _patch_config("pyrite.cli.kb_commands.load_config", cli_env):
        result = runner.invoke(app, ["kb", "validate", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "kbs" in data
    assert "all_valid" in data
    assert data["drift_checked"] is True
    assert isinstance(data["kbs"], list)


@pytest.mark.cli
def test_index_stats_json(cli_env):
    """index stats --format json returns valid JSON with 'total_entries' key."""
    with _patch_config("pyrite.cli.index_commands.load_config", cli_env):
        result = runner.invoke(app, ["index", "stats", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "total_entries" in data


@pytest.mark.cli
def test_index_health_json(cli_env):
    """index health --format json returns valid JSON with 'status' key."""
    with _patch_config("pyrite.cli.index_commands.load_config", cli_env):
        result = runner.invoke(app, ["index", "health", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "status" in data
    assert data["status"] in ("healthy", "unhealthy")


@pytest.mark.cli
def test_index_health_json_reports_content_changed_as_unhealthy(cli_env):
    """A same-second content edit (mtime unchanged) surfaces via
    content_changed and flips status to unhealthy — the CLI-visible half of
    hash-based staleness detection (see test_storage.py's
    test_check_health_detects_same_second_content_edit for the IndexManager
    level)."""
    import os

    events_path = cli_env["tmpdir"] / "events"
    target = next(events_path.rglob("*.md"))
    original_mtime = target.stat().st_mtime

    content = target.read_text()
    target.write_text(content + "\nEdited without advancing mtime.\n")
    os.utime(target, (original_mtime, original_mtime))

    with _patch_config("pyrite.cli.index_commands.load_config", cli_env):
        result = runner.invoke(app, ["index", "health", "--format", "json"])
    # Exit 1 is the point of #18: an unhealthy index must not read as success
    # to a script or CI step gating on this command. stdout stays clean JSON.
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["status"] == "unhealthy", data
    assert data["checks"]["content_changed"], data["checks"]


@pytest.mark.cli
def test_repo_list_json(cli_env):
    """repo list --format json returns valid JSON with 'repos' key."""
    from unittest.mock import MagicMock

    mock_db = MagicMock()
    mock_repo_service = MagicMock()
    mock_user_service = MagicMock()
    mock_repo_service.list_repos.return_value = [
        {
            "id": 1,
            "name": "test-repo",
            "local_path": "/tmp/test",
            "remote_url": "https://github.com/test/test",
            "is_fork": False,
            "last_synced": None,
        }
    ]

    with patch(
        "pyrite.cli.repo_commands._get_db_and_services",
        return_value=(cli_env["config"], mock_db, mock_repo_service, mock_user_service),
    ):
        result = runner.invoke(app, ["repo", "list", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "repos" in data


@pytest.mark.cli
def test_qa_validate_json(cli_env):
    """qa validate --format json returns valid JSON with 'issues' key."""
    with _patch_config("pyrite.cli.qa_commands.load_config", cli_env):
        result = runner.invoke(app, ["qa", "validate", "test-events", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "issues" in data
    assert "count" in data


@pytest.mark.cli
def test_qa_status_json(cli_env):
    """qa status --format json returns valid JSON."""
    with _patch_config("pyrite.cli.qa_commands.load_config", cli_env):
        result = runner.invoke(app, ["qa", "status", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "total_entries" in data or "total_issues" in data


@pytest.mark.cli
@pytest.mark.parametrize(
    ("command", "count_key"),
    [("stale", "stale_count"), ("compact", "candidate_count")],
)
def test_qa_reports_use_real_context(cli_env, monkeypatch, command, count_key):
    """Sibling QA reports use the same context-manager contract."""
    monkeypatch.setattr(cli_context_module, "load_config", lambda: cli_env["config"])

    result = runner.invoke(app, ["qa", command, "test-events", "--format", "json"])

    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.output)[count_key], int)


@pytest.mark.cli
def test_auth_status_json(cli_env):
    """auth status --format json returns valid JSON with 'authenticated' key."""
    with (
        _patch_config("pyrite.cli.load_config", cli_env),
        patch(
            "pyrite.github_auth.check_github_auth",
            return_value=(True, "Authenticated via token"),
        ),
    ):
        result = runner.invoke(app, ["auth", "status", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "authenticated" in data
    assert data["authenticated"] is True


@pytest.mark.cli
def test_auth_whoami_json(cli_env):
    """auth whoami --format json returns valid JSON."""
    with (
        _patch_config("pyrite.cli.load_config", cli_env),
        patch("pyrite.services.user_service.UserService.get_current_user") as mock_user,
    ):
        mock_user.return_value = {
            "github_id": 12345,
            "github_login": "testuser",
            "display_name": "Test User",
            "email": "test@example.com",
        }
        result = runner.invoke(app, ["auth", "whoami", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "github_login" in data or "github_id" in data


# ---------------------------------------------------------------------------
# Machine-format stdout purity: -f json must emit ONLY parseable JSON to stdout
# even on the error / empty-result paths, with human chatter routed to stderr.
# Regression for: "pyrite get/search -f json intermittently emits non-JSON".
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_search_no_results_json_is_parseable(cli_env):
    """search -f json with no matches returns valid empty-result JSON on stdout,
    not a human 'No results found.' line that breaks json.load()."""
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        result = runner.invoke(app, ["search", "zzznomatchquery12345xyz", "--format", "json"])
    # stdout must parse cleanly even though there are no results.
    data = json.loads(result.output)
    assert data["count"] == 0
    assert data["results"] == []


@pytest.mark.cli
def test_search_normal_results_json_is_parseable(cli_env):
    """A normal search -f json still emits clean JSON on stdout."""
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        result = runner.invoke(app, ["search", "Test", "--format", "json"])
    data = json.loads(result.output)
    assert "results" in data and "count" in data


# ---------------------------------------------------------------------------
# --include-body must populate body field — Tier A r2000 regression lock
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_search_include_body_populates_body_field(cli_env):
    """`pyrite search --include-body -f json` must return the actual body
    text in each result's `body` field. Pre-fix (per r2000 ticket), the
    field was empty even when --include-body was set. The bug doesn't
    reproduce at HEAD; this test locks the contract so a future
    regression (e.g., a projection layer that drops body again) is
    caught immediately. cli_env seeds one event with body 'Body text.'.
    """
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        result = runner.invoke(app, ["search", "Test", "--include-body", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] >= 1
    # At least one result must have a non-empty body field.
    bodies = [r.get("body", "") for r in data["results"]]
    assert any(b for b in bodies), f"--include-body returned only empty bodies: {bodies}"
    # And specifically the seeded body is present.
    assert any("Body text." in b for b in bodies)


@pytest.mark.cli
def test_search_without_include_body_omits_body_field(cli_env):
    """Without --include-body, results must NOT include the body field —
    saves tokens for callers that only need metadata + snippet."""
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        result = runner.invoke(app, ["search", "Test", "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] >= 1
    for r in data["results"]:
        assert "body" not in r, f"Default search must omit body; result has it: {list(r)}"


@pytest.mark.cli
@pytest.mark.parametrize(
    ("command", "invalid_format"),
    [
        (["get", "any-entry"], "bogus"),
        (["search", "anything"], "jsonl"),
        (["kb", "list"], "bogus"),
        (["tags"], "bogus"),
        (["backlinks", "any-entry", "-k", "any-kb"], "bogus"),
    ],
    ids=["get", "search", "kb-list", "tags", "backlinks"],
)
def test_unknown_format_is_a_usage_error(command, invalid_format):
    """Unknown --format values fail during argument parsing, not serialization."""
    result = runner.invoke(app, [*command, "--format", invalid_format])
    output = unstyle(result.output)

    assert result.exit_code == 2, result.output
    assert "Traceback" not in output
    assert output.count("Invalid value for '--format'") == 1, result.output
    assert invalid_format in output
    assert "error_type" not in output


@pytest.mark.cli
def test_search_error_json_is_clean_structured_error(cli_env):
    """When the index search raises, -f json must emit a clean structured
    error object (error + error_type) and exit non-zero — never a mix of
    error line + fallback file-search results. Regression lock for
    `search-fallback-error-obscured`."""
    boom = RuntimeError("simulated index failure")
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        with patch(
            "pyrite.services.search_service.SearchService.search",
            side_effect=boom,
        ):
            result = runner.invoke(app, ["search", "Test", "--format", "json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["error_type"] == "RuntimeError"
    assert "simulated index failure" in data["error"]
    # Must NOT leak fallback file-search results into the JSON payload.
    assert data["results"] == []


@pytest.mark.cli
def test_search_query_syntax_error_uses_canonical_error_shape(cli_env):
    """A QuerySyntaxError (mixed literal+operator query that the FTS5
    sanitizer bypass lets through unquoted, e.g. `"phrase" cross-link`)
    must emit the canonical error contract (error_code=QUERY_SYNTAX,
    retryable=False via cli_error/build_error) — not the ad-hoc
    {error, error_type} shape genuinely-internal errors still use.
    search-query-syntax-error-contract."""
    from pyrite.exceptions import QuerySyntaxError

    boom = QuerySyntaxError("Query could not be parsed: no such column: link")
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        with patch(
            "pyrite.services.search_service.SearchService.search",
            side_effect=boom,
        ):
            result = runner.invoke(
                app, ["search", '"family separation" cross-link', "--format", "json"]
            )
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["error_code"] == "QUERY_SYNTAX", data
    assert data["retryable"] is False, data
    assert "error_type" not in data, (
        f"QUERY_SYNTAX errors must use the canonical shape, not the ad-hoc error_type field: {data}"
    )


@pytest.mark.cli
def test_search_error_logged_at_debug(cli_env, caplog):
    """The full exception must be logged (with traceback) when index search
    fails, so operators can troubleshoot — not just shown as a one-line
    message. Regression lock for `search-fallback-error-obscured`."""
    import logging

    boom = RuntimeError("simulated index failure")
    with _patch_config("pyrite.cli.search_commands.load_config", cli_env):
        with patch(
            "pyrite.services.search_service.SearchService.search",
            side_effect=boom,
        ):
            with caplog.at_level(logging.DEBUG, logger="pyrite.cli.search_commands"):
                runner.invoke(app, ["search", "Test"])  # rich mode → fallback path
    # A log record must carry the original exception with traceback info.
    assert any(
        rec.exc_info is not None
        and "simulated index failure" in rec.getMessage() + str(rec.exc_text or "")
        or (rec.exc_info and rec.exc_info[1] is boom)
        for rec in caplog.records
    ), (
        f"expected exception logged; got records: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


@pytest.mark.cli
@pytest.mark.core
def test_get_reads_current_file_body_and_body_round_trip_is_clean(cli_env):
    """A stale index must not hide a section present in the file from get."""
    kb = cli_env["config"].knowledge_bases[0]
    repo = KBRepository(kb)
    entry, entry_path = next(repo.list_entries())
    entry_id = entry.id
    model_body = (
        "  Body text.  \n\n### Conductor QC (2026-10-05)\n\nThe existing section remains intact.  "
    )
    expected_body = model_body.replace("\n", os.linesep) + os.linesep
    entry.body = model_body
    entry_path.write_text(entry.to_markdown(), encoding="utf-8")
    committed_content = entry_path.read_bytes()

    # The fixture's index still has the old body; commit the current file as the
    # baseline so the get/update round trip must leave it unchanged.
    db = PyriteDB(cli_env["config"].settings.index_path)
    try:
        indexed = db.get_entry(entry_id, kb.name)
    finally:
        db.close()
    assert indexed is not None
    assert indexed["body"] == "Body text."

    for args in (
        ["git", "init", "--quiet"],
        ["git", "config", "user.name", "Pyrite test"],
        ["git", "config", "user.email", "pyrite-test@example.invalid"],
        ["git", "add", "--all"],
        ["git", "commit", "--quiet", "-m", "baseline"],
    ):
        subprocess.run(args, cwd=kb.path, check=True, capture_output=True)

    with _patch_config("pyrite.cli.entry_commands.load_config", cli_env):
        result = runner.invoke(app, ["get", entry_id, "--kb", kb.name, "--format", "json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["body"] == expected_body
    assert data["content_hash"] == hashlib.sha256(committed_content).hexdigest()

    body_file = cli_env["tmpdir"] / "round-trip-body.md"
    body_file.write_text(data["body"], encoding="utf-8", newline="")
    with _patch_config("pyrite.cli.entry_commands.load_config", cli_env):
        updated = runner.invoke(
            app,
            [
                "update",
                entry_id,
                "--kb",
                kb.name,
                "--body-file",
                str(body_file),
                "--format",
                "json",
            ],
        )
    assert updated.exit_code == 0, updated.output
    assert entry_path.read_bytes() == committed_content
    diff = subprocess.run(
        ["git", "diff", "--exit-code"],
        cwd=kb.path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert diff.returncode == 0, diff.stdout + diff.stderr


@pytest.mark.cli
def test_get_not_found_json_is_parseable(cli_env):
    """get -f json for a missing entry emits a valid JSON error object on
    stdout (so programmatic callers can parse it), not a rich error line."""
    with _patch_config("pyrite.cli.entry_commands.load_config", cli_env):
        result = runner.invoke(
            app, ["get", "no-such-entry", "-k", "test-events", "--format", "json"]
        )
    # Non-zero exit, but stdout is still parseable JSON carrying the error.
    data = json.loads(result.output)
    assert data.get("error_code") == "NOT_FOUND" or "error" in data


# =========================================================================
# CLI write-side type enforcement — Tier A 1090 CLI half
# =========================================================================


@pytest.mark.cli
def test_create_refuses_undeclared_type(cli_env):
    """`pyrite create -t <undeclared>` must refuse when the KB's kb.yaml
    declares a schema that does not include the requested type. Regression for
    Tier A 1090 — the MCP half landed in commit 435be48; this is the CLI half."""
    # Write kb.yaml declaring backlog_item only — and invalidate the
    # schema cache because the KBConfig was instantiated before this write.
    (cli_env["tmpdir"] / "events" / "kb.yaml").write_text(
        "name: test-events\nkb_type: events\ntypes:\n  backlog_item:\n"
        "    description: A backlog work item\n"
    )
    cli_env["config"].knowledge_bases[0].invalidate_schema_cache()

    with _patch_config("pyrite.cli.entry_commands.load_config", cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "-k",
                "test-events",
                "-t",
                "task",  # not declared in kb.yaml; not a core type either
                "--title",
                "Conductor-filed task",
                "--body",
                "Should be refused",
            ],
        )

    # Refusal: non-zero exit, error message names declared types.
    assert result.exit_code != 0, (
        f"create with undeclared type 'task' must fail; got exit 0 with output:\n{result.output}"
    )
    out = result.output
    assert "task" in out, f"error message should name the offending type; got:\n{out}"
    assert "backlog_item" in out, (
        f"error message should name declared types so the caller can self-correct; got:\n{out}"
    )


@pytest.mark.cli
def test_create_allows_undeclared_with_override(cli_env):
    """Passing --allow-undeclared bypasses the refusal — useful for ephemeral
    KBs or migration scenarios. The entry will still be flagged by
    `pyrite index health`'s undeclared_types check."""
    (cli_env["tmpdir"] / "events" / "kb.yaml").write_text(
        "name: test-events\nkb_type: events\ntypes:\n  backlog_item:\n"
        "    description: A backlog work item\n"
    )

    with _patch_config("pyrite.cli.entry_commands.load_config", cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "-k",
                "test-events",
                "-t",
                "task",
                "--title",
                "Override case",
                "--body",
                "Allowed by override",
                "--allow-undeclared",
            ],
        )

    assert result.exit_code == 0, (
        f"--allow-undeclared must let undeclared types through; got exit "
        f"{result.exit_code} with output:\n{result.output}"
    )
    assert "Created" in result.output


@pytest.mark.cli
def test_create_allows_core_types_even_without_declaration(cli_env):
    """Core types (note, event, person, ...) stay accepted regardless of the
    KB's declared schema — they're the universal fallback. This pins the
    contract that the refusal only fires for genuinely-unknown types."""
    (cli_env["tmpdir"] / "events" / "kb.yaml").write_text(
        "name: test-events\nkb_type: events\ntypes:\n  backlog_item:\n"
        "    description: A backlog work item\n"
    )

    with _patch_config("pyrite.cli.entry_commands.load_config", cli_env):
        result = runner.invoke(
            app,
            [
                "create",
                "-k",
                "test-events",
                "-t",
                "note",  # core type, not in this KB's kb.yaml — must succeed
                "--title",
                "Core type is fine",
                "--body",
                "Notes are universal",
            ],
        )

    assert result.exit_code == 0, (
        f"core type 'note' must succeed even when not in kb.yaml; got exit "
        f"{result.exit_code} with output:\n{result.output}"
    )


@pytest.mark.cli
@pytest.mark.parametrize("subcommand", ["stats", "health"])
def test_index_stats_and_health_close_database(cli_env, subcommand):
    """Both read-only index commands close the DB they open."""
    closed_dbs = []
    original_close = PyriteDB.close

    def tracked_close(db):
        closed_dbs.append(db)
        original_close(db)

    with patch.object(PyriteDB, "close", tracked_close):
        with _patch_config("pyrite.cli.index_commands.load_config", cli_env):
            result = runner.invoke(app, ["index", subcommand, "--format", "json"])

    assert result.exit_code == 0, result.output
    assert len(closed_dbs) == 1
