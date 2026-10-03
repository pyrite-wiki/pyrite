"""Tests for core task entry type, workflow, validators, and hooks."""

import pytest

from pyrite.models.task import (
    TASK_KB_PRESET,
    TASK_STATUSES,
    TASK_WORKFLOW,
    TaskEntry,
    can_transition,
    get_allowed_transitions,
    requires_reason,
)
from pyrite.models.task_validators import validate_task
from pyrite.server.tool_schemas import READ_TOOLS, WRITE_TOOLS


# =========================================================================
# Core registration
# =========================================================================


class TestCoreRegistration:
    def test_entry_type_in_registry(self):
        from pyrite.models.core_types import ENTRY_TYPE_REGISTRY

        assert "task" in ENTRY_TYPE_REGISTRY
        assert ENTRY_TYPE_REGISTRY["task"] is TaskEntry

    def test_get_entry_class_returns_task(self):
        from pyrite.models.core_types import get_entry_class

        assert get_entry_class("task") is TaskEntry

    def test_relationship_types_include_core(self):
        from pyrite.plugins.registry import CORE_RELATIONSHIP_TYPES

        assert "subtask_of" in CORE_RELATIONSHIP_TYPES
        assert "has_subtask" in CORE_RELATIONSHIP_TYPES
        assert "produces" in CORE_RELATIONSHIP_TYPES
        assert CORE_RELATIONSHIP_TYPES["subtask_of"]["inverse"] == "has_subtask"
        assert CORE_RELATIONSHIP_TYPES["produces"]["inverse"] == "produced_by"

    def test_kb_presets_include_task(self):
        from pyrite.plugins.registry import PluginRegistry

        registry = PluginRegistry()
        presets = registry.get_all_kb_presets()
        assert "task" in presets

    def test_core_hooks_registered(self):
        """KBService registers the task-system core hooks on its HookRunner
        at construction. After the extract-hookrunner-from-kb-service refactor
        (step 3) the registration lives in task_service.register_task_hooks
        and runs from KBService.__init__; this test asserts the wiring."""
        from unittest.mock import MagicMock

        from pyrite.services.kb_service import KBService

        svc = KBService(config=MagicMock(), db=MagicMock())
        assert len(svc.hook_runner.core_hooks("before_save")) >= 1
        # No core after_save hook: the parent rollup is deleted and a parent's
        # completion is derived (ADR-0042 decision 4).
        assert svc.hook_runner.core_hooks("after_save") == []


# =========================================================================
# TaskEntry
# =========================================================================


