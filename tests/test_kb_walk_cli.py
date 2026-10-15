"""Every CLI command that takes a KB name asks the one resolver -- walked (#952, #924).

The REST and MCP halves are `tests/test_kb_walk.py`. The CLI has no
dependency tree and no dispatcher: each command reads its own `-k`/`--kb` or
positional KB. So this walk reads the Typer trees of `pyrite`, `pyrite-read`
and `pyrite-admin`, finds every leaf with a KB-bearing parameter, fills the
other required parameters with values that pass, and runs it through the real
root with:

- an **unknown** name: exit 1 and `KB_NOT_FOUND`, never an empty result and
  exit 0 (`tags`, `timeline`, `task list`, `qa validate`, `sw backlog` did
  that; #924);
- a **shortname**: `KB_NOT_FOUND`, with the full name suggested (shortnames
  are link syntax only, ADR-0049 Q3).

A KB parameter that is not a lookup (the name of a KB being created) is
listed in `NOT_A_LOOKUP` with its reason; an unlisted parameter on a new
command fails `test_every_kb_parameter_is_walked_or_classified`. An extension
group may be absent from `pyrite.cli.app` in a worker that imported the
extension's own CLI first (#900), so extension commands are walked when
present and never required.
"""

from __future__ import annotations

import re

import pytest
import typer
from typer.testing import CliRunner

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models import NoteEntry
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from tests.outcome_walk import leaves, root_app

ROOTS = ("pyrite", "pyrite-read", "pyrite-admin")
KB_PARAMS = {"kb", "kb_name", "source_kb", "target_kb", "center_kb", "kb_a", "kb_b"}
FULL, SHORT, UNKNOWN = "demo", "dm", "no-such-kb"
EXTENSION_GROUPS = {"zettel", "sw", "investigation", "wiki", "social", "cascade"}

#: KB parameters that name a KB without looking one up, with the reason.
NOT_A_LOOKUP: dict[tuple[str, str, str], str] = {
    ("pyrite", "kb add", "name"): "the name of the KB being registered",
    ("pyrite", "kb create", "name"): "the name of the KB being created",
    ("pyrite-admin", "kb add", "name"): "the name of the KB being registered",
}

_FILE_LIKE = re.compile(r"file|input|path|schema|alias", re.I)

#: What a command needs beside its KB to get past its own validation, so the
#: KB is the one thing wrong with the call.
EXTRA_ARGS: dict[str, list[str]] = {
    "pyrite create": ["--title", "Walk"],
    "pyrite update": ["--title", "Walk"],
    "pyrite task create": ["Walk"],
    "pyrite task update": ["--assignee", "walker"],
    "pyrite delete": ["--force"],
    "pyrite links bulk-create": ["{links}"],
    "pyrite import": ["--format", "markdown"],
}
#: A positional argument that must be one of a command's own words.
POSITIONAL: dict[tuple[str, str], str] = {("pyrite wiki review", "action"): "approve"}
#: KB parameters a command takes only together (`links asymmetric --kb-a/--kb-b`).
PAIRED: dict[str, tuple[str, ...]] = {"pyrite links asymmetric": ("kb_a", "kb_b")}


@pytest.fixture(scope="module")
def cli_files(tmp_path_factory):
    """Two KBs, one with a shortname, in a config file the real CLI loads;
    built once per module, pointed at per test by `cli_world`."""
    from pyrite.utils.yaml import dump_yaml_file

    tmp = tmp_path_factory.mktemp("kb-walk-cli")
    kbs = []
    for name, short in ((FULL, SHORT), ("other", None)):
        path = tmp / name
        path.mkdir()
        kbs.append(KBConfig(name=name, path=path, kb_type="generic", shortname=short))
        KBRepository(kbs[-1]).save(NoteEntry(id="hello", title="Hello", body="Body", tags=["t"]))
    config = PyriteConfig(
        knowledge_bases=kbs, settings=Settings(index_path=tmp / "index.db", auto_embed=False)
    )
    db = PyriteDB(config.settings.index_path)
    for kb in kbs:
        IndexManager(db, config).sync_kb(kb)
    db.close()
    config_dir = tmp / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    dump_yaml_file(config.to_dict(), config_file)
    afile = tmp / "input.md"
    afile.write_text("---\nid: x\ntype: note\ntitle: X\n---\nBody\n")
    links = tmp / "links.yaml"
    links.write_text("- source: hello\n  target: hello\n")
    return {
        "file": str(afile),
        "links": str(links),
        "tmp": tmp,
        "config_dir": config_dir,
        "config_file": config_file,
    }


