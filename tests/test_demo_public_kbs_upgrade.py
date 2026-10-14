"""Already-seeded demos need a one-time `default_role: read` on their KBs.

seed.sh runs once, so a demo seeded before /site became public-KB-only
keeps a config.yaml without `default_role`, and its /site goes empty on
upgrade. deploy/demo/public-kbs.py (run by update.sh inside the container)
lists those KBs and, with --apply, marks them public.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pyrite.utils.yaml import dump_yaml_file, load_yaml_file

SCRIPT = Path(__file__).resolve().parent.parent / "deploy/demo/public-kbs.py"
UPDATE = Path(__file__).resolve().parent.parent / "deploy/demo/update.sh"


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    dump_yaml_file(
        {
            "knowledge_bases": [
                {"name": "old-a", "path": str(tmp_path / "a"), "kb_type": "generic"},
                {
                    "name": "kept-private",
                    "path": str(tmp_path / "b"),
                    "kb_type": "generic",
                    "default_role": "none",
                },
                {
                    "name": "already",
                    "path": str(tmp_path / "c"),
                    "kb_type": "generic",
                    "default_role": "read",
                },
            ],
            "settings": {"index_path": str(tmp_path / "index.db")},
        },
        path,
    )
    return path


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )


def test_check_lists_kbs_without_default_role_and_changes_nothing(tmp_path):
    cfg = _config(tmp_path)
    before = cfg.read_text()
    out = _run(str(cfg)).stdout
    assert "old-a" in out
    assert "kept-private" not in out and "already" not in out
    assert "--apply" in out
    assert cfg.read_text() == before


def test_apply_marks_only_unset_kbs_public(tmp_path):
    cfg = _config(tmp_path)
    _run(str(cfg), "--apply")
    roles = {kb["name"]: kb.get("default_role") for kb in load_yaml_file(cfg)["knowledge_bases"]}
    assert roles == {"old-a": "read", "kept-private": "none", "already": "read"}
    assert "old-a" not in _run(str(cfg)).stdout


def test_update_sh_runs_the_check():
    assert "public-kbs.py" in UPDATE.read_text()