class TestTaskEntry:
    def test_default_values(self):
        entry = TaskEntry(id="test", title="Test Task")
        assert entry.entry_type == "task"
        assert entry.status == "open"
        assert entry.assignee == ""
        assert entry.parent == ""
        assert entry.dependencies == []
        assert entry.evidence == []
        assert entry.priority == 5
        assert entry.due_date == ""
        assert entry.agent_context == {}

    def test_to_frontmatter(self):
        entry = TaskEntry(
            id="test",
            title="Implement Feature",
            status="in_progress",
            assignee="agent:claude-code-7a3f",
            parent="parent-123",
            dependencies=["dep-1", "dep-2"],
            evidence=["doc-1"],
            priority=3,
            due_date="2026-03-01",
            agent_context={"confidence": 0.9},
        )
        fm = entry.to_frontmatter()
        assert fm["type"] == "task"
        assert fm["status"] == "in_progress"
        assert fm["assignee"] == "agent:claude-code-7a3f"
        assert fm["parent"] == "parent-123"
        assert fm["dependencies"] == ["dep-1", "dep-2"]
        assert fm["evidence"] == ["doc-1"]
        assert fm["priority"] == 3
        assert fm["due_date"] == "2026-03-01"
        assert fm["agent_context"] == {"confidence": 0.9}

    def test_to_frontmatter_omits_defaults(self):
        entry = TaskEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert fm["status"] == "open"  # always written to prevent round-trip data loss
        assert "assignee" not in fm
        assert "parent" not in fm
        assert "dependencies" not in fm
        assert "evidence" not in fm
        assert fm["priority"] == 5  # always written to prevent round-trip data loss
        assert "due_date" not in fm
        assert "agent_context" not in fm

    def test_from_frontmatter(self):
        meta = {
            "id": "task-001",
            "title": "Build API",
            "type": "task",
            "status": "claimed",
            "assignee": "agent:test",
            "parent": "epic-1",
            "dependencies": ["task-000"],
            "evidence": ["doc-1"],
            "priority": 2,
            "due_date": "2026-04-01",
            "agent_context": {"checkpoint": "step3"},
            "tags": ["api"],
        }
        entry = TaskEntry.from_frontmatter(meta, "Build the REST API.")
        assert entry.id == "task-001"
        assert entry.status == "claimed"
        assert entry.assignee == "agent:test"
        assert entry.parent == "epic-1"
        assert entry.dependencies == ["task-000"]
        assert entry.evidence == ["doc-1"]
        assert entry.priority == 2
        assert entry.due_date == "2026-04-01"
        assert entry.agent_context == {"checkpoint": "step3"}
        assert entry.tags == ["api"]
        assert "REST API" in entry.body

    def test_from_frontmatter_legacy_parent_task(self):
        """Legacy parent_task field should be accepted."""
        meta = {
            "id": "task-002",
            "title": "Legacy task",
            "type": "task",
            "parent_task": "old-parent",
        }
        entry = TaskEntry.from_frontmatter(meta, "")
        assert entry.parent == "old-parent"

    def test_roundtrip_markdown(self):
        entry = TaskEntry(
            id="task-001",
            title="Build API",
            body="## Description\n\nBuild the API.",
            status="in_progress",
            priority=3,
            tags=["api"],
        )
        md = entry.to_markdown()
        assert "status: in_progress" in md
        assert "priority: 3" in md
        assert "Build the API." in md


class TestTaskEntryUnknownFrontmatterKeys:
    """Regression coverage for
    issue-pyrite-task-update-strips-non-schema-frontmatter-fields-silently-unparks-monitors.

    A top-level frontmatter key TaskEntry doesn't declare as a schema field
    (e.g. `parked_awaiting:`, the conductor's parked-monitor convention) must
    survive a load -> save round trip rather than being silently dropped.
    """

    def test_from_frontmatter_collects_unknown_key_into_metadata(self):
        meta = {
            "id": "task-003",
            "title": "Monitor task",
            "type": "task",
            "status": "in_progress",
            "parked_awaiting": "2026-09-11",
        }
        entry = TaskEntry.from_frontmatter(meta, "")
        assert entry.metadata.get("parked_awaiting") == "2026-09-11"

    def test_to_frontmatter_promotes_unknown_key_to_top_level(self):
        entry = TaskEntry(
            id="task-003",
            title="Monitor task",
            status="in_progress",
            metadata={"parked_awaiting": "2026-09-11"},
        )
        fm = entry.to_frontmatter()
        assert fm.get("parked_awaiting") == "2026-09-11"
        # Promoted to top level, not left nested under metadata: — this is
        # the shape the conductor's dispatch classifier greps for
        # (`^parked_awaiting:` in the file body).
        assert "metadata" not in fm

    def test_parked_awaiting_survives_load_mutate_save_roundtrip(self):
        """The exact failure mode reported: task claim / task update -s
        rewrite the file's frontmatter via load -> mutate -> save, and the
        unknown key must still be present afterward."""
        meta = {
            "id": "task-003",
            "title": "Monitor task",
            "type": "task",
            "status": "open",
            "parked_awaiting": "waiting-on-rfp-deadline",
        }
        entry = TaskEntry.from_frontmatter(meta, "body")
        # Simulate a claim/update: mutate a known field, then re-serialize —
        # mirrors KBService.update_entry's load -> setattr -> save_entry.
        entry.status = "claimed"
        entry.assignee = "agent:test"
        fm = entry.to_frontmatter()
        assert fm["status"] == "claimed"
        assert fm["assignee"] == "agent:test"
        assert fm["parked_awaiting"] == "waiting-on-rfp-deadline"

    def test_explicit_nested_metadata_block_still_round_trips(self):
        """Legacy nested `metadata:` block (rare but present in some KB
        files) should still load correctly; its keys get promoted to
        top-level on the next save, matching GenericEntry's existing
        behavior for kb.yaml custom types."""
        meta = {
            "id": "task-004",
            "title": "Legacy metadata task",
            "type": "task",
            "status": "open",
            "metadata": {"custom_field": "hello"},
        }
        entry = TaskEntry.from_frontmatter(meta, "")
        assert entry.metadata.get("custom_field") == "hello"
        fm = entry.to_frontmatter()
        assert fm.get("custom_field") == "hello"


