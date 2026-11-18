"""A kb.yaml-only type keeps every field it was given (#386).

`pyrite.models.factory.build_entry`'s `if resolved_cls is GenericEntry:`
branch constructed the entry with only `tags`, `summary` and `metadata`;
every other keyword -- anything passed as a bare `key=value`, not nested
under an explicit `metadata:` -- was silently dropped. `build_entry` is the
single choke point `KBService.create_entry` and `bulk_create_entries` call
through, so every write surface lost the data: CLI `create -f`, MCP
`kb_create`, and bulk/import (which calls `bulk_create_entries` directly, the
same path `pyrite import` uses).

Because the field never reached `to_frontmatter()`, the schema validator
that `_validate_write` runs on it never saw the value either -- so a
`validation.enforce: true` KB failed to refuse a bad enum value on a
kb.yaml-only type (the write silently *succeeded* with the bad value
dropped, rather than being refused).

`entry_from_frontmatter` (used by `pyrite add` and the indexer) already
routes unknown keys into `metadata` via `GenericEntry.from_frontmatter`, so
the same data survived that path and not this one -- this file is the direct
`build_entry` regression the issue names, plus one regression test per
write surface that reaches it.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner
from unittest.mock import patch

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import ValidationError
from pyrite.models.factory import build_entry
from pyrite.models.generic import GenericEntry
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import load_yaml


def _parse_frontmatter(text: str) -> dict:
    """Parse a written entry's YAML frontmatter into a dict.

    A substring check ("severity" in written) passes just as well for
    `metadata:\\n  severity: critical` as for a top-level `severity:
    critical` -- exactly the #149 nesting regression a cold read caught in
    this branch. Every "keeps the field" assertion below must see `severity`
    as a TOP-LEVEL key, so they all go through this parser instead.
    """
    assert text.startswith("---"), text
    end = text.find("---", 3)
    assert end > 0, text
    return load_yaml(text[3:end]) or {}


# A kb.yaml-only type: no Python model class, so `get_entry_class` falls
# back to `GenericEntry`. `severity` is a `select` field so `validation:
# enforce: true` can refuse an off-enum value once the field reaches the
# validator at all.
KB_YAML = """\
name: t
kb_type: generic
validation:
  enforce: true
types:
  finding:
    description: A finding
    fields:
      severity:
        type: select
        options: [low, medium, high]
      labels:
        type: list
