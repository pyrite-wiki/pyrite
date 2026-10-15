"""A QA result says a check passed, or a fix landed, only when that check ran
or that write returned. Anything else comes back as a failure with its reason
(#795, and the service half of #788).

Every test drives an entry point: `pyrite qa assess`, `pyrite ci`, `pyrite qa
fix`, `pyrite qa validate` through `CliRunner`, and MCP `kb_qa_assess` through
`_dispatch_tool`. The model is a real `LLMService` whose Anthropic SDK is
replaced at the process edge (`llm_service._import_anthropic`), so the
evaluator, its parsing and the wiring in each command all run for real.

Maintainer decisions (2026-10-15): a tier that did not run writes **no**
record, and `ci --tier 2` runs the judgment rubric through `validate_kb`.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.qa_fix_service import QAFixService
from pyrite.services import qa_service as qa_service_module
from pyrite.storage.database import PyriteDB
from pyrite.storage.index import IndexManager

runner = CliRunner()

RUBRIC = "Entry body explains the why, not just the what"
#: The tiers QA runs, from the code; (1, 2) only so this file imports on a
#: base that predates the constant (verify-red).
QA_TIERS = getattr(qa_service_module, "QA_TIERS", (1, 2))

JUDGED = "judged"  # a KB whose kb.yaml carries a judgment-only rubric item
PLAIN = "plain"  # a KB with no judgment items at all


# =========================================================================
# The model, at the process edge
# =========================================================================


def _answer(verdict: bool) -> str:
    return json.dumps(
        [{"item": RUBRIC, "pass": verdict, "confidence": 0.9, "reasoning": "Lists facts only"}]
    )


#: What the model says, per regime. A callable may raise. Only "passes" and
#: "fails" are answers to the rubric; every other regime is a check that did
#: not run, and must never be recorded as one that did.
MODEL_REPLIES = {
    "passes": lambda: _answer(True),
    "fails": lambda: _answer(False),
    "raises": lambda: (_ for _ in ()).throw(RuntimeError("provider unreachable")),
    "empty": lambda: "",
    "not_json": lambda: "The entry looks fine to me.",
    "not_list": lambda: json.dumps({"item": RUBRIC, "pass": True}),
    "answers_no_item": lambda: "[]",
    # Shapes the parser accepts as a list of objects but that do not answer:
    "item_not_string": lambda: json.dumps([{"item": [RUBRIC], "pass": True}]),
    "item_is_object": lambda: json.dumps([{"item": {"text": RUBRIC}, "pass": True}]),
    "item_is_number": lambda: json.dumps([{"item": 5, "pass": True}]),
    "row_not_object": lambda: json.dumps([RUBRIC, True]),
    "confidence_not_number": lambda: json.dumps(
        [{"item": RUBRIC, "pass": False, "confidence": "high", "reasoning": "x"}]
    ),
    # JSON the parser cannot even read without an error it does not expect
    # (RecursionError): whatever goes wrong is still that entry's failure.
    "deeply_nested": lambda: "[" * 100_000 + "]" * 100_000,
}
NOT_RUN = [
    "raises",
    "empty",
    "not_json",
    "not_list",
    "answers_no_item",
    "item_not_string",
    "item_is_object",
    "item_is_number",
    "row_not_object",
    "confidence_not_number",
    "deeply_nested",
]


class FakeAnthropic:
    """The Anthropic SDK module: `Anthropic(**kw).messages.create(**kw)`.

    `reply` is the default regime; `per_entry` overrides it for an entry
    whose title appears in the prompt (an `assess_kb` with one failure in
    the middle).
    """

    def __init__(self, reply: str = "passes", per_entry: dict[str, str] | None = None):
        self.reply = reply
        self.per_entry = per_entry or {}
        self.prompts: list[str] = []

    def module(self):
        create = self._create

        class Anthropic:
            def __init__(self, **_kwargs):
                self.messages = SimpleNamespace(create=create)

        return SimpleNamespace(Anthropic=Anthropic)

    def _create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        regime = self.reply
        for title, override in self.per_entry.items():
            if f"Title: {title}\n" in prompt:
                regime = override
        text = MODEL_REPLIES[regime]()
        return SimpleNamespace(content=[SimpleNamespace(text=text)], usage=None)


def _model(fake: FakeAnthropic):
    return patch("pyrite.services.llm_service._import_anthropic", return_value=fake.module())


# =========================================================================
# The world: two KBs on disk, indexed
# =========================================================================


def _note(entry_id: str, title: str, *, links: list[str], tags: list[str], extra: str = "") -> str:
    link_yaml = "".join(f"\n  - target: {t}\n    relation: related_to" for t in links)
    return (
        f"---\nid: {entry_id}\ntitle: {title}\ntype: note\ntags: [{', '.join(tags)}]\n"
        f"links:{link_yaml or ' []'}\n{extra}---\n"
        f"The {title.lower()} records what was found and where.\n"
    )


TITLES = {"alpha": "Alpha harbour survey", "beta": "Beta harbour survey", "gamma": "Gamma tide log"}


def _build(tmp_path: Path, *, model: bool) -> PyriteConfig:
    judged = tmp_path / JUDGED
    judged.mkdir()
    (judged / "kb.yaml").write_text(f"name: {JUDGED}\nevaluation_rubric:\n  - {RUBRIC}\n")
    # A ring of links, tagged and titled, so tier 1 finds nothing to warn on.
    ring = list(TITLES)
    for i, eid in enumerate(ring):
        (judged / f"{eid}.md").write_text(
            _note(eid, TITLES[eid], links=[ring[(i + 1) % 3], ring[(i - 1) % 3]], tags=["survey"])
        )
    plain = tmp_path / PLAIN
    plain.mkdir()
    (plain / "solo.md").write_text(_note("solo", "Solo plain note", links=[], tags=["x"]))

    settings = Settings(index_path=tmp_path / "index.db")
    if model:
        settings.ai_provider = "anthropic"
        settings.ai_api_key = "test-key"
        settings.ai_model = "fake-model"
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=JUDGED, path=judged, kb_type=KBType.GENERIC),
            KBConfig(name=PLAIN, path=plain, kb_type=KBType.GENERIC),
        ],
        settings=settings,
    )
    db = PyriteDB(settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    return config


@pytest.fixture
def no_model(tmp_path):
    return _build(tmp_path, model=False)


@pytest.fixture
def with_model(tmp_path):
    return _build(tmp_path, model=True)


def _records(config: PyriteConfig, kb: str, target: str) -> list[dict]:
    """Every qa_assessment file in `kb` whose target is `target`, as frontmatter."""
    out = []
    for path in config.get_kb(kb).path.rglob("*.md"):
        text = path.read_text()
        if not text.startswith("---"):
            continue
        front = yaml.safe_load(text.split("---", 2)[1]) or {}
        if front.get("type") == "qa_assessment" and front.get("target_entry") == target:
            out.append(front)
    return out


# =========================================================================
# The surfaces that take a tier
# =========================================================================


def _cli(config: PyriteConfig, args: list[str]):
    result = _invoke(config, args)
    try:
        body = json.loads(result.stdout)
    except ValueError:
        body = {"_raw": result.stdout}
    return result.exit_code, body


def _invoke(config: PyriteConfig, args: list[str]):
    """The raw `CliRunner` result, wide enough that Rich does not wrap a reason."""
    with (
        patch("pyrite.cli.context.load_config", return_value=config),
        patch("pyrite.cli.load_config", return_value=config),
    ):
        return runner.invoke(app, args, env={"COLUMNS": "300"})


def _mcp(config: PyriteConfig, args: dict):
    server = PyriteMCPServer(config=config, tier="write")
    try:
        return server._dispatch_tool("kb_qa_assess", args)
    finally:
        server.db.close()


def run_surface(surface: str, config: PyriteConfig, *, kb: str, tier: int, entry: str | None):
    """Run one tier-taking surface. Returns (refused, error_code, body).

    `refused` is a whole-call refusal: a non-zero exit on the CLI, an
    `error_code` in the MCP result.
    """
    if surface.startswith("cli-assess"):
        args = ["qa", "assess", kb, "--tier", str(tier), "--max-age", "0", "--format", "json"]
        if entry:
            args += ["--entry", entry]
        code, body = _cli(config, args)
        return code != 0, body.get("error_code"), body
    if surface == "ci":
        code, body = _cli(config, ["ci", "--kb", kb, "--tier", str(tier), "--format", "json"])
        return code != 0, body.get("error_code"), body
    if surface.startswith("mcp-assess"):
        args = {"kb_name": kb, "tier": tier, "max_age_hours": 0}
        if entry:
            args["entry_id"] = entry
        body = _mcp(config, args)
        return "error_code" in body, body.get("error_code"), body
    raise AssertionError(surface)


#: (surface, takes an entry id). Every surface that accepts `tier`.
TIER_SURFACES = [
    ("cli-assess-entry", True),
    ("cli-assess-kb", False),
    ("ci", False),
    ("mcp-assess-entry", True),
    ("mcp-assess-kb", False),
]


# =========================================================================
# (7) A tier outside QA_TIERS is refused on every surface
# =========================================================================


@pytest.mark.parametrize("tier", [0, 3, 7, -1])
@pytest.mark.parametrize(("surface", "takes_entry"), TIER_SURFACES)
def test_a_tier_that_does_not_exist_is_refused(with_model, surface, takes_entry, tier):
    assert tier not in QA_TIERS
    fake = FakeAnthropic("passes")
    with _model(fake):
        refused, code, body = run_surface(
            surface, with_model, kb=JUDGED, tier=tier, entry="alpha" if takes_entry else None
        )
    assert refused, body
    assert code == "INVALID_TIER", body
    assert body.get("result") != "pass"
    for eid in TITLES:
        assert _records(with_model, JUDGED, eid) == []
    assert fake.prompts == []


@pytest.mark.parametrize("tier", QA_TIERS)
def test_every_supported_tier_is_accepted_by_mcp_schema_and_service(with_model, tier):
    from pyrite.server.tool_schemas import WRITE_TOOLS

    schema = WRITE_TOOLS["kb_qa_assess"]["inputSchema"]["properties"]["tier"]
    assert schema["minimum"] == min(QA_TIERS) and schema["maximum"] == max(QA_TIERS)
    with _model(FakeAnthropic("passes")):
        refused, _code, body = run_surface(
            "mcp-assess-entry", with_model, kb=JUDGED, tier=tier, entry="alpha"
        )
    assert not refused, body


# =========================================================================
# (1), (2) Tier 2 with no model: refused, nothing recorded, never a pass
# =========================================================================


@pytest.mark.parametrize(("surface", "takes_entry"), TIER_SURFACES)
def test_tier_2_with_no_model_is_refused_and_writes_nothing(no_model, surface, takes_entry):
    refused, code, body = run_surface(
        surface, no_model, kb=JUDGED, tier=2, entry="alpha" if takes_entry else None
    )
    assert refused, body
    assert code == "AI_NOT_CONFIGURED", body
    assert body.get("result") != "pass"
    for eid in TITLES:
        assert _records(no_model, JUDGED, eid) == []


def test_ci_tier_2_with_no_model_refuses_even_with_no_kbs(tmp_path):
    """No KB to validate is no reason to say a tier that cannot run passed."""
    config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmp_path / "i.db"))
    code, body = _cli(config, ["ci", "--tier", "2", "--format", "json"])
    assert code != 0, body
    assert body.get("error_code") == "AI_NOT_CONFIGURED"


# =========================================================================
# (3) A model that did not answer: no record, a failure with its reason
# =========================================================================

ENTRY_SURFACES = ["cli-assess-entry", "mcp-assess-entry"]
KB_SURFACES = ["cli-assess-kb", "mcp-assess-kb"]


@pytest.mark.parametrize("regime", NOT_RUN)
@pytest.mark.parametrize("surface", ENTRY_SURFACES)
def test_entry_tier_2_that_did_not_run_is_refused_with_its_reason(with_model, surface, regime):
    fake = FakeAnthropic(regime)
    with _model(fake):
        refused, code, body = run_surface(surface, with_model, kb=JUDGED, tier=2, entry="alpha")
    assert fake.prompts, "the model was never asked"
    assert refused, body
    assert code == "EVALUATION_FAILED", body
    assert _records(with_model, JUDGED, "alpha") == []


@pytest.mark.parametrize("regime", NOT_RUN)
@pytest.mark.parametrize("surface", KB_SURFACES)
def test_kb_tier_2_that_did_not_run_lists_each_entry_as_failed(with_model, surface, regime):
    with _model(FakeAnthropic(regime)):
        refused, _code, body = run_surface(surface, with_model, kb=JUDGED, tier=2, entry=None)
    assert not refused, body
    failed = {f["entry_id"]: f for f in body["failed"]}
    assert set(failed) == set(TITLES), body
    for f in failed.values():
        assert f["error_code"] == "EVALUATION_FAILED"
        assert f["reason"]
    assert body["results"] == []
    for eid in TITLES:
        assert _records(with_model, JUDGED, eid) == []


@pytest.mark.parametrize("surface", KB_SURFACES)
def test_one_failure_in_the_middle_does_not_stop_the_rest(with_model, surface):
    fake = FakeAnthropic("passes", per_entry={TITLES["beta"]: "raises"})
    with _model(fake):
        _refused, _code, body = run_surface(surface, with_model, kb=JUDGED, tier=2, entry=None)
    assert [f["entry_id"] for f in body["failed"]] == ["beta"]
    assert "provider unreachable" in body["failed"][0]["reason"]
    assert sorted(r["target_entry"] for r in body["results"]) == ["alpha", "gamma"]
    assert _records(with_model, JUDGED, "beta") == []
    for eid in ("alpha", "gamma"):
        (record,) = _records(with_model, JUDGED, eid)
        assert record["tier"] == 2


@pytest.mark.parametrize("surface", ENTRY_SURFACES)
def test_a_model_finding_is_recorded_and_is_not_a_pass(with_model, surface):
    """tier: 2 with qa_status: pass must mean the model found nothing."""
    with _model(FakeAnthropic("fails")):
        refused, _code, body = run_surface(surface, with_model, kb=JUDGED, tier=2, entry="alpha")
    assert not refused, body
    (record,) = _records(with_model, JUDGED, "alpha")
    assert record["tier"] == 2
    assert record["qa_status"] != "pass", record
    assert any(i["rule"] == "llm_rubric_violation" for i in record["issues"])


@pytest.mark.control(reason="a model that answered every item and passed them is a real pass")
@pytest.mark.parametrize("surface", ENTRY_SURFACES)
def test_a_model_that_answered_and_passed_records_tier_2(with_model, surface):
    with _model(FakeAnthropic("passes")):
        refused, _code, body = run_surface(surface, with_model, kb=JUDGED, tier=2, entry="alpha")
    assert not refused, body
    (record,) = _records(with_model, JUDGED, "alpha")
    assert (record["tier"], record["qa_status"]) == (2, "pass")


@pytest.mark.control(reason="a type with no judgment items has nothing for tier 2 to run")
@pytest.mark.parametrize("surface", ENTRY_SURFACES)
def test_no_judgment_items_still_passes_tier_2(with_model, surface):
    fake = FakeAnthropic("raises")
    with _model(fake):
        refused, _code, body = run_surface(surface, with_model, kb=PLAIN, tier=2, entry="solo")
    assert not refused, body
    assert fake.prompts == []
    (record,) = _records(with_model, PLAIN, "solo")
    assert record["tier"] == 2


def test_an_entry_missing_from_the_index_records_no_tier_2(with_model):
    with _model(FakeAnthropic("passes")):
        refused, _code, body = run_surface(
            "cli-assess-entry", with_model, kb=JUDGED, tier=2, entry="no-such-entry"
        )
    assert refused, body
    assert _records(with_model, JUDGED, "no-such-entry") == []


# =========================================================================
# (2) ci --tier 2 runs the judgment rubric; its findings count at the gate
# =========================================================================


def _ci(config, *extra):
    return _cli(config, ["ci", "--kb", JUDGED, "--format", "json", *extra])


@pytest.mark.control(reason="the world is clean at tier 1, so a tier 2 failure is tier 2's")
def test_ci_tier_1_passes_on_the_clean_world(with_model):
    code, body = _ci(with_model)
    assert (code, body["result"]) == (0, "pass"), body


def test_ci_tier_2_finding_fails_the_command(with_model):
    fake = FakeAnthropic("fails")
    with _model(fake):
        code, body = _ci(with_model, "--tier", "2")
    assert len(fake.prompts) == len(TITLES)
    assert code == 1, body
    assert body["result"] == "fail"
    rules = [i["rule"] for kb in body["kbs"] for i in kb["issues"]]
    assert rules.count("llm_rubric_violation") == len(TITLES)


@pytest.mark.control(reason="a model that passes every entry leaves ci passing")
def test_ci_tier_2_that_passes_passes(with_model):
    with _model(FakeAnthropic("passes")):
        code, body = _ci(with_model, "--tier", "2")
    assert (code, body["result"]) == (0, "pass"), body


@pytest.mark.parametrize("regime", NOT_RUN)
def test_ci_tier_2_that_did_not_run_is_never_a_pass(with_model, regime):
    with _model(FakeAnthropic(regime, per_entry={TITLES["alpha"]: "passes"})):
        code, body = _ci(with_model, "--tier", "2", "--severity", "error")
    assert code == 1, body
    assert body["result"] == "fail"
    failed = [i for kb in body["kbs"] for i in kb["issues"] if i["rule"] == "evaluation_failed"]
    assert sorted(i["entry_id"] for i in failed) == ["beta", "gamma"]


# =========================================================================
# (5) --create-tasks returns the tasks it could not create
# =========================================================================


def test_assess_returns_the_task_it_could_not_create(no_model):
    """A second run on a failing entry finds the task's id taken: it says so."""
    beta = no_model.get_kb(JUDGED).path / "beta.md"
    beta.write_text(beta.read_text().replace("type: note", "type: event\ndate: '2006'"))
    IndexManager(PyriteDB(no_model.settings.index_path), no_model).index_all()

    args = ["qa", "assess", JUDGED, "--entry", "beta", "--create-tasks", "--format", "json"]
    code, first = _cli(no_model, args)
    assert code == 0 and first["qa_status"] == "fail", first
    assert "task_failed" not in first

    code, second = _cli(no_model, args)
    assert second["task_failed"]["entry_id"] == "beta", second
    assert second["task_failed"]["reason"]

    code, whole = _cli(
        no_model, [*args[:3], "--max-age", "0", "--create-tasks", "--format", "json"]
    )
    assert [t["entry_id"] for t in whole["tasks_failed"]] == ["beta"], whole


