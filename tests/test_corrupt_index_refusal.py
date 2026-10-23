"""A corrupt or unreadable SQLite index is refused from one place (#922).

Every opener of the SQLite index goes through ``ConnectionMixin._init_connection``.
When ``index.db`` holds garbage bytes, is a truncated SQLite database, or is a
directory, ``_init_connection`` disposes any engine handle and raises
``StorageError`` with a ``suggestion`` naming ``pyrite index build``.
A zero-byte ``index.db`` is opened by SQLite as an empty database and keeps
working.
"""

from __future__ import annotations

import ast
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

import pyrite.config as config_module
from pyrite.cli import app
from pyrite.exceptions import StorageError
from pyrite.storage.database import PyriteDB

REPO_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

COMMANDS = [
    ["get", "hi", "-k", "t"],
    ["backlinks", "hi", "-k", "t"],
    ["search", "hi"],
    # #1123 made an unknown --mode a refusal; a corrupt index must still win
    # over the mode, and every real mode must refuse the same way.
    ["search", "hi", "--mode", "keyword"],
    ["search", "hi", "--mode", "semantic"],
    ["search", "hi", "--mode", "hybrid"],
    ["search", "hi", "--mode", "bogus"],
    ["create", "--title", "hi", "-k", "t"],
    ["index", "sync"],
    ["index", "build"],
    ["index", "health"],
    ["index", "stats"],
    ["list-entries", "-k", "t"],
    ["recent", "-k", "t"],
    ["tags", "-k", "t"],
    ["orient", "-k", "t"],
    ["batch-read", "t:hi"],
    ["links", "check", "-k", "t"],
    ["task", "list", "-k", "t"],
    ["qa", "validate", "t"],
    ["ci"],
    # A check command exits 1 when the check cannot be made (json-contracts,
    # "Check commands"); it does not warn and answer 0 from the YAML alone.
    ["kb", "validate"],
    ["kb", "validate", "t"],
    # `db backup` opens the index with sqlite3 itself, not through PyriteDB.
    ["db", "backup"],
]


