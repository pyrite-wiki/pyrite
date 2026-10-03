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
  top-level key of the file that the operation did not name. Controls: the
  spy sees a create's frontmatter, and if the update itself ran any YAML emit
  (counted below the spy, at ``Emitter.emit``) the spy must have recorded it.
- ``concurrent``: the ``update`` block is a list; each element is sent by
  its own process, both starting from the same file. A barrier holds each
  writer at its first lock or replace of the file until all have reached it
  (see ``_CONCURRENT_CHILD``), so the race is entered every run, not by luck.
- ``stale``: after the read and before the update, the file is edited by hand
  (a line appended to the body). The read must return ``content_hash``, and
  the update (a whole-document replace, body included) carries it; the write
  must be refused and the file left as the hand edit made it. Two controls
  make the refusal mean "the hash is stale": a twin KB where the same replace
  with no hand edit is written, and, in the same KB after the refusal, a
  re-read and a replace with that re-read's hash, which must be written.
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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml

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

#: (See ``_require_actor_link_derived`` for why this is still a cascade KB:
#: no type derives links from `actors` yet.)
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
#: the lock, so it cannot deadlock here. A writer still waiting when the
#: group's one 60 s deadline passes reports `timed_out`, and the test then
#: fails as not entered, never as an xpass.
_CONCURRENT_CHILD = textwrap.dedent(
    """
    import json, os, sys, time
    from pathlib import Path
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.server.mcp_server import PyriteMCPServer

    spec = json.loads(sys.argv[1])
    target = os.path.realpath(spec["target"])
    state = {"started": False, "reached": None, "timed_out": None}

    def rendezvous(name):
        # One wall-clock deadline for the whole group, set by the parent:
        # a writer that starts late cannot run its own clock out.
        d = Path(spec[name])
        (d / str(os.getpid())).touch()
        while len(list(d.iterdir())) < spec["writers"]:
            if time.time() > spec["deadline"]:
                state["timed_out"] = state["timed_out"] or name
                return
            time.sleep(0.01)

    def wait(where):
        if state["reached"]:
            return
        state["reached"] = where
        rendezvous("critical")

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
        # Start barrier: every writer is loaded before any begins its update.
        rendezvous("start")
        state["started"] = True
        result = server._dispatch_tool("kb_update", spec["args"])
    finally:
        server.close()
    print(json.dumps({"result": result, **state}, default=str))
    """
)