# =========================================================================
# Workflows
# =========================================================================


class TestTaskWorkflow:
    def test_states(self):
        assert TASK_WORKFLOW["states"] == [
            "open",
            "claimed",
            "in_progress",
            "blocked",
            "review",
            "done",
            "failed",
            "cancelled",
        ]
        assert TASK_WORKFLOW["initial"] == "open"
        assert TASK_WORKFLOW["field"] == "status"

    def test_happy_path(self):
        assert can_transition(TASK_WORKFLOW, "open", "claimed", "write")
        assert can_transition(TASK_WORKFLOW, "claimed", "in_progress", "write")
        assert can_transition(TASK_WORKFLOW, "in_progress", "done", "write")

    def test_blocked_resume(self):
        assert can_transition(TASK_WORKFLOW, "in_progress", "blocked", "write")
        assert can_transition(TASK_WORKFLOW, "blocked", "in_progress", "write")

    def test_review_done(self):
        assert can_transition(TASK_WORKFLOW, "in_progress", "review", "write")
        assert can_transition(TASK_WORKFLOW, "review", "done", "write")

    def test_review_back_to_in_progress(self):
        assert can_transition(TASK_WORKFLOW, "review", "in_progress", "write")

    def test_failed_to_open_requires_reason(self):
        assert can_transition(TASK_WORKFLOW, "in_progress", "failed", "write")
        assert can_transition(TASK_WORKFLOW, "failed", "open", "write")
        assert requires_reason(TASK_WORKFLOW, "failed", "open")

    def test_cannot_skip_states(self):
        assert not can_transition(TASK_WORKFLOW, "open", "done", "write")
        assert not can_transition(TASK_WORKFLOW, "open", "in_progress", "write")
        assert not can_transition(TASK_WORKFLOW, "claimed", "done", "write")

    def test_no_transition_without_role(self):
        assert not can_transition(TASK_WORKFLOW, "open", "claimed", "")

    def test_allowed_transitions_from_in_progress(self):
        allowed = get_allowed_transitions(TASK_WORKFLOW, "in_progress", "write")
        targets = [t["to"] for t in allowed]
        assert "blocked" in targets
        assert "review" in targets
        assert "done" in targets
        assert "failed" in targets


# =========================================================================
# Relaxed-mode state machine — Tier A r1175
# =========================================================================


class TestRelaxedModeWorkflowKeys:
    """The core TASK_WORKFLOW declares the new toggles with strict defaults.

    Existing tasks unchanged: enforce_transitions=True keeps the workflow
    behaving exactly as it did, require_reason_on_transition=False keeps
    status_reason optional. Per-type configs override these.
    """

    def test_default_enforce_transitions_is_true(self):
        """Strict by default — back-compat for every task that existed
        before this feature. `pyrite/models/task.py` ships strict."""
        assert TASK_WORKFLOW.get("enforce_transitions", True) is True

    def test_default_require_reason_on_transition_is_false(self):
        """Reason optional by default — back-compat. A relaxed-mode type
        schema overrides this to True."""
        assert TASK_WORKFLOW.get("require_reason_on_transition", False) is False


