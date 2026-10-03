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


def _spy_emitter(
    monkeypatch: pytest.MonkeyPatch, watch: tuple[str, ...] = ("ruamel", "pyyaml")
) -> list[set[str]]:
    """Record the top-level keys of every mapping handed to a YAML emitter.

    ``watch`` names the emitters the spy patches; the default is all of them.
    A narrower value exists to point the spy at the wrong emitter on purpose
    (``test_emitter_control_fails_when_the_spy_watches_the_wrong_emitter``).
    """
    import yaml as pyyaml
    from ruamel.yaml import YAML

    emitted: list[set[str]] = []

    def record(data: Any) -> None:
        if hasattr(data, "keys"):
            emitted.append({str(k) for k in data.keys()})

    if "ruamel" in watch:
        real_dump = YAML.dump

        def ruamel_dump(self, data, stream=None, **kw):
            record(data)
            return real_dump(self, data, stream, **kw)

        monkeypatch.setattr(YAML, "dump", ruamel_dump)
    if "pyyaml" in watch:
        for name in ("dump", "safe_dump"):
            real = getattr(pyyaml, name)

            def py_dump(data, *a, _real=real, **kw):
                record(data)
                return _real(data, *a, **kw)

            monkeypatch.setattr(pyyaml, name, py_dump)
    return emitted


