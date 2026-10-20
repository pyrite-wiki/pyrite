"""One KB resolver: a caller's KB string becomes one KB, a KB set, or one refusal (#952).

The rule, in `pyrite/services/kb_resolver.py`, is what every surface asks --
REST (`server/authz.py`, the write tier), MCP (`_dispatch_tool`), the CLI and
the services -- and none decides on its own. This file pins the function;
`tests/test_kb_walk.py` pins that every surface asks it.

- Blank means "not named" (an unplaced id, #846/#528), never "unknown".
- A full name the caller may read is its KB.
- An unknown name is `KBNotFoundError` (`KB_NOT_FOUND`); an unscoped caller
  is offered near matches, a scoped one the static hint and no name.
- A KB the caller cannot read answers exactly like an unknown one (ADR-0037
  §4): the same class, message, suggestion and near matches.
- A shortname is link syntax only (ADR-0049 Q3; maintainer, 2026-10-15):
  outside `[[short:id]]` it is refused like an unknown name, and when the
  caller may read the KB it names, the refusal suggests the full name.
- The one existence lookup: config.yaml, the registry cache, then the live
  `kb` table when a database is given, so a KB registered after a server
  started is found (#925).
"""

from __future__ import annotations

import pytest

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.exceptions import KBNotFoundError
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.kb_resolver import (
    HOW_TO_LIST_KBS,
    kb_set,
    readable_kb_named,
    readable_kb_with_shortname,
    resolve_kb,
)
from pyrite.storage.database import PyriteDB


def _config(tmp_path, *, with_private: bool = True) -> PyriteConfig:
    names = ["pub", "notes", "beta", "alpha"] + (["priv"] if with_private else [])
    for n in names:
        (tmp_path / n).mkdir(exist_ok=True)
    kbs = [
        KBConfig(name="pub", path=tmp_path / "pub", default_role="read", shortname="pb"),
        KBConfig(name="notes", path=tmp_path / "notes", default_role="read"),
        KBConfig(name="beta", path=tmp_path / "beta", default_role="read"),
        # A shortname that is another KB's full name: the full name wins (#912).
        KBConfig(name="alpha", path=tmp_path / "alpha", default_role="none", shortname="beta"),
    ]
    if with_private:
        kbs.append(
            KBConfig(name="priv", path=tmp_path / "priv", default_role="none", shortname="pv")
        )
    return PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=tmp_path / "index.db"))


@pytest.fixture
def config(tmp_path):
    return _config(tmp_path)


READS_PUB_AND_NOTES = {"pub", "notes"}


def _refusal(exc: KBNotFoundError) -> dict:
    return {
        "code": exc.error_code,
        "message": str(exc),
        "suggestion": exc.suggestion,
        "did_you_mean": exc.did_you_mean,
    }


class TestBlankIsNotNamed:
    @pytest.mark.parametrize(
        "value", [None, "", "   ", 0, []], ids=["none", "empty", "ws", "0", "[]"]
    )
    def test_names_no_kb(self, config, value):
        assert resolve_kb(config, value, readable_kbs=UNSCOPED) is None
        assert resolve_kb(config, value, readable_kbs=set()) is None

    def test_kb_set_is_the_callers_scope(self, config):
        assert kb_set(config, None, readable_kbs=UNSCOPED) is None
        assert kb_set(config, "", readable_kbs=READS_PUB_AND_NOTES) == READS_PUB_AND_NOTES

    def test_kb_set_hands_back_a_copy(self, config):
        scope = {"pub"}
        out = kb_set(config, None, readable_kbs=scope)
        out.add("x")
        assert scope == {"pub"}


class TestAFullNameIsItsKB:
    def test_unscoped(self, config):
        assert resolve_kb(config, "pub", readable_kbs=UNSCOPED).name == "pub"
        assert resolve_kb(config, "priv", readable_kbs=UNSCOPED).name == "priv"

    def test_within_the_readable_set(self, config):
        assert resolve_kb(config, "pub", readable_kbs=READS_PUB_AND_NOTES).name == "pub"

    def test_a_full_name_wins_over_another_kbs_shortname(self, config):
        assert resolve_kb(config, "beta", readable_kbs=UNSCOPED).name == "beta"

    def test_kb_set_is_that_one_kb(self, config):
        assert kb_set(config, "pub", readable_kbs=UNSCOPED) == {"pub"}
        assert kb_set(config, "pub", readable_kbs=READS_PUB_AND_NOTES) == {"pub"}


