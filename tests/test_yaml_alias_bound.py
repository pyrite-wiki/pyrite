"""A YAML document whose aliases expand past a fixed bound is refused at load.

The property: an untrusted document whose alias count or *expanded* size passes
a bound is refused before an entry is built, on every surface that loads one:
``load_yaml`` (the typed ``FrontmatterError``), index sync (the file is reported
as malformed, the sync finishes), import (400 ``PARSE_ERROR``) and a read
(a miss with a log line, never a hang).

Fixtures are written by hand from the YAML spec: nine-way fan-out over seven
levels is 9**7 = 4,782,969 expanded scalars, from about 700 bytes of text. The
surface tests use 1,001 aliases of one scalar: past the alias bound, yet cheap
for the unfixed code (the expansion bomb above takes it minutes, which would
hang a run without the fix instead of failing it).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import FrontmatterError
from pyrite.services.access_policy import UNSCOPED
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.utils.yaml import load_yaml

# The bounds, written out: 1,000 aliases and 1,000,000 expanded nodes.
MAX_ALIASES = 1000


def _bomb(levels: int = 7, fan: int = 9) -> str:
    lines = ["l0: &l0 [" + ", ".join(["x"] * fan) + "]"]
    for i in range(1, levels):
        lines.append(f"l{i}: &l{i} [" + ", ".join([f"*l{i - 1}"] * fan) + "]")
    return "\n".join(lines)


def _many_aliases(n: int) -> str:
    return "a: &a x\nrefs: [" + ", ".join(["*a"] * n) + "]"


FRONT = "---\nid: bomb\ntitle: Bomb\ntype: note\n{yaml}\n---\n\nBody.\n"


def test_exponential_expansion_is_refused_fast_with_a_typed_error():
    started = time.monotonic()
    with pytest.raises(FrontmatterError) as exc:
        load_yaml(_bomb())
    assert time.monotonic() - started < 2
    assert exc.value.error_code == "INVALID_FRONTMATTER"
    assert "alias" in str(exc.value).lower()


def test_a_deep_bomb_is_refused_without_computing_the_expansion():
    # 9**40 would not fit a machine word of work; the check must stop early.
    started = time.monotonic()
    with pytest.raises(FrontmatterError):
        load_yaml(_bomb(levels=40))
    assert time.monotonic() - started < 2


def test_too_many_aliases_is_refused_though_the_expansion_is_small():
    with pytest.raises(FrontmatterError):
        load_yaml(_many_aliases(MAX_ALIASES + 1))


@pytest.mark.control(reason="control: legitimate YAML loaded before and must still load")
def test_ordinary_aliases_and_merge_keys_still_load():
    doc = (
        "base: &base {color: red, size: 3}\n"
        "one: {<<: *base, size: 4}\n"
        "two: {<<: *base}\n"
        "tags: &t [a, b]\n"
        "again: *t\n"
    )
    data = load_yaml(doc)
    assert data["one"]["size"] == 4 and data["again"] == ["a", "b"]
    assert len(load_yaml(_many_aliases(MAX_ALIASES))["refs"]) == MAX_ALIASES


@pytest.mark.control(reason="control: legitimate YAML loaded before and must still load")
def test_a_large_document_without_aliases_is_not_refused():
    doc = "items:\n" + "".join(f"  - {{id: {i}, name: n{i}, v: [1, 2, 3]}}\n" for i in range(20000))
    assert len(load_yaml(doc)["items"]) == 20000


@pytest.mark.control(reason="control: legitimate YAML loaded before and must still load")
def test_an_asterisk_in_text_is_not_an_alias():
    assert load_yaml("title: 'a * b & c'\nnote: x*y&z\n")["title"] == "a * b & c"


# --- the surfaces -------------------------------------------------------------

KB = "bombs"


def _env(tmp_path: Path) -> PyriteConfig:
    kb_path = tmp_path / "kb"
    (kb_path / "notes").mkdir(parents=True)
    (kb_path / "notes" / "good.md").write_text(
        "---\nid: good\ntitle: Good\ntype: note\n---\n\nFine.\n", encoding="utf-8"
    )
    (kb_path / "notes" / "bomb.md").write_text(
        FRONT.format(yaml=_many_aliases(MAX_ALIASES + 1)), encoding="utf-8"
    )
    return PyriteConfig(
        knowledge_bases=[KBConfig(name=KB, path=kb_path, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )


def test_index_sync_reports_the_file_as_malformed_and_finishes(tmp_path: Path):
    config = _env(tmp_path)
    db = PyriteDB(config.settings.index_path)
    started = time.monotonic()
    try:
        result = IndexManager(db, config).sync_incremental(KB)
        ids = {r["id"] for r in db.list_entries(kb_name=KB, limit=50)}
    finally:
        db.close()
    assert time.monotonic() - started < 20
    assert ids == {"good"}
    assert [Path(m["path"]).name for m in result["malformed"]] == ["bomb.md"]
    assert "alias" in result["malformed"][0]["error"].lower()


def test_a_read_of_the_file_is_a_miss_not_a_hang(tmp_path: Path):
    from pyrite.services.kb_service import KBService

    config = _env(tmp_path)
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        started = time.monotonic()
        assert svc.get_entry("bomb", kb_name=KB, readable_kbs=UNSCOPED) is None
        assert time.monotonic() - started < 5
    finally:
        db.close()


def test_import_answers_400_parse_error(tmp_path: Path):
    from tests._world import build_world

    w = build_world(
        tmp_path / "w", kbs=[KBConfig(name=KB, path=tmp_path / "w" / KB, kb_type="generic")]
    )
    try:
        started = time.monotonic()
        resp = w.client.post(
            f"/api/entries/import?kb={KB}&format=markdown",
            files={
                "file": (
                    "bomb.md",
                    FRONT.format(yaml=_many_aliases(MAX_ALIASES + 1)).encode(),
                    "text/markdown",
                )
            },
        )
        assert time.monotonic() - started < 10
        assert resp.status_code == 400, resp.text
        assert json.loads(resp.text)["detail"]["code"] == "PARSE_ERROR"
    finally:
        w.close()


# -- the bound is measured by the parser that loads ----------------------------

#: Characters PyYAML reads as line breaks and ruamel refuses where a line
#: starts: ahead of a document, ruamel cannot parse it and PyYAML loads it.
LINE_SEPARATORS = {"line-separator": " ", "next-line": "\x85"}


@pytest.mark.parametrize("prefix", LINE_SEPARATORS.values(), ids=LINE_SEPARATORS.keys())
def test_a_line_separator_ahead_of_aliases_does_not_skip_the_bound(prefix):
    """``safe_load_bounded`` loads with PyYAML, so the bound is counted on
    PyYAML's events: text another parser cannot read is still measured."""
    from pyrite.exceptions import YamlLimitError
    from pyrite.utils.yaml import safe_load_bounded

    with pytest.raises(YamlLimitError):
        safe_load_bounded(prefix + _many_aliases(MAX_ALIASES + 1))


