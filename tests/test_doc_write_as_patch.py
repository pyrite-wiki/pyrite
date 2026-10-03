"""Run docs/how-pyrite-edits-your-files.md: each example is a test (ADR-0042).

The doc is what Pyrite promises a user about editing their files. Each example
in it has a ``file`` block (written into a fresh KB), an ``update`` block (sent
through the real MCP ``kb_update`` handler) and a ``diff`` block: the file's
diff afterwards must equal it exactly, and an empty ``diff`` means the file is
byte-identical.

This is phase 1 of ADR-0042: it measures, it changes no behaviour. Examples
today's code does not honour are strict xfails, all in ``KNOWN_DIVERGENCES``
below, each with the observed failure as its reason. The day write-path work
makes one pass, its strict xfail fails CI: delete its line in the same PR.

The doc spells operations the way ADR-0042 does (``set``, ``append``,
``replace``); those interfaces are alpha (#303). ``_to_kb_update_args`` maps
each onto today's ``kb_update`` arguments, here and not in the doc.

Flags on the ``file`` fence (ADR-0042, "Acceptance"):

- ``crlf``: the file is written with CRLF line ends; the expected diff lines
  carry CRLF too.
- ``emitter``: during the update, the YAML emitter is never handed a
  top-level key of the file that the operation did not name.
- ``concurrent``: the ``update`` block is a list; each element is sent by
  its own process, both starting from the same file. A barrier holds each
  writer at its first lock or replace of the file until all have reached it
  (see ``_CONCURRENT_CHILD``), so the race is entered every run, not by luck.
- ``stale``: after the read and before the update, the file is edited by hand
  (a line appended to the body). The read must return ``content_hash``, and
  the update (a whole-document replace, body included) carries it; the write
  must be refused and the file left as the hand edit made it.
- ``noid``: the file has no ``id:``; it lives at ``posts/<slug>.md`` and is
  addressed by that path without ``.md`` (ADR-0042 decision 5). After the
  update, a read by that path must still return it.

Flags on the ``update`` fence: ``echo`` sends a ``kb_get`` result back, with
the block's keys laid over it; ``stale`` repeats the file flag (the ADR puts it on both).

A precondition of a flag's case that does not hold (the file is not CRLF,
the actor the hook resolves is missing, a writer missed the barrier) raises
``CaseNotEnteredError``, which no xfail absorbs: an example fails only for the
divergence it names.

The diff is rendered per line, with ``-`` for a line removed and ``+`` for a
line added; a block of n lines replaced by n lines is shown as n ``-``/``+``
pairs (the ADR's concurrent example), anything else as its removals then its
additions.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "how-pyrite-edits-your-files.md"
ADR = REPO / "kb" / "adrs" / "0042-a-write-changes-what-was-asked-and-nothing-else.md"
KB = "doc-kb"

#: Every flag ADR-0042's Acceptance defines, and where it goes.
FILE_FLAGS = frozenset({"crlf", "emitter", "concurrent", "stale", "noid"})
UPDATE_FLAGS = frozenset({"echo", "stale"})

#: Examples today's code does not honour: slug -> (what it does instead, a
#: pattern the failure message must match). Each is a strict xfail: when the
#: write path honours one, its test XPASSes and CI fails until the line is
#: deleted. If an example fails some other way than the one recorded, the
#: test fails outright, so a reason cannot quietly stop being true.
#: Measured on dev at c097e7f6, 2026-10-03.
KNOWN_DIVERGENCES: dict[str, tuple[str, str]] = {
    "one-field-edit-yields-that-field-s-lines": (
        "principle 3: the whole frontmatter is re-rendered: the header comment, the "
        "title's comment and the blank line go, `no` becomes `\"no\"`, the body's indent is stripped",
        r"diff was:\n-# reviewed by hand, do not reorder\n",
    ),
    "appending-to-a-list-leaves-the-existing-items-alone": (
        "principle 3: kb_update sets `links` aside (`ignored: ['links']`) and writes "
        "nothing; no operation appends to a list",
        r"diff was:\n\(end of diff\)",
    ),
    "a-save-does-not-add-what-a-hook-used-to-add": (
        "principle 4: cascade's resolve_actor_links writes a `links:` block for the "
        "actor into the file on an unrelated title edit",
        r"diff was:\n-title: The hearing\n\+title: The hearing, postponed\n"
        r"\+links:\n\+- target: jane-doe\n",
    ),
    "two-writers-on-different-keys-both-survive": (
        "decision 10: each writer replaces the whole file from its own read; the last "
        "one to replace drops the other's change",
        r"diff was:\n(-summary: one\n\+summary: two\n|-importance: 3\n\+importance: 5\n)\(end of diff\)",
    ),
    "sending-a-read-back-changes-nothing": (
        "principle 3: an echo that changes no field still rewrites the file: CRLF "
        "becomes LF and the header comment is dropped",
        r"diff was:\n----\r\n-# header comment\r\n",
    ),
    "a-stale-whole-document-replace-is-refused": (
        "decisions 9 and 11: kb_get returns no `content_hash`, so a replace cannot "
        "say what it read (kb_update would set the argument aside as `ignored`)",
        r"the read returned no content_hash",
    ),
    "an-id-less-file-keeps-its-identity-when-its-title-changes": (
        "principle 5: no entry `posts/intro`; an id-less file is known by its title's "
        "slug (`intro`), and an update by that id adds `id: intro`",
        r"Entry not found: posts/intro",
    ),
}

#: The KB type whose save hooks an entry type's example needs. The hook that
#: adds links to a `timeline_event` on save is the cascade plugin's
#: `resolve_actor_links`, which runs only in the cascade KB types. ADR-0042
#: says "journalism derivations", but in a journalism-investigation KB no hook
#: touches a timeline_event, and the example passes without entering the case
#: it is about.
KB_TYPE_FOR_ENTRY_TYPE = {"timeline_event": "cascade-timeline"}

#: Entries an example's file refers to, written beside it. The hook only adds
#: a link for an actor it can resolve; without `jane-doe` in the KB, the
#: hook example would pass for lack of an actor, not because the hook wrote
#: nothing.
NEIGHBOURS = {
    "a-save-does-not-add-what-a-hook-used-to-add": {
        "content/jane-doe.md": "---\nid: jane-doe\ntitle: Jane Doe\ntype: actor\n---\n\nShe testifies.\n",
    },
}

# --------------------------------------------------------------------------
# Parsing the doc
# --------------------------------------------------------------------------

_HEADING = re.compile(r"^(?:#{2,3} (?P<h>.+?)|\*\*(?P<b>.+?)\*\*.*)$")
_FENCE_OPEN = re.compile(r"^```(?P<kind>file|update|diff)(?P<flags>(?: [a-z]+)*)\s*$")


@dataclass
class Example:
    title: str
    file: str = ""
    file_flags: frozenset[str] = frozenset()
    update: str = ""
    update_flags: frozenset[str] = frozenset()
    diff: str = ""
    seen: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")


def parse_examples(text: str) -> list[Example]:
    """Every file/update/diff triple, named by the heading above its file block.

    A heading is a ``##``/``###`` line or a paragraph opening with a bold
    phrase (the ADR's form). A block other than file/update/diff is prose.
    """
    examples: list[Example] = []
    title = ""
    lines = text.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        line = lines[i].rstrip("\n")
        m = _FENCE_OPEN.match(line)
        if m:
            body: list[str] = []
            i += 1
            while lines[i].rstrip("\n") != "```":
                body.append(lines[i])
                i += 1
            kind, flags = m["kind"], frozenset(m["flags"].split())
            content = "".join(body)
            if kind == "file":
                examples.append(Example(title=title, file=content, file_flags=flags))
            else:
                if not examples or kind in examples[-1].seen:
                    raise ValueError(f"`{kind}` block with no file block above it: {title!r}")
                setattr(examples[-1], kind, content)
                if kind == "update":
                    examples[-1].update_flags = flags
            examples[-1].seen.append(kind)
        elif line.startswith("```"):
            i += 1
            while not lines[i].startswith("```"):
                i += 1
        else:
            h = _HEADING.match(line)
            if h:
                title = h["h"] or h["b"]
        i += 1
    for ex in examples:
        if ex.seen != ["file", "update", "diff"]:
            raise ValueError(f"example {ex.title!r} has blocks {ex.seen}, not file/update/diff")
    return examples


def _adr_examples() -> list[Example]:
    text = ADR.read_text(encoding="utf-8")
    acceptance = text.split("\n## Acceptance\n", 1)[1].split("\n### ", 1)[0]
    return parse_examples(acceptance)


DOC_EXAMPLES = parse_examples(DOC.read_text(encoding="utf-8")) if DOC.exists() else []

# --------------------------------------------------------------------------
# The KB and the calls
# --------------------------------------------------------------------------


class CaseNotEnteredError(Exception):
    """The run did not enter the case its example is about.

    Not an AssertionError, so no xfail absorbs it: an example may fail only
    for the divergence KNOWN_DIVERGENCES names, never because the harness
    missed the case.
    """


def _require(condition: object, message: str) -> None:
    if not condition:
        raise CaseNotEnteredError(message)


def _front(text: str) -> dict[str, Any]:
    from pyrite.utils.yaml import load_yaml

    return load_yaml(text.replace("\r\n", "\n").split("---\n", 2)[1]) or {}


def _entry_path(ex: Example) -> tuple[str, str]:
    """(path relative to the KB, entry id) for the example's file.

    Not at the KB root: a file there is moved on update today (#488), which
    is not what these examples measure.
    """
    meta = _front(ex.file)
    if "noid" in ex.file_flags:
        slug = re.sub(r"[^a-z0-9]+", "-", str(meta["title"]).lower()).strip("-")
        return f"posts/{slug}.md", f"posts/{slug}"
    return f"content/{meta['id']}.md", str(meta["id"])


def _build_kb(tmp_path: Path, ex: Example) -> tuple[PyriteConfig, Path, str]:
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    kb_type = KB_TYPE_FOR_ENTRY_TYPE.get(str(_front(ex.file).get("type")), "generic")
    (kb_path / "kb.yaml").write_text(f"name: {KB}\nkb_type: {kb_type}\n", encoding="utf-8")
    rel, entry_id = _entry_path(ex)
    path = kb_path / rel
    path.parent.mkdir(parents=True)
    text = ex.file.replace("\n", "\r\n") if "crlf" in ex.file_flags else ex.file
    path.write_bytes(text.encode("utf-8"))
    for rel_n, neighbour in NEIGHBOURS.get(ex.slug, {}).items():
        (kb_path / rel_n).write_text(neighbour, encoding="utf-8")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type=kb_type)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    return config, path, entry_id


def _call(config: PyriteConfig, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(config, tier="write")
    try:
        return server._dispatch_tool(tool, args)
    finally:
        server.close()


def _is_error(result: dict[str, Any]) -> bool:
    return bool(result.get("error") or result.get("isError"))


def _read(config: PyriteConfig, entry_id: str) -> dict[str, Any]:
    got = _call(config, "kb_get", {"kb_name": KB, "entry_id": entry_id})
    assert not _is_error(got), f"kb_get {entry_id!r} failed: {got}"
    return got.get("entry", got)


def _file_body(text: str) -> str:
    return text.replace("\r\n", "\n").split("---\n", 2)[2]


def _to_kb_update_args(
    op: dict[str, Any], config: PyriteConfig, entry_id: str, file_text: str, *, echo: bool
) -> dict[str, Any]:
    """The doc's operation as today's ``kb_update`` arguments.

    - ``set`` names fields, sent as fields.
    - ``append`` has no counterpart today. The item is added to the list as
      the *file* holds it, and the whole list is sent. A read would hand back
      Pyrite's normalised reading of the items (``target_id``/``target_kb``,
      ``since`` dropped), and sending that would rewrite them for a reason
      that is not this example's. Under ADR-0042 decision 2, a set of a whole
      list copies unchanged items byte for byte, so this mapping can pass.
      When kb_update grows an ``append`` operation, send that here instead.
    - ``replace`` is a whole-document write (decision 11): its fields, plus
      the body as the file holds it, plus ``content_hash``.
    """
    args: dict[str, Any] = dict(_read(config, entry_id)) if echo else {}
    for key, value in op.items():
        if key == "set":
            args.update(value)
        elif key == "replace":
            args.update(value)
            args["body"] = _file_body(file_text)
        elif key == "append":
            current = _front(file_text)
            for name, item in value.items():
                args[name] = [*(current.get(name) or []), item]
        elif key == "content_hash":
            args["content_hash"] = value
        else:
            raise ValueError(f"operation {key!r} has no mapping onto kb_update")
    args.update({"kb_name": KB, "entry_id": entry_id})
    return args


def _render_diff(before: str, after: str) -> str:
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        removed, added = a[i1:i2], b[j1:j2]
        if len(removed) == len(added):
            for r, s in zip(removed, added, strict=True):
                out += ["-" + r, "+" + s]
        else:
            out += ["-" + r for r in removed] + ["+" + s for s in added]
    return "".join(x if x.endswith("\n") else x + "\n\\ no newline\n" for x in out)


def _expected_diff(ex: Example) -> str:
    return ex.diff.replace("\n", "\r\n") if "crlf" in ex.file_flags else ex.diff


# --------------------------------------------------------------------------
# Flags that need more than one call
# --------------------------------------------------------------------------

#: Each writer is held at its first step into the critical section, until
#: every writer has reached it: the first exclusive lock it takes (fcntl
#: flock/lockf, msvcrt.locking) or, with no lock, its first replace of the
#: entry file. By then each has read the file it will write from. Today's
#: path takes no lock and reads the file three times before it replaces it,
#: so a barrier at a read would not force the race. A path that locks only
#: around compare-and-replace (ADR-0042 decision 10) meets the barrier before
#: the lock, so it cannot deadlock here. A writer that waits 10 s reports
#: `timed_out`, and the test then fails as not entered, never as an xpass.
_CONCURRENT_CHILD = textwrap.dedent(
    """
    import json, os, sys, time
    from pathlib import Path
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.server.mcp_server import PyriteMCPServer

    spec = json.loads(sys.argv[1])
    target = os.path.realpath(spec["target"])
    barrier = Path(spec["barrier"])
    state = {"reached": None, "timed_out": False}

    def wait(where):
        if state["reached"]:
            return
        state["reached"] = where
        (barrier / str(os.getpid())).touch()
        deadline = time.monotonic() + 10
        while len(list(barrier.iterdir())) < spec["writers"]:
            if time.monotonic() > deadline:
                state["timed_out"] = True
                return
            time.sleep(0.01)

    real_replace = os.replace
    def replace(src, dst, *a, **kw):
        if os.path.realpath(dst) == target:
            wait("replace")
        return real_replace(src, dst, *a, **kw)
    os.replace = replace

    try:
        import fcntl
        for name in ("flock", "lockf"):
            real = getattr(fcntl, name)
            def locked(fd, op, *a, _real=real, _name=name, **kw):
                if op & fcntl.LOCK_EX:
                    wait(_name)
                return _real(fd, op, *a, **kw)
            setattr(fcntl, name, locked)
    except ImportError:
        import msvcrt
        real_locking = msvcrt.locking
        def locking(fd, mode, n):
            if mode in (msvcrt.LK_LOCK, msvcrt.LK_NBLCK):
                wait("msvcrt.locking")
            return real_locking(fd, mode, n)
        msvcrt.locking = locking

    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=spec["kb"], path=Path(spec["kb_path"]), kb_type=spec["kb_type"])],
        settings=Settings(index_path=Path(spec["index"])),
    )
    server = PyriteMCPServer(config, tier="write")
    try:
        result = server._dispatch_tool("kb_update", spec["args"])
    finally:
        server.close()
    print(json.dumps({"result": result, **state}, default=str))
    """
)


def _run_concurrent(
    tmp_path: Path, config: PyriteConfig, path: Path, ops: list[dict[str, Any]], entry_id: str
) -> list[dict[str, Any]]:
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    kb = config.knowledge_bases[0]
    procs = []
    for op in ops:
        spec = {
            "target": str(path),
            "barrier": str(barrier),
            "writers": len(ops),
            "kb": KB,
            "kb_path": str(kb.path),
            "kb_type": kb.kb_type,
            "index": str(config.settings.index_path),
            "args": _to_kb_update_args(op, config, entry_id, path.read_text(), echo=False),
        }
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", _CONCURRENT_CHILD, json.dumps(spec)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONWARNINGS": "ignore"},
            )
        )
    reports = []
    for proc in procs:
        out, err = proc.communicate(timeout=120)
        _require(proc.returncode == 0, f"writer exited {proc.returncode}: {err[-2000:]}")
        reports.append(json.loads(out.strip().splitlines()[-1]))
    for report in reports:
        _require(
            report["reached"] and not report["timed_out"],
            f"a writer {'timed out at' if report['timed_out'] else 'never reached'} the barrier "
            f"({report['reached']}), so the writers did not start from the same file; if the "
            "write path neither locks nor replaces the entry file, move the barrier",
        )
    return [report["result"] for report in reports]


def _spy_emitter(monkeypatch: pytest.MonkeyPatch) -> list[set[str]]:
    """Record the top-level keys of every mapping handed to a YAML emitter."""
    import yaml as pyyaml
    from ruamel.yaml import YAML

    emitted: list[set[str]] = []

    def record(data: Any) -> None:
        if hasattr(data, "keys"):
            emitted.append({str(k) for k in data.keys()})

    real_dump = YAML.dump

    def ruamel_dump(self, data, stream=None, **kw):
        record(data)
        return real_dump(self, data, stream, **kw)

    monkeypatch.setattr(YAML, "dump", ruamel_dump)
    for name in ("dump", "safe_dump"):
        real = getattr(pyyaml, name)

        def py_dump(data, *a, _real=real, **kw):
            record(data)
            return _real(data, *a, **kw)

        monkeypatch.setattr(pyyaml, name, py_dump)
    return emitted


# --------------------------------------------------------------------------
# The examples
# --------------------------------------------------------------------------


def _require_entered(ex: Example, config: PyriteConfig, path: Path, entry_id: str) -> None:
    """What makes each flag's case the case, checked before the write."""
    raw = path.read_bytes()
    meta = _front(ex.file)
    if "crlf" in ex.file_flags:
        _require(raw.count(b"\r\n") == raw.count(b"\n"), "the crlf file is not all CRLF")
    if "noid" in ex.file_flags:
        _require("id" not in meta, "the noid file has an id: line")
    if meta.get("type") == "timeline_event":
        from pyrite.plugins.registry import get_registry

        kb_type = config.knowledge_bases[0].kb_type
        hooks = get_registry().get_hooks_for_kb(kb_type).get("before_save", [])
        _require(
            any(h.__name__ == "resolve_actor_links" for h in hooks),
            f"no resolve_actor_links before_save hook in a {kb_type} KB",
        )
        for ref in meta.get("actors", []):
            actor = ref.strip("[]")
            got = _call(config, "kb_get", {"kb_name": KB, "entry_id": actor})
            _require(
                not _is_error(got),
                f"actor {actor} is not in the KB, so the hook has nothing to add",
            )


