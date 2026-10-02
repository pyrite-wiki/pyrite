"""What orient says about a KB is true of that KB, on every surface (#66, #232).

Four properties, each asserted on the surface where its defect could appear:

1. **A bad KB name is `KB_NOT_FOUND`** over MCP, the CLI and REST (ADR-0037).
   MCP carried two other codes for it: `OPERATION_FAILED` from `_kb_orient`'s
   own handler (a caller with no readable set) and `NOT_FOUND` from the
   dispatcher's scoping refusal (a caller with one). Each keeps its old code
   in `legacy_error_code` for one release.
2. **`kb_orient` with no name is a valid first call**: the KBs this caller may
   read, and nothing about any other.
3. **`detail="brief"`** omits the write-side blocks and says how to get them;
   absent is `full` is what it was; anything else is a validation error.
4. **Orient and the write path share one rule for which types a KB has**
   (`KBSchema.declared_types`), so orient cannot list a type `create` refuses.

**Why the scoped cases drive a real MCP session.** As in
`tests/test_mcp_read_scoping.py`: the readable set has to *reach* the handler
on the path a connection takes. These build the SDK server the way
`mcp_routes.handle_sse` does (the access policy's `read_scope` for the
principal, passed per connection to `build_sdk_server`) and call it through a
real `ClientSession`. The SSE socket itself is not opened.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
import yaml
from sqlalchemy import event, text as sql_text
from typer.testing import CliRunner

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.exceptions import UndeclaredTypeError, ValidationError
from pyrite.schema.core_types import CORE_TYPES
from pyrite.server.mcp_server import PyriteMCPServer
from pyrite.services.access_policy import AccessPolicy, Principal
from pyrite.services.auth_service import AuthService
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB
from tests.auth_seed import seed_user

WRITE_SIDE_KEYS = ("ai_instructions", "evaluation_rubric", "guidelines", "goals")
ABSENT = "no-such-kb-at-all"
#: What the dispatcher's refusal says to do next: one string, for an absent and
#: an unreadable name alike, and no KB name in it (round 1, item 4).
SCOPED_REFUSAL_HINT = "Call kb_orient with no kb_name to list the KBs you can read."


def _presets() -> dict[str, dict]:
    """Every template `pyrite init -t` can build: built-in and plugin presets,
    resolved the way `init_kb` resolves them (a plugin preset wins on name)."""
    from pyrite.cli.init_command import BUILTIN_TEMPLATES
    from pyrite.plugins import get_registry

    presets = dict(BUILTIN_TEMPLATES)
    presets.update(get_registry().get_all_kb_presets())
    return presets


PRESETS = _presets()
DECLARING = sorted(name for name, p in PRESETS.items() if p.get("types"))


def _make_kb(root, name, preset=None, **kb_kwargs) -> KBConfig:
    """A KB directory with the kb.yaml `pyrite init` writes for `preset`
    (none at all when `preset` is None)."""
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    if preset is not None:
        (path / "kb.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": name,
                    "description": preset.get("description", ""),
                    "types": preset.get("types", {}),
                    "policies": preset.get("policies", {}),
                    "validation": preset.get("validation", {}),
                }
            )
        )
    kb = KBConfig(name=name, path=path, kb_type=kb_kwargs.pop("kb_type", "generic"), **kb_kwargs)
    kb.load_kb_yaml()
    return kb


class World:
    def __init__(self, tmp_path, kbs: list[KBConfig], *, auth: bool = False):
        self.tmp = tmp_path
        settings = Settings(index_path=tmp_path / "index.db")
        if auth:
            settings = Settings(
                index_path=tmp_path / "index.db",
                auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
            )
        self.config = PyriteConfig(knowledge_bases=kbs, settings=settings)
        self.db = PyriteDB(self.config.settings.index_path)
        self.svc = KBService(self.config, self.db)
        self._servers: dict[str, PyriteMCPServer] = {}

    def server(self, tier="read") -> PyriteMCPServer:
        if tier not in self._servers:
            self._servers[tier] = PyriteMCPServer(config=self.config, tier=tier)
        return self._servers[tier]

    def readable_for(self, user) -> set[str] | None:
        """The set `mcp_routes._resolve_bearer_auth` computes for a session user."""
        principal = Principal.user(user["id"], user["role"])
        return AccessPolicy(self.config, self.db).read_scope(principal).as_set()

    def mcp(self, tool, arguments=None, *, readable=None, tier="read"):
        """One real `tools/call` round trip; `readable=None` is the unscoped
        caller (local stdio, an operator API key)."""
        from mcp.shared.memory import create_connected_server_and_client_session

        sdk = self.server(tier).build_sdk_server(client_id="t", readable_kbs=readable)

        async def _run():
            async with create_connected_server_and_client_session(sdk) as session:
                result = await session.call_tool(tool, arguments or {})
                if result.isError:
                    # The SDK's own refusal (input-schema validation): not JSON.
                    return {"sdk_error": result.content[0].text}
                return json.loads(result.content[0].text)

        return asyncio.run(_run())

    def dispatch(self, tool, arguments=None, *, readable=None, tier="read"):
        """The dispatcher called directly: what a caller gets when the SDK's
        input-schema validation is not in front of it."""
        return self.server(tier)._dispatch_tool(tool, arguments or {}, readable_kbs=readable)

    def cli(self, monkeypatch, *argv):
        import pyrite.cli.context as ctx
        from pyrite.cli import app

        monkeypatch.setattr(
            ctx,
            "get_config_and_db",
            lambda config=None: (self.config, PyriteDB(self.config.settings.index_path)),
        )
        return CliRunner().invoke(app, list(argv))

    def rest(self):
        from fastapi.testclient import TestClient

        from pyrite.server.api import create_app, get_config, get_db

        app = create_app(config=self.config)
        app.dependency_overrides[get_config] = lambda: self.config
        app.dependency_overrides[get_db] = lambda: self.db
        return TestClient(app)

    def close(self):
        for s in self._servers.values():
            s.close()
        self.db.close()


@pytest.fixture
def make_world(tmp_path):
    made: list[World] = []

    def _make(kbs_spec, **kw):
        kbs = [
            spec if isinstance(spec, KBConfig) else _make_kb(tmp_path, spec[0], spec[1])
            for spec in kbs_spec
        ]
        w = World(tmp_path, kbs, **kw)
        made.append(w)
        return w

    yield _make
    for w in made:
        w.close()


@pytest.fixture
def one_kb(make_world):
    w = make_world([("notes", None)])
    w.svc.create_entry("notes", "first", "First", "note", "hello")
    return w


PUBLIC, PRIVATE = "field-notes", "field-nodes"  # one edit apart, on purpose


@pytest.fixture
def scoped(tmp_path, make_world):
    """A public KB, a private KB whose name is a near match of it, and a peer
    who may read only the public one."""
    w = make_world(
        [
            _make_kb(tmp_path, PUBLIC, None, default_role="read"),
            _make_kb(tmp_path, PRIVATE, None, default_role="none"),
        ],
        auth=True,
    )
    w.svc.create_entry(PUBLIC, "open", "Open", "note", "in the open")
    w.svc.create_entry(PRIVATE, "secret", "Secret", "note", "behind the wall")
    auth = AuthService(w.db, w.config.settings.auth)
    seed_user(w.db, "peer", "password123", role="read")
    seed_user(w.db, "nobody", "password123", role="read")
    users = {u["username"]: u for u in auth.list_users()}
    w.peer = w.readable_for(users["peer"])
    assert w.peer == {PUBLIC}, "fixture: the peer must read the public KB only"
    return w


# ---------------------------------------------------------------------------
# 4. One rule for which types a KB has
# ---------------------------------------------------------------------------


def _try_create(svc, kb, entry_type):
    """Create one entry of `entry_type` the strict way (no allow-undeclared).

    Returns the refusal, or None when the type was accepted. A refusal that is
    about something other than the type (a required field this minimal spec
    lacks) means the type itself was accepted.
    """
    try:
        svc.create_entry(
            kb,
            f"probe-{entry_type}".replace("_", "-"),
            f"Probe {entry_type}",
            entry_type,
            "body",
            allow_undeclared=False,
            date="2024-01-01",
        )
    except UndeclaredTypeError as e:
        return e
    except ValidationError:
        return None
    return None


class TestOrientListsTheTypesCreateAccepts:
    @pytest.mark.parametrize("template", DECLARING)
    def test_every_listed_type_is_accepted_and_no_other_core_type_is_listed(
        self, make_world, template
    ):
        w = make_world([("kb", PRESETS[template])])
        declared = set(PRESETS[template]["types"])
        listed = set(w.svc.orient("kb")["schema"]["types"])

        assert listed == declared, (
            f"{template}: orient lists {sorted(listed - declared)} which the KB does not "
            f"declare, and omits {sorted(declared - listed)}"
        )
        for t in sorted(listed):
            assert _try_create(w.svc, "kb", t) is None, f"orient lists '{t}'; create refuses it"
        for t in sorted(set(CORE_TYPES) - listed):
            assert isinstance(_try_create(w.svc, "kb", t), UndeclaredTypeError), (
                f"'{t}' is absent from orient and create accepted it"
            )

    def test_the_software_template_does_not_offer_note(self, make_world):
        """The groom's failing case: an agent told to trust orient picks the
        most general type it offers, and `create` refuses it."""
        w = make_world([("sw", PRESETS["software"])])
        assert "note" not in w.svc.orient("sw")["schema"]["types"]

    @pytest.mark.control(reason="a KB with no types: declares no vocabulary; it must not narrow")
    @pytest.mark.parametrize("preset", [None, PRESETS["empty"]], ids=["no-kb-yaml", "empty"])
    def test_a_kb_that_declares_no_types_still_lists_every_core_type(self, make_world, preset):
        w = make_world([("kb", preset)])
        listed = set(w.svc.orient("kb")["schema"]["types"])
        assert listed == set(CORE_TYPES)
        for t in sorted(listed):
            assert _try_create(w.svc, "kb", t) is None

    def test_kb_schema_rest_schema_and_orient_agree(self, make_world):
        w = make_world([("sw", PRESETS["software"])])
        declared = set(PRESETS["software"]["types"])
        assert set(w.mcp("kb_schema", {"kb_name": "sw"})["types"]) == declared
        assert set(w.rest().get("/api/kbs/sw/schema").json()["types"]) == declared
        assert set(w.mcp("kb_orient", {"kb_name": "sw"})["schema"]["types"]) == declared

    def test_the_rule_lives_in_one_place(self, make_world):
        """`declared_types` is what the write refusal, `to_agent_schema` and
        the web type picker's route read; an empty answer means "no
        vocabulary declared", never "nothing allowed"."""
        w = make_world([("sw", PRESETS["software"]), ("plain", None)])
        assert w.config.get_kb("sw").kb_schema.declared_types() == sorted(
            PRESETS["software"]["types"]
        )
        assert w.config.get_kb("plain").kb_schema.declared_types() == []
        body = w.rest().get("/api/entries/type-schemas", params={"kb": "sw"}).json()
        assert body["declared"] == sorted(PRESETS["software"]["types"])


