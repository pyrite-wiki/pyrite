"""docs/agent-write-path.md and docs/json-contracts.md, run as tests (#736).

The two pages an agent integrator acts on literally are their own tests:

1. **Every ```bash block runs**, in order, in a scratch HOME and config, through
   the installed ``pyrite``. A block's exit code is the one in the
   ``<!-- expect-exit: N -->`` comment above it (default 0); each
   ``<!-- expect-text: WORD -->`` comment above it names text its output
   (stdout + stderr) must contain.
2. **``<!-- output-keys -->`` before a ```json block**: the block's keys must
   equal the keys of the JSON the previous command printed. Keys, not values:
   ids, paths and times vary. A non-empty list or object in the doc is compared
   one level down, an empty one is not.
3. **``<!-- mcp-output: TOOL -->``** before a ```json block: the same
   comparison against the MCP tool handler's real result, called in process.
4. ``json-contracts.md``'s operational-contracts block equals what
   ``KBService.orient`` returns.
5. ``json-contracts.md``'s ``--format`` table equals each command's Typer
   signature.
6. ``KB_NOT_FOUND`` has one table row in ``docs/``, not one per page.

Every failure names the page and the line. REST rows are reference, marked
"not run" on the page: no server is started.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"
PAGES = ["agent-write-path.md", "json-contracts.md"]

ITEM = re.compile(
    r"<!--\s*(?P<directive>expect-exit|expect-text|output-keys|mcp-output|"
    r"orient-operational-contracts)\s*:?\s*(?P<arg>.*?)\s*-->"
    r"|^```(?P<lang>\w*)[^\n]*\n(?P<body>.*?)^```",
    re.S | re.M,
)


def items(page: str):
    """("directive", name, arg, line) and ("block", lang, body, line), in order."""
    text = (DOCS / page).read_text(encoding="utf-8")
    out = []
    for m in ITEM.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        if m.group("directive"):
            out.append(("directive", m.group("directive"), m.group("arg"), line))
        else:
            out.append(("block", m.group("lang"), m.group("body"), line))
    return out


def key_shape(doc, real, path="$"):
    """Differences between the keys of the documented JSON and the real one."""
    problems = []
    if isinstance(doc, dict):
        if not isinstance(real, dict):
            return [f"{path}: documented an object, got {type(real).__name__}"]
        if set(doc) != set(real):
            problems.append(f"{path}: documented keys {sorted(doc)}, real keys {sorted(real)}")
        for k in set(doc) & set(real):
            problems += key_shape(doc[k], real[k], f"{path}.{k}")
    elif isinstance(doc, list) and doc:
        if not isinstance(real, list) or not real:
            return [f"{path}: documented a non-empty list, got {real!r}"]
        for i, d in enumerate(doc):
            r = real[i] if i < len(real) else real[0]
            problems += key_shape(d, r, f"{path}[{i}]")
    return problems


def _last_json(output: str):
    start = min((i for i in (output.find("{"), output.find("[")) if i >= 0), default=-1)
    decoder = json.JSONDecoder()
    while start >= 0:
        try:
            return decoder.raw_decode(output[start:])[0]
        except json.JSONDecodeError:
            nxt = [i for i in (output.find("{", start + 1), output.find("[", start + 1)) if i >= 0]
            start = min(nxt, default=-1)
    raise AssertionError(f"no JSON in output:\n{output}")


def _env(tmp_path: Path) -> dict[str, str]:
    cfg = tmp_path / ".pyrite"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PYRITE_DATA_DIR": str(cfg),
        "PYRITE_CONFIG_DIR": str(cfg),
        "PYRITE_AUTO_EMBED": "0",
        "HF_HUB_OFFLINE": "1",
        "NO_COLOR": "1",
        "COLUMNS": "200",
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    env.pop("PYRITE_KB", None)
    return env


# --- MCP: the handler's real result, for `mcp-output` blocks ------------------


@pytest.fixture
def mcp_results(tmp_path):
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.server.mcp_server import PyriteMCPServer

    kb = tmp_path / "mcpkb"
    kb.mkdir()
    (kb / "kb.yaml").write_text("name: notes\ntypes: {}\n")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="notes", path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "mcp-index.db"),
    )
    server = PyriteMCPServer(config=config, tier="write")

    def call(tool, args):
        return server._dispatch_tool(tool, args)

    # Order matters: create, then update it, then a bulk that collides with it.
    return {
        "kb_create": call(
            "kb_create",
            {"kb_name": "notes", "entry_type": "note", "title": "Hello", "body": "b"},
        ),
        "kb_update": call("kb_update", {"kb_name": "notes", "entry_id": "hello", "body": "c"}),
        "kb_bulk_create": call(
            "kb_bulk_create",
            {
                "kb_name": "notes",
                "entries": [
                    {"entry_type": "note", "title": "Hello"},
                    {"entry_type": "note", "title": "Two"},
                ],
            },
        ),
    }


@pytest.mark.parametrize("page", PAGES)
def test_the_page_runs(page, tmp_path, mcp_results):
    env = _env(tmp_path)
    expect_exit = 0
    expect_text: list[str] = []
    last_output = ""
    ran = 0
    pending: tuple[str, str] | None = None  # (kind, arg) of the next json block

    for kind, a, b, line in items(page):
        where = f"docs/{page}:{line}"
        if kind == "directive":
            if a == "expect-exit":
                expect_exit = int(b)
            elif a == "expect-text":
                expect_text.append(b)
            elif a in ("output-keys", "mcp-output", "orient-operational-contracts"):
                pending = (a, b)
            continue
        lang, body = a, b
        if lang == "bash":
            run = subprocess.run(
                ["bash", "-e", "-c", body],
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = run.stdout + "\n" + run.stderr
            assert run.returncode == expect_exit, (
                f"{where}: exited {run.returncode}, the page says {expect_exit}\n{body}\n{out}"
            )
            for word in expect_text:
                assert word in out, f"{where}: output lacks {word!r}\n{body}\n{out}"
            expect_exit, expect_text = 0, []
            # the JSON the next `output-keys` block is compared to
            last_output = run.stdout + run.stderr
            ran += 1
        elif lang == "json" and pending:
            directive, arg = pending
            pending = None
            documented = json.loads(body)
            if directive == "output-keys":
                real = _last_json(last_output)
            elif directive == "mcp-output":
                assert arg in mcp_results, f"{where}: no MCP call named {arg!r}"
                real = mcp_results[arg]
            else:
                continue  # operational contracts: its own test
            problems = key_shape(documented, real)
            assert not problems, f"{where}: {problems}\nreal: {real}"
    assert ran >= 1, f"docs/{page} has no bash blocks"


def test_operational_contracts_equal_what_orient_returns(tmp_path):
    from pyrite.services.kb_service import KBService

    page = "json-contracts.md"
    quoted = None
    pending = False
    for kind, a, b, line in items(page):
        if kind == "directive" and a == "orient-operational-contracts":
            pending = True
        elif kind == "block" and a == "json" and pending:
            quoted, at = json.loads(b), line
            break
    assert quoted is not None, f"docs/{page}: no operational-contracts block"
    real = KBService._operational_contracts()
    assert quoted == real, (
        f"docs/{page}:{at}: the operational-contracts block differs from "
        f"KBService._operational_contracts(); update the page.\npage: {quoted}\ncode: {real}"
    )


def _format_table():
    text = (DOCS / "json-contracts.md").read_text(encoding="utf-8")
    start = text.index("## `--format` defaults")
    rows = {}
    for n, row in enumerate(text[start:].splitlines()):
        m = re.match(r"\| `([\w ]+)` \| ([^|]+) \| ([^|]+) \|", row)
        if m and m.group(1) != "Command":
            line = text.count("\n", 0, start) + n + 1
            rows[m.group(1)] = (m.group(2).strip(), m.group(3).strip(), line)
    return rows


def _command(path: str):
    import typer.main

    from pyrite.cli import app

    cmd = typer.main.get_command(app)
    for part in path.split():
        cmd = cmd.commands[part]
    return cmd


def test_format_table_matches_the_typer_signatures():
    rows = _format_table()
    assert {"create", "add", "delete", "link", "update", "rename", "import", "task create"} <= set(
        rows
    ), rows
    for command, (flag, default, line) in rows.items():
        where = f"docs/json-contracts.md:{line}"
        options = [o for o in _command(command).params if "--format" in getattr(o, "opts", [])]
        if flag == "none":
            assert not options, f"{where}: `{command}` has --format, the page says none"
        elif flag.startswith("input"):
            assert options, f"{where}: `{command}` has no --format"
        else:
            assert options, f"{where}: `{command}` has no --format, the page says it does"
            want = default.strip("`")
            assert options[0].default == want, (
                f"{where}: `{command}` --format defaults to {options[0].default!r}, "
                f"the page says {want!r}"
            )


def test_kb_not_found_has_one_table_row_in_the_docs():
    rows = []
    for page in sorted(DOCS.glob("*.md")):
        for n, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("|") and "`KB_NOT_FOUND`" in line.split("|")[1]:
                rows.append(f"docs/{page.name}:{n}")
    assert len(rows) == 1, rows


def test_the_pages_claim_no_verification_by_version():
    for page in PAGES:
        text = (DOCS / page).read_text(encoding="utf-8")
        assert not re.search(r"verified (by running )?against", text, re.I), page
