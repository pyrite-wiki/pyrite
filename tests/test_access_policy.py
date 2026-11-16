"""The access policy: one framework-free module owns every access rule (ADR-0037 §1, #383).

Medium tests: a real ``PyriteDB`` and ``AuthService`` under a real
``PyriteConfig``, no web framework. The REST, MCP and ``/ws`` suites prove
each surface still asks this module and answers as before; these pin the
rule itself -- the ladder, the API-key role, the KB default role (config,
then the registry, confined under an untrusted config), the per-KB walk
(grant, default role, ``global_access``, the self-registered read cap, the
anonymous ceiling), the readable and writable sets, and concealment.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBNotFoundError
from pyrite.services import access_policy
from pyrite.services.access_policy import (
    FORBIDDEN,
    NOT_FOUND,
    ROLE_LEVELS,
    ROLES,
    UNAUTHENTICATED,
    AccessPolicy,
    Action,
    AnyKB,
    Decision,
    Instance,
    KB,
    PolicyDeniedError,
    Principal,
    ReadScope,
    Row,
    kb_role,
    lower_role,
    resolve_api_key_role,
    role_at_least,
)
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB

PUBLIC_READ = "pub-read"  # default_role read
PUBLIC_WRITE = "pub-write"  # default_role write
PRIVATE = "private"  # default_role none
OPEN = "open"  # no default_role: the caller's global role, if it covers every KB
REGISTRY_ONLY = "registered"  # in the index registry only, default_role read
MISSING = "no-such-kb"


# -- the ladder ---------------------------------------------------------------


class TestTheLadder:
    def test_roles_in_order(self):
        assert ROLES == ("read", "write", "admin")
        assert [ROLE_LEVELS[r] for r in ROLES] == [0, 1, 2]

    @pytest.mark.parametrize(
        ("role", "tier", "expected"),
        [
            ("read", "read", True),
            ("read", "write", False),
            ("write", "read", True),
            ("write", "write", True),
            ("write", "admin", False),
            ("admin", "admin", True),
            # An unknown or absent role is below every rung.
            ("bogus", "read", False),
            (None, "read", False),
            # An unknown tier is above every role: fail closed.
            ("admin", "bogus", False),
        ],
    )
    def test_role_at_least(self, role, tier, expected):
        assert role_at_least(role, tier) is expected

    def test_lower_role_is_the_lower_rung_and_keeps_the_first_on_a_tie(self):
        assert lower_role("write", "read") == "read"
        assert lower_role("read", "write") == "read"
        assert lower_role("write", "write") == "write"


# -- the API-key role ---------------------------------------------------------


def _settings(**kw) -> PyriteConfig:
    auth = kw.pop("auth", AuthConfig())
    return PyriteConfig(knowledge_bases=[], settings=Settings(auth=auth, **kw))


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class TestTheApiKeyRole:
    def test_no_keys_and_auth_disabled_is_open_admin(self):
        assert resolve_api_key_role("anything", _settings()) == "admin"
        assert resolve_api_key_role(None, _settings()) == "admin"

    def test_no_keys_and_auth_enabled_accepts_no_key(self):
        config = _settings(auth=AuthConfig(enabled=True))
        assert resolve_api_key_role("anything", config) is None

    def test_the_legacy_single_key_is_admin(self):
        config = _settings(api_key="s3cret")
        assert resolve_api_key_role("s3cret", config) == "admin"
        assert resolve_api_key_role("wrong", config) is None
        assert resolve_api_key_role(None, config) is None

    def test_a_listed_key_has_its_role_and_wins_over_the_legacy_key(self):
        config = _settings(
            api_key="legacy",
            api_keys=[
                {"key_hash": _hash("w"), "role": "write"},
                {"key_hash": _hash("legacy"), "role": "read"},
                {"key_hash": _hash("norole")},
            ],
        )
        assert resolve_api_key_role("w", config) == "write"
        assert resolve_api_key_role("legacy", config) == "read"
        assert resolve_api_key_role("norole", config) == "read"
        assert resolve_api_key_role("unknown", config) is None


# -- the per-KB rule, pure ----------------------------------------------------


def _user(role: str, global_access: bool) -> dict:
    return {"role": role, "global_access": 1 if global_access else 0}


def _no_grant() -> None:
    return None


class TestTheKbRoleRule:
    def test_a_global_admin_is_admin_everywhere(self):
        assert kb_role(1, _user("admin", False), _no_grant, "none", None) == "admin"

    def test_a_grant_wins_over_the_default_role(self):
        assert kb_role(1, _user("read", False), lambda: "write", "none", None) == "write"

    def test_a_vetted_user_gets_the_kb_default_role(self):
        assert kb_role(1, _user("read", True), _no_grant, "write", None) == "write"

    def test_a_self_registered_user_is_capped_at_read_on_a_public_kb(self):
        # v0.25.4: a KB's default_role never lifts a self-registered user
        # above read, whatever the KB allows.
        assert kb_role(1, _user("write", False), _no_grant, "write", None) == "read"

    def test_a_vetted_users_global_role_covers_a_kb_without_a_default(self):
        assert kb_role(1, _user("write", True), _no_grant, None, None) == "write"

    def test_a_self_registered_users_global_role_does_not(self):
        assert kb_role(1, _user("write", False), _no_grant, None, None) is None

    def test_a_private_kb_is_closed_without_a_grant(self):
        assert kb_role(1, _user("write", True), _no_grant, "none", None) is None

    def test_a_user_id_with_no_user_row_falls_through_to_the_anonymous_rule(self):
        # Today's behaviour, moved unchanged: the default role, uncapped, then
        # the anonymous ceiling.
        assert kb_role(9, None, _no_grant, "write", None) == "write"
        assert kb_role(9, None, _no_grant, None, "read") == "read"
        assert kb_role(9, None, _no_grant, None, None) is None

    @pytest.mark.parametrize(
        ("tier", "default", "expected"),
        [
            ("read", None, "read"),
            ("write", None, "write"),
            ("write", "read", "read"),  # the KB lowers the ceiling
            ("read", "write", "read"),  # but never raises it
            ("write", "none", None),  # a private KB is hidden
            (None, "read", None),  # no anonymous access at all
        ],
    )
    def test_the_anonymous_ceiling(self, tier, default, expected):
        assert kb_role(None, None, _no_grant, default, tier) == expected


# -- the policy, against a real database --------------------------------------


@pytest.fixture
def world(tmp_path):
    kbs = []
    for name, default in (
        (PUBLIC_READ, "read"),
        (PUBLIC_WRITE, "write"),
        (PRIVATE, "none"),
        (OPEN, None),
    ):
        (tmp_path / name).mkdir()
        kbs.append(
            KBConfig(name=name, path=tmp_path / name, kb_type="generic", default_role=default)
        )
    config = PyriteConfig(
        knowledge_bases=kbs,
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True, allow_registration=True, anonymous_tier="read"),
        ),
    )
    with PyriteDB(config.settings.index_path) as db:
        (tmp_path / REGISTRY_ONLY).mkdir()
        db.register_kb(REGISTRY_ONLY, "generic", str(tmp_path / REGISTRY_ONLY), default_role="read")
        auth = AuthService(db, config.settings.auth)
        users = {
            "admin": auth.create_user("root", "password123", role="admin"),
            "vetted": auth.create_user("vetted", "password123", role="write"),
            "granted": auth.create_user("granted", "password123", role="read"),
            "self": auth.register("selfreg", "password123"),
        }
        auth.set_role(users["self"]["id"], "write")  # never widens a self-registered user
        auth.grant_kb_permission(users["granted"]["id"], PRIVATE, "write", users["admin"]["id"])
        yield {
            "config": config,
            "db": db,
            "policy": AccessPolicy(config, db),
            "users": users,
            "tmp": tmp_path,
        }


def _principal(world, who: str) -> Principal:
    user = world["users"][who]
    return Principal.user(user["id"], "write" if who == "self" else user["role"])


class TestTheDefaultRole:
    def test_config_wins(self, world):
        assert world["policy"].kb_default_role(PUBLIC_WRITE) == "write"
        assert world["policy"].kb_default_role(PRIVATE) == "none"

    def test_the_registry_answers_for_a_kb_not_in_config(self, world):
        assert world["policy"].kb_default_role(REGISTRY_ONLY) == "read"

    def test_a_kb_nobody_knows_has_none(self, world):
        # "none", not None (the instance default): a name the load left out
        # or that names no KB gives no access in the role check itself, not
        # only through each route's existence check
        # (tests/test_config_kb_default_role_is_the_files.py).
        assert world["policy"].kb_default_role(MISSING) == "none"

    def test_a_registry_value_that_opens_a_kb_is_ignored_under_an_untrusted_config(
        self, world, monkeypatch
    ):
        # v0.25.4: under an untrusted repo-local config the index belongs to
        # the tree, so only "none" is honoured from it.
        monkeypatch.setattr(world["config"], "_confine_root", world["tmp"])
        assert world["policy"].kb_default_role(REGISTRY_ONLY) is None
        world["db"].update_kb_default_role(REGISTRY_ONLY, "none")
        assert world["policy"].kb_default_role(REGISTRY_ONLY) == "none"

    def test_kb_exists_in_config_or_registry(self, world):
        policy = world["policy"]
        assert policy.kb_exists(PRIVATE)
        assert policy.kb_exists(REGISTRY_ONLY)
        assert not policy.kb_exists(MISSING)


class TestTheEffectiveKbRole:
    def test_no_principal_has_no_role(self, world):
        assert world["policy"].effective_kb_role(None, PUBLIC_READ) is None

    def test_an_operator_key_has_its_role_on_every_kb(self, world):
        policy = world["policy"]
        assert policy.effective_kb_role(Principal.from_api_key("read"), PRIVATE) == "read"
        assert policy.effective_kb_role(Principal.from_api_key("write"), MISSING) == "write"

    def test_the_local_principal_is_admin(self, world):
        assert world["policy"].effective_kb_role(Principal.local(), PRIVATE) == "admin"

    def test_a_global_admin_is_admin(self, world):
        admin = _principal(world, "admin")
        assert world["policy"].effective_kb_role(admin, PRIVATE) == "admin"

    def test_a_user_walks_grant_default_global(self, world):
        policy = world["policy"]
        granted = _principal(world, "granted")
        vetted = _principal(world, "vetted")
        selfreg = _principal(world, "self")
        assert policy.effective_kb_role(granted, PRIVATE) == "write"
        assert policy.effective_kb_role(vetted, PRIVATE) is None
        assert policy.effective_kb_role(vetted, OPEN) == "write"
        assert policy.effective_kb_role(selfreg, PUBLIC_WRITE) == "read"
        assert policy.effective_kb_role(selfreg, OPEN) is None

    def test_the_anonymous_visitor_is_capped_by_the_tier(self, world):
        policy = world["policy"]
        anon = Principal.anonymous("read")
        assert policy.effective_kb_role(anon, PUBLIC_WRITE) == "read"
        assert policy.effective_kb_role(anon, OPEN) == "read"
        assert policy.effective_kb_role(anon, PRIVATE) is None


class TestTheScopes:
    def test_unscoped_principals(self, world):
        policy = world["policy"]
        for p in (Principal.local(), Principal.from_api_key("read"), _principal(world, "admin")):
            assert policy.read_scope(p).unscoped
            assert policy.read_scope(p).as_set() is None
            assert policy.write_scope(p).as_set() is None
            assert policy.read_scope(p).permits(PRIVATE)

    def test_a_granted_user(self, world):
        policy = world["policy"]
        p = _principal(world, "granted")
        assert policy.read_scope(p).as_set() == {PUBLIC_READ, PUBLIC_WRITE, PRIVATE, OPEN}
        assert policy.write_scope(p).as_set() == {PUBLIC_WRITE, PRIVATE}

    def test_a_self_registered_user_reads_public_kbs_and_writes_none(self, world):
        policy = world["policy"]
        p = _principal(world, "self")
        assert policy.read_scope(p).as_set() == {PUBLIC_READ, PUBLIC_WRITE}
        assert policy.write_scope(p).as_set() == set()
        assert not policy.read_scope(p).permits(PRIVATE)

    def test_the_anonymous_visitor(self, world):
        policy = world["policy"]
        p = Principal.anonymous("read")
        assert policy.read_scope(p).as_set() == {PUBLIC_READ, PUBLIC_WRITE, OPEN}
        assert policy.write_scope(p).as_set() == set()

    def test_an_anonymous_admin_tier_is_unscoped_like_any_admin_role(self, world):
        # Today's rule: role "admin" is unscoped whoever holds it. The config
        # refuses anonymous_tier "admin", so this is the helper's contract only.
        assert world["policy"].read_scope(Principal.anonymous("admin")).unscoped

    def test_as_set_is_a_fresh_copy(self, world):
        scope = world["policy"].read_scope(_principal(world, "self"))
        scope.as_set().add(PRIVATE)
        assert not scope.permits(PRIVATE)

    def test_a_scope_is_made_by_the_policy_not_by_default(self):
        with pytest.raises(TypeError, match="made by AccessPolicy"):
            ReadScope(None, _by_policy=object())


class TestAuthorizeAndConcealment:
    """§4: existence before role, and an unreadable KB answers like a missing one."""

    def test_no_principal_is_unauthenticated(self, world):
        decision = world["policy"].authorize(None, Action.KB_READ, KB(PUBLIC_READ))
        assert decision == Decision(UNAUTHENTICATED)
        assert not decision.allowed

    def test_a_missing_kb_is_not_found_even_for_an_admin(self, world):
        policy = world["policy"]
        for p in (Principal.local(), _principal(world, "admin"), Principal.from_api_key("admin")):
            assert policy.authorize(p, Action.KB_READ, KB(MISSING)).code == NOT_FOUND

    def test_an_unreadable_kb_answers_exactly_like_a_missing_one(self, world):
        policy = world["policy"]
        p = _principal(world, "vetted")
        for action in (Action.KB_READ, Action.KB_WRITE, Action.KB_ADMIN):
            private = policy.authorize(p, action, KB(PRIVATE))
            missing = policy.authorize(p, action, KB(MISSING))
            assert private == missing == Decision(NOT_FOUND)

    def test_readable_but_not_at_the_rung_is_forbidden(self, world):
        policy = world["policy"]
        p = _principal(world, "self")
        assert policy.authorize(p, Action.KB_READ, KB(PUBLIC_WRITE)).allowed
        assert policy.authorize(p, Action.KB_WRITE, KB(PUBLIC_WRITE)) == Decision(FORBIDDEN)

    def test_a_grant_allows(self, world):
        p = _principal(world, "granted")
        assert world["policy"].authorize(p, Action.KB_WRITE, KB(PRIVATE)).allowed
        assert world["policy"].authorize(p, Action.KB_ADMIN, KB(PRIVATE)).code == FORBIDDEN

    def test_an_operator_key_below_the_rung_is_forbidden_on_a_kb_that_exists(self, world):
        p = Principal.from_api_key("read")
        assert world["policy"].authorize(p, Action.KB_WRITE, KB(PRIVATE)).code == FORBIDDEN

    def test_a_row_follows_its_kb(self, world):
        policy = world["policy"]
        p = _principal(world, "vetted")
        assert policy.authorize(p, Action.KB_WRITE, Row(PRIVATE)) == Decision(NOT_FOUND)
        assert policy.authorize(p, Action.KB_WRITE, Row(MISSING)) == Decision(NOT_FOUND)
        assert policy.authorize(p, Action.KB_WRITE, Row(OPEN)).allowed

    def test_the_instance_is_the_global_role(self, world):
        policy = world["policy"]
        assert policy.authorize(
            Principal.from_api_key("write"), Action.KB_WRITE, Instance()
        ).allowed
        assert (
            policy.authorize(Principal.from_api_key("read"), Action.KB_WRITE, Instance()).code
            == FORBIDDEN
        )
        assert policy.authorize(None, Action.KB_READ, Instance()).code == UNAUTHENTICATED
        assert policy.authorize(
            _principal(world, "admin"), Action.INSTANCE_ADMIN, Instance()
        ).allowed
        assert (
            policy.authorize(_principal(world, "vetted"), Action.INSTANCE_ADMIN, Instance()).code
            == FORBIDDEN
        )

    def test_any_kb_is_allowed_to_a_principal_the_scope_then_narrows(self, world):
        assert (
            world["policy"].authorize(Principal.anonymous("read"), Action.KB_READ, AnyKB()).allowed
        )
        assert world["policy"].authorize(None, Action.KB_READ, AnyKB()).code == UNAUTHENTICATED

    def test_an_action_theme_1_does_not_decide_is_refused_loudly(self, world):
        with pytest.raises(NotImplementedError):
            world["policy"].authorize(Principal.local(), Action.REPO_EGRESS, Instance())

    def test_require_raises_the_not_found_a_missing_kb_raises(self, world):
        policy = world["policy"]
        p = _principal(world, "vetted")
        with pytest.raises(KBNotFoundError) as private:
            policy.require(p, Action.KB_READ, KB(PRIVATE))
        with pytest.raises(KBNotFoundError) as missing:
            policy.require(p, Action.KB_READ, KB(MISSING))
        assert type(private.value) is type(missing.value)
        assert str(private.value) == f"KB '{PRIVATE}' not found"
        assert str(missing.value) == f"KB '{MISSING}' not found"

    def test_require_raises_a_denial_with_its_code(self, world):
        with pytest.raises(PolicyDeniedError) as denied:
            world["policy"].require(_principal(world, "self"), Action.KB_WRITE, KB(PUBLIC_WRITE))
        assert denied.value.code == FORBIDDEN
        with pytest.raises(PolicyDeniedError) as anon:
            world["policy"].require(None, Action.KB_READ, KB(PUBLIC_READ))
        assert anon.value.code == UNAUTHENTICATED
        world["policy"].require(_principal(world, "granted"), Action.KB_WRITE, KB(PRIVATE))

    def test_decisions_are_made_per_call_against_current_grants(self, world):
        policy = world["policy"]
        p = _principal(world, "vetted")
        assert policy.authorize(p, Action.KB_READ, KB(PRIVATE)).code == NOT_FOUND
        AuthService(world["db"], world["config"].settings.auth).grant_kb_permission(
            p.user_id, PRIVATE, "read", world["users"]["admin"]["id"]
        )
        assert policy.authorize(p, Action.KB_READ, KB(PRIVATE)).allowed


class TestPrincipal:
    def test_constructors(self):
        assert Principal.local() == Principal("local", "admin")
        assert Principal.from_api_key("read") == Principal("operator_key", "read")
        assert Principal.user(3, "write") == Principal("user", "write", 3)
        assert Principal.anonymous("read") == Principal("anonymous", "read")

    def test_only_users_and_the_anonymous_visitor_are_scoped(self):
        assert Principal.user(3, "read").scoped
        assert Principal.anonymous("read").scoped
        assert not Principal.from_api_key("read").scoped
        assert not Principal.local().scoped


# -- the module is framework-free ---------------------------------------------


def test_the_policy_imports_no_framework():
    source = Path(access_policy.__file__).read_text()
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("server"), node.module
    assert not imported & {"fastapi", "starlette", "mcp", "typer", "click"}, imported


@pytest.mark.parametrize("module", ["mcp_routes.py", "websocket.py"])
def test_mcp_and_the_socket_ask_the_policy_not_the_rest_app(module):
    """#383 acceptance 2: neither imports from `server/api.py`, at module level
    or inside a function; both import the policy."""
    source = (Path(access_policy.__file__).parents[1] / "server" / module).read_text()
    from_api = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module == "api"
    ]
    assert from_api == [], f"{module} imports from .api at lines {from_api}"
    assert "from ..services.access_policy import" in source
