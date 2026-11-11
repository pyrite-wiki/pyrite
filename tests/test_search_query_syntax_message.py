"""QUERY_SYNTAX must name the token the caller wrote, not SQLite's fragment (#67).

`detention AND third-party-doctrine` reaches MATCH unquoted because the query
carries an operator, and SQLite answers `no such column: party` — a fragment of
a token the caller never typed, which sends the reader looking for a schema
problem. The message now names the real token. Whenever the error does not name
a token we can find, the previous text is kept untouched.
"""

import sqlite3

import pytest

from pyrite.exceptions import QuerySyntaxError
from pyrite.services.search_service import SearchService


class _RaisingDB:
    """A db whose search always fails with the given error."""

    def __init__(self, error: sqlite3.OperationalError):
        self.error = error

    def search(self, **kwargs):
        raise self.error


def _message(query: str, error_text: str) -> str:
    service = SearchService(_RaisingDB(sqlite3.OperationalError(error_text)))
    with pytest.raises(QuerySyntaxError) as excinfo:
        service._db_search(query=query)
    return str(excinfo.value)


def test_names_the_token_the_caller_wrote():
    message = _message("detention AND third-party-doctrine", "no such column: party")

    assert "third-party-doctrine" in message
    assert "no such column" not in message
    # The suggestion quotes the token it just named.
    assert '"third-party-doctrine"' in message


def test_prefers_the_token_the_fragment_came_from():
    message = _message("detention AND cross-link AND third-party-doctrine", "no such column: party")

    assert "third-party-doctrine" in message
    assert "cross-link" not in message


def test_a_column_the_query_does_not_name_is_not_the_callers_fault():
    """FTS5 names a column it read *from the query*; a name the query never
    contains is the SQL itself missing a column -- a storage fault, not a
    token to blame on the caller (#431)."""
    from pyrite.exceptions import StorageError

    service = SearchService(_RaisingDB(sqlite3.OperationalError("no such column: zebra")))
    with pytest.raises(StorageError) as excinfo:
        service._db_search(query="detention AND third-party-doctrine")
    assert "no such column: zebra" in str(excinfo.value)
    assert not isinstance(excinfo.value, QuerySyntaxError)


@pytest.mark.control(reason="the local operator keeps the driver's text, as before")
def test_other_operational_errors_keep_the_previous_text():
    from pyrite.services.acting import local_process

    with local_process(True):  # the CLI's operator
        message = _message("detention AND third-party-doctrine", "unterminated string")

    assert "unterminated string" in message
    assert "third-party-doctrine" not in message


def test_a_network_caller_never_sees_the_drivers_text():
    """The FTS parser's own words are the operator's (ADR-0052 N3): a
    network caller reads Pyrite's message with the detail withheld."""
    message = _message("detention AND third-party-doctrine", "unterminated string at /srv/x")

    assert message.startswith("Query could not be parsed: the server log has the details.")
    assert "/srv/x" not in message


def test_error_code_is_unchanged():
    service = SearchService(_RaisingDB(sqlite3.OperationalError("no such column: party")))

    with pytest.raises(QuerySyntaxError) as excinfo:
        service._db_search(query="detention AND third-party-doctrine")

    assert excinfo.value.error_code == "QUERY_SYNTAX"


# ---------------------------------------------------------------------------
# A non-syntax OperationalError is a server failure, not the caller's fault
# (#414 fix round 2): "database is locked", a disk I/O error, a missing
# table, or a database file that can't be opened must stay a 5xx and be
# logged -- not get relabeled QUERY_SYNTAX/400 just because they are also
# OperationalError under the hood.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error_text",
    [
        "database is locked",
        "disk I/O error",
        "no such table: entry_fts",
        "unable to open database file",
    ],
)
def test_non_syntax_operational_errors_are_not_query_syntax_error(error_text):
    from pyrite.exceptions import StorageError

    service = SearchService(_RaisingDB(sqlite3.OperationalError(error_text)))
    with pytest.raises(StorageError) as excinfo:
        service._db_search(query="hello")
    assert error_text in str(excinfo.value)
    # And specifically not reclassified as the caller's query being bad.
    assert not isinstance(excinfo.value, QuerySyntaxError)


class TestOnlyQueryShapedErrorsAreTheCallersFault:
    """#428 delta cold read: the parse-error allow-list must match what FTS5
    says about a bad query, and must not match a schema fault that happens
    to use the same words."""

    @staticmethod
    def _fts_error(query: str) -> str:
        import sqlite3

        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
        try:
            con.execute("SELECT * FROM t WHERE t MATCH ?", (query,)).fetchall()
        except sqlite3.OperationalError as exc:
            return str(exc)
        finally:
            con.close()
        raise AssertionError(f"{query!r} parsed")

    @pytest.mark.parametrize(
        "query",
        ["x AND " + "(" * 200 + "a" + ")" * 200, "x OR NEAR(a b, y)", "a:b", '"open'],
        ids=["stack-overflow", "near-arg", "column-filter", "unterminated"],
    )
    def test_a_bad_query_is_a_syntax_error(self, query):
        from pyrite.services.search_service import _looks_like_query_syntax_error

        message = self._fts_error(query)
        assert _looks_like_query_syntax_error(message, query), message

    def test_a_missing_table_column_is_not_the_callers_fault(self):
        import sqlite3

        from pyrite.services.search_service import _looks_like_query_syntax_error

        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE entry (id TEXT)")
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            con.execute("SELECT e.fips FROM entry e").fetchall()
        con.close()
        assert not _looks_like_query_syntax_error(str(exc_info.value), "fips"), str(exc_info.value)
