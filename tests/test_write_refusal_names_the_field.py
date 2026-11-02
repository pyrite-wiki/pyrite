"""A write refusal names the refused field, on every surface (#1284).

Before this, `VALIDATION_FAILED` carried only a sentence: an event without a
date answered `"Event must have a date"` with no `field` and no `suggestion`,
while `UNDECLARED_TYPE` listed its valid types. A caller had to read prose to
learn which field to fix, and REST, MCP and the CLI had no machine-readable
answer at all.

The entry model now answers `validation_errors()` (one row per refused field,
`{field, rule, message, expected?}`) and `validate()` stays the string view the
loader, `pyrite qa` and the plugins already read. `_validate_write` -- the check
`_prepare` calls on a create and an update both -- raises
`ValidationError(..., errors=rows)` for the model's rows and
`SchemaViolationError(..., rows)` for the KB schema's, and `errors` travels
through `refusal_fields`, so the three surfaces answer the same rows.

CLI, MCP (`kb_create`) and REST (`POST /api/entries`) are pinned here for both
shapes: the model's `date` row (`VALIDATION_FAILED`) and the KB schema's
`field_select` row (`SCHEMA_VIOLATION`), in one table over the three surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType
from pyrite.exceptions import SchemaViolationError, ValidationError, refusal_fields
from pyrite.services.kb_service import KBService
from tests._world import build_world
from tests.characterization.world import build_world as build_characterization_world
from tests.link_scope_seed import READABLE

NO_DATE = {"entry_type": "event", "title": "No date event"}


@pytest.fixture
def event_kb(tmp_path):
    """One real world with an empty `ev` KB: config on disk, the app's own db."""
    built = build_world(
        tmp_path,
        kbs=[KBConfig(name="ev", path=tmp_path / "events", kb_type=KBType.GENERIC)],
        settings={"index_path": tmp_path / "index.db"},
        rest=False,
    )
    try:
        yield built
    finally:
        built.close()


class TestServiceRefusal:
    def test_a_missing_field_names_itself_and_suggests_the_value(self, event_kb):
        service = KBService(event_kb.config, event_kb.db)

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "no-date", "No date event", "event", "x")

        exc = caught.value
        assert exc.error_code == "VALIDATION_FAILED"
        assert exc.errors[0]["field"] == "date"
        assert exc.errors[0]["rule"] == "required"
        assert exc.errors[0]["expected"] == "YYYY-MM-DD"
        assert exc.suggestion == "set date (YYYY-MM-DD)"
        # The sentence every existing caller reads is unchanged.
        assert "Event must have a date" in str(exc)

    def test_every_refused_field_gets_its_own_row(self, event_kb):
        service = KBService(event_kb.config, event_kb.db)

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "bad-event", "Bad event", "event", "x", importance=15)

        fields = [row["field"] for row in caught.value.errors]
        assert fields == ["date", "importance"]
        assert caught.value.errors[1]["rule"] == "range"
        assert caught.value.errors[1]["expected"] == "1-10"
        # The first mechanical fix is the one suggested.
        assert caught.value.suggestion == "set date (YYYY-MM-DD)"


class TestCliRefusal:
    def test_create_prints_the_field_in_json(self, event_kb):
        result = event_kb.cli(
            "create", "-k", "ev", "-t", "event", "--title", "No date event", "--format", "json"
        )

        assert result.exit_code == 1, result.output
        body = json.loads(result.stdout)
        assert body["error_code"] == "VALIDATION_FAILED"
        assert body["errors"][0]["field"] == "date"
        assert body["suggestion"] == "set date (YYYY-MM-DD)"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    built = build_characterization_world(tmp_path_factory, label="write-refusal-field")
    try:
        yield built
    finally:
        built.close()


class TestRestRefusal:
    def test_post_names_the_field(self, world):
        principal = world.principals["admin_key"]
        response = world.client.post(
            "/api/entries",
            json={"kb": READABLE, **NO_DATE},
            headers=principal.rest_headers,
            cookies=principal.rest_cookies,
        )

        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "VALIDATION_FAILED"
        assert detail["errors"][0]["field"] == "date"


class TestMcpRefusal:
    def test_kb_create_names_the_field(self, world):
        result = world.dispatch_tool(
            "kb_create",
            {"kb_name": READABLE, **NO_DATE},
            client_kind="local",
            readable_kbs=None,
        )

        assert result["error_code"] == "VALIDATION_FAILED", result
        assert result["errors"][0]["field"] == "date"


class TestNonFieldRefusal:
    def test_a_refusal_that_is_not_about_a_field_carries_no_errors_key(self, event_kb):
        """`errors` stays None, so `refusal_fields` leaves the key out entirely."""
        service = KBService(event_kb.config, event_kb.db)
        service.create_entry("ev", "dated", "Dated", "event", "x", date="2026-01-01")

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "dated", "Dated again", "event", "x", date="2026-01-01")

        assert caught.value.errors is None
        assert "errors" not in refusal_fields(caught.value)


