"""The one behaviour the backlog item's "self-registered user with and
without global access" principal pair actually distinguishes (ADR-0037
theme 0, #476 blocker 5).

`NO_DEFAULT_ROLE` is now one of the four states in `test_rest_matrix.py`/
`test_mcp_matrix.py`'s main KB axis (`KB_STATES = (READABLE, PRIVATE,
MISSING, NO_DEFAULT_ROLE)`), so `global_access=1` vs `0` IS pinned on every
route and tool there, for every principal, not only here. What THIS file
adds on top: a short, human-readable, always-run assertion of the exact
mechanism -- `PRIVATE` (`default_role="none"`) is closed to global and local
alike (`AuthService.get_kb_role`'s docstring: "If KB is private
(default_role='none'), deny unless explicit grant" -- unconditional, no
`global_access` check at all), so it is specifically `NO_DEFAULT_ROLE`
(`default_role` unset entirely) where a global user's role falls back to
covering a KB and a local (self-registered, `global_access=False`) user's
does not. A small, direct pair of cases instead of relying on a reviewer to
notice this fact inside the much larger matrix's goldens.
"""

from __future__ import annotations

from tests.characterization.world import NO_DEFAULT_ROLE, NO_DEFAULT_ROLE_ENTRY

# Not @pytest.mark.core: `core` is the small, always-run smoke set
# scripts/test-affected pins by an exact, exhaustive file list
# (tests/test_test_affected.py::TestThisRepository.CORE) -- adding a mark
# here without adding this file to that list fails
# test_core_set_is_exactly_the_named_surfaces, and this suite is deliberately
# heavier than that set is meant to be. test-affected's import-graph walker
# already selects these files whenever a branch touches a module they
# import (pyrite.server.api, pyrite.server.mcp_server, ...).


def test_global_user_reads_a_kb_with_no_default_role(world):
    principal = world.principals["global_user"]
    resp = world.client.get(
        f"/api/entries/{NO_DEFAULT_ROLE_ENTRY}",
        params={"kb": NO_DEFAULT_ROLE},
        cookies=principal.rest_cookies,
    )
    assert resp.status_code == 200, resp.json()


def test_local_user_is_refused_the_same_kb(world):
    principal = world.principals["local_user"]
    resp = world.client.get(
        f"/api/entries/{NO_DEFAULT_ROLE_ENTRY}",
        params={"kb": NO_DEFAULT_ROLE},
        cookies=principal.rest_cookies,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "KB_NOT_FOUND"


def test_global_user_reads_it_over_mcp_too(world):
    principal = world.principals["global_user"]
    result = world.dispatch_tool(
        "kb_get",
        {"entry_id": NO_DEFAULT_ROLE_ENTRY, "kb_name": NO_DEFAULT_ROLE},
        client_id="characterization-global-access-check",
        readable_kbs=set(principal.readable_kbs) if principal.readable_kbs is not None else None,
        writable_kbs=set(principal.writable_kbs) if principal.writable_kbs is not None else None,
    )
    assert "error" not in result, result


def test_local_user_is_refused_it_over_mcp_too(world):
    principal = world.principals["local_user"]
    result = world.dispatch_tool(
        "kb_get",
        {"entry_id": NO_DEFAULT_ROLE_ENTRY, "kb_name": NO_DEFAULT_ROLE},
        client_id="characterization-global-access-check-2",
        readable_kbs=set(principal.readable_kbs) if principal.readable_kbs is not None else None,
        writable_kbs=set(principal.writable_kbs) if principal.writable_kbs is not None else None,
    )
    assert result.get("error_code") == "KB_NOT_FOUND", result
