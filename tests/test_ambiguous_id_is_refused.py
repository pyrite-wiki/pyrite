"""An unqualified entry id must identify one readable KB or be refused (#528)."""

import json

import pytest
from typer.testing import CliRunner

from pyrite.exceptions import PyriteError
from pyrite.read_cli import app as read_app
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.characterization.world import PRIVATE, READABLE, READ_ONLY, build_world


DUPLICATE = "duplicate-id-528"
HIDDEN_TWIN = "hidden-twin-528"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    result = build_world(tmp_path_factory, label="ambiguous-id-528")
    svc = KBService(result.config, result.db)
    for kb in (READABLE, PRIVATE):
        svc.create_entry(kb, HIDDEN_TWIN, f"{kb} twin", "note", kb)
    for kb in (READABLE, PRIVATE):
        svc.create_entry(kb, DUPLICATE, f"{kb} duplicate", "note", kb)
    # The read-only KB can be populated by indexing a hand-written file.
    (result.tmpdir / READ_ONLY / f"{DUPLICATE}.md").write_text(
        f"---\nid: {DUPLICATE}\ntype: note\ntitle: Read-only duplicate\n---\n\nReadable"
    )
    result.index_worker.submit_sync(READ_ONLY)
    result.index_worker.wait_for_idle(timeout=10)
    try:
        yield result
    finally:
        result.close()


def test_service_refuses_two_readable_matches_without_exposing_private(world):
    svc = KBService(world.config, world.db)
    readable = {READABLE, READ_ONLY}
    with pytest.raises(PyriteError) as raised:
        svc.get_entry(DUPLICATE, readable_kbs=readable)
    assert raised.value.error_code == "AMBIGUOUS"
    assert raised.value.candidate_kbs == [READABLE, READ_ONLY]
    assert PRIVATE not in str(raised.value)


@pytest.mark.control(reason="Zero and one readable match retain their existing results")
def test_service_zero_one_and_hidden_twin(world):
    svc = KBService(world.config, world.db)
    readable = {READABLE, READ_ONLY}
    assert svc.get_entry("absent-528", readable_kbs=readable) is None
    assert svc.get_entry(HIDDEN_TWIN, readable_kbs={READABLE})["kb_name"] == READABLE
    assert svc.get_entry(DUPLICATE, readable_kbs={READ_ONLY})["kb_name"] == READ_ONLY


def test_service_blank_kb_is_unqualified_and_named_kb_resolves(world):
    svc = KBService(world.config, world.db)
    with pytest.raises(PyriteError):
        svc.get_entry(DUPLICATE, kb_name=" ", readable_kbs={READABLE, READ_ONLY})
    assert (
        svc.get_entry(DUPLICATE, kb_name=READABLE, readable_kbs={READABLE, READ_ONLY})["kb_name"]
        == READABLE
    )


def test_mcp_get_reports_ambiguity_and_scopes_candidates(world):
    result = world.dispatch_tool(
        "kb_get", {"entry_id": DUPLICATE}, client_kind="local", readable_kbs={READABLE, READ_ONLY}
    )
    assert result["error_code"] == "AMBIGUOUS"
    assert READABLE in result["error"] and READ_ONLY in result["error"]
    assert PRIVATE not in json.dumps(result)
    unique = world.dispatch_tool(
        "kb_get", {"entry_id": HIDDEN_TWIN}, client_kind="local", readable_kbs={READABLE}
    )
    assert unique["entry"]["kb_name"] == READABLE


def test_read_only_cli_get_reports_ambiguity(world, monkeypatch):
    import pyrite.read_cli as read_cli

    def make_service():
        db = PyriteDB(world.config.settings.index_path)
        return KBService(world.config, db), db

    monkeypatch.setattr(read_cli, "_get_svc", make_service)
    result = CliRunner().invoke(read_app, ["get", DUPLICATE, "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "AMBIGUOUS"
    assert READABLE in payload["error"] and READ_ONLY in payload["error"]


@pytest.mark.control(
    reason="REST GET without kb keeps its existing first readable match for web links"
)
def test_rest_get_without_kb_remains_compatible_for_web(world):
    principal = world.principals["local_user"]
    world.release_idle_connections()
    result = world.client.get(
        f"/api/entries/{DUPLICATE}",
        headers=principal.rest_headers,
        cookies=principal.rest_cookies,
    )
    assert result.status_code == 200
    assert result.json()["kb_name"] == READABLE
