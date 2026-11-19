"""An ambiguous implicit ADR KB is a validation refusal, not a missing name."""

from pyrite.config import KBConfig
from pyrite.plugins import get_registry
from pyrite_software_kb.plugin import SoftwareKBPlugin


def test_sw_create_adr_blank_name_does_not_report_a_missing_kb(cli_world, tmp_path):
    get_registry().register(SoftwareKBPlugin())
    world = cli_world(
        kbs=[
            KBConfig(name="one", path=tmp_path / "one", kb_type="generic"),
            KBConfig(name="two", path=tmp_path / "two", kb_type="generic"),
        ]
    )
    result = world.mcp()._dispatch_tool("sw_create_adr", {"title": "Decision", "kb_name": ""})
    assert result["error_code"] == "VALIDATION_FAILED", result
    assert result.get("created") is not True


def test_implicit_adr_scope_does_not_name_a_hidden_kb(cli_world, tmp_path):
    get_registry().register(SoftwareKBPlugin())
    world = cli_world(
        kbs=[KBConfig(name="hidden-architecture", path=tmp_path / "hidden", kb_type="software")]
    )
    server = world.mcp()
    result = server.tools["sw_create_adr"]["handler"]({"title": "Decision"}, readable_kbs=set())
    assert result["error_code"] == "VALIDATION_FAILED", result
    assert "pass kb_name" in result["error"]
    assert "hidden-architecture" not in str(result)


def test_adr_numbering_skips_and_logs_non_utf8_files(tmp_path, caplog):
    from pyrite_software_kb.adr_listing import next_adr_number

    (tmp_path / "kb.yaml").write_text("name: demo\n")
    (tmp_path / "broken.md").write_bytes(b"\xff\xfeinvalid")
    (tmp_path / "decision.md").write_text(
        "---\nid: adr-0007\ntype: adr\ntitle: Decision\nadr_number: 7\n---\n"
    )
    assert next_adr_number(KBConfig(name="demo", path=tmp_path)) == 8
    assert "broken.md" in caplog.text


def test_shared_resolver_preserves_explicit_db_registered_kbs(tmp_path):
    from pyrite.config import PyriteConfig
    from pyrite_software_kb.adr_listing import resolve_adr_kb

    config = PyriteConfig()
    config.register_db_kbs([{"name": "registered", "path": str(tmp_path), "kb_type": "software"}])
    assert resolve_adr_kb(config, "registered", readable_kbs=None).name == "registered"
    assert resolve_adr_kb(config, "registered", readable_kbs=set()) is None
