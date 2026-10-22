"""The landing rule: `KBConfig.landing` and `AccessPolicy.lands_in`
(ADR-0044 §1, as revised 2026-10-20; markramm/pyrite-security#110).

The rule's whole table, by hand, from the ADR -- not from the code:

| Principal                         | canonical | review    |
|-----------------------------------|-----------|-----------|
| the local process (CLI, stdio)    | Canonical | Canonical |
| an operator API key               | Canonical | Canonical |
| a signed-in global admin          | Canonical | Canonical |
| a user with a write grant         | Canonical | ChangeSet |
| a user with no write permission   | Deny      | Deny      |
| anonymous, no write               | Deny      | Deny      |
| anonymous, `anonymous_tier: write` | Canonical | Deny      | (its own test)

The end-to-end property over every transport is
`test_writes_land_in_the_users_worktree.py`.
"""

from __future__ import annotations

import re

import pytest

from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
from pyrite.exceptions import ConfigError
from pyrite.services.access_policy import AccessPolicy, Principal
from pyrite.services.auth_service import AuthService
from pyrite.storage.database import PyriteDB

CANON, REVIEW = "canon-kb", "review-kb"
OPEN_CANON, OPEN_REVIEW = "open-canon-kb", "open-review-kb"  # default_role: write


@pytest.fixture
def policy(tmp_path):
    for name in (CANON, REVIEW, OPEN_CANON, OPEN_REVIEW):
        (tmp_path / name).mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name=CANON, path=tmp_path / CANON),
            KBConfig(name=REVIEW, path=tmp_path / REVIEW, landing="review"),
            KBConfig(name=OPEN_CANON, path=tmp_path / OPEN_CANON, default_role="write"),
            KBConfig(
                name=OPEN_REVIEW,
                path=tmp_path / OPEN_REVIEW,
                default_role="write",
                landing="review",
            ),
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True),
        ),
    )
    db = PyriteDB(config.settings.index_path)
    db.merge_registered_kbs(config)
    auth = AuthService(db, config.settings.auth)
    policy = AccessPolicy(config, db, auth)
    ids = {
        role: auth.create_user(f"{role}-user", "password123", role=role)["id"]
        for role in ("admin", "write", "read")
    }
    policy.principals = {
        "local": Principal.local(),
        "operator_key": Principal.from_api_key("write"),
        "global_admin": Principal.user(ids["admin"], "admin"),
        "write_user": Principal.user(ids["write"], "write"),
        "read_user": Principal.user(ids["read"], "read"),
        "anonymous": Principal.anonymous("read"),
    }
    try:
        yield policy
    finally:
        db.close()


EXPECTED = {
    ("local", CANON): "canonical",
    ("local", REVIEW): "canonical",
    ("operator_key", CANON): "canonical",
    ("operator_key", REVIEW): "canonical",
    ("global_admin", CANON): "canonical",
    ("global_admin", REVIEW): "canonical",
    ("write_user", CANON): "canonical",
    ("write_user", REVIEW): "change_set",
    ("read_user", CANON): "deny",
    ("read_user", REVIEW): "deny",
    ("anonymous", CANON): "deny",
    ("anonymous", REVIEW): "deny",
}


@pytest.mark.parametrize(("who", "kb"), sorted(EXPECTED))
def test_lands_in_follows_the_adr_table(policy, who, kb):
    assert policy.lands_in(policy.principals[who], kb) == EXPECTED[(who, kb)]


def test_an_anonymous_writer_lands_canonical_and_never_in_a_change_set(policy):
    """`anonymous_tier: write` on a KB whose default_role grants the write:
    canonical lands on the files; review is Deny -- a change set belongs to a
    user, and the anonymous visitor is none (ADR-0044 §1 as built)."""
    policy.config.settings.auth.anonymous_tier = "write"
    fresh = AccessPolicy(policy.config, policy.db)
    anonymous = Principal.anonymous("write")
    assert fresh.lands_in(anonymous, OPEN_CANON) == "canonical"
    assert fresh.lands_in(anonymous, OPEN_REVIEW) == "deny"