def _run_concurrent(
    tmp_path: Path, config: PyriteConfig, path: Path, ops: list[dict[str, Any]], entry_id: str
) -> list[dict[str, Any]]:
    for name in ("start", "critical"):
        (tmp_path / name).mkdir()
    deadline = time.time() + 60
    kb = config.knowledge_bases[0]
    procs = []
    for op in ops:
        spec = {
            "target": str(path),
            "start": str(tmp_path / "start"),
            "critical": str(tmp_path / "critical"),
            "deadline": deadline,
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
        out, err = proc.communicate(timeout=max(1.0, deadline - time.time()) + 30)
        _require(proc.returncode == 0, f"writer exited {proc.returncode}: {err[-2000:]}")
        reports.append(json.loads(out.strip().splitlines()[-1]))
    for report in reports:
        _require(
            report["started"] and report["reached"] and not report["timed_out"],
            f"a writer timed out at the {report['timed_out']} barrier or never reached the "
            f"critical one ({report['reached']}), so the writers did not both write from the "
            "file as it was; if the write path neither locks nor replaces the entry file "
            "(ADR-0042 decision 10 does both), move the barrier",
        )
    return [report["result"] for report in reports]


class EmitterWatch:
    """What the YAML emitters were handed: the top-level keys of each document.

    ``docs`` has one set per emitted document; ``unwatched`` names every door
    the watch cannot see through that was opened.
    """

    def __init__(self) -> None:
        self.docs: list[set[str]] = []
        self.unwatched: list[str] = []

    def keys(self) -> set[str]:
        return set().union(*self.docs) if self.docs else set()

    def clear(self) -> None:
        self.docs.clear()
        self.unwatched.clear()


def _watch_emitter(
    monkeypatch: pytest.MonkeyPatch, watch: tuple[str, ...] = ("ruamel", "pyyaml")
) -> EmitterWatch:
    """Record the top-level keys of every document either library's emitter writes.

    It watches ``Emitter.emit``, the call every Python-side dump ends in
    (``dump``, ``dump_all``, ``safe_dump``, a ``safe_dump`` bound at import, a
    ``Dumper`` used directly), and rebuilds the top-level keys from the event
    stream: a scalar at depth 1 in an even position of the root mapping is a
    key. A scalar value emitted alone has no top-level keys and is not a leak;
    a document that carries the whole frontmatter is.

    Limits (#741): any mapping emitted as the root of its own document has its
    keys counted as top-level, so a correct append of a mapping item, or a
    nested set that emits its parent's value, whose keys repeat a top-level key
    reads as a leak; and only depth-1 keys of a root mapping are seen, so
    untouched values re-emitted one by one, or a frontmatter wrapped in a list,
    are not caught here (the diff check still holds the file's bytes).

    ``watch`` narrows which library is watched, so a test can point the watch
    at the wrong one on purpose.

    Not seen through, and counted in ``unwatched`` so a run that opens one
    fails as not entered: PyYAML's C dumpers (``CDumper``, ``CSafeDumper``,
    ``CBaseDumper``, which use libyaml's emitter and never call
    ``Emitter.emit``) and a ruamel ``YAML`` whose ``Emitter`` is not the
    pure-Python class. Pyrite uses neither today (``pyrite/utils/yaml.py``
    dumps through ruamel's round-trip emitter). Any other way of writing
    frontmatter (hand-built strings, ``json``) is not an emitter and is not
    watched: the diff check is what catches it.
    """
    import yaml.emitter as py_emitter
    from ruamel.yaml import YAML
    from ruamel.yaml import emitter as ru_emitter

    result = EmitterWatch()
    state: dict[int, dict[str, Any]] = {}

    def observe(emitter: Any, event: Any) -> None:
        name = type(event).__name__
        st = state.setdefault(id(emitter), {"stack": [], "keys": set()})
        stack: list[list[Any]] = st["stack"]
        if name == "DocumentStartEvent":
            stack.clear()
            st["keys"] = set()
        elif name == "DocumentEndEvent":
            if st["keys"]:
                result.docs.append(st["keys"])
            st["keys"] = set()
        elif name in ("MappingEndEvent", "SequenceEndEvent"):
            if stack:
                stack.pop()
        elif name in ("ScalarEvent", "AliasEvent", "MappingStartEvent", "SequenceStartEvent"):
            if stack and stack[-1][0] == "map":
                index = stack[-1][1]
                stack[-1][1] += 1
                if len(stack) == 1 and index % 2 == 0 and name == "ScalarEvent":
                    st["keys"].add(str(event.value))
            if name == "MappingStartEvent":
                stack.append(["map", 0])
            elif name == "SequenceStartEvent":
                stack.append(["seq", 0])

    libraries = {"pyyaml": py_emitter.Emitter, "ruamel": ru_emitter.Emitter}
    for lib in watch:
        cls = libraries[lib]
        real = cls.emit

        def emit(self, event, _real=real):
            observe(self, event)
            return _real(self, event)

        monkeypatch.setattr(cls, "emit", emit)

    try:
        from yaml import cyaml
    except ImportError:  # PyYAML without libyaml cannot open the C door
        cyaml = None
    if cyaml is not None:
        for name in ("CBaseDumper", "CDumper", "CSafeDumper"):
            dumper = getattr(cyaml, name)
            real_init = dumper.__init__

            def init(self, *a, _real=real_init, _name=name, **kw):
                result.unwatched.append(f"yaml.{_name}")
                return _real(self, *a, **kw)

            monkeypatch.setattr(dumper, "__init__", init)
    real_get = YAML.get_serializer_representer_emitter

    def get_emitter(self, *a, **kw):
        if not (isinstance(self.Emitter, type) and issubclass(self.Emitter, ru_emitter.Emitter)):
            result.unwatched.append(f"ruamel Emitter {self.Emitter!r}")
        return real_get(self, *a, **kw)

    monkeypatch.setattr(YAML, "get_serializer_representer_emitter", get_emitter)
    return result


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
    for actor in _actors(meta):
        got = _call(config, "kb_get", {"kb_name": KB, "entry_id": actor})
        _require(not _is_error(got), f"actor {actor} is not in the KB, so nothing links to it")


def _actors(meta: dict[str, Any]) -> list[str]:
    return [str(ref).strip("[]") for ref in meta.get("actors", [])]


def _link_items(value: Any) -> list[dict[str, Any]]:
    """Every mapping in a nested result that carries a link relation."""
    if isinstance(value, dict):
        here = [value] if "relation" in value else []
        return here + [i for v in value.values() for i in _link_items(v)]
    if isinstance(value, (list, tuple)):
        return [i for v in value for i in _link_items(v)]
    return []


def _is_link_to(value: Any, target: str) -> bool:
    """A link (a mapping with a ``relation``) one of whose values is ``target``.

    A derived LINK, not a derived value: ADR-0042 decision 9 also puts
    normalised field values under the derived key (``actors`` as the type
    reads it), and that the file names an actor is not that something linked
    to it.
    """
    return any(
        isinstance(v, str) and v.strip("[]") == target
        for item in _link_items(value)
        for v in item.values()
    )


def _derived_link_seen(
    config: PyriteConfig, actor: str, entry_id: str, *, call: Any = None
) -> str | None:
    """How the actor link is visible, or None.

    - ``kb_backlinks`` on the actor lists the entry. That is the index's row.
      Today the row exists because the cascade hook wrote a `links:` block that
      was then indexed; under ADR-0042 decision 4 the index derives it.
    - ``kb_get`` on the entry or on the actor returns a link (a mapping with a
      ``relation``) under a key whose name says it is derived. Which sub-key
      holds it is not fixed yet, so any link under such a key counts, and
      nothing that is not a link does.

    Not read: the file's own `links:` block, as ``kb_get`` returns it under
    ``links``. That the file says so is what the example measures.
    """
    call = call or _call
    got = call(config, "kb_backlinks", {"kb_name": KB, "entry_id": actor})
    if entry_id in [b.get("id") for b in got.get("backlinks", [])]:
        return "kb_backlinks"
    for read_id, wanted in ((entry_id, actor), (actor, entry_id)):
        read = call(config, "kb_get", {"kb_name": KB, "entry_id": read_id})
        read = read.get("entry", read)
        for key, value in read.items():
            if "derived" in key.lower() and _is_link_to(value, wanted):
                return f"kb_get[{key!r}]"
    return None


def _require_actor_link_derived(
    config: PyriteConfig, meta: dict[str, Any], entry_id: str, *, call: Any = None
) -> None:
    """After the save, something other than the file shows the actor link.

    What rules this out: an empty diff that proves nothing about hooks because
    the KB derives no link to the actor at all (a type with no actor
    handling, an actor the lookup cannot resolve). It must not rule out a
    write path that is correct: one that writes no hook links into the file
    and has the index derive them (ADR-0042 decision 4). So the link is read
    from `kb_backlinks` or as a link under the derived key of a read, never
    from the file's `links:` block as a read returns it, and no KB type is
    named here. A derived field value (the normalised `actors`) is not a link
    and does not count.

    Limit, stated because it is real: no KB type derives links from `actors`
    at index time yet (the index derives them from body wikilinks and
    `references:`, not from `actors`; ADR-0045's reference fields are not
    built). Until one does, the only link a save leaves is the one the
    cascade hook writes into the file, which the index then reads back, so
    the hook example still needs the cascade KB type
    (``KB_TYPE_FOR_ENTRY_TYPE``). When a derived-link type exists, put it
    there; this check needs no change.
    """
    for actor in _actors(meta):
        seen = _derived_link_seen(config, actor, entry_id, call=call)
        _require(
            seen,
            f"{actor} shows no link from {entry_id} after the save, neither in "
            "kb_backlinks nor under a derived key of kb_get, so nothing derived an actor link "
            "and the example did not enter its case",
        )


def _whole_document(op: dict[str, Any], file_text: str) -> dict[str, Any]:
    """The op with every key the file holds carried in ``replace``.

    ADR-0042 decision 11: a whole-document replace says every key it keeps.
    The example's own ``replace`` names only title and status, so a replace
    that is actually written would, under that decision, unset ``id`` and
    ``type``. The controls that need the write to happen carry the whole
    document, so a correct path is not asked to damage the file. The stale
    replace itself keeps the doc's wording: it is refused, so nothing is
    written from it (limit: this does not test what a replace with omitted
    keys does).
    """
    return {**op, "replace": {**_front(file_text), **op["replace"]}}


def _require_fresh_replace_is_written(
    ex: Example, base: Path, op: dict[str, Any], *, echo: bool
) -> None:
    """Control for `stale`: the same replace, with no hand edit, is written.

    Run in a second KB built from the same file. What it rules out: a write
    path that refuses every whole-document replace, which would make the
    refusal of the stale one say nothing about staleness.
    """
    base.mkdir()
    config, path, entry_id = _build_kb(base, ex)
    file_text = path.read_text()
    read = _read(config, entry_id)
    fresh = _whole_document({**op, "content_hash": read.get("content_hash")}, file_text)
    result = _call(
        config, "kb_update", _to_kb_update_args(fresh, config, entry_id, file_text, echo=echo)
    )
    _require_replace_landed(
        result,
        path,
        fresh,
        "the same replace with no hand edit was not written as asked",
        "a refusal of the stale one would not show that staleness was detected",
    )


def _require_replace_landed(
    result: dict[str, Any], path: Path, op: dict[str, Any], what: str, why: str
) -> None:
    on_disk = yaml.safe_load(path.read_text().split("---\n", 2)[1]) or {}
    landed = all(on_disk.get(k) == v for k, v in op["replace"].items())
    _require(
        not _is_error(result) and landed,
        f"{what} ({result}; file now {on_disk}), so {why}",
    )


def _require_old_hash_still_refused(
    config: PyriteConfig, entry_id: str, args: dict[str, Any]
) -> None:
    """After the hand edit AND a read by anyone, a replace with the OLD hash is refused.

    What it separates: a write path that checks the hash the replace carries
    from one that refuses whenever the file differs from the last read (or
    since indexing) and ignores the carried hash. After a re-read the second
    would accept the old hash's replace. Raised as a failure, not as a miss:
    writing a replace made from a stale hash is the behaviour the example
    forbids.
    """
    _read(config, entry_id)
    result = _call(config, "kb_update", args)
    assert _is_error(result), (
        "a replace carrying the hash from before the hand edit was written after the "
        f"entry was read again; the carried hash is not what decides: {result}"
    )


def _require_reread_replace_is_written(
    ex: Example, config: PyriteConfig, path: Path, entry_id: str, op: dict[str, Any], *, echo: bool
) -> None:
    """Control for `stale`, in the SAME KB and after the hand edit.

    Re-read the entry now, replace with the hash of that re-read, expect the
    write to happen. What it rules out: a refusal that is not about the hash
    the replace carried. The twin KB control shows a fresh-hash replace is
    written when nothing changed; it cannot show that the write path accepts
    a fresh hash on a file that was edited after it was indexed. Without this
    control, a path that refuses any file changed since indexing would pass
    the stale example for the wrong reason. (Paired with
    ``_require_old_hash_still_refused``, which rules out the reverse: a path
    that ignores the hash and accepts after any re-read.)
    """
    file_text = path.read_text()
    read = _read(config, entry_id)
    _require(read.get("content_hash"), "the re-read after the hand edit returned no content_hash")
    op = _whole_document({**op, "content_hash": read["content_hash"]}, file_text)
    result = _call(
        config, "kb_update", _to_kb_update_args(op, config, entry_id, file_text, echo=echo)
    )
    _require_replace_landed(
        result,
        path,
        op,
        "a replace carrying the hash of a re-read after the hand edit was not written",
        "the refusal of the stale one may be about the changed file, not the stale hash",
    )


def _check(
    ex: Example,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    spy_watch: tuple[str, ...] = ("ruamel", "pyyaml"),
) -> None:
    config, path, entry_id = _build_kb(tmp_path, ex)
    op = json.loads(ex.update)
    echo = "echo" in ex.update_flags
    _require_entered(ex, config, path, entry_id)

    meta = _front(ex.file)
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
        _require_fresh_replace_is_written(ex, tmp_path / "control", op, echo=echo)
        with path.open("ab") as fh:
            fh.write(b"Edited by hand after the read.\n")
        before = path.read_bytes().decode("utf-8")
        _require(before != file_at_read, "the hand edit did not change the file")
        result = _call(config, "kb_update", args)
        assert _is_error(result), (
            "a stale replace was written, not refused; diff:\n"
            + _render_diff(before, path.read_bytes().decode("utf-8"))
        )
        # Controls, same KB: the refusal is about the hash carried. The second
        # control writes the file, so the bytes the refusal left are put back
        # for the diff.
        _require_old_hash_still_refused(config, entry_id, args)
        refused = path.read_bytes()
        _require_reread_replace_is_written(ex, config, path, entry_id, op, echo=echo)
        path.write_bytes(refused)
    else:
        args = _to_kb_update_args(op, config, entry_id, path.read_text(), echo=echo)
        before = path.read_bytes().decode("utf-8")
        for name in op.get("append", {}):
            # Against PyYAML's parse of the bytes on disk, not the parse that
            # built the arguments: a mapping that sent Pyrite's reading of the
            # items (normalised keys, `since` dropped) fails here.
            on_disk = yaml.safe_load(before.split("---\n", 2)[1]) or {}
            _require(
                args[name][:-1] == on_disk.get(name, []),
                f"the {name} sent are not the file's own items, so the append is not "
                "what was measured",
            )
        with pytest.MonkeyPatch.context() as spy_patch:
            watched = _watch_emitter(spy_patch, spy_watch) if "emitter" in ex.file_flags else None
            if watched is not None:
                # Control 1: a create has to emit frontmatter (ADR-0042 decision
                # 13), so the watch must see it.
                _call(
                    config, "kb_create", {"kb_name": KB, "entry_type": "note", "title": "Spy probe"}
                )
                _require(
                    watched.docs,
                    "the emitter watch saw no document during a create; it is not watching "
                    "the emitter Pyrite uses, so an empty record would prove nothing",
                )
                watched.clear()
            result = _call(config, "kb_update", args)
        if watched is not None:
            # Control 2: the update did not go through a door the watch cannot
            # see (a C dumper). What it judges is the one question the flag
            # asks, whether a key nobody asked to change was emitted. It does
            # not ask whether a mapping was seen: an update that emits one
            # value (a quoted scalar, a list item in its neighbours' style)
            # emits no top-level key and is correct.
            _require(
                not watched.unwatched,
                f"the update opened an emitter the watch cannot see ({watched.unwatched}), "
                "so an empty leak list would prove nothing",
            )
        assert not _is_error(result), f"kb_update refused: {result}"
        if watched is not None:
            asked = set(args) - {"kb_name", "entry_id"}
            untouched = set(_front(before)) - asked
            leaked = sorted(watched.keys() & untouched)
            assert not leaked, f"the emitter was handed keys nobody asked to change: {leaked}"

    if _actors(meta):
        _require_actor_link_derived(config, meta, entry_id)
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


# --------------------------------------------------------------------------
# The harness against write paths that are right and ones that are wrong
# --------------------------------------------------------------------------
#
# The examples above run against today's write path, which diverges, so most
# of the harness's controls are never reached by a passing run. These tests
# run `_check` against ModelWritePath, a stand-in for the write path ADR-0042
# describes (a splice of the named fields; a hash compare; links derived,
# never written), and against variants that are wrong in one way each. The
# property (#703): a write example's verdict depends only on the behaviour it
# names. A correct path is never hard-failed (no CaseNotEnteredError); an
# incorrect one never satisfies a precondition or a control vacuously.
#
# Every precondition, control and spy in this module, the one behaviour it
# separates, and where each side is shown:
#
# - crlf / noid / actor-in-KB (`_require_entered`): the harness's own fixture
#   bytes and neighbours, checked before the write. Safe by reading: they do
#   not depend on the write path.
# - append items (`_check`, the `append` loop): the arguments the harness
#   built. Safe by reading: harness-side.
# - barrier (`_run_concurrent`): both writers reach the critical section at
#   once. A path that neither locks nor replaces the file times out as not
#   entered; it cannot pass. Safe by reading.
# - twin control (`_require_fresh_replace_is_written`): a path that refuses
#   every replace. Rejected by test_twin_control_catches_a_path_that_refuses_every_replace;
#   accepted: the correct model.
# - same-KB re-read control: a path that refuses a file changed since indexing.
#   Rejected by test_stale_control_in_the_same_kb_...; accepted: the model.
# - old-hash control (`_require_old_hash_still_refused`): a path that ignores
#   the carried hash and refuses only when the file differs from the last
#   read. Rejected by test_stale_example_fails_for_a_path_that_checks_the_last_read_not_the_carried_hash;
#   accepted: the model.
# - emitter create control: a watch that sees nothing of Pyrite's writes.
#   Rejected by test_emitter_watch_pointed_at_pyyaml_alone_...; accepted: the
#   one-field example under today's path (xfail test above).
# - emitter unwatched-door control: an update through a C dumper. Rejected by
#   test_emitter_control_fails_when_the_update_uses_a_door_the_watch_cannot_see;
#   accepted: the model, and a splice that emits a scalar.
# - emitter leak check: a key nobody asked to change at depth 1 of a root
#   mapping handed to a watched emitter, including through an import-bound
#   `safe_dump` (limits in `_watch_emitter`'s docstring, #741). Rejected by
#   test_emitter_leak_is_seen_through_an_import_bound_dump; accepted by
#   test_emitter_control_passes_a_splice_that_emits_a_scalar.
# - hook precondition (`_require_actor_link_derived`): a derived LINK, not a
#   derived value and not the file's own `links:` block. Accepted: the model
#   (derived link) and the real backlinks path; rejected: the normalised
#   echo, the unindexed `links:` block, nothing at all.


class ModelWritePath:
    """Replaces ``_call``: reads and the create go to the real server, the update does not.

    ``refuse_if_changed_since_indexed`` is the wrong reason to refuse a stale
    replace; ``ignore_hash`` is a path that does not check it at all;
    ``refuse_all_replaces`` never writes a replace. ``derived`` is what a read
    returns under the derived key: a link (the correct shape) or only the
    normalised field value (a read that derived no link).
    """

    def __init__(
        self,
        *,
        derived: str = "links",
        refuse_if_changed_since_indexed: bool = False,
        ignore_hash: bool = False,
        refuse_all_replaces: bool = False,
    ) -> None:
        self.real = _call
        self.derived = derived
        self.refuse_if_changed_since_indexed = refuse_if_changed_since_indexed
        self.ignore_hash = ignore_hash
        self.refuse_all_replaces = refuse_all_replaces
        self.first_seen: dict[Path, str] = {}
        self.last_read: dict[Path, str] = {}

    @staticmethod
    def _digest(path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _file(self, config: PyriteConfig, entry_id: str) -> Path | None:
        got = self.real(config, "kb_get", {"kb_name": KB, "entry_id": entry_id})
        entry = got.get("entry", got)
        if not entry.get("file_path"):
            return None
        path = Path(entry["file_path"])
        return path if path.is_absolute() else config.knowledge_bases[0].path / path

    def __call__(self, config: PyriteConfig, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool == "kb_get":
            got = self.real(config, tool, args)
            entry = dict(got.get("entry", got))
            path = self._file(config, args["entry_id"])
            if path is not None:
                self.first_seen.setdefault(path, self._digest(path))
                self.last_read[path] = self._digest(path)
                entry["content_hash"] = self._digest(path)
            entry["derived"] = self._derived(config, args["entry_id"])
            return {"entry": entry} if "entry" in got else entry
        if tool == "kb_update":
            return self._update(config, args)
        return self.real(config, tool, args)

    def _derived(self, config: PyriteConfig, entry_id: str) -> dict[str, Any]:
        """Links (outgoing from this entry, incoming to it) derived from the files."""
        if self.derived == "none":
            return {}
        outgoing: list[str] = []
        incoming: list[str] = []
        for path in sorted(config.knowledge_bases[0].path.rglob("*.md")):
            meta = _front(path.read_text())
            actors = [str(r).strip("[]") for r in meta.get("actors", [])]
            if meta.get("id") == entry_id:
                outgoing = actors
            if entry_id in actors:
                incoming.append(str(meta["id"]))
        if self.derived == "normalised":
            # A read's normalised field value, which is not a link.
            return {"actors": outgoing}
        return {
            "links": [{"target": t, "relation": "actor_reference"} for t in outgoing]
            + [{"id": i, "relation": "actor_reference"} for i in incoming]
        }

    def _update(self, config: PyriteConfig, args: dict[str, Any]) -> dict[str, Any]:
        path = self._file(config, args["entry_id"])
        assert path is not None
        replace = "content_hash" in args
        if replace and self.refuse_all_replaces:
            return {"error": "no replace"}
        if self.refuse_if_changed_since_indexed and self.first_seen.get(path) != self._digest(path):
            return {"error": "the file changed since it was indexed"}
        if replace and self.ignore_hash and self.last_read.get(path) != self._digest(path):
            # Ignores the carried hash; refuses when the file differs from the last read by anyone.
            return {"error": "changed since the last read"}
        if replace and not self.ignore_hash and args["content_hash"] != self._digest(path):
            return {"error": "the file changed since you read it; read it again"}
        text = path.read_bytes().decode("utf-8")
        head, front, rest = text.split("---\n", 2)
        for key, value in args.items():
            if key in {"kb_name", "entry_id", "content_hash", "body"}:
                continue
            front, n = re.subn(
                rf"^({re.escape(key)}:[ \t]*)(.*?)([ \t]+#.*)?$",
                lambda m, v=value: f"{m[1]}{v}{m[3] or ''}",
                front,
                flags=re.M,
            )
            assert n == 1, f"the model splices only keys already in the file: {key}"
        if "body" in args and args["body"] != rest:
            rest = args["body"]
        path.write_bytes(f"{head}---\n{front}---\n{rest}".encode())
        return {"updated": True}


class LastReadModel(ModelWritePath):
    """WRONG: ignores the carried hash and refuses when the file differs from the last read."""

    def __init__(self) -> None:
        super().__init__(ignore_hash=True)


def _example(slug_start: str) -> Example:
    return next(e for e in DOC_EXAMPLES if e.slug.startswith(slug_start))


def _install(monkeypatch: pytest.MonkeyPatch, **names: Any) -> None:
    this = sys.modules[__name__]
    for name, value in names.items():
        monkeypatch.setattr(this, name, value)


def test_a_correct_write_path_is_not_hard_failed_by_the_hook_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that writes no hook link, with the link derived, passes the hook example.

    What it rules out: the example's precondition reading the link from the
    file's `links:` block or requiring a cascade-only KB type. The KB is a
    generic one, ModelWritePath writes no link, and the link is visible only
    as a derived link under `kb_get`'s derived key. (The cascade hook is not
    in play here: ModelWritePath replaces the update, so it is neither
    switched off nor run.)
    """
    _install(monkeypatch, KB_TYPE_FOR_ENTRY_TYPE={}, _call=ModelWritePath())
    _check(_example("a-save-does-not"), tmp_path, monkeypatch)


def test_the_hook_precondition_accepts_the_real_backlinks_path(tmp_path: Path) -> None:
    """The real `kb_backlinks`, with no derived key, satisfies the precondition.

    What it rules out: accepting only the derived key of a read. Today the
    backlink exists because the file holds a `links:` block that was indexed,
    so this builds exactly that, and goes through the real server.
    """
    ex = _example("a-save-does-not")
    config, path, entry_id = _build_kb(tmp_path, ex)
    text = path.read_text()
    path.write_text(
        text.replace(
            "---\n\nText.", "links:\n- target: jane-doe\n  relation: actor_reference\n---\n\nText."
        )
    )
    db = PyriteDB(config.settings.index_path)
    try:
        IndexManager(db, config).index_all()
    finally:
        db.close()
    assert _derived_link_seen(config, "jane-doe", entry_id) == "kb_backlinks"
    _require_actor_link_derived(config, _front(ex.file), entry_id)


def test_the_hook_precondition_accepts_a_derived_link_alone(tmp_path: Path) -> None:
    """A derived link under the key of a read, with no backlink, satisfies it.

    What it rules out: accepting only `kb_backlinks`, which would hard-fail a
    path that provides the other place ADR-0042 allows.
    """
    ex = _example("a-save-does-not")
    config, _path, entry_id = _build_kb(tmp_path, ex)
    model = ModelWritePath()
    _require_actor_link_derived(config, _front(ex.file), entry_id, call=model)
    assert _derived_link_seen(config, "jane-doe", entry_id, call=model) == "kb_get['derived']"


def test_the_hook_precondition_rejects_a_normalised_field_under_the_derived_key(
    tmp_path: Path,
) -> None:
    """`derived: {actors: [...]}` is the field echoed back, not a link.

    What it rules out: the example being entered because a read returned the
    entry's own `actors` under the derived key (ADR-0042 decision 9 puts
    normalised values there) when nothing linked to the actor.
    """
    ex = _example("a-save-does-not")
    config, _path, entry_id = _build_kb(tmp_path, ex)
    echo = ModelWritePath(derived="normalised")
    with pytest.raises(CaseNotEnteredError, match="shows no link"):
        _require_actor_link_derived(config, _front(ex.file), entry_id, call=echo)


def test_the_hook_precondition_rejects_a_links_block_in_the_file_that_is_not_indexed(
    tmp_path: Path,
) -> None:
    """A file whose `links:` names the actor, with no backlink and no derived key, is not enough.

    What it rules out: reading the file's own `links:` block as proof that a
    link was derived. The KB is not re-indexed after the block is written, so
    the real `kb_backlinks` has no row; the read is the one ADR-0042 decision
    9 describes, which returns the file's parsed `links` (as a field, not under
    a derived key).
    """
    ex = _example("a-save-does-not")
    config, path, entry_id = _build_kb(tmp_path, ex)
    path.write_text(
        path.read_text().replace(
            "---\n\nText.",
            "links:\n- target: jane-doe\n  relation: actor_reference\n---\n\nText.",
        )
    )

    def reads_the_file(config, tool, args):
        got = _call(config, tool, args)
        if tool != "kb_get" or args["entry_id"] != entry_id:
            return got
        entry = dict(got.get("entry", got))
        entry["links"] = json.loads(json.dumps(_front(path.read_text())["links"]))
        return {"entry": entry}

    assert _call(config, "kb_backlinks", {"kb_name": KB, "entry_id": "jane-doe"})["backlinks"] == []
    with pytest.raises(CaseNotEnteredError, match="shows no link"):
        _require_actor_link_derived(config, _front(ex.file), entry_id, call=reads_the_file)


def test_the_hook_precondition_still_fails_when_nothing_derives_the_link(tmp_path: Path) -> None:
    """With no backlink and no derived key, the example is not entered.

    What it rules out: a precondition loosened until it accepts anything,
    which would let an empty diff stand for a hook that never ran.
    """
    ex = _example("a-save-does-not")
    config, _path, entry_id = _build_kb(tmp_path, ex)
    with pytest.raises(CaseNotEnteredError, match="shows no link"):
        _require_actor_link_derived(
            config, _front(ex.file), entry_id, call=ModelWritePath(derived="none")
        )


def test_a_correct_write_path_passes_the_stale_example_and_its_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hash compare only: refused when stale (also after a re-read), written when fresh."""
    _install(monkeypatch, _call=ModelWritePath())
    _check(_example("a-stale"), tmp_path, monkeypatch)