# =========================================================================
# (4) qa fix: `fixed` holds only fixes whose write returned
# =========================================================================

#: Each fix kind `fix_kb` dispatches, as the scenario that produces it: the
#: entry file to write, and the issue validation reports for it when the
#: validator cannot produce one (the missing-field fix: see `FIX_KINDS`).
FIX_SCENARIOS = {
    "invalid_date": (
        "fixd",
        "---\nid: fixd\ntitle: Dated harbour event\ntype: event\ndate: '2006'\n"
        "importance: 5\ntags: [ok]\n---\nBody.\n",
        None,
    ),
    "broken_link": (
        "fixl",
        _note("fixl", "Linking harbour note", links=["alphx"], tags=["ok"]),
        None,
    ),
    "tag_case": ("fixt", _note("fixt", "Tagged harbour note", links=[], tags=["MixedCase"]), None),
    "schema_violation": (
        "fixm",
        _note("fixm", "Missing harbour note", links=[], tags=["ok"]),
        {
            "rule": "schema_violation",
            "field": "importance",
            "severity": "error",
            "message": "missing field importance",
        },
    ),
}


def test_every_fix_kind_has_a_scenario():
    assert set(FIX_SCENARIOS) == set(getattr(QAFixService, "FIX_KINDS", ()))


def _fix_world(tmp_path: Path, kind: str) -> tuple[PyriteConfig, Path]:
    config = _build(tmp_path, model=False)
    eid, text, _issue = FIX_SCENARIOS[kind]
    root = config.get_kb(JUDGED).path
    for sub, name, body in (
        (kind, eid, text),
        (
            "bystander",
            "bystand",
            _note("bystand", "Bystander harbour note", links=[], tags=["Loud"]),
        ),
    ):
        (root / sub).mkdir()
        (root / sub / f"{name}.md").write_text(body)
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    return config, root / kind / f"{eid}.md"


