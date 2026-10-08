"""A command's default output is its result (#584, #43).

Diagnostics are asked for (`-v`, `-vv`, ``PYRITE_LOG_LEVEL``); a degraded answer
says so. Every logging test here runs a real entry point's ``main()`` in a
subprocess: ``CliRunner`` skips ``main()``, which is where logging is
configured, so it cannot see this class of bug.

Model: ``test_logging_configuration.py`` (subprocess, real entry point) and
``test_semantic_search_warns_when_nothing_is_embedded.py`` (CLI, REST, MCP).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

CLI = "pyrite.cli"
ADMIN = "pyrite.admin_cli"
READ = "pyrite.read_cli"
SERVER = "pyrite.server.api"


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYRITE_")}
    env.update(
        HOME=str(tmp_path / "home"),
        PYRITE_CONFIG_DIR=str(tmp_path / "config"),
        PYRITE_DATA_DIR=str(tmp_path / "data"),
    )
    env.update(extra)
    return env


def _run(tmp_path, module, *args, pre="", env=None, stdin=subprocess.DEVNULL):
    """Run ``module.main()`` as the installed script would, ``args`` as argv."""
    code = f"import sys; {pre}\nfrom {module} import main; main()"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        env=_env(tmp_path, **(env or {})),
        cwd=tmp_path,
        stdin=stdin,
        timeout=120,
    )


#: Make the app log one INFO and one WARNING record, then print a result: what
#: a command does, minus everything the test is not about.
_PROBE = (
    "import logging; from unittest.mock import patch; "
    "patch('{app}', side_effect=lambda: (logging.getLogger('pyrite.probe').info('INFO-LINE'), "
    "logging.getLogger('pyrite.probe').debug('DEBUG-LINE'), "
    "logging.getLogger('pyrite.probe').warning('WARN-LINE'), "
    "print('RESULT', sys.argv[1:]))).start()"
)


def _probe(tmp_path, module, app, *args, env=None):
    return _run(tmp_path, module, *args, pre=_PROBE.format(app=app), env=env)


# ---------------------------------------------------------------------------
# The groom's failing test: the real `pyrite init` on a fresh data dir
# ---------------------------------------------------------------------------


def test_init_on_a_fresh_data_dir_writes_nothing_to_stderr(tmp_path):
    """Regime: fresh index (every migration runs)."""
    proc = _run(tmp_path, CLI, "init", "--template", "research", "--path", str(tmp_path / "kb"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    assert proc.stdout, "the result still reaches stdout"


def test_an_existing_index_is_quiet_and_search_writes_no_trace_line(tmp_path):
    """Regime: existing index. `search.query` was one stderr line per search."""
    kb = tmp_path / "kb"
    init = _run(tmp_path, CLI, "init", "--template", "research", "--path", str(kb))
    assert init.returncode == 0, init.stderr
    created = _run(tmp_path, CLI, "create", "-k", "kb", "-t", "note", "--title", "Kestrel")
    assert created.returncode == 0, created.stdout
    first = _run(tmp_path, CLI, "search", "kestrel", "-k", "kb", "--format", "json")
    second = _run(tmp_path, CLI, "search", "kestrel", "-k", "kb", "--format", "json")
    for proc in (created, first, second):
        assert "Applied migration" not in proc.stderr, proc.stderr
        assert "search.query" not in proc.stderr, proc.stderr
    assert second.stderr == ""
    assert second.returncode == 0, second.stdout
    assert second.returncode == 0, second.stdout
    json.loads(second.stdout)  # still exactly one document


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
def test_a_terminal_sees_the_same_quiet_as_a_pipe(tmp_path):
    """Regime: TTY. Logging must not depend on what stderr is attached to."""
    import pty

    master, slave = pty.openpty()
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pyrite.cli import main; main()",
                "init",
                "--template",
                "research",
                "--path",
                str(tmp_path / "kb"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=slave,
            env=_env(tmp_path),
            cwd=tmp_path,
            timeout=120,
        )
        os.close(slave)
        slave = None
        os.set_blocking(master, False)
        seen = b""
        while True:
            try:
                chunk = os.read(master, 65536)
            except BlockingIOError:
                break  # nothing more buffered (macOS)
            except OSError:
                break  # EIO: the slave is closed and the buffer is drained (Linux)
            if not chunk:
                break
            seen += chunk
    finally:
        os.close(master)
        if slave is not None:
            os.close(slave)
    assert proc.returncode == 0
    assert seen == b""


# ---------------------------------------------------------------------------
# One level policy, three entry points
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "app"),
    [(CLI, "pyrite.cli.app"), (ADMIN, "pyrite.admin_cli.app"), (READ, "pyrite.read_cli.app")],
)
class TestEveryEntryPointFollowsOnePolicy:
    def test_default_shows_a_formatted_warning_and_nothing_below_it(self, tmp_path, module, app):
        proc = _probe(tmp_path, module, app, "x")
        assert "[WARNING] pyrite.probe: WARN-LINE" in proc.stderr, proc.stderr
        assert "INFO-LINE" not in proc.stderr
        assert "DEBUG-LINE" not in proc.stderr

    def test_v_shows_info_and_vv_shows_debug(self, tmp_path, module, app):
        v = _probe(tmp_path, module, app, "x", "-v")
        vv = _probe(tmp_path, module, app, "-vv", "x")
        assert "INFO-LINE" in v.stderr and "DEBUG-LINE" not in v.stderr
        assert "DEBUG-LINE" in vv.stderr

    def test_the_flag_never_reaches_the_command_parser_and_works_after_the_command(
        self, tmp_path, module, app
    ):
        proc = _probe(tmp_path, module, app, "search", "q", "--limit", "3", "-v")
        assert "RESULT ['search', 'q', '--limit', '3']" in proc.stdout
        assert "INFO-LINE" in proc.stderr

    def test_env_var_sets_the_level_and_the_flag_beats_it(self, tmp_path, module, app):
        env = {"PYRITE_LOG_LEVEL": "debug"}
        assert "DEBUG-LINE" in _probe(tmp_path, module, app, "x", env=env).stderr
        flagged = _probe(tmp_path, module, app, "x", "-v", env=env)
        assert "INFO-LINE" in flagged.stderr and "DEBUG-LINE" not in flagged.stderr

    def test_a_bad_env_value_is_named_and_the_default_holds(self, tmp_path, module, app):
        proc = _probe(tmp_path, module, app, "x", env={"PYRITE_LOG_LEVEL": "loud"})
        assert "PYRITE_LOG_LEVEL" in proc.stderr and "loud" in proc.stderr
        assert "INFO-LINE" not in proc.stderr

    def test_logs_never_reach_stdout(self, tmp_path, module, app):
        proc = _probe(tmp_path, module, app, "x", "-vv")
        assert "LINE" not in proc.stdout
        assert proc.stdout.strip() == "RESULT ['x']"


def test_double_dash_ends_the_scan_so_a_dash_v_argument_is_data(tmp_path):
    proc = _probe(tmp_path, CLI, "pyrite.cli.app", "search", "--", "-v")
    assert "RESULT ['search', '--', '-v']" in proc.stdout
    assert "INFO-LINE" not in proc.stderr


class TestServersAndStdioMcp:
    """The maintainer's decision: HTTP servers keep INFO; stdio mcp goes quiet."""

    @pytest.mark.control(
        reason="dev logged INFO everywhere; pins that the HTTP server still does after the default moved to WARNING"
    )
    def test_pyrite_serve_keeps_info(self, tmp_path):
        proc = _probe(tmp_path, CLI, "pyrite.cli.app", "serve")
        assert "INFO-LINE" in proc.stderr and "DEBUG-LINE" not in proc.stderr

    def test_pyrite_mcp_is_quiet_and_can_be_raised(self, tmp_path):
        quiet = _probe(tmp_path, CLI, "pyrite.cli.app", "mcp")
        assert "INFO-LINE" not in quiet.stderr and "WARN-LINE" in quiet.stderr
        raised = _probe(tmp_path, CLI, "pyrite.cli.app", "mcp", env={"PYRITE_LOG_LEVEL": "INFO"})
        assert "INFO-LINE" in raised.stderr

    def test_pyrite_server_entry_point_logs_info_and_its_own_warning_is_no_longer_dropped(
        self, tmp_path
    ):
        """`pyrite-server` configured nothing, so the package NullHandler ate
        every record, including its open-registration warning."""
        pre = (
            "import logging; from unittest.mock import patch; import uvicorn; "
            "patch('uvicorn.run', side_effect=lambda *a, **k: "
            "(logging.getLogger('pyrite.probe').info('INFO-LINE'), "
            "logging.getLogger('pyrite.probe').debug('DEBUG-LINE'))).start()"
        )
        proc = _run(tmp_path, SERVER, pre=pre)
        assert "[INFO] pyrite.probe: INFO-LINE" in proc.stderr, proc.stderr
        assert "DEBUG-LINE" not in proc.stderr
        assert proc.stdout == ""

    @pytest.mark.control(
        reason="stdout was already clean on dev (the INFO lines went to stderr); pins stdout stays the protocol's, and the stderr half is covered by the init/search tests"
    )
    def test_stdio_mcp_writes_nothing_to_stdout_and_no_info_to_stderr(self, tmp_path):
        """Real server, stdin at EOF: the protocol stream stays empty and the
        client's server log gets no INFO chatter."""
        init = _run(tmp_path, CLI, "init", "--template", "research", "--path", str(tmp_path / "kb"))
        assert init.returncode == 0, init.stderr
        proc = _run(tmp_path, CLI, "mcp", "--tier", "read")
        assert proc.stdout == "", proc.stdout
        assert "[INFO]" not in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# A degraded semantic answer says so (#43): the extra is missing
