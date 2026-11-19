"""Tests for the shared CLI error helper (cli-error-shape-consistency)."""

import json

import pytest
import typer

from pyrite.utils.errors import build_error, cli_error, cli_error_from


def test_build_error_shape():
    """build_error returns the canonical agent-facing error dict, matching the
    MCP _error() shape."""
    payload = build_error(
        "Entry 'foo' not found",
        "NOT_FOUND",
        suggestion="try `pyrite search foo`",
    )
    assert payload == {
        "error": "Entry 'foo' not found",
        "error_code": "NOT_FOUND",
        "suggestion": "try `pyrite search foo`",
        "retryable": False,
    }


def test_build_error_minimal():
    """Only message + code are required; optional fields omitted when unset."""
    payload = build_error("boom", "VALIDATION_FAILED")
    assert payload["error"] == "boom"
    assert payload["error_code"] == "VALIDATION_FAILED"
    assert "suggestion" not in payload
    assert payload["retryable"] is False


def test_build_error_common_classes():
    """The canonical shape carries each of the common error classes verbatim,
    incl. PERMISSION_DENIED and a retryable failure."""
    for code in ("NOT_FOUND", "KB_NOT_FOUND", "VALIDATION_FAILED", "PERMISSION_DENIED"):
        assert build_error("m", code)["error_code"] == code
    retryable = build_error("transient", "SYNC_FAILED", retryable=True)
    assert retryable["retryable"] is True


def test_cli_error_json_format(capsys):
    """In a machine format, cli_error emits the structured payload as JSON and
    exits non-zero."""
    with pytest.raises(typer.Exit) as exc:
        cli_error(
            "KB not found: x", "json", error_code="KB_NOT_FOUND", suggestion="run `pyrite kb list`"
        )
    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["error_code"] == "KB_NOT_FOUND"
    assert parsed["error"] == "KB not found: x"
    assert parsed["suggestion"] == "run `pyrite kb list`"


def test_cli_error_rich_format(capsys):
    """In rich format, cli_error prints a colored single line with the code."""
    with pytest.raises(typer.Exit):
        cli_error("KB not found: x", "rich", error_code="KB_NOT_FOUND")
    out = capsys.readouterr().out
    assert "KB_NOT_FOUND" in out
    assert "KB not found: x" in out


class TestCliErrorFrom:
    """ADR-0037 theme 2: cli_error_from(exc) replaces the hand-kept
    isinstance chain (the old ``pyrite/cli/__init__.py::_cli_err``, which
    hardcoded NOT_FOUND / KB_NOT_FOUND / VALIDATION_FAILED for three
    exception types and "ERROR" for everything else) with one call that
    reads the class's own ``error_code`` -- the same code REST's central
    handler and MCP's ``_refusal`` now read from the same attribute.
    """

    def test_reads_the_class_code(self, capsys):
        from pyrite.exceptions import KBNotFoundError

        with pytest.raises(typer.Exit) as exc:
            cli_error_from(KBNotFoundError("KB not found: x"), output_format="json")
        assert exc.value.exit_code == 1
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["error_code"] == "KB_NOT_FOUND"
        assert parsed["error"] == "KB not found: x"

    def test_entry_not_found_code(self, capsys):
        from pyrite.exceptions import EntryNotFoundError

        with pytest.raises(typer.Exit):
            cli_error_from(EntryNotFoundError("no entry here"), output_format="json")
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["error_code"] == "ENTRY_NOT_FOUND"

    def test_base_validation_error_reads_the_class_code(self, capsys):
        """The old _cli_err hardcoded VALIDATION_FAILED for any ValidationError
        via an isinstance chain; cli_error_from reads the class's own code
        instead -- which happens to be the same string here (conductor
        decision, ADR-0037 theme 2 fix round 1: the base ValidationError code
        is VALIDATION_FAILED, the write pipeline's documented spelling), so
        this is not a change in what the CLI reports, only in how it is
        derived. No legacy_error_code -- that concept is MCP-only per the
        maintainer's decision (2026-09-25)."""
        from pyrite.exceptions import ValidationError

        with pytest.raises(typer.Exit):
            cli_error_from(ValidationError("bad field"), output_format="json")
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["error_code"] == "VALIDATION_FAILED"
        assert "legacy_error_code" not in parsed

    def test_a_class_the_old_isinstance_chain_did_not_know_about(self, capsys):
        """The old chain fell to a bare "ERROR" for anything that wasn't
        EntryNotFoundError/KBNotFoundError/ValidationError -- e.g. StorageError.
        cli_error_from has no such gap: every PyriteError has a class code."""
        from pyrite.exceptions import StorageError

        with pytest.raises(typer.Exit):
            cli_error_from(StorageError("disk gone"), output_format="json")
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["error_code"] == "STORAGE_ERROR"

    def test_public_message_replaces_str_exc_when_set(self, capsys):
        from pyrite.exceptions import ConfigSaveRefusedError

        exc = ConfigSaveRefusedError("/real/secret/path.yaml leaked here", dropped=["x"])
        with pytest.raises(typer.Exit):
            cli_error_from(exc, output_format="json")
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["error_code"] == "CONFIG_SAVE_REFUSED"
        assert "/real/secret/path.yaml" not in parsed["error"]

    def test_suggestion_becomes_the_hint(self, capsys):
        from pyrite.exceptions import ValidationError

        exc = ValidationError("bad field")
        exc.suggestion = "try again"
        with pytest.raises(typer.Exit):
            cli_error_from(exc, output_format="json")
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["suggestion"] == "try again"

    def test_defaults_to_rich_format(self, capsys):
        from pyrite.exceptions import KBNotFoundError

        with pytest.raises(typer.Exit):
            cli_error_from(KBNotFoundError("KB not found: x"))
        out = capsys.readouterr().out
        assert "KB_NOT_FOUND" in out
        assert "KB not found: x" in out


