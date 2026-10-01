"""Schemas Pyrite ships never refuse the workflows of the types they declare (#555 round 1).

`validation.rules[].enum` was ignored until #555 made it real, on by default.
A rule enum is not scoped to a type -- it applies to every entry that has the
field -- so a shipped schema whose `status` rule lists one type's states
refuses every other type's. `docs/desk-schema.yaml` listed
`[open, in_progress, blocked, done, dropped]`, so on a desk built the way
`docs/conductor-desk.md` says, claiming a task (open -> claimed) was refused.
The `software` init template and the software-kb preset listed the ADR
statuses only, so a backlog item could not move to `in_progress`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.models.task import TASK_STATUSES
from pyrite.services.access_policy import UNSCOPED
from pyrite.storage.database import PyriteDB
from pyrite.utils.yaml import load_yaml, load_yaml_file

REPO = Path(__file__).resolve().parents[1]
DESK_SCHEMA = REPO / "docs" / "desk-schema.yaml"
runner = CliRunner()


# --------------------------------------------------------------------------
# (b) the desk, built as docs/conductor-desk.md says, runs the task workflow
# --------------------------------------------------------------------------


@pytest.fixture
def desk(tmp_path):
    """`pyrite init -t empty -p <dir> -n <name> --schema-file docs/desk-schema.yaml
    --no-examples`, with the config writes kept in tmp_path."""
    desk_path = tmp_path / "desk"
    config = PyriteConfig(
        knowledge_bases=[], settings=Settings(index_path=tmp_path / "index.db", auto_embed=False)
    )
    saved: list[PyriteConfig] = []

    def _save(cfg):
        saved.append(cfg)

    with (
        patch("pyrite.config.load_config", side_effect=lambda: saved[-1] if saved else config),
        patch("pyrite.config.save_config", side_effect=_save),
    ):
        result = runner.invoke(
            app,
            [
                "init",
                "-t",
                "empty",
                "-p",
                str(desk_path),
                "-n",
                "proj-desk",
                "--schema-file",
                str(DESK_SCHEMA),
                "--no-examples",
            ],
        )
    assert result.exit_code == 0, result.output
    assert (desk_path / "kb.yaml").exists()
    cfg = PyriteConfig(
        knowledge_bases=[KBConfig(name="proj-desk", path=desk_path, kb_type="desk")],
        settings=config.settings,
    )
    db = PyriteDB(cfg.settings.index_path)
    yield cfg, db
    db.close()


def test_the_desk_created_by_init_runs_the_whole_task_workflow(desk):
    from pyrite.services.task_service import TaskService

    config, db = desk
    svc = TaskService(config, db)
    created = svc.create_task(
        "proj-desk", "Merge #184", fields={"project": "proj", "kind": "merge"}
    )
    tid = created["entry_id"]
    svc.claim_task(tid, "proj-desk", "agent:conductor")
    for status in ("in_progress", "blocked", "in_progress", "review", "done"):
        svc.update_task(tid, "proj-desk", status=status)
    assert svc.get_task(tid, "proj-desk", readable_kbs=UNSCOPED)["status"] == "done"


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_the_desk_accepts_every_closing_state(desk, terminal):
    from pyrite.services.task_service import TaskService

    config, db = desk
    svc = TaskService(config, db)
    tid = svc.create_task(
        "proj-desk", f"Close as {terminal}", fields={"project": "proj", "kind": "decide"}
    )["entry_id"]
    svc.claim_task(tid, "proj-desk", "agent:conductor")
    svc.update_task(tid, "proj-desk", status="in_progress")
    svc.update_task(tid, "proj-desk", status=terminal)
    assert svc.get_task(tid, "proj-desk", readable_kbs=UNSCOPED)["status"] == terminal


# --------------------------------------------------------------------------
# (c) every shipped schema's rule enums cover the vocabularies of its types
# --------------------------------------------------------------------------


def _vocabularies() -> dict[tuple[str, str], tuple[str, ...]]:
    """The values each (type, field) is defined to take, from the code that owns them."""
    from pyrite.schema.enums import EventStatus, ResearchStatus

    research = tuple(s.value for s in ResearchStatus)
    vocab: dict[tuple[str, str], tuple[str, ...]] = {
        ("task", "status"): TASK_STATUSES,
        # Core types whose frontmatter carries these by default (an event is
        # `status: confirmed`, a person `research_status: stub`).
        ("event", "status"): tuple(s.value for s in EventStatus),
        ("person", "research_status"): research,
        ("organization", "research_status"): research,
    }
    try:
        from pyrite_software_kb import entry_types as sw

        vocab.update(
            {
                ("adr", "status"): sw.ADR_STATUSES,
                ("design_doc", "status"): sw.DESIGN_DOC_STATUSES,
                ("backlog_item", "status"): sw.BACKLOG_STATUSES,
                ("backlog_item", "kind"): sw.BACKLOG_KINDS,
                ("backlog_item", "priority"): sw.BACKLOG_PRIORITIES,
                ("component", "kind"): sw.COMPONENT_KINDS,
                ("milestone", "status"): sw.MILESTONE_STATUSES,
                ("runbook", "runbook_kind"): sw.RUNBOOK_KINDS,
            }
        )
    except ImportError:
        pass
    try:
        from pyrite_encyclopedia.workflows import ARTICLE_REVIEW_WORKFLOW

        vocab[("article", "review_status")] = tuple(ARTICLE_REVIEW_WORKFLOW["states"])
    except ImportError:
        pass
    return vocab


def _shipped_schemas() -> list[tuple[str, dict]]:
    from pyrite.cli.init_command import BUILTIN_TEMPLATES
    from pyrite.plugins import get_registry

    schemas = [
        (f"docs/{p.name}", load_yaml_file(p)) for p in sorted((REPO / "docs").glob("*.yaml"))
    ]
    schemas += [(f"init template {k!r}", v) for k, v in BUILTIN_TEMPLATES.items()]
    schemas += [(f"plugin preset {k!r}", v) for k, v in get_registry().get_all_kb_presets().items()]
    return [(name, s) for name, s in schemas if isinstance(s, dict) and "types" in s]


def _conflicts() -> list[str]:
    vocab = _vocabularies()
    out = []
    for name, schema in _shipped_schemas():
        types = schema.get("types") or {}
        for rule in (schema.get("validation") or {}).get("rules") or []:
            if not isinstance(rule, dict) or "enum" not in rule:
                continue
            field, allowed = rule.get("field"), set(rule["enum"])
            for type_name in types:
                missing = set(vocab.get((type_name, field), ())) - allowed
                if missing:
                    out.append(
                        f"{name}: rule enum on {field!r} refuses {type_name}'s {sorted(missing)}"
                    )
    return out


def test_the_guard_sees_the_shipped_schemas():
    names = [name for name, _ in _shipped_schemas()]
    assert "docs/desk-schema.yaml" in names
    assert "init template 'software'" in names


def test_no_shipped_rule_enum_refuses_a_declared_types_own_values():
    assert _conflicts() == []


def test_the_guard_catches_a_status_rule_missing_a_task_state(tmp_path):
    """The guard is live: a desk schema without `claimed` is reported."""
    text = DESK_SCHEMA.read_text()
    broken = load_yaml(text)
    for rule in broken["validation"]["rules"]:
        if rule["field"] == "status":
            rule["enum"] = [s for s in rule["enum"] if s != "claimed"]
    with patch(f"{__name__}._shipped_schemas", return_value=[("broken desk", broken)]):
        assert any("claimed" in c for c in _conflicts())


def test_an_event_can_be_created_in_a_movement_kb(tmp_path):
    """The `movement` template's `status` enum is `practice`'s; as a rule it
    refused every event, whose frontmatter is `status: confirmed`."""
    from pyrite.cli.init_command import BUILTIN_TEMPLATES
    from pyrite.services.kb_service import KBService
    from pyrite.utils.yaml import dump_yaml_file

    kb = tmp_path / "mv"
    kb.mkdir()
    template = {k: v for k, v in BUILTIN_TEMPLATES["movement"].items() if k != "directories"}
    dump_yaml_file(template, kb / "kb.yaml")
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="mv", path=kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "i.db", auto_embed=False),
    )
    db = PyriteDB(config.settings.index_path)
    try:
        svc = KBService(config, db)
        svc.create("mv", {"entry_type": "event", "title": "March", "date": "2026-01-01"})
        svc.create("mv", {"entry_type": "practice", "title": "Sit-in", "status": "active"})
        with pytest.raises(Exception, match="status"):
            svc.create("mv", {"entry_type": "practice", "title": "Bad", "status": "bogus"})
    finally:
        db.close()