class TestValidateStatusChange:
    """The new `validate_status_change` helper dispatches strict vs relaxed
    based on the workflow's `enforce_transitions` flag. Returns
    (ok: bool, error: str).

    This is what `_task_validate_transition` will call after resolving
    the per-type workflow. Tests pin both modes side-by-side.
    """

    @staticmethod
    def _strict_workflow():
        # Same shape as TASK_WORKFLOW but minimal — keep tests focused.
        return {
            "states": ["open", "claimed", "in_progress", "done"],
            "initial": "open",
            "field": "status",
            "transitions": [
                {"from": "open", "to": "claimed", "requires": "write"},
                {"from": "claimed", "to": "in_progress", "requires": "write"},
                {"from": "in_progress", "to": "done", "requires": "write"},
            ],
            "enforce_transitions": True,
            "require_reason_on_transition": False,
        }

    @staticmethod
    def _relaxed_workflow():
        # Same states, but the transitions table is bypassed entirely.
        return {
            "states": ["open", "claimed", "in_progress", "blocked", "done"],
            "initial": "open",
            "field": "status",
            "transitions": [],  # ignored under relaxed mode
            "enforce_transitions": False,
            "require_reason_on_transition": True,
        }

    # -- strict mode (existing behavior, regression-locked) -------------

    def test_strict_mode_allows_declared_transition(self):
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._strict_workflow(),
            old_status="open",
            new_status="claimed",
            status_reason="",
            user_role="write",
        )
        assert ok is True, err

    def test_strict_mode_rejects_skipped_state(self):
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._strict_workflow(),
            old_status="open",
            new_status="done",
            status_reason="",
            user_role="write",
        )
        assert ok is False
        assert "claimed" in err or "open" in err  # mentions valid path

    def test_strict_mode_does_not_require_reason(self):
        """Strict mode keeps the pre-existing 'reason optional except
        for transitions that opt in via requires_reason' behavior."""
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._strict_workflow(),
            old_status="claimed",
            new_status="in_progress",
            status_reason="",  # no reason, allowed by strict
            user_role="write",
        )
        assert ok is True, err

    # -- relaxed mode (new behavior) ------------------------------------

    def test_relaxed_mode_accepts_any_state_set_member_with_reason(self):
        """The relaxed-mode contract: any status that's in the type's
        `states` list is accepted, IFF status_reason is non-empty.
        This is what unblocks open->blocked for the conductor without
        adding a 'held' state."""
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._relaxed_workflow(),
            old_status="open",
            new_status="blocked",  # not in any strict transition
            status_reason="awaiting GAO docket",
            user_role="write",
        )
        assert ok is True, err

    def test_relaxed_mode_rejects_without_reason(self):
        """The whole point of relaxed mode is auditability via the
        reason field. No reason -> rejected."""
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._relaxed_workflow(),
            old_status="open",
            new_status="blocked",
            status_reason="",
            user_role="write",
        )
        assert ok is False
        assert "reason" in err.lower()

    def test_relaxed_mode_rejects_status_not_in_states_set(self):
        """Relaxed mode loosens transitions, NOT state membership.
        `status` must still be a member of the declared `states` —
        catches typos and keeps the index queryable."""
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._relaxed_workflow(),
            old_status="open",
            new_status="hung",  # not in states
            status_reason="a reason",
            user_role="write",
        )
        assert ok is False
        assert "hung" in err.lower() or "state" in err.lower()

    def test_relaxed_mode_whitespace_reason_is_not_a_reason(self):
        """Whitespace-only reason fails the same way as empty.
        Otherwise the audit field would be defeatable trivially."""
        from pyrite.models.task import validate_status_change

        ok, err = validate_status_change(
            self._relaxed_workflow(),
            old_status="open",
            new_status="blocked",
            status_reason="   \n  ",
            user_role="write",
        )
        assert ok is False
        assert "reason" in err.lower()

    # -- atomic claim preservation (both modes) -------------------------

    def test_open_to_claimed_works_under_strict_mode(self):
        """Sanity: the atomic-claim path is reachable under strict."""
        from pyrite.models.task import validate_status_change

        ok, _ = validate_status_change(
            self._strict_workflow(),
            old_status="open",
            new_status="claimed",
            status_reason="",
            user_role="write",
        )
        assert ok is True

    def test_open_to_claimed_works_under_relaxed_mode(self):
        """The relaxed dispatch must NOT clobber the atomic-claim path.
        open->claimed must still be accepted whether or not the caller
        passed a reason (the atomicity is enforced higher up at the
        repo/db CAS layer; this just confirms the validator doesn't
        block it)."""
        from pyrite.models.task import validate_status_change

        ok, _ = validate_status_change(
            self._relaxed_workflow(),
            old_status="open",
            new_status="claimed",
            status_reason="picked up by agent",
            user_role="write",
        )
        assert ok is True