def test_search_unregistered_kb_returns_kb_not_found():
    """`pyrite search -k <unknown>` must fail with KB_NOT_FOUND, not an empty
    result set that looks like a query miss (cli-error-shape-consistency #4)."""
    from typer.testing import CliRunner

    from pyrite.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app, ["search", "anything", "-k", "definitely-not-a-real-kb", "--format", "json"]
    )
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error_code"] == "KB_NOT_FOUND"
    assert "definitely-not-a-real-kb" in parsed["error"]


def test_search_kb_not_found_suggestion_includes_db_only_kb(tmp_path, monkeypatch):
    """The near-match suggestion on KB_NOT_FOUND must draw on KBs registered
    only via `pyrite kb add` (DB-only, not in config.yaml's knowledge_bases)
    -- collapse-kb-registry-to-one-source-of-truth's all_kbs() sweep.
    Previously iterated knowledge_bases directly, so a DB-only KB was
    invisible in the typo-suggestion even though it's a real, searchable KB.
    The one resolver (#952) offers an unscoped caller its near matches, so
    the probe is one letter off the DB-only KB's name."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from pyrite.cli import app
    from pyrite.config import KBConfig, PyriteConfig, Settings

    kb_path = tmp_path / "db-only-kb"
    kb_path.mkdir()
    db_only_kb = KBConfig(name="db-only-kb", path=kb_path, kb_type="generic")
    config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmp_path / "index.db"))
    config._db_kb_cache["db-only-kb"] = db_only_kb

    runner = CliRunner()
    with patch("pyrite.cli.search_commands.load_config", return_value=config):
        result = runner.invoke(app, ["search", "anything", "-k", "db-only-kbb", "--format", "json"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["error_code"] == "KB_NOT_FOUND"
    assert parsed["did_you_mean"] == ["db-only-kb"]
    assert "db-only-kb" in parsed["suggestion"], (
        f"expected DB-only KB in the near-match suggestion; got {parsed['suggestion']!r}"
    )


class TestHintsAreNotRichMarkup:
    """A hint is literal text, not Rich markup.

    `cli_error` rendered the suggestion through `console.print`, which parses
    `[...]` as a style tag. The install hint for semantic search read:

        hint: install with: pip install pyrite

    -- the `[semantic]` silently eaten, leaving a command that installs the
    package the user already has and does not fix their error. Found by the
    0.24.3 release dry run, whose tutorial step hit it on block 9.

    The message is user-controlled too (entry ids, KB names, git stderr), so
    both halves of the line are rendered literally.
    """

    def _render(self, capsys, message, suggestion=None):
        with pytest.raises(typer.Exit):
            cli_error(message, error_code="DEPENDENCY_MISSING", suggestion=suggestion)
        return capsys.readouterr().out

    def test_bracketed_extra_survives_in_the_hint(self, capsys):
        out = self._render(
            capsys,
            "sentence-transformers is not installed.",
            suggestion="install with: pip install pyrite[semantic]",
        )
        assert "pip install pyrite[semantic]" in out

    def test_bracketed_json_example_survives_in_the_hint(self, capsys):
        out = self._render(capsys, "bad entries", suggestion='pass entries as [{"id": "x"}]')
        assert '[{"id": "x"}]' in out

    def test_brackets_in_the_message_survive(self, capsys):
        out = self._render(capsys, "no entry matched [draft] in kb")
        assert "[draft]" in out

    def test_an_unclosed_bracket_does_not_crash_the_error_path(self, capsys):
        """Rich raises MarkupError on a malformed tag. An error reporter that
        raises while reporting an error is the worst possible failure."""
        out = self._render(capsys, "unterminated [tag", suggestion="also [unclosed")
        assert "[tag" in out
        assert "[unclosed" in out