def _run_fix(config: PyriteConfig, kind: str, *, dry_run: bool = False) -> dict:
    """`fix_kb` as `qa fix` runs it, plus the issue no validator yields today."""
    from pyrite.services.qa_service import QAService

    db = PyriteDB(config.settings.index_path)
    try:
        qa = QAService(config, db)
        eid, _text, extra = FIX_SCENARIOS[kind]

        def validate(kb):
            result = qa.validate_kb(kb, readable_kbs=None)
            if extra:
                result["issues"].append({"entry_id": eid, "kb_name": kb, **extra})
            return result

        return QAFixService(config, db).fix_kb(JUDGED, dry_run=dry_run, validate_kb_fn=validate)
    finally:
        db.close()


def _of(items: list[dict], kind: str, eid: str) -> list[dict]:
    return [f for f in items if f.get("rule") == kind and f.get("entry_id") == eid]


needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory permissions"
)


@pytest.mark.control(reason="a fix whose write returned is reported fixed and is on disk")
@pytest.mark.parametrize("kind", sorted(FIX_SCENARIOS))
def test_a_fix_that_landed_is_fixed(tmp_path, kind):
    config, path = _fix_world(tmp_path, kind)
    before = path.read_text()
    report = _run_fix(config, kind)
    eid = FIX_SCENARIOS[kind][0]
    assert _of(report["fixed"], kind, eid), report
    assert report.get("failed", []) == []
    assert path.read_text() != before


