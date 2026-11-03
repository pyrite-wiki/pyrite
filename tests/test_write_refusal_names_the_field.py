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
from pyrite.services.kb_service import KBService, check_link_spec
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


class TestPipelineRefusalsCarryRows:
    """The write pipeline's own field raises carry a row too (#1284).

    The model and the KB schema are not the only places a field is refused:
    `_prepare`, the importer and the bulk link checker each name a field in
    prose. They raised a bare `ValidationError`, so a caller who had learned to
    read `errors` fell back to parsing the sentence on exactly these refusals.
    """

    def test_a_create_without_a_title_names_the_title(self, event_kb):
        service = KBService(event_kb.config, event_kb.db)

        with pytest.raises(ValidationError) as caught:
            service.create_entry("ev", "no-title", "", "note", "x")

        exc = caught.value
        assert exc.errors == [
            {"field": "title", "rule": "required", "message": "title is required"}
        ]
        assert exc.suggestion == "set title"

    @pytest.mark.parametrize(
        ("frontmatter", "field"), [("title: Has no type\n", "type"), ("type: note\n", "title")]
    )
    def test_an_imported_file_names_the_frontmatter_key_it_lacks(
        self, event_kb, tmp_path, frontmatter, field
    ):
        service = KBService(event_kb.config, event_kb.db)
        source = tmp_path / f"{field}.md"
        source.write_text(f"---\n{frontmatter}---\n\nBody\n", encoding="utf-8")

        with pytest.raises(ValidationError) as caught:
            service.add_entry_from_file("ev", source)

        row = caught.value.errors[0]
        assert (row["field"], row["rule"]) == (field, "required")
        assert row["message"] == f"Frontmatter must include '{field}'"
        assert caught.value.suggestion == f"set {field}"

    def test_a_managed_field_names_itself(self, event_kb):
        service = KBService(event_kb.config, event_kb.db)

        with pytest.raises(ValidationError) as caught:
            service.create_entry(
                "ev", "managed", "Managed", "note", "x", file_path="notes/managed.md"
            )

        row = caught.value.errors[0]
        assert (row["field"], row["rule"]) == ("file_path", "managed")
        assert "Pyrite maintains this field" in row["message"]

    def test_a_bulk_link_spec_keeps_its_row_in_the_result(self, event_kb):
        service = KBService(event_kb.config, event_kb.db)

        results = service.add_links("ev", [{"source": "one"}], dry_run=True)

        assert results[0]["status"] == "failed"
        assert results[0]["errors"] == [
            {"field": "target", "rule": "required", "message": "missing required field 'target'"}
        ]


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


# -- what the rows carry, and what a row that only names a field reads like ----


@pytest.fixture
def typed_world(tmp_path):
    """The `typed` KB behind REST, so both the request body and the answer are real JSON."""
    typed = tmp_path / SCHEMA_KB
    typed.mkdir(parents=True)
    (typed / "kb.yaml").write_text(SCHEMA_KB_YAML, encoding="utf-8")
    built = build_world(
        tmp_path,
        kbs=[KBConfig(name=SCHEMA_KB, path=typed, kb_type=KBType.GENERIC)],
        settings={"index_path": tmp_path / "index.db"},
        rest=True,
    )
    try:
        yield built
    finally:
        built.close()


def _json_body(result) -> dict:
    assert result.exit_code == 1, result.output
    return json.loads(result.stdout[result.stdout.index("{") :])


def test_validate_only_answers_a_sentence_and_keeps_the_rows(typed_world, tmp_path):
    """`add --validate-only` used to print a row as a Python dict in `error` (#1381 review).

    The CLI joined the rows themselves, so `error` read
    `Validation failed: {'field': 'org_type', ...}` and the body carried no
    `errors` key at all: the sentence lost to the dict, and the rows never
    reached the caller.
    """
    source = tmp_path / "off-list.md"
    source.write_text(
        "---\ntype: org\ntitle: Off-list org\norg_type: bogus\n---\nbody\n", encoding="utf-8"
    )

    body = _json_body(
        typed_world.cli("add", str(source), "-k", SCHEMA_KB, "--validate-only", "--format", "json")
    )

    assert "{" not in body["error"], body
    assert "org_type" in body["error"], body
    assert [row["field"] for row in body["errors"]] == ["org_type"], body
    assert body["errors"][0]["rule"] == "field_select", body


def test_a_date_reaches_the_row_as_its_iso_string(typed_world, tmp_path):
    """An unquoted YAML date made `add --format json` answer NO_OUTCOME (#1381 review).

    `yaml` reads `org_type: 2026-01-01` as a `date`, the row shipped it as is,
    and the answer could not be rendered -- a TypeError the root reported as
    NO_OUTCOME, "what the command did is unknown", instead of the refusal.
    """
    source = tmp_path / "dated.md"
    source.write_text(
        "---\ntype: org\ntitle: Dated org\norg_type: 2026-01-01\n---\nbody\n", encoding="utf-8"
    )

    body = _json_body(typed_world.cli("add", str(source), "-k", SCHEMA_KB, "--format", "json"))

    assert body["error_code"] == "SCHEMA_VIOLATION", body
    assert body["errors"][0]["got"] == "2026-01-01", body


