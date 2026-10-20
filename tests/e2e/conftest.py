"""Fixtures for the smoke layer: a real `pyrite-server` process on a free port.

Everything here deliberately avoids importing pyrite in-process for the thing
under test. The point of this layer is that the *assembled* artifact works:
the console scripts a user actually runs (`pyrite-server`, `pyrite`), resolved
through PATH from an installed distribution, talking over a socket and over
stdio. An in-process TestClient could not have caught any of PRs #3/#4/#5.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("httpx", reason="the smoke layer drives the server over HTTP")
import httpx  # noqa: E402

# Generous because CI runners are slow and the first request imports the whole
# FastAPI app; a hang here should still fail rather than sit forever.
STARTUP_TIMEOUT = 90.0


def free_port() -> int:
    """Claim a port from the ephemeral range and release it immediately.

    Racy in principle (another process could take it between close and bind),
    fine in practice and the standard trick: the alternative is a fixed port,
    which is *guaranteed* to collide under `-n auto`.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def console_script(name: str) -> str:
    """Resolve an installed console script, preferring this interpreter's venv.

    `shutil.which` alone finds whatever is first on PATH, which in a worktree
    with several venvs is not necessarily the one running the tests. Looking
    next to sys.executable first keeps the smoke layer testing the install the
    suite was launched from.
    """
    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return str(candidate)
    found = shutil.which(name)
    if not found:
        pytest.skip(f"{name!r} console script not installed; smoke layer needs the package")
    return found


def smoke_env(data_dir: Path, **extra: str) -> dict[str, str]:
    """A clean environment for a pyrite subprocess under test.

    PYRITE_DATA_DIR/PYRITE_CONFIG_DIR are read at *import* time
    (pyrite.config.CONFIG_DIR is a module-level constant), so they can only be
    set for a subprocess -- which is exactly what this layer does.
    """
    env = dict(os.environ)
    env.pop("PYRITE_API_KEY", None)
    env.update(
        {
            "PYRITE_DATA_DIR": str(data_dir),
            "PYRITE_CONFIG_DIR": str(data_dir),
            # No write-time embedding: it would load a ~90 MB model on every
            # create. The prewarm test opts back in explicitly.
            "PYRITE_AUTO_EMBED": "0",
            # Never reach the network for a model in CI.
            "HF_HUB_OFFLINE": "1",
            "PYRITE_AUTH_ENABLED": "false",
        }
    )
    env.update(extra)
    return env


@dataclass
class LiveServer:
    """A running `pyrite-server` process plus a client pointed at it."""

    base_url: str
    data_dir: Path
    proc: subprocess.Popen
    client: httpx.Client

    def output(self) -> str:
        """Everything the server wrote, for a failure message."""
        log = self.data_dir / "server.log"
        return log.read_text(errors="replace") if log.exists() else "<no output captured>"


def _write_config(data_dir: Path, kbs: list[dict], settings: dict | None = None) -> None:
    import yaml

    merged = {"auto_embed": False}
    merged.update(settings or {})
    (data_dir / "config.yaml").write_text(
        yaml.safe_dump({"knowledge_bases": kbs, "settings": merged})
    )


def start_server(
    data_dir: Path,
    *,
    kbs: list[dict] | None = None,
    env_extra: dict[str, str] | None = None,
    settings: dict | None = None,
) -> LiveServer:
    """Start pyrite-server on a free port against `data_dir` and wait for /health."""
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_config(data_dir, kbs or [], settings)

    port = free_port()
    env = smoke_env(data_dir, PYRITE_HOST="127.0.0.1", PYRITE_PORT=str(port), **(env_extra or {}))

    log_path = data_dir / "server.log"
    log = log_path.open("w")
    proc = subprocess.Popen(
        [console_script("pyrite-server")],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=str(data_dir),
    )

    base_url = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base_url, timeout=30.0)
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.close()
            raise RuntimeError(
                f"pyrite-server exited with {proc.returncode} before serving:\n"
                f"{log_path.read_text(errors='replace')}"
            )
        try:
            if client.get("/health").status_code == 200:
                return LiveServer(base_url=base_url, data_dir=data_dir, proc=proc, client=client)
        except httpx.TransportError:
            pass
        time.sleep(0.2)

    proc.terminate()
    log.close()
    raise RuntimeError(
        f"pyrite-server did not answer /health within {STARTUP_TIMEOUT}s:\n"
        f"{log_path.read_text(errors='replace')}"
    )


def stop_server(server: LiveServer) -> None:
    server.client.close()
    server.proc.terminate()
    try:
        server.proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        server.proc.kill()
        server.proc.wait(timeout=10)


def seed_kb(kb_dir: Path, name: str, *, title: str, body: str) -> None:
    """Write a minimal KB on disk: kb.yaml plus one note entry."""
    import yaml

    kb_dir.mkdir(parents=True, exist_ok=True)
    (kb_dir / "kb.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"smoke KB {name}",
                "entry_types": {"note": {"fields": {}}},
            }
        )
    )
    slug = title.lower().replace(" ", "-")
    (kb_dir / f"{slug}.md").write_text(
        f"---\nid: {slug}\ntitle: {title}\ntype: note\ntags: [smoke]\n---\n\n{body}\n"
    )