@needs_permissions
@pytest.mark.parametrize("kind", sorted(FIX_SCENARIOS))
def test_a_write_that_raised_is_failed_and_the_rest_still_run(tmp_path, kind):
    config, path = _fix_world(tmp_path, kind)
    before = path.read_text()
    path.parent.chmod(0o555)
    try:
        report = _run_fix(config, kind)
    finally:
        path.parent.chmod(0o755)
    eid = FIX_SCENARIOS[kind][0]
    assert _of(report["fixed"], kind, eid) == [], report
    (failure,) = _of(report["failed"], kind, eid)
    assert failure["reason"]
    assert _of(report["manual"], kind, eid) == []
    assert path.read_text() == before
    # The bystander's tag fix still ran and landed.
    assert _of(report["fixed"], "tag_case", "bystand")
    assert "loud" in (path.parent.parent / "bystander" / "bystand.md").read_text()


@pytest.mark.parametrize("kind", sorted(FIX_SCENARIOS))
def test_a_fix_for_an_entry_whose_file_is_gone_is_failed(tmp_path, kind):
    config, path = _fix_world(tmp_path, kind)
    path.unlink()
    report = _run_fix(config, kind)
    eid = FIX_SCENARIOS[kind][0]
    assert _of(report["fixed"], kind, eid) == [], report
    (failure,) = _of(report["failed"], kind, eid)
    assert failure["reason"]
    assert not path.exists()


