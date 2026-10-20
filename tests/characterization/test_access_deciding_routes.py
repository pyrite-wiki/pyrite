"""Per-principal STATUS goldens for every access-deciding route this harness
excludes from the per-KB matrix (ADR-0037 theme 0, #476 round-2 issue 3).

`REST_ACCESS_EXCLUSIONS` correctly keeps these OUT of
`test_rest_matrix.py`'s per-KB READABLE/PRIVATE/MISSING/NO_DEFAULT_ROLE axis
-- each one gates on `requires_tier("admin"/"write")` alone, with no per-KB
branch to vary that axis over (see each exclusion's own reason in
`surfaces.py`). But "no per-KB axis" is not "no access decision at all":
every route below decides admin-or-not (or write-tier-or-not) for the
CALLER, and before this file existed nothing pinned that decision -- cold
review (#476 round-2) found `DELETE /api/kbs/{name}` with its admin check
removed (mutation 5) passed silently, because the only place it appeared
was `REST_ACCESS_EXCLUSIONS`, which this harness's other tests never turn
into a live request.

**What this file covers.** The named categories from the round-2 cold
read: admin (index sync/jobs/usage/site-render), KB CRUD (create, delete,
reindex, gc, ephemeral), permissions/default-role, publish/commit/push,
settings, and user roles. Excluded from this file's own scope: pure
public/static routes with no access decision to characterize at all
(`/health`, `/branding/*`, `/auth/
login`+`register`+`logout` -- these authenticate or serve statically,
they do not decide access to an existing resource) and the GitHub OAuth
dance (`/auth/github/*`), whose "access decision" is entirely about a
THIRD PARTY's redirect flow, not this server's authorization.

**STATUS only, never the body.** The body of an admin listing (usage
stats, job ids, KB registry contents) is exactly the kind of volatile,
count-shaped content `normalize.py` blanks elsewhere -- pinning it here
would either fight that same battle again or (worse) quietly encode
today's admin usage numbers as "correct". The status code IS the access
decision (`INLINE_ACCESS_DECIDING_ROUTES`'s own principle: "the body can
be normalised; the status must not be" -- round-2 issue 3's own words).

**A dedicated, disposable, module-scoped world.** Several of these routes
are genuinely destructive (`DELETE /api/kbs/{name}`, `POST /api/kbs/gc`,
`POST /api/index/sync`) or create real state (`POST /api/kbs`,
`POST /api/kbs/ephemeral`). Never run against `world` (session-scoped,
relied on as read-only everywhere else) or `write_world` (shared with
`test_rest_matrix.py`'s and `test_mcp_matrix.py`'s write cases in whatever
module runs first) -- `access_world` is its own `build_world` call, used
by nothing else, so this file's mutations can never leak into another
file's assumptions about what a KB or listing contains. Every mutating
call here targets a KB/resource name this file made up
(`_DISPOSABLE_NAME`) rather than one of `world.py`'s seeded fixtures, so
even the one principal that reaches past the tier gate (`admin_key`) only
ever 404s ("not found"), never actually deleting or mutating anything a
different case depends on.
"""

from __future__ import annotations

import pytest

from tests.characterization.golden_io import MismatchCollector, load, regenerating, save
from tests.characterization.world import World, build_world

GOLDEN_NAME = "access_deciding"

PRINCIPAL_NAMES = (
    "anonymous",
    "read_key",
    "write_key",
    "admin_key",
    "global_user",
    "local_user",
    "granted_user",
)

_DISPOSABLE_NAME = "characterization-access-deciding-does-not-exist"
_DISPOSABLE_JOB_ID = "characterization-access-deciding-no-such-job"
_DISPOSABLE_USER_ID = "999999999"
_DISPOSABLE_KEY_PROVIDER = "does-not-exist-provider"
_DISPOSABLE_INVITE_CODE = "does-not-exist-invite-code"
_DISPOSABLE_SETTINGS_KEY = "characterization.does.not.exist"

