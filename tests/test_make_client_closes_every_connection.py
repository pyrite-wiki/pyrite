"""The `make_client` fixture must leave no live SQLite connection behind.

This is the regression guard for the defect a cold read found on the branch
that introduced the fixture: `create_app()` eagerly seeds the KB registry,
which opens a SECOND `PyriteDB` on the same file and parks it on
`application.state.pyrite_db`. Nothing in `pyrite/` ever closes that one, and
some routes read it directly rather than through dependency injection, so a
fixture that closes only the DB it constructed itself leaves an open WAL
connection -- the exact leak the fixture was written to eliminate
(`tests-leak-open-pyritedb-connections-into-temporarydirectory-teardown`).

The leak was invisible on the branch because the fixture also moved to
`tmp_path`, which pytest does not delete during the run. That is a real
mitigation but it is a *default* (`tmp_path_retention_policy`), not a
guarantee: flip the policy and every leaked connection is back to racing
`rmtree`. So this asserts on the connection, not on whether a directory
removal happened to survive.

The check has to run AFTER `make_client`'s teardown, which is why it lives in a
fixture of its own: pytest tears fixtures down in reverse order of setup, so
requesting the probe before `make_client` makes it finalize after it. That
keeps the test self-contained -- an earlier version split it across two tests
and shared a module-level dict, which broke whenever xdist put them on
different workers.
"""

import pytest


@pytest.fixture
def wal_teardown_probe():
    """Assert, after `make_client` has torn down, that no WAL/SHM file survived.

    Requested BEFORE `make_client` in the test signature: fixtures finalize in
    reverse order of setup, so this runs after the client fixture's teardown,
    which is the moment the leak is visible.
    """
    state: dict = {}
    yield state

    work_dir = state["dir"]
    leftovers = sorted(p.name for p in work_dir.iterdir())
    live = [name for name in leftovers if name.endswith(("-wal", "-shm"))]
    assert not live, (
        f"SQLite WAL/SHM files are still live after teardown: {leftovers}. "
        "Some connection on this database was not closed -- most likely "
        "application.state.pyrite_db, which create_app opens and nothing in "
        "pyrite/ closes."
    )


def test_fixture_leaves_no_live_wal_connection(wal_teardown_probe, make_client):
    """One self-contained test: build a client, then let the fixtures tear down.

    The premise is asserted here -- create_app parks a `PyriteDB` on
    `app.state` that nothing in `pyrite/` closes, and since #1077 that is the
    `db` the fixture hands back (one connection, not a second beside it) --
    and the WAL assertion runs in `wal_teardown_probe`'s finalizer, after
    `make_client`'s teardown.
    """
    client, config, db = make_client()
    assert client.get("/api/kbs").status_code == 200

    app_state_db = client.app.state.pyrite_db
    assert app_state_db is not None, "create_app no longer parks a db on app.state"
    assert app_state_db is db, (
        "make_client hands back a database other than the app's own: the "
        "fixture now owns two connections and must close both"
    )

    wal_teardown_probe["dir"] = config.settings.index_path.parent