# =========================================================================
# Per-entity-type workflow resolver — Tier A r1175 fire 3/4
# =========================================================================


class TestResolveWorkflowForType:
    """`resolve_workflow_for_type(entry_type, kb_schema)` returns the
    workflow dict the dispatcher should validate against:

      - For the core `task` type or any type with no `state_machine`
        block on its TypeSchema: TASK_WORKFLOW (back-compat).
      - For a type whose TypeSchema carries a `state_machine` dict
        (e.g., a plugin's `sw_ticket` that opts into relaxed mode):
        the type's own dict — letting plugins override
        enforce_transitions / require_reason_on_transition / states /
        transitions independently.

    Per-type config is the core of Reading C from the locked design.
    """

    def test_task_type_falls_back_to_TASK_WORKFLOW(self):
        from pyrite.models.task import TASK_WORKFLOW, resolve_workflow_for_type

        # kb_schema can be None — the resolver still gives back the
        # core workflow for the `task` type.
        result = resolve_workflow_for_type("task", None)
        assert result is TASK_WORKFLOW

    def test_unknown_type_with_no_schema_returns_task_workflow(self):
        """If we don't have a schema and the type isn't `task`, return
        TASK_WORKFLOW as the safe default — the dispatcher won't
        actually fire because _task_validate_transition filters on
        entry_type, but the resolver shouldn't crash on None."""
        from pyrite.models.task import TASK_WORKFLOW, resolve_workflow_for_type

        assert resolve_workflow_for_type("sw_ticket", None) is TASK_WORKFLOW

    def test_type_schema_state_machine_overrides_task_workflow(self):
        """The locked design: a plugin's entry-type schema can carry
        its own `state_machine` block. When present, the resolver
        returns it instead of TASK_WORKFLOW."""
        from pyrite.models.task import resolve_workflow_for_type
        from pyrite.schema import KBSchema, TypeSchema

        custom_workflow = {
            "states": ["open", "claimed", "in_progress", "blocked", "done"],
            "initial": "open",
            "field": "status",
            "transitions": [],
            "enforce_transitions": False,
            "require_reason_on_transition": True,
        }
        schema = KBSchema(
            name="test",
            kb_type="generic",
            types={
                "sw_ticket": TypeSchema(
                    name="sw_ticket",
                    state_machine=custom_workflow,
                ),
            },
        )

        result = resolve_workflow_for_type("sw_ticket", schema)
        assert result is custom_workflow
        # And the relaxed flags survived round-trip:
        assert result["enforce_transitions"] is False
        assert result["require_reason_on_transition"] is True

    def test_type_without_state_machine_falls_back(self):
        """A TypeSchema with NO state_machine block: fall back to
        TASK_WORKFLOW. This is the case for every existing plugin
        until it opts in."""
        from pyrite.models.task import TASK_WORKFLOW, resolve_workflow_for_type
        from pyrite.schema import KBSchema, TypeSchema

        schema = KBSchema(
            name="test",
            kb_type="generic",
            types={"sw_ticket": TypeSchema(name="sw_ticket")},
        )
        # No state_machine declared -> back-compat fallback
        assert resolve_workflow_for_type("sw_ticket", schema) is TASK_WORKFLOW


