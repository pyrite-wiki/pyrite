"""
Tests for QAService — structural validation of knowledge bases.
"""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.models import EventEntry, NoteEntry, PersonEntry
from pyrite.models.task import TaskEntry
from pyrite.services.qa_service import QAService
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager
from pyrite.storage.repository import KBRepository
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def qa_setup():
    """Create a QAService with seeded test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        db_path = tmpdir / "index.db"

        # Create KB directories
        events_path = tmpdir / "events"
        events_path.mkdir()
        research_path = tmpdir / "research"
        research_path.mkdir()
        (research_path / "actors").mkdir()

        events_kb = KBConfig(
            name="test-events",
            path=events_path,
            kb_type=KBType.EVENTS,
            description="Test events KB",
        )
        research_kb = KBConfig(
            name="test-research",
            path=research_path,
            kb_type=KBType.RESEARCH,
            description="Test research KB",
        )

        config = PyriteConfig(
            knowledge_bases=[events_kb, research_kb],
            settings=Settings(index_path=db_path),
        )

        # Create valid entries
        events_repo = KBRepository(events_kb)
        for i in range(3):
            event = EventEntry.create(
                date=f"2025-01-{10 + i:02d}",
                title=f"Test Event {i}",
                body=f"This is test event {i} about policy.",
                importance=5 + i,
            )
            event.tags = ["test"]
            events_repo.save(event)

        research_repo = KBRepository(research_kb)
        person = PersonEntry.create(name="Test Person", role="Policy researcher", importance=7)
        person.body = "Test person description body."
        person.tags = ["research"]
        research_repo.save(person)

        db = PyriteDB(db_path)
        index_mgr = IndexManager(db, config)
        index_mgr.index_all()

        qa = QAService(config, db)

        yield {
            "qa": qa,
            "config": config,
            "db": db,
            "events_kb": events_kb,
            "research_kb": research_kb,
            "events_repo": events_repo,
            "research_repo": research_repo,
            "index_mgr": index_mgr,
            "tmpdir": tmpdir,
        }

        db.close()


# =========================================================================
# Bulk SQL validation tests
# =========================================================================


class TestValidateCleanKB:
    """Test validation on clean data."""

    def test_validate_clean_kb_no_issues(self, qa_setup):
        """Seeded KB with valid entries returns no errors."""
        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        errors = [i for i in result["issues"] if i["severity"] == "error"]
        assert errors == [], f"Unexpected errors: {errors}"

    def test_validate_detects_empty_body(self, qa_setup):
        """Entry with empty body flagged as warning."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("empty-body-entry", "test-events", "note", "Empty Body Note", "", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        body_issues = [i for i in result["issues"] if i["rule"] == "empty_body"]
        assert any(i["entry_id"] == "empty-body-entry" for i in body_issues)
        assert all(
            i["severity"] == "warning" for i in body_issues if i["entry_id"] == "empty-body-entry"
        )

    def test_validate_reports_a_body_line_leaking_frontmatter(self, qa_setup):
        path = qa_setup["events_kb"].path / "events" / "leaked.md"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "---\nid: leaked\ntype: event\ndate: '2025-02-01'\ntitle: Leaked\n"
            "importance: 5\nstatus: confirmed\n---\ntype: event\n",
            encoding="utf-8",
        )
        qa_setup["index_mgr"].index_kb("test-events")

        issues = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)["issues"]
        leak = [i for i in issues if i["rule"] == "frontmatter_line_in_body"]
        assert len(leak) == 1
        assert leak[0]["entry_id"] == "leaked"
        assert leak[0]["field"] == "type"

    def test_validate_skips_body_check_for_collections(self, qa_setup):
        """Collections exempt from empty body check."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("test-collection", "test-events", "collection", "My Collection", "", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        body_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "empty_body" and i["entry_id"] == "test-collection"
        ]
        assert body_issues == []

    def test_validate_detects_missing_title(self, qa_setup):
        """Entry with empty title flagged as error."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("no-title-entry", "test-events", "note", "", "Has body", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        title_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "missing_title" and i["entry_id"] == "no-title-entry"
        ]
        assert len(title_issues) == 1
        assert title_issues[0]["severity"] == "error"

    def test_validate_detects_event_missing_date(self, qa_setup):
        """Event without date flagged as error."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("dateless-event", "test-events", "event", "No Date Event", "Body", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        date_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "event_missing_date" and i["entry_id"] == "dateless-event"
        ]
        assert len(date_issues) == 1
        assert date_issues[0]["severity"] == "error"

    def test_validate_detects_broken_link(self, qa_setup):
        """Link to non-existent target flagged as warning (wiki-style wanted page)."""
        db = qa_setup["db"]
        # Insert a link pointing to a non-existent entry
        db._raw_conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "2025-01-10--test-event-0",
                "test-events",
                "nonexistent",
                "test-events",
                "related_to",
                "related_to",
            ),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        broken = [i for i in result["issues"] if i["rule"] == "broken_link"]
        assert len(broken) >= 1
        # Broken wikilinks represent wanted-but-not-yet-created pages in a
        # wiki, not data integrity errors. Keep them visible (warning) but
        # don't fail CI (`pyrite qa validate` exits 1 only on errors).
        assert broken[0]["severity"] == "warning"

    def test_validate_detects_invalid_date(self, qa_setup):
        """Entry with bad date format flagged as error."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, date, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("bad-date-entry", "test-events", "event", "Bad Date", "Body", "not-a-date", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        date_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "invalid_date" and i["entry_id"] == "bad-date-entry"
        ]
        assert len(date_issues) == 1
        assert date_issues[0]["severity"] == "error"

    def test_validate_detects_importance_out_of_range(self, qa_setup):
        """Importance > 10 flagged as warning."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("bad-importance", "test-events", "note", "Over Importance", "Body", 15),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        imp_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "importance_range" and i["entry_id"] == "bad-importance"
        ]
        assert len(imp_issues) == 1
        assert imp_issues[0]["severity"] == "warning"


# =========================================================================
# Schema validation tests
# =========================================================================


class TestSchemaValidation:
    def test_validate_with_schema_catches_field_violations(self, qa_setup):
        """KB with kb.yaml catches field type violations."""
        # Create a kb.yaml with enforced validation
        kb_yaml = qa_setup["events_kb"].path / "kb.yaml"
        kb_yaml.write_text(
            "name: test-events\n"
            "validation:\n"
            "  enforce: true\n"
            "types:\n"
            "  event:\n"
            "    fields:\n"
            "      severity:\n"
            "        type: select\n"
            "        options: [low, medium, high]\n"
            "        required: true\n"
        )
        # Invalidate cached schema so the new kb.yaml is picked up
        qa_setup["events_kb"].invalidate_schema_cache()

        # Insert an entry that violates required field
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, date, importance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("schema-test", "test-events", "event", "Schema Test", "Body", "2025-01-15", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        schema_issues = [
            i
            for i in result["issues"]
            if i["rule"] == "schema_violation" and i["entry_id"] == "schema-test"
        ]
        assert len(schema_issues) >= 1

    def test_validate_without_schema_skips_field_checks(self, qa_setup):
        """No kb.yaml means only bulk checks run, no schema violations."""
        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        schema_issues = [i for i in result["issues"] if i["rule"] == "schema_violation"]
        assert schema_issues == []


# =========================================================================
# Scope tests
# =========================================================================


class TestValidationScope:
    def test_validate_entry_single(self, qa_setup):
        """validate_entry returns issues for one entry only."""
        db = qa_setup["db"]
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("single-test", "test-events", "note", "", "Body", 5),
        )
        db._raw_conn.commit()

        result = qa_setup["qa"].validate_entry("single-test", "test-events", readable_kbs=UNSCOPED)
        assert result["entry_id"] == "single-test"
        assert result["kb_name"] == "test-events"
        assert any(i["rule"] == "missing_title" for i in result["issues"])

    def test_validate_kb_scoped(self, qa_setup):
        """validate_kb only checks entries in that KB."""
        db = qa_setup["db"]
        # Insert bad entry in research KB
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("research-bad", "test-research", "note", "", "Body", 5),
        )
        db._raw_conn.commit()

        # Validate events KB — should not find research-bad
        result = qa_setup["qa"].validate_kb("test-events", readable_kbs=UNSCOPED)
        research_issues = [i for i in result["issues"] if i["entry_id"] == "research-bad"]
        assert research_issues == []

    def test_validate_all_checks_every_kb(self, qa_setup):
        """validate_all aggregates across KBs."""
        result = qa_setup["qa"].validate_all(readable_kbs=UNSCOPED)
        assert "kbs" in result
        kb_names = [kb["kb_name"] for kb in result["kbs"]]
        assert "test-events" in kb_names
        assert "test-research" in kb_names

    def test_validate_all_includes_db_only_kb(self, tmp_path):
        """A KB registered only via `pyrite kb add` (DB-only, not in
        config.yaml's knowledge_bases) must be validated too --
        collapse-kb-registry-to-one-source-of-truth's all_kbs() sweep.
        validate_all() previously iterated config.knowledge_bases
        directly, silently excluding DB-only KBs from QA validation."""
        db_path = tmp_path / "index.db"
        kb_path = tmp_path / "db-only-kb"
        kb_path.mkdir()

        # NOT passed to PyriteConfig(knowledge_bases=...) -- DB-only.
        config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=db_path))
        db = PyriteDB(db_path)
        db.register_kb("db-only-kb", "generic", str(kb_path), "A DB-only KB", source="user")
        db.merge_registered_kbs(config)

        qa = QAService(config, db)
        result = qa.validate_all(readable_kbs=UNSCOPED)
        db.close()

        kb_names = [kb["kb_name"] for kb in result["kbs"]]
        assert "db-only-kb" in kb_names, f"expected DB-only KB in validate_all; got {kb_names}"


# =========================================================================
# Status tests
# =========================================================================


class TestStatus:
    def test_status_returns_counts(self, qa_setup):
        """get_status returns total_entries and issue breakdown."""
        db = qa_setup["db"]
        # Add an issue
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, importance) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("status-test", "test-events", "note", "", "Body", 15),
        )
        db._raw_conn.commit()

        status = qa_setup["qa"].get_status("test-events", readable_kbs=UNSCOPED)
        assert "total_entries" in status
        assert "total_issues" in status
        assert "issues_by_severity" in status
        assert "issues_by_rule" in status
        assert status["total_entries"] > 0
        assert status["total_issues"] > 0

    def test_status_empty_kb(self, qa_setup):
        """get_status on KB with no entries returns zeros."""
        # Create an empty KB
        empty_path = qa_setup["tmpdir"] / "empty-kb"
        empty_path.mkdir()
        empty_kb = KBConfig(name="empty-kb", path=empty_path, kb_type="generic")
        qa_setup["config"].add_kb(empty_kb)

        status = qa_setup["qa"].get_status("empty-kb", readable_kbs=UNSCOPED)
        assert status["total_entries"] == 0
        assert status["total_issues"] == 0


def _save_task(
    repo,
    task_id: str,
    *,
    status: str = "open",
    dependencies: list[str] | None = None,
    produces: list[str] | None = None,
    deliverable: list[str] | None = None,
) -> None:
    """Save a task whose free deliverable fields ride in ``metadata`` (#935)."""
    metadata = {}
    if produces is not None:
        metadata["produces"] = produces
    if deliverable is not None:
        metadata["deliverable"] = deliverable
    repo.save(
        TaskEntry(
            id=task_id,
            title=task_id.replace("-", " ").title(),
            body="Task body.",
            status=status,
            dependencies=list(dependencies or []),
            metadata=metadata,
        )
    )


def _task_reference_issues(qa_setup, readable_kbs=UNSCOPED):
    result = qa_setup["qa"].validate_kb("test-research", readable_kbs=readable_kbs)
    return [issue for issue in result["issues"] if issue["rule"] == "task_references"]


class TestTaskReferences:
    """`qa validate` checks a task's dependencies and deliverable (#935)."""

    def test_missing_dependency_is_an_error(self, qa_setup):
        _save_task(qa_setup["research_repo"], "task-a", dependencies=["no-such-entry"])
        qa_setup["index_mgr"].index_all()

        issues = _task_reference_issues(qa_setup)

        assert [(issue["severity"], issue["field"]) for issue in issues] == [
            ("error", "dependencies")
        ]
        assert "no-such-entry" in issues[0]["message"]

    def test_missing_deliverable_is_an_error(self, qa_setup):
        _save_task(qa_setup["research_repo"], "task-a", produces=["no-such-entry"])
        qa_setup["index_mgr"].index_all()

        issues = _task_reference_issues(qa_setup)

        assert [(issue["severity"], issue["field"]) for issue in issues] == [("error", "produces")]

    def test_a_deliverable_held_in_the_deliverable_field_is_checked_too(self, qa_setup):
        _save_task(qa_setup["research_repo"], "task-a", deliverable=["no-such-entry"])
        qa_setup["index_mgr"].index_all()

        issues = _task_reference_issues(qa_setup)

        assert [(issue["severity"], issue["field"]) for issue in issues] == [
            ("error", "deliverable")
        ]

    def test_existing_dependency_is_clean(self, qa_setup):
        person_id = qa_setup["db"].execute_sql(
            "SELECT id FROM entry WHERE kb_name = 'test-research'"
        )[0]["id"]
        _save_task(qa_setup["research_repo"], "task-a", dependencies=[person_id])
        qa_setup["index_mgr"].index_all()

        assert _task_reference_issues(qa_setup) == []

    def test_open_task_whose_deliverable_exists_is_a_warning(self, qa_setup):
        qa_setup["research_repo"].save(
            NoteEntry(id="delivered-report", title="Delivered Report", body="Done.")
        )
        _save_task(qa_setup["research_repo"], "task-a", produces=["delivered-report"])
        qa_setup["index_mgr"].index_all()

        issues = _task_reference_issues(qa_setup)

        assert [(issue["severity"], issue["field"]) for issue in issues] == [
            ("warning", "produces")
        ]
        assert "may be done" in issues[0]["message"]

    def test_a_deliverable_that_is_itself_a_task_is_not_a_warning(self, qa_setup):
        _save_task(qa_setup["research_repo"], "other-task")
        _save_task(qa_setup["research_repo"], "task-a", produces=["other-task"])
        qa_setup["index_mgr"].index_all()

        assert _task_reference_issues(qa_setup) == []

    def test_a_task_naming_itself_as_deliverable_is_ignored(self, qa_setup):
        _save_task(qa_setup["research_repo"], "task-a", produces=["task-a"])
        qa_setup["index_mgr"].index_all()

        assert _task_reference_issues(qa_setup) == []

    def test_a_closed_task_is_not_checked(self, qa_setup):
        _save_task(
            qa_setup["research_repo"],
            "task-a",
            status="done",
            dependencies=["no-such-entry"],
        )
        qa_setup["index_mgr"].index_all()

        assert _task_reference_issues(qa_setup) == []

    def test_cross_kb_deliverable_resolves(self, qa_setup):
        event_id = qa_setup["db"].execute_sql("SELECT id FROM entry WHERE kb_name = 'test-events'")[
            0
        ]["id"]
        _save_task(
            qa_setup["research_repo"],
            "task-a",
            produces=[f"test-events:{event_id}"],
        )
        qa_setup["index_mgr"].index_all()

        issues = _task_reference_issues(qa_setup)

        assert [(issue["severity"], issue["field"]) for issue in issues] == [
            ("warning", "produces")
        ]

    def test_a_target_the_caller_cannot_read_is_missing(self, qa_setup):
        event_id = qa_setup["db"].execute_sql("SELECT id FROM entry WHERE kb_name = 'test-events'")[
            0
        ]["id"]
        _save_task(
            qa_setup["research_repo"],
            "task-a",
            dependencies=[f"test-events:{event_id}"],
        )
        qa_setup["index_mgr"].index_all()

        issues = _task_reference_issues(qa_setup, readable_kbs={"test-research"})

        assert [(issue["severity"], issue["field"]) for issue in issues] == [
            ("error", "dependencies")
        ]

    def test_qa_validate_exits_1_on_a_missing_task_reference(self, cli_world):
        world = cli_world()
        folder = world.kb_path("test-kb")
        (folder / "task-a.md").write_text(
            "---\n"
            "id: task-a\n"
            "type: task\n"
            "title: Task A\n"
            "status: open\n"
            "dependencies:\n"
            "  - no-such-entry\n"
            "---\n"
            "Task body.\n",
            encoding="utf-8",
        )
        world.index()

        result = world.cli("qa", "validate", "test-kb", "--format", "json")

        assert result.exit_code == 1, result.output
        assert "task_references" in result.output
