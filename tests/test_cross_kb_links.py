"""Tests for cross-KB wikilink resolution."""

import pytest

from pyrite.storage.index import _WIKILINK_RE
from pyrite.services.access_policy import UNSCOPED


class TestWikilinkRegex:
    """Test the updated wikilink regex with cross-KB prefix support."""

    def test_simple_wikilink(self):
        match = _WIKILINK_RE.search("See [[some-entry]] for details")
        assert match is not None
        assert match.group(1) is None  # No KB prefix
        assert match.group(2) == "some-entry"

    def test_wikilink_with_display(self):
        match = _WIKILINK_RE.search("See [[some-entry|display text]] here")
        assert match is not None
        assert match.group(1) is None
        assert match.group(2) == "some-entry"

    def test_cross_kb_wikilink(self):
        match = _WIKILINK_RE.search("See [[dev:some-entry]] for details")
        assert match is not None
        assert match.group(1) == "dev"
        assert match.group(2) == "some-entry"

    def test_cross_kb_wikilink_with_display(self):
        match = _WIKILINK_RE.search("See [[ops:deploy-guide|Deploy Guide]] here")
        assert match is not None
        assert match.group(1) == "ops"
        assert match.group(2) == "deploy-guide"

    def test_multiple_wikilinks(self):
        text = "See [[kb1:entry-a]] and [[entry-b]] and [[kb2:entry-c|Display]]"
        matches = list(_WIKILINK_RE.finditer(text))
        assert len(matches) == 3
        assert matches[0].group(1) == "kb1"
        assert matches[0].group(2) == "entry-a"
        assert matches[1].group(1) is None
        assert matches[1].group(2) == "entry-b"
        assert matches[2].group(1) == "kb2"
        assert matches[2].group(2) == "entry-c"

    def test_no_match_for_urls(self):
        """URLs with colons should not match as cross-KB links in regex."""
        text = "Visit https://example.com for details"
        matches = list(_WIKILINK_RE.finditer(text))
        assert len(matches) == 0


class TestCrossKBResolution:
    """Test cross-KB entry resolution via KBService."""

    @pytest.fixture
    def config_with_shortnames(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings

        kb1_path = tmp_path / "kb1"
        kb1_path.mkdir()
        kb2_path = tmp_path / "kb2"
        kb2_path.mkdir()

        return PyriteConfig(
            knowledge_bases=[
                KBConfig(name="dev-kb", path=kb1_path, shortname="dev"),
                KBConfig(name="ops-kb", path=kb2_path, shortname="ops"),
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )

    def test_a_shortname_names_its_kb(self, config_with_shortnames):
        # The one shortname lookup is the resolver's, scoped to the caller's
        # readable KBs (#952); the config itself maps nothing.
        from pyrite.services.kb_resolver import readable_kb_with_shortname

        kb = readable_kb_with_shortname(config_with_shortnames, "dev", None)
        assert kb is not None
        assert kb.name == "dev-kb"
        assert readable_kb_with_shortname(config_with_shortnames, "nonexistent", None) is None
        assert not hasattr(config_with_shortnames, "get_kb_by_shortname")

    def test_resolve_cross_kb_entry(self, config_with_shortnames):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config = config_with_shortnames
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)

        # Register KBs and insert a test entry
        db.register_kb(
            name="dev-kb",
            kb_type="generic",
            path=str(config.knowledge_bases[0].path),
            description="",
        )
        db.upsert_entry(
            {
                "id": "my-entry",
                "kb_name": "dev-kb",
                "entry_type": "note",
                "title": "My Entry",
                "body": "",
                "tags": [],
                "links": [],
                "sources": [],
            }
        )

        # Resolve using shortname
        result = svc.resolve_entry("dev:my-entry", readable_kbs=UNSCOPED)
        assert result is not None
        assert result["id"] == "my-entry"
        assert result["kb_name"] == "dev-kb"

    def test_resolve_cross_kb_by_full_name(self, config_with_shortnames):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config = config_with_shortnames
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)

        db.register_kb(
            name="ops-kb",
            kb_type="generic",
            path=str(config.knowledge_bases[1].path),
            description="",
        )
        db.upsert_entry(
            {
                "id": "runbook",
                "kb_name": "ops-kb",
                "entry_type": "note",
                "title": "Runbook",
                "body": "",
                "tags": [],
                "links": [],
                "sources": [],
            }
        )

        # Resolve using full KB name
        result = svc.resolve_entry("ops-kb:runbook", readable_kbs=UNSCOPED)
        assert result is not None
        assert result["id"] == "runbook"

    def test_resolve_batch_with_cross_kb(self, config_with_shortnames):
        from pyrite.services.kb_service import KBService
        from pyrite.storage.database import PyriteDB

        config = config_with_shortnames
        db = PyriteDB(config.settings.index_path)
        svc = KBService(config, db)

        db.register_kb(
            name="dev-kb",
            kb_type="generic",
            path=str(config.knowledge_bases[0].path),
            description="",
        )
        db.upsert_entry(
            {
                "id": "entry-a",
                "kb_name": "dev-kb",
                "entry_type": "note",
                "title": "Entry A",
                "body": "",
                "tags": [],
                "links": [],
                "sources": [],
            }
        )

        result = svc.resolve_batch(
            ["dev:entry-a", "dev:nonexistent", "entry-a"], kb_name="dev-kb", readable_kbs=UNSCOPED
        )
        assert result["dev:entry-a"] is True
        assert result["dev:nonexistent"] is False
        assert result["entry-a"] is True