def _count_emits(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count calls to the two libraries' lowest-level ``Emitter.emit``.

    Every YAML dump of either library, however it is reached (``dump``,
    ``dump_all``, ``safe_dump``, a ``Dumper`` used directly), ends in
    ``Emitter.emit``, so this counter does not depend on which front door the
    write path uses. It is the control for ``_spy_emitter``: if the update
    emitted, the spy must have recorded a mapping.
    """
    import yaml.emitter as py_emitter
    from ruamel.yaml import emitter as ru_emitter

    calls: list[int] = []
    for cls in (py_emitter.Emitter, ru_emitter.Emitter):
        real = cls.emit

        def counted(self, event, _real=real):
            calls.append(1)
            return _real(self, event)

        monkeypatch.setattr(cls, "emit", counted)
    return calls


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


def _mentions(value: Any, target: str) -> bool:
    """Whether ``target`` appears as a value anywhere in a nested result."""
    if isinstance(value, dict):
        return any(_mentions(v, target) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_mentions(v, target) for v in value)
    return isinstance(value, str) and value.strip("[]") == target


def _derived_link_seen(
    config: PyriteConfig, actor: str, entry_id: str, *, call: Any = None
) -> str | None:
    """How the actor link is visible, or None.

    Two places, neither of them the file's own `links:` block (the file may or
    may not hold one; that is what the example measures, so it cannot also be
    the precondition):

    - ``kb_backlinks`` on the actor lists the entry (the index derived it);
    - ``kb_get`` on the entry or on the actor returns it under a key whose
      name says it is derived (``derived``, ``derived_links``, ...), the
      shape ADR-0042 decision 4 and decision 9 give a read. Which sub-key
      holds it is not fixed yet, so any value under such a key counts.
    """
    call = call or _call
    got = call(config, "kb_backlinks", {"kb_name": KB, "entry_id": actor})
    if entry_id in [b.get("id") for b in got.get("backlinks", [])]:
        return "kb_backlinks"
    for read_id, wanted in ((entry_id, actor), (actor, entry_id)):
        read = call(config, "kb_get", {"kb_name": KB, "entry_id": read_id})
        read = read.get("entry", read)
        for key, value in read.items():
            if "derived" in key.lower() and _mentions(value, wanted):
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
    from `kb_backlinks` or from the derived key of a read, never from the
    file's `links:` block, and no KB type is named here.

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


def _require_fresh_replace_is_written(
    ex: Example, base: Path, op: dict[str, Any], *, echo: bool
) -> None:
    """Control for `stale`: the same replace, with no hand edit, is written.

    Run in a second KB built from the same file. If it is refused too, the
    refusal of the stale replace says nothing about staleness.
    """
    base.mkdir()
    config, path, entry_id = _build_kb(base, ex)
    file_text = path.read_text()
    read = _read(config, entry_id)
    fresh = {**op, "content_hash": read.get("content_hash")}
    result = _call(
        config, "kb_update", _to_kb_update_args(fresh, config, entry_id, file_text, echo=echo)
    )
    _require_replace_landed(
        result,
        path,
        op,
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


def _require_reread_replace_is_written(
    ex: Example, config: PyriteConfig, path: Path, entry_id: str, op: dict[str, Any], *, echo: bool
) -> None:
    """Control for `stale`, in the SAME KB and after the hand edit.

    Re-read the entry now, replace with the hash of that re-read, expect the
    write to happen. What it rules out: a refusal that is not about the hash
    the replace carried. The twin KB control shows a fresh-hash replace is
    written when nothing changed; it cannot show that the write path accepts
    a fresh hash on a file that was edited after it was indexed. Without this
    control, a path that refuses any file changed since indexing (or since the
    first read) would pass the stale example for the wrong reason.
    """
    file_text = path.read_text()
    read = _read(config, entry_id)
    _require(read.get("content_hash"), "the re-read after the hand edit returned no content_hash")
    op = {**op, "content_hash": read["content_hash"]}
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
        # Control, same KB: the refusal is about the hash. The control writes
        # the file, so the bytes the refusal left are put back for the diff.
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
            emitted = _spy_emitter(spy_patch, spy_watch) if "emitter" in ex.file_flags else None
            emits = _count_emits(spy_patch) if emitted is not None else []
            if emitted is not None:
                # Control 1: a create has to emit frontmatter (ADR-0042 decision
                # 13), so the spy must see it.
                _call(
                    config, "kb_create", {"kb_name": KB, "entry_type": "note", "title": "Spy probe"}
                )
                _require(
                    emitted,
                    "the emitter spy saw nothing during a create; it is not watching "
                    "the emitter Pyrite uses, so an empty record would prove nothing",
                )
                emitted.clear()
                emits.clear()
            result = _call(config, "kb_update", args)
        if emitted is not None:
            # Control 2: the UPDATE's own emitter. A create and an update may
            # not share a front door. If the update ran any YAML emit and the
            # spy recorded no mapping, the spy is not watching the emitter the
            # update used, and "nothing leaked" would be vacuous. An update
            # that never emits (a splice) leaves both empty: a real pass.
            _require(
                emitted or not emits,
                f"the update ran the YAML emitter {len(emits)} times but the spy "
                "recorded no mapping: it is not watching the emitter the update uses, so "
                "an empty leak list would prove nothing",
            )
        assert not _is_error(result), f"kb_update refused: {result}"
        if emitted is not None:
            asked = set(args) - {"kb_name", "entry_id"}
            untouched = set(_front(before)) - asked
            leaked = sorted(set().union(*emitted) & untouched) if emitted else []
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
# The harness against a write path that is right, and against ones that are wrong
# --------------------------------------------------------------------------
#
# The examples above run against today's write path, which diverges, so most
# of the harness's controls are never reached by a passing run. These tests
# run `_check` against ModelWritePath, a stand-in for the write path ADR-0042
# describes (a splice of the named fields; a hash compare; links derived,
# never written), and against variants that are wrong in one way each. Two
# properties of a write example's verdict (#703):
#
# - a correct write path is never hard-failed (no CaseNotEnteredError);
# - an incorrect one never passes, and a wrong reason is not mistaken for the
#   right one.


class ModelWritePath:
    """Replaces ``_call``: reads and the create go to the real server, the update does not.

    ``refuse_if_changed_since_indexed`` is the wrong reason to refuse a stale
    replace; ``ignore_hash`` is a path that does not check it at all.
    """

    def __init__(
        self,
        *,
        derived_key: bool = True,
        refuse_if_changed_since_indexed: bool = False,
        ignore_hash: bool = False,
    ) -> None:
        self.real = _call
        self.derived_key = derived_key
        self.refuse_if_changed_since_indexed = refuse_if_changed_since_indexed
        self.ignore_hash = ignore_hash
        self.first_seen: dict[Path, str] = {}

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
                entry["content_hash"] = self._digest(path)
            if self.derived_key:
                entry["derived"] = {"links": self._derived_links(config, args["entry_id"])}
            return {"entry": entry} if "entry" in got else entry
        if tool == "kb_backlinks" and self.derived_key:
            return {"backlinks": [{"id": i} for i in self._linking_to(config, args["entry_id"])]}
        if tool == "kb_update":
            return self._update(config, args)
        return self.real(config, tool, args)

    def _entries(self, config: PyriteConfig) -> list[Path]:
        return sorted(config.knowledge_bases[0].path.rglob("*.md"))

    def _derived_links(self, config: PyriteConfig, entry_id: str) -> list[str]:
        out = []
        for path in self._entries(config):
            meta = _front(path.read_text())
            if meta.get("id") == entry_id:
                out += [str(ref).strip("[]") for ref in meta.get("actors", [])]
        return out

    def _linking_to(self, config: PyriteConfig, target: str) -> list[str]:
        out = []
        for path in self._entries(config):
            meta = _front(path.read_text())
            if target in [str(r).strip("[]") for r in meta.get("actors", [])]:
                out.append(str(meta["id"]))
        return out

    def _update(self, config: PyriteConfig, args: dict[str, Any]) -> dict[str, Any]:
        path = self._file(config, args["entry_id"])
        assert path is not None
        if self.refuse_if_changed_since_indexed and self.first_seen.get(path) != self._digest(path):
            return {"error": "the file changed since it was indexed"}
        if "content_hash" in args and not self.ignore_hash:
            if args["content_hash"] != self._digest(path):
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


def _example(slug_start: str) -> Example:
    return next(e for e in DOC_EXAMPLES if e.slug.startswith(slug_start))


def test_a_correct_write_path_is_not_hard_failed_by_the_hook_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path that writes no hook link, with the link derived, passes the hook example.

    What it rules out: the example's precondition reading the link from the
    file's `links:` block or from a cascade-only KB type. The hook is
    switched off (the cascade plugin's `resolve_actor_links` becomes a
    no-op), the KB is a generic one, and the link is visible only under
    `kb_get`'s derived key and in `kb_backlinks`.
    """
    import pyrite_cascade.plugin as cascade

    monkeypatch.setattr(cascade, "resolve_actor_links", lambda entry, ctx: entry)
    monkeypatch.setattr(sys.modules[__name__], "KB_TYPE_FOR_ENTRY_TYPE", {})
    monkeypatch.setattr(sys.modules[__name__], "_call", ModelWritePath())
    _check(_example("a-save-does-not"), tmp_path, monkeypatch)