def test_twin_control_catches_a_path_that_refuses_every_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that never writes a replace is not a pass for the stale example.

    What it rules out: the refusal of the stale replace standing for
    staleness when this path would refuse the replace at any hash.
    """
    _install(monkeypatch, _call=ModelWritePath(refuse_all_replaces=True))
    with pytest.raises(CaseNotEnteredError, match="no hand edit was not written"):
        _check(_example("a-stale"), tmp_path, monkeypatch)


def test_stale_control_in_the_same_kb_catches_a_refusal_for_the_wrong_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that refuses any file changed since it was indexed is not a pass.

    What it rules out: the stale example passing because the write path
    refuses a file that changed after indexing, whatever hash the replace
    carries. The twin-KB control cannot see this (nothing changed there);
    the same-KB re-read control does.
    """
    _install(monkeypatch, _call=ModelWritePath(refuse_if_changed_since_indexed=True))
    with pytest.raises(CaseNotEnteredError, match="re-read after the hand edit"):
        _check(_example("a-stale"), tmp_path, monkeypatch)


def test_stale_example_fails_for_a_path_that_ignores_the_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal assertion still bites: a stale replace that is written fails."""
    # ignore_hash alone also refuses when the file differs from the last read, so
    # this variant checks nothing: the first stale replace is written.
    model = ModelWritePath(ignore_hash=True)
    model.last_read = _AlwaysFresh()
    _install(monkeypatch, _call=model)
    with pytest.raises(AssertionError, match="written, not refused"):
        _check(_example("a-stale"), tmp_path, monkeypatch)


class _AlwaysFresh(dict):
    """A ``last_read`` that always matches, so ``ignore_hash`` checks nothing."""

    def get(self, key, default=None):
        return ModelWritePath._digest(key)


def test_stale_example_fails_for_a_path_that_checks_the_last_read_not_the_carried_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse-if-changed-since-the-last-read passes the first three controls and fails here.

    What it rules out: a write path that ignores the `content_hash` the
    replace carries. It refuses the stale replace (the file differs from the
    last read), accepts a fresh one, and after a read by anyone accepts the
    replace that carries the OLD hash. The old-hash control refuses that.
    """
    _install(monkeypatch, _call=LastReadModel())
    with pytest.raises(AssertionError, match="carried hash is not what decides"):
        _check(_example("a-stale"), tmp_path, monkeypatch)