@needs_permissions
@pytest.mark.control(reason="--dry-run writes nothing, so nothing it plans can fail")
@pytest.mark.parametrize("kind", sorted(FIX_SCENARIOS))
def test_dry_run_fails_nothing(tmp_path, kind):
    config, path = _fix_world(tmp_path, kind)
    path.parent.chmod(0o555)
    try:
        report = _run_fix(config, kind, dry_run=True)
    finally:
        path.parent.chmod(0o755)
    assert report.get("failed", []) == []
    assert _of(report["fixed"], kind, FIX_SCENARIOS[kind][0])


def test_a_link_fix_with_nothing_to_rewrite_is_not_fixed(tmp_path):
    """A broken link that lives only in the body is not in `links`: the
    rewrite finds nothing to change and must not report a fix."""
    config, path = _fix_world(tmp_path, "tag_case")
    body_link = path.parent / "bodylink.md"
    body_link.write_text(
        "---\nid: bodylink\ntitle: Body linking harbour note\ntype: note\ntags: [ok]\n---\n"
        "See [[alphx]] for more.\n"
    )
    db = PyriteDB(config.settings.index_path)
    IndexManager(db, config).index_all()
    db.close()
    before = body_link.read_text()
    report = _run_fix(config, "tag_case")
    assert _of(report["fixed"], "broken_link", "bodylink") == [], report
    (failure,) = _of(report["failed"], "broken_link", "bodylink")
    assert failure["reason"]
    assert body_link.read_text() == before