# ---------------------------------------------------------------------------
# 1. A bad name is KB_NOT_FOUND
# ---------------------------------------------------------------------------


class TestABadNameIsKBNotFound:
    def test_mcp_unscoped_caller(self, one_kb):
        out = one_kb.mcp("kb_orient", {"kb_name": "nope"})
        assert out["error_code"] == "KB_NOT_FOUND", out
        assert out["retryable"] is False
        assert out["legacy_error_code"] == "OPERATION_FAILED", (
            "the code orient reported before this release, kept one release (ADR-0037)"
        )

    def test_mcp_scoped_caller_over_the_http_wiring(self, scoped):
        out = scoped.mcp("kb_orient", {"kb_name": ABSENT}, readable=scoped.peer)
        assert out["error_code"] == "KB_NOT_FOUND", out
        assert out["legacy_error_code"] == "NOT_FOUND", (
            "the code the scoping refusal reported before this release"
        )

    @pytest.mark.control(reason="the CLI already answered KB_NOT_FOUND, exit 1; pinned beside MCP")
    def test_cli(self, one_kb, monkeypatch):
        result = one_kb.cli(monkeypatch, "orient", "-k", "nope")
        assert result.exit_code == 1
        assert json.loads(result.output)["error_code"] == "KB_NOT_FOUND"

    @pytest.mark.control(reason="REST already answered 404 KB_NOT_FOUND; pinned beside the others")
    def test_rest(self, one_kb):
        r = one_kb.rest().get("/api/kbs/nope/orient")
        assert r.status_code == 404
        assert "KB_NOT_FOUND" in r.text


