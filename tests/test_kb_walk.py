"""Every surface asks the one KB resolver -- walked, not argued (#952).

`tests/test_kb_resolver.py` pins the rule (`services/kb_resolver.py`). This
file pins that every REST route and every MCP tool that takes a KB name hands
it to that rule, by driving each one through the real wiring with the
characterization harness's own call builders (`tests/characterization/`), so
a new route or tool is walked the moment it declares a KB parameter -- and
fails here until it resolves through the resolver. The CLI half is
`tests/test_kb_walk_cli.py`.

Per surface and per caller (an operator key, unscoped; a self-registered user,
scoped to the readable KB):

- the **full name** of a readable KB is never `KB_NOT_FOUND`;
- an **unknown** name is `KB_NOT_FOUND`;
- a KB the caller **cannot read** answers byte for byte like an unknown one
  (ADR-0037 §4), by full name and by shortname;
- a **shortname** of a readable KB is `KB_NOT_FOUND` with `did_you_mean`
  naming the full name (shortnames are link syntax only, ADR-0049 Q3);
- a **blank** name is never `KB_NOT_FOUND`: it names no KB (#846/#528).

A call whose arguments do not carry the KB string at all (the harness fills
a row id from the KB instead: `DELETE /api/reviews/{review_id}`, `kb_list`)
names no KB on that surface and is skipped by inspection of the built call,
not by a hand-kept list.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from pyrite.server.api import KB_PARAM_NAMES
from pyrite.server.mcp_server import KB_ARGUMENT_NAMES
from pyrite.services.kb_resolver import near_kb_names
from tests.characterization.mcp_calls import build_arguments
from tests.characterization.rest_calls import build_call
from tests.characterization.surfaces import (
    kb_bearing_mcp_tool_names,
    kb_bearing_rest_operations,
)
from tests.characterization.world import MISSING, PRIVATE, READABLE, build_world

SHORT = {READABLE: "rdb", PRIVATE: "pvt"}
UNKNOWN = MISSING
PRINCIPALS = ("admin_key", "local_user")  # unscoped; scoped to READABLE


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = build_world(tmp_path_factory, label="kb-walk", shortnames=SHORT)
    try:
        yield w
    finally:
        w.close()


def _readable(world, principal_name):
    p = world.principals[principal_name]
    return None if p.readable_kbs is None else set(p.readable_kbs)


def _carries(value: Any, name: str) -> bool:
    return value == name or (isinstance(value, list | tuple) and name in value)


def _names_kb_rest(path: str, kwargs: dict, kb: str) -> bool:
    """Does this call name `kb` where REST's resolver reads a KB
    (`api._resolve_kb_names`: a KB path parameter, a `KB_PARAM_NAMES` query
    or body key)? A call that only carries it elsewhere -- a repo name, an
    entry id, a per-item KB in a batch body -- names no KB on this route."""
    if kb and kb in kwargs["url"]:
        if any(f"{{{p}}}" in path for p in KB_PARAM_NAMES) or path.startswith("/api/kbs/{name}"):
            return True
    for where in ("params", "json"):
        payload = kwargs.get(where)
        if isinstance(payload, dict) and any(_carries(payload.get(p), kb) for p in KB_PARAM_NAMES):
            return True
    return False


def _names_kb_mcp(args: dict, kb: str) -> bool:
    """Does this call name `kb` where the MCP chokepoint reads a KB
    (`_kbs_named_in`: a top-level `KB_ARGUMENT_NAMES` argument)? A per-item
    KB in `kb_batch_read`'s list is answered per item, not refused whole."""
    return any(_carries(args.get(p), kb) for p in KB_ARGUMENT_NAMES)


def _refused_before_the_kb(status: int, body: Any, kb: str) -> bool:
    """A 401/403 that does not mention the KB: the global tier floor or a
    session requirement refused the request before any KB was read. The
    same answer for every name, so no oracle; not the resolver's to give."""
    return status in (401, 403) and kb not in json.dumps(body)


def _swap(body: Any, a: str, b: str) -> str:
    return json.dumps(body, sort_keys=True).replace(a, "<kb>").replace(b, "<kb>")


# ---------------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------------