"""


def _make_kb(tmp_path: Path) -> tuple[PyriteConfig, KBConfig]:
    kb_path = tmp_path / "kb"
    kb_path.mkdir()
    (kb_path / "kb.yaml").write_text(KB_YAML, encoding="utf-8")
    db_path = tmp_path / "index.db"
    PyriteDB(db_path).close()
    kb_config = KBConfig(name="t", path=kb_path, kb_type=KBType.GENERIC)
    config = PyriteConfig(
        knowledge_bases=[kb_config], settings=Settings(index_path=db_path, auto_embed=False)
    )
    return config, kb_config


class TestBuildEntryDirect:
    """The factory function itself: the exact reproduction from the issue."""

    def test_generic_type_keeps_a_bare_kwarg(self):
        entry = build_entry("finding", entry_id="x", title="X", severity="high")
        assert isinstance(entry, GenericEntry)
        fm = entry.to_frontmatter()
        assert fm.get("severity") == "high", (
            f"expected 'severity' to survive build_entry for a GenericEntry; got {fm}"
        )

    def test_generic_type_still_keeps_tags_summary_metadata(self):
        """The three keys the old branch handled must keep working."""
        entry = build_entry(
            "finding",
            entry_id="x",
            title="X",
            tags=["a"],
            summary="s",
            metadata={"m": 1},
        )
        assert entry.tags == ["a"]
        assert entry.summary == "s"
        assert entry.metadata.get("m") == 1

    def test_generic_type_metadata_kwarg_is_promoted_not_nested(self):
        """dev wrote a `metadata={"m": 1}` kwarg as a top-level `m: 1` key
        (the GenericEntry constructor call had no `_nested_metadata_keys`,
        so `to_frontmatter`'s promotion loop treated every metadata entry as
        promotable). Routing through `GenericEntry.from_frontmatter` must not
        regress that: an explicit `metadata=` kwarg is caller data assembled
        from extra fields, not a file's own nested `metadata:` block, so it
        must promote exactly like a bare kwarg does (#149 layout must not
        come back).
        """
        entry = build_entry("finding", entry_id="x", title="X", metadata={"m": 1})
        fm = entry.to_frontmatter()
        assert fm.get("m") == 1, f"expected 'm' promoted to top level; got {fm}"
        assert "metadata" not in fm, f"metadata must not come back nested; got {fm}"

    def test_generic_type_kwarg_cannot_override_id_title_type(self):
        """`type=` (or `id=`/`title=`) arriving as a plain kwarg must not
        override the entry's actual type -- an MCP `kb_create` call with a
        stray `type` field must not be validated as one type and written as
        another."""
        entry = build_entry("finding", entry_id="y", title="Y", type="event")
        assert entry.entry_type == "finding"
        assert entry.to_frontmatter().get("type") == "finding"

    def test_generic_type_reserved_keys_do_not_leak_into_frontmatter(self):
        """`kb_name`, `_entry_type`, `extra_frontmatter` and `lifecycle` are
        Entry bookkeeping, not caller-settable frontmatter content; a plain
        kwarg with one of these names must not land as a literal top-level
        key the way an ordinary custom field does."""
        entry = build_entry(
            "finding",
            entry_id="z",
            title="Z",
            kb_name="should-not-leak",
            lifecycle="archived",
        )
        fm = entry.to_frontmatter()
        assert fm.get("kb_name") != "should-not-leak", fm
        # `lifecycle` is a real Entry attribute with its own meaning (active/
        # archived); a caller passing it as a bare kwarg should set it the
        # way the typed branch would, not silently vanish or corrupt id/type.
        assert entry.id == "z"
        assert fm.get("type") == "finding"


class TestCreateEntrySurface:
    """`KBService.create_entry` -- what CLI `create -f` and MCP `kb_create` call."""

    def test_create_entry_keeps_the_field(self, tmp_path):
        config, kb_config = _make_kb(tmp_path)
        db = PyriteDB(config.settings.index_path)
        from pyrite.services.kb_service import KBService

        svc = KBService(config, db)
        try:
            entry = svc.create_entry("t", "x", "X", "finding", "body", severity="high")
        finally:
            db.close()
        fm = entry.to_frontmatter()
        assert fm.get("severity") == "high", fm
        assert "metadata" not in fm, fm

    def test_create_entry_refuses_an_off_enum_value(self, tmp_path):
        """Acceptance 2: enforce=true refuses a bad select value on a
        kb.yaml-only type -- only possible once the field reaches the
        validator, i.e. once acceptance 1 holds."""
        config, kb_config = _make_kb(tmp_path)
        db = PyriteDB(config.settings.index_path)
        from pyrite.services.kb_service import KBService

        svc = KBService(config, db)
        try:
            with pytest.raises(ValidationError, match="severity"):
                svc.create_entry("t", "x", "X", "finding", "body", severity="critical")
        finally:
            db.close()
        assert not (kb_config.path / "x.md").exists(), "a refused create must not write the file"

    def test_create_entry_refuses_an_off_enum_value_given_via_metadata(self, tmp_path):
        """The same refusal must hold when the bad value arrives nested
        under an explicit `metadata=` kwarg rather than as a bare kwarg --
        dev refused this (metadata's contents reached the validator once
        promoted); routing through `from_frontmatter`'s nested-metadata
        tracking must not exempt the `metadata=` path from enforcement."""
        config, kb_config = _make_kb(tmp_path)
        db = PyriteDB(config.settings.index_path)
        from pyrite.services.kb_service import KBService

        svc = KBService(config, db)
        try:
            with pytest.raises(ValidationError, match="severity"):
                svc.create_entry(
                    "t", "x", "X", "finding", "body", metadata={"severity": "critical"}
                )
        finally:
            db.close()
        assert not (kb_config.path / "x.md").exists(), "a refused create must not write the file"


class TestCLICreateSurface:
    """`pyrite create -f severity=high`."""

    runner = CliRunner()

    def test_cli_create_dash_f_keeps_the_field(self, tmp_path):
        from pyrite.cli import app

        config, kb_config = _make_kb(tmp_path)
        with patch("pyrite.cli.context.load_config", return_value=config):
            result = self.runner.invoke(
                app,
                [
                    "create",
                    "-k",
                    "t",
                    "-t",
                    "finding",
                    "--title",
                    "X",
                    "-b",
                    "body",
                    "-f",
                    "severity=high",
                ],
            )
        assert result.exit_code == 0, result.output
        fm = _parse_frontmatter((kb_config.path / "x.md").read_text(encoding="utf-8"))
        assert fm.get("severity") == "high", fm
        assert "metadata" not in fm, fm

    def test_cli_create_dash_f_preserves_a_comma_in_an_undeclared_field(self, tmp_path):
        from pyrite.cli import app

        config, kb_config = _make_kb(tmp_path)
        with patch("pyrite.cli.context.load_config", return_value=config):
            result = self.runner.invoke(
                app,
                [
                    "create",
                    "-k",
                    "t",
                    "-t",
                    "finding",
                    "--title",
                    "Comma",
                    "-b",
                    "body",
                    "-f",
                    "remark=one, two",
                ],
            )
        assert result.exit_code == 0, result.output
        fm = _parse_frontmatter((kb_config.path / "comma.md").read_text(encoding="utf-8"))
        assert fm["remark"] == "one, two"

    def test_cli_create_dash_f_sends_several_list_items_as_a_json_array(self, tmp_path):
        """Several items for a schema-declared list go as a JSON array; a comma
        value is the one item it spells: the CLI never splits (#1502)."""
        from pyrite.cli import app

        config, kb_config = _make_kb(tmp_path)
        base = ["create", "-k", "t", "-t", "finding", "-b", "body"]
        with patch("pyrite.cli.context.load_config", return_value=config):
            several = self.runner.invoke(
                app, [*base, "--title", "Labels", "-f", 'labels=["one", "two"]']
            )
            one = self.runner.invoke(app, [*base, "--title", "Comma List", "-f", "labels=one,two"])
        assert several.exit_code == 0, several.output
        assert one.exit_code == 0, one.output
        fm = _parse_frontmatter((kb_config.path / "labels.md").read_text(encoding="utf-8"))
        assert fm["labels"] == ["one", "two"]
        fm = _parse_frontmatter((kb_config.path / "comma-list.md").read_text(encoding="utf-8"))
        assert fm["labels"] == ["one,two"]

    def test_cli_create_dash_f_refuses_off_enum(self, tmp_path):
        from pyrite.cli import app

        config, kb_config = _make_kb(tmp_path)
        with patch("pyrite.cli.context.load_config", return_value=config):
            result = self.runner.invoke(
                app,
                [
                    "create",
                    "-k",
                    "t",
                    "-t",
                    "finding",
                    "--title",
                    "X",
                    "-b",
                    "body",
                    "-f",
                    "severity=critical",
                ],
            )
        assert result.exit_code != 0, result.output
        assert not (kb_config.path / "x.md").exists()


class TestMCPKBCreateSurface:
    """MCP `kb_create` with `severity` as a top-level arg."""

    def test_kb_create_keeps_the_field(self, tmp_path):
        config, kb_config = _make_kb(tmp_path)
        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "t",
                    "entry_type": "finding",
                    "title": "X",
                    "body": "body",
                    "severity": "high",
                },
            )
            assert result.get("created") is True, result
            # The DB-indexed `kb_get` view keeps unknown fields nested under
            # `metadata`, same as `test_kb_create_with_metadata_generic_type`
            # asserts for an explicit `metadata=` kwarg -- the file on disk is
            # the unambiguous check that the field itself, not just the
            # write's success flag, survived, and that it landed top-level
            # rather than re-nested under `metadata:` (#149).
            fm = _parse_frontmatter(Path(result["file_path"]).read_text(encoding="utf-8"))
            assert fm.get("severity") == "high", fm
            assert "metadata" not in fm, fm
        finally:
            server.close()

    def test_kb_create_type_kwarg_cannot_override_entry_type(self, tmp_path):
        """A stray `type` argument in the MCP call must not be validated as
        one type and written as another (coordinator repro: `entry_type` and
        `type` disagreeing)."""
        config, kb_config = _make_kb(tmp_path)
        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "t",
                    "entry_type": "finding",
                    "title": "X",
                    "body": "body",
                    "type": "event",
                    "severity": "high",
                },
            )
            assert result.get("created") is True, result
            fm = _parse_frontmatter(Path(result["file_path"]).read_text(encoding="utf-8"))
            assert fm.get("type") == "finding", fm
        finally:
            server.close()

    def test_kb_create_metadata_cannot_retype_or_rename_the_entry(self, tmp_path):
        """Delta cold read on eda8018a: reserved names inside `metadata=` were
        merged after `id`/`title`/`type`, so `metadata={"type": "event"}`
        validated and wrote a `finding` as an `event` -- and an off-enum
        value got past `enforce: true`. It is refused as a finding, and
        nothing is written."""
        config, kb_config = _make_kb(tmp_path)
        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "t",
                    "entry_type": "finding",
                    "title": "X",
                    "body": "body",
                    "metadata": {"type": "event", "id": "other-id", "severity": "critical"},
                },
            )
            assert result.get("created") is not True, result
            assert not list(Path(kb_config.path).rglob("*.md")), "nothing is written"
        finally:
            server.close()

    def test_build_entry_metadata_reserved_keys_are_ignored(self):
        e = build_entry(
            "finding",
            entry_id="x",
            title="X",
            metadata={"type": "event", "id": "other", "title": "Other", "m": 1},
        )
        fm = e.to_frontmatter()
        assert (fm["id"], fm["title"], fm["type"], fm["m"]) == ("x", "X", "finding", 1)

    def test_kb_create_refuses_off_enum(self, tmp_path):
        config, kb_config = _make_kb(tmp_path)
        server = PyriteMCPServer(config, tier="write")
        try:
            result = server._dispatch_tool(
                "kb_create",
                {
                    "kb_name": "t",
                    "entry_type": "finding",
                    "title": "X",
                    "body": "body",
                    "severity": "critical",
                },
            )
            assert result.get("created") is not True, result
            assert not (kb_config.path / "x.md").exists()
        finally:
            server.close()