class TestUnreadableAndAbsentAreOneAnswer:
    """For one caller and one tool, a KB the caller may not read and a KB that
    does not exist get the same bytes; otherwise the answer is an oracle for
    private KB names."""

    @staticmethod
    def _same(a, b, name_a, name_b):
        return json.dumps(a, sort_keys=True).replace(name_a, "<kb>") == json.dumps(
            b, sort_keys=True
        ).replace(name_b, "<kb>")

    @pytest.mark.control(reason="held before this change (both NOT_FOUND); must still hold")
    def test_kb_orient_over_the_http_wiring(self, scoped):
        refused = scoped.mcp("kb_orient", {"kb_name": PRIVATE}, readable=scoped.peer)
        absent = scoped.mcp("kb_orient", {"kb_name": ABSENT}, readable=scoped.peer)
        assert "error" in refused
        assert self._same(refused, absent, PRIVATE, ABSENT), (refused, absent)

    @pytest.mark.control(reason="held before this change; the refusal's code moved for every tool")
    def test_every_tool_that_names_a_kb(self, scoped):
        """A scoped caller never reaches a handler with a KB outside its set,
        so the handler's own not-found answer cannot be compared with the
        refusal by one caller. Walked over every tool rather than argued."""
        server = scoped.server("admin")
        diverged = []
        for tool in sorted(server.tools):
            for key in ("kb_name", "kb"):
                a = server._dispatch_tool(
                    tool, {key: PRIVATE}, readable_kbs=scoped.peer, writable_kbs=set()
                )
                b = server._dispatch_tool(
                    tool, {key: ABSENT}, readable_kbs=scoped.peer, writable_kbs=set()
                )
                if not self._same(a, b, PRIVATE, ABSENT):
                    diverged.append((tool, key, a, b))
        assert not diverged, diverged

    def test_the_refusal_is_the_contract_code(self, scoped):
        out = scoped.mcp("kb_list_entries", {"kb_name": PRIVATE}, readable=scoped.peer)
        assert out == {
            "error": f"KB '{PRIVATE}' not found",
            "error_code": "KB_NOT_FOUND",
            "retryable": False,
            "suggestion": SCOPED_REFUSAL_HINT,
            "legacy_error_code": "NOT_FOUND",
        }


