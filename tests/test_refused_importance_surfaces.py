"""Pin refused importance warnings and QA findings on every requested surface."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.read_cli import app as read_app
from pyrite.server.worktree_resolver import WorktreeResolver
from pyrite.services.kb_service import KBService
from pyrite.services.search_service import SearchService
from pyrite.services.write_target import change_set_target
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from tests._world import build_world
from tests.test_refused_importance_never_silent import _note
from tests.test_worktree_service import _clean_git_env, _init_git_repo

KB = "k"
ENTRY = "n"
REFUSED_VALUES = {"7.5", "high", ".nan"}
VALUES = [
    "7.5",
    "high",
    ".nan",
    pytest.param(
        None,
        marks=pytest.mark.control(reason="negative control: a missing key was not warned"),
    ),
    pytest.param(
        "5.0",
        marks=pytest.mark.control(reason="negative control: an accepted value was not warned"),
    ),
]


def _entry_note(entry_id: str, value: str | None, *, title: str | None = None) -> str:
    """A note with explicit id and optional importance."""
    line = "" if value is None else f"importance: {value}\n"
    heading = title or f"Zebra marker {entry_id}"
    return f"---\nid: {entry_id}\ntype: note\ntitle: {heading}\n{line}---\n\nzebra body.\n"


@pytest.fixture
def make_surface_world(tmp_path):
    worlds = []

    def make(value: str | None):
        world = build_world(
            tmp_path / f"world-{len(worlds)}",
            kbs=[KBConfig(name=KB, path=tmp_path / f"kb-{len(worlds)}")],
            rest=True,
        )
        worlds.append(world)
        world.add_entry(KB, _note(value))
        return world

    yield make
    for world in worlds:
        world.close()


def _warnings(entry: dict) -> list[str]:
    return entry.get("entry_warnings", [])


def _assert_warning(entry: dict, value: str | None) -> None:
    warnings = _warnings(entry)
    if value in REFUSED_VALUES:
        assert len(warnings) == 1 and "whole number 1-10" in warnings[0], entry
    else:
        assert "entry_warnings" not in entry, entry


def _assert_text_warning(text: str, value: str | None, context: object = "") -> None:
    if value in REFUSED_VALUES:
        assert "whole number 1-10" in text and "readers show" in text, (context, text)
    else:
        assert "whole number 1-10" not in text and "Warning:" not in text, (context, text)


@pytest.mark.parametrize("value", VALUES)
def test_get_keeps_importance_warning_on_rest_mcp_and_cli(make_surface_world, value):
    world = make_surface_world(value)

    rest_response = world.client.get(f"/api/entries/{ENTRY}", params={"kb": KB})
    assert rest_response.status_code == 200, rest_response.text
    rest = rest_response.json()
    assert rest["id"] == ENTRY
    _assert_warning(rest, value)

    mcp = world.mcp()._dispatch_tool("kb_get", {"kb_name": KB, "entry_id": ENTRY})
    assert "error_code" not in mcp, mcp
    _assert_warning(mcp["entry"], value)

    cli = world.cli(
        "get",
        ENTRY,
        "-k",
        KB,
        "--fields",
        "importance",
        "--format",
        "json",
    )
    assert cli.exit_code == 0, cli.output
    _assert_warning(json.loads(cli.stdout), value)


@pytest.mark.parametrize("value", VALUES)
def test_single_entry_qa_reports_importance_range_on_rest_and_mcp(make_surface_world, value):
    world = make_surface_world(value)

    rest_response = world.client.get(f"/api/qa/validate/{ENTRY}", params={"kb": KB})
    assert rest_response.status_code == 200, rest_response.text
    rest = rest_response.json()
    mcp = world.mcp()._dispatch_tool("kb_qa_validate", {"kb_name": KB, "entry_id": ENTRY})
    assert "error_code" not in mcp, mcp

    for result in (rest, mcp):
        findings = [issue for issue in result["issues"] if issue["field"] == "importance"]
        if value in {"7.5", "high", ".nan"}:
            assert len(findings) == 1, result
            assert findings[0]["rule"] == "importance_range", result
        else:
            assert findings == [], result


@pytest.mark.parametrize("value", VALUES)
def test_mcp_kb_batch_read_carries_importance_warning(make_surface_world, value):
    world = make_surface_world(value)
    mcp = world.mcp()

    whole = mcp._dispatch_tool("kb_batch_read", {"entries": [{"entry_id": ENTRY, "kb_name": KB}]})
    assert "error_code" not in whole, whole
    assert len(whole["entries"]) == 1, whole
    _assert_warning(whole["entries"][0], value)

    projected = mcp._dispatch_tool(
        "kb_batch_read",
        {"entries": [{"entry_id": ENTRY, "kb_name": KB}], "fields": ["importance"]},
    )
    assert "error_code" not in projected, projected
    assert len(projected["entries"]) == 1, projected
    _assert_warning(projected["entries"][0], value)


@pytest.mark.parametrize("value", VALUES)
def test_mcp_kb_list_entries_and_recent_carry_importance_warning(make_surface_world, value):
    world = make_surface_world(value)
    mcp = world.mcp()

    for tool, base_args in (
        ("kb_list_entries", {"kb_name": KB}),
        ("kb_recent", {"kb_name": KB}),
    ):
        whole = mcp._dispatch_tool(tool, base_args)
        assert "error_code" not in whole, (tool, whole)
        assert len(whole["entries"]) == 1, (tool, whole)
        _assert_warning(whole["entries"][0], value)

        projected = mcp._dispatch_tool(tool, {**base_args, "fields": ["title"]})
        assert "error_code" not in projected, (tool, projected)
        assert len(projected["entries"]) == 1, (tool, projected)
        _assert_warning(projected["entries"][0], value)


@pytest.mark.parametrize("value", VALUES)
def test_rest_list_and_batch_entries_carry_importance_warning(make_surface_world, value):
    world = make_surface_world(value)

    list_resp = world.client.get("/api/entries", params={"kb": KB})
    assert list_resp.status_code == 200, list_resp.text
    list_body = list_resp.json()
    assert len(list_body["entries"]) == 1, list_body
    _assert_warning(list_body["entries"][0], value)

    list_md = world.client.get(
        "/api/entries", params={"kb": KB}, headers={"Accept": "text/markdown"}
    )
    assert list_md.status_code == 200, list_md.text
    _assert_text_warning(list_md.text, value, "GET /api/entries Accept: text/markdown")

    batch_resp = world.client.post(
        "/api/entries/batch",
        json={"entries": [{"entry_id": ENTRY, "kb_name": KB}]},
    )
    assert batch_resp.status_code == 200, batch_resp.text
    _assert_warning(batch_resp.json()["entries"][0], value)

    batch_proj = world.client.post(
        "/api/entries/batch",
        json={"entries": [{"entry_id": ENTRY, "kb_name": KB}], "fields": ["importance"]},
    )
    assert batch_proj.status_code == 200, batch_proj.text
    _assert_warning(batch_proj.json()["entries"][0], value)


@pytest.mark.parametrize("value", VALUES)
def test_cli_get_rich_and_markdown_print_importance_warning(make_surface_world, value):
    world = make_surface_world(value)

    for fmt in ("rich", "markdown"):
        whole = world.cli("get", ENTRY, "-k", KB, "--format", fmt)
        assert whole.exit_code == 0, whole.output
        _assert_text_warning(whole.stdout, value, ("get", fmt))

        projected = world.cli("get", ENTRY, "-k", KB, "--fields", "importance", "--format", fmt)
        assert projected.exit_code == 0, projected.output
        _assert_text_warning(projected.stdout, value, ("get --fields", fmt))

        read_only = world.cli("get", ENTRY, "-k", KB, "--format", fmt, app=read_app)
        assert read_only.exit_code == 0, read_only.output
        _assert_text_warning(read_only.stdout, value, ("pyrite-read get", fmt))


@pytest.mark.parametrize("value", VALUES)
def test_cli_search_rich_and_markdown_print_importance_warning(make_surface_world, value):
    world = make_surface_world(value)

    for fmt in ("rich", "markdown"):
        whole = world.cli("search", "zebra", "-k", KB, "--format", fmt)
        assert whole.exit_code == 0, whole.output
        _assert_text_warning(whole.stdout, value, ("search", fmt))

        projected = world.cli("search", "zebra", "-k", KB, "--fields", "title", "--format", fmt)
        assert projected.exit_code == 0, projected.output
        _assert_text_warning(projected.stdout, value, ("search --fields", fmt))


@pytest.mark.parametrize("value", VALUES)
def test_cli_list_entries_and_batch_read_carry_importance_warning(make_surface_world, value):
    world = make_surface_world(value)

    for base_args in (("list-entries", "-k", KB), ("batch-read", f"{KB}:{ENTRY}")):
        whole_json = world.cli(*base_args, "--format", "json")
        assert whole_json.exit_code == 0, whole_json.output
        _assert_warning(json.loads(whole_json.stdout)["entries"][0], value)

        proj_json = world.cli(*base_args, "--fields", "title", "--format", "json")
        assert proj_json.exit_code == 0, proj_json.output
        _assert_warning(json.loads(proj_json.stdout)["entries"][0], value)

        for fmt in ("rich", "markdown"):
            whole = world.cli(*base_args, "--format", fmt)
            assert whole.exit_code == 0, whole.output
            _assert_text_warning(whole.stdout, value, (base_args, fmt))

            proj = world.cli(*base_args, "--fields", "title", "--format", fmt)
            assert proj.exit_code == 0, proj.output
            _assert_text_warning(proj.stdout, value, (base_args, "--fields", fmt))

        csv_out = world.cli(*base_args, "--format", "csv")
        assert csv_out.exit_code == 0, csv_out.output
        if value in REFUSED_VALUES:
            assert "whole number 1-10" in csv_out.stderr, (base_args, csv_out.stderr)
        else:
            assert "whole number 1-10" not in csv_out.stderr, (base_args, csv_out.stderr)


def test_multi_row_lists_flag_only_the_refused_entry(tmp_path):
    """In a multi-row list, batch-read, or search, only the refused entry warns."""
    world = build_world(
        tmp_path / "world", kbs=[KBConfig(name=KB, path=tmp_path / "kb")], rest=True
    )
    try:
        world.add_entry(KB, _entry_note("bad", "7.5"))
        world.add_entry(KB, _entry_note("good", "5.0"))
        world.add_entry(KB, _entry_note("plain", None))

        mcp = world.mcp()
        mcp_list = mcp._dispatch_tool("kb_list_entries", {"kb_name": KB})
        mcp_batch = mcp._dispatch_tool(
            "kb_batch_read",
            {
                "entries": [
                    {"entry_id": "bad", "kb_name": KB},
                    {"entry_id": "good", "kb_name": KB},
                    {"entry_id": "plain", "kb_name": KB},
                ]
            },
        )
        rest_list = world.client.get("/api/entries", params={"kb": KB}).json()
        rest_batch = world.client.post(
            "/api/entries/batch",
            json={
                "entries": [
                    {"entry_id": "bad", "kb_name": KB},
                    {"entry_id": "good", "kb_name": KB},
                    {"entry_id": "plain", "kb_name": KB},
                ]
            },
        ).json()
        cli_list = json.loads(world.cli("list-entries", "-k", KB, "--format", "json").stdout)
        cli_batch = json.loads(
            world.cli(
                "batch-read", f"{KB}:bad", f"{KB}:good", f"{KB}:plain", "--format", "json"
            ).stdout
        )

        for label, payload in (
            ("mcp_list", mcp_list),
            ("mcp_batch", mcp_batch),
            ("rest_list", rest_list),
            ("rest_batch", rest_batch),
            ("cli_list", cli_list),
            ("cli_batch", cli_batch),
        ):
            by_id = {row["id"]: row for row in payload["entries"]}
            assert set(by_id) == {"bad", "good", "plain"}, label
            _assert_warning(by_id["bad"], "7.5")
            _assert_warning(by_id["good"], "5.0")
            _assert_warning(by_id["plain"], None)

        for fmt in ("rich", "markdown"):
            search_out = world.cli("search", "zebra", "-k", KB, "--format", fmt)
            assert search_out.exit_code == 0, search_out.output
            assert search_out.stdout.count("whole number 1-10") == 1, (fmt, search_out.stdout)
            assert "7.5" in search_out.stdout, (fmt, search_out.stdout)
    finally:
        world.close()


def test_overlay_row_warns_through_owning_kb_files_and_symlink_outside_root_stays_silent(tmp_path):
    """A worktree overlay row warns on search, list, and batch-read just like get,
    reading through the owning KB's KBFiles; a symlink escaping the overlay root
    stays refused and silent."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    kb_path = repo / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / f"{ENTRY}.md").write_text(_note("5"))
    for cmd in (["git", "add", "."], ["git", "commit", "-qm", "seed"]):
        subprocess.run(
            cmd, cwd=repo, env=_clean_git_env(), check=True, capture_output=True, timeout=120
        )

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    cache: dict = {}
    ann = {"id": 7, "username": "ann"}
    bob = {"id": 8, "username": "bob"}
    try:
        IndexManager(db, config).index_kb(KB)
        resolver = WorktreeResolver(config, db, cache)
        wt_cfg, wt_db = change_set_target(config, db, 7, KB, diff_cache=cache, username="ann")
        KBService(wt_cfg, wt_db).update(ENTRY, KB, {"body": "zebra overlay edit."})

        ann_get = resolver.get_read_service(KB, ann).get_entry(ENTRY, KB, readable_kbs=None)
        assert ann_get is not None
        ann_file = Path(ann_get["file_path"])
        assert kb_path not in ann_file.parents, "overlay file must live outside main KB root"

        # Hand-edit Ann's overlay file to hold a refused importance.
        ann_file.write_text(_note("7.5"))

        ann_read_db = resolver.get_read_db(KB, ann)
        ann_svc = resolver.get_read_service(KB, ann)

        get_res = ann_svc.get_entry(ENTRY, KB, readable_kbs=None)
        search_res = SearchService(ann_read_db, settings=config.settings).search_response(
            "zebra", kb_name=KB, mode="keyword"
        )
        list_res = ann_svc.list_entries(kb_name=KB)
        batch_res = ann_svc.get_entries([(ENTRY, KB)])

        assert get_res is not None
        expected = get_res["entry_warnings"]
        assert expected and "whole number 1-10" in expected[0] and "7.5" in expected[0]
        assert search_res["results"][0].get("entry_warnings") == expected
        assert list_res[0].get("entry_warnings") == expected
        assert batch_res[0].get("entry_warnings") == expected

        # Bob has no worktree and sees main's valid importance: 5 -> silent.
        bob_read_db = resolver.get_read_db(KB, bob)
        bob_search = SearchService(bob_read_db, settings=config.settings).search_response(
            "zebra", kb_name=KB, mode="keyword"
        )
        assert "entry_warnings" not in bob_search["results"][0]

        # Containment preserved: a symlink inside the overlay pointing outside
        # the overlay root is refused by KBFiles and stays silent.
        outside = tmp_path / "outside.md"
        outside.write_text(_note("7.5"))
        ann_file.unlink()
        ann_file.symlink_to(outside)

        escaped_search = SearchService(ann_read_db, settings=config.settings).search_response(
            "zebra", kb_name=KB, mode="keyword"
        )
        assert "entry_warnings" not in escaped_search["results"][0]
        assert "entry_warnings" not in ann_svc.list_entries(kb_name=KB)[0]
        assert "entry_warnings" not in ann_svc.get_entries([(ENTRY, KB)])[0]
    finally:
        for d in cache.values():
            d.close()
        db.close()


