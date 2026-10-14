"""Where a ``public_message`` replaces an exception's text, the cause is logged (#848).

The user's model: "When a command is refused, I can find out why, without a
debugger." The implementation model: ``public_message`` decides what a response
*shows*; ``pyrite.exceptions.public_text`` is the one function that decides it,
and it logs ``str(exc)`` whenever it substitutes. On the CLI that log is stderr
at the default level (WARNING).

Entered through each surface's own entry point: the real ``pyrite`` root in a
subprocess with a planted command body that raises (``tests/outcome_walk.py``),
``cli_error_from``, MCP ``_dispatch_tool`` and ``_get_prompt``, and REST
``error_response``. The bulk-link test is service-level, not an entry point:
``KBService.add_links`` with a ``DocumentManager`` subclass whose save fails.
Two source scans: none reads
``.public_message`` but the helper, and every logger passed as ``log=`` is under
``pyrite`` (so ``configure_logging`` controls it).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pyrite.exceptions import (
    BrandingInvalidError,
    ConfigError,
    ConfigFileUnreadableError,
    ConfigSaveRefusedError,
    KBAlreadyExistsError,
    PluginError,
    PyriteError,
    StorageError,
)
from tests.outcome_walk import planted, root_app

REPO = Path(__file__).resolve().parents[1]
DETAIL = "DETAIL-/secret/path/x.db"


def _pyrite_logger_state():
    package_logger = logging.getLogger("pyrite")
    return (package_logger.handlers[:], package_logger.level, package_logger.propagate)


@pytest.fixture(scope="module", autouse=True)
def _pyrite_logger_at_module_start():
    """What the ``pyrite`` logger was before this file's first test."""
    return _pyrite_logger_state()


CLASSES = [
    StorageError,
    ConfigError,
    PluginError,
    BrandingInvalidError,
    ConfigSaveRefusedError,
    ConfigFileUnreadableError,
]


@pytest.mark.control(reason="pins that CLASSES covers every masking class; true before the change")
def test_the_class_list_is_every_class_that_sets_a_public_message():
    def subclasses(c):
        for s in c.__subclasses__():
            yield s
            yield from subclasses(s)

    setters = {c for c in subclasses(PyriteError) if "public_message" in vars(c)}
    masking = {c for c in setters if c.public_message is not None}
    # every masking class is one of ours or a subclass of one (inherits the text)
    assert all(issubclass(c, tuple(CLASSES)) for c in masking), masking
    # a masking class that sets an empty string would show nothing at all
    assert all(c.public_message.strip() for c in masking), masking


_DRIVER = """
import sys
from tests.outcome_walk import args_for, click_command, planted, root_app
import pyrite.exceptions as ex
cls = getattr(ex, sys.argv[1])
detail, fmt = sys.argv[2], sys.argv[3]
app = root_app("pyrite")
path = ("task", "claim")
def body():
    raise cls(detail)
with planted(app, path, body):
    sys.argv = ["pyrite", *path, *args_for(click_command(app, path)), "--format", fmt]
    from pyrite.logging import configure_entry_point_logging
    configure_entry_point_logging(app=app)  # what main() does, at its default level
    app()
"""


@pytest.mark.parametrize("cls", CLASSES, ids=lambda c: c.__name__)
@pytest.mark.parametrize("fmt", ["json", "rich"])
def test_a_masked_refusal_on_the_cli_puts_the_cause_on_stderr(cls, fmt):
    """Through the real root in a real process: stderr is where the log lands."""
    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER, cls.__name__, DETAIL, fmt],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert DETAIL not in proc.stdout
    if fmt == "json":
        doc = json.loads(proc.stdout)
        assert doc["error_code"] == cls.error_code
        assert doc["error"] == cls.public_message
    else:
        assert cls.error_code in proc.stdout
    assert proc.stderr.count(DETAIL) == 1, proc.stderr


def test_cli_error_from_logs_the_cause(caplog):
    from pyrite.utils.errors import cli_error_from
    import typer

    with caplog.at_level(logging.WARNING), pytest.raises(typer.Exit):
        cli_error_from(StorageError(DETAIL), "json")
    assert any(DETAIL in r.getMessage() for r in caplog.records)


