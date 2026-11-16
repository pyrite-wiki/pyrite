"""An unexpected fault anywhere in an MCP tool call shows the client no text.

Property: whatever step of a tool call raises an exception that is not a
`PyriteError` -- a KB check, a plugin's `default_kb`, the id lookup, the
landing rule, the truncated-body check, the handler -- the client gets
``INTERNAL``, a fixed message and ``retryable``, and the exception's text goes
to the server log only, as REST's 500 does. A `PyriteError` keeps its own code
and public message.
"""

from __future__ import annotations

import logging

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.server import mcp_server
from pyrite.server.mcp_server import PyriteMCPServer

DETAIL = "/srv/private/path/index.db: disk I/O error near 'secret_table'"


@pytest.fixture
def server(tmp_path):
    (tmp_path / "kb").mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="kb", path=tmp_path / "kb", kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    s = PyriteMCPServer(config=config, tier="admin")
    try:
        yield s
    finally:
        s.close()


def _break(server, name: str, exc: Exception) -> None:
    def handler(args, **kwargs):
        raise exc

    server.tools[name] = {**server.tools[name], "handler": handler}
    server.__dict__.get("_handler_keywords", {}).pop(name, None)


def _raise(*args, **kwargs):
    raise RuntimeError(DETAIL)


def _assert_internal(result, caplog):
    assert result["error_code"] == "INTERNAL", result
    assert result["retryable"] is True
    assert "secret_table" not in str(result) and "/srv/private" not in str(result), result
    assert "secret_table" in caplog.text, "the detail is in the server log"


@pytest.mark.parametrize("exc", [RuntimeError(DETAIL), OSError(5, DETAIL), KeyError(DETAIL)])
def test_a_handler_fault_answers_without_its_text(server, caplog, exc):
    _break(server, "kb_list", exc)
    with caplog.at_level(logging.ERROR, logger="pyrite.server.mcp_server"):
        result = server._dispatch_tool("kb_list", {}, client_kind="local")
    _assert_internal(result, caplog)


# Each step is the dispatcher's own (pyrite.server), faulted where it runs;
# the services behind it are left real.


def _fault_in_kb_resolution(server, monkeypatch):
    monkeypatch.setattr(mcp_server, "_kb_refusal", _raise)
    return "kb_list_entries", {"kb_name": "kb"}


def _fault_in_landing_rule(server, monkeypatch):
    monkeypatch.setattr(PyriteMCPServer, "_landing_target", _raise)
    return "kb_create", {"kb_name": "kb", "entry_type": "note", "title": "T"}


def _fault_in_truncated_body_check(server, monkeypatch):
    monkeypatch.setattr(PyriteMCPServer, "_refuse_truncated_write", _raise)
    return "task_create", {"kb_name": "kb", "title": "T"}


def _fault_in_id_lookup(server, monkeypatch):
    monkeypatch.setattr(PyriteMCPServer, "_locate_entry_ids", _raise)
    name = next(n for n, t in server.tools.items() if t.get("locates_entry"))
    args = dict.fromkeys(server.tools[name]["locates_entry"]["id_args"], "x")
    return name, args


def _fault_in_default_kb(server, monkeypatch):
    name = next(n for n, t in server.tools.items() if t.get("default_kb"))
    monkeypatch.setitem(server.tools, name, {**server.tools[name], "default_kb": _raise})
    return name, {}


BEFORE_THE_HANDLER = {
    "kb resolution": _fault_in_kb_resolution,
    "landing rule": _fault_in_landing_rule,
    "truncated-body check": _fault_in_truncated_body_check,
    "id lookup": _fault_in_id_lookup,
    "default_kb": _fault_in_default_kb,
}


@pytest.mark.parametrize("step", list(BEFORE_THE_HANDLER))
def test_a_fault_before_the_handler_answers_without_its_text(server, caplog, monkeypatch, step):
    tool, args = BEFORE_THE_HANDLER[step](server, monkeypatch)
    with caplog.at_level(logging.ERROR, logger="pyrite.server.mcp_server"):
        result = server._dispatch_tool(tool, args, client_kind="local")
    _assert_internal(result, caplog)


@pytest.mark.control(reason="a Pyrite refusal keeps its own public message, as before")
def test_a_pyrite_refusal_keeps_its_message(server):
    from pyrite.exceptions import ValidationError

    _break(server, "kb_list", ValidationError("limit must be a number"))
    result = server._dispatch_tool("kb_list", {}, client_kind="local")
    assert result["error"] == "limit must be a number", result


def test_a_pyrite_refusal_before_the_handler_keeps_its_code(server, monkeypatch):
    from pyrite.exceptions import ValidationError

    def refuse(*args, **kwargs):
        raise ValidationError("not that one")

    monkeypatch.setattr(PyriteMCPServer, "_refuse_truncated_write", refuse)
    result = server._dispatch_tool("task_create", {"kb_name": "kb", "title": "T"})
    assert (result["error_code"], result["error"]) == ("VALIDATION_FAILED", "not that one")


# -- prompts and resources keep the tools' contract ---------------------------


def test_a_prompt_fault_answers_without_its_text(server, caplog):
    """``prompts/get`` is a third KB-content chokepoint: a non-Pyrite
    exception in it is a fault, logged, and the client gets ``INTERNAL``."""
    server._prompt_research_topic = _raise
    with caplog.at_level(logging.ERROR, logger="pyrite.server.mcp_server"):
        result = server._get_prompt("research_topic", {"topic": "t"})
    _assert_internal(result, caplog)


def test_a_resource_fault_answers_without_its_text(server, caplog, monkeypatch):
    """Faulted in the dispatcher's own KB check on the resource's path, the
    services behind it left real."""
    monkeypatch.setattr(mcp_server, "_kb_refusal", _raise)
    with caplog.at_level(logging.ERROR, logger="pyrite.server.mcp_server"):
        result = server._read_resource("pyrite://kbs/kb/entries")
    _assert_internal(result, caplog)


@pytest.mark.control(reason="a refusal on a resource path kept its code before too")
def test_a_resource_refusal_keeps_its_own_code(server):
    """A resource naming a KB the config does not hold is that refusal, with
    its own code and message, never ``INTERNAL``."""
    result = server._read_resource("pyrite://kbs/no-such-kb/entries")
    assert result.get("error_code") not in (None, "INTERNAL"), result
