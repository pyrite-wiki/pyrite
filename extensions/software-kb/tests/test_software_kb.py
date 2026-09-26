"""Tests for the Software KB extension."""

import json
import tempfile
from pathlib import Path

import pytest
from pyrite_software_kb.entry_types import (
    ADR_STATUSES,
    BACKLOG_EFFORTS,
    BACKLOG_KINDS,
    BACKLOG_PRIORITIES,
    BACKLOG_STATUSES,
    COMPONENT_KINDS,
    CONVENTION_CATEGORIES,
    DESIGN_DOC_STATUSES,
    MILESTONE_STATUSES,
    RUNBOOK_KINDS,
    STANDARD_CATEGORIES,
    VALIDATION_CATEGORIES,
    ADREntry,
    BacklogItemEntry,
    ComponentEntry,
    DesignDocEntry,
    DevelopmentConventionEntry,
    MilestoneEntry,
    ProgrammaticValidationEntry,
    RunbookEntry,
    StandardEntry,
    WorkLogEntry,
)
from pyrite_software_kb.plugin import SoftwareKBPlugin
from pyrite_software_kb.preset import SOFTWARE_KB_PRESET
from pyrite_software_kb.validators import validate_software_kb
from pyrite_software_kb.workflows import (
    ADR_LIFECYCLE,
    BACKLOG_WORKFLOW,
    can_transition,
    get_allowed_transitions,
    requires_reason,
)

from pyrite.plugins.registry import PluginRegistry
from pyrite.services.access_policy import UNSCOPED

# =========================================================================
# Plugin registration
# =========================================================================


