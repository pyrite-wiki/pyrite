"""Rebuild the index-shape fixtures from the Pyrite trees that wrote them (#1260).

Each ``<shape>.sql`` beside this file is the schema of an empty index exactly
as one released or branch build of Pyrite left it on disk: that tree's own
``PyriteDB`` opened a new file (``create_all``, the FTS table, its migrations),
and this script dumped what was there. The tests load the SQL with plain
``sqlite3`` and then open it with the code under test, so they see the same
tables, columns, indexes, triggers and ``schema_version`` rows a user upgrading
from that build has, without a live install.

Run from the repository root, with the dev venv::

    .venv/bin/python tests/fixtures/index-shapes/build_index_shapes.py

It needs the refs in the local clone (``git fetch origin --tags`` and the
``arch/2026-10`` history). Plugin tables are left out (they depend on which
extensions are installed, and are created ``IF NOT EXISTS`` on every open), as
are the FTS5 and sqlite-vec shadow tables (``CREATE VIRTUAL TABLE`` makes the
FTS ones; the vec table is created on open when the extension loads).
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]

# shape name -> (git ref, why this shape matters)
SHAPES = {
    "v0.25.8": ("v0.25.8", "released 0.25.8: schema 27, no entry.aliases, no link.target_prefix"),
    "v0.26.0": ("v0.26.0", "released 0.26.0: schema 29, the current columns"),
    "arch-df6372f2": (
        "df6372f2",
        "arch/2026-10 at df6372f2 (2026-10-17): schema 28 meant entry.aliases, "
        "so link.target_prefix is absent while 28 is recorded (#1260)",
    ),
}

# Runs inside the old tree. It prints the dump on stdout.
_DUMP = r"""
import sqlite3, sys
from pathlib import Path
from pyrite.storage import connection
connection.ConnectionMixin._create_plugin_tables = lambda self: None
from pyrite.storage.database import PyriteDB

path = Path(sys.argv[1])
db = PyriteDB(path)
db.close()
conn = sqlite3.connect(path)
skip_prefixes = ("sqlite_", "entry_fts_", "vec_entry")
rows = conn.execute(
    "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
).fetchall()
order = {"table": 0, "index": 1, "trigger": 2}
out = []
for kind, name, sql in sorted(rows, key=lambda r: order.get(r[0], 3)):
    if name.startswith(skip_prefixes) or kind not in order:
        continue
    # Trailing spaces stripped: the repo's whitespace hook would otherwise rewrite them.
    out.append("\n".join(line.rstrip() for line in sql.strip().splitlines()) + ";")
for version, description, applied_at in conn.execute(
    "SELECT version, description, applied_at FROM schema_version ORDER BY version"
):
    desc = description.replace("'", "''")
    out.append(
        f"INSERT INTO schema_version (version, description, applied_at) "
        f"VALUES ({version}, '{desc}', '2026-01-01T00:00:00+00:00');"
    )
print("\n".join(out))
"""


def build(shape: str, ref: str, why: str) -> None:
    archive = subprocess.run(
        ["git", "-C", str(REPO), "archive", ref, "pyrite"],
        check=True,
        capture_output=True,
        timeout=120,
    ).stdout
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        tree.mkdir()
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(tree, filter="data")
        dump = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                f"import sys; sys.path.insert(0, {str(tree)!r})\n" + _DUMP,
                str(Path(tmp) / "index.db"),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        ).stdout
    header = f"-- Index shape {shape}: {why}.\n-- Written by build_index_shapes.py from git ref {ref}; do not edit.\n"
    (HERE / f"{shape}.sql").write_text(header + dump)
    print(f"wrote {shape}.sql")


if __name__ == "__main__":
    for name, (ref, why) in SHAPES.items():
        build(name, ref, why)