# =========================================================================
# Validators
# =========================================================================


class TestValidators:
    def test_valid_task(self):
        errors = validate_task("task", {"status": "open", "priority": 5}, {})
        assert errors == []

    def test_invalid_status(self):
        errors = validate_task("task", {"status": "invalid"}, {})
        assert any(e["field"] == "status" for e in errors)

    def test_invalid_priority_high(self):
        errors = validate_task("task", {"priority": 11}, {})
        assert any(e["field"] == "priority" for e in errors)

    def test_invalid_priority_low(self):
        errors = validate_task("task", {"priority": 0}, {})
        assert any(e["field"] == "priority" for e in errors)

    def test_non_task_ignored(self):
        errors = validate_task("note", {"status": "invalid"}, {})
        assert errors == []

    def test_validates_any_task_entry(self):
        errors = validate_task("task", {"status": "todo"}, {})
        assert any(e["field"] == "status" for e in errors)

    def test_parent_field_validated(self):
        errors = validate_task("task", {"parent": 123}, {})
        assert any(e["field"] == "parent" for e in errors)


# =========================================================================
# Hooks
# =========================================================================


class TestHooks:
    def test_before_save_validates_transition_with_old_status(self):
        from unittest.mock import MagicMock

        from pyrite.plugins.context import PluginContext
        from pyrite.services.task_service import _task_validate_transition

        entry = TaskEntry(id="t1", title="Test", status="done")
        ctx = PluginContext(
            config=MagicMock(),
            db=MagicMock(),
            kb_name="test",
            operation="update",
            kb_type="task",
            extra={"old_status": "open"},
        )
        # open → done is not a valid transition — raises a typed, helpful error
        from pyrite.exceptions import ValidationError

        with pytest.raises(ValidationError, match="Cannot move task from 'open' to 'done'"):
            _task_validate_transition(entry, ctx)

    def test_before_save_allows_valid_transition(self):
        from unittest.mock import MagicMock

        from pyrite.plugins.context import PluginContext
        from pyrite.services.task_service import _task_validate_transition

        entry = TaskEntry(id="t1", title="Test", status="claimed")
        ctx = PluginContext(
            config=MagicMock(),
            db=MagicMock(),
            kb_name="test",
            operation="update",
            kb_type="task",
            extra={"old_status": "open"},
        )
        result = _task_validate_transition(entry, ctx)
        assert result.status == "claimed"

    def test_before_save_ignores_non_update(self):
        from unittest.mock import MagicMock

        from pyrite.plugins.context import PluginContext
        from pyrite.services.task_service import _task_validate_transition

        entry = TaskEntry(id="t1", title="Test", status="done")
        ctx = PluginContext(
            config=MagicMock(),
            db=MagicMock(),
            kb_name="test",
            operation="create",
            kb_type="task",
            extra={"old_status": "open"},
        )
        result = _task_validate_transition(entry, ctx)
        assert result.status == "done"


# =========================================================================
# MCP tool schemas
# =========================================================================