def _rest(world, principal_name: str, method: str, path: str, kb: str, tag: str):
    """(status, body) of `method path` naming `kb`, or None when the built
    call does not name it."""
    principal = world.principals[principal_name]
    # The key becomes part of a disposable entry id, so it must be URL-safe.
    tag = re.sub(r"[^A-Za-z0-9-]", "_", tag)
    # Before the call is built, not after: building it may write a fixture
    # row through the world's own session, and a server thread's idle
    # fallback session holding a connection is what makes that write wait
    # for the busy timeout on a slow runner ("database is locked", which
    # rolls the session back and loses the row for every later call). See
    # `World.release_idle_connections`.
    world.release_idle_connections()
    kwargs = build_call(world, method, path, kb, call_key=f"walk-{principal_name}-{tag}")
    if "skip" in kwargs or not _names_kb_rest(path, kwargs, kb):
        return None
    url = kwargs.pop("url")
    resp = world.client.request(
        method,
        url,
        headers=principal.rest_headers or None,
        cookies=principal.rest_cookies or None,
        **kwargs,
    )
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    return resp.status_code, body


def _rest_code(answer) -> str | None:
    status, body = answer
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail.get("code") if isinstance(detail, dict) else None


def _rest_ops(world):
    return sorted((op.method, op.path) for op in kb_bearing_rest_operations(world.app))


class TestRest:
    def test_the_world_offers_no_near_match_for_the_probe_names(self, world):
        """The byte comparison below relies on it for the unscoped caller
        (a scoped one is offered no near match at all): near matches are a
        function of the name, so two names with none compare whole."""
        assert near_kb_names(world.config, UNKNOWN, _readable(world, "admin_key")) == []

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_a_full_name_is_never_kb_not_found(self, world, principal_name):
        wrong = []
        for method, path in _rest_ops(world):
            answer = _rest(world, principal_name, method, path, READABLE, f"full-{method}{path}")
            if answer is not None and _rest_code(answer) == "KB_NOT_FOUND":
                wrong.append((method, path, answer))
        assert not wrong, wrong

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_an_unknown_name_is_kb_not_found(self, world, principal_name):
        wrong = []
        for method, path in _rest_ops(world):
            answer = _rest(world, principal_name, method, path, UNKNOWN, f"unk-{method}{path}")
            if answer is None or _refused_before_the_kb(*answer, UNKNOWN):
                continue
            if (answer[0], _rest_code(answer)) != (404, "KB_NOT_FOUND"):
                wrong.append((method, path, answer))
        assert not wrong, wrong

    @pytest.mark.parametrize("name", [PRIVATE, SHORT[PRIVATE]], ids=["full", "shortname"])
    def test_an_unreadable_kb_answers_like_an_unknown_one(self, world, name):
        diverged = []
        for method, path in _rest_ops(world):
            private = _rest(world, "local_user", method, path, name, f"priv-{method}{path}")
            unknown = _rest(world, "local_user", method, path, UNKNOWN, f"unk2-{method}{path}")
            if private is None or unknown is None:
                continue
            if private[0] != unknown[0] or _swap(private[1], name, UNKNOWN) != _swap(
                unknown[1], name, UNKNOWN
            ):
                diverged.append((method, path, private, unknown))
            if PRIVATE in _swap(private[1], name, UNKNOWN):
                diverged.append((method, path, "names the private KB", private))
        assert not diverged, diverged

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_a_readable_kbs_shortname_is_refused_with_the_full_name(self, world, principal_name):
        wrong = []
        for method, path in _rest_ops(world):
            answer = _rest(
                world, principal_name, method, path, SHORT[READABLE], f"short-{method}{path}"
            )
            if answer is None or _refused_before_the_kb(*answer, SHORT[READABLE]):
                continue
            status, body = answer
            detail = body.get("detail") if isinstance(body, dict) else {}
            if (
                status != 404
                or _rest_code(answer) != "KB_NOT_FOUND"
                or detail.get("did_you_mean") != [READABLE]
                or READABLE not in str(detail.get("hint"))
            ):
                wrong.append((method, path, answer))
        assert not wrong, wrong

    def test_an_operator_key_is_told_a_private_kbs_full_name(self, world):
        wrong = []
        for method, path in _rest_ops(world):
            answer = _rest(world, "admin_key", method, path, SHORT[PRIVATE], f"shp-{method}{path}")
            if answer is None or _refused_before_the_kb(*answer, SHORT[PRIVATE]):
                continue
            detail = answer[1].get("detail") if isinstance(answer[1], dict) else {}
            if _rest_code(answer) != "KB_NOT_FOUND" or detail.get("did_you_mean") != [PRIVATE]:
                wrong.append((method, path, answer))
        assert not wrong, wrong

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_a_blank_name_is_never_kb_not_found(self, world, principal_name):
        wrong = []
        for method, path in _rest_ops(world):
            if "{kb" in path:
                continue  # a path cannot be blank
            principal = world.principals[principal_name]
            kwargs = build_call(world, method, path, "", call_key=f"blank-{principal_name}")
            if "skip" in kwargs:
                continue
            world.release_idle_connections()
            url = kwargs.pop("url")
            resp = world.client.request(
                method,
                url,
                headers=principal.rest_headers or None,
                cookies=principal.rest_cookies or None,
                **kwargs,
            )
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            if _rest_code((resp.status_code, body)) == "KB_NOT_FOUND":
                wrong.append((method, path, resp.status_code, body))
        assert not wrong, wrong


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def _mcp(world, principal_name: str, tool: str, kb: str, tag: str):
    principal = world.principals[principal_name]
    world.release_idle_connections()  # as in `_rest`: before the build's writes
    args = build_arguments(world, tool, kb, call_key=f"walk-{principal_name}-{tag}")
    if not _names_kb_mcp(args, kb):
        return None
    return world.dispatch_tool(
        tool,
        args,
        client_id=f"walk-{principal_name}-{tool}-{tag}",
        readable_kbs=None if principal.readable_kbs is None else set(principal.readable_kbs),
        writable_kbs=None if principal.writable_kbs is None else set(principal.writable_kbs),
    )