class TestPluginRegistration:
    def test_plugin_has_name(self):
        plugin = SoftwareKBPlugin()
        assert plugin.name == "software_kb"

    def test_register_with_registry(self):
        registry = PluginRegistry()
        plugin = SoftwareKBPlugin()
        registry.register(plugin)
        assert "software_kb" in registry.list_plugins()

    def test_entry_types_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        types = registry.get_all_entry_types()
        assert "adr" in types
        assert "design_doc" in types
        assert "standard" in types
        assert "programmatic_validation" in types
        assert "development_convention" in types
        assert "component" in types
        assert "backlog_item" in types
        assert "runbook" in types
        assert types["adr"] is ADREntry
        assert types["design_doc"] is DesignDocEntry
        assert types["programmatic_validation"] is ProgrammaticValidationEntry
        assert types["development_convention"] is DevelopmentConventionEntry

    def test_relationship_types_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        rels = registry.get_all_relationship_types()
        assert "implements" in rels
        assert "supersedes" in rels
        assert "documents" in rels
        assert "depends_on" in rels
        assert "tracks" in rels
        assert rels["implements"]["inverse"] == "implemented_by"
        assert rels["supersedes"]["inverse"] == "superseded_by"

    def test_validators_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        validators = registry.get_all_validators()
        assert validate_software_kb in validators

    def test_cli_commands_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        commands = registry.get_all_cli_commands()
        cmd_names = [name for name, _ in commands]
        assert "sw" in cmd_names

    def test_mcp_read_tools_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        tools = registry.get_all_mcp_tools("read")
        assert "sw_adrs" in tools
        assert "sw_component" in tools
        assert "sw_standards" in tools
        assert "sw_validations" in tools
        assert "sw_conventions" in tools
        assert "sw_backlog" in tools

    def test_mcp_write_tools_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        tools = registry.get_all_mcp_tools("write")
        assert "sw_create_adr" in tools
        assert "sw_create_backlog_item" in tools
        # Read tools also available at write tier
        assert "sw_adrs" in tools

    def test_mcp_read_tier_no_write_tools(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        tools = registry.get_all_mcp_tools("read")
        assert "sw_create_adr" not in tools
        assert "sw_create_backlog_item" not in tools

    def test_kb_presets_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        presets = registry.get_all_kb_presets()
        assert "software" in presets

    def test_kb_types_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        kb_types = registry.get_all_kb_types()
        assert "software" in kb_types

    def test_workflows_registered(self):
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        workflows = registry.get_all_workflows()
        assert "adr_lifecycle" in workflows
        assert "backlog_workflow" in workflows

    def test_no_db_tables(self):
        plugin = SoftwareKBPlugin()
        assert plugin.get_db_tables() == []


# =========================================================================
# Entry types — ADR
# =========================================================================


class TestADREntry:
    def test_default_values(self):
        entry = ADREntry(id="test", title="Test")
        assert entry.entry_type == "adr"
        assert entry.adr_number == 0
        assert entry.status == "proposed"
        assert entry.deciders == []
        assert entry.date == ""
        assert entry.superseded_by == ""

    def test_to_frontmatter(self):
        entry = ADREntry(
            id="test",
            title="Use PostgreSQL",
            adr_number=1,
            status="accepted",
            deciders=["alice", "bob"],
            date="2026-01-15",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "adr"
        assert fm["adr_number"] == 1
        assert fm["status"] == "accepted"
        assert fm["deciders"] == ["alice", "bob"]
        assert fm["date"] == "2026-01-15"

    def test_to_frontmatter_omits_defaults(self):
        entry = ADREntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert fm["adr_number"] == 0  # always written (0 is valid)
        assert fm["status"] == "proposed"  # always written (enum field)
        assert "deciders" not in fm
        assert "date" not in fm
        assert "superseded_by" not in fm

    def test_from_frontmatter(self):
        meta = {
            "id": "adr-001",
            "title": "Use PostgreSQL",
            "type": "adr",
            "adr_number": 1,
            "status": "accepted",
            "deciders": ["alice"],
            "date": "2026-01-15",
            "tags": ["database"],
        }
        entry = ADREntry.from_frontmatter(meta, "## Context\n\nWe need a database.")
        assert entry.id == "adr-001"
        assert entry.adr_number == 1
        assert entry.status == "accepted"
        assert entry.deciders == ["alice"]
        assert entry.date == "2026-01-15"
        assert entry.tags == ["database"]
        assert "Context" in entry.body

    def test_from_frontmatter_generates_id(self):
        meta = {"title": "Use Event Sourcing", "type": "adr"}
        entry = ADREntry.from_frontmatter(meta, "")
        assert entry.id == "use-event-sourcing"

    def test_roundtrip_markdown(self):
        entry = ADREntry(
            id="adr-001",
            title="Use PostgreSQL",
            body="## Context\n\nDatabase choice.",
            adr_number=1,
            status="accepted",
            tags=["database"],
        )
        md = entry.to_markdown()
        assert "adr_number: 1" in md
        assert "status: accepted" in md
        assert "Database choice." in md

    def test_superseded_entry(self):
        entry = ADREntry(
            id="adr-001",
            title="Old Decision",
            status="superseded",
            superseded_by="adr-002",
        )
        fm = entry.to_frontmatter()
        assert fm["status"] == "superseded"
        assert fm["superseded_by"] == "adr-002"


# =========================================================================
# Entry types — DesignDoc
# =========================================================================


class TestDesignDocEntry:
    def test_default_values(self):
        entry = DesignDocEntry(id="test", title="Test")
        assert entry.entry_type == "design_doc"
        assert entry.status == "draft"
        assert entry.reviewers == []
        # Inherits from DocumentEntry
        assert entry.date == ""
        assert entry.author == ""
        assert entry.url == ""

    def test_to_frontmatter(self):
        entry = DesignDocEntry(
            id="test",
            title="Auth Design",
            status="review",
            reviewers=["alice", "bob"],
            date="2026-02-01",
            author="charlie",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "design_doc"
        assert fm["status"] == "review"
        assert fm["reviewers"] == ["alice", "bob"]
        assert fm["date"] == "2026-02-01"
        assert fm["author"] == "charlie"

    def test_to_frontmatter_omits_defaults(self):
        entry = DesignDocEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "status" not in fm  # draft is default
        assert "reviewers" not in fm

    def test_from_frontmatter(self):
        meta = {
            "id": "auth-design",
            "title": "Auth Design",
            "type": "design_doc",
            "status": "approved",
            "reviewers": ["alice"],
            "author": "bob",
            "date": "2026-02-01",
        }
        entry = DesignDocEntry.from_frontmatter(meta, "Design content")
        assert entry.status == "approved"
        assert entry.reviewers == ["alice"]
        assert entry.author == "bob"
        assert entry.date == "2026-02-01"


# =========================================================================
# Entry types — Standard
# =========================================================================


class TestStandardEntry:
    def test_default_values(self):
        entry = StandardEntry(id="test", title="Test")
        assert entry.entry_type == "standard"
        assert entry.category == ""
        assert entry.enforced is False

    def test_to_frontmatter(self):
        entry = StandardEntry(
            id="test", title="Naming Convention", category="coding", enforced=True
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "standard"
        assert fm["category"] == "coding"
        assert fm["enforced"] is True

    def test_to_frontmatter_omits_defaults(self):
        entry = StandardEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "category" not in fm
        assert "enforced" not in fm

    def test_from_frontmatter(self):
        meta = {
            "id": "naming",
            "title": "Naming Convention",
            "type": "standard",
            "category": "coding",
            "enforced": True,
        }
        entry = StandardEntry.from_frontmatter(meta, "Use snake_case")
        assert entry.category == "coding"
        assert entry.enforced is True


# =========================================================================
# Entry types — ProgrammaticValidation
# =========================================================================


class TestProgrammaticValidationEntry:
    def test_entry_type(self):
        entry = ProgrammaticValidationEntry(id="test", title="Test")
        assert entry.entry_type == "programmatic_validation"

    def test_default_values(self):
        entry = ProgrammaticValidationEntry(id="test", title="Test")
        assert entry.category == ""
        assert entry.check_command == ""
        assert entry.pass_criteria == ""

    def test_to_frontmatter(self):
        entry = ProgrammaticValidationEntry(
            id="test",
            title="Ruff Linting",
            category="coding",
            check_command="ruff check .",
            pass_criteria="exit code 0",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "programmatic_validation"
        assert fm["category"] == "coding"
        assert fm["check_command"] == "ruff check ."
        assert fm["pass_criteria"] == "exit code 0"

    def test_to_frontmatter_omits_defaults(self):
        entry = ProgrammaticValidationEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "category" not in fm
        assert "check_command" not in fm
        assert "pass_criteria" not in fm

    def test_roundtrip(self):
        entry = ProgrammaticValidationEntry(
            id="test",
            title="Ruff Linting",
            category="coding",
            check_command="ruff check .",
            pass_criteria="exit code 0",
            tags=["ci"],
        )
        fm = entry.to_frontmatter()
        restored = ProgrammaticValidationEntry.from_frontmatter(fm, entry.body)
        assert restored.entry_type == "programmatic_validation"
        assert restored.category == "coding"
        assert restored.check_command == "ruff check ."
        assert restored.pass_criteria == "exit code 0"
        assert restored.tags == ["ci"]

    def test_validation_category_enum(self):
        errors = validate_software_kb(
            "programmatic_validation", {"category": "coding", "check_command": "x"}, {}
        )
        non_warnings = [e for e in errors if e.get("severity") != "warning"]
        assert non_warnings == []

    def test_invalid_category(self):
        errors = validate_software_kb(
            "programmatic_validation", {"category": "invalid", "check_command": "x"}, {}
        )
        assert any(e["field"] == "category" for e in errors)

    def test_check_command_warning(self):
        errors = validate_software_kb("programmatic_validation", {"category": "coding"}, {})
        warnings = [e for e in errors if e.get("severity") == "warning"]
        assert any(e["rule"] == "check_command_recommended" for e in warnings)

    def test_no_warning_with_check_command(self):
        errors = validate_software_kb(
            "programmatic_validation", {"check_command": "ruff check ."}, {}
        )
        assert not any(e["rule"] == "check_command_recommended" for e in errors)


# =========================================================================
# Entry types — DevelopmentConvention
# =========================================================================


class TestDevelopmentConventionEntry:
    def test_entry_type(self):
        entry = DevelopmentConventionEntry(id="test", title="Test")
        assert entry.entry_type == "development_convention"

    def test_default_values(self):
        entry = DevelopmentConventionEntry(id="test", title="Test")
        assert entry.category == ""

    def test_to_frontmatter(self):
        entry = DevelopmentConventionEntry(id="test", title="Naming Convention", category="coding")
        fm = entry.to_frontmatter()
        assert fm["type"] == "development_convention"
        assert fm["category"] == "coding"

    def test_to_frontmatter_omits_defaults(self):
        entry = DevelopmentConventionEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "category" not in fm

    def test_roundtrip(self):
        entry = DevelopmentConventionEntry(
            id="test", title="Naming Convention", category="coding", tags=["style"]
        )
        fm = entry.to_frontmatter()
        restored = DevelopmentConventionEntry.from_frontmatter(fm, entry.body)
        assert restored.entry_type == "development_convention"
        assert restored.category == "coding"
        assert restored.tags == ["style"]

    def test_validation_category_enum(self):
        errors = validate_software_kb("development_convention", {"category": "coding"}, {})
        assert errors == []

    def test_invalid_category(self):
        errors = validate_software_kb("development_convention", {"category": "invalid"}, {})
        assert any(e["field"] == "category" for e in errors)


# =========================================================================
# Standards Migration
# =========================================================================


class TestStandardsMigration:
    def _write_standard_file(
        self, path: Path, title: str, enforced: bool, category: str = "coding"
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        enforced_str = "true" if enforced else "false"
        path.write_text(
            f"---\ntype: standard\ntitle: {title}\ncategory: {category}\nenforced: {enforced_str}\n---\n\nBody text.\n"
        )

    def test_dry_run_no_changes(self, tmp_path):
        """Dry run should not modify any files."""
        standards_dir = tmp_path / "standards"
        std_file = standards_dir / "test.md"
        self._write_standard_file(std_file, "Test Standard", enforced=False)

        original_content = std_file.read_text()

        from pyrite.storage.database import PyriteDB

        db_path = tmp_path / "test.db"
        db = PyriteDB(db_path)
        try:
            db._raw_conn.execute(
                "INSERT INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                ("test", str(tmp_path), "generic"),
            )
            db._raw_conn.execute(
                "INSERT INTO entry (id, kb_name, entry_type, title, body, file_path, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "test-std",
                    "test",
                    "standard",
                    "Test Standard",
                    "Body text.",
                    str(std_file),
                    json.dumps({"category": "coding", "enforced": False}),
                ),
            )
            db._raw_conn.commit()

            from pyrite_software_kb.cli import _query_entries

            rows = _query_entries(db, "standard")
            assert len(rows) == 1

            # Verify file unchanged (simulating dry run logic)
            assert std_file.read_text() == original_content
        finally:
            db.close()

    def test_enforced_becomes_validation(self, tmp_path):
        """An enforced standard should become programmatic_validation."""
        standards_dir = tmp_path / "standards"
        std_file = standards_dir / "ruff.md"
        self._write_standard_file(std_file, "Ruff Linting", enforced=True)

        import re

        content = std_file.read_text()
        content = re.sub(
            r"^type:\s*standard\s*$", "type: programmatic_validation", content, flags=re.MULTILINE
        )
        content = re.sub(
            r"^enforced:\s*(true|false)\s*\n", "", content, flags=re.MULTILINE | re.IGNORECASE
        )

        new_dir = tmp_path / "validations"
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / "ruff.md"
        new_path.write_text(content)

        assert "type: programmatic_validation" in new_path.read_text()
        assert "enforced:" not in new_path.read_text()
        assert new_path.exists()

    def test_non_enforced_becomes_convention(self, tmp_path):
        """A non-enforced standard should become development_convention."""
        standards_dir = tmp_path / "standards"
        std_file = standards_dir / "naming.md"
        self._write_standard_file(std_file, "Naming Convention", enforced=False)

        import re

        content = std_file.read_text()
        content = re.sub(
            r"^type:\s*standard\s*$", "type: development_convention", content, flags=re.MULTILINE
        )
        content = re.sub(
            r"^enforced:\s*(true|false)\s*\n", "", content, flags=re.MULTILINE | re.IGNORECASE
        )

        new_dir = tmp_path / "conventions"
        new_dir.mkdir(parents=True, exist_ok=True)
        new_path = new_dir / "naming.md"
        new_path.write_text(content)

        assert "type: development_convention" in new_path.read_text()
        assert "enforced:" not in new_path.read_text()
        assert new_path.exists()


# =========================================================================
# Entry types — Component
# =========================================================================


class TestComponentEntry:
    def test_default_values(self):
        entry = ComponentEntry(id="test", title="Test")
        assert entry.entry_type == "component"
        assert entry.kind == ""
        assert entry.path == ""
        assert entry.owner == ""
        assert entry.dependencies == []

    def test_to_frontmatter(self):
        entry = ComponentEntry(
            id="test",
            title="Plugin System",
            kind="module",
            path="pyrite/plugins/",
            owner="alice",
            dependencies=["pyrite.models"],
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "component"
        assert fm["kind"] == "module"
        assert fm["path"] == "pyrite/plugins/"
        assert fm["owner"] == "alice"
        assert fm["dependencies"] == ["pyrite.models"]

    def test_from_frontmatter(self):
        meta = {
            "id": "plugins",
            "title": "Plugin System",
            "type": "component",
            "kind": "module",
            "path": "pyrite/plugins/",
            "dependencies": ["pyrite.models"],
        }
        entry = ComponentEntry.from_frontmatter(meta, "Docs")
        assert entry.kind == "module"
        assert entry.path == "pyrite/plugins/"
        assert entry.dependencies == ["pyrite.models"]


# =========================================================================
# Entry types — BacklogItem
# =========================================================================


class TestBacklogItemEntry:
    def test_default_values(self):
        entry = BacklogItemEntry(id="test", title="Test")
        assert entry.entry_type == "backlog_item"
        assert entry.kind == ""
        assert entry.status == "proposed"
        assert entry.priority == "medium"
        assert entry.assignee == ""
        assert entry.effort == ""

    def test_to_frontmatter(self):
        entry = BacklogItemEntry(
            id="test",
            title="Add caching",
            kind="feature",
            status="accepted",
            priority="high",
            assignee="alice",
            effort="M",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "backlog_item"
        assert fm["kind"] == "feature"
        assert fm["status"] == "accepted"
        assert fm["priority"] == "high"
        assert fm["assignee"] == "alice"
        assert fm["effort"] == "M"

    def test_to_frontmatter_omits_defaults(self):
        entry = BacklogItemEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "kind" not in fm
        assert fm["status"] == "proposed"  # always written (enum field)
        assert fm["priority"] == "medium"  # always written (enum field)
        assert fm["rank"] == 0  # always written (0 is valid)
        assert "assignee" not in fm
        assert "effort" not in fm

    def test_from_frontmatter(self):
        meta = {
            "id": "caching",
            "title": "Add Caching",
            "type": "backlog_item",
            "kind": "feature",
            "status": "in_progress",
            "priority": "high",
            "effort": "L",
        }
        entry = BacklogItemEntry.from_frontmatter(meta, "Details")
        assert entry.kind == "feature"
        assert entry.status == "in_progress"
        assert entry.priority == "high"
        assert entry.effort == "L"


# =========================================================================
# Entry types — Runbook
# =========================================================================


class TestRunbookEntry:
    def test_default_values(self):
        entry = RunbookEntry(id="test", title="Test")
        assert entry.entry_type == "runbook"
        assert entry.runbook_kind == ""
        assert entry.audience == ""

    def test_to_frontmatter(self):
        entry = RunbookEntry(
            id="test",
            title="Deploy Guide",
            runbook_kind="operations",
            audience="ops-team",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "runbook"
        assert fm["runbook_kind"] == "operations"
        assert fm["audience"] == "ops-team"

    def test_to_frontmatter_omits_defaults(self):
        entry = RunbookEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "runbook_kind" not in fm
        assert "audience" not in fm

    def test_from_frontmatter(self):
        meta = {
            "id": "deploy",
            "title": "Deploy Guide",
            "type": "runbook",
            "runbook_kind": "operations",
            "audience": "ops-team",
        }
        entry = RunbookEntry.from_frontmatter(meta, "Steps")
        assert entry.runbook_kind == "operations"
        assert entry.audience == "ops-team"


# =========================================================================
# Validators
# =========================================================================


class TestValidators:
    # ADR validators
    def test_adr_valid(self):
        errors = validate_software_kb(
            "adr", {"adr_number": 1, "status": "accepted", "date": "2026-01-01"}, {}
        )
        non_warnings = [e for e in errors if e.get("severity") != "warning"]
        assert non_warnings == []

    def test_adr_invalid_status(self):
        errors = validate_software_kb("adr", {"status": "invalid", "date": "2026-01-01"}, {})
        assert any(e["field"] == "status" for e in errors)

    def test_adr_number_must_be_positive(self):
        errors = validate_software_kb("adr", {"adr_number": -1, "date": "2026-01-01"}, {})
        assert any(e["field"] == "adr_number" for e in errors)

    def test_adr_superseded_requires_link(self):
        errors = validate_software_kb("adr", {"status": "superseded", "date": "2026-01-01"}, {})
        assert any(e["rule"] == "required_when_superseded" for e in errors)

    def test_adr_superseded_with_link_ok(self):
        errors = validate_software_kb(
            "adr",
            {"status": "superseded", "superseded_by": "adr-002", "date": "2026-01-01"},
            {},
        )
        assert not any(e["rule"] == "required_when_superseded" for e in errors)

    def test_adr_date_warning(self):
        errors = validate_software_kb("adr", {"adr_number": 1}, {})
        warnings = [e for e in errors if e.get("severity") == "warning"]
        assert any(e["rule"] == "date_recommended" for e in warnings)

    def test_adr_with_date_no_warning(self):
        errors = validate_software_kb("adr", {"date": "2026-01-01"}, {})
        assert not any(e["rule"] == "date_recommended" for e in errors)

    # Design doc validators
    def test_design_doc_valid(self):
        errors = validate_software_kb("design_doc", {"status": "approved"}, {})
        assert errors == []

    def test_design_doc_invalid_status(self):
        errors = validate_software_kb("design_doc", {"status": "invalid"}, {})
        assert any(e["field"] == "status" for e in errors)

    # Standard validators
    def test_standard_valid(self):
        errors = validate_software_kb("standard", {"category": "coding"}, {})
        assert errors == []

    def test_standard_invalid_category(self):
        errors = validate_software_kb("standard", {"category": "invalid"}, {})
        assert any(e["field"] == "category" for e in errors)

    def test_standard_no_category_ok(self):
        errors = validate_software_kb("standard", {}, {})
        assert errors == []

    # Component validators
    def test_component_valid(self):
        errors = validate_software_kb("component", {"kind": "module", "path": "pyrite/"}, {})
        non_warnings = [e for e in errors if e.get("severity") != "warning"]
        assert non_warnings == []

    def test_component_invalid_kind(self):
        errors = validate_software_kb("component", {"kind": "invalid", "path": "x/"}, {})
        assert any(e["field"] == "kind" for e in errors)

    def test_component_path_warning(self):
        errors = validate_software_kb("component", {"kind": "module"}, {})
        warnings = [e for e in errors if e.get("severity") == "warning"]
        assert any(e["rule"] == "path_recommended" for e in warnings)

    def test_component_path_exists_no_warning(self, tmp_path):
        (tmp_path / "pyrite" / "services").mkdir(parents=True)
        (tmp_path / "pyrite" / "services" / "foo.py").touch()
        context = {"kb_path": str(tmp_path)}
        errors = validate_software_kb(
            "component", {"kind": "module", "path": "pyrite/services/foo.py"}, context
        )
        warnings = [e for e in errors if e.get("rule") == "path_not_found"]
        assert warnings == []

    def test_component_path_not_found_warning(self, tmp_path):
        context = {"kb_path": str(tmp_path)}
        errors = validate_software_kb(
            "component", {"kind": "module", "path": "nonexistent/module/"}, context
        )
        warnings = [e for e in errors if e.get("rule") == "path_not_found"]
        assert len(warnings) == 1
        assert warnings[0]["severity"] == "warning"

    def test_component_path_check_skipped_without_context(self):
        errors = validate_software_kb("component", {"kind": "module", "path": "some/path/"}, {})
        assert not any(e.get("rule") == "path_not_found" for e in errors)

    # Backlog validators
    def test_backlog_valid(self):
        errors = validate_software_kb(
            "backlog_item", {"kind": "feature", "status": "proposed", "priority": "high"}, {}
        )
        assert errors == []

    def test_backlog_invalid_kind(self):
        errors = validate_software_kb("backlog_item", {"kind": "invalid"}, {})
        assert any(e["field"] == "kind" for e in errors)

    def test_backlog_invalid_status(self):
        errors = validate_software_kb("backlog_item", {"status": "invalid"}, {})
        assert any(e["field"] == "status" for e in errors)

    def test_backlog_invalid_priority(self):
        errors = validate_software_kb("backlog_item", {"priority": "invalid"}, {})
        assert any(e["field"] == "priority" for e in errors)

    def test_backlog_invalid_effort(self):
        errors = validate_software_kb("backlog_item", {"effort": "XXL"}, {})
        assert any(e["field"] == "effort" for e in errors)

    def test_backlog_valid_effort(self):
        errors = validate_software_kb("backlog_item", {"effort": "XL"}, {})
        assert not any(e["field"] == "effort" for e in errors)

    # Runbook validators
    def test_runbook_valid(self):
        errors = validate_software_kb("runbook", {"runbook_kind": "howto"}, {})
        assert errors == []

    def test_runbook_invalid_kind(self):
        errors = validate_software_kb("runbook", {"runbook_kind": "invalid"}, {})
        assert any(e["field"] == "runbook_kind" for e in errors)

    # Unrelated types
    def test_ignores_unrelated_types(self):
        errors = validate_software_kb("note", {"title": "regular note"}, {})
        assert errors == []


# =========================================================================
# Workflows
# =========================================================================


class TestADRLifecycle:
    def test_states(self):
        assert ADR_LIFECYCLE["states"] == [
            "proposed",
            "accepted",
            "rejected",
            "deprecated",
            "superseded",
        ]
        assert ADR_LIFECYCLE["initial"] == "proposed"
        assert ADR_LIFECYCLE["field"] == "status"

    def test_proposed_to_accepted(self):
        assert can_transition(ADR_LIFECYCLE, "proposed", "accepted", "write")

    def test_proposed_to_rejected(self):
        assert can_transition(ADR_LIFECYCLE, "proposed", "rejected", "write")

    def test_rejected_requires_reason(self):
        assert requires_reason(ADR_LIFECYCLE, "proposed", "rejected")

    def test_accepted_to_deprecated(self):
        assert can_transition(ADR_LIFECYCLE, "accepted", "deprecated", "write")

    def test_accepted_to_superseded(self):
        assert can_transition(ADR_LIFECYCLE, "accepted", "superseded", "write")

    def test_proposed_cannot_skip_to_deprecated(self):
        assert not can_transition(ADR_LIFECYCLE, "proposed", "deprecated", "write")

    def test_no_transition_without_role(self):
        assert not can_transition(ADR_LIFECYCLE, "proposed", "accepted", "")

    def test_allowed_transitions_from_accepted(self):
        allowed = get_allowed_transitions(ADR_LIFECYCLE, "accepted", "write")
        targets = [t["to"] for t in allowed]
        assert "deprecated" in targets
        assert "superseded" in targets


class TestBacklogWorkflow:
    def test_states(self):
        assert "proposed" in BACKLOG_WORKFLOW["states"]
        assert "done" in BACKLOG_WORKFLOW["states"]
        assert "wont_do" in BACKLOG_WORKFLOW["states"]

    def test_happy_path(self):
        assert can_transition(BACKLOG_WORKFLOW, "proposed", "accepted", "write")
        assert can_transition(BACKLOG_WORKFLOW, "accepted", "in_progress", "write")
        assert can_transition(BACKLOG_WORKFLOW, "in_progress", "done", "write")

    def test_wont_do_from_proposed(self):
        assert can_transition(BACKLOG_WORKFLOW, "proposed", "wont_do", "write")

    def test_wont_do_from_accepted(self):
        assert can_transition(BACKLOG_WORKFLOW, "accepted", "wont_do", "write")

    def test_reopen_from_done(self):
        assert can_transition(BACKLOG_WORKFLOW, "done", "accepted", "write")

    def test_wont_do_requires_reason(self):
        assert requires_reason(BACKLOG_WORKFLOW, "proposed", "wont_do")
        assert requires_reason(BACKLOG_WORKFLOW, "accepted", "wont_do")

    def test_reopen_requires_reason(self):
        assert requires_reason(BACKLOG_WORKFLOW, "done", "accepted")

    def test_normal_transitions_no_reason(self):
        assert not requires_reason(BACKLOG_WORKFLOW, "proposed", "accepted")
        assert not requires_reason(BACKLOG_WORKFLOW, "accepted", "in_progress")

    def test_proposed_to_planned(self):
        assert can_transition(BACKLOG_WORKFLOW, "proposed", "planned", "write")

    def test_planned_to_accepted(self):
        assert can_transition(BACKLOG_WORKFLOW, "planned", "accepted", "write")

    def test_done_to_retired(self):
        assert can_transition(BACKLOG_WORKFLOW, "done", "retired", "write")

    def test_proposed_to_deferred(self):
        assert can_transition(BACKLOG_WORKFLOW, "proposed", "deferred", "write")

    def test_planned_to_deferred(self):
        assert can_transition(BACKLOG_WORKFLOW, "planned", "deferred", "write")

    def test_deferred_to_proposed(self):
        assert can_transition(BACKLOG_WORKFLOW, "deferred", "proposed", "write")

    def test_cannot_skip_to_done(self):
        assert not can_transition(BACKLOG_WORKFLOW, "proposed", "done", "write")

    def test_accepted_to_proposed(self):
        assert can_transition(BACKLOG_WORKFLOW, "accepted", "proposed", "write")

    def test_accepted_to_proposed_requires_reason(self):
        assert requires_reason(BACKLOG_WORKFLOW, "accepted", "proposed")

    def test_accepted_to_deferred(self):
        assert can_transition(BACKLOG_WORKFLOW, "accepted", "deferred", "write")

    def test_accepted_to_deferred_no_reason(self):
        assert not requires_reason(BACKLOG_WORKFLOW, "accepted", "deferred")

    def test_in_progress_to_accepted(self):
        assert can_transition(BACKLOG_WORKFLOW, "in_progress", "accepted", "write")

    def test_in_progress_to_accepted_requires_reason(self):
        assert requires_reason(BACKLOG_WORKFLOW, "in_progress", "accepted")


# =========================================================================
# Preset
# =========================================================================


class TestPreset:
    def test_preset_structure(self):
        p = SOFTWARE_KB_PRESET
        assert p["name"] == "my-project"
        assert "adr" in p["types"]
        assert "design_doc" in p["types"]
        assert "standard" in p["types"]
        assert "programmatic_validation" in p["types"]
        assert "development_convention" in p["types"]
        assert "component" in p["types"]
        assert "backlog_item" in p["types"]
        assert "runbook" in p["types"]
        assert p["policies"]["team_owned"] is True
        assert p["policies"]["require_adr_number"] is True
        assert p["validation"]["enforce"] is True

    def test_preset_directories(self):
        dirs = SOFTWARE_KB_PRESET["directories"]
        assert "adrs" in dirs
        assert "designs" in dirs
        assert "standards" in dirs
        assert "validations" in dirs
        assert "conventions" in dirs
        assert "components" in dirs
        assert "backlog" in dirs
        assert "runbooks" in dirs

    def test_preset_adr_type(self):
        adr = SOFTWARE_KB_PRESET["types"]["adr"]
        assert "title" in adr["required"]
        assert "adr_number" in adr["optional"]
        assert "status" in adr["optional"]
        assert adr["subdirectory"] == "adrs/"


# =========================================================================
# Enum tuples
# =========================================================================


class TestEnums:
    def test_adr_statuses(self):
        assert "proposed" in ADR_STATUSES
        assert "accepted" in ADR_STATUSES
        assert "rejected" in ADR_STATUSES
        assert "deprecated" in ADR_STATUSES
        assert "superseded" in ADR_STATUSES

    def test_design_doc_statuses(self):
        assert "draft" in DESIGN_DOC_STATUSES
        assert "review" in DESIGN_DOC_STATUSES
        assert "approved" in DESIGN_DOC_STATUSES
        assert "implemented" in DESIGN_DOC_STATUSES
        assert "obsolete" in DESIGN_DOC_STATUSES

    def test_standard_categories(self):
        assert "coding" in STANDARD_CATEGORIES
        assert "security" in STANDARD_CATEGORIES
        assert len(STANDARD_CATEGORIES) == 7

    def test_validation_categories(self):
        assert VALIDATION_CATEGORIES == STANDARD_CATEGORIES

    def test_convention_categories(self):
        assert CONVENTION_CATEGORIES == STANDARD_CATEGORIES

    def test_component_kinds(self):
        assert "module" in COMPONENT_KINDS
        assert "service" in COMPONENT_KINDS
        assert "application" in COMPONENT_KINDS
        assert "utility" in COMPONENT_KINDS
        assert "endpoint" in COMPONENT_KINDS

    def test_backlog_kinds(self):
        assert "feature" in BACKLOG_KINDS
        assert "bug" in BACKLOG_KINDS
        assert "tech_debt" in BACKLOG_KINDS

    def test_backlog_statuses(self):
        assert "proposed" in BACKLOG_STATUSES
        assert "planned" in BACKLOG_STATUSES
        assert "accepted" in BACKLOG_STATUSES
        assert "in_progress" in BACKLOG_STATUSES
        assert "done" in BACKLOG_STATUSES
        assert "completed" not in BACKLOG_STATUSES
        assert "retired" in BACKLOG_STATUSES
        assert "deferred" in BACKLOG_STATUSES
        assert "wont_do" in BACKLOG_STATUSES

    def test_backlog_priorities(self):
        assert "critical" in BACKLOG_PRIORITIES
        assert "low" in BACKLOG_PRIORITIES

    def test_backlog_efforts(self):
        assert "XS" in BACKLOG_EFFORTS
        assert "XL" in BACKLOG_EFFORTS
        assert len(BACKLOG_EFFORTS) == 5

    def test_runbook_kinds(self):
        assert "howto" in RUNBOOK_KINDS
        assert "troubleshooting" in RUNBOOK_KINDS
        assert "onboarding" in RUNBOOK_KINDS


# =========================================================================
# Core integration
# =========================================================================


class TestCoreIntegration:
    """Test that the plugin integrates correctly with pyrite core when registered."""

    def test_entry_class_resolution(self):
        """Plugin entry types resolve via get_entry_class when registered."""
        import pyrite.plugins.registry as reg_module
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import get_entry_class

            assert get_entry_class("adr") is ADREntry
            assert get_entry_class("design_doc") is DesignDocEntry
            assert get_entry_class("standard") is StandardEntry
            assert get_entry_class("component") is ComponentEntry
            assert get_entry_class("backlog_item") is BacklogItemEntry
            assert get_entry_class("runbook") is RunbookEntry

            # Core types still work
            from pyrite.models.core_types import NoteEntry

            assert get_entry_class("note") is NoteEntry
        finally:
            reg_module._registry = old

    def test_entry_from_frontmatter_resolution(self):
        """Plugin entry types resolve via entry_from_frontmatter when registered."""
        import pyrite.plugins.registry as reg_module
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.models.core_types import entry_from_frontmatter

            entry = entry_from_frontmatter(
                {"type": "adr", "title": "Use Postgres", "adr_number": 1}, "Body"
            )
            assert isinstance(entry, ADREntry)
            assert entry.adr_number == 1

            entry = entry_from_frontmatter(
                {"type": "backlog_item", "title": "Fix bug", "kind": "bug"}, "Details"
            )
            assert isinstance(entry, BacklogItemEntry)
            assert entry.kind == "bug"

            entry = entry_from_frontmatter(
                {"type": "component", "title": "Plugin System", "kind": "module"}, "Docs"
            )
            assert isinstance(entry, ComponentEntry)
            assert entry.kind == "module"
        finally:
            reg_module._registry = old

    def test_relationship_types_merged(self):
        """Plugin relationship types merge into schema."""
        import pyrite.plugins.registry as reg_module
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())

        old = reg_module._registry
        reg_module._registry = registry

        try:
            from pyrite.schema import get_all_relationship_types, get_inverse_relation

            all_rels = get_all_relationship_types()
            assert "implements" in all_rels
            assert "supersedes" in all_rels
            assert "documents" in all_rels
            assert "depends_on" in all_rels
            assert "tracks" in all_rels

            assert get_inverse_relation("implements") == "implemented_by"
            assert get_inverse_relation("supersedes") == "superseded_by"
            assert get_inverse_relation("depends_on") == "depended_on_by"
            # Core types still work
            assert get_inverse_relation("owns") == "owned_by"
        finally:
            reg_module._registry = old


class TestBacklogStatusColumn:
    """Verify sw_backlog reads status from the DB column, not just metadata JSON."""

    def test_status_from_db_column_overrides_metadata(self):
        """When DB status column differs from metadata JSON, DB column wins."""
        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = PyriteDB(db_path)
            try:
                # Insert a KB row for the foreign key
                db._raw_conn.execute(
                    "INSERT INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                    ("test", str(tmpdir), "generic"),
                )
                # Insert an entry with status=done in the column but proposed in metadata
                db._raw_conn.execute(
                    "INSERT INTO entry (id, kb_name, entry_type, title, body, status, priority, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "test-item",
                        "test",
                        "backlog_item",
                        "Test Item",
                        "",
                        "done",
                        "high",
                        json.dumps({"status": "proposed", "priority": "medium", "kind": "bug"}),
                    ),
                )
                db._raw_conn.commit()

                from pyrite_software_kb.cli import _query_entries

                rows = _query_entries(db, "backlog_item")
                assert len(rows) == 1
                row = rows[0]

                # The row's DB column should be authoritative
                assert row["status"] == "done"
                assert row["priority"] == "high"
                # But _meta still has the old values from JSON
                assert row["_meta"]["status"] == "proposed"
            finally:
                db.close()


class TestBacklogTimestampProjection:
    """`sw backlog` must surface `updated_at` and `created_at` in its JSON
    projection. Regression for task-index-timestamp-drift (Tier A rank 1100):
    the DB stored both fields correctly, but `_mcp_backlog`'s output dict
    dropped `updated_at` entirely and exposed `created_at` only as an
    internal `_created` sort key that got deleted before return. Conductor
    workflows like "done today" / "opened today" need these fields.
    """

    def test_mcp_backlog_returns_updated_at_and_created_at(self):
        from pyrite_software_kb.plugin import SoftwareKBPlugin

        from pyrite.storage.database import PyriteDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = PyriteDB(db_path)
            try:
                db._raw_conn.execute(
                    "INSERT INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
                    ("test", str(tmpdir), "generic"),
                )
                db._raw_conn.execute(
                    "INSERT INTO entry (id, kb_name, entry_type, title, body, status, "
                    "priority, metadata, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "test-item-ts",
                        "test",
                        "backlog_item",
                        "Timestamps Test",
                        "",
                        "proposed",
                        "high",
                        json.dumps({"kind": "bug"}),
                        "2026-06-10T12:34:56+00:00",
                        "2026-06-11T01:23:45+00:00",
                    ),
                )
                db._raw_conn.commit()

                # Inject the test DB via PluginContext so _get_db returns it
                from unittest.mock import MagicMock

                from pyrite.plugins.context import PluginContext

                plugin = SoftwareKBPlugin()
                plugin.set_context(PluginContext(config=MagicMock(), db=db, kb_name="test"))
                result = plugin._mcp_backlog({"kb_name": "test"})
                items = result.get("items", [])
                assert len(items) == 1, f"expected 1 item, got {items}"
                item = items[0]

                # Both timestamps must surface in the projection
                assert "updated_at" in item, (
                    f"updated_at missing from sw backlog projection; got keys: {list(item.keys())}"
                )
                assert "created_at" in item, (
                    f"created_at missing from sw backlog projection; got keys: {list(item.keys())}"
                )
                assert item["updated_at"].startswith("2026-06-11"), item["updated_at"]
                assert item["created_at"].startswith("2026-06-10"), item["created_at"]
            finally:
                db.close()


# =========================================================================
# MilestoneEntry
# =========================================================================


class TestMilestoneEntry:
    def test_entry_type(self):
        entry = MilestoneEntry(id="m1", title="v1.0")
        assert entry.entry_type == "milestone"

    def test_defaults(self):
        entry = MilestoneEntry(id="m1", title="v1.0")
        assert entry.status == "open"

    def test_to_frontmatter_omits_defaults(self):
        entry = MilestoneEntry(id="m1", title="v1.0")
        fm = entry.to_frontmatter()
        assert fm["type"] == "milestone"
        assert fm["status"] == "open"  # always written (enum field)

    def test_to_frontmatter_with_status(self):
        entry = MilestoneEntry(id="m1", title="v1.0", status="closed")
        fm = entry.to_frontmatter()
        assert fm["status"] == "closed"

    def test_from_frontmatter(self):
        meta = {"id": "m1", "title": "v1.0", "type": "milestone", "status": "closed"}
        entry = MilestoneEntry.from_frontmatter(meta, "Release notes")
        assert entry.id == "m1"
        assert entry.title == "v1.0"
        assert entry.status == "closed"
        assert entry.body == "Release notes"

    def test_roundtrip(self):
        entry = MilestoneEntry(id="m1", title="v1.0", status="closed")
        fm = entry.to_frontmatter()
        restored = MilestoneEntry.from_frontmatter(fm, entry.body)
        assert restored.status == entry.status
        assert restored.title == entry.title


# =========================================================================
# Milestone validator
# =========================================================================


class TestMilestoneValidator:
    def test_valid_status(self):
        errors = validate_software_kb("milestone", {"status": "open"}, {})
        assert errors == []

    def test_valid_closed(self):
        errors = validate_software_kb("milestone", {"status": "closed"}, {})
        assert errors == []

    def test_invalid_status(self):
        errors = validate_software_kb("milestone", {"status": "invalid"}, {})
        assert any(e["field"] == "status" for e in errors)

    def test_default_status_ok(self):
        errors = validate_software_kb("milestone", {}, {})
        assert errors == []


# =========================================================================
# Board config
# =========================================================================


class TestBoardConfig:
    def test_fallback_to_defaults(self):
        from pyrite_software_kb.board import DEFAULT_BOARD_CONFIG, load_board_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_board_config(Path(tmpdir))
            assert config["lanes"] == DEFAULT_BOARD_CONFIG["lanes"]
            assert config["wip_policy"] == "warn"

    def test_load_from_file(self):
        from pyrite_software_kb.board import load_board_config

        with tempfile.TemporaryDirectory() as tmpdir:
            board_file = Path(tmpdir) / "board.yaml"
            board_file.write_text(
                "lanes:\n"
                "  - name: Todo\n"
                "    statuses: [proposed]\n"
                "  - name: Done\n"
                "    statuses: [done]\n"
                "wip_policy: block\n"
            )
            config = load_board_config(Path(tmpdir))
            assert len(config["lanes"]) == 2
            assert config["lanes"][0]["name"] == "Todo"
            assert config["wip_policy"] == "block"

    def test_lane_structure(self):
        from pyrite_software_kb.board import DEFAULT_BOARD_CONFIG

        for lane in DEFAULT_BOARD_CONFIG["lanes"]:
            assert "name" in lane
            assert "statuses" in lane
            assert isinstance(lane["statuses"], list)


# =========================================================================
# Review workflow transitions
# =========================================================================


class TestReviewWorkflow:
    def test_review_in_states(self):
        assert "review" in BACKLOG_WORKFLOW["states"]

    def test_in_progress_to_review(self):
        assert can_transition(BACKLOG_WORKFLOW, "in_progress", "review", "write")

    def test_review_to_done(self):
        assert can_transition(BACKLOG_WORKFLOW, "review", "done", "write")

    def test_review_to_completed_removed(self):
        assert not can_transition(BACKLOG_WORKFLOW, "review", "completed", "write")

    def test_review_to_in_progress(self):
        assert can_transition(BACKLOG_WORKFLOW, "review", "in_progress", "write")

    def test_review_to_in_progress_requires_reason(self):
        assert requires_reason(BACKLOG_WORKFLOW, "review", "in_progress")

    def test_in_progress_to_review_no_reason(self):
        assert not requires_reason(BACKLOG_WORKFLOW, "in_progress", "review")

    def test_review_to_done_no_reason(self):
        assert not requires_reason(BACKLOG_WORKFLOW, "review", "done")

    def test_review_in_backlog_statuses(self):
        assert "review" in BACKLOG_STATUSES


# =========================================================================
# Plugin registration updates
# =========================================================================


class TestMilestonePluginRegistration:
    def test_milestone_type_registered(self):
        plugin = SoftwareKBPlugin()
        types = plugin.get_entry_types()
        assert "milestone" in types
        assert types["milestone"] is MilestoneEntry

    def test_milestone_mcp_tools(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "sw_milestones" in tools
        assert "sw_board" in tools

    def test_milestone_statuses_enum(self):
        assert MILESTONE_STATUSES == ("open", "closed")

    def test_preset_has_milestone(self):
        assert "milestone" in SOFTWARE_KB_PRESET["types"]
        assert SOFTWARE_KB_PRESET["types"]["milestone"]["subdirectory"] == "milestones/"

    def test_preset_has_milestones_dir(self):
        assert "milestones" in SOFTWARE_KB_PRESET["directories"]

    def test_preset_has_default_board(self):
        assert "default_board" in SOFTWARE_KB_PRESET
        assert "lanes" in SOFTWARE_KB_PRESET["default_board"]
        assert "wip_policy" in SOFTWARE_KB_PRESET["default_board"]


# =========================================================================
# Kanban Flow Tools — helpers
# =========================================================================


def _make_test_db(tmpdir, entries=None, links=None):
    """Create a test DB with a KB and optional entries/links."""
    from pyrite.storage.database import PyriteDB

    db_path = Path(tmpdir) / "test.db"
    db = PyriteDB(db_path)
    db._raw_conn.execute(
        "INSERT INTO kb (name, path, kb_type) VALUES (?, ?, ?)",
        ("test", str(tmpdir), "generic"),
    )
    for e in entries or []:
        meta = e.get("meta", {})
        db._raw_conn.execute(
            "INSERT INTO entry (id, kb_name, entry_type, title, body, status, priority, assignee, metadata, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["id"],
                "test",
                e.get("entry_type", "backlog_item"),
                e.get("title", e["id"]),
                e.get("body", ""),
                e.get("status"),
                e.get("priority"),
                e.get("assignee"),
                json.dumps(meta),
                e.get("created_at", "2026-01-01T00:00:00"),
                e.get("updated_at", "2026-01-01T00:00:00"),
            ),
        )
    for lnk in links or []:
        db._raw_conn.execute(
            "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                lnk["source"],
                "test",
                lnk["target"],
                "test",
                lnk.get("relation", "tracks"),
                lnk.get("inverse", "tracked_by"),
            ),
        )
    db._raw_conn.commit()
    return db


def _make_plugin_with_db(db):
    """Create a plugin with injected DB context."""

    class _Ctx:
        def __init__(self, db):
            self.db = db

    plugin = SoftwareKBPlugin()
    plugin.set_context(_Ctx(db))
    return plugin


# =========================================================================
# TestReviewQueue
# =========================================================================


class TestReviewQueue:
    def test_review_queue_returns_review_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Review Me",
                        "status": "review",
                        "priority": "high",
                        "meta": {"kind": "bug", "status": "review", "priority": "high"},
                        "updated_at": "2026-01-01T10:00:00",
                    },
                    {
                        "id": "item-2",
                        "title": "In Progress",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                    {
                        "id": "item-3",
                        "title": "Also Review",
                        "status": "review",
                        "priority": "medium",
                        "meta": {"kind": "feature", "status": "review", "priority": "medium"},
                        "updated_at": "2026-01-02T10:00:00",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_review_queue({"kb_name": "test"})
                assert result["count"] == 2
                assert result["items"][0]["id"] == "item-1"  # older first
                assert result["items"][1]["id"] == "item-3"
            finally:
                db.close()

    def test_review_queue_excludes_non_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Accepted",
                        "status": "accepted",
                        "meta": {"kind": "feature", "status": "accepted"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_review_queue({"kb_name": "test"})
                assert result["count"] == 0
                assert result["items"] == []
            finally:
                db.close()


# =========================================================================
# TestContextForItem
# =========================================================================


class TestContextForItem:
    def test_context_categorizes_linked_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Task",
                        "status": "accepted",
                        "entry_type": "backlog_item",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                    {
                        "id": "adr-1",
                        "title": "Use REST",
                        "entry_type": "adr",
                        "meta": {"status": "accepted"},
                    },
                    {
                        "id": "comp-1",
                        "title": "API Server",
                        "entry_type": "component",
                        "meta": {"kind": "service"},
                    },
                    {
                        "id": "val-1",
                        "title": "Ruff Check",
                        "entry_type": "programmatic_validation",
                        "meta": {"category": "coding"},
                    },
                ],
                links=[
                    {
                        "source": "item-1",
                        "target": "adr-1",
                        "relation": "tracks",
                        "inverse": "tracked_by",
                    },
                    {
                        "source": "item-1",
                        "target": "comp-1",
                        "relation": "tracks",
                        "inverse": "tracked_by",
                    },
                    {
                        "source": "item-1",
                        "target": "val-1",
                        "relation": "tracks",
                        "inverse": "tracked_by",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_context_for_item({"item_id": "item-1", "kb_name": "test"})
                assert result["item"]["id"] == "item-1"
                assert result["item"]["title"] == "My Task"
                assert len(result["adrs"]) == 1
                assert result["adrs"][0]["id"] == "adr-1"
                assert len(result["components"]) == 1
                assert result["components"][0]["id"] == "comp-1"
                assert len(result["validations"]) == 1
                assert result["validations"][0]["id"] == "val-1"
            finally:
                db.close()

    def test_context_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_context_for_item({"item_id": "nonexistent", "kb_name": "test"})
                assert "error" in result
            finally:
                db.close()


# =========================================================================
# TestPullNext
# =========================================================================


class TestPullNext:
    def test_pull_next_returns_highest_priority(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "low-1",
                        "title": "Low Priority",
                        "status": "accepted",
                        "priority": "low",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "low"},
                        "created_at": "2026-01-01T00:00:00",
                    },
                    {
                        "id": "high-1",
                        "title": "High Priority",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "bug", "status": "accepted", "priority": "high"},
                        "created_at": "2026-01-02T00:00:00",
                    },
                    {
                        "id": "crit-1",
                        "title": "Critical",
                        "status": "accepted",
                        "priority": "critical",
                        "meta": {"kind": "bug", "status": "accepted", "priority": "critical"},
                        "created_at": "2026-01-03T00:00:00",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_pull_next({"kb_name": "test"})
                assert result["recommendation"] is not None
                assert result["recommendation"]["id"] == "crit-1"
                assert result["recommendation"]["priority"] == "critical"
            finally:
                db.close()

    def test_pull_next_no_accepted_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Proposed",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_pull_next({"kb_name": "test"})
                assert result["recommendation"] is None
                assert "No accepted" in result["reason"]
            finally:
                db.close()

    def test_pull_next_wip_limit_enforce(self):
        """When WIP limit is reached and policy is enforce, return no recommendation."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a board config with enforce policy and wip_limit=1
            board_file = Path(tmpdir) / "board.yaml"
            board_file.write_text(
                "lanes:\n"
                "  - name: In Progress\n"
                "    statuses: [in_progress]\n"
                "    wip_limit: 1\n"
                "wip_policy: enforce\n"
            )
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "ip-1",
                        "title": "Already Working",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                    {
                        "id": "acc-1",
                        "title": "Waiting",
                        "status": "accepted",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                from pyrite_software_kb.board import load_board_config as real_load

                with patch(
                    "pyrite_software_kb.board.load_board_config",
                    side_effect=lambda p: real_load(Path(tmpdir)),
                ):
                    result = plugin._mcp_pull_next({"kb_name": "test"})
                assert result["recommendation"] is None
                assert "WIP limit" in result["reason"]
            finally:
                db.close()


# =========================================================================
# TestClaim
# =========================================================================


class TestClaim:
    def test_claim_validates_transition(self):
        """Cannot claim from proposed (must be accepted first)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Proposed Item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_claim(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "assignee": "agent-1",
                    }
                )
                assert result["claimed"] is False
                assert "Cannot transition" in result["error"]
                assert "allowed_transitions" in result
            finally:
                db.close()

    def test_claim_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_claim(
                    {
                        "item_id": "nonexistent",
                        "kb_name": "test",
                        "assignee": "agent-1",
                    }
                )
                assert result["claimed"] is False
                assert "not found" in result["error"]
            finally:
                db.close()

    def test_claim_already_in_progress(self):
        """Cannot claim an item already in_progress."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Already Working",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                        "assignee": "someone",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_claim(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "assignee": "agent-2",
                    }
                )
                assert result["claimed"] is False
                assert "Cannot transition" in result["error"]
            finally:
                db.close()


# =========================================================================
# Plugin registration for flow tools
# =========================================================================


class TestFlowToolRegistration:
    def test_read_tier_has_review_queue(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "sw_review_queue" in tools

    def test_read_tier_has_pull_next(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "sw_pull_next" in tools

    def test_read_tier_has_context_for_item(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "sw_context_for_item" in tools

    def test_write_tier_has_claim(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("write")
        assert "sw_claim" in tools

    def test_read_tier_does_not_have_claim(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "sw_claim" not in tools


# =========================================================================
# sw new-adr file creation
# =========================================================================


class TestNewAdrGoesThroughPipeline:
    """`sw new-adr` used to write the file directly with `file_path.write_text`
    (cli.py:239 before this fix): no exists check (an existing ADR number or
    id was silently overwritten), no validators, no before/after-save hooks,
    nothing indexed until a separate `pyrite index sync`, and a missing KB
    silently fell back to writing `./adrs` under the cwd. These tests pin the
    pipeline instead: `KBService.create_entry`, the software-kb `adr` type's
    `file_pattern` keeping the `NNNN-slug.md` convention (#391).
    """

    @pytest.fixture
    def env(self, tmp_path):
        import yaml

        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.storage.database import PyriteDB
        from pyrite.storage.index import IndexManager

        kb_path = tmp_path / "sw-kb"
        kb_path.mkdir()
        (kb_path / "kb.yaml").write_text(yaml.safe_dump(SOFTWARE_KB_PRESET, sort_keys=False))
        db_path = tmp_path / "index.db"
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="sw-kb", path=kb_path, kb_type="software")],
            settings=Settings(index_path=db_path, auto_embed=False),
        )
        db = PyriteDB(db_path)
        IndexManager(db, config).index_all()
        db.close()
        return {"config": config, "kb_path": kb_path, "db_path": db_path}

    def _run(self, env, args):
        from unittest.mock import patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        runner = CliRunner()
        with patch("pyrite_software_kb.cli.load_config", return_value=env["config"]):
            return runner.invoke(sw_app, args)

    @pytest.mark.control(
        reason="repo.save()'s mkdir(parents=True) already created missing "
        "directories before this fix; pins the pipeline still does, not #391"
    )
    def test_creates_adrs_directory_if_missing(self, env):
        """sw new-adr must create the adrs/ directory if it doesn't exist yet
        -- every other test's `env` fixture never creates it either, but this
        names the behaviour explicitly so a future regression fails here
        first, on its own, instead of only as a side effect elsewhere."""
        assert not (env["kb_path"] / "adrs").exists()
        result = self._run(env, ["new-adr", "Use Kafka", "--kb", "sw-kb"])
        assert result.exit_code == 0, result.output
        assert (env["kb_path"] / "adrs" / "0001-use-kafka.md").exists()

    @pytest.mark.control(
        reason="the pre-pipeline direct write already produced this filename/content; "
        "pins the pipeline keeps the convention, not the write-pipeline bug itself"
    )
    def test_creates_adr_file_with_the_conventional_filename(self, env):
        result = self._run(env, ["new-adr", "Use PostgreSQL", "--kb", "sw-kb"])
        assert result.exit_code == 0, result.output

        expected_file = env["kb_path"] / "adrs" / "0001-use-postgresql.md"
        assert expected_file.exists(), f"Expected {expected_file} to be created"

        content = expected_file.read_text()
        assert "type: adr" in content
        assert "adr_number: 1" in content
        assert "id: adr-0001" in content
        assert "status: proposed" in content
        assert "# ADR-0001: Use PostgreSQL" in content, (
            "26 of 37 existing ADRs in this repo have this heading (#391 cold read item 6)"
        )
        assert "## Context" in content
        assert "## Decision" in content
        assert "## Consequences" in content

    @pytest.mark.control(
        reason="#17's original fix already stripped this punctuation via the "
        "shared slug; pins file_pattern's {title} keeps using it, not the #391 fix"
    )
    def test_title_punctuation_never_reaches_the_filename(self, env):
        """`/` and `:` in a title used to land in the ADR filename (#17):
        `adrs/0001-use-a/b-testing.md` crashed with FileNotFoundError, and a
        colon is illegal on Windows. `{title}` is the shared, slugified
        placeholder, so this still holds through `file_pattern`."""
        result = self._run(env, ["new-adr", "Use A/B testing: now, or later?", "--kb", "sw-kb"])
        assert result.exit_code == 0, result.output
        created = sorted(p.name for p in (env["kb_path"] / "adrs").glob("*.md"))
        assert created == ["0001-use-a-b-testing-now-or-later.md"], created
        assert not (env["kb_path"] / "adrs" / "0001-use-a").exists()

    def test_new_adr_is_indexed_immediately(self, env):
        result = self._run(env, ["new-adr", "Use Redis", "--kb", "sw-kb"])
        assert result.exit_code == 0, result.output

        from pyrite.storage.database import PyriteDB

        db = PyriteDB(env["db_path"])
        try:
            row = db.get_entry("adr-0001", "sw-kb")
            assert row is not None, "a new ADR should be indexed without `index sync`"
        finally:
            db.close()

    def test_new_adr_runs_before_and_after_save_hooks(self, env):
        from pyrite.plugins.registry import get_registry

        before_calls = []
        after_calls = []

        def before_save(entry, ctx):
            before_calls.append(entry.id)

        def after_save(entry, ctx):
            after_calls.append(entry.id)

        class ProbePlugin:
            name = "probe_hook_plugin_adr"

            def get_hooks(self):
                return {"before_save": [before_save], "after_save": [after_save]}

        reg = get_registry()
        reg.register(ProbePlugin())
        try:
            result = self._run(env, ["new-adr", "Hooked ADR", "--kb", "sw-kb"])
            assert result.exit_code == 0, result.output
            assert before_calls == ["adr-0001"], before_calls
            assert after_calls == ["adr-0001"], after_calls
        finally:
            del reg._plugins["probe_hook_plugin_adr"]

    def test_auto_increments_number(self, env):
        first = self._run(env, ["new-adr", "First ADR", "--kb", "sw-kb"])
        assert first.exit_code == 0, first.output
        second = self._run(env, ["new-adr", "Second ADR", "--kb", "sw-kb"])
        assert second.exit_code == 0, second.output

        expected_file = env["kb_path"] / "adrs" / "0002-second-adr.md"
        assert expected_file.exists(), f"Expected {expected_file} (number 2 after 1)"
        assert "adr_number: 2" in expected_file.read_text()
        assert "id: adr-0002" in expected_file.read_text()

    @pytest.mark.control(
        reason="_resolve_adr_kb's single-KB fallback predates #391 and was "
        "already correct; pins it still works, not the write-pipeline fix"
    )
    def test_resolves_single_kb_without_kb_flag(self, env):
        """Without --kb, new-adr must write into the (single) configured KB's
        adrs/ dir. Regression for new-adr-writes-to-cwd-without-kb-flag."""
        result = self._run(env, ["new-adr", "Use gRPC"])
        assert result.exit_code == 0, result.output
        expected = env["kb_path"] / "adrs" / "0001-use-grpc.md"
        assert expected.exists(), f"ADR should land in the KB: {expected}"

    def test_no_kb_resolved_refuses_and_writes_nothing(self, tmp_path, monkeypatch):
        """No KB resolved must refuse with a clear, KB_NOT_FOUND-style error --
        NOT fall back to writing ./adrs under the cwd (the old behaviour this
        pipeline replaces)."""
        from unittest.mock import patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        from pyrite.config import PyriteConfig, Settings

        run_cwd = tmp_path / "elsewhere"
        run_cwd.mkdir()
        monkeypatch.chdir(run_cwd)

        empty_config = PyriteConfig(
            knowledge_bases=[],
            settings=Settings(index_path=tmp_path / "test.db"),
        )
        runner = CliRunner()
        with patch("pyrite_software_kb.cli.load_config", return_value=empty_config):
            result = runner.invoke(sw_app, ["new-adr", "Use gRPC"])

        assert result.exit_code != 0, result.output
        assert "KB_NOT_FOUND" in result.output or "No KB" in result.output, result.output
        assert not (run_cwd / "adrs").exists(), (
            "new-adr must not create ./adrs in the current directory"
        )

    def test_existing_adr_number_is_refused_with_entry_exists(self, env):
        """An existing ADR's id (adr-0001) is refused; the file is untouched."""
        first = self._run(env, ["new-adr", "Original Title", "--kb", "sw-kb"])
        assert first.exit_code == 0, first.output
        expected_file = env["kb_path"] / "adrs" / "0001-original-title.md"
        original = expected_file.read_bytes()

        # A second ADR that resolves to the SAME id (adr-0001) is refused.
        # The numbering query only sees indexed ADRs, so this simulates a
        # race/duplicate rather than the auto-increment's normal path.
        from pyrite.storage.database import PyriteDB

        db = PyriteDB(env["db_path"])
        try:
            db._raw_conn.execute(
                "DELETE FROM entry WHERE id = ? AND kb_name = ?", ("adr-0001", "sw-kb")
            )
            db._raw_conn.commit()
        finally:
            db.close()

        result = self._run(env, ["new-adr", "Original Title", "--kb", "sw-kb"])
        assert result.exit_code != 0, result.output
        assert "ENTRY_EXISTS" in result.output, result.output
        assert expected_file.read_bytes() == original

    def test_create_failure_prints_str_e_not_the_masked_public_message(self, env):
        """#506 item 3: this is the operator's own terminal, so there is no
        transport boundary to protect -- masking a StorageError/PluginError/
        ConfigError's public_message here (#501's fixed sentence, meant for
        REST/MCP) hides real detail for no security gain. Local CLI output
        should always show str(e)."""
        from unittest.mock import patch

        from pyrite.exceptions import StorageError
        from pyrite.services.kb_service import KBService

        real_message = "Failed to materialize query result: real driver detail (dsn=x)"
        with patch.object(KBService, "create_entry", side_effect=StorageError(real_message)):
            result = self._run(env, ["new-adr", "Doomed ADR", "--kb", "sw-kb"])

        assert result.exit_code != 0, result.output
        # Rich may wrap the line at the terminal width the runner reports,
        # so compare with whitespace collapsed rather than a raw substring.
        normalized = " ".join(result.output.split())
        assert real_message in normalized, result.output
        assert "check the server log" not in normalized, result.output


# =========================================================================
# TestBacklogDependencies
# =========================================================================


class TestBacklogDependencies:
    """Tests for blocks/blocked_by dependency support in flow tools."""

    def test_relationship_types_include_blocks(self):
        plugin = SoftwareKBPlugin()
        rel_types = plugin.get_relationship_types()
        assert "blocks" in rel_types
        assert "blocked_by" in rel_types
        assert rel_types["blocks"]["inverse"] == "blocked_by"
        assert rel_types["blocked_by"]["inverse"] == "blocks"

    def test_pull_next_skips_blocked_items(self):
        """B is blocked_by A (unresolved). pull_next returns A."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-a",
                        "title": "Task A",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                    {
                        "id": "item-b",
                        "title": "Task B",
                        "status": "accepted",
                        "priority": "critical",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "critical"},
                    },
                ],
                links=[
                    {
                        "source": "item-b",
                        "target": "item-a",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_pull_next({"kb_name": "test"})
                # B has higher priority (critical) but is blocked, so A is recommended
                assert result["recommendation"]["id"] == "item-a"
            finally:
                db.close()

    def test_pull_next_resolved_deps_eligible(self):
        """B blocked_by A, but A is done. B is eligible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-a",
                        "title": "Task A",
                        "status": "done",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "done", "priority": "high"},
                    },
                    {
                        "id": "item-b",
                        "title": "Task B",
                        "status": "accepted",
                        "priority": "critical",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "critical"},
                    },
                ],
                links=[
                    {
                        "source": "item-b",
                        "target": "item-a",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_pull_next({"kb_name": "test"})
                # A is done so B is unblocked, and B has higher priority
                assert result["recommendation"]["id"] == "item-b"
            finally:
                db.close()

    def test_pull_next_blocked_items_field(self):
        """Blocked items appear in blocked_items response field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-a",
                        "title": "Task A",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                    {
                        "id": "item-b",
                        "title": "Task B",
                        "status": "accepted",
                        "priority": "critical",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "critical"},
                    },
                ],
                links=[
                    {
                        "source": "item-b",
                        "target": "item-a",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_pull_next({"kb_name": "test"})
                assert "blocked_items" in result
                blocked = result["blocked_items"]
                assert len(blocked) == 1
                assert blocked[0]["id"] == "item-b"
                assert "item-a" in blocked[0]["blocked_by"]
            finally:
                db.close()

    def test_context_for_item_shows_dependencies(self):
        """context_for_item includes dependencies bucket with status info."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-a",
                        "title": "Task A",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                        "entry_type": "backlog_item",
                    },
                    {
                        "id": "item-b",
                        "title": "Task B",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                        "entry_type": "backlog_item",
                    },
                    {
                        "id": "item-c",
                        "title": "Task C",
                        "status": "done",
                        "priority": "medium",
                        "meta": {"kind": "bug", "status": "done", "priority": "medium"},
                        "entry_type": "backlog_item",
                    },
                ],
                links=[
                    # B is blocked_by A (A blocks B)
                    {
                        "source": "item-b",
                        "target": "item-a",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                    # B is blocked_by C (C blocks B, but C is done)
                    {
                        "source": "item-b",
                        "target": "item-c",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_context_for_item({"item_id": "item-b", "kb_name": "test"})
                assert "dependencies" in result
                deps = result["dependencies"]
                assert len(deps["blocked_by"]) == 2
                assert deps["is_blocked"] is True
                # Check resolved flags
                by_id = {d["id"]: d for d in deps["blocked_by"]}
                assert by_id["item-a"]["resolved"] is False
                assert by_id["item-c"]["resolved"] is True
            finally:
                db.close()

    def test_claim_blocked_item_fails(self):
        """Claiming an item with unresolved deps returns error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-a",
                        "title": "Task A",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                    {
                        "id": "item-b",
                        "title": "Task B",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                ],
                links=[
                    {
                        "source": "item-b",
                        "target": "item-a",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_claim(
                    {"item_id": "item-b", "kb_name": "test", "assignee": "agent"}
                )
                assert result["claimed"] is False
                assert "unresolved dependencies" in result["error"]
                assert len(result["unresolved_dependencies"]) == 1
                assert result["unresolved_dependencies"][0]["id"] == "item-a"
            finally:
                db.close()

    def test_claim_resolved_deps_succeeds(self):
        """Claiming an item whose blockers are all done succeeds."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-a",
                        "title": "Task A",
                        "status": "done",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "done", "priority": "high"},
                    },
                    {
                        "id": "item-b",
                        "title": "Task B",
                        "status": "accepted",
                        "priority": "high",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "high"},
                    },
                ],
                links=[
                    {
                        "source": "item-b",
                        "target": "item-a",
                        "relation": "blocked_by",
                        "inverse": "blocks",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                # claim_entry needs KBService — mock it
                from unittest.mock import MagicMock, patch

                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-b",
                    "status": "in_progress",
                    "assignee": "agent",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_claim(
                        {"item_id": "item-b", "kb_name": "test", "assignee": "agent"}
                    )
                assert result["claimed"] is True
            finally:
                db.close()


# =========================================================================
# TestReview — sw_review tool
# =========================================================================


class TestReview:
    def test_review_tool_registered(self):
        """sw_review is in write tier, not in read tier."""
        plugin = SoftwareKBPlugin()
        write_tools = plugin.get_mcp_tools("write")
        read_tools = plugin.get_mcp_tools("read")
        assert "sw_review" in write_tools
        assert "sw_review" not in read_tools

    def test_review_approved_transitions_to_done(self):
        """Item in review → approved → status becomes done."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Review Me",
                        "status": "review",
                        "meta": {"kind": "feature", "status": "review"},
                        "assignee": "dev-1",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-1",
                    "status": "done",
                    "assignee": "dev-1",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_review(
                        {
                            "item_id": "item-1",
                            "kb_name": "test",
                            "outcome": "approved",
                            "reviewer": "reviewer-1",
                        }
                    )
                assert result["reviewed"] is True
                assert result["outcome"] == "approved"
                assert result["new_status"] == "done"
                assert "review_id" in result
                # CAS was called with review → done
                mock_svc.claim_entry.assert_called_once_with(
                    "item-1",
                    "test",
                    "dev-1",
                    from_status="review",
                    to_status="done",
                )
            finally:
                db.close()

    def test_review_changes_requested_transitions_to_in_progress(self):
        """Item in review → changes_requested → status becomes in_progress."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Review Me",
                        "status": "review",
                        "meta": {"kind": "bug", "status": "review"},
                        "assignee": "dev-1",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-1",
                    "status": "in_progress",
                    "assignee": "dev-1",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_review(
                        {
                            "item_id": "item-1",
                            "kb_name": "test",
                            "outcome": "changes_requested",
                            "reviewer": "reviewer-1",
                            "feedback": "Missing error handling in edge case",
                        }
                    )
                assert result["reviewed"] is True
                assert result["outcome"] == "changes_requested"
                assert result["new_status"] == "in_progress"
                mock_svc.claim_entry.assert_called_once_with(
                    "item-1",
                    "test",
                    "dev-1",
                    from_status="review",
                    to_status="in_progress",
                )
            finally:
                db.close()

    def test_review_changes_requested_requires_feedback(self):
        """changes_requested without feedback → error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Review Me",
                        "status": "review",
                        "meta": {"kind": "feature", "status": "review"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_review(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "outcome": "changes_requested",
                        "reviewer": "reviewer-1",
                    }
                )
                assert result["reviewed"] is False
                assert "Feedback is required" in result["error"]
            finally:
                db.close()

    def test_review_not_in_review_status_fails(self):
        """Item in accepted → review → error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Not In Review",
                        "status": "accepted",
                        "meta": {"kind": "feature", "status": "accepted"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_review(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "outcome": "approved",
                        "reviewer": "reviewer-1",
                    }
                )
                assert result["reviewed"] is False
                assert "not 'review'" in result["error"]
            finally:
                db.close()

    def test_review_not_found_fails(self):
        """Nonexistent item → error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_review(
                    {
                        "item_id": "nonexistent",
                        "kb_name": "test",
                        "outcome": "approved",
                        "reviewer": "reviewer-1",
                    }
                )
                assert result["reviewed"] is False
                assert "not found" in result["error"]
            finally:
                db.close()

    def test_review_creates_review_record(self):
        """After approved, review record exists in DB."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Review Me",
                        "status": "review",
                        "meta": {"kind": "feature", "status": "review"},
                        "assignee": "dev-1",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-1",
                    "status": "done",
                    "assignee": "dev-1",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    plugin._mcp_review(
                        {
                            "item_id": "item-1",
                            "kb_name": "test",
                            "outcome": "approved",
                            "reviewer": "reviewer-1",
                            "feedback": "LGTM",
                        }
                    )
                # Verify review record in DB
                reviews = db.get_reviews("item-1", "test")
                assert len(reviews) == 1
                assert reviews[0]["reviewer"] == "reviewer-1"
                assert reviews[0]["result"] == "approved"
                assert reviews[0]["details"] == "LGTM"
            finally:
                db.close()

    def test_context_for_item_shows_reviews(self):
        """Item with prior review → reviews in context response."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Has Reviews",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                ],
            )
            try:
                # Insert a review record directly
                db.create_review(
                    entry_id="item-1",
                    kb_name="test",
                    content_hash="",
                    reviewer="reviewer-1",
                    reviewer_type="agent",
                    result="changes_requested",
                    details="Fix the tests",
                )
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_context_for_item({"item_id": "item-1", "kb_name": "test"})
                assert "reviews" in result
                assert len(result["reviews"]) == 1
                assert result["reviews"][0]["reviewer"] == "reviewer-1"
                assert result["reviews"][0]["result"] == "changes_requested"
                assert result["reviews"][0]["details"] == "Fix the tests"
            finally:
                db.close()


# =========================================================================
# TestSubmit — sw_submit tool
# =========================================================================


class TestSubmit:
    def test_submit_tool_registered(self):
        """sw_submit is in write tier, not in read tier."""
        plugin = SoftwareKBPlugin()
        write_tools = plugin.get_mcp_tools("write")
        read_tools = plugin.get_mcp_tools("read")
        assert "sw_submit" in write_tools
        assert "sw_submit" not in read_tools

    def test_submit_transitions_to_review(self):
        """Item in in_progress → submit → status becomes review."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Working On It",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                        "assignee": "dev-1",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-1",
                    "status": "review",
                    "assignee": "dev-1",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_submit({"item_id": "item-1", "kb_name": "test"})
                assert result["submitted"] is True
                assert result["new_status"] == "review"
                mock_svc.claim_entry.assert_called_once_with(
                    "item-1",
                    "test",
                    "dev-1",
                    from_status="in_progress",
                    to_status="review",
                )
            finally:
                db.close()

    def test_submit_not_in_progress_fails(self):
        """Item in accepted → submit → error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Not Started",
                        "status": "accepted",
                        "meta": {"kind": "feature", "status": "accepted"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_submit({"item_id": "item-1", "kb_name": "test"})
                assert result["submitted"] is False
                assert "Cannot transition" in result["error"]
                assert result["current_status"] == "accepted"
            finally:
                db.close()

    def test_submit_not_found_fails(self):
        """Nonexistent item → error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_submit({"item_id": "nonexistent", "kb_name": "test"})
                assert result["submitted"] is False
                assert "not found" in result["error"]
            finally:
                db.close()


# =========================================================================
# WorkLogEntry
# =========================================================================


class TestWorkLogEntry:
    def test_work_log_defaults(self):
        entry = WorkLogEntry(id="test", title="Test")
        assert entry.entry_type == "work_log"
        assert entry.item_id == ""
        assert entry.decisions == ""
        assert entry.rejected == ""
        assert entry.open_questions == ""
        assert entry.date == ""

    def test_work_log_to_frontmatter(self):
        entry = WorkLogEntry(
            id="item-1-log-20260308",
            title="Session 1",
            item_id="item-1",
            date="2026-03-08",
            decisions="Used REST over GraphQL",
            rejected="Tried gRPC, too complex",
            open_questions="Auth strategy TBD",
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "work_log"
        assert fm["item_id"] == "item-1"
        assert fm["date"] == "2026-03-08"
        assert fm["decisions"] == "Used REST over GraphQL"
        assert fm["rejected"] == "Tried gRPC, too complex"
        assert fm["open_questions"] == "Auth strategy TBD"

    def test_work_log_to_frontmatter_omits_defaults(self):
        entry = WorkLogEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "item_id" not in fm
        assert "date" not in fm
        assert "decisions" not in fm
        assert "rejected" not in fm
        assert "open_questions" not in fm

    def test_work_log_from_frontmatter(self):
        meta = {
            "id": "item-1-log-20260308",
            "title": "Session 1",
            "type": "work_log",
            "item_id": "item-1",
            "date": "2026-03-08",
            "decisions": "Used REST",
            "rejected": "gRPC",
            "open_questions": "Auth?",
        }
        entry = WorkLogEntry.from_frontmatter(meta, "Session summary")
        assert entry.item_id == "item-1"
        assert entry.date == "2026-03-08"
        assert entry.decisions == "Used REST"
        assert entry.rejected == "gRPC"
        assert entry.open_questions == "Auth?"
        assert entry.body == "Session summary"

    def test_work_log_roundtrip(self):
        entry = WorkLogEntry(
            id="log-1",
            title="Session",
            item_id="item-1",
            date="2026-03-08",
            decisions="d",
            rejected="r",
            open_questions="q",
        )
        fm = entry.to_frontmatter()
        restored = WorkLogEntry.from_frontmatter(fm, entry.body)
        assert restored.item_id == entry.item_id
        assert restored.date == entry.date
        assert restored.decisions == entry.decisions
        assert restored.rejected == entry.rejected
        assert restored.open_questions == entry.open_questions


# =========================================================================
# WorkLog validator
# =========================================================================


class TestWorkLogValidator:
    def test_work_log_validator_requires_item_id(self):
        errors = validate_software_kb("work_log", {}, {})
        assert any(e["field"] == "item_id" and e["rule"] == "required" for e in errors)

    def test_work_log_validator_valid(self):
        errors = validate_software_kb("work_log", {"item_id": "item-1"}, {})
        assert errors == []


# =========================================================================
# sw_log tool
# =========================================================================


class TestSwLog:
    def test_sw_log_tool_registered(self):
        """sw_log is in write tier, not in read tier."""
        plugin = SoftwareKBPlugin()
        write_tools = plugin.get_mcp_tools("write")
        read_tools = plugin.get_mcp_tools("read")
        assert "sw_log" in write_tools
        assert "sw_log" not in read_tools

    def test_sw_log_returns_frontmatter(self):
        """Handler returns correct structure with link."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Task",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_log(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "summary": "Implemented REST endpoints",
                        "decisions": "Used FastAPI",
                        "rejected": "Flask too minimal",
                        "open_questions": "Rate limiting approach",
                    }
                )
                assert result["created"] is True
                assert result["item_id"] == "item-1"
                assert result["id"].startswith("item-1-log-")
                assert result["body"] == "Implemented REST endpoints"
                assert result["frontmatter"]["type"] == "work_log"
                assert result["frontmatter"]["item_id"] == "item-1"
                assert result["frontmatter"]["decisions"] == "Used FastAPI"
                assert result["frontmatter"]["rejected"] == "Flask too minimal"
                assert result["frontmatter"]["open_questions"] == "Rate limiting approach"
                assert result["link"]["target"] == "item-1"
                assert result["link"]["relation"] == "session_for"
                assert result["link"]["inverse"] == "has_session"
                assert result["filename"].startswith("work-logs/")
            finally:
                db.close()

    def test_sw_log_item_not_found(self):
        """Nonexistent item → error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_log(
                    {
                        "item_id": "nonexistent",
                        "kb_name": "test",
                        "summary": "Session notes",
                    }
                )
                assert result["created"] is False
                assert "not found" in result["error"]
            finally:
                db.close()

    def test_sw_log_optional_fields_omitted(self):
        """Optional fields not in frontmatter when empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Task",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_log(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "summary": "Quick session",
                    }
                )
                assert result["created"] is True
                assert "decisions" not in result["frontmatter"]
                assert "rejected" not in result["frontmatter"]
                assert "open_questions" not in result["frontmatter"]
            finally:
                db.close()


# =========================================================================
# Context surfaces work logs
# =========================================================================


class TestContextSurfacesWorkLogs:
    def test_context_surfaces_work_logs(self):
        """work_log entries appear in context work_logs bucket."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Task",
                        "status": "in_progress",
                        "entry_type": "backlog_item",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                    {
                        "id": "item-1-log-1",
                        "title": "Session 1",
                        "entry_type": "work_log",
                        "meta": {"item_id": "item-1", "date": "2026-03-08"},
                    },
                ],
                links=[
                    {
                        "source": "item-1-log-1",
                        "target": "item-1",
                        "relation": "session_for",
                        "inverse": "has_session",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_context_for_item({"item_id": "item-1", "kb_name": "test"})
                assert "work_logs" in result
                assert len(result["work_logs"]) == 1
                assert result["work_logs"][0]["id"] == "item-1-log-1"
                assert result["work_logs"][0]["entry_type"] == "work_log"
            finally:
                db.close()


# =========================================================================
# Column-first status fallback (bug fix regression tests)
# =========================================================================


class TestColumnFirstStatusFallback:
    """Verify MCP handlers read status/priority/assignee from DB columns first,
    falling back to metadata JSON only when columns are NULL.

    Regression tests for the bug where sw_backlog, sw_board, and sw_milestones
    read status only from metadata JSON, ignoring the authoritative DB column.
    """

    def test_backlog_uses_db_column_over_metadata(self):
        """sw_backlog returns status from DB column even when metadata disagrees."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Column wins",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "priority": "high",
                        "assignee": "alice",
                        "meta": {
                            "kind": "feature",
                            "status": "proposed",
                            "priority": "low",
                            "assignee": "bob",
                        },
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test"})
                assert result["count"] == 1
                item = result["items"][0]
                assert item["status"] == "in_progress"
                assert item["priority"] == "high"
                assert item["assignee"] == "alice"
            finally:
                db.close()

    def test_backlog_falls_back_to_metadata_when_column_null(self):
        """sw_backlog falls back to metadata JSON when DB column is NULL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Metadata fallback",
                        "entry_type": "backlog_item",
                        "status": None,
                        "priority": None,
                        "assignee": None,
                        "meta": {
                            "kind": "bug",
                            "status": "accepted",
                            "priority": "critical",
                            "assignee": "carol",
                        },
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test"})
                assert result["count"] == 1
                item = result["items"][0]
                assert item["status"] == "accepted"
                assert item["priority"] == "critical"
                assert item["assignee"] == "carol"
            finally:
                db.close()

    def test_board_uses_db_column_for_lane_routing(self):
        """sw_board routes items to lanes based on DB column status, not metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "In progress item",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "priority": "high",
                        "meta": {
                            "kind": "feature",
                            "status": "proposed",
                        },
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_board({"kb_name": "test"})
                found = False
                for lane in result["lanes"]:
                    for item in lane["items"]:
                        if item["id"] == "item-1":
                            assert item["status"] == "in_progress"
                            found = True
                assert found, "Item should appear on the board"
            finally:
                db.close()

    def test_board_lane_items_bounded_by_default(self):
        """A lane with many items does not return every one unbounded (#233).

        `sw_board` grows with the project the same way `sw_backlog` does —
        it embeds a full item list per lane. Counts stay exact; only the
        embedded item list per lane is bounded.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                {
                    "id": f"item-{i:03d}",
                    "title": f"Item {i}",
                    "entry_type": "backlog_item",
                    "status": "proposed",
                    "priority": "medium",
                    "meta": {"kind": "feature", "status": "proposed"},
                }
                for i in range(75)
            ]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_board({"kb_name": "test"})
                proposed_lane = next(lane for lane in result["lanes"] if lane["count"] == 75)
                assert len(proposed_lane["items"]) < 75
            finally:
                db.close()

    def test_backlog_filter_uses_db_column(self):
        """sw_backlog status filter matches against DB column, not metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "Actually in progress",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "priority": "medium",
                        "meta": {
                            "kind": "feature",
                            "status": "proposed",
                        },
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test", "status": "in_progress"})
                assert result["count"] == 1
                result = plugin._mcp_backlog({"kb_name": "test", "status": "proposed"})
                assert result["count"] == 0
            finally:
                db.close()

    def test_adrs_use_db_column_status(self):
        """sw_adrs reads status from DB column first."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "adr-1",
                        "title": "Use Typer",
                        "entry_type": "adr",
                        "status": "accepted",
                        "meta": {
                            "adr_number": 1,
                            "status": "proposed",
                        },
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_adrs({"kb_name": "test"})
                assert result["count"] == 1
                assert result["adrs"][0]["status"] == "accepted"
            finally:
                db.close()


# =========================================================================
# Transition tool
# =========================================================================


class TestTransition:
    def test_transition_tool_registered(self):
        """sw_transition should be in write tier, not read tier."""
        registry = PluginRegistry()
        registry.register(SoftwareKBPlugin())
        write_tools = registry.get_all_mcp_tools("write")
        read_tools = registry.get_all_mcp_tools("read")
        assert "sw_transition" in write_tools
        assert "sw_transition" not in read_tools

    def test_transition_proposed_to_accepted(self):
        """Happy path: proposed -> accepted."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Feature",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-1",
                    "status": "accepted",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_transition(
                        {
                            "item_id": "item-1",
                            "kb_name": "test",
                            "to_status": "accepted",
                        }
                    )
                assert result["transitioned"] is True
                assert result["old_status"] == "proposed"
                assert result["new_status"] == "accepted"
                mock_svc.claim_entry.assert_called_once_with(
                    "item-1",
                    "test",
                    "",
                    from_status="proposed",
                    to_status="accepted",
                )
            finally:
                db.close()

    def test_transition_requires_reason(self):
        """proposed -> wont_do without reason should error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Feature",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_transition(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "to_status": "wont_do",
                    }
                )
                assert result["transitioned"] is False
                assert "reason" in result["error"].lower() or "Reason" in result["error"]
            finally:
                db.close()

    def test_transition_with_reason(self):
        """proposed -> wont_do with reason should succeed."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Feature",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                mock_svc.claim_entry.return_value = {
                    "claimed": True,
                    "id": "item-1",
                    "status": "wont_do",
                }
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_transition(
                        {
                            "item_id": "item-1",
                            "kb_name": "test",
                            "to_status": "wont_do",
                            "reason": "Not needed anymore",
                        }
                    )
                assert result["transitioned"] is True
                assert result["new_status"] == "wont_do"
                assert result["reason"] == "Not needed anymore"
            finally:
                db.close()

    def test_transition_invalid(self):
        """proposed -> in_progress should fail (must go through accepted)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Feature",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_transition(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "to_status": "in_progress",
                    }
                )
                assert result["transitioned"] is False
                assert "Cannot transition" in result["error"]
            finally:
                db.close()

    def test_transition_not_found(self):
        """Nonexistent item should return error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_transition(
                    {
                        "item_id": "nonexistent",
                        "kb_name": "test",
                        "to_status": "accepted",
                    }
                )
                assert result["transitioned"] is False
                assert "not found" in result["error"]
            finally:
                db.close()

    def test_transition_shows_allowed(self):
        """Error response should include allowed_transitions list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-1",
                        "title": "My Feature",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_transition(
                    {
                        "item_id": "item-1",
                        "kb_name": "test",
                        "to_status": "in_progress",
                    }
                )
                assert result["transitioned"] is False
                assert "allowed_transitions" in result
                assert "accepted" in result["allowed_transitions"]
            finally:
                db.close()


# =========================================================================
# Workflow normalization
# =========================================================================


class TestWorkflowNormalization:
    def test_completed_removed_from_workflow(self):
        """completed should no longer be a valid state."""
        assert "completed" not in BACKLOG_WORKFLOW["states"]
        # No transitions should reference completed
        for t in BACKLOG_WORKFLOW["transitions"]:
            assert t["from"] != "completed"
            assert t["to"] != "completed"

    def test_done_is_terminal(self):
        """done -> retired and done -> accepted (reopen) should work."""
        assert can_transition(BACKLOG_WORKFLOW, "done", "retired", "write")
        assert can_transition(BACKLOG_WORKFLOW, "done", "accepted", "write")
        assert requires_reason(BACKLOG_WORKFLOW, "done", "accepted")

    def test_completed_not_in_backlog_statuses(self):
        """BACKLOG_STATUSES enum should not include completed."""
        from pyrite_software_kb.entry_types import BACKLOG_STATUSES

        assert "completed" not in BACKLOG_STATUSES


# =========================================================================
# CLI: context-for-item
# =========================================================================


class TestContextForItemCLI:
    def test_command_exists(self):
        """context-for-item should be a registered subcommand."""
        from pyrite_software_kb.cli import sw_app

        command_names = [cmd.name for cmd in sw_app.registered_commands]
        assert "context-for-item" in command_names

    def test_returns_item_details_json(self):
        """context-for-item should return item details in JSON format."""
        from unittest.mock import patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-ctx-1",
                        "title": "Context Test Item",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "priority": "high",
                        "body": "Some body text",
                        "meta": {
                            "kind": "feature",
                            "status": "in_progress",
                            "priority": "high",
                        },
                    },
                ],
            )
            try:
                with patch(
                    "pyrite_software_kb.plugin.SoftwareKBPlugin._get_db",
                    return_value=(db, False),
                ):
                    result = runner.invoke(
                        sw_app,
                        ["context-for-item", "item-ctx-1", "--kb", "test"],
                    )

                assert result.exit_code == 0, f"Failed: {result.output}"
                data = json.loads(result.output)
                assert data["item"]["id"] == "item-ctx-1"
                assert data["item"]["title"] == "Context Test Item"
                assert data["item"]["status"] == "in_progress"
                assert data["item"]["priority"] == "high"
            finally:
                db.close()

    def test_shows_linked_adrs(self):
        """context-for-item should include linked ADRs."""
        from unittest.mock import patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-ctx-2",
                        "title": "Item With ADR",
                        "entry_type": "backlog_item",
                        "status": "accepted",
                        "meta": {"kind": "feature", "status": "accepted"},
                    },
                    {
                        "id": "adr-linked",
                        "title": "Use REST API",
                        "entry_type": "adr",
                        "meta": {"adr_number": 1, "status": "accepted"},
                    },
                ],
                links=[
                    {
                        "source": "item-ctx-2",
                        "target": "adr-linked",
                        "relation": "implements",
                        "inverse": "implemented_by",
                    },
                ],
            )
            try:
                with patch(
                    "pyrite_software_kb.plugin.SoftwareKBPlugin._get_db",
                    return_value=(db, False),
                ):
                    result = runner.invoke(
                        sw_app,
                        ["context-for-item", "item-ctx-2", "--kb", "test"],
                    )

                assert result.exit_code == 0, f"Failed: {result.output}"
                data = json.loads(result.output)
                assert len(data["adrs"]) >= 1
                adr_ids = [a["id"] for a in data["adrs"]]
                assert "adr-linked" in adr_ids
            finally:
                db.close()

    def test_rich_output_format(self):
        """context-for-item --format rich should produce human-readable output."""
        from unittest.mock import patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "item-ctx-3",
                        "title": "Rich Output Item",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "priority": "medium",
                        "meta": {
                            "kind": "bug",
                            "status": "in_progress",
                            "priority": "medium",
                        },
                    },
                ],
            )
            try:
                with patch(
                    "pyrite_software_kb.plugin.SoftwareKBPlugin._get_db",
                    return_value=(db, False),
                ):
                    result = runner.invoke(
                        sw_app,
                        [
                            "context-for-item",
                            "item-ctx-3",
                            "--kb",
                            "test",
                            "--format",
                            "rich",
                        ],
                    )

                assert result.exit_code == 0, f"Failed: {result.output}"
                assert "Rich Output Item" in result.output
                assert "item-ctx-3" in result.output
            finally:
                db.close()

    def test_error_for_missing_item(self):
        """context-for-item should error for a non-existent item."""
        from unittest.mock import patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        runner = CliRunner()

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                with patch(
                    "pyrite_software_kb.plugin.SoftwareKBPlugin._get_db",
                    return_value=(db, False),
                ):
                    result = runner.invoke(
                        sw_app,
                        ["context-for-item", "nonexistent-item", "--kb", "test"],
                    )

                assert result.exit_code != 0
                assert "Error" in result.output or "not found" in result.output
            finally:
                db.close()