def test_no_principal_is_denied(policy):
    assert policy.lands_in(None, CANON) == "deny"


def test_the_index_cannot_set_a_landing(policy, tmp_path):
    """`landing` is the operator's file's to set (ADR-0039 decision 1). A KB
    known from an index registry row is canonical, whatever the row says."""
    row = {"name": "reg-kb", "path": str(tmp_path), "landing": "review"}
    assert policy.config.kb_config_from_registry_row(row).landing == "canonical"


# -- the field -----------------------------------------------------------


def test_landing_defaults_to_canonical(tmp_path):
    assert KBConfig(name="k", path=tmp_path).landing == "canonical"


def test_an_unknown_landing_fails_at_load(tmp_path):
    with pytest.raises(ConfigError, match="landing"):
        PyriteConfig.from_dict(
            {"knowledge_bases": [{"name": "k", "path": str(tmp_path), "landing": "reveiw"}]}
        )


def test_an_ephemeral_kb_cannot_land_in_review(tmp_path):
    with pytest.raises(ConfigError, match="ephemeral"):
        KBConfig(name="k", path=tmp_path, ephemeral=True, landing="review")


def test_landing_is_read_from_and_written_to_the_config_file(tmp_path):
    config = PyriteConfig.from_dict(
        {
            "knowledge_bases": [
                {"name": "r", "path": str(tmp_path), "landing": "review"},
                {"name": "c", "path": str(tmp_path)},
            ]
        }
    )
    assert [kb.landing for kb in config.knowledge_bases] == ["review", "canonical"]
    saved = config.to_dict()["knowledge_bases"]
    assert saved[0]["landing"] == "review"
    assert "landing" not in saved[1], "the default is not written out"


# -- the per-call binding ------------------------------------------------------


def test_a_binding_is_seen_only_inside_its_own_task():
    """Two MCP sessions are two asyncio tasks on one event loop: a target
    bound in one is never seen by the other, and is gone when the block ends."""
    import anyio

    from pyrite.services.write_target import binding, current_target

    seen = {}

    async def bound(started, release):
        with binding(("alice-config", "alice-db")):
            started.set()
            await release.wait()
            seen["inside"] = current_target().config
        seen["after"] = current_target()

    async def other(started, release):
        await started.wait()
        seen["other"] = current_target()
        release.set()

    async def main():
        started, release = anyio.Event(), anyio.Event()
        async with anyio.create_task_group() as tg:
            tg.start_soon(bound, started, release)
            tg.start_soon(other, started, release)

    anyio.run(main)
    assert seen == {"inside": "alice-config", "after": None, "other": None}


# -- the change-set DB forwards nothing it has not classified --------------------


def _pyritedb_names(tmp_path) -> set[str]:
    import inspect

    db = PyriteDB(tmp_path / "names.db")
    try:
        members = {n for n, _ in inspect.getmembers(PyriteDB) if not n.startswith("__")}
        return members | set(vars(db))
    finally:
        db.close()


def test_every_pyritedb_name_is_classified_for_a_change_set(tmp_path):
    """A name WorktreeDB neither defines nor classifies would fall through to
    main -- the way `register_kb` repointed main's `kb` row at a user's
    worktree. Every PyriteDB name is defined on the overlay or classified."""
    from pyrite.storage.backends import overlay_backend as ob

    defined = {n for n in vars(ob.WorktreeDB) if not n.startswith("__")}
    classified = (
        ob._READS_FROM_MAIN
        | ob._READ_SQL_ONLY
        | ob._RAW_HANDLES
        | ob._WRITES_TO_DIFF
        | set(ob._RECORDS_REFUSED)
        | ob._WRITES_REFUSED
    )
    assert _pyritedb_names(tmp_path) - defined - classified == set()