@pytest.fixture(scope="module")
def seeded_data_dir(tmp_path_factory) -> Path:
    """A temp data dir holding one KB (`smoke`) with one findable entry."""
    data_dir = tmp_path_factory.mktemp("pyrite-smoke-data")
    seed_kb(
        data_dir / "smoke-kb",
        "smoke",
        title="Analytical Engine",
        body="Charles Babbage designed a mechanical general-purpose computer.",
    )
    return data_dir


@pytest.fixture(scope="module")
def seeded_kbs(seeded_data_dir) -> list[dict]:
    return [
        {
            "name": "smoke",
            "path": str(seeded_data_dir / "smoke-kb"),
            "kb_type": "generic",
            "description": "smoke KB",
        }
    ]


@pytest.fixture(scope="module")
def live_server(seeded_data_dir, seeded_kbs):
    """A `pyrite-server` process serving the seeded KB, torn down after."""
    server = start_server(seeded_data_dir, kbs=seeded_kbs)
    # wait=true: without it the endpoint queues a background job and returns
    # immediately, so a search right after would race the indexer.
    resp = server.client.post("/api/index/sync?wait=true")
    assert resp.status_code == 200, (resp.status_code, resp.text)
    try:
        yield server
    finally:
        stop_server(server)


READ_API_KEY = "smoke-read-key"


@pytest.fixture(scope="module")
def read_tier_server(tmp_path_factory):
    """A server whose only credential is a *read*-role API key.

    Needed so the SSE tool list is comparable with `pyrite mcp --tier read`:
    with no credentials configured at all, `_resolve_bearer_auth` hands out
    `admin`, and admin's tool list is a strict superset of read's.
    """
    import hashlib

    data_dir = tmp_path_factory.mktemp("pyrite-smoke-read")
    kb_dir = data_dir / "smoke-kb"
    seed_kb(
        kb_dir,
        "smoke",
        title="Analytical Engine",
        body="Charles Babbage designed a mechanical general-purpose computer.",
    )
    kbs = [{"name": "smoke", "path": str(kb_dir), "kb_type": "generic", "description": "smoke"}]
    settings = {
        "api_keys": [
            {
                "key_hash": hashlib.sha256(READ_API_KEY.encode()).hexdigest(),
                "role": "read",
                "label": "smoke",
            }
        ]
    }
    # Index before the server starts: the only credential this server has is a
    # read key, which deliberately cannot trigger a sync.
    _write_config(data_dir, kbs, settings)
    index_kb(data_dir)

    server = start_server(data_dir, kbs=kbs, settings=settings)
    server.client.headers["Authorization"] = f"Bearer {READ_API_KEY}"
    try:
        yield server
    finally:
        stop_server(server)


def index_kb(data_dir: Path, kb_dir: Path | None = None) -> None:
    """Run `pyrite index sync` against a data dir, out of process."""
    subprocess.run(
        [console_script("pyrite"), "index", "sync"],
        env=smoke_env(data_dir),
        cwd=str(data_dir),
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="module")
def sse_session_result(read_tier_server) -> dict:
    """One complete read-tier MCP session over SSE; its findings.

    Lives in conftest, not in test_mcp_sse.py, because the stdio module
    compares its tool list against this one -- the two transports must not
    drift, and asserting that is the point of having both.

    Module-scoped because an MCP session is stateful and the handshake is the
    expensive part: every assertion reads from this one session.
    """
    from .sse_client import SSESession, handshake

    with SSESession(read_tier_server.base_url, api_key=READ_API_KEY) as session:
        endpoint = session.endpoint_path

        # The endpoint's shape is checked HERE, before the handshake, so that a
        # doubled prefix (PR #3) reports as itself. Left to the handshake, the
        # same bug surfaces as `initialize -> 404` in fixture setup, which
        # errors out every test in this module and names none of them.
        ping_status = session.post({"jsonrpc": "2.0", "id": 0, "method": "ping"}).status_code
        if ping_status == 404:
            return {
                "endpoint": endpoint,
                "ping_status": ping_status,
                "server_info": {},
                "tool_names": set(),
                "search_text": "",
                "unreachable": True,
            }

        info = handshake(session)

        listed = session.request(2, "tools/list")
        assert "result" in listed, listed
        names = {tool["name"] for tool in listed["result"]["tools"]}

        called = session.request(
            3,
            "tools/call",
            {"name": "kb_search", "arguments": {"query": "Babbage", "kb_name": "smoke"}},
        )
        assert "result" in called, called
        text = "".join(block.get("text", "") for block in called["result"].get("content", []))

    return {
        "endpoint": endpoint,
        "server_info": info,
        "tool_names": names,
        "search_text": text,
        "ping_status": ping_status,
        "unreachable": False,
    }