# ---------------------------------------------------------------------------


@pytest.fixture
def indexed(tmp_path):
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="t", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    KBService(config, db).create_entry("t", "kestrel", "Kestrel Notes", "note", "a falcon hovers")
    yield config, db
    db.close()


@pytest.fixture
def no_extra(monkeypatch):
    """The `[semantic]` extra is not installed."""
    monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: False)


def _search(db, mode, warnings):
    from pyrite.services.search_service import SearchService

    return SearchService(db).search("falcon", mode=mode, warnings=warnings)


class TestSemanticWithoutTheExtraSaysSo:
    def test_semantic_names_the_install_line_and_does_not_claim_a_keyword_leg(
        self, indexed, no_extra
    ):
        warnings: list[str] = []
        assert _search(indexed[1], "semantic", warnings) == []
        assert len(warnings) == 1
        assert "pip install pyrite[semantic]" in warnings[0]
        assert "keyword leg ran" not in warnings[0], "false in pure semantic mode"

    def test_hybrid_names_the_install_line_beside_real_keyword_hits(self, indexed, no_extra):
        warnings: list[str] = []
        results = _search(indexed[1], "hybrid", warnings)
        assert [r["id"] for r in results] == ["kestrel"]
        assert len(warnings) == 1
        assert "pip install pyrite[semantic]" in warnings[0]
        assert "only the keyword leg ran" in warnings[0]

    def test_an_extension_that_will_not_load_does_not_get_the_install_line(
        self, indexed, monkeypatch
    ):
        """sqlite-vec is installed but the interpreter cannot load extensions:
        `pip install pyrite[semantic]` would not help, so it is not the advice."""
        import importlib.util

        db = indexed[1]
        if importlib.util.find_spec("sqlite_vec") is None:
            pytest.skip("sqlite-vec package not installed")
        monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: True)
        monkeypatch.setattr(db, "vec_available", False, raising=False)
        warnings: list[str] = []
        _search(db, "semantic", warnings)
        assert len(warnings) == 1
        assert "did not load" in warnings[0]
        assert "pip install" not in warnings[0]

    @pytest.mark.control(
        reason="keyword mode never warned on dev; pins that the new semantic warning does not leak into it"
    )
    def test_keyword_mode_stays_silent(self, indexed, no_extra):
        warnings: list[str] = []
        _search(indexed[1], "keyword", warnings)
        assert warnings == []

    @pytest.mark.control(
        reason="dev already returned before encoding when the extra is missing; pins that the new warning keeps that order"
    )
    def test_no_model_is_loaded_to_find_out(self, indexed, no_extra, monkeypatch):
        """The leg is skipped before the query is encoded (the groom's question)."""
        from pyrite.services.embedding_service import EmbeddingService

        boom = MagicMock(side_effect=AssertionError("a model was loaded"))
        monkeypatch.setattr(EmbeddingService, "embed_text", boom)
        monkeypatch.setattr(EmbeddingService, "_get_model", boom, raising=False)
        _search(indexed[1], "semantic", [])
        boom.assert_not_called()

    def test_extra_present_but_zero_vectors_still_names_embed(self, indexed):
        warnings: list[str] = []
        _search(indexed[1], "semantic", warnings)
        if not indexed[1].vec_available:
            pytest.skip("sqlite-vec unavailable")
        assert any("pyrite index embed" in w for w in warnings), warnings
        assert not any("keyword leg ran" in w for w in warnings)

    @pytest.mark.control(
        reason="dev did not warn here; pins that the new warning is not raised for a plain empty result"
    )
    def test_vectors_present_and_no_hit_is_not_a_degraded_answer(self, indexed, monkeypatch):
        """No warning when the leg ran and simply found nothing."""
        config, db = indexed
        if not db.vec_available:
            pytest.skip("sqlite-vec unavailable")
        db.backend.upsert_embedding("kestrel", "t", [0.01] * 384)
        monkeypatch.setattr(
            "pyrite.services.embedding_service.EmbeddingService.embed_text",
            lambda self, text: [0.9] * 384,
        )
        warnings: list[str] = []
        _search(db, "semantic", warnings)
        assert warnings == []

    @pytest.mark.api
    def test_the_warning_reaches_rest(self, indexed, no_extra):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        config, db = indexed
        app = create_app(config=config)
        app.dependency_overrides[get_config] = lambda: config
        app.dependency_overrides[get_db] = lambda: db
        body = (
            TestClient(app)
            .get("/api/search", params={"q": "falcon", "kb": "t", "mode": "semantic"})
            .json()
        )
        assert any("pip install pyrite[semantic]" in w for w in body["warnings"]), body

    def test_the_warning_reaches_mcp(self, indexed, no_extra):
        from pyrite.server.mcp_server import PyriteMCPServer

        config, _ = indexed
        server = PyriteMCPServer(config, tier="read")
        try:
            result = server._dispatch_tool(
                "kb_search", {"query": "falcon", "kb_name": "t", "mode": "hybrid"}
            )
        finally:
            server.close()
        assert any("pip install pyrite[semantic]" in w for w in result["warnings"]), result

    def test_the_warning_reaches_the_cli_on_stderr_and_stdout_stays_one_document(self, tmp_path):
        kb = tmp_path / "kb"
        assert (
            _run(tmp_path, CLI, "init", "--template", "research", "--path", str(kb)).returncode == 0
        )
        made = _run(tmp_path, CLI, "create", "-k", "kb", "-t", "note", "--title", "Kestrel")
        assert made.returncode == 0, made.stdout
        pre = "sys.modules['sentence_transformers'] = None"  # import raises ImportError
        proc = _run(
            tmp_path,
            CLI,
            "search",
            "kestrel",
            "-k",
            "kb",
            "--mode",
            "hybrid",
            "--format",
            "json",
            pre=pre,
        )
        assert proc.returncode == 0, proc.stderr
        assert "pip install pyrite[semantic]" in proc.stderr
        json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# `index embed` reports what it did