def test_the_public_contract_documents_the_key():
    """docs/json-contracts.md is the table agents read; it names `errors`."""
    contracts = (Path(__file__).resolve().parent.parent / "docs" / "json-contracts.md").read_text(
        encoding="utf-8"
    )

    assert "A field refusal names the field" in contracts
    assert '"errors": [' in contracts


# -- one table: every surface, both row shapes -----------------------------

SURFACES = ("cli", "mcp", "rest")

#: A KB whose own schema refuses an off-list `select` value, so the row comes
#: from `pyrite/schema` (``field_select``) rather than from the entry model.
SCHEMA_KB = "typed"
SCHEMA_KB_YAML = """\
types:
  org:
    description: An organisation
    subdirectory: orgs
    fields:
      org_type:
        type: select
        values: [ngo, company]
"""

SHAPES = {
    "model": {
        "kb": "ev",
        "entry_type": "event",
        "title": "No date event",
        "fields": {},
        "code": "VALIDATION_FAILED",
        "field": "date",
        "rule": "required",
        "expected": "YYYY-MM-DD",
    },
    "schema": {
        "kb": SCHEMA_KB,
        "entry_type": "org",
        "title": "Off-list org",
        "fields": {"org_type": "bogus"},
        "code": "SCHEMA_VIOLATION",
        "field": "org_type",
        "rule": "field_select",
        "expected": ["ngo", "company"],
    },
}


@pytest.fixture(scope="module", params=SURFACES)
def surface_world(request, tmp_path_factory):
    """The same two KBs behind one surface: `pyrite` CLI, MCP or the REST app."""
    surface = request.param
    root = tmp_path_factory.mktemp(f"write-refusal-{surface}")
    typed = root / SCHEMA_KB
    typed.mkdir(parents=True)
    (typed / "kb.yaml").write_text(SCHEMA_KB_YAML, encoding="utf-8")
    world = build_world(
        root,
        kbs=[
            KBConfig(name="ev", path=root / "events", kb_type=KBType.GENERIC),
            KBConfig(name=SCHEMA_KB, path=typed, kb_type=KBType.GENERIC),
        ],
        settings={"index_path": root / "index.db"},
        rest=surface == "rest",
    )
    try:
        yield surface, world
    finally:
        world.close()


def _refused_body(surface, world, case) -> dict:
    """Ask `surface` to create `case`, and return the refusal body it answered."""
    if surface == "cli":
        args = [
            "create",
            "-k",
            case["kb"],
            "-t",
            case["entry_type"],
            "--title",
            case["title"],
            "--format",
            "json",
        ]
        for key, value in case["fields"].items():
            args += ["-f", f"{key}={value}"]
        result = world.cli(*args)
        assert result.exit_code == 1, result.output
        return json.loads(result.stdout[result.stdout.index("{") :])
    if surface == "mcp":
        return world.mcp(tier="admin")._dispatch_tool(
            "kb_create",
            {
                "kb_name": case["kb"],
                "entry_type": case["entry_type"],
                "title": case["title"],
                **case["fields"],
            },
        )
    response = world.client.post(
        "/api/entries",
        json={
            "kb": case["kb"],
            "entry_type": case["entry_type"],
            "title": case["title"],
            "metadata": dict(case["fields"]),
        },
    )
    assert response.status_code in (400, 422), response.text
    return response.json()["detail"]


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_every_surface_names_the_field_and_the_rule(surface_world, shape):
    """A schema row reaches CLI, MCP and REST the way a model row does.

    The groom asked for exactly this table: the surfaces were pinned on the
    model's `date` row only, so a `SCHEMA_VIOLATION` was never carried across
    them by a real refusal -- only by the characterisation golden's synthetic
    exception.
    """
    surface, world = surface_world
    case = SHAPES[shape]

    body = _refused_body(surface, world, case)

    # REST spells the code `code` inside `detail`; MCP and the CLI say `error_code`.
    assert (body.get("error_code") or body.get("code")) == case["code"], body
    row = body["errors"][0]
    # The docs' promise: every row names the field and the rule it broke.
    assert row["field"] == case["field"], body
    assert row["rule"] == case["rule"], body
    assert row["expected"] == case["expected"], body


def test_a_schema_refusal_without_rows_leaves_the_key_out():
    """`errors` is None, not [], so the key is absent -- the docs' wording.

    `SchemaViolationError` used to store `errors or []`, which put
    `"errors": []` on a refusal the docs say carries no key at all.
    """
    exc = SchemaViolationError("The KB schema could not be read")

    assert exc.errors is None
    assert "errors" not in refusal_fields(exc)
