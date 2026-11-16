"""Software tools report creation only after the file and index exist."""

import copy
import datetime
import re

import pytest
import yaml

from pyrite.config import KBConfig
from pyrite_software_kb.preset import SOFTWARE_KB_PRESET
from tests._world import build_world


def make_world(tmp_path, mode="preset", *, read_only=False):
    root = tmp_path / "kb"
    root.mkdir()
    schema = copy.deepcopy(SOFTWARE_KB_PRESET)
    if mode == "declared":
        schema["types"]["work_log"] = {"required": ["title"], "subdirectory": "work-logs/"}
    elif mode == "legacy":
        schema["types"].pop("work_log", None)
    if mode != "unrestricted":
        (root / "kb.yaml").write_text(yaml.safe_dump(schema))
    world = build_world(
        tmp_path,
        kbs=[KBConfig(name="project", path=root, kb_type="software", read_only=read_only)],
        rest=False,
    )
    world.add_entry(
        "project",
        "---\nid: item\ntype: backlog_item\ntitle: Fix it\nkind: bug\n---\n\nWork.\n",
        filename="backlog/item.md",
    )
    return world


@pytest.mark.parametrize("mode", ["preset", "declared", "unrestricted"])
@pytest.mark.parametrize("surface", ["mcp", "cli"])
def test_work_log_is_saved_and_appears_in_context(tmp_path, mode, surface):
    world = make_world(tmp_path, mode)
    try:
        server = world.mcp()
        if surface == "mcp":
            result = server._dispatch_tool(
                "sw_log",
                {
                    "kb_name": "project",
                    "item_id": "item",
                    "summary": "Fixed rounding",
                    "decisions": "Use Decimal",
                },
            )
            assert result["created"] is True, result
            log_id = result["id"]
            assert (world.kb_path("project") / result["filename"]).is_file()
        else:
            result = world.cli(
                "sw",
                "log",
                "item",
                "-k",
                "project",
                "--summary",
                "Fixed rounding",
                "--decisions",
                "Use Decimal",
            )
            assert result.exit_code == 0, result.output
            log_id = re.search(r"Logged: (\S+)", result.stdout).group(1)
        entry = server._dispatch_tool("kb_get", {"kb_name": "project", "entry_id": log_id})
        assert "error" not in entry, entry
        assert "Fixed rounding" in entry["entry"]["body"]
        context = server._dispatch_tool(
            "sw_context_for_item", {"kb_name": "project", "item_id": "item"}
        )
        assert log_id in str(context["work_logs"]), context
    finally:
        world.close()


@pytest.mark.parametrize(
    ("mode", "read_only", "code"),
    [("legacy", False, "SCHEMA_VIOLATION"), ("declared", True, "KB_READ_ONLY")],
)
def test_a_refused_log_does_not_report_creation_or_change_the_schema(
    tmp_path, mode, read_only, code
):
    world = make_world(tmp_path, mode, read_only=read_only)
    try:
        schema = world.kb_path("project") / "kb.yaml"
        before = schema.read_bytes()
        result = world.mcp()._dispatch_tool(
            "sw_log", {"kb_name": "project", "item_id": "item", "summary": "Notes"}
        )
        # A read-only KB is refused at the dispatcher, before the handler
        # could report anything; the handler's own refusal
        # says created: False.
        assert result.get("created") in (False, None), result
        assert result["error_code"] == code, result
        if mode == "legacy":
            assert "work_log" in result["suggestion"] and "types:" in result["suggestion"]
        cli = world.cli("sw", "log", "item", "-k", "project", "--summary", "Notes")
        assert cli.exit_code == 1, cli.output
        assert "Logged:" not in cli.output
        if mode == "legacy":
            assert "types:" in cli.output
        assert schema.read_bytes() == before
        assert not list(world.kb_path("project").rglob("item-log-*.md"))
    finally:
        world.close()


@pytest.mark.control(reason="a missing backlog item remains a refusal on both surfaces")
def test_missing_item_does_not_report_a_saved_log(tmp_path):
    world = make_world(tmp_path)
    try:
        result = world.mcp()._dispatch_tool(
            "sw_log", {"kb_name": "project", "item_id": "absent", "summary": "Notes"}
        )
        assert result.get("created") is not True
        assert result["error_code"] == "ENTRY_NOT_FOUND", result
        cli = world.cli("sw", "log", "absent", "-k", "project", "--summary", "Notes")
        assert cli.exit_code == 1, cli.output
        assert "Logged:" not in cli.output
    finally:
        world.close()


def test_same_second_logs_refuse_to_overwrite(tmp_path, monkeypatch):
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 25, 12, 0, tzinfo=tz)

    monkeypatch.setattr(datetime, "datetime", FixedDateTime)
    world = make_world(tmp_path, "declared")
    try:
        server = world.mcp()
        args = {"kb_name": "project", "item_id": "item", "summary": "First session"}
        first = server._dispatch_tool("sw_log", args)
        assert first["created"] is True, first
        second = server._dispatch_tool("sw_log", {**args, "summary": "Second session"})
        assert second.get("created") is False, second
        assert second["error_code"] == "ENTRY_EXISTS"
        text = (world.kb_path("project") / first["filename"]).read_text()
        assert "First session" in text and "Second session" not in text
    finally:
        world.close()


@pytest.mark.parametrize("existing", [0, 42])
def test_mcp_adr_is_saved_with_numbering_from_files(tmp_path, existing):
    world = make_world(tmp_path)
    try:
        if existing:
            adrs = world.kb_path("project") / "adrs"
            adrs.mkdir(exist_ok=True)
            (adrs / "0042-existing.md").write_text(
                "---\nid: adr-0042\ntype: adr\ntitle: Existing\nadr_number: 42\n---\nBody.\n"
            )
        server = world.mcp()
        result = server._dispatch_tool(
            "sw_create_adr",
            {
                "kb_name": "project",
                "title": "Use Decimal",
                "context": "Rounding",
                "decision": "Use Decimal",
            },
        )
        assert result["created"] is True, result
        assert result["adr_number"] == existing + 1
        assert (world.kb_path("project") / result["filename"]).is_file()
        entry = server._dispatch_tool(
            "kb_get", {"kb_name": "project", "entry_id": f"adr-{existing + 1:04d}"}
        )
        assert "error" not in entry, entry
        assert "Rounding" in entry["entry"]["body"]
    finally:
        world.close()