class TestMCPToolSchemas:
    def test_task_list_in_read_tools(self):
        assert "task_list" in READ_TOOLS
        assert "task_status" in READ_TOOLS

    def test_task_write_tools_present(self):
        assert "task_create" in WRITE_TOOLS
        assert "task_update" in WRITE_TOOLS
        assert "task_claim" in WRITE_TOOLS
        assert "task_decompose" in WRITE_TOOLS
        assert "task_checkpoint" in WRITE_TOOLS

    def test_task_create_requires_kb_name_and_title(self):
        schema = WRITE_TOOLS["task_create"]["inputSchema"]
        assert "kb_name" in schema["required"]
        assert "title" in schema["required"]

    def test_task_create_schema_includes_optional_tags(self):
        schema = WRITE_TOOLS["task_create"]["inputSchema"]
        tags = schema["properties"]["tags"]
        assert tags["type"] == "array"
        assert tags["items"] == {"type": "string"}
        assert "tags" not in schema["required"]

    def test_task_claim_requires_all_fields(self):
        schema = WRITE_TOOLS["task_claim"]["inputSchema"]
        assert set(schema["required"]) == {"task_id", "kb_name", "assignee"}

    def test_task_decompose_schema(self):
        schema = WRITE_TOOLS["task_decompose"]["inputSchema"]
        assert "parent_id" in schema["required"]
        assert "children" in schema["required"]


# =========================================================================
# Preset
# =========================================================================


class TestPreset:
    def test_preset_structure(self):
        p = TASK_KB_PRESET
        assert p["name"] == "task-board"
        assert "task" in p["types"]
        assert p["policies"]["enforce_workflow"] is True
        assert p["validation"]["enforce"] is True

    def test_preset_task_type(self):
        task_type = TASK_KB_PRESET["types"]["task"]
        assert "title" in task_type["required"]
        assert "status" in task_type["optional"]
        assert "priority" in task_type["optional"]
        assert task_type["subdirectory"] == "tasks/"

    def test_preset_directories(self):
        assert "tasks" in TASK_KB_PRESET["directories"]


# =========================================================================
# Enums
# =========================================================================


class TestEnums:
    def test_task_statuses(self):
        assert "open" in TASK_STATUSES
        assert "claimed" in TASK_STATUSES
        assert "in_progress" in TASK_STATUSES
        assert "blocked" in TASK_STATUSES
        assert "review" in TASK_STATUSES
        assert "done" in TASK_STATUSES
        assert "failed" in TASK_STATUSES
        assert "cancelled" in TASK_STATUSES
        assert len(TASK_STATUSES) == 8


# =========================================================================
# MCP task_create tags
# =========================================================================


class TestMCPTaskCreateTags:
    """Acceptance tests for issue #319: MCP task_create forwards tags."""

    def _server(self, tmp_path):
        from pyrite.config import KBConfig, PyriteConfig, Settings
        from pyrite.server.mcp_server import PyriteMCPServer
        from pyrite.storage.database import PyriteDB

        tasks_path = tmp_path / "tasks-kb"
        (tasks_path / "tasks").mkdir(parents=True)
        kb_config = KBConfig(
            name="test-tasks",
            path=tasks_path,
            kb_type="task",
            description="Test task KB",
        )
        config = PyriteConfig(
            knowledge_bases=[kb_config],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db = PyriteDB(config.settings.index_path)
        db.register_kb(
            name="test-tasks",
            kb_type="task",
            path=str(tasks_path),
            description="Test task KB",
        )
        db.close()
        server = PyriteMCPServer(config=config, tier="write")
        return server, kb_config

    def test_task_create_handler_writes_tags_frontmatter(self, tmp_path):
        from pyrite.storage.repository import KBRepository

        server, kb_config = self._server(tmp_path)
        result = server._task_create(
            {
                "kb_name": "test-tasks",
                "title": "Tagged via MCP",
                "tags": ["alpha", "beta"],
            }
        )
        assert result.get("created") is True
        entry = KBRepository(kb_config).load(result["entry_id"])
        assert entry.tags == ["alpha", "beta"]

    def test_task_create_handler_omits_tags_when_absent(self, tmp_path):
        from pyrite.storage.repository import KBRepository

        server, kb_config = self._server(tmp_path)
        result = server._task_create(
            {
                "kb_name": "test-tasks",
                "title": "Untagged via MCP",
            }
        )
        assert result.get("created") is True
        entry = KBRepository(kb_config).load(result["entry_id"])
        assert not entry.tags
        frontmatter = entry.to_frontmatter()
        assert "tags" not in frontmatter or not frontmatter.get("tags")