class TestWikilinkExtraction:
    """Test that cross-KB wikilinks are correctly extracted during indexing."""

    def test_cross_kb_link_extraction(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="test-kb", path=tmp_path),
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        mgr = IndexManager(db, config)

        entry = NoteEntry(
            id="source-entry",
            title="Source",
            body="Links to [[other-kb:target-entry]] and [[local-entry]]",
        )

        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "source-entry.md")
        links = data["links"]

        # Should have two wikilinks
        targets = {link["target"] for link in links}
        assert "target-entry" in targets
        assert "local-entry" in targets

        # Cross-KB link should have the kb prefix
        cross_kb_link = next(link for link in links if link["target"] == "target-entry")
        assert cross_kb_link["kb"] == "other-kb"

        # Local link should have the current KB
        local_link = next(link for link in links if link["target"] == "local-entry")
        assert local_link["kb"] == "test-kb"

    def test_wikilinks_in_fenced_code_blocks_are_ignored(self, tmp_path):
        """Wikilink-shaped tokens inside fenced code blocks are code, not links."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = (
            "Real link: [[real-target]]\n\n"
            "```\n"
            "Syntax example: [[bug:123]]\n"
            "Use [[kb:entry-id]] to cross-link\n"
            "```\n\n"
            "More: [[another-real]]\n"
        )
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "real-target" in targets
        assert "another-real" in targets
        assert "123" not in targets
        assert "entry-id" not in targets

    def test_wikilinks_in_inline_code_are_ignored(self, tmp_path):
        """Wikilink-shaped tokens inside backtick spans are code, not links."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = (
            "Real link: [[real-target]]. "
            "Write `[[some-placeholder]]` to create a link. "
            "Also `[[wiki:PageName]]` syntax."
        )
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "real-target" in targets
        assert "some-placeholder" not in targets
        assert "PageName" not in targets

    def test_path_like_targets_are_rejected(self, tmp_path):
        """Targets containing `/` cannot be valid entry IDs."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = "See [[jan6/witnesses]] and [[kb/positioning/software-teams]] and [[valid-id]]"
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "valid-id" in targets
        assert "jan6/witnesses" not in targets
        assert "kb/positioning/software-teams" not in targets

    def test_transclusions_in_code_blocks_are_ignored(self, tmp_path):
        """Transclusion-shaped tokens inside code blocks are code, not links."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="test-kb", path=tmp_path)],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        mgr = IndexManager(PyriteDB(config.settings.index_path), config)

        body = "Real transclusion: ![[real-target]]\n\n```\nExample: ![[bug:123]]\n```\n"
        entry = NoteEntry(id="src", title="S", body=body)
        data = mgr._entry_to_dict(entry, "test-kb", tmp_path / "src.md")
        targets = {l["target"] for l in data["links"]}
        assert "real-target" in targets
        assert "123" not in targets