def _tools(world):
    return kb_bearing_mcp_tool_names(world.mcp_server)


class TestMcp:
    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_a_full_name_is_never_kb_not_found(self, world, principal_name):
        wrong = []
        for tool in _tools(world):
            out = _mcp(world, principal_name, tool, READABLE, "full")
            if (
                out is not None
                and isinstance(out, dict)
                and out.get("error_code") == "KB_NOT_FOUND"
            ):
                wrong.append((tool, out))
        assert not wrong, wrong

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_an_unknown_name_is_kb_not_found(self, world, principal_name):
        wrong = []
        for tool in _tools(world):
            out = _mcp(world, principal_name, tool, UNKNOWN, "unk")
            if out is not None and (
                not isinstance(out, dict) or out.get("error_code") != "KB_NOT_FOUND"
            ):
                wrong.append((tool, out))
        assert not wrong, wrong

    @pytest.mark.parametrize("name", [PRIVATE, SHORT[PRIVATE]], ids=["full", "shortname"])
    def test_an_unreadable_kb_answers_like_an_unknown_one(self, world, name):
        diverged = []
        for tool in _tools(world):
            private = _mcp(world, "local_user", tool, name, "priv")
            unknown = _mcp(world, "local_user", tool, UNKNOWN, "unk2")
            if private is None or unknown is None:
                continue
            if _swap(private, name, UNKNOWN) != _swap(unknown, name, UNKNOWN):
                diverged.append((tool, private, unknown))
            if PRIVATE in _swap(private, name, UNKNOWN):
                diverged.append((tool, "names the private KB", private))
        assert not diverged, diverged

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_a_readable_kbs_shortname_is_refused_with_the_full_name(self, world, principal_name):
        wrong = []
        for tool in _tools(world):
            out = _mcp(world, principal_name, tool, SHORT[READABLE], "short")
            if out is None:
                continue
            if (
                not isinstance(out, dict)
                or out.get("error_code") != "KB_NOT_FOUND"
                or out.get("did_you_mean") != [READABLE]
                or READABLE not in str(out.get("suggestion"))
            ):
                wrong.append((tool, out))
        assert not wrong, wrong

    def test_an_operator_key_is_told_a_private_kbs_full_name(self, world):
        wrong = []
        for tool in _tools(world):
            out = _mcp(world, "admin_key", tool, SHORT[PRIVATE], "shp")
            if out is None:
                continue
            if not isinstance(out, dict) or out.get("did_you_mean") != [PRIVATE]:
                wrong.append((tool, out))
        assert not wrong, wrong

    @pytest.mark.parametrize("principal_name", PRINCIPALS)
    def test_a_blank_name_is_never_kb_not_found(self, world, principal_name):
        wrong = []
        principal = world.principals[principal_name]
        for tool in _tools(world):
            args = build_arguments(world, tool, "", call_key=f"blank-{principal_name}")
            out = world.dispatch_tool(
                tool,
                args,
                client_id=f"walk-blank-{principal_name}-{tool}",
                readable_kbs=None
                if principal.readable_kbs is None
                else set(principal.readable_kbs),
                writable_kbs=None
                if principal.writable_kbs is None
                else set(principal.writable_kbs),
            )
            if isinstance(out, dict) and out.get("error_code") == "KB_NOT_FOUND":
                wrong.append((tool, out))
        assert not wrong, wrong
