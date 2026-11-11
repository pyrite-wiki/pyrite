"""``unmodellable_files`` names a file by the bytes it read inside the KB.

The row's ``id`` comes from the same text ``_read_inside`` returned, never
from a second, raw read of the path: whatever the path names by the time of
a second read (a link out of the KB put in its place) is not the KB's, and
its id must not reach ``index health`` or ``qa validate``.
"""

from __future__ import annotations

from pyrite.config import KBConfig
from pyrite.storage import repository
from pyrite.storage.index import unmodellable_files

SECRET = "secretoutsidethekb7d41"


def test_the_row_id_comes_from_the_bytes_read_inside(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    odd = kb / "notes" / "odd.md"
    odd.write_text("---\n{title: Odd, type: note}\n---\nbody\n")  # a flow root
    outside = tmp_path / "outside.md"
    outside.write_text(f"---\n{{id: {SECRET}, title: X, type: note}}\n---\n")

    real = repository.KBRepository._read_inside

    def read_then_swap(self, path):
        raw = real(self, path)
        if path == odd:
            path.unlink()
            path.symlink_to(outside)  # the path names something else now
        return raw

    monkeypatch.setattr(repository.KBRepository, "_read_inside", read_then_swap)
    rows = unmodellable_files(KBConfig(name="k", path=kb, kb_type="generic"))
    assert [r["id"] for r in rows] == ["odd"]