class TestAShortnameLinkIsIndexedUnderTheFullName:
    """ADR-0049 / #952: the server resolves a `[[short:id]]` prefix to the
    KB's full name and the index stores the full name, so backlinks, the
    graph and the broken-link count agree with `resolve_entry` on whether
    the link resolves (#979). Stored as written, `ops` named no KB and every
    consumer of the link table called the link broken."""

    @staticmethod
    def _config(tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings

        for name in ("kb1", "kb2"):
            (tmp_path / name).mkdir()
        return PyriteConfig(
            knowledge_bases=[
                KBConfig(name="dev-kb", path=tmp_path / "kb1", shortname="dev"),
                KBConfig(name="ops-kb", path=tmp_path / "kb2", shortname="ops"),
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )

    @pytest.fixture
    def indexed(self, tmp_path):
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        config = self._config(tmp_path)
        dev, ops = config.knowledge_bases
        KBRepository(ops).save(NoteEntry(id="runbook", title="Runbook", body="ops body"))
        KBRepository(dev).save(
            NoteEntry(
                id="note",
                title="Note",
                body="See [[ops:runbook]] and ![[ops:runbook]]; [[ops-kb:runbook|by name]].",
                metadata={"references": ["ops:runbook"]},
            )
        )
        db = PyriteDB(config.settings.index_path)
        for kb in (ops, dev):
            IndexManager(db, config).sync_kb(kb)
        try:
            yield config, db
        finally:
            db.close()

    def test_every_link_row_names_the_full_kb(self, indexed):
        _, db = indexed
        rows = db.execute_sql(
            "SELECT relation, target_kb FROM link WHERE source_id = 'note' ORDER BY relation",
            {},
        )
        assert rows, "no link rows indexed"
        assert {r["target_kb"] for r in rows} == {"ops-kb"}, rows

    def test_the_link_is_not_broken_and_is_a_backlink(self, indexed):
        _, db = indexed
        broken = db.execute_sql(
            "SELECT COUNT(*) AS cnt FROM link l LEFT JOIN entry e "
            "ON l.target_id = e.id AND l.target_kb = e.kb_name WHERE e.id IS NULL",
            {},
        )
        assert broken[0]["cnt"] == 0
        back = db.execute_sql(
            "SELECT source_id FROM link WHERE target_id = 'runbook' AND target_kb = 'ops-kb'", {}
        )
        assert {r["source_id"] for r in back} == {"note"}

    @pytest.mark.control(reason="an unknown prefix was stored as written before the fix too")
    def test_an_unknown_prefix_stays_as_written(self, tmp_path):
        """A prefix that names no KB (full or short) is kept verbatim, so
        the link is counted broken, which it is."""
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        config = self._config(tmp_path)
        dev = config.knowledge_bases[0]
        KBRepository(dev).save(NoteEntry(id="dangling", title="D", body="[[nowhere:x]]"))
        db = PyriteDB(config.settings.index_path)
        try:
            IndexManager(db, config).sync_kb(dev)
            rows = db.execute_sql("SELECT target_kb FROM link WHERE source_id = 'dangling'", {})
            assert [r["target_kb"] for r in rows] == ["nowhere"]
        finally:
            db.close()

    def test_the_written_prefix_is_stored_beside_the_full_name(self, indexed):
        """ADR-0049 / #980 review item 1: a stored link row can always be
        checked against what the file wrote, so the prefix as written sits
        beside the KB name it resolved to."""
        _, db = indexed
        rows = db.execute_sql(
            "SELECT target_prefix, target_kb FROM link WHERE source_id = 'note'", {}
        )
        # One row per written link (#1009): the shortname spellings and the
        # full-name spelling are rows of their own.
        assert {(r["target_prefix"], r["target_kb"]) for r in rows} == {
            ("ops", "ops-kb"),
            ("ops-kb", "ops-kb"),
        }

    def test_a_frontmatter_link_kb_is_resolved_and_kept(self, tmp_path):
        """`links: [{target, kb: ops}]` in frontmatter is a KB field in link
        position (#980 review item 2): the shortname resolves to the full
        name as a `[[ops:id]]` does, and the written value is kept."""
        from pyrite.models import NoteEntry
        from pyrite.schema.provenance import Link
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        config = self._config(tmp_path)
        dev, ops = config.knowledge_bases
        KBRepository(ops).save(NoteEntry(id="runbook", title="Runbook", body="ops body"))
        KBRepository(dev).save(
            NoteEntry(
                id="fm",
                title="FM",
                body="no wikilinks",
                links=[
                    Link(target="runbook", relation="related", kb="ops"),
                    Link(target="runbook", relation="depends_on", kb="ops-kb"),
                ],
            )
        )
        db = PyriteDB(config.settings.index_path)
        try:
            for kb in (ops, dev):
                IndexManager(db, config).sync_kb(kb)
            rows = db.execute_sql(
                "SELECT relation, target_prefix, target_kb FROM link WHERE source_id = 'fm' "
                "ORDER BY relation",
                {},
            )
            assert [(r["relation"], r["target_prefix"], r["target_kb"]) for r in rows] == [
                ("depends_on", "ops-kb", "ops-kb"),
                ("related", "ops", "ops-kb"),
            ]
        finally:
            db.close()

    def test_a_renamed_shortname_is_reported_until_a_rebuild(self, indexed):
        """The review's probe: rename the shortname `ops` to `o` and sync. The
        file's hash is unchanged, so the row keeps `ops-kb` while
        `resolve_entry` now misses the link; `index health` says so
        (`links_stale_kb`). A rebuild rewrites the row from the file, after
        which the link is broken, which it now is, and nothing is stale: a
        rebuild clears what the counter counts, by definition."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.storage.index import IndexManager

        config, db = indexed
        dev, ops = config.knowledge_bases
        renamed = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=dev.name, path=dev.path, shortname=dev.shortname),
                KBConfig(name=ops.name, path=ops.path, shortname="o"),
            ],
            settings=Settings(index_path=config.settings.index_path),
        )
        after_sync = IndexManager(db, renamed)
        after_sync.sync_kb(renamed.knowledge_bases[0])
        # One row per written link (#1009): `[[ops:runbook]]`, `![[ops:runbook]]`
        # and the `references` item write `ops`, `[[ops-kb:runbook]]` the full
        # name; all four resolved to `ops-kb` when indexed.
        rows = db.execute_sql("SELECT target_kb FROM link WHERE source_id = 'note'", {})
        assert [r["target_kb"] for r in rows] == ["ops-kb"] * 4  # the file was not re-read
        health = after_sync.check_health()
        assert health["links_stale_kb"] == 3
        assert health["broken_links"] == 0

        after_sync.reconcile_kb(renamed.knowledge_bases[0], force=True)
        rows = db.execute_sql(
            "SELECT relation, target_prefix, target_kb FROM link WHERE source_id = 'note'", {}
        )
        assert sorted((r["relation"], r["target_prefix"], r["target_kb"]) for r in rows) == [
            ("references", "ops", "ops"),
            ("transclusion", "ops", "ops"),
            ("wikilink", "ops", "ops"),
            ("wikilink", "ops-kb", "ops-kb"),
        ]
        health = after_sync.check_health()
        assert health["links_stale_kb"] == 0
        assert health["broken_links"] == 3

    def test_a_link_to_no_kb_is_broken_not_stale(self, tmp_path):
        """`[[nowhere:x]]` names no KB: it is a broken link, and a rebuild
        would write the same row, so it is not stale. Once a KB takes the
        shortname `nowhere`, a rebuild would point the row at that KB, and
        until then it is stale."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        config = self._config(tmp_path)
        dev, ops = config.knowledge_bases
        KBRepository(dev).save(NoteEntry(id="dangling", title="D", body="[[nowhere:x]]"))
        db = PyriteDB(config.settings.index_path)
        try:
            IndexManager(db, config).sync_kb(dev)
            health = IndexManager(db, config).check_health()
            assert health["links_stale_kb"] == 0
            assert health["broken_links"] == 1

            named = PyriteConfig(
                knowledge_bases=[
                    KBConfig(name=dev.name, path=dev.path, shortname=dev.shortname),
                    KBConfig(name=ops.name, path=ops.path, shortname="nowhere"),
                ],
                settings=Settings(index_path=config.settings.index_path),
            )
            assert IndexManager(db, named).check_health()["links_stale_kb"] == 1
        finally:
            db.close()

    def test_a_row_indexed_before_the_prefix_was_kept_is_still_reported(self, indexed):
        """A row from before this change has no `target_prefix`; its
        `target_kb` is the prefix the file wrote (the old rule). Counted in
        `links_stale_kb` when that prefix now resolves to another KB (a
        shortname), not when it names no KB (a rebuild writes the same row).
        The raw insert is the only way to make such a row from this code."""
        from pyrite.storage.index import IndexManager

        config, db = indexed
        assert IndexManager(db, config).check_health()["links_stale_kb"] == 0
        for target_kb in ("ops", "nowhere"):
            db._raw_conn.execute(
                "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, "
                "inverse_relation, note) VALUES ('note', 'dev-kb', 'other', :kb, 'wikilink', "
                "'wikilink', '')",
                {"kb": target_kb},
            )
        db._raw_conn.commit()
        health = IndexManager(db, config).check_health()
        assert health["links_stale_kb"] == 1
        assert health["broken_links"] == 2  # both are broken

    def test_duplicate_shortnames_take_the_first_kb_in_config(self, tmp_path):
        """Two KBs sharing a shortname (config.validate warns): the indexer,
        unscoped, takes the first in config order, the same KB
        `PyriteConfig.readable_kb_named` gives an unscoped caller."""
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.models import NoteEntry
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager
        from pyrite.storage.repository import KBRepository

        for name in ("first", "second", "src"):
            (tmp_path / name).mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="first", path=tmp_path / "first", shortname="s"),
                KBConfig(name="second", path=tmp_path / "second", shortname="s"),
                KBConfig(name="src", path=tmp_path / "src"),
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        KBRepository(config.knowledge_bases[2]).save(NoteEntry(id="n", title="N", body="[[s:x]]"))
        db = PyriteDB(config.settings.index_path)
        try:
            IndexManager(db, config).sync_kb(config.knowledge_bases[2])
            rows = db.execute_sql("SELECT target_kb FROM link WHERE source_id = 'n'", {})
            assert [r["target_kb"] for r in rows] == ["first"]
            assert config.readable_kb_named("s", readable_kbs=None).name == "first"
            # Pinned, not refused: config load warns about the pair.
            assert any("share shortname 's'" in m for m in config.validate()["shortnames"])
        finally:
            db.close()


def test_index_health_reports_a_renamed_shortname_and_names_the_command_that_clears_it(
    tmp_path, monkeypatch
):
    """The probe through the real CLI and a real config.yaml (#980): index,
    rename the shortname `ops` to `o` in the file, sync. `index health` warns
    with `links_stale_kb` 1 and names a command; running that command clears
    the warning. The command is taken from the warning itself, so a message
    naming a command that does not exist fails here."""
    import json
    import re
    import shlex

    from click import unstyle
    from typer.testing import CliRunner

    import pyrite.config as config_module
    from pyrite.cli import app
    from pyrite.config import KBConfig, PyriteConfig, Settings
    from pyrite.models import NoteEntry
    from pyrite.storage.repository import KBRepository
    from pyrite.utils.yaml import dump_yaml_file, load_yaml_file

    dev = KBConfig(name="dev-kb", path=tmp_path / "kb1", shortname="dev")
    ops = KBConfig(name="ops-kb", path=tmp_path / "kb2", shortname="ops")
    for kb in (dev, ops):
        kb.path.mkdir()
    KBRepository(ops).save(NoteEntry(id="runbook", title="Runbook", body="ops body"))
    KBRepository(dev).save(NoteEntry(id="note", title="Note", body="See [[ops:runbook]]."))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.yaml"
    config = PyriteConfig(knowledge_bases=[dev, ops], settings=Settings(auto_embed=False))
    dump_yaml_file(config.to_dict(), config_file)
    monkeypatch.setenv("PYRITE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)
    runner = CliRunner()

    def run(*args):
        result = runner.invoke(app, list(args))
        assert result.exception is None or isinstance(result.exception, SystemExit), result.output
        return result

    def health_json():
        return json.loads(run("index", "health", "--format", "json").output)

    assert run("index", "build", "--no-embed").exit_code == 0
    assert health_json()["checks"]["links_stale_kb"] == 0

    data = load_yaml_file(config_file)
    for kb in data["knowledge_bases"]:
        if kb["name"] == "ops-kb":
            kb["shortname"] = "o"
    dump_yaml_file(data, config_file)
    assert run("index", "sync", "--no-embed").exit_code == 0

    report = health_json()
    assert report["checks"]["links_stale_kb"] == 1
    assert report["status"] == "warning", report

    rich = " ".join(unstyle(run("index", "health", "--format", "rich").output).split())
    named = re.search(r"Run '(pyrite [^']+)'", rich[rich.index("link row") :])
    assert named, rich
    argv = shlex.split(named.group(1))[1:]
    assert run(*argv, "--no-embed").exit_code == 0, f"{named.group(1)} failed"

    assert health_json()["checks"]["links_stale_kb"] == 0
    assert "link row" not in " ".join(
        unstyle(run("index", "health", "--format", "rich").output).split()
    )