_DML = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", re.IGNORECASE)


def _writes_in(fn) -> list[str]:
    """What in `fn`'s body writes, read from its source (AST): a commit, a
    session add/merge/delete, a call to a classified write method or a
    migration, a DML or DDL string, or SQL passed in by the caller and run."""
    import ast
    import inspect
    import textwrap

    from pyrite.storage.backends import overlay_backend as ob

    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError):
        return []
    writes_named = ob._WRITES_TO_DIFF | set(ob._RECORDS_REFUSED) | ob._WRITES_REFUSED
    func = tree.body[0]
    params = {a.arg for a in func.args.args} if isinstance(func, ast.FunctionDef) else set()
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _DML.search(node.value):
                hits.append(f"SQL {node.value.split()[0]}")
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
        receiver = ast.unparse(callee.value) if isinstance(callee, ast.Attribute) else ""
        if name == "commit" or name in writes_named or "migrat" in name:
            hits.append(name)
        elif name in ("add", "add_all", "merge", "delete") and "session" in receiver:
            hits.append(f"session.{name}")
        elif name in ("execute", "exec_driver_sql", "executescript") and any(
            isinstance(sub, ast.Name) and sub.id in params
            for arg in node.args[:1]
            for sub in ast.walk(arg)
        ):
            hits.append("runs the caller's SQL")
    return hits


def test_nothing_forwarded_to_main_as_a_read_writes():
    """A name filed as a read, or as a raw handle, is scanned: its body must
    not write. `get_local_user` (it creates the row it does not find),
    `execute_sql` (it runs whatever it is given), `transaction` and the
    schema/policy initialisers were filed as reads before 2026-10-20."""
    import inspect

    from pyrite.storage.backends import overlay_backend as ob
    from pyrite.storage.database import PyriteDB

    misfiled = {}
    for name in sorted(ob._READS_FROM_MAIN | ob._RAW_HANDLES):
        attr = inspect.getattr_static(PyriteDB, name, None)
        fn = attr.fget if isinstance(attr, property) else getattr(attr, "__func__", attr)
        if callable(fn):
            hits = _writes_in(fn)
            if hits:
                misfiled[name] = sorted(set(hits))
    assert misfiled == {}


def test_the_scan_sees_each_kind_of_write():
    """Control for the scan above: each misfiling it must catch is caught."""
    from pyrite.storage.database import PyriteDB

    for name in (
        "get_local_user",
        "execute_sql",
        "transaction",
        "_record_private_policy",
        "_create_table_from_def",
        "_run_migrations",
    ):
        fn = getattr(PyriteDB, name)
        assert _writes_in(getattr(fn, "__func__", fn)), name


def test_execute_sql_through_a_change_set_reads_and_never_writes(tmp_path):
    from pyrite.exceptions import ChangeSetRecordRefusedError
    from pyrite.storage.backends.overlay_backend import WorktreeDB

    main, diff = PyriteDB(tmp_path / "main.db"), PyriteDB(tmp_path / "diff.db")
    try:
        main.register_kb("k", "generic", str(tmp_path / "k"))
        wt = WorktreeDB(main, diff)
        assert wt.execute_sql("SELECT name FROM kb")[0]["name"] == "k"
        with pytest.raises(ChangeSetRecordRefusedError):
            wt.execute_sql("UPDATE kb SET description = 'x'")
        with pytest.raises(ChangeSetRecordRefusedError):
            wt.create_review(entry_id="e", kb_name="k", reviewer="r")
        assert main.execute_sql("SELECT description FROM kb")[0]["description"] == ""
    finally:
        main.close()
        diff.close()