@pytest.mark.parametrize("prefix", LINE_SEPARATORS.values(), ids=LINE_SEPARATORS.keys())
def test_a_template_with_a_line_separator_ahead_of_aliases_is_refused(tmp_path, prefix):
    """The same through ``GET /api/kbs/{kb}/templates/{name}``, whose
    frontmatter is read with PyYAML."""
    from tests._world import build_world

    kb = tmp_path / "kb"
    (kb / "_templates").mkdir(parents=True)
    text = f"---\n{prefix}{_many_aliases(MAX_ALIASES + 1)}\n---\nbody\n"
    (kb / "_templates" / "t.md").write_text(text, encoding="utf-8")
    w = build_world(tmp_path / "w", kbs=[KBConfig(name="k", path=kb)])
    try:
        r = w.client.get("/api/kbs/k/templates/t")
    finally:
        w.close()
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "INVALID_FRONTMATTER"


def test_a_document_the_checker_cannot_read_is_refused_typed():
    """ruamel stops on a ``%YAML 1.3`` directive with an ``AssertionError``,
    not a parse error: the document cannot be measured, so it is refused with
    the typed error rather than passed on (or crashing the caller)."""
    from pyrite.exceptions import YamlLimitError

    with pytest.raises(YamlLimitError):
        load_yaml("%YAML 1.3\n---\na: &a x\nb: *a\n")
    with pytest.raises(YamlLimitError):
        load_yaml("%YAML 1.3\n---\na: 1\n")


@pytest.mark.control(reason="an alias inside its own anchor counts once; fast before and after")
def test_an_alias_inside_its_own_anchor_stays_fast():
    from pyrite.utils.yaml import safe_load_bounded

    started = time.monotonic()
    for load in (load_yaml, safe_load_bounded):
        try:
            load("a: &a [*a, *a, *a]\n")
        except FrontmatterError:
            pass
    assert time.monotonic() - started < 2
