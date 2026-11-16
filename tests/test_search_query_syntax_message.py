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


def _message(query: str, error_text: str, *, caller_query: str | None = None) -> str:
    """The refusal for ``error_text``; the caller's own query is ``query``
    unless ``caller_query`` says otherwise (an expanded query is not theirs)."""
    service = SearchService(_RaisingDB(sqlite3.OperationalError(error_text)))
    with pytest.raises(QuerySyntaxError) as excinfo:
        service._db_search(
            query=query, caller_query=query if caller_query is None else caller_query
        )
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


def test_a_network_caller_sees_the_parsers_detail_about_their_own_query():
    """A syntax error is about the caller's own query: the FTS parser
    is given only the query, so its words are the caller's to see, over the
    network too. A fault that is not a syntax error stays the operator's
    (``test_a_column_the_query_does_not_name_is_not_the_callers_fault`` and
    the storage cases below)."""
    from pyrite.services.acting import local_process

    with local_process(False):
        message = _message('detention AND "third', "unterminated string")

    assert message.startswith("Query could not be parsed: unterminated string.")
    assert "the server log has the details" not in message


@pytest.mark.control(
    reason="dev withheld every FTS message; red against this branch's first showing rule (60be99f6)"
)
def test_a_network_caller_never_sees_driver_text_beyond_their_query():
    """A message that carries a syntax marker but more than the caller's own
    query (here a path) is the driver's: withheld from a network caller."""
    from pyrite.services.acting import local_process

    with local_process(False):
        message = _message("detention AND third-party-doctrine", "unterminated string at /srv/x")

    assert message.startswith("Query could not be parsed: the server log has the details.")
    assert "/srv/x" not in message


_WITHHELD_CONTROL = pytest.mark.control(
    reason="dev withheld every FTS message; red against this branch's first showing rule (60be99f6)"
)


@pytest.mark.parametrize(
    ("caller_query", "error_text", "shown"),
    [
        ("AND falcon", 'fts5: syntax error near "AND"', True),
        ("falcon AND", 'fts5: syntax error near ""', True),
        ("*falcon", "unknown special query: falcon", True),
        ("NEAR(a b, x)", 'expected integer, got "x"', True),
        # The quoted part is not in the caller's query: not theirs to see.
        pytest.param(
            *("falcon AND", 'fts5: syntax error near "zebra"', False), marks=_WITHHELD_CONTROL
        ),
        pytest.param(
            *("falcon", "unknown special query: provider-term", False), marks=_WITHHELD_CONTROL
        ),
        # Not one of the parser's templates, whatever it contains.
        pytest.param(
            *("falcon AND", "fts5: syntax error near falcon in /srv/x", False),
            marks=_WITHHELD_CONTROL,
        ),
    ],
)
def test_only_a_parser_template_quoting_the_callers_own_query_is_shown(
    caller_query, error_text, shown
):
    from pyrite.services.acting import local_process

    with local_process(False):
        message = _message(caller_query, error_text)
    assert (error_text in message) is shown, message
    assert ("the server log has the details" in message) is not shown, message


def test_a_column_token_is_named_only_from_the_callers_own_query():
    """The token named as a column reference comes from the query the caller
    wrote; a token only an expansion added is not named back to them."""
    from pyrite.services.acting import local_process

    with local_process(False):
        message = _message(
            "falcon OR third-party-doctrine", "no such column: party", caller_query="falcon"
        )
    assert "third-party" not in message and "party" not in message, message
    assert "the server log has the details" in message


@pytest.mark.control(
    reason="dev withheld every FTS message; red against this branch's first showing rule (60be99f6)"
)
def test_an_expanded_querys_parse_error_does_not_quote_a_provider_term(tmp_path, monkeypatch):
    """Expansion adds the provider's terms, unquoted, after an OR, so the
    query is not sanitized: the FTS parser's error can quote a term that is
    the provider's, not the caller's. It is withheld from a network caller."""
    from pyrite.config import KBConfig
    from pyrite.services.acting import local_process
    from tests._world import build_world

    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: Falcon\ntype: note\n---\nb\n")
    world = build_world(tmp_path / "w", kbs=[KBConfig(name="k", path=kb, kb_type="generic")])
    try:
        world.index("k")

        class Provider:
            def expand(self, _query):
                return ["NEAR(a b, providersecret)"]

        service = SearchService(world.db)
        monkeypatch.setattr(service, "_get_expansion_service", lambda: Provider())
        # The caller wrote "falcon"; the provider added the rest.
        with local_process(False), pytest.raises(QuerySyntaxError) as network:
            service.search("falcon", expand=True, caller_wrote_query=True)
        assert "providersecret" not in str(network.value), str(network.value)
        with local_process(True), pytest.raises(QuerySyntaxError) as local:
            service.search("falcon", expand=True, caller_wrote_query=True)
        assert "providersecret" in str(local.value)  # the operator's to see
    finally:
        world.close()


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