def test_a_change_set_write_goes_to_the_diff_and_an_instance_write_is_refused(tmp_path):
    from pyrite.exceptions import ChangeSetRecordRefusedError
    from pyrite.storage.backends.overlay_backend import WorktreeDB

    main, diff = PyriteDB(tmp_path / "main.db"), PyriteDB(tmp_path / "diff.db")
    try:
        main.register_kb("k", "generic", str(tmp_path / "main-k"))
        wt = WorktreeDB(main, diff)
        wt.register_kb("k", "generic", str(tmp_path / "worktree-k"))
        assert main.execute_sql("SELECT path FROM kb WHERE name = 'k'")[0]["path"].endswith(
            "main-k"
        )
        assert diff.execute_sql("SELECT path FROM kb WHERE name = 'k'")[0]["path"].endswith(
            "worktree-k"
        )
        with pytest.raises(ChangeSetRecordRefusedError):
            wt.set_setting("ui.theme", "dark")
        with pytest.raises(AttributeError, match="classify"):
            wt.no_such_method  # noqa: B018
    finally:
        main.close()
        diff.close()


# -- REST fails closed ---------------------------------------------------------


def _request(method: str, **state):
    from starlette.requests import Request

    request = Request({"type": "http", "method": method, "headers": [], "path": "/x"})
    for key, value in state.items():
        setattr(request.state, key, value)
    return request


@pytest.mark.parametrize(
    ("method", "state", "refused"),
    [
        # a user's write on a route that never asked the rule: refused
        ("POST", {"api_role": "write", "auth_user": {"id": 2}}, True),
        ("DELETE", {"api_role": "write", "auth_user": {"id": 2}}, True),
        # asked, and canonical: the instance's own
        ("POST", {"api_role": "write", "auth_user": {"id": 2}, "landing_asked": True}, False),
        # a read, or the operator: the instance's own
        ("GET", {"api_role": "write", "auth_user": {"id": 2}}, False),
        ("POST", {"api_role": "admin", "auth_user": {"id": 1}}, False),
        ("POST", {"api_role": "write"}, False),  # an operator key
    ],
)
def test_a_write_route_that_never_asked_the_rule_is_refused(method, state, refused):
    from pyrite.server.api import _landed
    from pyrite.services.write_target import LandingRefusedError

    request = _request(method, **state)
    if refused:
        with pytest.raises(LandingRefusedError):
            _landed(request, "config", "db")
    else:
        assert _landed(request, "config", "db") == ("config", "db")


def test_every_write_route_that_takes_the_write_service_asks_the_rule():
    """Structural: a POST/PUT/PATCH/DELETE route that builds `get_kb_service`
    or `get_task_service` declares `requires_kb_tier("write")`, or is admin
    tier (the operator). A route that only reads takes `get_read_kb_service`."""
    from pyrite.server.api import create_app
    from tests._surface_inventory import _walk_routes

    def calls(dependant, acc):
        for sub in dependant.dependencies:
            acc.add(getattr(sub.call, "__qualname__", repr(sub.call)))
            calls(sub, acc)
        return acc

    unguarded = []
    for path, route in _walk_routes(create_app().routes):
        if not route.methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        names = calls(route.dependant, set())
        if not names & {"get_kb_service", "get_task_service"}:
            continue
        asks = any(n.startswith("requires_kb_tier.") for n in names)
        admin = any(
            getattr(d.call, "__closure__", None)
            and any(getattr(c, "cell_contents", None) == "admin" for c in d.call.__closure__)
            for d in route.dependant.dependencies
        )
        if not (asks or admin):
            unguarded.append(f"{sorted(route.methods)} {path}")
    assert unguarded == []


@pytest.mark.parametrize("value", [None, "", "Review"])
def test_an_empty_null_or_misspelt_landing_fails_at_load(tmp_path, value):
    """Only an absent key means the default; `landing:` with no value is not
    canonical by accident."""
    with pytest.raises(ConfigError, match="landing"):
        PyriteConfig.from_dict(
            {"knowledge_bases": [{"name": "k", "path": str(tmp_path), "landing": value}]}
        )
