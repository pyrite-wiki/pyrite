"""Concurrent MCP calls are safe on the shared per-tier server.

Calls run in worker threads against one ``PyriteMCPServer``. Two properties:

- lazily built shared state (the index worker, the per-target service cache)
  is built once, however many calls ask at the same moment;
- a write made by a call on one thread is seen by the next call on another
  (a thread must not keep a database snapshot from its earlier call).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.server.mcp_server import PyriteMCPServer
from tests._world import build_world


@pytest.fixture
def world(tmp_path: Path):
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name="k", path=tmp_path / "w" / "k", kb_type="generic")],
        rest=False,
    )
    w.add_entry("k", "---\nid: first\ntitle: First\ntype: note\n---\n\nBody.\n")
    yield w
    w.close()


def _in_threads(count: int, fn):
    barrier = threading.Barrier(count, timeout=10)
    results, errors = [None] * count, []

    def run(i):
        try:
            barrier.wait(timeout=10)
            results[i] = fn(i)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    return results


def test_the_index_worker_is_built_once_under_concurrent_calls(world):
    server = PyriteMCPServer(config=world.config, tier="admin")
    try:
        for _ in range(5):  # a fresh race each round
            server._index_worker = None
            workers = _in_threads(8, lambda i: server._get_index_worker())
            assert len({id(w) for w in workers}) == 1
    finally:
        server.close()


def test_a_per_target_service_is_built_once_under_concurrent_calls(world):
    import time

    server = PyriteMCPServer(config=world.config, tier="read")
    built = []

    def build(config, db):
        built.append(1)
        time.sleep(0.05)
        return object()

    try:
        services = _in_threads(8, lambda i: server._per_target("probe", build))
        assert len(built) == 1
        assert len({id(s) for s in services}) == 1
    finally:
        server.close()


@pytest.mark.control(
    reason="control: on SQLite no stale read was reproducible before; pins the property"
)
def test_a_write_on_one_thread_is_read_by_the_next_call_on_another(world):
    """Thread A reads, thread B writes, thread A reads again: A sees the write."""
    server = PyriteMCPServer(config=world.config, tier="write")
    try:
        a_ready, b_done, a_second = threading.Event(), threading.Event(), {}

        def thread_a():
            first = server._dispatch_tool("kb_list", {"kb_name": "k"})
            assert "error_code" not in first, first
            a_second["first"] = first
            a_ready.set()
            assert b_done.wait(timeout=20)
            a_second["second"] = server._dispatch_tool("kb_list", {"kb_name": "k"})

        def thread_b():
            assert a_ready.wait(timeout=20)
            created = server._dispatch_tool(
                "kb_create", {"kb_name": "k", "title": "Second", "body": "Body."}
            )
            assert created.get("created"), created
            b_done.set()

        ta, tb = threading.Thread(target=thread_a), threading.Thread(target=thread_b)
        ta.start()
        tb.start()
        ta.join(timeout=40)
        tb.join(timeout=40)

        def count(result):
            return result["knowledge_bases"][0]["entry_count"]

        assert count(a_second["second"]) == count(a_second["first"]) + 1
    finally:
        server.close()


def test_each_call_gets_a_session_of_its_own_even_on_a_reused_thread(world):
    """Worker threads are reused. A call's session (and its identity map) is
    closed with the call, so the next call on that thread starts clean."""
    server = PyriteMCPServer(config=world.config, tier="read")
    seen = []

    def probe(arguments):
        seen.append(server.db.session)
        return {"ok": True}

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    server._register_tool(
        "probe_session", {"description": "p", "inputSchema": schema, "handler": probe}, "read"
    )
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(1) as one_thread:
            for _ in range(2):
                one_thread.submit(server._dispatch_tool, "probe_session", {}).result()
        assert len(seen) == 2 and seen[0] is not seen[1]
    finally:
        server.close()


def test_a_call_bound_to_a_change_set_gets_an_index_worker_on_that_target(world, tmp_path):
    """A bound call's worker indexes the target's database with the target's
    config, never the shared ones (and never the two swapped)."""
    from pyrite.config import PyriteConfig
    from pyrite.storage.database import PyriteDB
    from pyrite.services.write_target import binding

    server = PyriteMCPServer(config=world.config, tier="admin")
    target_config = PyriteConfig(knowledge_bases=list(world.config.knowledge_bases))
    target_db = PyriteDB(tmp_path / "target.db")
    world.track_db(target_db)
    try:
        with binding((target_config, target_db)):
            worker = server._get_index_worker()
        assert worker.db is target_db
        assert worker.config is target_config
        assert server._index_worker is None or server._index_worker is not worker
    finally:
        server.close()


def _twice_on_one_thread(fn):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(1) as one_thread:
        for _ in range(2):
            one_thread.submit(fn).result()


def test_each_prompt_gets_a_session_of_its_own_even_on_a_reused_thread(world):
    """``prompts/get`` reads entries as a tool does, so it gets the same
    per-call session."""
    server = PyriteMCPServer(config=world.config, tier="read")
    seen = []

    def probe(arguments):
        seen.append(server.db.session)
        return {"messages": []}

    server._prompt_research_topic = probe
    try:
        _twice_on_one_thread(lambda: server._get_prompt("research_topic", {"topic": "t"}))
        assert len(seen) == 2 and seen[0] is not seen[1]
    finally:
        server.close()


def test_each_resource_read_gets_a_session_of_its_own_even_on_a_reused_thread(world):
    """The session is opened by ``_read_resource_in_session`` (inside the
    error containment of ``_read_resource``), so the probe stands in for the
    read inside both: ``_read_resource_checked``."""
    server = PyriteMCPServer(config=world.config, tier="read")
    seen = []

    def probe(uri, *, readable_kbs):
        seen.append(server.db.session)
        return {"contents": []}

    server._read_resource_checked = probe
    try:
        _twice_on_one_thread(lambda: server._read_resource("pyrite://kbs"))
        assert len(seen) == 2 and seen[0] is not seen[1]
    finally:
        server.close()


def test_a_calls_kb_checks_and_landing_run_in_the_calls_own_session(world):
    """Everything a tool call reads -- KB resolution, ``require_kb``, the
    landing rule -- runs in the one session the call owns, not in the
    thread's fallback session from an earlier call: ``session_for_call``
    wraps the whole dispatch, not only the handler."""
    server = PyriteMCPServer(config=world.config, tier="write")
    seen: dict[str, object] = {}
    landing = server._landing_target

    def watch_landing(*args, **kwargs):
        seen["landing"] = server.db.session
        return landing(*args, **kwargs)

    def probe(arguments):
        seen["handler"] = server.db.session
        return {"ok": True}

    schema = {
        "type": "object",
        "properties": {"kb_name": {"type": "string"}},
        "additionalProperties": False,
    }
    server._register_tool(
        "probe_write", {"description": "p", "inputSchema": schema, "handler": probe}, "write"
    )
    server._landing_target = watch_landing
    try:
        result = server._dispatch_tool("probe_write", {"kb_name": "k"})
        assert result == {"ok": True}, result
        assert seen["landing"] is seen["handler"]
    finally:
        server.close()