@pytest.fixture
def scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    kb_path = tmp_path / "kb"
    config_dir.mkdir()
    data_dir.mkdir()
    kb_path.mkdir()

    config_file = config_dir / "config.yaml"
    config_file.write_text(
        json.dumps(
            {
                "knowledge_bases": [
                    {
                        "name": "t",
                        "path": str(kb_path),
                        "kb_type": "generic",
                    }
                ],
                "settings": {},
            }
        ),
        encoding="utf-8",
    )
    (kb_path / "kb.yaml").write_text(
        "name: t\ntypes:\n  note:\n    required: [title]\n",
        encoding="utf-8",
    )
    (kb_path / "hi.md").write_text(
        "---\nid: hi\ntype: note\ntitle: Hi\n---\n\nBody text.\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(data_dir))
    monkeypatch.delenv("PYRITE_FORMAT", raising=False)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    return {
        "config_dir": config_dir,
        "config_file": config_file,
        "data_dir": data_dir,
        "kb_path": kb_path,
        "index_db": data_dir / "index.db",
    }


def _assert_clean_storage_refusal(result) -> dict:
    assert result.exit_code == 1, f"exit={result.exit_code} stdout={result.stdout!r}"
    for forbidden in ("Traceback", "sqlalche.me", "sqlite3"):
        assert forbidden not in result.stdout, f"{forbidden!r} leaked in stdout: {result.stdout!r}"
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "STORAGE_ERROR"
    assert "pyrite index build" in payload.get("suggestion", "")
    return payload


class TestCommandsRefuseCorruptIndex:
    @pytest.mark.parametrize("argv", COMMANDS, ids=" ".join)
    def test_garbage_index_exits_1_with_storage_error_json(
        self, scratch_home: dict[str, Path], argv: list[str]
    ):
        scratch_home["index_db"].write_bytes(b"garbage")

        result = runner.invoke(app, argv)

        _assert_clean_storage_refusal(result)

    @pytest.mark.parametrize("cut", [10, 64, 512, 4096, -100])
    @pytest.mark.parametrize("argv", COMMANDS, ids=" ".join)
    def test_truncated_real_index_exits_1_with_storage_error_json(
        self, scratch_home: dict[str, Path], argv: list[str], cut: int
    ):
        db = PyriteDB(scratch_home["index_db"])
        db.close()
        raw = scratch_home["index_db"].read_bytes()
        for suffix in ("-wal", "-shm"):
            sidecar = scratch_home["index_db"].with_name(scratch_home["index_db"].name + suffix)
            sidecar.unlink(missing_ok=True)
        scratch_home["index_db"].write_bytes(raw[:cut])

        result = runner.invoke(app, argv)

        _assert_clean_storage_refusal(result)

    @pytest.mark.parametrize("argv", COMMANDS, ids=" ".join)
    def test_directory_at_index_path_exits_1_with_storage_error_json(
        self, scratch_home: dict[str, Path], argv: list[str]
    ):
        scratch_home["index_db"].mkdir()

        result = runner.invoke(app, argv)

        _assert_clean_storage_refusal(result)


def _healthy_index(path: Path) -> None:
    PyriteDB(path).close()


def _wal_ahead_index(directory: Path) -> Path:
    """A healthy WAL-mode index whose page 1 is ahead of its main file.

    A checkpoint wrote page 1 (with the new page count) and stopped before it
    grew the file, or the process died there. Plain sqlite3 reads every row
    and ``integrity_check`` says ``ok``: the pages are in the ``-wal``.
    """
    base = directory / "base.db"
    _healthy_index(base)
    live = sqlite3.connect(base)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("CREATE TABLE filler (v BLOB)")
    live.executemany("INSERT INTO filler VALUES (?)", [(b"x" * 3000,)] * 200)
    live.commit()
    index = directory / "index.db"
    shutil.copyfile(base, index)
    wal = Path(f"{index}-wal")
    shutil.copyfile(f"{base}-wal", wal)
    live.close()

    frames = wal.read_bytes()
    page_size = int.from_bytes(frames[8:12], "big")
    page_one = None
    for at in range(32, len(frames) - page_size - 23, page_size + 24):
        if int.from_bytes(frames[at : at + 4], "big") == 1:
            page_one = frames[at + 24 : at + 24 + page_size]
    assert page_one is not None, "the WAL holds no page 1 frame"
    with index.open("r+b") as fh:
        fh.write(page_one)
    header = index.read_bytes()[:100]
    assert wal.stat().st_size > 0
    assert index.stat().st_size < int.from_bytes(header[28:32], "big") * page_size
    assert header[92:96] == header[24:28], "the header count must be current for this state"
    return index


def _stale_header_count_index(index: Path) -> None:
    """A healthy index whose header page count is not current.

    Offset 92 (version-valid-for) differs from offset 24 (change counter), so
    SQLite ignores the count at offset 28 and uses the file size.
    """
    _healthy_index(index)
    raw = bytearray(index.read_bytes())
    pages = len(raw) // int.from_bytes(raw[16:18], "big")
    raw[28:32] = (pages + 7).to_bytes(4, "big")
    raw[92:96] = ((int.from_bytes(raw[24:28], "big") + 1) & 0xFFFFFFFF).to_bytes(4, "big")
    index.write_bytes(bytes(raw))


def _plain_sqlite_calls_it_healthy(index: Path, workdir: Path) -> None:
    copy = workdir / "probe-copy.db"
    shutil.copyfile(index, copy)
    if Path(f"{index}-wal").exists():
        shutil.copyfile(f"{index}-wal", f"{copy}-wal")
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        conn.close()


class TestHealthyIndexIsNeverRefused:
    """A refusal tells the user to rebuild; it must not fire on a healthy index."""

    @pytest.fixture(params=["wal-ahead", "stale-header-count"])
    def healthy_state(self, request, tmp_path: Path) -> Path:
        directory = tmp_path / "state"
        directory.mkdir()
        if request.param == "wal-ahead":
            index = _wal_ahead_index(directory)
        else:
            index = directory / "index.db"
            _stale_header_count_index(index)
        _plain_sqlite_calls_it_healthy(index, tmp_path)
        return index

    def test_pyritedb_opens_it(self, healthy_state: Path):
        PyriteDB(healthy_state).close()

    def test_db_backup_copies_it(self, scratch_home: dict[str, Path], healthy_state: Path):
        shutil.copyfile(healthy_state, scratch_home["index_db"])
        wal = Path(f"{healthy_state}-wal")
        if wal.exists():
            shutil.copyfile(wal, f"{scratch_home['index_db']}-wal")

        result = runner.invoke(app, ["db", "backup"])

        assert result.exit_code == 0, result.output
        assert "Backup created" in result.output

    def test_a_command_reads_through_it(self, scratch_home: dict[str, Path], healthy_state: Path):
        shutil.copyfile(healthy_state, scratch_home["index_db"])
        wal = Path(f"{healthy_state}-wal")
        if wal.exists():
            shutil.copyfile(wal, f"{scratch_home['index_db']}-wal")

        result = runner.invoke(app, ["get", "hi", "-k", "t"])

        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["id"] == "hi"


class TestZeroByteIndexKeepsWorking:
    @pytest.mark.control(
        reason="0-byte index files already opened cleanly on dev; pins that 0-byte remains valid"
    )
    def test_zero_byte_file_opens_and_indexes_cleanly(self, scratch_home: dict[str, Path]):
        scratch_home["index_db"].write_bytes(b"")

        build_res = runner.invoke(app, ["index", "build", "--no-embed"])
        assert build_res.exit_code == 0, build_res.output

        get_res = runner.invoke(app, ["get", "hi", "-k", "t"])
        assert get_res.exit_code == 0, get_res.output
        assert json.loads(get_res.stdout)["id"] == "hi"

        search_res = runner.invoke(app, ["search", "Body"])
        assert search_res.exit_code == 0, search_res.output
        assert json.loads(search_res.stdout)["count"] == 1


class TestSearchCatchAllDoesNotSwallowStorageError:
    def test_search_rich_format_does_not_fall_back_to_file_search_on_corrupt_index(
        self, scratch_home: dict[str, Path]
    ):
        scratch_home["index_db"].write_bytes(b"garbage")

        result = runner.invoke(app, ["search", "Body", "--format", "rich"])

        assert result.exit_code == 1
        assert "STORAGE_ERROR" in result.stdout
        # Rich wraps the suggestion at the console width; read it unwrapped.
        assert "pyrite index build" in " ".join(result.stdout.split())
        assert "Falling back to file search" not in result.stdout

    @pytest.mark.parametrize("fmt", ["json", "yaml", "csv", "markdown"])
    def test_search_machine_formats_emit_storage_error_instead_of_catchall_payload(
        self, scratch_home: dict[str, Path], fmt: str
    ):
        scratch_home["index_db"].write_bytes(b"garbage")

        result = runner.invoke(app, ["search", "Body", "--format", fmt])

        payload = _assert_clean_storage_refusal(result)
        assert payload["retryable"] is False
        assert "error_type" not in payload
        assert "results" not in payload


class TestInteriorPageCorruptionRefusal:
    """Interior page corruption passes the open-time header probe; any mid-command
    sqlite3.DatabaseError must refuse with STORAGE_ERROR (#1182)."""

    def test_non_corruption_query_errors_keep_their_sqlite_identity(self, tmp_path):
        from sqlalchemy.exc import OperationalError

        with PyriteDB(tmp_path / "index.db") as db:
            with pytest.raises(OperationalError) as caught:
                db.execute_sql("SELECT nonexistent_column FROM entry")
            assert caught.value.orig.sqlite_errorcode == sqlite3.SQLITE_ERROR
            with pytest.raises(sqlite3.OperationalError) as raw:
                with db._raw_cursor() as cursor:
                    cursor.execute("SELECT nonexistent_column FROM entry")
            assert raw.value.sqlite_errorcode == sqlite3.SQLITE_ERROR

    @pytest.fixture
    def corrupt_interior_page_index(self, scratch_home: dict[str, Path]) -> dict[str, Path]:
        for i in range(10):
            p = scratch_home["kb_path"] / f"entry{i}.md"
            p.write_text(
                f"---\nid: entry{i}\ntype: note\ntitle: Entry {i}\ntags: [tagA, tagB]\n---\n"
                + ("Content " * 100)
                + "\n",
                encoding="utf-8",
            )
        build_res = runner.invoke(app, ["index", "build", "--no-embed"])
        assert build_res.exit_code == 0, build_res.output

        index_db = scratch_home["index_db"]
        conn = sqlite3.connect(index_db)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        rootpages = [
            row[0]
            for row in conn.execute(
                "SELECT rootpage FROM sqlite_schema WHERE type='table' AND name IN ('entry', 'tag', 'entry_tag', 'link')"
            ).fetchall()
        ]
        conn.close()

        # Flip bytes inside interior table pages
        for rootpage in rootpages:
            offset = (rootpage - 1) * 4096
            with index_db.open("r+b") as fh:
                fh.seek(offset)
                raw = fh.read(4)
                fh.seek(offset)
                fh.write(bytes([b ^ 0xFF for b in raw]))

        from pyrite.storage.connection import index_file_problem

        assert index_file_problem(index_db) is None

        return scratch_home

    @pytest.mark.parametrize(
        "argv",
        [
            ["get", "entry1", "-k", "t"],
            ["list-entries", "-k", "t"],
            ["recent", "-k", "t"],
            ["tags", "-k", "t"],
            ["backlinks", "entry1", "-k", "t"],
            ["orient", "-k", "t"],
            ["batch-read", "t:entry1"],
        ],
        ids=" ".join,
    )
    def test_non_search_commands_exit_1_with_storage_error_on_interior_corruption(
        self, corrupt_interior_page_index: dict[str, Path], argv: list[str]
    ):
        result = runner.invoke(app, argv)
        _assert_clean_storage_refusal(result)


# ---------------------------------------------------------------------------
# Structural guard: ConnectionMixin._init_connection is the one index opener
# ---------------------------------------------------------------------------

ALLOWED_INDEX_OPENERS = {
    "pyrite/storage/connection.py::_init_connection": (
        "the one place that opens the SQLite index with create_engine"
    ),
    "pyrite/storage/backends/postgres_backend.py::create_postgres_engine": (
        "Postgres backend engine factory, not the SQLite index"
    ),
    "pyrite/services/index_backup.py::backup_index": (
        "sqlite3 backup API copies between source and backup file; refuses a "
        "corrupt index itself with StorageError (db backup rows of COMMANDS)"
    ),
    "pyrite/cli/db_commands.py::db_restore": (
        "validates the user-supplied backup file before copying it into place"
    ),
    "pyrite/services/id_pin_service.py::_registered_kb": (
        "read-only immutable probe for dry-run id checks; catches sqlite3.Error"
    ),
    "pyrite/storage/migrations.py::ensure_migrated": (
        "standalone migration helper in storage/migrations.py"
    ),
}


def _find_sqlite_openers(source: str, rel: str) -> list[str]:
    """Return ``rel::func`` for every function calling ``create_engine`` or ``sqlite3.connect``."""
    found: list[str] = []
    tree = ast.parse(source)

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def _visit_scoped(self, node: ast.AST, name: str) -> None:
            self.scope.append(name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_scoped(node, node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self._visit_scoped(node, node.name)

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            fn = node.func
            is_opener = (isinstance(fn, ast.Name) and fn.id in {"create_engine", "connect"}) or (
                isinstance(fn, ast.Attribute)
                and fn.attr == "connect"
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "sqlite3"
            )
            if is_opener:
                qual = self.scope[-1] if self.scope else "<module>"
                found.append(f"{rel}::{qual}")
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


@pytest.mark.control(
    reason="structural guard that _init_connection remains the one SQLite index opener"
)
def test_only_init_connection_opens_the_sqlite_index():
    roots = [REPO_ROOT / "pyrite", *sorted((REPO_ROOT / "extensions").glob("*/src"))]
    found: set[str] = set()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            found.update(_find_sqlite_openers(path.read_text(encoding="utf-8"), rel))

    unexpected = sorted(found - set(ALLOWED_INDEX_OPENERS))
    assert not unexpected, (
        f"Index opened outside ConnectionMixin._init_connection: {unexpected}. "
        "Open the index through PyriteDB / ConnectionMixin._init_connection so "
        "corrupt-index refusal is raised from one place (#922)."
    )
    stale = sorted(set(ALLOWED_INDEX_OPENERS) - found)
    assert not stale, f"Stale entries in ALLOWED_INDEX_OPENERS: {stale}"


@pytest.mark.control(reason="self-test for the AST opener scanner")
def test_scanner_catches_rogue_sqlite_connect_and_create_engine():
    sample = (
        "def bad_cmd(p):\n"
        "    return sqlite3.connect(p)\n"
        "def bad_engine(u):\n"
        "    return create_engine(u)\n"
    )
    assert _find_sqlite_openers(sample, "pyrite/cli/x.py") == [
        "pyrite/cli/x.py::bad_cmd",
        "pyrite/cli/x.py::bad_engine",
    ]


def test_init_connection_raises_storage_error_directly(tmp_path: Path):
    bad = tmp_path / "index.db"
    bad.write_bytes(b"garbage")
    with pytest.raises(StorageError) as exc_info:
        PyriteDB(bad)
    assert exc_info.value.error_code == "STORAGE_ERROR"
    assert "pyrite index build" in (exc_info.value.suggestion or "")
