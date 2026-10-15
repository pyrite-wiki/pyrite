"""Every CLI parameter that names an input file refuses a bad path first (#923).

The user's model: "I gave the command the wrong path. It tells me which path
and why, and it writes nothing."

The rule is one helper, ``pyrite.cli.context.require_input_file`` (#846): a
missing path is ``NOT_FOUND``, a directory is ``VALIDATION_FAILED``, exit 1, no
traceback. #846 called it from four commands and left the rest; this walks the
real click command tree (the registry, extensions included), so a command added
tomorrow with an input-file parameter fails ``test_every_candidate_is_classified``
until someone says here whether it is an input file.

Every case enters through the real root with ``CliRunner`` and a real config
(``PYRITE_CONFIG_DIR``), then compares bytes before and after.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings, save_config
from tests.outcome_walk import leaves

# (command path, parameter name) -> argv builder, given the path under test.
# Each is an input FILE the user names: read, never written.
INPUT_FILE_PARAMS: dict[tuple[tuple[str, ...], str], object] = {
    (("create",), "body_file"): lambda p: [
        "create",
        "-k",
        "k",
        "-t",
        "note",
        "--title",
        "D",
        "--body-file",
        p,
    ],  # fmt: skip
    (("update",), "body_file"): lambda p: ["update", "hello", "-k", "k", "--body-file", p],
    (("add",), "file_path"): lambda p: ["add", p, "-k", "k"],
    (("links", "bulk-create"), "file_option"): lambda p: [
        "links",
        "bulk-create",
        "--file",
        p,
        "-k",
        "k",
    ],  # fmt: skip
    (("links", "bulk-create"), "file"): lambda p: ["links", "bulk-create", p, "-k", "k"],
    (("import",), "file_path"): lambda p: ["import", p, "-k", "k"],
    (("kb", "schema", "set"), "schema_file"): lambda p: [
        "kb",
        "schema",
        "set",
        "k",
        "--schema-file",
        p,
    ],  # fmt: skip
    (("init",), "schema_file"): lambda p: [
        "init",
        "-t",
        "software",
        "-p",
        str(Path(p).parent / "newkb"),
        "--schema-file",
        p,
    ],  # fmt: skip
    (("investigation", "bulk-edges"), "file"): lambda p: [
        "investigation",
        "bulk-edges",
        "--file",
        p,
        "-k",
        "k",
    ],  # fmt: skip
    (("investigation", "ftm-import"), "file"): lambda p: [
        "investigation",
        "ftm-import",
        "--file",
        p,
        "-k",
        "k",
    ],  # fmt: skip
    (("cascade", "extract-actors"), "alias_file"): lambda p: [
        "cascade",
        "extract-actors",
        "-k",
        "k",
        "--alias-file",
        p,
    ],  # fmt: skip
    (("db", "restore"), "backup_path"): lambda p: ["db", "restore", p, "--force"],
}

# Candidates that are not an input file, with the reason.
NOT_INPUT_FILE: dict[tuple[tuple[str, ...], str], str] = {
    (("init",), "path"): "the directory to create",
    (("kb", "add"), "path"): "a directory (a different rule)",
    (("kb", "create"), "path"): "the directory to create",
    (("kb", "discover"), "search_path"): "a directory to search",
    (("repo", "add"), "path"): "a directory (a repository)",
    (("extension", "init"), "path"): "the directory to create",
    (("extension", "install"), "path"): "a directory (a package)",
    (("schema", "validate"), "files"): "KB files, reported one by one with a read error each",
    (("qa", "check-urls"), "cache_file"): "Pyrite's own cache file, read and written",
    (("mcp-setup",), "config_path"): "another program's config, guarded by #582",
    (("kb", "commit"), "paths"): "paths inside a KB for git, not read by Pyrite",
    (("kb", "schema", "add-type"), "subdirectory"): "a KB subdirectory name",
    (("search",), "use_files"): "a boolean",
    (("repo", "unsubscribe"), "delete_files"): "a boolean",
    (("link",), "bidirectional"): "a boolean",
    (("investigation", "money-flow"), "direction"): "not a path",
    (("social", "vote"), "direction"): "not a path",
}

_NAME = re.compile(r"file|input|backup|cache|config|schema|alias|path|dir", re.I)


def _candidates():
    from pyrite.cli import app

    for path, command in leaves(typer.main.get_command(app)):
        for param in command.params:
            if _NAME.search(param.name) and "output" not in param.name:
                yield tuple(path), param.name


@pytest.mark.control(
    reason="control: a pin on the tree; it fails when a command is added unclassified"
)
def test_every_candidate_is_classified():
    found = set(_candidates())
    unclassified = found - set(INPUT_FILE_PARAMS) - set(NOT_INPUT_FILE)
    assert not unclassified, (
        "a command takes a parameter that may name an input file; add it to "
        "INPUT_FILE_PARAMS (and route it through require_input_file) or to "
        f"NOT_INPUT_FILE with the reason: {sorted(unclassified)}"
    )
    stale = (set(INPUT_FILE_PARAMS) | set(NOT_INPUT_FILE)) - found
    assert not stale, f"classified but no longer in the command tree: {sorted(stale)}"


@pytest.fixture
def env(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "hello.md").write_text("---\nid: hello\ntype: note\ntitle: Hello\n---\nBody\n")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="k", path=kb, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "cfg" / "index.db"),
    )
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(cfg))
    save_config(config)
    from pyrite.storage.database import PyriteDB
    from pyrite.storage.index import IndexManager

    db = PyriteDB(cfg / "index.db")
    IndexManager(db, config).index_all()
    db.close()
    return {"tmp": tmp_path, "kb": kb, "cfg": cfg}


def _snapshot(env) -> dict[str, bytes]:
    out = {}
    for root in (env["kb"], env["cfg"], env["tmp"] / "newkb"):
        if root.exists():
            for f in sorted(root.rglob("*")):
                if f.is_file() and not f.name.endswith(("-wal", "-shm")):
                    out[str(f)] = f.read_bytes()
    return out


def _run(env, key, path: Path):
    from pyrite.cli import app

    argv = INPUT_FILE_PARAMS[key](str(path))
    # The legacy plugin commands spell machine output `--json`; the rest `--format`.
    machine = ["--json"] if key[0][0] == "investigation" else ["--format", "json"]
    if key[0] in (("db", "restore"), ("cascade", "extract-actors"), ("import",)):
        machine = []  # no machine format: the error is the rich line
    return CliRunner().invoke(app, [*argv, *machine], env={"COLUMNS": "500"})


def _refusal(out) -> tuple[str, str]:
    """(error_code, message) from the JSON payload or the rich ``ERROR [CODE]: msg`` line."""
    try:
        doc = json.loads(out.stdout)
        return doc["error_code"], doc["error"]
    except (ValueError, KeyError):
        m = re.search(r"ERROR \[(\w+)\]:\s*(.*)", out.stdout)
        assert m, f"no refusal in output: {out.output!r}"
        return m.group(1), m.group(2)


# Guarded by #846 before this change: these pass without it, as controls.
_ALREADY_GUARDED = {
    (("create",), "body_file"),
    (("update",), "body_file"),
    (("add",), "file_path"),
    (("links", "bulk-create"), "file_option"),
    (("links", "bulk-create"), "file"),
    (("db", "restore"), "backup_path"),
}


# A missing path was already NOT_FOUND here (the groom's table).
_MISSING_ALREADY_REFUSED = _ALREADY_GUARDED | {
    (("import",), "file_path"),
    (("kb", "schema", "set"), "schema_file"),
    (("init",), "schema_file"),
}


def _params(guarded=_ALREADY_GUARDED):
    control = pytest.mark.control(reason="control: this command was already guarded (#846)")
    return [
        pytest.param(k, id=f"{' '.join(k[0])} {k[1]}", marks=[control] if k in guarded else [])
        for k in INPUT_FILE_PARAMS
    ]


@pytest.mark.parametrize("key", _params())
def test_a_directory_is_validation_failed_and_writes_nothing(env, key):
    d = env["tmp"] / "a-directory.json"
    d.mkdir()
    before = _snapshot(env)
    out = _run(env, key, d)
    assert out.exit_code == 1, out.output
    assert not isinstance(out.exception, (IsADirectoryError, PermissionError)), repr(out.exception)
    code, message = _refusal(out)
    assert code == "VALIDATION_FAILED", message
    assert str(d) in message
    assert _snapshot(env) == before


@pytest.mark.parametrize("key", _params(_MISSING_ALREADY_REFUSED))
def test_a_missing_path_is_not_found_and_writes_nothing(env, key):
    missing = env["tmp"] / "nope.json"
    before = _snapshot(env)
    out = _run(env, key, missing)
    assert out.exit_code == 1, out.output
    assert not isinstance(out.exception, FileNotFoundError), repr(out.exception)
    code, message = _refusal(out)
    assert code == "NOT_FOUND", message
    assert str(missing) in message
    assert _snapshot(env) == before


def test_a_symlink_to_a_directory_is_refused_like_a_directory(env):
    d = env["tmp"] / "real-dir"
    d.mkdir()
    link = env["tmp"] / "link.json"
    link.symlink_to(d, target_is_directory=True)
    out = _run(env, (("import",), "file_path"), link)
    assert out.exit_code == 1
    assert _refusal(out)[0] == "VALIDATION_FAILED"


@pytest.mark.parametrize("fmt_args", [[], ["--format", "yaml"]], ids=["json-ext", "yaml"])
def test_import_dry_run_still_refuses_a_directory(env, fmt_args):
    from pyrite.cli import app

    d = env["tmp"] / "d.yaml"
    d.mkdir()
    out = CliRunner().invoke(app, ["import", str(d), "-k", "k", "--dry-run", *fmt_args])
    assert _refusal(out)[0] == "VALIDATION_FAILED"
    assert out.exit_code == 1
    assert not isinstance(out.exception, IsADirectoryError)


# These already refused first before this change (#846, or the check was the
# first statement): controls, not red. Only `kb schema set` was out of order.
_GUARDED_FIRST = {
    (("add",), "file_path"),
    (("create",), "body_file"),
    (("update",), "body_file"),
    (("db", "restore"), "backup_path"),
    (("import",), "file_path"),
    (("links", "bulk-create"), "file"),
    (("links", "bulk-create"), "file_option"),
}
_FIRST_ALREADY = {(k, b) for k in _GUARDED_FIRST for b in ("directory", "missing")} | {
    ((("init",), "schema_file"), "missing")
}


def _order_params():
    control = pytest.mark.control(reason="control: this check already came first")
    return [
        pytest.param(
            k,
            bad,
            id=f"{' '.join(k[0])} {k[1]}-{bad}",
            marks=[control] if (k, bad) in _FIRST_ALREADY else [],
        )
        for k in INPUT_FILE_PARAMS
        for bad in ("directory", "missing")
    ]


@pytest.mark.parametrize(("key", "bad"), _order_params())
def test_a_refusal_comes_before_any_config_lookup_that_opens_the_index(env, key, bad):
    """The refusal precedes every lookup that can create the index DB (#923, fix round 1).

    A config dir with no ``index.db`` and a KB that is not in ``config.yaml``
    (so a registry lookup would open ``PyriteDB`` to find it): after the
    refusal ``index.db`` must still not exist.
    """
    (env["cfg"] / "index.db").unlink(missing_ok=True)
    for sidecar in env["cfg"].glob("index.db-*"):
        sidecar.unlink()
    path = env["tmp"] / "bad-input"
    if bad == "directory":
        path.mkdir()
    argv = INPUT_FILE_PARAMS[key](str(path))
    argv = ["nosuchkb" if a == "k" else a for a in argv]  # `-k k` / `kb schema set k`
    from pyrite.cli import app

    machine = ["--json"] if key[0][0] == "investigation" else ["--format", "json"]
    if key[0] in (("db", "restore"), ("cascade", "extract-actors"), ("import",)):
        machine = []
    out = CliRunner().invoke(app, [*argv, *machine], env={"COLUMNS": "500"})
    assert out.exit_code == 1, out.output
    assert _refusal(out)[0] == ("VALIDATION_FAILED" if bad == "directory" else "NOT_FOUND")
    assert not (env["cfg"] / "index.db").exists(), "the refusal came after the index was opened"
