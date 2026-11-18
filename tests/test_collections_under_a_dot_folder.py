"""A KB stored under a dot-folder lists all of its collections (#1575).

Only a dot-prefixed part *inside* the KB (relative to the KB root) hides a
file. Where the KB itself is stored (e.g. ~/.kbs/notes, or under a dot-folder
parent) makes no difference to what it lists, counts, or validates.
"""

from __future__ import annotations

import json
from pathlib import Path

from pyrite.config import KBConfig
from pyrite.storage.repository import KBRepository


def _populate_kb(root: Path) -> Path:
    """Build a KB with one note, one collection, one hidden collection and note,
    and one collection and note under _templates."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "kb.yaml").write_text("name: test\n")
    (root / "note.md").write_text("---\nid: note-1\ntitle: Note 1\ntype: note\n---\nHello.\n")

    # Regular collection
    (root / "topic").mkdir(parents=True, exist_ok=True)
    (root / "topic" / "__collection.yaml").write_text("title: Topic\n")

    # Hidden folder: should be skipped in all locations
    (root / ".drafts").mkdir(parents=True, exist_ok=True)
    (root / ".drafts" / "__collection.yaml").write_text("title: Drafts\n")
    (root / ".drafts" / "secret.md").write_text(
        "---\nid: secret\ntitle: Secret\ntype: note\n---\nSecret.\n"
    )

    # Templates folder (any case): should be skipped in all locations
    (root / "_templates").mkdir(parents=True, exist_ok=True)
    (root / "_templates" / "__collection.yaml").write_text("title: Template Collection\n")
    (root / "_templates" / "tmpl.md").write_text(
        "---\nid: tmpl\ntitle: Template\ntype: note\n---\nTemplate.\n"
    )

    return root


def test_repository_methods_parity(tmp_path):
    """list_collection_files, list_entries, and count give identical answers
    regardless of whether the KB is stored under a dot-folder parent or has a
    dot-prefixed name."""
    plain_root = _populate_kb(tmp_path / "plain" / "kb")
    dot_parent_root = _populate_kb(tmp_path / ".hidden" / "kb")
    dot_kb_root = _populate_kb(tmp_path / "kbs" / ".notes")

    for root in [plain_root, dot_parent_root, dot_kb_root]:
        repo = KBRepository(KBConfig(name="k", path=root))

        # 1. list_collection_files: only topic/__collection.yaml is listed
        colls = list(repo.list_collection_files())
        assert len(colls) == 1, f"Expected 1 collection in {root}, got {colls}"
        assert colls[0].parent.name == "topic"

        # 2. list_entries: 1 note + 1 collection = 2 entries
        entries = list(repo.list_entries())
        assert len(entries) == 2, f"Expected 2 entries in {root}, got {entries}"
        entry_ids = {e[0].id for e in entries}
        assert entry_ids == {"note-1", "collection-topic"}

        # 3. count: 1 md + 1 yaml = 2
        assert repo.count() == 2


def test_cli_surfaces_parity(tmp_path, cli_world):
    """CLI surfaces (collections list, kb health, kb validate) give parity
    across plain, dot-parent, and dot-kb roots."""
    plain_root = _populate_kb(tmp_path / "plain" / "kb")
    dot_parent_root = _populate_kb(tmp_path / ".hidden" / "kb")
    dot_kb_root = _populate_kb(tmp_path / "kbs" / ".notes")

    kbs = [
        KBConfig(name="plain", path=plain_root),
        KBConfig(name="dotparent", path=dot_parent_root),
        KBConfig(name="dotkb", path=dot_kb_root),
    ]
    world = cli_world(kbs=kbs)
    world.index()

    for kb_name in ["plain", "dotparent", "dotkb"]:
        # collections list
        res_colls = world.cli("collections", "list", "-k", kb_name, "--format", "json")
        assert res_colls.exit_code == 0, res_colls.output
        data = json.loads(res_colls.output)
        colls = data.get("collections", [])
        assert len(colls) == 1, f"Expected 1 collection in {kb_name}, got {colls}"
        assert colls[0]["id"] == "collection-topic"

        # kb health: reports file_count 1 (the single regular note, ignoring .drafts and _templates)
        res_health = world.cli("kb", "health", "-k", kb_name, "--format", "json")
        assert res_health.exit_code == 0, res_health.output
        health_data = json.loads(res_health.output)
        assert health_data.get("file_count") == 1, (
            f"Expected file_count 1 for {kb_name}, got {health_data}"
        )

        # kb validate
        res_val = world.cli("kb", "validate", kb_name, "--format", "rich")
        assert res_val.exit_code == 0, res_val.output
        assert "1 markdown files found" in res_val.output, (
            f"Missing markdown count for {kb_name}: {res_val.output}"
        )


def test_ids_pin_dry_run_reports_collection_collision_under_dot_parent(tmp_path, cli_world):
    """ids pin --dry-run sees collection ids and detects collisions even when
    the KB is stored under a dot-folder parent."""
    dot_parent_root = _populate_kb(tmp_path / ".hidden" / "kb")
    # Plant a second file that implicitly holds the collection's id: collection-topic.md with no id
    collision_file = dot_parent_root / "collection-topic.md"
    collision_file.write_text("---\ntitle: Collection Topic\ntype: note\n---\nBody.\n")

    kbs = [KBConfig(name="dotparent", path=dot_parent_root)]
    world = cli_world(kbs=kbs)

    from pyrite.services import id_pin_service

    scan_res = id_pin_service.scan(KBConfig(name="dotparent", path=dot_parent_root))
    assert any(c["id"] == "collection-topic" for c in scan_res.collisions)

    res = world.cli("ids", "pin", "-k", "dotparent", "--dry-run", "--format", "json")
    # Exit code 3 represents refused items due to collision
    assert res.exit_code in (0, 3), res.output
    data = json.loads(res.output)
    refused = data.get("refused", [])
    assert any(r["id"] == "collection-topic" for r in refused), (
        f"Expected collision for collection-topic: {data}"
    )


def test_no_test_of_absolute_path_parts_in_pyrite():
    """Grep-level invariant: pyrite/ contains no test of path parts with startswith('.')
    except for relative parts inside entry_paths.py."""
    pyrite_root = Path(__file__).resolve().parents[1] / "pyrite"
    violations = []
    for py_file in pyrite_root.rglob("*.py"):
        rel = py_file.relative_to(pyrite_root).as_posix()
        for idx, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), 1):
            if 'startswith(".")' in line or "startswith('.')" in line:
                if ".parts" in line:
                    violations.append(f"{rel}:{idx}: {line.strip()}")

    assert not violations, f"Found test of path parts with startswith('.'): {violations}"