class TestAnUnknownNameIsRefused:
    def test_the_code_and_message(self, config):
        with pytest.raises(KBNotFoundError) as e:
            resolve_kb(config, "zz", readable_kbs=UNSCOPED)
        assert e.value.error_code == "KB_NOT_FOUND"
        assert str(e.value) == "KB 'zz' not found"
        assert e.value.did_you_mean == []
        assert e.value.suggestion, "the hint still says how to list KBs"
        assert "zz" not in e.value.suggestion

    def test_near_matches_are_offered(self, config):
        with pytest.raises(KBNotFoundError) as e:
            resolve_kb(config, "note", readable_kbs=UNSCOPED)
        assert e.value.did_you_mean == ["notes"]
        assert "notes" in e.value.suggestion

    def test_a_scoped_caller_is_offered_no_near_match(self, config):
        """0.25.7's decision (#66): a scoped refusal names no KB. 'note' is
        one edit from 'notes', which this caller may read, and still nothing."""
        for readable in (READS_PUB_AND_NOTES, set()):
            with pytest.raises(KBNotFoundError) as e:
                resolve_kb(config, "note", readable_kbs=readable)
            assert e.value.did_you_mean == []
            assert e.value.suggestion == HOW_TO_LIST_KBS

    def test_near_matches_are_capped(self, tmp_path):
        for i in range(12):
            (tmp_path / f"kb-{i:02d}").mkdir()
        config = PyriteConfig(
            knowledge_bases=[
                KBConfig(name=f"kb-{i:02d}", path=tmp_path / f"kb-{i:02d}") for i in range(12)
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        with pytest.raises(KBNotFoundError) as e:
            resolve_kb(config, "kb-", readable_kbs=UNSCOPED)
        assert 0 < len(e.value.did_you_mean) <= 3

    def test_kb_set_refuses_too(self, config):
        with pytest.raises(KBNotFoundError):
            kb_set(config, "zz", readable_kbs=UNSCOPED)


class TestUnreadableAnswersLikeUnknown:
    """The same bytes for a KB that exists and the caller may not read as for
    one that does not exist at all: compared against a config in which the
    private KB really is absent."""

    def test_by_full_name(self, tmp_path):
        with_priv, without = _config(tmp_path), _config(tmp_path, with_private=False)
        with pytest.raises(KBNotFoundError) as a:
            resolve_kb(with_priv, "priv", readable_kbs=READS_PUB_AND_NOTES)
        with pytest.raises(KBNotFoundError) as b:
            resolve_kb(without, "priv", readable_kbs=READS_PUB_AND_NOTES)
        assert _refusal(a.value) == _refusal(b.value)

    def test_by_shortname(self, tmp_path):
        with_priv, without = _config(tmp_path), _config(tmp_path, with_private=False)
        with pytest.raises(KBNotFoundError) as a:
            resolve_kb(with_priv, "pv", readable_kbs=READS_PUB_AND_NOTES)
        with pytest.raises(KBNotFoundError) as b:
            resolve_kb(without, "pv", readable_kbs=READS_PUB_AND_NOTES)
        assert _refusal(a.value) == _refusal(b.value)
        assert "priv" not in str(_refusal(a.value))

    def test_a_shared_shortname_is_the_readable_kbs(self, tmp_path):
        """A private KB that shares the shortname, and is listed first, does
        not change the answer: the shortname is looked up among the KBs the
        caller may read, so `did_you_mean` names the readable one, the same
        as when no private KB exists (#965 review, blocking 3)."""
        (tmp_path / "hidden").mkdir()
        hidden = KBConfig(
            name="hidden", path=tmp_path / "hidden", default_role="none", shortname="pb"
        )
        with_priv = _config(tmp_path)
        with_priv.knowledge_bases.insert(0, hidden)
        without = _config(tmp_path, with_private=False)
        with pytest.raises(KBNotFoundError) as a:
            resolve_kb(with_priv, "pb", readable_kbs=READS_PUB_AND_NOTES)
        with pytest.raises(KBNotFoundError) as b:
            resolve_kb(without, "pb", readable_kbs=READS_PUB_AND_NOTES)
        assert _refusal(a.value) == _refusal(b.value)
        assert a.value.did_you_mean == ["pub"]
        assert "hidden" not in str(_refusal(a.value))

    def test_an_empty_readable_set_reads_nothing(self, config):
        with pytest.raises(KBNotFoundError):
            resolve_kb(config, "pub", readable_kbs=set())


class TestAShortnameIsLinkSyntaxOnly:
    def test_refused_with_the_full_name_suggested(self, config):
        with pytest.raises(KBNotFoundError) as e:
            resolve_kb(config, "pb", readable_kbs=UNSCOPED)
        assert e.value.error_code == "KB_NOT_FOUND"
        assert str(e.value) == "KB 'pb' not found"
        assert e.value.did_you_mean == ["pub"]
        assert "pub" in e.value.suggestion
        assert "[[pb:" in e.value.suggestion, "the suggestion shows where a shortname is valid"

    def test_only_when_the_caller_may_read_that_kb(self, config):
        with pytest.raises(KBNotFoundError) as e:
            resolve_kb(config, "pb", readable_kbs=READS_PUB_AND_NOTES)
        assert e.value.did_you_mean == ["pub"]
        with pytest.raises(KBNotFoundError) as e:
            resolve_kb(config, "pb", readable_kbs={"notes"})
        assert "pub" not in e.value.did_you_mean
        assert "pub" not in e.value.suggestion

    def test_kb_set_refuses_a_shortname(self, config):
        with pytest.raises(KBNotFoundError):
            kb_set(config, "pb", readable_kbs=UNSCOPED)


class TestTheOneExistenceLookup:
    """config.yaml, then the registry cache, then the live `kb` table (#925)."""

    @pytest.fixture
    def db(self, config):
        db = PyriteDB(config.settings.index_path)
        try:
            yield db
        finally:
            db.close()

    def test_a_kb_registered_after_load_is_found_through_the_database(self, config, db, tmp_path):
        (tmp_path / "late").mkdir()
        db.register_kb("late", "generic", str(tmp_path / "late"))
        assert config.get_kb("late") is None, "the cache was filled before the registration"
        with pytest.raises(KBNotFoundError):
            resolve_kb(config, "late", readable_kbs=UNSCOPED)
        assert resolve_kb(config, "late", readable_kbs=UNSCOPED, db=db).name == "late"

    def test_the_policy_and_require_kb_give_the_same_answer(self, config, db, tmp_path):
        from pyrite.services.access_policy import AccessPolicy

        (tmp_path / "late").mkdir()
        db.register_kb("late", "generic", str(tmp_path / "late"))
        policy = AccessPolicy(config, db)
        assert policy.kb_exists("late")
        assert config.require_kb("late", db=db).name == "late"

    def test_a_config_rows_kb_is_the_config_entry_with_every_attribute(self, db, tmp_path):
        """The server seeds a `kb` row (source "config") for every config.yaml
        KB. That row never stands in for the entry: resolved through the
        config that defines it, the KB carries `read_only` and `shortname`;
        resolved through a config that does not, it is not found -- never a
        bare KB built from the row, which would let a write into a read-only
        KB through (review of #965, blocking 1)."""
        (tmp_path / "ro").mkdir()
        defines = PyriteConfig(
            knowledge_bases=[
                KBConfig(name="ro", path=tmp_path / "ro", read_only=True, shortname="r")
            ],
            settings=Settings(index_path=tmp_path / "index.db"),
        )
        db.register_kb("ro", "generic", str(tmp_path / "ro"), source="config")
        kb = resolve_kb(defines, "ro", readable_kbs=UNSCOPED, db=db)
        assert kb.read_only is True and kb.shortname == "r"
        does_not = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
        with pytest.raises(KBNotFoundError):
            resolve_kb(does_not, "ro", readable_kbs=UNSCOPED, db=db)

    def test_a_user_rows_kb_carries_the_rows_attributes(self, db, tmp_path):
        (tmp_path / "added").mkdir()
        db.register_kb("added", "generic", str(tmp_path / "added"), default_role="read")
        config = PyriteConfig(settings=Settings(index_path=tmp_path / "index.db"))
        kb = resolve_kb(config, "added", readable_kbs=UNSCOPED, db=db)
        assert (kb.name, kb.default_role, kb.read_only) == ("added", "read", False)

    def test_require_kb_delegates(self, config):
        with pytest.raises(KBNotFoundError) as e:
            config.require_kb("pb")
        assert e.value.did_you_mean == ["pub"]
        assert config.require_kb("pub").name == "pub"


class TestThePolicyAsksTheResolver:
    """`AccessPolicy.authorize(KB)` resolves first (existence, readability,
    shortname), then checks the rung; `require` raises the resolver's refusal."""

    @pytest.fixture
    def policy(self, config):
        from pyrite.config import AuthConfig
        from pyrite.services.access_policy import AccessPolicy

        # An auth-enabled instance whose anonymous visitor may read.
        config.settings.auth = AuthConfig(enabled=True, anonymous_tier="read")
        db = PyriteDB(config.settings.index_path)
        try:
            yield AccessPolicy(config, db)
        finally:
            db.close()

    def test_a_shortname_is_not_found_for_an_operator_key(self, policy):
        from pyrite.services.access_policy import KB, NOT_FOUND, Action, Principal

        p = Principal.from_api_key("admin")
        assert policy.authorize(p, Action.KB_READ, KB("pub")).allowed
        assert policy.authorize(p, Action.KB_READ, KB("pb")).code == NOT_FOUND
        with pytest.raises(KBNotFoundError) as e:
            policy.require(p, Action.KB_WRITE, KB("pb"))
        assert e.value.did_you_mean == ["pub"]

    def test_a_private_kbs_shortname_is_not_found_for_a_visitor(self, policy):
        from pyrite.services.access_policy import KB, NOT_FOUND, Action, Principal

        p = Principal.anonymous("read")
        assert policy.authorize(p, Action.KB_READ, KB("pub")).allowed
        assert policy.authorize(p, Action.KB_READ, KB("priv")).code == NOT_FOUND
        with pytest.raises(KBNotFoundError) as e:
            policy.require(p, Action.KB_READ, KB("pv"))
        assert "priv" not in str(_refusal(e.value))
        with pytest.raises(KBNotFoundError) as e:
            policy.require(p, Action.KB_READ, KB("pb"))
        assert e.value.did_you_mean == ["pub"]


class TestALinkPrefixIsLookedUpAmongReadableKbs:
    """`readable_kb_named`, the lookup a `[[prefix:id]]` uses (#965 round 3):
    the readable KB with that full name, else the readable KB with that
    shortname. A KB the caller cannot read is never consulted, so it never
    changes the answer."""

    @staticmethod
    def _config(tmp_path, private_first):
        for name in ("notes", "team"):
            (tmp_path / name).mkdir(exist_ok=True)
        private = KBConfig(name="notes", path=tmp_path / "notes", default_role="none")
        readable = KBConfig(name="team", path=tmp_path / "team", shortname="notes")
        kbs = [private, readable] if private_first else [readable, private]
        return PyriteConfig(knowledge_bases=kbs, settings=Settings(index_path=tmp_path / "i.db"))

    @pytest.mark.parametrize("private_first", [True, False])
    def test_a_private_full_name_never_hides_a_readable_shortname(self, tmp_path, private_first):
        config = self._config(tmp_path, private_first)
        assert readable_kb_named(config, "notes", {"team"}).name == "team"
        assert readable_kb_named(config, "team", {"team"}).name == "team"
        assert readable_kb_named(config, "notes", {"other"}) is None

    def test_the_unscoped_caller_gets_the_full_name_first(self, tmp_path):
        config = self._config(tmp_path, private_first=False)
        assert readable_kb_named(config, "notes", None).name == "notes"
        assert readable_kb_with_shortname(config, "notes", None).name == "team"