def test_mcp_refusal_logs_the_cause_and_the_body_is_unchanged(caplog):
    from pyrite.server.mcp_server import _refusal

    with caplog.at_level(logging.WARNING):
        body = _refusal(StorageError(DETAIL))
    assert StorageError.public_message in json.dumps(body)
    assert DETAIL not in json.dumps(body)
    assert [r for r in caplog.records if DETAIL in r.getMessage()]


def test_a_class_with_no_public_message_shows_its_text_and_logs_nothing_extra(caplog):
    from pyrite.exceptions import public_text

    with caplog.at_level(logging.DEBUG):
        text = public_text(KBAlreadyExistsError(DETAIL))
    assert text == DETAIL
    assert not caplog.records


def test_already_logged_is_not_logged_again(caplog):
    from pyrite.exceptions import public_text

    with caplog.at_level(logging.DEBUG):
        assert public_text(StorageError(DETAIL), already_logged=True) == StorageError.public_message
    assert not caplog.records


@pytest.mark.control(
    reason="REST already logged once; this pins that moving it into the helper kept it at one"
)
def test_rest_5xx_is_logged_once(caplog):
    from pyrite.server.errors import error_response

    with caplog.at_level(logging.DEBUG):
        status, _ = error_response(StorageError(DETAIL))
    assert status >= 500
    assert len([r for r in caplog.records if DETAIL in r.getMessage()]) == 1


@pytest.mark.control(
    reason="REST already logged once; this pins that moving it into the helper kept it at one"
)
def test_rest_sub_500_is_logged_once(caplog):
    from pyrite.server.errors import error_response

    with caplog.at_level(logging.DEBUG):
        status, _ = error_response(ConfigSaveRefusedError(DETAIL))
    assert status < 500
    assert len([r for r in caplog.records if DETAIL in r.getMessage()]) == 1


# -- The source scan -----------------------------------------------------------

HELPER_HOME = "pyrite/exceptions.py"


def _helper_lines(source: str) -> range:
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name == "public_text":
            return range(node.lineno, node.end_lineno + 1)
    return range(0)