# ---------------------------------------------------------------------------


def _embed_env(tmp_path, monkeypatch, n, other_kb_entries=0):
    """A config + index with ``n`` entries whose embeds are queued (ADR-0035)."""
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    kbs = [KBConfig(name="t", path=kb_path, kb_type=KBType.GENERIC)]
    if other_kb_entries:
        (tmp_path / "kb2").mkdir()
        kbs.append(KBConfig(name="u", path=tmp_path / "kb2", kb_type=KBType.GENERIC))
    config = PyriteConfig(
        knowledge_bases=kbs,
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=True),
    )
    db = PyriteDB(config.settings.index_path)
    if not db.vec_available:
        db.close()
        pytest.skip("sqlite-vec unavailable")
    svc = KBService(config, db)
    for i in range(n):
        svc.create_entry("t", f"e{i}", f"Entry {i}", "note", f"body {i}")
    for i in range(other_kb_entries):
        svc.create_entry("u", f"x{i}", f"Other {i}", "note", f"other body {i}")

    from pyrite.services.embedding_service import EmbeddingService
    from pyrite.services.embedding_worker import EmbeddingWorker

    def fake_embed_entry(entry_id, kb_name):
        db.backend.upsert_embedding(entry_id, kb_name, [0.01] * 384)
        return True

    monkeypatch.setattr(
        EmbeddingWorker,
        "_get_embedding_svc",
        lambda self: MagicMock(**{"embed_entry.side_effect": fake_embed_entry}),
    )
    monkeypatch.setattr(EmbeddingService, "embed_text", lambda self, text: [0.01] * 384)
    monkeypatch.setattr("pyrite.cli.index_commands.get_config_and_db", lambda: (config, db))
    return config, db