@needs_permissions
def test_qa_fix_cli_reports_failed(tmp_path):
    """The `failed` bucket reaches `pyrite qa fix --format json`."""
    config, path = _fix_world(tmp_path, "tag_case")
    path.parent.chmod(0o555)
    try:
        code, body = _cli(config, ["qa", "fix", "--kb", JUDGED, "--format", "json"])
    finally:
        path.parent.chmod(0o755)
    assert [f["entry_id"] for f in body["failed"]] == ["fixt"], body
    assert body["failed_count"] == 1


# =========================================================================
# Tier 1 neighbours: a check that raised, a file that would not load
# =========================================================================


def test_a_rubric_checker_that_raised_is_reported(no_model):
    """A misconfigured checker (`fields` not a list) raises; it is not a pass."""
    kb_yaml = no_model.get_kb(JUDGED).path / "kb.yaml"
    kb_yaml.write_text(
        f"name: {JUDGED}\nevaluation_rubric:\n"
        "  - text: Has a source field\n    checker: has_any_field\n    params: {fields: 5}\n"
    )
    no_model.get_kb(JUDGED).invalidate_schema_cache()
    code, body = _cli(no_model, ["qa", "validate", JUDGED, "--entry", "alpha", "--format", "json"])
    failed = [i for i in body["issues"] if i["rule"] == "checker_failed"]
    assert len(failed) == 1, body
    assert "has_any_field" in failed[0]["message"]


def test_a_file_that_would_not_load_is_reported(no_model):
    path = no_model.get_kb(JUDGED).path / "alpha.md"
    path.write_text("---\nid: alpha\ntitle: [unclosed\n---\nbody\n")
    code, body = _cli(no_model, ["qa", "validate", JUDGED, "--entry", "alpha", "--format", "json"])
    unloadable = [i for i in body["issues"] if i["rule"] == "file_unloadable"]
    assert len(unloadable) == 1, body


def test_ci_on_a_kb_that_does_not_exist_is_not_a_pass(no_model):
    """Nothing was validated, so the gate cannot say it passed."""
    code, body = _cli(no_model, ["ci", "--kb", "no-such-kb", "--format", "json"])
    assert code != 0, body
    assert body.get("error_code") == "KB_NOT_FOUND", body


def test_ci_on_a_named_kb_with_none_configured_is_not_a_pass(tmp_path):
    config = PyriteConfig(knowledge_bases=[], settings=Settings(index_path=tmp_path / "i.db"))
    code, body = _cli(config, ["ci", "--kb", "no-such-kb", "--format", "json"])
    assert code != 0, body
    assert body.get("error_code") == "KB_NOT_FOUND", body


# =========================================================================
# Fix round 1: every output mode, every KB string, every reply shape
# =========================================================================

#: Every `--format` `qa assess` takes (`validate_output_format`).
ASSESS_FORMATS = ["json", "yaml", "rich", "csv", "markdown"]


@pytest.mark.parametrize("fmt", ASSESS_FORMATS)
def test_every_assess_output_mode_shows_the_entries_not_assessed(with_model, fmt):
    """csv and markdown render only `results`: the failures must still show."""
    with _model(FakeAnthropic("raises")):
        result = _invoke(
            with_model, ["qa", "assess", JUDGED, "--tier", "2", "--max-age", "0", "--format", fmt]
        )
    shown = result.stdout + result.stderr
    for eid in TITLES:
        assert eid in shown, (fmt, shown)
    assert "provider unreachable" in shown, (fmt, shown)


