"""Tests for Definition of Ready / Definition of Done quality gates."""

import json

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.plugins.registry import get_registry
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from pyrite.services.access_policy import UNSCOPED


@pytest.fixture
def gate_setup(tmp_path, monkeypatch):
    """Set up a software KB with gate config for testing."""
    kb_dir = tmp_path / "sw-kb"
    kb_dir.mkdir()

    db_path = tmp_path / "index.db"
    kb_config = KBConfig(
        name="test-sw",
        path=kb_dir,
        kb_type="software",
        description="Test software KB",
    )
    config = PyriteConfig(
        knowledge_bases=[kb_config],
        settings=Settings(index_path=db_path),
    )
    db = PyriteDB(db_path)
    svc = KBService(config, db)

    from pyrite.plugins.context import PluginContext
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    plugin = SoftwareKBPlugin()
    ctx = PluginContext(config=config, db=db)
    plugin.set_context(ctx)

    registry = get_registry()
    registry.register(plugin)

    # Monkeypatch load_config so plugin internals find our test KB
    monkeypatch.setattr("pyrite.config.load_config", lambda: config)

    yield {
        "config": config,
        "db": db,
        "svc": svc,
        "plugin": plugin,
        "kb_dir": kb_dir,
    }
    db.close()


def _insert_entry(db, entry_id, kb_name, entry_type, title, status="", metadata=None, kb_dir=None):
    """Insert a minimal entry row and optionally create the markdown file."""
    meta_json = json.dumps(metadata) if metadata else None
    conn = db._raw_conn
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO entry (id, kb_name, entry_type, title, status, metadata, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        [entry_id, kb_name, entry_type, title, status, meta_json],
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    # Create markdown file so CAS claim_entry can update it
    if kb_dir is not None:
        frontmatter = {"id": entry_id, "title": title, "type": entry_type}
        if status:
            frontmatter["status"] = status
        if metadata:
            for k, v in metadata.items():
                if k != "status":
                    frontmatter[k] = v

        import yaml

        md_path = kb_dir / f"{entry_id}.md"
        md_path.write_text(
            "---\n" + yaml.dump(frontmatter, default_flow_style=False) + "---\n\nContent.\n"
        )


# -- Board config helpers --

BOARD_WITH_WARN_GATE = {
    "lanes": [
        {"name": "Backlog", "statuses": ["proposed", "accepted"]},
        {"name": "In Progress", "statuses": ["in_progress"]},
        {"name": "Done", "statuses": ["done"]},
    ],
    "wip_policy": "warn",
    "gates": {
        "in_progress": {
            "name": "Definition of Ready",
            "policy": "warn",
            "criteria": [
                {"text": "Problem statement is clear", "type": "judgment"},
                {"text": "Effort estimated", "checker": "has_field", "params": {"field": "effort"}},
                {"text": "Not oversized", "checker": "not_oversized"},
                {"text": "No blockers", "checker": "no_open_blockers"},
            ],
        },
        "done": {
            "name": "Definition of Done",
            "policy": "warn",
            "criteria": [
                {"text": "Tests passing", "type": "agent_responsibility"},
                {"text": "KB docs updated", "type": "judgment"},
            ],
        },
    },
}

BOARD_WITH_ENFORCE_GATE = {
    **BOARD_WITH_WARN_GATE,
    "gates": {
        "in_progress": {
            **BOARD_WITH_WARN_GATE["gates"]["in_progress"],
            "policy": "enforce",
        },
        "done": BOARD_WITH_WARN_GATE["gates"]["done"],
    },
}

BOARD_NO_GATES = {
    "lanes": BOARD_WITH_WARN_GATE["lanes"],
    "wip_policy": "warn",
}


# -- _evaluate_gate unit tests --


def test_gate_evaluation_dor_all_pass(gate_setup):
    """Item with effort, no blockers → gate passes."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"effort": "M", "kind": "feature", "status": "accepted"}
    _insert_entry(
        db, "item-1", "test-sw", "backlog_item", "Fix it", status="accepted", metadata=meta
    )
    row = db._raw_conn.execute("SELECT * FROM entry WHERE id = 'item-1'").fetchone()

    result = plugin._evaluate_gate(
        db, BOARD_WITH_WARN_GATE, "in_progress", row, meta, readable_kbs=UNSCOPED
    )
    assert result is not None
    assert result["passed"] is True
    assert result["gate_name"] == "Definition of Ready"
    assert len(result["criteria"]) == 4


def test_gate_evaluation_dor_missing_effort(gate_setup):
    """No effort field → checker fails, gate fails."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "accepted"}
    _insert_entry(
        db, "item-2", "test-sw", "backlog_item", "Fix it", status="accepted", metadata=meta
    )
    row = db._raw_conn.execute("SELECT * FROM entry WHERE id = 'item-2'").fetchone()

    result = plugin._evaluate_gate(
        db, BOARD_WITH_WARN_GATE, "in_progress", row, meta, readable_kbs=UNSCOPED
    )
    assert result is not None
    assert result["passed"] is False
    # Find the effort criterion
    effort_crit = [c for c in result["criteria"] if "Effort" in c["text"]][0]
    assert effort_crit["passed"] is False
    assert "effort" in effort_crit.get("message", "").lower()


def test_gate_evaluation_dor_oversized(gate_setup):
    """Effort=XL → not_oversized fails."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"effort": "XL", "kind": "feature", "status": "accepted"}
    _insert_entry(
        db, "item-3", "test-sw", "backlog_item", "Big task", status="accepted", metadata=meta
    )
    row = db._raw_conn.execute("SELECT * FROM entry WHERE id = 'item-3'").fetchone()

    result = plugin._evaluate_gate(
        db, BOARD_WITH_WARN_GATE, "in_progress", row, meta, readable_kbs=UNSCOPED
    )
    assert result["passed"] is False
    oversized_crit = [c for c in result["criteria"] if "oversized" in c["text"].lower()][0]
    assert oversized_crit["passed"] is False
    assert "XL" in oversized_crit.get("message", "")


def test_gate_evaluation_dor_blocked(gate_setup):
    """Item with unresolved dependency → no_open_blockers fails."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    # Create blocker item (not done)
    _insert_entry(db, "blocker-1", "test-sw", "backlog_item", "Blocker", status="in_progress")

    # Create item with blocked_by link
    meta = {"effort": "M", "kind": "feature", "status": "accepted"}
    _insert_entry(
        db, "item-4", "test-sw", "backlog_item", "Blocked item", status="accepted", metadata=meta
    )

    # Add blocked_by link
    conn = db._raw_conn
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO link (source_id, source_kb, target_id, target_kb, relation, inverse_relation) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ["item-4", "test-sw", "blocker-1", "test-sw", "blocked_by", "blocks"],
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")

    row = db._raw_conn.execute("SELECT * FROM entry WHERE id = 'item-4'").fetchone()
    result = plugin._evaluate_gate(
        db, BOARD_WITH_WARN_GATE, "in_progress", row, meta, readable_kbs=UNSCOPED
    )
    assert result["passed"] is False
    blocker_crit = [c for c in result["criteria"] if "blocker" in c["text"].lower()][0]
    assert blocker_crit["passed"] is False


def test_gate_judgment_items_always_pass(gate_setup):
    """Judgment criteria don't affect gate.passed."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    board_config = {
        "gates": {
            "in_progress": {
                "name": "DoR",
                "policy": "warn",
                "criteria": [
                    {"text": "Problem clear", "type": "judgment"},
                    {"text": "Agent checks", "type": "agent_responsibility"},
                ],
            }
        }
    }
    meta = {"status": "accepted"}
    _insert_entry(db, "item-5", "test-sw", "backlog_item", "Task", status="accepted", metadata=meta)
    row = db._raw_conn.execute("SELECT * FROM entry WHERE id = 'item-5'").fetchone()

    result = plugin._evaluate_gate(
        db, board_config, "in_progress", row, meta, readable_kbs=UNSCOPED
    )
    assert result["passed"] is True
    assert all(c["passed"] for c in result["criteria"])


def test_gate_warn_policy_allows_transition(gate_setup):
    """Warn policy: transition succeeds even with gate failures in response."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    # Item missing effort → gate fails, but warn policy allows transition
    meta = {"kind": "feature", "status": "accepted"}
    _insert_entry(
        db,
        "item-6",
        "test-sw",
        "backlog_item",
        "No effort",
        status="accepted",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    # Write board.yaml with warn policy
    board_yaml = gate_setup["kb_dir"] / "board.yaml"
    import yaml

    yaml.dump(BOARD_WITH_WARN_GATE, board_yaml.open("w"))

    result = plugin._mcp_transition(
        {
            "item_id": "item-6",
            "kb_name": "test-sw",
            "to_status": "in_progress",
        }
    )
    assert result["transitioned"] is True
    assert "gate" in result
    assert result["gate"]["passed"] is False


def test_gate_enforce_policy_blocks_transition(gate_setup):
    """Enforce policy: transition fails when gate fails."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "accepted"}
    _insert_entry(
        db,
        "item-7",
        "test-sw",
        "backlog_item",
        "No effort",
        status="accepted",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    board_yaml = gate_setup["kb_dir"] / "board.yaml"
    import yaml

    yaml.dump(BOARD_WITH_ENFORCE_GATE, board_yaml.open("w"))

    result = plugin._mcp_transition(
        {
            "item_id": "item-7",
            "kb_name": "test-sw",
            "to_status": "in_progress",
        }
    )
    assert result["transitioned"] is False
    assert "gate" in result
    assert result["gate"]["passed"] is False
    assert "Gate check failed" in result["error"]


def test_gate_no_gate_defined(gate_setup):
    """Transition to status without gate config → no gate in response."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "accepted"}
    _insert_entry(
        db,
        "item-8",
        "test-sw",
        "backlog_item",
        "Task",
        status="accepted",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    board_yaml = gate_setup["kb_dir"] / "board.yaml"
    import yaml

    yaml.dump(BOARD_NO_GATES, board_yaml.open("w"))

    result = plugin._mcp_transition(
        {
            "item_id": "item-8",
            "kb_name": "test-sw",
            "to_status": "in_progress",
        }
    )
    assert result["transitioned"] is True
    assert "gate" not in result


def test_gate_dod_on_done_transition(gate_setup):
    """in_progress→done evaluates DoD gate."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "in_progress", "effort": "S"}
    _insert_entry(
        db,
        "item-9",
        "test-sw",
        "backlog_item",
        "Done task",
        status="in_progress",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    board_yaml = gate_setup["kb_dir"] / "board.yaml"
    import yaml

    yaml.dump(BOARD_WITH_WARN_GATE, board_yaml.open("w"))

    result = plugin._mcp_transition(
        {
            "item_id": "item-9",
            "kb_name": "test-sw",
            "to_status": "done",
        }
    )
    assert result["transitioned"] is True
    assert "gate" in result
    assert result["gate"]["gate_name"] == "Definition of Done"
    # DoD has only judgment/agent_responsibility items, so it always passes
    assert result["gate"]["passed"] is True


def test_claim_evaluates_dor(gate_setup):
    """sw_claim returns gate results."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "accepted", "effort": "M"}
    _insert_entry(
        db,
        "item-10",
        "test-sw",
        "backlog_item",
        "Claimable",
        status="accepted",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    board_yaml = gate_setup["kb_dir"] / "board.yaml"
    import yaml

    yaml.dump(BOARD_WITH_WARN_GATE, board_yaml.open("w"))

    result = plugin._mcp_claim(
        {
            "item_id": "item-10",
            "kb_name": "test-sw",
            "assignee": "agent-1",
        }
    )
    assert result.get("claimed") is True
    assert "gate" in result
    assert result["gate"]["gate_name"] == "Definition of Ready"


# -- not_oversized checker unit tests --


def test_review_approved_evaluates_dod(gate_setup):
    """sw_review with outcome=approved evaluates DoD gate."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "review", "effort": "S"}
    _insert_entry(
        db,
        "item-review-dod",
        "test-sw",
        "backlog_item",
        "Review me",
        status="review",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    board_yaml = gate_setup["kb_dir"] / "board.yaml"
    import yaml

    yaml.dump(BOARD_WITH_WARN_GATE, board_yaml.open("w"))

    result = plugin._mcp_review(
        {
            "item_id": "item-review-dod",
            "kb_name": "test-sw",
            "outcome": "approved",
            "reviewer": "reviewer-1",
            "feedback": "LGTM",
        }
    )
    assert result["reviewed"] is True
    assert result["new_status"] == "done"
    assert "gate" in result
    assert result["gate"]["gate_name"] == "Definition of Done"
    assert result["gate"]["passed"] is True


def test_review_changes_requested_no_dod(gate_setup):
    """sw_review with outcome=changes_requested does not evaluate DoD."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "review", "effort": "S"}
    _insert_entry(
        db,
        "item-review-cr",
        "test-sw",
        "backlog_item",
        "Needs work",
        status="review",
        metadata=meta,
        kb_dir=gate_setup["kb_dir"],
    )

    result = plugin._mcp_review(
        {
            "item_id": "item-review-cr",
            "kb_name": "test-sw",
            "outcome": "changes_requested",
            "reviewer": "reviewer-1",
            "feedback": "Fix the tests",
        }
    )
    assert result["reviewed"] is True
    assert result["new_status"] == "in_progress"
    assert "gate" not in result


def test_not_oversized_checker_passes():
    """not_oversized passes for M effort."""
    from pyrite.services.rubric_checkers import check_not_oversized

    entry = {"id": "t1", "kb_name": "kb", "metadata": {"effort": "M"}}
    assert check_not_oversized(entry, None) is None


def test_not_oversized_checker_fails_xl():
    """not_oversized fails for XL effort."""
    from pyrite.services.rubric_checkers import check_not_oversized

    entry = {"id": "t1", "kb_name": "kb", "metadata": {"effort": "XL"}}
    result = check_not_oversized(entry, None)
    assert result is not None
    assert "XL" in result["message"]


def test_not_oversized_checker_no_effort():
    """not_oversized passes when no effort set."""
    from pyrite.services.rubric_checkers import check_not_oversized

    entry = {"id": "t1", "kb_name": "kb", "metadata": {}}
    assert check_not_oversized(entry, None) is None


# -- sw_check_ready tests --


def test_check_ready_passes_when_dor_met(gate_setup):
    """Item with effort, no blockers → ready: True."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"effort": "M", "kind": "feature", "status": "accepted"}
    _insert_entry(
        db, "ready-1", "test-sw", "backlog_item", "Ready item", status="accepted", metadata=meta
    )

    result = plugin._mcp_check_ready({"item_id": "ready-1", "kb_name": "test-sw"})
    assert result["ready"] is True
    assert result["item_id"] == "ready-1"
    assert result["title"] == "Ready item"
    assert result["gate"] is not None
    assert result["gate"]["passed"] is True


def test_check_ready_fails_missing_effort(gate_setup):
    """Item without effort → ready: False, criteria show failure."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    meta = {"kind": "feature", "status": "accepted"}
    _insert_entry(
        db, "unready-1", "test-sw", "backlog_item", "Unready item", status="accepted", metadata=meta
    )

    result = plugin._mcp_check_ready({"item_id": "unready-1", "kb_name": "test-sw"})
    assert result["ready"] is False
    assert result["gate"]["passed"] is False
    effort_crit = [c for c in result["gate"]["criteria"] if "Effort" in c["text"]][0]
    assert effort_crit["passed"] is False


def test_check_ready_item_not_found(gate_setup):
    """Nonexistent item → error response."""
    plugin = gate_setup["plugin"]
    result = plugin._mcp_check_ready({"item_id": "nope", "kb_name": "test-sw"})
    assert "error" in result


def test_check_ready_in_read_tier():
    """sw_check_ready is registered in the read tier."""
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    plugin = SoftwareKBPlugin()
    tools = plugin.get_mcp_tools("read")
    assert "sw_check_ready" in tools


# -- sw_refine tests --


def test_refine_sorts_by_priority(gate_setup):
    """Items sorted by priority: critical before low."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    _insert_entry(
        db,
        "low-1",
        "test-sw",
        "backlog_item",
        "Low item",
        status="accepted",
        metadata={"effort": "S", "kind": "feature", "status": "accepted", "priority": "low"},
    )
    _insert_entry(
        db,
        "crit-1",
        "test-sw",
        "backlog_item",
        "Critical item",
        status="accepted",
        metadata={"effort": "M", "kind": "bug", "status": "accepted", "priority": "critical"},
    )

    result = plugin._mcp_refine({"kb_name": "test-sw"})
    ids = [item["id"] for item in result["items"]]
    assert ids.index("crit-1") < ids.index("low-1")


def test_refine_filters_by_status(gate_setup):
    """Only items matching status filter are returned."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    _insert_entry(
        db,
        "prop-1",
        "test-sw",
        "backlog_item",
        "Proposed",
        status="proposed",
        metadata={"effort": "S", "kind": "feature", "status": "proposed"},
    )
    _insert_entry(
        db,
        "acc-1",
        "test-sw",
        "backlog_item",
        "Accepted",
        status="accepted",
        metadata={"effort": "S", "kind": "feature", "status": "accepted"},
    )

    result = plugin._mcp_refine({"kb_name": "test-sw", "status": "proposed"})
    statuses = {item["status"] for item in result["items"]}
    assert statuses == {"proposed"}


def test_refine_summary_counts(gate_setup):
    """summary.ready + summary.not_ready == summary.total."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    _insert_entry(
        db,
        "r-1",
        "test-sw",
        "backlog_item",
        "Ready",
        status="accepted",
        metadata={"effort": "M", "kind": "feature", "status": "accepted"},
    )
    _insert_entry(
        db,
        "nr-1",
        "test-sw",
        "backlog_item",
        "Not ready",
        status="accepted",
        metadata={"kind": "feature", "status": "accepted"},
    )

    result = plugin._mcp_refine({"kb_name": "test-sw"})
    s = result["summary"]
    assert s["ready"] + s["not_ready"] == s["total"]
    assert s["not_ready"] >= 1
    assert s["ready"] >= 1


def test_refine_excludes_in_progress(gate_setup):
    """Items in in_progress/done/review are not included."""
    plugin = gate_setup["plugin"]
    db = gate_setup["db"]

    _insert_entry(
        db,
        "wip-1",
        "test-sw",
        "backlog_item",
        "In progress item",
        status="in_progress",
        metadata={"effort": "M", "kind": "feature", "status": "in_progress"},
    )

    result = plugin._mcp_refine({"kb_name": "test-sw"})
    ids = [item["id"] for item in result["items"]]
    assert "wip-1" not in ids


def test_refine_in_read_tier():
    """sw_refine is registered in the read tier."""
    from pyrite_software_kb.plugin import SoftwareKBPlugin

    plugin = SoftwareKBPlugin()
    tools = plugin.get_mcp_tools("read")
    assert "sw_refine" in tools