# =========================================================================
# TestBacklogItemRank
# =========================================================================


class TestBacklogItemRank:
    def test_default_rank_is_zero(self):
        from pyrite_software_kb.entry_types import BacklogItemEntry

        entry = BacklogItemEntry(id="test", title="Test")
        assert entry.rank == 0

    def test_to_frontmatter_includes_zero_rank(self):
        from pyrite_software_kb.entry_types import BacklogItemEntry

        entry = BacklogItemEntry(id="test", title="Test", rank=0)
        fm = entry.to_frontmatter()
        assert fm["rank"] == 0  # always written (0 is valid)

    def test_to_frontmatter_includes_nonzero_rank(self):
        from pyrite_software_kb.entry_types import BacklogItemEntry

        entry = BacklogItemEntry(id="test", title="Test", rank=100)
        fm = entry.to_frontmatter()
        assert fm["rank"] == 100

    def test_from_frontmatter_parses_rank(self):
        from pyrite_software_kb.entry_types import BacklogItemEntry

        entry = BacklogItemEntry.from_frontmatter(
            {"title": "Test", "type": "backlog_item", "rank": 200}, "body"
        )
        assert entry.rank == 200

    def test_from_frontmatter_default_rank(self):
        from pyrite_software_kb.entry_types import BacklogItemEntry

        entry = BacklogItemEntry.from_frontmatter({"title": "Test", "type": "backlog_item"}, "body")
        assert entry.rank == 0

    def test_roundtrip_with_rank(self):
        from pyrite_software_kb.entry_types import BacklogItemEntry

        entry = BacklogItemEntry(id="ranked", title="Ranked Item", rank=300, kind="feature")
        fm = entry.to_frontmatter()
        restored = BacklogItemEntry.from_frontmatter(fm, entry.body)
        assert restored.rank == 300
        assert restored.kind == "feature"