# ---------------------------------------------------------------------------
# The suggestion
# ---------------------------------------------------------------------------


class TestTheSuggestionNamesOnlyWhatTheCallerMayRead:
    def test_a_near_match_is_offered(self, one_kb):
        out = one_kb.mcp("kb_orient", {"kb_name": "note"})
        assert out["did_you_mean"] == ["notes"], out
        assert "notes" in out["suggestion"]

    def test_no_candidate_is_an_empty_list_not_a_missing_key(self, one_kb):
        out = one_kb.mcp("kb_orient", {"kb_name": "zzzzzzzz"})
        assert out["did_you_mean"] == []
        assert out["suggestion"], "the hint still says how to list KBs"
        assert "notes" not in json.dumps(out)

    def test_it_is_capped(self, make_world):
        w = make_world([(f"kb-{i:02d}", None) for i in range(54)])
        out = w.mcp("kb_orient", {"kb_name": "kb-"})
        assert 0 < len(out["did_you_mean"]) <= 3, out

    @pytest.mark.control(reason="the dispatcher refuses first; no names today, none after")
    def test_a_near_match_the_caller_cannot_read_is_never_named(self, scoped):
        # One edit from PRIVATE, and not PUBLIC either.
        out = scoped.mcp("kb_orient", {"kb_name": "field-node"}, readable=scoped.peer)
        assert "error" in out
        assert PRIVATE not in json.dumps(out), out

    def test_the_service_draws_only_from_the_readable_set(self, scoped):
        """The rule itself, below the dispatcher: were a scoped caller ever to
        reach it, the private name still could not come out."""
        names, hint = scoped.svc.kb_name_suggestions("field-node", readable_kbs={PUBLIC})
        assert names == [PUBLIC]
        assert PRIVATE not in hint
        names, _ = scoped.svc.kb_name_suggestions("field-node", readable_kbs=set())
        assert names == []

    def test_cli_and_stdio_agree(self, one_kb, monkeypatch):
        mcp = one_kb.mcp("kb_orient", {"kb_name": "note"})
        cli = json.loads(one_kb.cli(monkeypatch, "orient", "-k", "note").output)
        for key in ("error", "error_code", "retryable", "suggestion", "did_you_mean"):
            assert cli[key] == mcp[key], key


# ---------------------------------------------------------------------------
# 2. No name is a valid first call
# ---------------------------------------------------------------------------