def _vector_count(db):
    return len(db.backend.get_embedded_rowids())


class TestIndexEmbedReportsTheVectorsItAdded:
    def test_the_drained_entries_are_counted_as_embedded_not_skipped(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from pyrite.cli import app

        _, db = _embed_env(tmp_path, monkeypatch, 4)
        assert _vector_count(db) == 0
        result = CliRunner().invoke(app, ["index", "embed"])
        assert result.exit_code == 0, result.output
        assert _vector_count(db) == 4
        assert "Embedded: 4" in result.output, result.output
        assert "Skipped: 0" in result.output, result.output

    def test_counts_split_between_the_queue_and_the_sweep(self, tmp_path, monkeypatch):
        """An entry with no queue row is swept by embed_all, not the drain."""
        from typer.testing import CliRunner

        from pyrite.cli import app

        _, db = _embed_env(tmp_path, monkeypatch, 3)
        db.backend.upsert_embedding("e0", "t", [0.5] * 384)  # already current
        db._raw_conn.execute("DELETE FROM embed_queue WHERE entry_id = 'e0'")
        result = CliRunner().invoke(app, ["index", "embed"])
        assert "Embedded: 2" in result.output and "Skipped: 1" in result.output, result.output
        assert _vector_count(db) == 3

    def test_a_re_embedded_edit_counts_though_it_adds_no_vector(self, tmp_path, monkeypatch):
        """An edit re-embeds an entry that already has a vector (same rowid), so
        a before/after diff of the vector table sees nothing; the worker's own
        count does."""
        from typer.testing import CliRunner

        from pyrite.cli import app

        _, db = _embed_env(tmp_path, monkeypatch, 3)
        db.backend.upsert_embedding("e0", "t", [0.5] * 384)  # stale vector, row still queued
        result = CliRunner().invoke(app, ["index", "embed"])
        assert "Embedded: 3" in result.output and "Skipped: 0" in result.output, result.output
        assert _vector_count(db) == 3

    @pytest.mark.control(
        reason="on dev force re-embeds everything and counted correctly; pins that crediting the queue drain leaves --force alone"
    )
    def test_force_does_not_double_count(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from pyrite.cli import app

        _embed_env(tmp_path, monkeypatch, 2)
        result = CliRunner().invoke(app, ["index", "embed", "--force"])
        assert "Embedded: 2" in result.output, result.output

    def test_model_unreachable_names_the_errors_and_exits_one(self, tmp_path, monkeypatch):
        """Regime: model unreachable. The report names the errors, and the exit
        is 1 (#526): none was embedded, and they stay owed (3 when some were). docs/getting-started.md's
        embed step accepts 0 or 1, which is what keeps the offline tutorial and the
        release check (scripts/release.py step c) green."""
        from typer.testing import CliRunner

        from pyrite.cli import app
        from pyrite.services.embedding_service import EmbeddingService

        _, db = _embed_env(tmp_path, monkeypatch, 2)

        def offline(self, text):
            raise OSError("model unreachable")

        monkeypatch.setattr(EmbeddingService, "embed_text", offline)
        monkeypatch.setattr(
            "pyrite.services.embedding_worker.EmbeddingWorker._get_embedding_svc",
            lambda self: MagicMock(**{"embed_entry.side_effect": offline}),
        )
        result = CliRunner().invoke(app, ["index", "embed"])
        assert result.exit_code == 1, result.output
        assert "Embedded: 0" in result.output
        assert "Errors: 2" in result.output, result.output
        assert _vector_count(db) == 0


# ---------------------------------------------------------------------------
# Round 1 review of PR #590
# ---------------------------------------------------------------------------


class TestAnOptionValueThatLooksLikeTheFlagIsData:
    """Review item 1. On dev, Click reads `-b -v` as body="-v". Stripping every
    bare `-v` token made `-b` swallow the *next* option as its value, and a
    valid command line saved a different value without a word."""

    def test_a_value_taking_option_keeps_dash_v_as_its_value(self, tmp_path):
        kb = tmp_path / "kb"
        assert (
            _run(tmp_path, CLI, "init", "--template", "research", "--path", str(kb)).returncode == 0
        )
        made = _run(tmp_path, CLI, "create", "-k", "kb", "-t", "note", "-b", "-v", "--title", "T")
        assert made.returncode == 0, made.stdout + made.stderr
        shown = _run(tmp_path, CLI, "get", "t", "-k", "kb", "--format", "json")
        assert shown.returncode == 0, shown.stdout + shown.stderr
        assert json.loads(shown.stdout)["body"].strip() == "-v"

    def test_it_is_not_counted_as_verbosity(self):
        from typer.testing import CliRunner  # noqa: F401  (the real app is introspected)

        from pyrite.cli import app
        from pyrite.logging import split_verbosity

        argv = ["create", "-k", "kb", "-b", "-v", "--title", "T"]
        assert split_verbosity(argv, app=app) == (0, argv)

    def test_a_boolean_flag_before_dash_v_is_still_a_flag(self):
        from pyrite.cli import app
        from pyrite.logging import split_verbosity

        count, rest = split_verbosity(["index", "embed", "--force", "-v"], app=app)
        assert (count, rest) == (1, ["index", "embed", "--force"])

    def test_a_dash_v_that_follows_a_value_is_a_flag(self):
        from pyrite.cli import app
        from pyrite.logging import split_verbosity

        count, rest = split_verbosity(["create", "-b", "text", "-v", "--title", "T"], app=app)
        assert (count, rest) == (1, ["create", "-b", "text", "--title", "T"])

    def test_an_unknown_app_falls_back_to_treating_it_as_a_flag(self):
        from pyrite.logging import split_verbosity

        assert split_verbosity(["x", "--opt", "-v"], app=None) == (1, ["x", "--opt"])


class TestTheTraceAndTheWarningAgree:
    """Review item 2. The trace said a keyword search ran for a semantic search
    that was skipped, naming embeddings as the cause, beside a warning that
    named a missing package."""

    @staticmethod
    def _trace(db, mode):
        from pyrite.services.search_service import SearchService

        trace: dict = {}
        warnings: list[str] = []
        SearchService(db).search("falcon", mode=mode, warnings=warnings, trace=trace)
        return trace, warnings

    def test_semantic_without_the_extra(self, indexed, no_extra):
        trace, warnings = self._trace(indexed[1], "semantic")
        assert trace["reason"] == "semantic_extra_missing", trace
        assert trace["actual_mode"] == "none", "nothing ran in pure semantic mode"
        assert "sentence-transformers" in warnings[0]

    def test_hybrid_without_the_extra(self, indexed, no_extra):
        trace, warnings = self._trace(indexed[1], "hybrid")
        assert trace["reason"] == "hybrid_extra_missing", trace
        assert trace["actual_mode"] == "keyword"
        assert "sentence-transformers" in warnings[0]

    def test_extension_not_loaded(self, indexed, monkeypatch):
        import importlib.util

        db = indexed[1]
        load_error = RuntimeError("simulated sqlite-vec loader failure")
        monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: True)
        monkeypatch.setattr(db, "vec_available", False, raising=False)

        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            "importlib.util.find_spec",
            lambda name, *args: object() if name == "sqlite_vec" else real_find_spec(name, *args),
        )
        monkeypatch.setattr(db, "vec_load_error", load_error, raising=False)
        trace, warnings = self._trace(db, "semantic")
        assert trace["reason"] == "semantic_sqlite_vec_not_loaded", trace
        assert "RuntimeError: simulated sqlite-vec loader failure" in warnings[0]
        assert "probably built without" not in warnings[0]

    @staticmethod
    def _installed_sqlite_vec(monkeypatch, load):
        """A `sqlite_vec` that imports and is found, whose `load` is ``load``.

        Injected rather than patched, so the tests below enter the same regime
        whether or not the real package is installed.
        """
        from importlib.machinery import ModuleSpec
        from types import ModuleType

        module = ModuleType("sqlite_vec")
        module.__spec__ = ModuleSpec("sqlite_vec", None)
        module.load = load
        monkeypatch.setitem(sys.modules, "sqlite_vec", module)
        monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: True)

    def test_a_load_that_raises_reaches_the_warning_from_a_real_db(self, tmp_path, monkeypatch):
        """#606's "fails today": nothing here sets `vec_load_error` by hand. The
        loader raises while a real `PyriteDB` opens, and the text has to travel
        connection -> search service -> the warning a user reads."""

        def load(_conn):
            raise RuntimeError("dlopen(vec0.dylib): incompatible architecture")

        self._installed_sqlite_vec(monkeypatch, load)
        db = PyriteDB(tmp_path / "second.db")
        try:
            assert db.vec_available is False
            trace, warnings = self._trace(db, "semantic")
        finally:
            db.close()
        assert trace["reason"] == "semantic_sqlite_vec_not_loaded", trace
        assert "RuntimeError: dlopen(vec0.dylib): incompatible architecture" in warnings[0]
        assert "use a Python whose sqlite3 allows extensions" not in warnings[0]

    def test_a_sqlite3_without_enable_load_extension_keeps_its_remedy(self, tmp_path, monkeypatch):
        """The one failure a different Python fixes still says so (#606's
        acceptance), beside the exception that proves it. The real loader runs
        against a connection that has no `enable_load_extension`, as on a
        Python built without `--enable-loadable-sqlite-extensions`."""
        from pyrite.storage.connection import ConnectionMixin

        class NoLoadableExtensions:
            def __init__(self, conn):
                self._conn = conn

            def __getattr__(self, name):
                if name == "enable_load_extension":
                    return object.__getattribute__(self, name)  # the genuine AttributeError
                return getattr(self._conn, name)

        real_load_extensions = ConnectionMixin._load_extensions

        def load_extensions(self):
            real_conn = self._raw_conn
            self._raw_conn = NoLoadableExtensions(real_conn)
            try:
                real_load_extensions(self)
            finally:
                self._raw_conn = real_conn

        monkeypatch.setattr(ConnectionMixin, "_load_extensions", load_extensions)
        self._installed_sqlite_vec(monkeypatch, lambda conn: conn.load_extension("vec0"))
        db = PyriteDB(tmp_path / "second.db")
        try:
            assert isinstance(db.vec_load_error, AttributeError), db.vec_load_error
            trace, warnings = self._trace(db, "semantic")
        finally:
            db.close()
        assert trace["reason"] == "semantic_sqlite_vec_not_loaded", trace
        assert "AttributeError" in warnings[0] and "enable_load_extension" in warnings[0]
        assert "use a Python whose sqlite3 allows extensions" in warnings[0]

    @pytest.mark.parametrize(
        ("error", "names_the_python"),
        [
            # What CPython raises when SQLite refuses: `enable_load_extension`
            # ("Error enabling load extension") and `load_extension` on a
            # connection where loading is off ("not authorized").
            (sqlite3.OperationalError("Error enabling load extension"), True),
            (sqlite3.OperationalError("not authorized"), True),
            (sqlite3.NotSupportedError("extension loading is not supported"), True),
            (AttributeError("no load_extension", name="load_extension"), True),
            # The same types for another reason are not that failure: the type
            # name alone does not earn the remedy.
            (sqlite3.OperationalError("dlopen(vec0.dylib): no such file"), False),
            (AttributeError("module has no attribute 'load'", name="load"), False),
            (OSError("vec0.dll is not a valid Win32 application"), False),
        ],
    )
    def test_only_a_refused_extension_load_names_the_python(
        self, monkeypatch, error, names_the_python
    ):
        from pyrite.services.embedding_service import semantic_unavailable

        self._installed_sqlite_vec(monkeypatch, lambda conn: None)
        code, cause, remedy = semantic_unavailable(False, error)
        assert code == "sqlite_vec_not_loaded"
        assert f"{type(error).__name__}: {error}" in cause
        assert ("use a Python whose sqlite3 allows extensions" in remedy) is names_the_python

    def test_package_missing(self, indexed, monkeypatch):
        db = indexed[1]
        monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: True)
        monkeypatch.setattr(db, "vec_available", False, raising=False)
        monkeypatch.setattr("importlib.util.find_spec", lambda name, *a: None)
        trace, warnings = self._trace(db, "hybrid")
        assert trace["reason"] == "hybrid_sqlite_vec_missing", trace
        assert "sqlite-vec package is not installed" in warnings[0]

    def test_no_embeddings_keeps_its_reason(self, indexed):
        db = indexed[1]
        if not db.vec_available:
            pytest.skip("sqlite-vec unavailable")
        trace, warnings = self._trace(db, "semantic")
        assert trace["reason"] == "semantic_empty_no_embeddings", trace
        assert "no embeddings" in warnings[0]