def _one_field_with_emitter() -> Example:
    one_field = DOC_EXAMPLES[0]
    return Example(
        title=one_field.title,
        file=one_field.file,
        file_flags=one_field.file_flags | {"emitter"},
        update=one_field.update,
        diff=one_field.diff,
    )


class ScalarEmitting(ModelWritePath):
    """CORRECT: splices the line, and runs the new VALUE through the YAML emitter to quote it."""

    def _update(self, config: PyriteConfig, args: dict[str, Any]) -> dict[str, Any]:
        from io import StringIO

        from pyrite.utils.yaml import _get_yaml

        _get_yaml().dump(args["title"], StringIO())
        return super()._update(config, args)


def test_emitter_control_passes_a_splice_that_emits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A splice emits no YAML: both the watch and the unwatched list are empty."""
    _install(monkeypatch, _call=ModelWritePath())
    _check(_one_field_with_emitter(), tmp_path, monkeypatch)


def test_emitter_control_passes_a_splice_that_emits_a_scalar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correct splice may run one value through the emitter (ADR-0042 decision 2).

    What it rules out: judging "did the spy record a mapping" instead of "did
    the update emit a key it was not asked to change". A quoted scalar or an
    appended list item emits no top-level key.
    """
    _install(monkeypatch, _call=ScalarEmitting())
    _check(_one_field_with_emitter(), tmp_path, monkeypatch)


