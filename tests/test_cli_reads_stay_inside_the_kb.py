"""Two operator CLI reports read entry files inside the KB (``KBFiles``):
``pyrite ids missing`` (``id_pin_service.scan``) and ``pyrite schema migrate
--dry-run`` (``SchemaService.migrate_entries``). A file that is a link out of
the KB, or has a second hard link, is reported as not the KB's; nothing it
holds (its id, a parse error quoting its text) reaches the output.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

SECRET = "secretoutsidethekb7d41"
KB = "k"
FORMS = ["symlink", "hardlink"]


def _place(form: str, outside: Path, inside: Path, text: str) -> None:
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(text)
    inside.parent.mkdir(parents=True, exist_ok=True)
    if form == "symlink":
        inside.symlink_to(outside)
    else:
        os.link(outside, inside)


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "own.md").write_text("---\ntitle: Own\ntype: note\n---\nmine\n")
    made = []

    def make():
        w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb, kb_type="generic")])
        made.append(w)
        return w

    yield make, kb, tmp_path / "outside"
    for w in made:
        w.close()


@pytest.mark.parametrize("form", FORMS)
def test_ids_missing_does_not_report_an_outside_files_id(world, form):
    make, kb, outside = world
    _place(form, outside / "o.md", kb / "notes" / "theirs.md", f"---\ntitle: {SECRET}\n---\n")
    w = make()
    r = w.cli("ids", "missing", "-k", KB, "--format", "json")
    assert SECRET not in r.output, r.output
    out = json.loads(r.output)
    assert [row["path"] for row in out["skipped"]] == ["notes/theirs.md"]
    assert "own" in [row["id"] for row in out["missing"]]  # control


@pytest.mark.parametrize("form", FORMS)
def test_schema_migrate_dry_run_does_not_quote_an_outside_file(world, form, caplog):
    make, kb, outside = world
    _place(
        form, outside / "o.md", kb / "notes" / "theirs.md", f"---\n{SECRET}: 1\n{SECRET}: 2\n---\n"
    )
    w = make()
    with caplog.at_level("WARNING"):
        r = w.cli("schema", "migrate", "-k", KB, "--dry-run")
    assert SECRET not in r.output
    assert SECRET not in caplog.text
    assert "Errors: 1" in r.output, r.output