class TestOrientWithNoNameIsTheFirstCall:
    def test_the_schema_no_longer_requires_a_name(self, one_kb):
        schema = one_kb.server().tools["kb_orient"]["inputSchema"]
        assert "kb_name" not in schema.get("required", [])

    def test_zero_kbs(self, make_world):
        out = make_world([]).mcp("kb_orient")
        assert out["knowledge_bases"] == []
        assert "operational_contracts" in out and out["next"]

    def test_one_kb_answers_with_the_registry_not_that_kb(self, one_kb):
        out = one_kb.mcp("kb_orient")
        assert [k["name"] for k in out["knowledge_bases"]] == ["notes"]
        assert out["knowledge_bases"][0]["entry_count"] == 1
        assert "schema" not in out, "one shape for the no-name call, whatever the KB count"

    def test_many(self, make_world):
        w = make_world([(f"kb-{i}", None) for i in range(5)])
        assert len(w.mcp("kb_orient")["knowledge_bases"]) == 5

    def test_a_scoped_caller_sees_only_its_readable_kbs(self, scoped):
        out = scoped.mcp("kb_orient", readable=scoped.peer)
        assert [k["name"] for k in out["knowledge_bases"]] == [PUBLIC], out
        assert PRIVATE not in json.dumps(out)

    def test_a_caller_who_can_read_nothing_gets_an_empty_list_not_a_refusal(self, scoped):
        out = scoped.mcp("kb_orient", readable=set())
        assert out.get("knowledge_bases") == [], out
        assert "field-" not in json.dumps(out)

    def test_cli_with_no_kb_is_the_same_answer(self, one_kb, monkeypatch):
        result = one_kb.cli(monkeypatch, "orient")
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == one_kb.mcp("kb_orient")

    def test_detail_with_no_name_is_accepted_and_changes_nothing(self, one_kb):
        brief = one_kb.mcp("kb_orient", {"detail": "brief"})
        assert "knowledge_bases" in brief
        assert brief == one_kb.mcp("kb_orient")

    def test_an_invalid_detail_with_no_name_is_still_refused(self, one_kb):
        assert one_kb.dispatch("kb_orient", {"detail": "bogus"})["error_code"] == (
            "VALIDATION_FAILED"
        )
        assert "sdk_error" in one_kb.mcp("kb_orient", {"detail": "bogus"})

    def test_two_callers_on_one_server_instance_get_their_own_answers(self, scoped):
        """The readable set is per call; were it state on the shared, per-tier
        server, interleaved callers would see each other's KBs."""
        server = scoped.server()
        seen: dict[str, set[frozenset]] = {"peer": set(), "all": set()}
        start = threading.Barrier(2)

        def run(label, readable):
            start.wait(timeout=10)
            for _ in range(40):
                out = server._dispatch_tool("kb_orient", {}, readable_kbs=readable)
                seen[label].add(frozenset(k["name"] for k in out["knowledge_bases"]))

        threads = [
            threading.Thread(target=run, args=("peer", {PUBLIC})),
            threading.Thread(target=run, args=("all", {PUBLIC, PRIVATE})),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert seen["peer"] == {frozenset({PUBLIC})}
        assert seen["all"] == {frozenset({PUBLIC, PRIVATE})}


# ---------------------------------------------------------------------------
# 3. detail
# ---------------------------------------------------------------------------


@pytest.fixture
def sw(make_world):
    w = make_world([("sw", PRESETS["software"])])
    w.svc.create_entry("sw", "adr-one", "One", "adr", "body")
    return w


def _write_side_keys_in(schema: dict) -> list[str]:
    found = [k for k in WRITE_SIDE_KEYS if k in schema]
    for name, info in schema.get("types", {}).items():
        found += [f"{name}.{k}" for k in WRITE_SIDE_KEYS if k in info]
    return found


class TestDetail:
    @pytest.mark.control(reason="the compatibility line: no detail is full is today's response")
    def test_absent_is_full(self, sw):
        assert sw.mcp("kb_orient", {"kb_name": "sw"}) == sw.mcp(
            "kb_orient", {"kb_name": "sw", "detail": "full"}
        )
        full = sw.svc.orient("sw")
        assert "detail" not in full and "detail_note" not in full
        assert _write_side_keys_in(full["schema"]), "fixture: full must carry what brief drops"

    def test_brief_omits_the_write_side_blocks_and_says_how_to_get_them(self, sw):
        full = sw.mcp("kb_orient", {"kb_name": "sw"})
        brief = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})
        assert _write_side_keys_in(brief["schema"]) == []
        assert brief["detail"] == "brief"
        assert "kb_schema" in brief["detail_note"] and "full" in brief["detail_note"]
        for key in ("kb", "total_entries", "types", "top_tags", "recent", "operational_contracts"):
            assert brief[key] == full[key], key
        assert set(brief["schema"]["types"]) == set(full["schema"]["types"])
        assert len(json.dumps(brief)) < len(json.dumps(full))

    def test_brief_says_the_plugin_supplement_is_not_bounded(self, sw):
        note = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})["detail_note"]
        assert "plugin" in note.lower()

    @pytest.mark.parametrize("bad", ["bogus", "", "BRIEF", 1, ["brief"]])
    def test_an_invalid_detail_is_a_validation_error_over_mcp(self, sw, bad):
        """Two layers, both refusing. A real session is stopped by the SDK's
        input-schema validation (the `enum`), in the SDK's own shape, as for
        every enum argument of every tool. Behind it the service refuses in
        the error contract, which is what the CLI and REST reach."""
        out = sw.dispatch("kb_orient", {"kb_name": "sw", "detail": bad})
        assert out.get("error_code") == "VALIDATION_FAILED", out
        assert "brief" in out["error"] and "full" in out["error"]
        assert out["retryable"] is False

        session = sw.mcp("kb_orient", {"kb_name": "sw", "detail": bad})
        assert "sdk_error" in session, session
        assert "schema" not in session

    def test_an_invalid_detail_is_a_validation_error_on_the_cli(self, sw, monkeypatch):
        result = sw.cli(monkeypatch, "orient", "-k", "sw", "--detail", "bogus")
        assert result.exit_code == 1
        assert json.loads(result.output)["error_code"] == "VALIDATION_FAILED"

    def test_an_invalid_detail_is_a_validation_error_over_rest(self, sw):
        r = sw.rest().get("/api/kbs/sw/orient", params={"detail": "bogus"})
        assert r.status_code == 422
        assert "VALIDATION_FAILED" in r.text

    @pytest.mark.control(reason="before `detail` existed a null was ignored; it must stay 'absent'")
    def test_a_null_detail_is_absent(self, sw):
        """A null reaching the service is "not given" (REST's absent query
        parameter, the CLI's absent flag)."""
        assert sw.dispatch("kb_orient", {"kb_name": "sw", "detail": None}) == sw.dispatch(
            "kb_orient", {"kb_name": "sw"}
        )

    def test_brief_on_the_cli_and_over_rest(self, sw, monkeypatch):
        want = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})
        cli = json.loads(sw.cli(monkeypatch, "orient", "-k", "sw", "--detail", "brief").output)
        rest = sw.rest().get("/api/kbs/sw/orient", params={"detail": "brief"}).json()
        assert cli == want
        assert rest == want

    def test_the_tool_schema_declares_detail(self, sw):
        prop = sw.server().tools["kb_orient"]["inputSchema"]["properties"]["detail"]
        # null is allowed beside the two values (TestTheFirstCallSurvivesAClientThatSendsNull)
        assert [v for v in prop["enum"] if v is not None] == ["brief", "full"]

    def test_read_and_write_tiers_answer_alike(self, sw):
        args = {"kb_name": "sw", "detail": "brief"}
        read = sw.mcp("kb_orient", args, tier="read")
        assert read["detail"] == "brief"
        assert read == sw.mcp("kb_orient", args, tier="write")


