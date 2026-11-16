"""Registering a KB path over the network is limited to folders under the
configured ``settings.kb_roots``; the local operator registers any path.

Network: a REST request (``POST /api/kbs``, admin tier) and an MCP call made
with a principal that is not the local process (``kb_registry_add``). Local:
the CLI (``pyrite kb add``) and stdio MCP under it. A refused path answers
``KB_PATH_NOT_ALLOWED`` naming the allowed roots, and nothing is created: no
folder, no registry row. Paths are compared fully resolved, so a symbolic
link or ``..`` cannot leave a root. With no roots configured (the default),
no network caller may register a path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.services.access_policy import Principal
from tests._world import build_world


@pytest.fixture
def world(tmp_path):
    made = []
    roots = tmp_path / "roots"
    roots.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    def make(kb_roots: list[Path] | None):
        w = build_world(
            tmp_path / "world",
            kbs=[KBConfig(name="seed", path=tmp_path / "seed", kb_type="generic")],
        )
        w.config.settings.kb_roots = list(kb_roots or [])
        made.append(w)
        return w

    yield make, roots.resolve(), outside.resolve()
    for w in made:
        w.close()


def _rest_add(w, name: str, path: Path | str):
    return w.client.post("/api/kbs", json={"name": name, "path": str(path)})


def _registered(w, name: str) -> bool:
    return bool(w.db.execute_sql("SELECT name FROM kb WHERE name = :n", {"n": name}))


# -- the local operator ----------------------------------------------------------


@pytest.mark.control(
    reason="the local operator was never limited; pinned so the rule does not reach it"
)
def test_the_operator_at_the_cli_registers_any_path(world):
    make, roots, outside = world
    w = make([roots])
    target = outside / "mine"
    target.mkdir()
    r = w.cli("kb", "add", str(target), "--name", "mine")
    assert r.exit_code == 0, r.output
    assert w.db.execute_sql("SELECT name FROM kb WHERE name = 'mine'"), r.output


@pytest.mark.control(reason="stdio MCP is the local process; pinned so the rule does not reach it")
def test_stdio_mcp_as_the_local_principal_registers_any_path(world):
    make, roots, outside = world
    w = make([])
    server = w.mcp("admin")
    r = server._dispatch_tool(
        "kb_registry_add",
        {"name": "mine", "path": str(outside / "mine")},
        client_kind="local",
        principal=Principal.local(),
    )
    assert r.get("created") is True, r


# -- network admins ------------------------------------------------------------------


@pytest.mark.control(reason="inside a root stays allowed; pinned beside the refusals")
def test_a_network_admin_registers_a_path_under_a_root(world):
    make, roots, _outside = world
    w = make([roots])
    r = _rest_add(w, "inside", roots / "inside")
    assert r.status_code == 200, r.text
    assert (roots / "inside").is_dir()


@pytest.mark.parametrize(
    "how", ["outside", "symlink", "dotdot", "the-root-itself"], ids=lambda h: h
)
def test_a_network_admin_is_refused_outside_every_root(world, how):
    make, roots, outside = world
    w = make([roots])
    if how == "outside":
        path = outside / "theirs"
    elif how == "symlink":
        (roots / "link").symlink_to(outside, target_is_directory=True)
        path = roots / "link" / "theirs"
    elif how == "dotdot":
        path = f"{roots}/../elsewhere/theirs"
    else:
        path = roots
    r = _rest_add(w, "theirs", path)
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "KB_PATH_NOT_ALLOWED"
    assert str(roots) in detail["message"]
    assert not (outside / "theirs").exists()
    assert not _registered(w, "theirs")


def test_with_no_roots_a_network_admin_registers_no_path(world):
    make, roots, _outside = world
    w = make([])
    r = _rest_add(w, "inside", roots / "inside")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "KB_PATH_NOT_ALLOWED"
    assert "kb_roots" in r.json()["detail"]["message"]
    assert not (roots / "inside").exists()


@pytest.mark.parametrize(
    "principal",
    [Principal.from_api_key("admin"), Principal.user(1, "admin")],
    ids=["operator-key", "admin-user"],
)
def test_mcp_over_the_network_is_held_to_the_roots(world, principal):
    """An operator API key over the network is still the network."""
    make, roots, outside = world
    w = make([roots])
    server = w.mcp("admin")
    r = server._dispatch_tool(
        "kb_registry_add",
        {"name": "theirs", "path": str(outside / "theirs")},
        client_kind=principal.kind,
        client_id="c",
        principal=principal,
    )
    assert r.get("error_code") == "KB_PATH_NOT_ALLOWED", r
    assert not (outside / "theirs").exists()
    ok = server._dispatch_tool(
        "kb_registry_add",
        {"name": "inside", "path": str(roots / "inside")},
        client_kind=principal.kind,
        client_id="c",
        principal=principal,
    )
    assert ok.get("created") is True, ok


def test_kb_roots_round_trips_through_the_config_file(tmp_path):
    from pyrite.config import PyriteConfig, Settings

    root = tmp_path / "r"
    root.mkdir()
    cfg = PyriteConfig(knowledge_bases=[], settings=Settings(kb_roots=[str(root)]))
    again = PyriteConfig.from_dict(cfg.to_dict())
    assert again.settings.kb_roots == [root.resolve()]
    assert PyriteConfig.from_dict(PyriteConfig().to_dict()).settings.kb_roots == []


def test_a_relative_root_is_refused_when_the_config_loads():
    from pyrite.config import Settings

    with pytest.raises(ValueError, match="kb_roots"):
        Settings(kb_roots=["relative/path"])
