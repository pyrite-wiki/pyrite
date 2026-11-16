"""Export never reads a file through a link that leaves the KB.

Every file an export reads from a KB is read the way `KBFiles` writes: only
where it resolves inside the KB's root. A link inside the KB that points out
of it is refused (``OUTSIDE_KB_ROOT``), and what it points at reaches no
export. The reads:

- ``ExportService.export_kb_to_directory`` (behind REST export to a repo)
  copies ``kb.yaml``;
- ``pyrite export collection`` and ``pyrite export site`` read each entry's
  file, by the path the index holds and, for a folder collection, by walking
  the collection's folder.

The exports that write from the index are held by the indexer reading the
same way: tests/test_indexed_bytes_stay_inside_the_kb.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyrite.config import KBConfig
from pyrite.services.export_service import ExportService
from pyrite.utils.kb_files import KBFileOutsideRootError, KBFiles
from tests._world import build_world

SECRET = "secret-outside-the-kb-7f3a"


def _outside(tmp_path: Path, name: str, text: str) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    target = outside / name
    target.write_text(text)
    return target


def _texts(root: Path) -> str:
    return "\n".join(p.read_text(errors="replace") for p in root.rglob("*") if p.is_file())


class TestKBFilesRead:
    def test_a_file_inside_is_read(self, tmp_path):
        (tmp_path / "kb").mkdir()
        (tmp_path / "kb" / "a.md").write_text("inside")
        assert KBFiles(tmp_path / "kb").read_bytes(tmp_path / "kb" / "a.md") == b"inside"

    def test_a_link_out_of_the_kb_is_refused(self, tmp_path):
        (tmp_path / "kb").mkdir()
        (tmp_path / "kb" / "a.md").symlink_to(_outside(tmp_path, "s.md", SECRET))
        with pytest.raises(KBFileOutsideRootError):
            KBFiles(tmp_path / "kb").read_bytes(tmp_path / "kb" / "a.md")

    def test_a_missing_file_is_none(self, tmp_path):
        (tmp_path / "kb").mkdir()
        assert KBFiles(tmp_path / "kb").read_bytes(tmp_path / "kb" / "nope.md") is None


class TestExportService:
    def test_a_kb_yaml_linked_out_of_the_kb_is_never_copied(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        (kb / "kb.yaml").symlink_to(_outside(tmp_path, "kb.yaml", f"name: {SECRET}\n"))
        world = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb)], rest=False)
        target = tmp_path / "export"
        try:
            with pytest.raises(KBFileOutsideRootError):
                ExportService(world.config, world.db).export_kb_to_directory("k", target)
        finally:
            world.close()
        assert SECRET not in (_texts(target) if target.exists() else "")


class TestCliExport:
    @pytest.fixture
    def world(self, tmp_path):
        kb = tmp_path / "kb"
        (kb / "notes").mkdir(parents=True)
        (kb / "notes" / "a.md").write_text("---\nid: a\ntitle: A\ntype: note\n---\nA body\n")
        world = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb)], rest=False)
        # After indexing, the file becomes a link out of the KB.
        (kb / "notes" / "a.md").unlink()
        secret = _outside(tmp_path, "a.md", f"---\nid: a\ntitle: A\ntype: note\n---\n{SECRET}\n")
        (kb / "notes" / "a.md").symlink_to(secret)
        try:
            yield world, tmp_path
        finally:
            world.close()

    def test_export_site_reads_no_file_through_a_link(self, world):
        w, tmp_path = world
        out = tmp_path / "site"
        result = w.cli("export", "site", "-k", "k", "-o", str(out))
        assert SECRET not in _texts(out) if out.exists() else True, result.output
        assert SECRET not in result.output

    def test_export_collection_query_reads_no_file_through_a_link(self, world):
        w, tmp_path = world
        out = tmp_path / "coll"
        result = w.cli("export", "collection", "q:type:note", "-k", "k", "-o", str(out))
        assert SECRET not in (_texts(out) if out.exists() else ""), result.output
        assert SECRET not in result.output

    def test_a_folder_collection_reads_nothing_outside_the_kb(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        _outside(tmp_path, "s.md", f"---\nid: s\ntitle: S\ntype: note\n---\n{SECRET}\n")
        (kb / "c.md").write_text(
            "---\nid: c\ntitle: C\ntype: collection\nsource_type: folder\n"
            "folder_path: ../outside\n---\n"
        )
        world = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb)], rest=False)
        out = tmp_path / "coll"
        try:
            result = world.cli("export", "collection", "c", "-k", "k", "-o", str(out))
        finally:
            world.close()
        assert SECRET not in (_texts(out) if out.exists() else ""), result.output


class TestLeftOutReasons:
    """An export names each row it leaves out with a reason that is true of
    the file: a hard link does not resolve anywhere, so "resolves outside the
    KB" would be false of it. The reason is the indexer's own wording."""

    @staticmethod
    def _plain(result) -> str:
        import re

        return re.sub(r"[\s│╭╮╰╯─]+", " ", result.output)

    @pytest.mark.parametrize("form", ["symlink", "hardlink"])
    def test_a_file_outside_is_named_with_the_indexers_reason(self, tmp_path, form):
        from pyrite.utils.kb_files import OUTSIDE_KB_REASON

        kb = tmp_path / "kb"
        (kb / "notes").mkdir(parents=True)
        (kb / "notes" / "a.md").write_text("---\nid: a\ntitle: A\ntype: note\n---\nA body\n")
        world = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb)], rest=False)
        (kb / "notes" / "a.md").unlink()
        secret = _outside(tmp_path, "a.md", f"---\nid: a\ntitle: A\ntype: note\n---\n{SECRET}\n")
        if form == "symlink":
            (kb / "notes" / "a.md").symlink_to(secret)
        else:
            os.link(secret, kb / "notes" / "a.md")
        try:
            result = world.cli("export", "site", "-k", "k", "-o", str(tmp_path / "site"))
        finally:
            world.close()
        text = self._plain(result)
        assert "1 entry left out" in text, text
        assert f"a: the file {OUTSIDE_KB_REASON}" in text, text
        assert SECRET not in text

    @pytest.mark.control(reason="pins the folder's wording, which a link-only case keeps")
    def test_a_folder_outside_is_named_as_resolving_outside(self, tmp_path):
        kb = tmp_path / "kb"
        kb.mkdir()
        _outside(tmp_path, "s.md", f"---\nid: s\ntitle: S\ntype: note\n---\n{SECRET}\n")
        (kb / "c.md").write_text(
            "---\nid: c\ntitle: C\ntype: collection\nsource_type: folder\n"
            "folder_path: ../outside\n---\n"
        )
        world = build_world(tmp_path / "world", kbs=[KBConfig(name="k", path=kb)], rest=False)
        try:
            result = world.cli("export", "collection", "c", "-k", "k", "-o", str(tmp_path / "o"))
        finally:
            world.close()
        assert "../outside: the folder resolves outside the KB" in self._plain(result)