def _check(ex: Example, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, path, entry_id = _build_kb(tmp_path, ex)
    op = json.loads(ex.update)
    echo = "echo" in ex.update_flags
    _require_entered(ex, config, path, entry_id)

    if "concurrent" in ex.file_flags:
        before = path.read_bytes().decode("utf-8")
        results = _run_concurrent(tmp_path, config, path, op, entry_id)
        for r in results:
            assert not _is_error(r), f"a concurrent writer was refused: {r}"
    elif "stale" in ex.file_flags:
        file_at_read = path.read_text()
        read = _read(config, entry_id)
        # A divergence, not a harness miss: decision 9 says a read returns
        # content_hash. Without one, nothing can be stale.
        assert read.get("content_hash"), "the read returned no content_hash"
        op = {**op, "content_hash": read["content_hash"]}
        args = _to_kb_update_args(op, config, entry_id, file_at_read, echo=echo)
        with path.open("ab") as fh:
            fh.write(b"Edited by hand after the read.\n")
        before = path.read_bytes().decode("utf-8")
        _require(before != file_at_read, "the hand edit did not change the file")
        result = _call(config, "kb_update", args)
        assert _is_error(result), (
            "a stale replace was written, not refused; diff:\n"
            + _render_diff(before, path.read_bytes().decode("utf-8"))
        )
    else:
        args = _to_kb_update_args(op, config, entry_id, path.read_text(), echo=echo)
        before = path.read_bytes().decode("utf-8")
        for name in op.get("append", {}):
            _require(
                args[name][:-1] == _front(before).get(name, []),
                f"the {name} sent are not the file's own items, so the append is not "
                "what was measured",
            )
        emitted = _spy_emitter(monkeypatch) if "emitter" in ex.file_flags else None
        result = _call(config, "kb_update", args)
        if emitted is not None:
            monkeypatch.undo()
        assert not _is_error(result), f"kb_update refused: {result}"
        if emitted is not None:
            asked = set(args) - {"kb_name", "entry_id"}
            untouched = set(_front(before)) - asked
            leaked = sorted(set().union(*emitted) & untouched) if emitted else []
            assert not leaked, f"the emitter was handed keys nobody asked to change: {leaked}"

    assert path.exists(), (
        f"the file is gone; KB now holds {sorted(map(str, path.parent.parent.rglob('*.md')))}"
    )
    got = _render_diff(before, path.read_bytes().decode("utf-8"))
    expected = _expected_diff(ex)
    if got != expected:
        # Raised, not asserted: pytest's rewrite would indent the message, and
        # KNOWN_DIVERGENCES' patterns match the diff as rendered.
        raise AssertionError(
            f"diff was:\n{got}(end of diff)\nthe doc says:\n{expected}(end of diff)"
        )
    if "noid" in ex.file_flags:
        _read(config, entry_id)


def _param(ex: Example):
    known = KNOWN_DIVERGENCES.get(ex.slug)
    marks = (
        [pytest.mark.xfail(strict=True, raises=AssertionError, reason=known[0])] if known else []
    )
    return pytest.param(ex, id=ex.slug, marks=marks)


@pytest.mark.parametrize("ex", [_param(ex) for ex in DOC_EXAMPLES])
def test_doc_example(ex: Example, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    known = KNOWN_DIVERGENCES.get(ex.slug)
    try:
        _check(ex, tmp_path, monkeypatch)
    except AssertionError as e:
        if known and not re.search(known[1], str(e)):
            # Not an AssertionError, so the xfail does not absorb it.
            pytest.fail(f"failed, but not as KNOWN_DIVERGENCES records ({known[0]}):\n{e}")
        raise


# --------------------------------------------------------------------------
# The doc, the ADR and the list agree
# --------------------------------------------------------------------------


def test_the_doc_holds_the_adrs_examples_verbatim() -> None:
    """The doc's examples are ADR-0042's, block for block, in order."""
    assert DOC.exists(), f"{DOC.relative_to(REPO)} is missing"
    adr = [(e.file, e.file_flags, e.update, e.update_flags, e.diff) for e in _adr_examples()]
    doc = [(e.file, e.file_flags, e.update, e.update_flags, e.diff) for e in DOC_EXAMPLES]
    assert len(adr) == 7
    assert doc[: len(adr)] == adr


def test_every_example_has_a_test_and_every_divergence_an_example() -> None:
    slugs = [e.slug for e in DOC_EXAMPLES]
    assert slugs and len(slugs) == len(set(slugs)), f"examples need distinct headings: {slugs}"
    assert all(slugs), "an example has no heading above its file block"
    stale = sorted(set(KNOWN_DIVERGENCES) - set(slugs))
    assert not stale, f"KNOWN_DIVERGENCES names examples the doc lacks: {stale}"


def test_every_flag_in_the_doc_is_one_the_runner_handles() -> None:
    for ex in DOC_EXAMPLES:
        assert ex.file_flags <= FILE_FLAGS, (ex.slug, ex.file_flags - FILE_FLAGS)
        assert ex.update_flags <= UPDATE_FLAGS, (ex.slug, ex.update_flags - UPDATE_FLAGS)


def test_parser_reads_both_heading_forms_and_flags() -> None:
    text = (
        "## A heading\n\n```file crlf noid\nF\n```\n```update echo\n{}\n```\n```diff\n```\n\n"
        "**Bold heading** and prose.\n\n```bash\nnot an example\n```\n"
        "```file\nG\n```\n```update\n[]\n```\n```diff\n-a\n+b\n```\n"
    )
    a, b = parse_examples(text)
    assert (a.slug, a.file, a.file_flags, a.update_flags, a.diff) == (
        "a-heading",
        "F\n",
        frozenset({"crlf", "noid"}),
        frozenset({"echo"}),
        "",
    )
    assert (b.slug, b.update, b.diff) == ("bold-heading", "[]\n", "-a\n+b\n")


def test_diff_rendering_pairs_equal_replacements() -> None:
    assert _render_diff("a\nb\nc\n", "A\nB\nc\n") == "-a\n+A\n-b\n+B\n"
    assert _render_diff("a\n", "a\nb\nc\n") == "+b\n+c\n"
    assert _render_diff("x\r\n", "x\r\n") == ""


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="principle 3: a one-field edit hands the emitter flag, id, links, tags and type "
    "(the list is pinned as observed; a narrower leak fails outright)",
)
def test_emitter_flag_on_the_one_field_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `emitter` flag is wired. No ADR example carries it yet, so this
    runs the one-field edit with it: today the whole frontmatter goes to the
    emitter. When the write path honours principle 3 this XPASSes; then
    delete the mark, or put `emitter` on the doc's example.
    """
    one_field = DOC_EXAMPLES[0]
    ex = Example(
        title=one_field.title,
        file=one_field.file,
        file_flags=one_field.file_flags | {"emitter"},
        update=one_field.update,
        diff=one_field.diff,
    )
    try:
        _check(ex, tmp_path, monkeypatch)
    except AssertionError as e:
        if (
            "emitter was handed keys nobody asked to change: ['flag', 'id', 'links', 'tags', 'type']"
            not in str(e)
        ):
            pytest.fail(f"failed, but not by the emitter check:\n{e}")
        raise
