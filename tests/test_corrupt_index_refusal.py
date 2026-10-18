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
    ["create", "--title", "hi", "-k", "t"],
    ["index", "sync"],
    ["index", "build"],
    ["index", "health"],
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
    @pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a[:2]))
    def test_garbage_index_exits_1_with_storage_error_json(
        self, scratch_home: dict[str, Path], argv: list[str]
    ):
        scratch_home["index_db"].write_bytes(b"garbage")

        result = runner.invoke(app, argv)

        _assert_clean_storage_refusal(result)

    @pytest.mark.parametrize("cut", [10, 64, 512, 4096, -100])
    @pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a[:2]))
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

    @pytest.mark.parametrize("argv", COMMANDS, ids=lambda a: " ".join(a[:2]))
    def test_directory_at_index_path_exits_1_with_storage_error_json(
        self, scratch_home: dict[str, Path], argv: list[str]
    ):
        scratch_home["index_db"].mkdir()

        result = runner.invoke(app, argv)

        _assert_clean_storage_refusal(result)


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
    "pyrite/cli/db_commands.py::db_backup": (
        "sqlite3 backup API copies between source and backup file"
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