class TestPyriteServerSaysItsOwnWarning:
    """Review item 3: the reason the entry point was touched at all."""

    def test_open_registration_warning_is_formatted_on_stderr(self, tmp_path):
        cfg = tmp_path / "data"  # PYRITE_DATA_DIR wins over PYRITE_CONFIG_DIR
        cfg.mkdir(exist_ok=True)
        (cfg / "config.yaml").write_text(
            "settings:\n  auth:\n    enabled: true\n    allow_registration: true\n"
        )
        pre = (
            "from unittest.mock import patch; import uvicorn; "
            "patch('uvicorn.run', side_effect=lambda *a, **k: None).start()"
        )
        proc = _run(tmp_path, SERVER, pre=pre)
        assert "[WARNING] pyrite.server.api:" in proc.stderr, proc.stderr
        assert "allow_registration" in proc.stderr, proc.stderr
        assert proc.stdout == ""


class TestIndexEmbedAccounting:
    def test_counts_helper_three_cases(self):
        from pyrite.cli.index_commands import _embed_counts

        stats = {"embedded": 1, "skipped": 4}
        assert _embed_counts(stats, 0, False) == (1, 4, 0)  # no queue
        assert _embed_counts(stats, 3, False) == (4, 1, 0)  # queue within skipped
        assert _embed_counts(stats, 6, False) == (7, 0, 2)  # queue exceeds skipped
        assert _embed_counts(stats, 3, True) == (1, 4, 0)  # force: embed_all did it all

    def test_a_queue_that_exceeds_skipped_is_said(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from pyrite.cli import app
        from pyrite.services import embedding_service

        _embed_env(tmp_path, monkeypatch, 2)
        real = embedding_service.EmbeddingService.embed_all

        def fewer_skipped(self, *a, **k):
            stats = real(self, *a, **k)
            stats["skipped"] = 0
            return stats

        monkeypatch.setattr(embedding_service.EmbeddingService, "embed_all", fewer_skipped)
        out = CliRunner().invoke(app, ["index", "embed"]).output
        assert "more than" in out and "skipped" in out, out

    def test_other_kbs_settled_by_the_drain_are_counted(self, tmp_path, monkeypatch):
        from typer.testing import CliRunner

        from pyrite.cli import app

        _, db = _embed_env(tmp_path, monkeypatch, 2, other_kb_entries=2)
        out = CliRunner().invoke(app, ["index", "embed", "--kb", "t"]).output
        assert "Embedded: 2" in out, out
        assert "2 entries in other KBs" in out, out
        assert _vector_count(db) == 4

    def test_sqlite_vec_that_will_not_load_gets_the_search_wording(self, monkeypatch):
        import importlib.util

        from typer.testing import CliRunner

        from pyrite.cli import app, index_commands

        load_error = OSError("simulated loadable-extension failure")
        db = type(
            "UnavailableDB",
            (),
            {"vec_available": False, "vec_load_error": load_error},
        )()
        real_find_spec = importlib.util.find_spec
        monkeypatch.setattr(
            importlib.util,
            "find_spec",
            lambda name, *args: object() if name == "sqlite_vec" else real_find_spec(name, *args),
        )
        monkeypatch.setattr("pyrite.services.embedding_service.is_available", lambda: True)
        monkeypatch.setattr(index_commands, "get_config_and_db", lambda: (None, db))
        out = CliRunner().invoke(app, ["index", "embed"]).output
        flat = " ".join(out.split())
        assert "OSError: simulated loadable-extension failure" in flat, out
        assert "probably built without" not in flat, out
        assert "pip install" not in flat, out


@pytest.mark.control(
    reason="dev already reported these two reasons; pins that naming the install causes left them unchanged"
)
def test_hybrid_and_semantic_keep_their_published_no_embeddings_reasons(indexed):
    db = indexed[1]
    if not db.vec_available:
        pytest.skip("sqlite-vec unavailable")
    assert (
        TestTheTraceAndTheWarningAgree._trace(db, "hybrid")[0]["reason"] == "hybrid_no_embeddings"
    )
    assert (
        TestTheTraceAndTheWarningAgree._trace(db, "semantic")[0]["reason"]
        == "semantic_empty_no_embeddings"
    )