@pytest.mark.parametrize("fmt", ASSESS_FORMATS)
def test_every_assess_output_mode_shows_the_task_not_created(no_model, fmt):
    beta = no_model.get_kb(JUDGED).path / "beta.md"
    beta.write_text(beta.read_text().replace("type: note", "type: event\ndate: '2006'"))
    IndexManager(PyriteDB(no_model.settings.index_path), no_model).index_all()
    args = ["qa", "assess", JUDGED, "--entry", "beta", "--create-tasks", "--format", "json"]
    _cli(no_model, args)  # creates the task; the next run collides with it
    for run in (
        args[:-1] + [fmt],
        [*args[:3], "--max-age", "0", "--create-tasks", "--format", fmt],
    ):
        result = _invoke(no_model, run)
        shown = result.stdout + result.stderr
        assert "EntryExistsError" in shown, (fmt, run, shown)


@needs_permissions
def test_qa_fix_default_output_reports_failed(tmp_path):
    """The human report: no 'No fixable issues', the failure and its reason, the count."""
    config, path = _fix_world(tmp_path, "tag_case")
    # Every fix fails: the entry's folder and the bystander's are read-only.
    folders = [path.parent, path.parent.parent / "bystander"]
    for folder in folders:
        folder.chmod(0o555)
    try:
        result = _invoke(config, ["qa", "fix", "--kb", JUDGED])
    finally:
        for folder in folders:
            folder.chmod(0o755)
    out = result.stdout
    assert "No fixable issues found" not in out, out
    assert "fixt" in out and "bystand" in out and "update_failed" in out, out
    assert "0 fixed, 2 failed" in out, out


@pytest.mark.parametrize("regime", ["raises", "item_not_string"])
def test_ci_text_output_fails_a_tier_that_did_not_run(with_model, regime):
    with _model(FakeAnthropic(regime)):
        result = _invoke(with_model, ["ci", "--kb", JUDGED, "--tier", "2", "--severity", "error"])
    assert result.exit_code == 1, result.stdout
    assert "Tier 2 did not run" in result.stdout
    assert "Result: FAIL" in result.stdout


def _required_world(tmp_path: Path) -> PyriteConfig:
    """`judged` declares `source` required on notes; no entry has one."""
    config = _build(tmp_path, model=False)
    kb = config.get_kb(JUDGED)
    (kb.path / "kb.yaml").write_text(
        f"name: {JUDGED}\ntypes:\n  note:\n    required: [title, source]\n"
    )
    kb.invalidate_schema_cache()
    return config


def _without(config: PyriteConfig, name: str) -> PyriteConfig:
    return PyriteConfig(
        knowledge_bases=[kb for kb in config.knowledge_bases if kb.name != name],
        settings=config.settings,
    )


@pytest.mark.control(reason="the schema check fails the gate while the KB is configured")
def test_ci_fails_the_required_field_while_the_kb_is_configured(tmp_path):
    config = _required_world(tmp_path)
    code, body = _cli(config, ["ci", "--kb", JUDGED, "--severity", "error", "--format", "json"])
    assert (code, body["result"]) == (1, "fail"), body


def test_ci_runs_the_checks_of_a_kb_removed_from_the_config(tmp_path):
    """The reviewer's repro: out of config.yaml, not reindexed. Indexing
    registered the KB (`kb` row, source `user`), so it resolves through the
    registry and its schema check really runs. Index rows with no `kb` row
    cannot exist: deleting the row cascades to its entries."""
    config = _without(_required_world(tmp_path), JUDGED)
    code, body = _cli(config, ["ci", "--kb", JUDGED, "--severity", "error", "--format", "json"])
    assert code == 1, body
    rules = {i["rule"] for kb in body["kbs"] for i in kb["issues"]}
    assert "schema_violation" in rules, body


def test_ci_runs_the_checks_of_a_db_registered_kb(tmp_path):
    """A KB `pyrite kb add` registered is resolved, and its checks really run."""
    full = _required_world(tmp_path)
    path = full.get_kb(JUDGED).path
    db = PyriteDB(full.settings.index_path)
    db.register_kb(name=JUDGED, kb_type="generic", path=str(path), description="", source="user")
    db.close()
    code, body = _cli(
        _without(full, JUDGED), ["ci", "--kb", JUDGED, "--severity", "error", "--format", "json"]
    )
    assert code == 1, body
    rules = {i["rule"] for kb in body["kbs"] for i in kb["issues"]}
    assert "schema_violation" in rules, body


# =========================================================================
# Fix round 2: a KB whose files cannot be read is a failed check, never a
# pass and never a crash that loses the other KBs' results
# =========================================================================

#: How kbB's folder is broken, and the words its issue gives as the reason.
FOLDER_FAULTS = {
    "missing": "missing",  # moved away
    "unreadable": "cannot be read",  # chmod 000
    "unlistable": "cannot be read",  # chmod 311: can be entered, not listed
    "kb_yaml_unreadable": "cannot be read",  # the folder is fine, its kb.yaml is not
    "not_a_folder": "not a folder",  # a file where the folder was
}
#: The faults that rely on permissions (root ignores them).
PERMISSION_FAULTS = {"unreadable", "unlistable", "kb_yaml_unreadable"}
#: How kbB reaches QA: listed in the config, or registered in the index only.
KB_SOURCES = ["configured", "registered"]