@pytest.mark.parametrize("derived_key", [True, False])
def test_the_hook_precondition_reads_either_derived_form(
    derived_key: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The link counts from `kb_backlinks` alone or from the derived key alone.

    What it rules out: accepting only one of the two places ADR-0042 allows,
    which would hard-fail a path that provides the other.
    """
    model = ModelWritePath()
    ex = _example("a-save-does-not")
    config, _path, entry_id = _build_kb(tmp_path, ex)

    def only(config, tool, args, _m=model, _d=derived_key):
        got = _m(config, tool, args)
        if tool == "kb_backlinks" and not _d:
            return got
        if tool == "kb_backlinks":
            return {"backlinks": []}
        if tool == "kb_get" and not _d:
            entry = got.get("entry", got)
            return {"entry": {k: v for k, v in entry.items() if k != "derived"}}
        return got

    _require_actor_link_derived(config, _front(ex.file), entry_id, call=only)


def test_the_hook_precondition_still_fails_when_nothing_derives_the_link(
    tmp_path: Path,
) -> None:
    """With no backlink and no derived key, the example is not entered.

    What it rules out: a precondition loosened until it accepts anything,
    which would let an empty diff stand for a hook that never ran.
    """
    ex = _example("a-save-does-not")
    config, _path, entry_id = _build_kb(tmp_path, ex)
    silent = lambda config, tool, args: (  # noqa: E731
        {"backlinks": []} if tool == "kb_backlinks" else {"entry": {"id": args["entry_id"]}}
    )
    with pytest.raises(CaseNotEnteredError, match="no link"):
        _require_actor_link_derived(config, _front(ex.file), entry_id, call=silent)


def test_a_correct_write_path_passes_the_stale_example_and_its_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hash compare only: refused when stale, written when the hash is fresh."""
    monkeypatch.setattr(sys.modules[__name__], "_call", ModelWritePath())
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
    model = ModelWritePath(refuse_if_changed_since_indexed=True)
    monkeypatch.setattr(sys.modules[__name__], "_call", model)
    with pytest.raises(CaseNotEnteredError, match="re-read after the hand edit"):
        _check(_example("a-stale"), tmp_path, monkeypatch)


def test_stale_example_fails_for_a_path_that_ignores_the_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal assertion still bites: a stale replace that is written fails."""
    monkeypatch.setattr(sys.modules[__name__], "_call", ModelWritePath(ignore_hash=True))
    with pytest.raises(AssertionError, match="written, not refused"):
        _check(_example("a-stale"), tmp_path, monkeypatch)


def test_emitter_control_passes_an_update_that_emits_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A splice emits no YAML; the spy and the counter are both empty: a pass.

    What it rules out: the update control hard-failing a correct path because
    its update did not emit.
    """
    one_field = DOC_EXAMPLES[0]
    ex = Example(
        title=one_field.title,
        file=one_field.file,
        file_flags=one_field.file_flags | {"emitter"},
        update=one_field.update,
        diff=one_field.diff,
    )

    monkeypatch.setattr(sys.modules[__name__], "_call", ModelWritePath())
    _check(ex, tmp_path, monkeypatch)


def test_emitter_control_fails_when_the_spy_watches_a_different_emitter_than_the_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spy that sees the create but not the update's emitter is not accepted.

    What it rules out: the leak check passing vacuously because the spy was
    never pointed at the emitter the update goes through. Here the spy is
    given the create's mapping and nothing from the update, while the real
    update emits through the YAML emitter (counted below the spy).
    """
    one_field = DOC_EXAMPLES[0]
    ex = Example(
        title=one_field.title,
        file=one_field.file,
        file_flags=one_field.file_flags | {"emitter"},
        update=one_field.update,
        diff=one_field.diff,
    )
    seen: list[set[str]] = []
    real_call = _call

    def create_only_spy(patch: pytest.MonkeyPatch, watch: tuple[str, ...]) -> list[set[str]]:
        return seen

    def call(config, tool, args):
        out = real_call(config, tool, args)
        if tool == "kb_create":
            seen.append({"id", "title", "type"})
        return out

    this = sys.modules[__name__]
    monkeypatch.setattr(this, "_spy_emitter", create_only_spy)
    monkeypatch.setattr(this, "_call", call)
    with pytest.raises(CaseNotEnteredError, match="not watching the emitter the update uses"):
        _check(ex, tmp_path, monkeypatch)


def test_emitter_spy_pointed_at_pyyaml_alone_does_not_see_the_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The narrowing argument is real: PyYAML alone sees nothing of Pyrite's writes.

    What it rules out: a spy that watches the wrong library passing the first
    control by accident.
    """
    one_field = DOC_EXAMPLES[0]
    ex = Example(
        title=one_field.title,
        file=one_field.file,
        file_flags=one_field.file_flags | {"emitter"},
        update=one_field.update,
        diff=one_field.diff,
    )
    with pytest.raises(CaseNotEnteredError, match="saw nothing during a create"):
        _check(ex, tmp_path, monkeypatch, spy_watch=("pyyaml",))