def test_rich_prints_refused_importance_with_markup_brackets_literally(make_surface_world):
    """A refused importance containing Rich markup brackets prints literally without tag stripping."""
    world = make_surface_world('"[red]high[/red]"')

    for args in (
        ("get", ENTRY, "-k", KB, "--format", "rich"),
        ("search", "zebra", "-k", KB, "--format", "rich"),
    ):
        res = world.cli(*args)
        assert res.exit_code == 0, res.output
        assert "whole number 1-10" in res.stdout, (args, res.stdout)
        assert "[red]high[/red]" in res.stdout, (args, res.stdout)


@pytest.mark.control(
    reason="security containment guard: empty/blank roots and sibling prefix paths must never be read"
)
def test_empty_root_and_sibling_prefix_paths_are_rejected(tmp_path, monkeypatch):
    """Empty/blank roots must never resolve to CWD, and `/kb` must not match `/kb2`."""
    from pyrite.services.importance_reading import row_importance_warning

    class _FakeDB:
        kb_roots = None

        def __init__(self, path_value: str | None):
            self._path = path_value

        def execute_sql(self, _sql: str, _params: dict):
            return [{"path": self._path}] if self._path is not None else []

    cwd_dir = tmp_path / "cwd"
    cwd_dir.mkdir()
    cwd_entry = cwd_dir / "leak.md"
    cwd_entry.write_text(_note("7.5"))
    monkeypatch.chdir(cwd_dir)

    # Empty or whitespace-only roots (from DB or pre-populated cache) must not read CWD.
    assert row_importance_warning(_FakeDB(""), KB, cwd_entry) is None
    assert row_importance_warning(_FakeDB("   "), KB, cwd_entry) is None
    assert row_importance_warning(_FakeDB(None), KB, cwd_entry, roots={KB: ""}) is None
    assert row_importance_warning(_FakeDB(None), KB, cwd_entry, roots={KB: ("", "  ")}) is None

    # Sibling prefix `/kb2/n.md` must not be treated as inside `/kb`.
    kb1 = tmp_path / "kb"
    kb2 = tmp_path / "kb2"
    kb1.mkdir()
    kb2.mkdir()
    sibling_entry = kb2 / "n.md"
    sibling_entry.write_text(_note("7.5"))
    assert row_importance_warning(_FakeDB(str(kb1)), KB, sibling_entry) is None