def readers_of_public_message(source: str) -> list[int]:
    """Lines that read ``.public_message`` or ``getattr(x, "public_message", ...)``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute) and node.attr == "public_message":
            if isinstance(node.ctx, ast.Load):
                found.append(node.lineno)
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "public_message"
        ):
            found.append(node.lineno)
    return found


def test_the_scan_sees_each_way_of_reading():
    assert readers_of_public_message("x = e.public_message or str(e)") == [1]
    assert readers_of_public_message("x = getattr(e, 'public_message', None)") == [1]
    assert readers_of_public_message("class A:\n    public_message = None") == []


def test_only_public_text_reads_public_message():
    roots = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]
    offenders = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            source = path.read_text()
            exempt = _helper_lines(source) if rel == HELPER_HOME else range(0)
            offenders += [
                f"{rel}:{n}" for n in readers_of_public_message(source) if n not in exempt
            ]
    assert not offenders, (
        "read public_text(exc) from pyrite.exceptions instead of .public_message, so the "
        "cause is logged where it is masked (#848):\n" + "\n".join(offenders)
    )


def test_a_read_in_exceptions_py_outside_public_text_is_caught():
    source = "def public_text(e):\n    return e.public_message\n\ndef other(e):\n    return e.public_message\n"
    exempt = _helper_lines(source)
    assert [n for n in readers_of_public_message(source) if n not in exempt] == [5]


# -- MCP: through the dispatchers, not _refusal ---------------------------------


@pytest.fixture
def mcp_server(tmp_path):
    """A real server whose tool and prompt handlers raise a storage fault.

    A subclass, not a patch: the dispatchers are the code under test, and the
    handler's raise stands for a database failing (Postgres raises
    ``StorageError`` from a failed query, postgres_backend.py:292), which a
    live backend cannot be made to do on cue.
    """
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.server.mcp_server import PyriteMCPServer

    class FaultingServer(PyriteMCPServer):
        fault: type[PyriteError] = StorageError

        def _kb_get(self, *args, **kwargs):
            raise self.fault(DETAIL)

        def _prompt_summarize_entry(self, *args, **kwargs):
            raise self.fault(DETAIL)

    kb = tmp_path / "kb"
    kb.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    server = FaultingServer(config=config, tier="admin")
    try:
        yield server
    finally:
        server.close()


def _causes(caplog):
    return [r for r in caplog.records if DETAIL in r.getMessage() or DETAIL in str(r.exc_info)]


def test_an_mcp_tool_storage_fault_is_logged_once(mcp_server, caplog):
    with caplog.at_level(logging.DEBUG):
        body = mcp_server._dispatch_tool("kb_get", {"entry_id": "x", "kb_name": "kb"})
    assert StorageError.public_message in json.dumps(body)
    assert DETAIL not in json.dumps(body)
    records = _causes(caplog)
    assert len(records) == 1, [(r.name, r.levelname) for r in records]
    assert records[0].levelname == "ERROR"  # the dispatcher's, with a traceback (#431)


def test_an_mcp_prompt_storage_fault_is_logged_once(mcp_server, caplog):
    with caplog.at_level(logging.DEBUG):
        body = mcp_server._get_prompt(
            "summarize_entry", {"entry_id": "x", "kb_name": "kb"}, readable_kbs=None
        )
    assert StorageError.public_message in json.dumps(body)
    assert DETAIL not in json.dumps(body)
    records = _causes(caplog)
    assert len(records) == 1, [(r.name, r.levelname) for r in records]
    assert records[0].levelname == "ERROR"


@pytest.mark.parametrize("fault", [PluginError, ConfigError, BrandingInvalidError])
def test_a_masked_non_storage_error_through_the_mcp_dispatchers_is_logged_once(
    mcp_server, caplog, fault
):
    """Only a StorageError is logged by the dispatcher; the helper logs the rest."""
    mcp_server.fault = fault
    with caplog.at_level(logging.DEBUG):
        tool = mcp_server._dispatch_tool("kb_get", {"entry_id": "x", "kb_name": "kb"})
        tool_records = _causes(caplog)
        caplog.clear()
        prompt = mcp_server._get_prompt(
            "summarize_entry", {"entry_id": "x", "kb_name": "kb"}, readable_kbs=None
        )
        prompt_records = _causes(caplog)
    for body in (tool, prompt):
        assert fault.public_message in json.dumps(body)
        assert DETAIL not in json.dumps(body)
    for records in (tool_records, prompt_records):
        assert [(r.name, r.levelname) for r in records] == [("pyrite.server.mcp_server", "WARNING")]


# -- Every logger handed to public_text is one configure_logging controls ----------


def test_the_scan_of_log_arguments_sees_a_logger_outside_pyrite():
    bad = 'import logging\nlog = logging.getLogger("other.x")\npublic_text(e, log=log)\n'
    good = 'import logging\nlog = logging.getLogger("pyrite.ext.x")\npublic_text(e, log=log)\n'
    assert logger_names_passed(bad, "pkg.mod") == ["other.x"]
    assert logger_names_passed(good, "pkg.mod") == ["pyrite.ext.x"]
    named = "import logging\nlogger = logging.getLogger(__name__)\npublic_text(e, log=logger)\n"
    assert logger_names_passed(named, "pyrite_social.plugin") == ["pyrite_social.plugin"]


def logger_names_passed(source: str, module: str) -> list[str]:
    """The name of each logger a ``public_text(..., log=<name>)`` call passes.

    Known limits, so a reader does not take a green scan for more than it is:
    it matches the bare name ``public_text(...)`` only, so it does not see an
    aliased import (``from pyrite.exceptions import public_text as pt``) or
    ``exceptions.public_text(...)``; it reads ``log=`` keywords only, so a
    ``**kw`` splat is invisible; and it resolves module-level
    ``name = logging.getLogger(...)`` assignments only, so a function-local
    logger or a reassignment inside a function reads as ``<unresolved>`` (which
    fails the scan) or, for a rebinding, is missed. Review is the check on those.
    """
    tree = ast.parse(source)
    named: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "attr", None) == "getLogger"
            and node.value.args
        ):
            arg = node.value.args[0]
            if isinstance(arg, ast.Name) and arg.id == "__name__":
                named[node.targets[0].id] = module
            elif isinstance(arg, ast.Constant):
                named[node.targets[0].id] = arg.value
            else:
                named[node.targets[0].id] = "<unresolved>"
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "public_text":
            for kw in node.keywords:
                if kw.arg == "log":
                    value = kw.value
                    names.append(
                        named.get(value.id, "<unresolved>")
                        if isinstance(value, ast.Name)
                        else "<unresolved>"
                    )
    return names


def _module_name(rel: str) -> str:
    parts = Path(rel).with_suffix("").parts
    if parts[0] == "extensions":  # extensions/<name>/src/<pkg>/...
        parts = parts[3:]
    return ".".join(parts)


def test_every_logger_passed_to_public_text_is_under_pyrite():
    """A logger outside `pyrite` has no handler or level from configure_logging
    (it sets the only handler on `pyrite`, propagate=False): its records print
    bare through Python's last-resort handler, even at level ERROR (#848)."""
    roots = [REPO / "pyrite", *sorted((REPO / "extensions").glob("*/src"))]
    bad = []
    seen = 0
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            for name in logger_names_passed(path.read_text(), _module_name(rel)):
                seen += 1
                if not (name == "pyrite" or name.startswith("pyrite.")):
                    bad.append(f"{rel}: log= is {name!r}")
    assert seen >= 10, "the scan found no call sites; it is not looking"
    assert not bad, "\n".join(bad)