from yaml import safe_dump as _safe_dump_bound_at_import  # noqa: E402  (bound before any patch)


class WholeFrontmatterThrough(ModelWritePath):
    """WRONG: splices the line, then emits the whole frontmatter through ``door``."""

    def __init__(self, door: str) -> None:
        super().__init__()
        self.door = door

    def _update(self, config: PyriteConfig, args: dict[str, Any]) -> dict[str, Any]:
        import yaml as pyyaml

        path = self._file(config, args["entry_id"])
        assert path is not None
        whole = json.loads(json.dumps(_front(path.read_text()), default=str))
        if self.door == "c":
            pyyaml.dump(whole, Dumper=pyyaml.CSafeDumper)
        else:
            _safe_dump_bound_at_import(whole)
        return super()._update(config, args)


def test_emitter_leak_is_seen_through_an_import_bound_dump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole frontmatter through PyYAML's `safe_dump` is a leak, not a pass.

    What it rules out: a spy that only wraps `dump` attributes, which a
    function bound with `from yaml import safe_dump` would escape. The watch
    is at `Emitter.emit`, below every such binding.
    """
    _install(monkeypatch, _call=WholeFrontmatterThrough("python"))
    with pytest.raises(AssertionError, match="emitter was handed keys nobody asked to change"):
        _check(_one_field_with_emitter(), tmp_path, monkeypatch)


@pytest.mark.skipif(
    not getattr(__import__("yaml"), "__with_libyaml__", False), reason="PyYAML has no libyaml"
)
def test_emitter_control_fails_when_the_update_uses_a_door_the_watch_cannot_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole frontmatter through PyYAML's C dumper is not entered, not passed.

    What it rules out: the leak check passing vacuously because the update
    emitted through an emitter the watch is not pointed at.
    """
    _install(monkeypatch, _call=WholeFrontmatterThrough("c"))
    with pytest.raises(CaseNotEnteredError, match="cannot see"):
        _check(_one_field_with_emitter(), tmp_path, monkeypatch)


def test_emitter_watch_pointed_at_pyyaml_alone_does_not_see_the_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrowing is real: PyYAML alone sees nothing of Pyrite's writes.

    What it rules out: a watch on the wrong library passing the create
    control by accident.
    """
    with pytest.raises(CaseNotEnteredError, match="saw no document during a create"):
        _check(_one_field_with_emitter(), tmp_path, monkeypatch, spy_watch=("pyyaml",))