@pytest.fixture
def broken_plain(tmp_path, request):
    """`judged` is fine; `plain` (kbB) has a broken folder. Yields the config."""
    fault, source = request.param
    if fault in PERMISSION_FAULTS and hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    config = _build(tmp_path, model=False)
    folder = config.get_kb(PLAIN).path
    if source == "registered":
        # Indexing registered it (`kb` row, source user); the config forgets it.
        config = _without(config, PLAIN)
    kb_yaml = folder / "kb.yaml"
    if fault == "missing":
        shutil.rmtree(folder)
    elif fault == "not_a_folder":
        shutil.rmtree(folder)
        folder.write_text("not a folder\n")
    elif fault == "kb_yaml_unreadable":
        kb_yaml.write_text(f"name: {PLAIN}\n")
        kb_yaml.chmod(0o000)
    else:
        folder.chmod(0o000 if fault == "unreadable" else 0o311)
    try:
        yield config, fault
    finally:
        if folder.is_dir():
            folder.chmod(0o755)
            if kb_yaml.exists():
                kb_yaml.chmod(0o644)


BROKEN = [(f, s) for f in FOLDER_FAULTS for s in KB_SOURCES]
BROKEN_IDS = [f"{f}-{s}" for f, s in BROKEN]


def _unreadable_issues(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i["rule"] == "kb_unreadable"]


@pytest.mark.parametrize("broken_plain", BROKEN, ids=BROKEN_IDS, indirect=True)
def test_ci_json_fails_a_kb_whose_files_cannot_be_read(broken_plain):
    config, fault = broken_plain
    result = _invoke(config, ["ci", "--severity", "error", "--format", "json"])
    body = json.loads(result.stdout)
    assert result.exit_code == 1, result.stdout + result.stderr
    assert body["result"] == "fail"
    by_kb = {kb["kb_name"]: kb for kb in body["kbs"]}
    assert set(by_kb) == {JUDGED, PLAIN}, body  # the other KB's result is kept
    (issue,) = _unreadable_issues(by_kb[PLAIN]["issues"])
    assert issue["severity"] == "error"
    assert FOLDER_FAULTS[fault] in issue["message"]
    assert "did not run" in issue["message"]
    assert _unreadable_issues(by_kb[JUDGED]["issues"]) == []


@pytest.mark.parametrize("broken_plain", BROKEN, ids=BROKEN_IDS, indirect=True)
def test_ci_text_fails_a_kb_whose_files_cannot_be_read(broken_plain):
    config, _fault = broken_plain
    result = _invoke(config, ["ci", "--severity", "error"])
    assert result.exit_code == 1, result.stdout + result.stderr
    assert f"{JUDGED}:" in result.stdout and f"{PLAIN}:" in result.stdout
    assert "did not run" in result.stdout
    assert "Result: FAIL" in result.stdout


@pytest.mark.parametrize("broken_plain", BROKEN, ids=BROKEN_IDS, indirect=True)
@pytest.mark.parametrize("scope", ["kb", "entry", "all"])
def test_qa_validate_reports_a_kb_whose_files_cannot_be_read(broken_plain, scope):
    config, _fault = broken_plain
    args = {"kb": [PLAIN], "entry": [PLAIN, "--entry", "solo"], "all": []}[scope]
    result = _invoke(config, ["qa", "validate", *args, "--format", "json"])
    body = json.loads(result.stdout)
    assert len(_unreadable_issues(body["issues"])) == 1, result.stdout + result.stderr


@pytest.mark.parametrize("broken_plain", BROKEN, ids=BROKEN_IDS, indirect=True)
@pytest.mark.parametrize(
    "surface", ["cli-assess-entry", "cli-assess-kb", "mcp-assess-entry", "mcp-assess-kb"]
)
@pytest.mark.parametrize("tier", QA_TIERS)
def test_assess_refuses_a_kb_whose_files_cannot_be_read(broken_plain, surface, tier):
    """No record can be read back or written there: refused, not a crash, and a
    missing folder is not recreated to hold a record."""
    config, fault = broken_plain
    config.settings.ai_provider, config.settings.ai_api_key = "anthropic", "k"
    folder = (config.get_kb(PLAIN) or config.get_kb(JUDGED)).path.parent / PLAIN
    with _model(FakeAnthropic("passes")):
        refused, code, body = run_surface(
            surface, config, kb=PLAIN, tier=tier, entry="solo" if "entry" in surface else None
        )
    assert refused, body
    assert code == "KB_UNREADABLE", body
    # A missing folder is not recreated to hold a record.
    assert folder.exists() == (fault != "missing")