class TestAPluginSupplementSurvivesBothDetails:
    def test_the_supplement_is_whole_under_brief(self, sw, monkeypatch):
        from pyrite.plugins.registry import PluginRegistry

        supplement = {"board": {"lanes": ["Backlog", "Done"]}, "ai_instructions": "plugin's own"}
        monkeypatch.setattr(
            PluginRegistry, "get_orient_supplements", lambda self, kb, kb_type: dict(supplement)
        )
        for detail in ("full", "brief"):
            out = sw.svc.orient("sw", detail=detail)
            for key, value in supplement.items():
                assert out[key] == value, (detail, key)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class TestOrientAnswersInABrokenEnvironment:
    @pytest.mark.control(reason="existing behaviour the detail and declared-types work must keep")
    def test_a_kb_whose_directory_is_gone(self, one_kb):
        import shutil

        shutil.rmtree(one_kb.config.get_kb("notes").path)
        for detail in ("full", "brief"):
            out = one_kb.svc.orient("notes", detail=detail)
            assert out["kb"] == "notes"
        assert [k["name"] for k in one_kb.mcp("kb_orient")["knowledge_bases"]] == ["notes"]

    def test_brief_when_the_schema_could_not_be_read(self, one_kb, monkeypatch):
        """`orient` logs and carries on with an empty schema when the
        conversion raises; brief must not turn that into a crash."""
        from pyrite.schema.kb_schema import KBSchema

        def boom(self):
            raise RuntimeError("schema error")

        monkeypatch.setattr(KBSchema, "to_agent_schema", boom)
        out = one_kb.svc.orient("notes", detail="brief")
        assert out["schema"] == {} and out["detail"] == "brief"


# ---------------------------------------------------------------------------
# Review round 1
# ---------------------------------------------------------------------------

OMITTED_BY_BRIEF = ("ai_instructions", "evaluation_rubric", "guidelines", "goals")