# One request-call spec per access-deciding route (#476 round-2 issue 3):
# method, path, and the kwargs `TestClient.request` needs beyond
# headers/cookies. Every mutating call targets `_DISPOSABLE_NAME`-shaped
# resources so the one principal that reaches past its tier gate still only
# 404s, never mutating real state -- see the module docstring.
_ACCESS_DECIDING_ROUTES: tuple[tuple[str, str, dict], ...] = (
    # -- admin: instance-wide operations, requires_tier("admin") --------
    ("POST", "/api/index/sync", {}),
    ("GET", "/api/index/jobs", {}),
    ("GET", f"/api/index/jobs/{_DISPOSABLE_JOB_ID}", {}),
    ("GET", "/api/admin/usage", {}),
    # -- KB CRUD: requires_tier("admin"), targets a KB that does not exist -
    ("POST", "/api/kbs", {"json": {"name": _DISPOSABLE_NAME, "kb_type": "generic"}}),
    ("PUT", f"/api/kbs/{_DISPOSABLE_NAME}", {"json": {"default_role": "read"}}),
    ("DELETE", f"/api/kbs/{_DISPOSABLE_NAME}", {}),
    ("POST", f"/api/kbs/{_DISPOSABLE_NAME}/reindex", {}),
    ("POST", "/api/kbs/gc", {}),
    ("GET", "/api/kbs/ephemeral", {}),
    ("POST", "/api/kbs/ephemeral", {"json": {}}),
    ("DELETE", f"/api/kbs/ephemeral/{_DISPOSABLE_NAME}", {}),
    # -- default-role / publish / commit / push: requires_tier("admin"),
    # same "no such KB" targeting -----------------------------------------
    (
        "PUT",
        f"/api/kbs/{_DISPOSABLE_NAME}/default-role",
        {"json": {"default_role": "read"}},
    ),
    ("POST", f"/api/kbs/{_DISPOSABLE_NAME}/publish", {"json": {}}),
    ("POST", f"/api/kbs/{_DISPOSABLE_NAME}/commit", {"json": {"message": "characterization"}}),
    ("POST", f"/api/kbs/{_DISPOSABLE_NAME}/push", {}),
    # -- settings: requires_tier("admin"/"write"), names a settings key, not
    # a KB -----------------------------------------------------------------
    ("GET", "/api/settings", {}),
    ("PUT", "/api/settings", {"json": {}}),
    ("GET", f"/api/settings/{_DISPOSABLE_SETTINGS_KEY}", {}),
    ("PUT", f"/api/settings/{_DISPOSABLE_SETTINGS_KEY}", {"json": {"value": "x"}}),
    ("DELETE", f"/api/settings/{_DISPOSABLE_SETTINGS_KEY}", {}),
    # -- user roles / permissions: _ADMIN_ONLY, names a user id, not a KB --
    ("GET", "/auth/users", {}),
    (
        "GET",
        f"/auth/users/{_DISPOSABLE_USER_ID}/permissions",
        {},
    ),
    ("PUT", f"/auth/users/{_DISPOSABLE_USER_ID}/role", {"json": {"role": "read"}}),
    # -- account-scoped, but still worth a status golden: the caller's OWN
    # api-keys/invite-codes, refused identically to a non-caller's own data
    # by an unauthenticated/wrong-tier request -----------------------------
    ("GET", "/auth/api-keys", {}),
    ("POST", "/auth/api-keys", {"json": {"provider": _DISPOSABLE_KEY_PROVIDER}}),
    ("DELETE", f"/auth/api-keys/{_DISPOSABLE_KEY_PROVIDER}", {}),
    ("GET", "/auth/invite-codes", {}),
    ("POST", "/auth/invite-codes", {"json": {}}),
    ("DELETE", f"/auth/invite-codes/{_DISPOSABLE_INVITE_CODE}", {}),
    # -- merge queue: requires_tier("admin") only, by design -------------
    ("GET", "/api/admin/merge-queue", {}),
    ("GET", f"/api/admin/merge-queue/{_DISPOSABLE_NAME}/diff", {}),
    ("POST", f"/api/admin/merge-queue/{_DISPOSABLE_NAME}/merge", {"json": {}}),
    ("POST", f"/api/admin/merge-queue/{_DISPOSABLE_NAME}/reject", {"json": {"feedback": ""}}),
)


@pytest.fixture(scope="module")
def access_world(tmp_path_factory) -> World:
    w = build_world(tmp_path_factory, label="adr0037-access-deciding")
    try:
        yield w
    finally:
        w.close()


@pytest.mark.parametrize("principal_name", PRINCIPAL_NAMES)
def test_access_deciding_route_status(access_world, principal_name):
    """STATUS only (never the body -- see the module docstring) for every
    access-deciding route this harness excludes from the per-KB matrix.
    Mutation 5 (#476): removing `DELETE /api/kbs/{name}`'s admin check
    flips every non-admin principal's status here from 403 to 404 (the
    dependency no longer refuses first, so the handler runs and reports
    "not found" instead) -- this test is what catches that, which nothing
    in `test_rest_matrix.py` can, because the route is deliberately
    excluded from that file's per-KB axis.
    """
    principal = access_world.principals[principal_name]
    golden = load(GOLDEN_NAME)
    collector = MismatchCollector()
    for method, path, kwargs in _ACCESS_DECIDING_ROUTES:
        key = f"{method} {path} | {principal_name}"
        resp = access_world.client.request(
            method,
            path,
            headers=principal.rest_headers or None,
            cookies=principal.rest_cookies or None,
            **kwargs,
        )
        actual = {"status": resp.status_code}
        collector.check(GOLDEN_NAME, key, actual, golden)
    if regenerating():
        save(GOLDEN_NAME, golden, principal_scope=principal_name)
    collector.assert_clean()