class TestBulkAndImportSurface:
    """`bulk_create_entries` -- shared by MCP `kb_bulk_create` and `pyrite import`."""

    def test_bulk_create_keeps_the_field(self, tmp_path):
        config, kb_config = _make_kb(tmp_path)
        db = PyriteDB(config.settings.index_path)
        from pyrite.services.kb_service import KBService

        svc = KBService(config, db)
        try:
            results = svc.bulk_create_entries(
                "t",
                [{"entry_type": "finding", "title": "X", "body": "body", "severity": "high"}],
            )
        finally:
            db.close()
        assert results[0]["created"] is True, results
        fm = _parse_frontmatter((kb_config.path / "x.md").read_text(encoding="utf-8"))
        assert fm.get("severity") == "high", fm
        assert "metadata" not in fm, fm

    # No off-enum refusal test for `bulk_create_entries` here: unlike
    # `create_entry`, it never calls `_validate_write` at all (confirmed by
    # reading `KBService.bulk_create_entries` -- there is no `_validate_write`
    # or `entry.validate()` call in its loop, and `test_write_path_enforces_
    # schema.py`'s enforce coverage only exercises `create_entry`/
    # `update_entry`). That is a real gap, but its root cause is in
    # `kb_service.py`'s bulk loop, not in `build_entry` -- this ticket's
    # touch is `factory.py` only. Already tracked as #366 (bulk create skips
    # schema/plugin validation); not folded into this fix.