@pytest.fixture
def restore_pyrite_logger():
    """Put the ``pyrite`` logger back exactly (handlers, level, propagate)."""
    package_logger = logging.getLogger("pyrite")
    handlers, level, propagate = _pyrite_logger_state()
    yield
    package_logger.handlers = handlers
    package_logger.setLevel(level)
    package_logger.propagate = propagate


def test_an_extension_s_masked_error_obeys_the_configured_level(restore_pyrite_logger):
    import io

    from pyrite.logging import configure_logging

    pytest.importorskip("pyrite_software_kb")
    from pyrite_software_kb.plugin import _safe_error

    stream = io.StringIO()
    configure_logging(level="ERROR", stream=stream)
    assert _safe_error(StorageError(DETAIL)) == StorageError.public_message
    assert stream.getvalue() == ""  # a WARNING under level ERROR stays quiet
    configure_logging(level="WARNING", stream=stream)
    _safe_error(StorageError(DETAIL))
    assert stream.getvalue().count(DETAIL) == 1


# -- Bulk link save: one failed save is one cause, not one per position ----------


def test_a_failed_bulk_link_save_logs_its_cause_once(tmp_path, caplog):
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.services.kb_service import KBService
    from pyrite.storage.database import PyriteDB
    from pyrite.storage.document_manager import DocumentManager
    from pyrite.storage.index import IndexManager

    kb = tmp_path / "kb"
    kb.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)

    class FailingSave(DocumentManager):
        """The injection point KBService takes: a save that fails on cue."""

        def save_entry(self, *args, **kwargs):
            raise StorageError(DETAIL)

    try:
        KBService(config, db).create_entry("kb", "a", "A", "note", "body")
        svc = KBService(config, db, doc_mgr=FailingSave(db, IndexManager(db, config)))
        links = [{"source": "a", "target": f"t{i}"} for i in range(3)]
        with caplog.at_level(logging.DEBUG):
            results = svc.add_links("kb", links)
    finally:
        db.close()
    assert [r["status"] for r in results] == ["failed"] * 3
    assert all(r["error"] == StorageError.public_message for r in results)
    assert len(_causes(caplog)) == 1, [(r.name, r.levelname) for r in _causes(caplog)]


@pytest.mark.control(reason="Runs last: no test above leaves global logging state changed")
def test_zz_no_test_in_this_file_leaves_the_pyrite_logger_changed(_pyrite_logger_at_module_start):
    """``configure_logging`` replaces the handlers, sets the level and
    ``propagate=False``; left behind it hides ``pyrite.*`` from ``caplog`` in
    every later test on the xdist worker (the pattern in
    tests/test_logging_configuration.py)."""
    assert _pyrite_logger_state() == _pyrite_logger_at_module_start