def test_a_non_finite_float_reaches_the_row_as_text(typed_world):
    """`Infinity` in `metadata` turned the 400 into a 500 (#1381 review).

    `json.dumps` refuses `inf`, so serialising the refusal raised
    `ValueError: Out of range float values are not JSON compliant` -- the rows
    now carry the value as text, which is what the client needed to see.
    """
    # Sent as raw text: `httpx`'s own encoder refuses `inf` (allow_nan=False), and
    # the claim under test is what the server does with a body that carries it.
    raw = (
        f'{{"kb": "{SCHEMA_KB}", "entry_type": "org", "title": "Inf org", '
        '"metadata": {"org_type": Infinity}}'
    )
    response = typed_world.client.post(
        "/api/entries", content=raw, headers={"content-type": "application/json"}
    )

    assert response.status_code == 400, response.text
    body = response.json()["detail"]
    assert body["code"] == "SCHEMA_VIOLATION", body
    assert body["errors"][0]["got"] == "inf", body


# -- the bulk answers carry the rows on the record they refused ----------------


def test_a_bulk_link_spec_keeps_its_row_through_the_cli(typed_world, tmp_path):
    """`links bulk-create` copied `error` and `error_code`, and dropped the row (#1381 review)."""
    specs = tmp_path / "specs.yaml"
    specs.write_text("- target: b\n", encoding="utf-8")  # no `source`

    result = typed_world.cli(
        "links", "bulk-create", str(specs), "-k", SCHEMA_KB, "--format", "json"
    )

    body = json.loads(result.stdout[result.stdout.index("{") :])
    item = body["results"][0]
    assert item["status"] == "failed", body
    assert item["error_code"] == "VALIDATION_FAILED", body
    assert [row["field"] for row in item["errors"]] == ["source"], body


def test_a_bulk_create_record_keeps_its_row(typed_world):
    """`_refusal_result` built `{created, error, error_code}` with no rows (#1381 review)."""
    service = KBService(typed_world.config, typed_world.db)

    results = service.bulk_create_entries(
        SCHEMA_KB, [{"entry_type": "org", "title": "Off-list", "org_type": "bogus"}]
    )

    assert results[0]["created"] is False, results
    assert results[0]["error_code"] == "SCHEMA_VIOLATION", results
    assert [row["field"] for row in results[0]["errors"]] == ["org_type"], results


def test_an_imported_record_keeps_its_row(typed_world):
    """REST import narrowed each failure to `{title, error, error_code}` (#1381 review)."""
    body = json.dumps([{"type": "org", "title": "Off-list", "org_type": "bogus"}])

    response = typed_world.client.post(
        "/api/entries/import",
        params={"kb": SCHEMA_KB},
        files={"file": ("in.json", body, "application/json")},
    )

    assert response.status_code == 200, response.text
    out = response.json()
    assert out["errors"] == 1, out
    # The top-level `errors` is the count; the row rides on the record it refused.
    assert [row["field"] for row in out["error_details"][0]["errors"]] == ["org_type"], out


def test_a_saved_link_keeps_the_rows_of_its_refusal(typed_world, monkeypatch):
    """A save refused inside `add_links` kept the message alone (#1381 review)."""
    service = KBService(typed_world.config, typed_world.db)
    service.create_entry(SCHEMA_KB, "a", "A", "org")
    refusal = ValidationError(
        "links: an item is an object",
        errors=[{"field": "links", "rule": "type", "message": "an item is an object"}],
    )

    def refuse(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(service, "apply_operations", refuse)

    results = service.add_links(SCHEMA_KB, [{"source": "a", "target": "b"}])

    assert results[0]["status"] == "failed", results
    assert results[0]["error_code"] == "VALIDATION_FAILED", results
    assert [row["field"] for row in results[0]["errors"]] == ["links"], results


# -- the raises that were left: a link spec, a managed field, an unknown key ---


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"target": ""}, "target"),
        ({"target": "x", "relation": "  "}, "relation"),
        ({"target": "x", "note": 5}, "note"),
        ({"target": "x", "target_kb": 5}, "target_kb"),
    ],
)
def test_a_refused_link_spec_names_its_field(kwargs, field):
    """`check_link_spec` refused with a sentence alone (#1381 review, item 4)."""
    with pytest.raises(ValidationError) as caught:
        check_link_spec(**kwargs)

    assert [row["field"] for row in caught.value.errors] == [field]


def test_a_structural_field_on_an_update_names_its_field(typed_world):
    """An update refused `type` with a sentence and no row (#1381 review, item 4)."""
    service = KBService(typed_world.config, typed_world.db)
    service.create_entry(SCHEMA_KB, "a", "A", "org")

    result = typed_world.cli("update", "a", "-k", SCHEMA_KB, "-f", "type=org", "--format", "json")

    body = _json_body(result)
    assert [row["field"] for row in body["errors"]] == ["type"], body


def test_a_bad_field_pair_names_the_argument(typed_world):
    """`--field` without `=` is a field-shaped refusal too (#1381 review, item 4)."""
    result = typed_world.cli(
        "create",
        "-k",
        SCHEMA_KB,
        "-t",
        "org",
        "--title",
        "X",
        "-f",
        "nokeyvalue",
        "--format",
        "json",
    )

    body = _json_body(result)
    assert [row["field"] for row in body["errors"]] == ["--field"], body


def test_an_unknown_write_key_gets_one_row_per_key(typed_world):
    """REST refused unknown keys with a sentence and no rows (#1381 review, item 4)."""
    response = typed_world.client.post(
        "/api/entries", json={"kb": SCHEMA_KB, "title": "X", "bogus": 1}
    )

    assert response.status_code == 400, response.text
    body = response.json()["detail"]
    assert [row["field"] for row in body["errors"]] == ["bogus"], body
    assert body["errors"][0]["rule"] == "unknown_field", body