class TestBacklogItemRankValidator:
    def test_rank_valid_positive(self):
        from pyrite_software_kb.validators import validate_software_kb

        errors = validate_software_kb("backlog_item", {"rank": 100}, {})
        rank_errors = [e for e in errors if e["field"] == "rank"]
        assert rank_errors == []

    def test_rank_valid_zero(self):
        from pyrite_software_kb.validators import validate_software_kb

        errors = validate_software_kb("backlog_item", {"rank": 0}, {})
        rank_errors = [e for e in errors if e["field"] == "rank"]
        assert rank_errors == []

    def test_rank_invalid_negative(self):
        from pyrite_software_kb.validators import validate_software_kb

        errors = validate_software_kb("backlog_item", {"rank": -5}, {})
        rank_errors = [e for e in errors if e["field"] == "rank"]
        assert len(rank_errors) == 1
        assert rank_errors[0]["rule"] == "min_value"

    def test_rank_invalid_string(self):
        from pyrite_software_kb.validators import validate_software_kb

        errors = validate_software_kb("backlog_item", {"rank": "abc"}, {})
        rank_errors = [e for e in errors if e["field"] == "rank"]
        assert len(rank_errors) == 1
        assert rank_errors[0]["rule"] == "type"


# =========================================================================
# TestEpicRelationships
# =========================================================================