@pytest.fixture
def cli_world(cli_files, monkeypatch):
    """Point the real CLI at the world, per test: the root conftest's own
    per-test isolation runs after a module fixture and would override it."""
    import pyrite.config as config_module

    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(cli_files["config_dir"]))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(cli_files["tmp"]))
    monkeypatch.setattr(config_module, "CONFIG_DIR", cli_files["config_dir"])
    monkeypatch.setattr(config_module, "CONFIG_FILE", cli_files["config_file"])
    return cli_files


def _kb_params():
    """(root, command path, param) for every KB-bearing parameter."""
    out = []
    for root in ROOTS:
        for path, cmd in leaves(typer.main.get_command(root_app(root))):
            for p in cmd.params:
                is_kb_route_name = p.name == "name" and path[:1] == ("kb",)
                if p.name in KB_PARAMS or is_kb_route_name:
                    out.append((root, " ".join(path), p.name))
    return out


def _cases():
    return [c for c in _kb_params() if c not in NOT_A_LOOKUP]


def _argv(root: str, command: str, kb_param: str, probe: str, files: dict) -> list[str]:
    """The command line: every required parameter filled with a value that
    passes, every other KB parameter the full name, and `kb_param` the probe."""
    path = tuple(command.split())
    cmd = dict(leaves(typer.main.get_command(root_app(root))))[path]
    key = f"{root} {command}"
    argv = list(path) + [a.format(**files) for a in EXTRA_ARGS.get(key, [])]
    given = {a for a in EXTRA_ARGS.get(key, []) if a.startswith("--")}
    paired = PAIRED.get(key, ())
    for p in cmd.params:
        name_is_kb = p.name in KB_PARAMS or (p.name == "name" and path[:1] == ("kb",))
        if p.opts[0] in given:
            continue
        if p.name == kb_param:
            value = probe
        elif name_is_kb and (p.required or (p.name in paired and kb_param in paired)):
            value = FULL
        elif not p.required:
            continue
        elif (key, p.name) in POSITIONAL:
            value = POSITIONAL[(key, p.name)]
        elif _FILE_LIKE.search(p.name):
            value = files["file"]
        else:
            kind = getattr(p.type, "name", "")
            if kind in ("integer", "float"):
                value = "1"
            elif getattr(p.type, "choices", None):
                value = str(list(p.type.choices)[0])
            else:
                value = "x"
        if p.param_type_name == "argument":
            argv.append(value)
        else:
            argv += [p.opts[0], value]
    # The output option, not a command's own `--format` (export's target,
    # import's input format): named `output_format`, or `fmt` with "output"
    # in its help.
    if any(
        "--format" in p.opts
        and (p.name == "output_format" or (p.name == "fmt" and "output" in (p.help or "").lower()))
        for p in cmd.params
    ):
        argv += ["--format", "json"]
    return argv


def _run(root: str, argv: list[str]):
    return CliRunner().invoke(root_app(root), argv)


def _case_id(case):
    return f"{case[0]} {case[1]} [{case[2]}]"


@pytest.mark.control(reason="a pin on the tree: fails when a command is added unclassified")
def test_every_kb_parameter_is_walked_or_classified():
    found = set(_kb_params())
    stale = {k for k in NOT_A_LOOKUP if k not in found and k[1].split()[0] not in EXTENSION_GROUPS}
    assert not stale, f"classified but no longer in the tree: {sorted(stale)}"
    assert ("pyrite", "tags", "kb_name") in found and ("pyrite", "qa validate", "kb_name") in found


@pytest.mark.parametrize("case", _cases(), ids=_case_id)
def test_an_unknown_kb_is_refused(cli_world, case):
    root, command, param = case
    result = _run(root, _argv(root, command, param, UNKNOWN, cli_world))
    assert result.exit_code == 1, (result.exit_code, result.output)
    assert "KB_NOT_FOUND" in result.output, result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("case", _cases(), ids=_case_id)
def test_a_shortname_is_refused_with_the_full_name(cli_world, case):
    root, command, param = case
    result = _run(root, _argv(root, command, param, SHORT, cli_world))
    assert result.exit_code == 1, (result.exit_code, result.output)
    assert "KB_NOT_FOUND" in result.output and FULL in result.output, result.output