class TestBriefPointsAtWhatReturnsTheOmittedBlocks:
    """Item 1: every command `detail_note` names returns what brief omitted."""

    def _note(self, sw):
        return sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})["detail_note"]

    def test_every_cli_command_the_note_names_returns_the_omitted_blocks(self, sw, monkeypatch):
        import re
        import shlex

        commands = re.findall(r"`(pyrite [^`]+)`", self._note(sw))
        assert commands, "the note must name a CLI command, in backticks"
        for command in commands:
            argv = shlex.split(command.replace("<kb>", "sw"))[1:]
            result = sw.cli(monkeypatch, *argv)
            assert result.exit_code == 0, (command, result.output)
            for block in ("evaluation_rubric", "guidelines"):
                assert block in result.output, (command, block)

    @pytest.mark.control(reason="kb_schema already returned them; pinned beside the CLI command")
    def test_the_mcp_tool_the_note_names_returns_them(self, sw):
        assert "kb_schema" in self._note(sw)
        out = sw.mcp("kb_schema", {"kb_name": "sw"})
        assert "evaluation_rubric" in out and "guidelines" in out


class TestTheFirstCallSurvivesAClientThatSendsNull:
    """Item 2: validated against the published inputSchema, as the SDK does."""

    @staticmethod
    def _validate(sw, arguments):
        import jsonschema

        schema = sw.server().tools["kb_orient"]["inputSchema"]
        jsonschema.validate(arguments, schema)

    @pytest.mark.parametrize(
        "args", [{"kb_name": None}, {"detail": None}, {"kb_name": None, "detail": None}]
    )
    def test_null_arguments_validate_against_the_published_schema(self, sw, args):
        self._validate(sw, args)

    @pytest.mark.control(reason="the schema refused these before null was allowed; it must still")
    @pytest.mark.parametrize("args", [{"detail": "bogus"}, {"kb_name": 123}, {"recent_limit": "x"}])
    def test_the_schema_still_refuses_what_it_refused(self, sw, args):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            self._validate(sw, args)

    def test_a_null_kb_name_answers_as_if_absent_over_a_real_session(self, sw):
        assert sw.mcp("kb_orient", {"kb_name": None}) == sw.mcp("kb_orient")
        assert "knowledge_bases" in sw.mcp("kb_orient", {"kb_name": None})

    def test_a_null_detail_answers_as_if_absent_over_a_real_session(self, sw):
        assert sw.mcp("kb_orient", {"kb_name": "sw", "detail": None}) == sw.mcp(
            "kb_orient", {"kb_name": "sw"}
        )


class TestAKbNameThatIsNotAStringIsRefused:
    """Item 3: not read as "no name"."""

    @pytest.mark.parametrize("bad", [123, 0, True, {"x": 1}, ["sw"]])
    def test_unscoped(self, sw, bad):
        out = sw.dispatch("kb_orient", {"kb_name": bad})
        assert out.get("error_code") == "VALIDATION_FAILED", out
        assert out["retryable"] is False
        assert "knowledge_bases" not in out

    @pytest.mark.parametrize("bad", [123, 0, True, {"x": 1}, [PUBLIC]])
    def test_scoped(self, scoped, bad):
        out = scoped.dispatch("kb_orient", {"kb_name": bad}, readable=scoped.peer)
        assert out.get("error_code") == "VALIDATION_FAILED", out
        assert "knowledge_bases" not in out

    @pytest.mark.control(reason="a blank string is already 'no name' (named_kb, private #74)")
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_string_is_still_no_name(self, sw, blank):
        assert "knowledge_bases" in sw.dispatch("kb_orient", {"kb_name": blank})


class TestTheScopedRefusalSaysWhatToDoNext:
    """Item 4: one static hint, the same for an absent and an unreadable name."""

    def test_the_hint_is_the_same_string_for_both_and_names_no_kb(self, scoped):
        refused = scoped.mcp("kb_orient", {"kb_name": PRIVATE}, readable=scoped.peer)
        absent = scoped.mcp("kb_orient", {"kb_name": ABSENT}, readable=scoped.peer)
        assert refused["suggestion"] == absent["suggestion"] == SCOPED_REFUSAL_HINT
        assert PRIVATE not in json.dumps(refused).replace(f"KB '{PRIVATE}' not found", "")
        assert "did_you_mean" not in refused and "did_you_mean" not in absent

    def test_every_tool_refuses_with_the_hint(self, scoped):
        server = scoped.server("admin")
        for tool in sorted(server.tools):
            for key in ("kb_name", "kb"):
                out = server._dispatch_tool(
                    tool, {key: PRIVATE}, readable_kbs=scoped.peer, writable_kbs=set()
                )
                if out.get("error_code") == "KB_NOT_FOUND":
                    assert out["suggestion"] == SCOPED_REFUSAL_HINT, (tool, key)

    def test_the_changelog_does_not_promise_scoped_callers_near_matches(self):
        import pathlib

        text = pathlib.Path("changelog.d/66-orient-true-as-first-call.changed.md").read_text()
        flat = " ".join(text.split())
        assert "Names are drawn only from the KBs the caller may read" not in flat
        assert "never from a KB the caller may not read" in flat
        assert "scoped caller" in flat and "static hint" in flat