class TestEpicRelationships:
    def test_has_subtask_registered(self):
        plugin = SoftwareKBPlugin()
        rels = plugin.get_relationship_types()
        assert "has_subtask" in rels
        assert rels["has_subtask"]["inverse"] == "subtask_of"

    def test_subtask_of_registered(self):
        plugin = SoftwareKBPlugin()
        rels = plugin.get_relationship_types()
        assert "subtask_of" in rels
        assert rels["subtask_of"]["inverse"] == "has_subtask"


# =========================================================================
# TestEpicProgress
# =========================================================================


class TestEpicProgress:
    def test_epic_progress_with_subtasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-1",
                        "title": "Test Epic",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "meta": {"kind": "epic", "status": "in_progress"},
                    },
                    {
                        "id": "sub-1",
                        "title": "Subtask 1",
                        "entry_type": "backlog_item",
                        "status": "done",
                        "meta": {"kind": "feature", "status": "done"},
                    },
                    {
                        "id": "sub-2",
                        "title": "Subtask 2",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                    {
                        "id": "sub-3",
                        "title": "Subtask 3",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "meta": {"kind": "feature", "status": "in_progress"},
                    },
                ],
                links=[
                    {
                        "source": "epic-1",
                        "target": "sub-1",
                        "relation": "has_subtask",
                        "inverse": "subtask_of",
                    },
                    {
                        "source": "epic-1",
                        "target": "sub-2",
                        "relation": "has_subtask",
                        "inverse": "subtask_of",
                    },
                    {
                        "source": "epic-1",
                        "target": "sub-3",
                        "relation": "has_subtask",
                        "inverse": "subtask_of",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                progress = plugin._get_epic_progress(db, "epic-1", "test", readable_kbs=UNSCOPED)
                assert progress["total"] == 3
                assert progress["done"] == 1
                assert progress["in_progress"] == 1
                assert progress["completion_pct"] == 33
                assert len(progress["subtasks"]) == 3
                assert "done" in progress["by_status"]
                assert "proposed" in progress["by_status"]
            finally:
                db.close()

    def test_epic_progress_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-empty",
                        "title": "Empty Epic",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "epic", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                progress = plugin._get_epic_progress(
                    db, "epic-empty", "test", readable_kbs=UNSCOPED
                )
                assert progress["total"] == 0
                assert progress["done"] == 0
                assert progress["completion_pct"] == 0
            finally:
                db.close()

    def test_epic_progress_via_subtask_of_backlinks(self):
        """Items linking to epic via subtask_of should also be counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-2",
                        "title": "Epic Two",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "meta": {"kind": "epic", "status": "in_progress"},
                    },
                    {
                        "id": "child-1",
                        "title": "Child via subtask_of",
                        "entry_type": "backlog_item",
                        "status": "done",
                        "meta": {"kind": "feature", "status": "done"},
                    },
                ],
                links=[
                    {
                        "source": "child-1",
                        "target": "epic-2",
                        "relation": "subtask_of",
                        "inverse": "has_subtask",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                progress = plugin._get_epic_progress(db, "epic-2", "test", readable_kbs=UNSCOPED)
                assert progress["total"] == 1
                assert progress["done"] == 1
                assert progress["completion_pct"] == 100
            finally:
                db.close()


# =========================================================================
# TestEpicsMcpTool
# =========================================================================


class TestEpicsMcpTool:
    def test_sw_epics_in_read_tools(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("read")
        assert "sw_epics" in tools
        assert "sw_epic_detail" in tools

    def test_sw_prioritize_in_write_tools(self):
        plugin = SoftwareKBPlugin()
        tools = plugin.get_mcp_tools("write")
        assert "sw_prioritize" in tools

    def test_epics_lists_only_epics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-a",
                        "title": "Epic A",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "epic", "status": "proposed"},
                    },
                    {
                        "id": "feature-1",
                        "title": "Feature 1",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_epics({"kb_name": "test"})
                assert result["count"] == 1
                assert result["epics"][0]["id"] == "epic-a"
            finally:
                db.close()

    def test_epic_detail_returns_subtasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-det",
                        "title": "Detail Epic",
                        "entry_type": "backlog_item",
                        "status": "in_progress",
                        "meta": {"kind": "epic", "status": "in_progress"},
                    },
                    {
                        "id": "sub-det-1",
                        "title": "Sub 1",
                        "entry_type": "backlog_item",
                        "status": "done",
                        "meta": {"kind": "feature", "status": "done"},
                    },
                ],
                links=[
                    {
                        "source": "epic-det",
                        "target": "sub-det-1",
                        "relation": "has_subtask",
                        "inverse": "subtask_of",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_epic_detail({"epic_id": "epic-det", "kb_name": "test"})
                assert result["total"] == 1
                assert result["done"] == 1
                assert result["completion_pct"] == 100
                assert "done" in result["by_status"]
            finally:
                db.close()

    def test_epic_detail_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_epic_detail({"epic_id": "nope", "kb_name": "test"})
                assert "error" in result
            finally:
                db.close()


# =========================================================================
# TestBacklogSortAndFilter
# =========================================================================


class TestBacklogSortAndFilter:
    def test_sort_by_rank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "unranked",
                        "title": "Unranked",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "priority": "medium",
                        "meta": {"kind": "feature", "status": "proposed", "priority": "medium"},
                        "created_at": "2026-01-01T00:00:00",
                    },
                    {
                        "id": "rank-200",
                        "title": "Rank 200",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "priority": "medium",
                        "meta": {
                            "kind": "feature",
                            "status": "proposed",
                            "priority": "medium",
                            "rank": 200,
                        },
                        "created_at": "2026-01-02T00:00:00",
                    },
                    {
                        "id": "rank-100",
                        "title": "Rank 100",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "priority": "medium",
                        "meta": {
                            "kind": "feature",
                            "status": "proposed",
                            "priority": "medium",
                            "rank": 100,
                        },
                        "created_at": "2026-01-03T00:00:00",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test", "sort": "rank"})
                ids = [i["id"] for i in result["items"]]
                # Ranked items first (100, 200), then unranked
                assert ids == ["rank-100", "rank-200", "unranked"]
            finally:
                db.close()

    def test_filter_by_epic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-f",
                        "title": "Filter Epic",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "epic", "status": "proposed"},
                    },
                    {
                        "id": "in-epic",
                        "title": "In Epic",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                    {
                        "id": "out-epic",
                        "title": "Outside Epic",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
                links=[
                    {
                        "source": "epic-f",
                        "target": "in-epic",
                        "relation": "has_subtask",
                        "inverse": "subtask_of",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test", "epic": "epic-f"})
                ids = [i["id"] for i in result["items"]]
                assert "in-epic" in ids
                assert "out-epic" not in ids
            finally:
                db.close()

    def test_default_limit_bounds_large_result_set(self):
        """No --limit given: result is bounded, not the full 661-item firehose (#233)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                {
                    "id": f"item-{i:03d}",
                    "title": f"Item {i}",
                    "entry_type": "backlog_item",
                    "status": "proposed",
                    "priority": "medium",
                    "meta": {"kind": "feature", "status": "proposed", "priority": "medium"},
                    "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00",
                }
                for i in range(75)
            ]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test"})
                assert len(result["items"]) < 75
                assert result["has_more"] is True
                assert result["count"] == len(result["items"])
                assert result["total"] == 75
            finally:
                db.close()

    def test_limit_and_offset_page_without_skip_or_duplicate(self):
        """Paging through --limit/--offset covers every item exactly once."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                {
                    "id": f"item-{i:03d}",
                    "title": f"Item {i}",
                    "entry_type": "backlog_item",
                    "status": "proposed",
                    "priority": "medium",
                    "meta": {"kind": "feature", "status": "proposed", "priority": "medium"},
                    "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00",
                }
                for i in range(23)
            ]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                seen_ids: list[str] = []
                offset = 0
                page_size = 10
                for _ in range(10):  # enough iterations to exhaust 23 items
                    result = plugin._mcp_backlog(
                        {"kb_name": "test", "limit": page_size, "offset": offset}
                    )
                    ids = [i["id"] for i in result["items"]]
                    seen_ids.extend(ids)
                    if not result["has_more"]:
                        break
                    offset += page_size
                assert sorted(seen_ids) == sorted(e["id"] for e in entries)
                assert len(seen_ids) == len(set(seen_ids))  # no duplicates across pages
            finally:
                db.close()

    def test_has_more_accurate_at_boundary(self):
        """has_more is False exactly when a page reaches the last item."""
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                {
                    "id": f"item-{i:03d}",
                    "title": f"Item {i}",
                    "entry_type": "backlog_item",
                    "status": "proposed",
                    "priority": "medium",
                    "meta": {"kind": "feature", "status": "proposed", "priority": "medium"},
                    "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00",
                }
                for i in range(10)
            ]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                # Exactly consumes all 10 -> no more.
                result = plugin._mcp_backlog({"kb_name": "test", "limit": 10, "offset": 0})
                assert result["total"] == 10
                assert result["has_more"] is False

                # One short of the end -> more remains.
                result = plugin._mcp_backlog({"kb_name": "test", "limit": 9, "offset": 0})
                assert result["has_more"] is True

                # Offset lands exactly on the last item -> no more.
                result = plugin._mcp_backlog({"kb_name": "test", "limit": 5, "offset": 5})
                assert len(result["items"]) == 5
                assert result["has_more"] is False

                # Offset past the end -> empty page, no more.
                result = plugin._mcp_backlog({"kb_name": "test", "limit": 5, "offset": 20})
                assert result["items"] == []
                assert result["has_more"] is False
            finally:
                db.close()

    def test_filter_composes_with_limit_not_applied_after(self):
        """--status filtering happens before the limit is applied.

        If limiting happened first and filtering second, a --limit smaller
        than the unfiltered result could silently drop matching items that
        were sorted past the cut, returning the wrong page.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # 5 "done" items sorted before 5 "proposed" items by created_at,
            # so a naive limit-then-filter with limit=3 would see only "done"
            # items and incorrectly report zero proposed results.
            entries = [
                {
                    "id": f"done-{i}",
                    "title": f"Done {i}",
                    "entry_type": "backlog_item",
                    "status": "done",
                    "priority": "low",
                    "meta": {"kind": "feature", "status": "done", "priority": "low"},
                    "created_at": f"2025-01-{i + 1:02d}T00:00:00",
                }
                for i in range(5)
            ] + [
                {
                    "id": f"proposed-{i}",
                    "title": f"Proposed {i}",
                    "entry_type": "backlog_item",
                    "status": "proposed",
                    "priority": "critical",
                    "meta": {"kind": "feature", "status": "proposed", "priority": "critical"},
                    "created_at": f"2025-02-{i + 1:02d}T00:00:00",
                }
                for i in range(5)
            ]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog(
                    {"kb_name": "test", "status": "proposed", "limit": 3, "offset": 0}
                )
                ids = [i["id"] for i in result["items"]]
                assert len(ids) == 3
                assert all(i.startswith("proposed-") for i in ids)
                assert result["total"] == 5
                assert result["has_more"] is True
            finally:
                db.close()

    def test_group_by_epic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "epic-g",
                        "title": "Group Epic",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "epic", "status": "proposed"},
                    },
                    {
                        "id": "grouped",
                        "title": "Grouped Item",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                    {
                        "id": "orphan",
                        "title": "Orphan Item",
                        "entry_type": "backlog_item",
                        "status": "proposed",
                        "meta": {"kind": "feature", "status": "proposed"},
                    },
                ],
                links=[
                    {
                        "source": "grouped",
                        "target": "epic-g",
                        "relation": "subtask_of",
                        "inverse": "has_subtask",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_backlog({"kb_name": "test", "group_by": "epic"})
                assert "groups" in result
                groups = result["groups"]
                epic_group = [g for g in groups if g["epic_id"] == "epic-g"]
                unassigned = [g for g in groups if g["epic_id"] is None]
                assert len(epic_group) == 1
                assert epic_group[0]["count"] >= 1
                assert len(unassigned) == 1
            finally:
                db.close()


class TestBacklogCliPaginationFlags:
    """`sw backlog --limit/--offset` (#233) must reach `_mcp_backlog`."""

    def test_limit_and_offset_threaded_to_mcp_backlog(self):
        from unittest.mock import MagicMock, patch

        from pyrite_software_kb.cli import sw_app
        from typer.testing import CliRunner

        runner = CliRunner()
        mock_plugin = MagicMock()
        mock_plugin._mcp_backlog.return_value = {"count": 0, "items": [], "has_more": False}

        with patch("pyrite_software_kb.plugin.SoftwareKBPlugin", return_value=mock_plugin):
            result = runner.invoke(
                sw_app,
                ["backlog", "--limit", "5", "--offset", "10", "--kb", "test"],
            )

        assert result.exit_code == 0, result.output
        mock_plugin._mcp_backlog.assert_called_once()
        call_args = mock_plugin._mcp_backlog.call_args[0][0]
        assert call_args["limit"] == 5
        assert call_args["offset"] == 10


# =========================================================================
# TestPullNextRankAware
# =========================================================================


class TestPullNextRankAware:
    def test_ranked_item_preferred_over_unranked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "unranked-acc",
                        "title": "Unranked Accepted",
                        "status": "accepted",
                        "priority": "medium",
                        "meta": {"kind": "feature", "status": "accepted", "priority": "medium"},
                        "created_at": "2026-01-01T00:00:00",
                    },
                    {
                        "id": "ranked-acc",
                        "title": "Ranked Accepted",
                        "status": "accepted",
                        "priority": "medium",
                        "meta": {
                            "kind": "feature",
                            "status": "accepted",
                            "priority": "medium",
                            "rank": 100,
                        },
                        "created_at": "2026-01-02T00:00:00",
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                result = plugin._mcp_pull_next({"kb_name": "test"})
                assert result["recommendation"]["id"] == "ranked-acc"
            finally:
                db.close()


# =========================================================================
# TestOrphanBacklogItemChecker
# =========================================================================


class TestOrphanBacklogItemChecker:
    def test_orphan_open_item_warns(self):
        from pyrite.services.rubric_checkers import check_orphan_backlog_item

        entry = {
            "id": "orphan-1",
            "kb_name": "test",
            "status": "proposed",
            "metadata": json.dumps({"kind": "feature", "status": "proposed"}),
            "_links": [],
        }
        result = check_orphan_backlog_item(entry, None)
        assert result is not None
        assert result["severity"] == "warning"
        assert "subtask_of" in result["message"]

    def test_item_with_parent_passes(self):
        from pyrite.services.rubric_checkers import check_orphan_backlog_item

        entry = {
            "id": "child-1",
            "kb_name": "test",
            "status": "proposed",
            "metadata": json.dumps({"kind": "feature", "status": "proposed"}),
            "_links": [{"relation": "subtask_of", "target": "epic-1"}],
        }
        result = check_orphan_backlog_item(entry, None)
        assert result is None

    def test_epic_item_passes(self):
        from pyrite.services.rubric_checkers import check_orphan_backlog_item

        entry = {
            "id": "epic-1",
            "kb_name": "test",
            "status": "proposed",
            "metadata": json.dumps({"kind": "epic", "status": "proposed"}),
            "_links": [],
        }
        result = check_orphan_backlog_item(entry, None)
        assert result is None

    def test_done_item_passes(self):
        from pyrite.services.rubric_checkers import check_orphan_backlog_item

        entry = {
            "id": "done-1",
            "kb_name": "test",
            "status": "done",
            "metadata": json.dumps({"kind": "feature", "status": "done"}),
            "_links": [],
        }
        result = check_orphan_backlog_item(entry, None)
        assert result is None


# =========================================================================
# TestPrioritizeAfterAnchor — Tier A 1050
# =========================================================================


class TestPrioritizeAfterAnchor:
    """`sw prioritize --after <anchor> item-a item-b` must assign ranks
    starting just after the anchor's rank, not from a global 100/200/300...
    sequence that clobbers existing ranks on other items.

    Regression for bug-pyrite-sw-prioritize-renumbers-globally-clobbering-
    existing-ranks (Tier A rank 1050). The pre-fix behavior renumbered every
    named item from rank 100 with step 100; if any unnamed item already held
    those ranks (e.g. the cascade cluster at 100-700), it was silently shadowed.
    """

    def test_after_anchor_with_batch_starts_at_anchor_plus_100(self):
        """Multi-item prioritize with --after: ranks start at anchor_rank + 100,
        step 100. Anchor and other ranked items are not modified."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    # The anchor: an existing ranked item that must NOT be touched.
                    {
                        "id": "anchor-item",
                        "title": "Anchor",
                        "entry_type": "backlog_item",
                        "meta": {"kind": "feature", "rank": 700},
                    },
                    # The items to rank, none yet ranked.
                    {
                        "id": "new-a",
                        "title": "New A",
                        "entry_type": "backlog_item",
                        "meta": {"kind": "feature"},
                    },
                    {
                        "id": "new-b",
                        "title": "New B",
                        "entry_type": "backlog_item",
                        "meta": {"kind": "feature"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_prioritize(
                        {
                            "kb_name": "test",
                            "item_ids": ["new-a", "new-b"],
                            "after": "anchor-item",
                        }
                    )

                # No error
                assert "error" not in result, result
                # new-a got 800 (700 + 100), new-b got 900.
                updates = {u["id"]: u["rank"] for u in result.get("updated", [])}
                assert updates == {"new-a": 800, "new-b": 900}, updates

                # Crucially: anchor-item's rank was never written. mock_svc
                # records every update_entry call.
                anchor_calls = [
                    c
                    for c in mock_svc.update_entry.call_args_list
                    if c.args[:2] == ("anchor-item", "test")
                ]
                assert anchor_calls == [], (
                    f"--after must NOT update the anchor; got calls {anchor_calls}"
                )
            finally:
                db.close()

    def test_after_anchor_missing_returns_error(self):
        """If the anchor ID doesn't exist, refuse with an error rather than
        silently falling back to the global-renumber path."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(
                tmpdir,
                entries=[
                    {
                        "id": "new-a",
                        "title": "New A",
                        "entry_type": "backlog_item",
                        "meta": {"kind": "feature"},
                    },
                ],
            )
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_prioritize(
                        {
                            "kb_name": "test",
                            "item_ids": ["new-a"],
                            "after": "no-such-anchor",
                        }
                    )
                assert "error" in result
                assert "no-such-anchor" in result["error"]
                # And the unranked items were not silently re-ranked.
                assert mock_svc.update_entry.call_count == 0
            finally:
                db.close()

    def test_no_anchor_batch_still_works(self):
        """Existing batch-without-anchor behavior preserved: ranks 100, 200, ...
        This is the pre-fix path; it stays as the fallback so callers who
        accept the destructive default can still use it."""
        from unittest.mock import MagicMock, patch

        with tempfile.TemporaryDirectory() as tmpdir:
            db = _make_test_db(tmpdir, entries=[])
            try:
                plugin = _make_plugin_with_db(db)
                mock_svc = MagicMock()
                with (
                    patch("pyrite.config.load_config"),
                    patch("pyrite.services.kb_service.KBService", return_value=mock_svc),
                ):
                    result = plugin._mcp_prioritize(
                        {
                            "kb_name": "test",
                            "item_ids": ["a", "b"],
                        }
                    )
                updates = {u["id"]: u["rank"] for u in result.get("updated", [])}
                assert updates == {"a": 100, "b": 200}
            finally:
                db.close()


SW_LIST_SURFACES = [
    ("_mcp_adrs", "adrs", "status", "proposed"),
    ("_mcp_component", "components", "path", "pyrite/x.py"),
    ("_mcp_standards", "standards", "category", "coding"),
]


def _sw_entry(entry_id, entry_type, index):
    """One row for the three list surfaces, with the metadata each reads."""
    meta = {
        "adr": {"adr_number": index},
        "component": {"kind": "module", "path": "pyrite/x.py"},
        "standard": {"category": "coding"},
    }[entry_type]
    return {
        "id": entry_id,
        "title": f"{entry_type} {index}",
        "entry_type": entry_type,
        "status": "proposed",
        "meta": meta,
        "created_at": f"2026-01-{(index % 28) + 1:02d}T00:00:00",
    }


class TestSwListCommandsAreBounded:
    """`sw adrs`, `sw components` and `sw standards` bound their output (#238).

    The properties #233 pinned for `sw backlog`, on the three surfaces that had
    the same shape: a default bound, a paging walk covering every item once,
    `has_more` exact at the boundary, and filtering applied *before* the bound.
    """

    @pytest.mark.parametrize("handler,key,filter_arg,filter_value", SW_LIST_SURFACES)
    def test_the_default_bound_is_fifty(self, handler, key, filter_arg, filter_value):
        entry_type = "adr" if key == "adrs" else key[:-1]
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [_sw_entry(f"e-{i:03d}", entry_type, i) for i in range(75)]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                result = getattr(_make_plugin_with_db(db), handler)({"kb_name": "test"})
                assert len(result[key]) == 50, "the default bound is 50, as elsewhere"
                assert result["total"] == 75
                assert result["count"] == len(result[key])
                assert result["has_more"] is True
            finally:
                db.close()

    @pytest.mark.parametrize("handler,key,filter_arg,filter_value", SW_LIST_SURFACES)
    def test_paging_covers_every_item_once(self, handler, key, filter_arg, filter_value):
        entry_type = "adr" if key == "adrs" else key[:-1]
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [_sw_entry(f"e-{i:03d}", entry_type, i) for i in range(23)]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                seen: list[str] = []
                offset = 0
                for _ in range(10):
                    result = getattr(plugin, handler)(
                        {"kb_name": "test", "limit": 10, "offset": offset}
                    )
                    seen.extend(item["id"] for item in result[key])
                    if not result["has_more"]:
                        break
                    offset += 10
                assert sorted(seen) == sorted(e["id"] for e in entries)
                assert len(seen) == len(set(seen)), "no duplicates across pages"
            finally:
                db.close()

    @pytest.mark.parametrize("handler,key,filter_arg,filter_value", SW_LIST_SURFACES)
    def test_has_more_is_exact_at_the_boundary(self, handler, key, filter_arg, filter_value):
        entry_type = "adr" if key == "adrs" else key[:-1]
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [_sw_entry(f"e-{i:03d}", entry_type, i) for i in range(10)]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                plugin = _make_plugin_with_db(db)
                exact = getattr(plugin, handler)({"kb_name": "test", "limit": 10, "offset": 0})
                assert exact["has_more"] is False

                short = getattr(plugin, handler)({"kb_name": "test", "limit": 9, "offset": 0})
                assert short["has_more"] is True

                past_end = getattr(plugin, handler)({"kb_name": "test", "limit": 5, "offset": 20})
                assert past_end[key] == []
                assert past_end["has_more"] is False
            finally:
                db.close()

    @pytest.mark.parametrize("handler,key,filter_arg,filter_value", SW_LIST_SURFACES)
    def test_limit_none_returns_everything(self, handler, key, filter_arg, filter_value):
        """`--limit 0` on the CLI passes None: the full, unbounded list."""
        entry_type = "adr" if key == "adrs" else key[:-1]
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [_sw_entry(f"e-{i:03d}", entry_type, i) for i in range(60)]
            db = _make_test_db(tmpdir, entries=entries)
            try:
                result = getattr(_make_plugin_with_db(db), handler)(
                    {"kb_name": "test", "limit": None}
                )
                assert len(result[key]) == 60
                assert result["total"] == 60
                assert result["has_more"] is False
            finally:
                db.close()

    @pytest.mark.parametrize("handler,key,filter_arg,filter_value", SW_LIST_SURFACES)
    def test_the_filter_is_applied_before_the_bound(self, handler, key, filter_arg, filter_value):
        """Filter first, bound second -- the other order returns the wrong page.

        The five non-matching rows are created *later* than the matching ones,
        so they sort first. A bound-then-filter with limit=3 would look at
        three non-matching rows, discard them, and report nothing; filtering
        first reports the true total of five.
        """
        entry_type = "adr" if key == "adrs" else key[:-1]
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [_sw_entry(f"match-{i}", entry_type, i) for i in range(5)]
            others = [_sw_entry(f"other-{i}", entry_type, i + 20) for i in range(5)]
            for other in others:
                if entry_type == "adr":
                    other["status"] = "accepted"
                elif entry_type == "component":
                    other["meta"]["path"] = "elsewhere/z.py"
                else:
                    other["meta"]["category"] = "testing"
            db = _make_test_db(tmpdir, entries=entries + others)
            try:
                result = getattr(_make_plugin_with_db(db), handler)(
                    {"kb_name": "test", filter_arg: filter_value, "limit": 3}
                )
                assert result["total"] == 5, "the bound applies to the filtered set"
                assert len(result[key]) == 3
                assert {item["id"] for item in result[key]} <= {f"match-{i}" for i in range(5)}
            finally:
                db.close()