class TestBriefAgreesWithItself:
    """Item 5: no top-level `guidelines` that contradicts the dropped block."""

    def test_no_top_level_guidelines_under_brief(self, sw):
        brief = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})
        assert "guidelines" not in brief and "guidelines" not in brief["schema"]

    def test_a_configured_guideline_is_not_hidden_behind_an_empty_one(self, sw):
        sw.config.get_kb("sw").guidelines = {"tone": "plain"}
        full = sw.mcp("kb_orient", {"kb_name": "sw"})
        brief = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})
        assert full["guidelines"] == {"tone": "plain"}
        assert "guidelines" not in brief
        assert "guidelines" in brief["detail_note"]


class TestBriefKeepsTheRelationNames:
    """Item 6: a read session following links needs `relationship_types`."""

    def test_relationship_types_survive_brief(self, sw):
        full = sw.mcp("kb_orient", {"kb_name": "sw"})["schema"]
        brief = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})
        assert full["relationship_types"]
        assert brief["schema"]["relationship_types"] == full["relationship_types"]

    def test_nothing_says_brief_drops_them(self, sw):
        brief = sw.mcp("kb_orient", {"kb_name": "sw", "detail": "brief"})
        desc = sw.server().tools["kb_orient"]["description"]
        import re

        for text in (brief["detail_note"], desc):
            omitted = re.search(r"omits ([^.]*)\.", text).group(1)
            assert "relationship_types" not in omitted, text


class TestAPluginCannotOverwriteTheDetailMarkers:
    """Item 9: `detail` and `detail_note` are set after the supplements."""

    def test_brief_markers_win_over_a_supplement(self, sw, monkeypatch):
        from pyrite.plugins.registry import PluginRegistry

        monkeypatch.setattr(
            PluginRegistry,
            "get_orient_supplements",
            lambda self, kb, kb_type: {"detail": "plugin", "detail_note": "plugin", "board": 1},
        )
        out = sw.svc.orient("sw", detail="brief")
        assert out["detail"] == "brief"
        assert out["detail_note"].startswith("brief omits")
        assert out["board"] == 1


def test_no_name_overview_is_bounded_paginated_and_uses_bounded_queries(make_world):
    world = make_world([(f"kb-{i:03d}", None) for i in range(60)])
    server = world.server()
    engine = server.db.session.get_bind()
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        first = world.mcp("kb_orient")
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
    assert len(selects) <= 3
    assert len(first["knowledge_bases"]) == 50
    assert first["limit"] == 50
    assert first["offset"] == 0
    assert first["has_more"] is True

    second = world.mcp("kb_orient", {"limit": 50, "offset": 50})
    assert len(second["knowledge_bases"]) == 10
    assert second["offset"] == 50
    assert second["has_more"] is False
    scoped = world.mcp("kb_orient", {"limit": 1}, readable={"kb-059"})
    assert [kb["name"] for kb in scoped["knowledge_bases"]] == ["kb-059"]
    assert scoped["has_more"] is False
    assert {kb["name"] for kb in first["knowledge_bases"]}.isdisjoint(
        kb["name"] for kb in second["knowledge_bases"]
    )


def test_no_name_overview_matches_kb_list_registry_type_and_count(make_world):
    world = make_world([("notes", None)])
    world.svc.create_entry("notes", "first", "First", "note", "hello")
    world.mcp("kb_list")
    server = world.server()
    server.db.session.execute(
        sql_text("UPDATE kb SET kb_type = :kb_type, entry_count = :entry_count WHERE name = :name"),
        {"kb_type": "project", "entry_count": 17, "name": "notes"},
    )
    server.db.session.commit()

    listed = world.mcp("kb_list")["knowledge_bases"][0]
    oriented = world.mcp("kb_orient")["knowledge_bases"][0]

    assert oriented["type"] == listed["type"] == "project"
    assert oriented["entry_count"] == listed["entry_count"] == 1
