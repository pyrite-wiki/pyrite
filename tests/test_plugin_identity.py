"""`pyrite.plugins.identity` -- the person-argument rule, case by case.

Tools that record a person are tested through the real transport in
`test_mcp_tools_record_the_caller.py`; this pins the rule itself (ADR-0040,
alpha), so an extension author reading one tool need not infer it.
"""

import pytest

from pyrite.exceptions import Forbidden, ValidationError
from pyrite.plugins.identity import CALLER, OPTIONAL, person_key, resolve_person_arguments
from pyrite.services.access_policy import Principal

ALICE = Principal.user(7, "write")


def test_a_signed_in_user_is_keyed_by_account_id_and_nothing_else_has_a_key():
    assert person_key(ALICE) == "user:7"
    for principal in (
        Principal.local(),
        Principal.from_api_key("admin"),
        Principal.anonymous("read"),
        None,
    ):
        assert person_key(principal) is None


def test_a_signed_in_caller_is_recorded_whether_or_not_they_name_themselves():
    assert resolve_person_arguments({}, {"who": CALLER}, ALICE) == {"who": "user:7"}
    assert resolve_person_arguments({"who": " "}, {"who": CALLER}, ALICE) == {"who": "user:7"}
    assert resolve_person_arguments({"who": "user:7"}, {"who": CALLER}, ALICE) == {"who": "user:7"}


@pytest.mark.parametrize("other", ["bob", "7", "user:8", "alice"])
def test_any_other_value_from_a_signed_in_caller_is_refused(other):
    with pytest.raises(Forbidden) as caught:
        resolve_person_arguments({"who": other}, {"who": CALLER}, ALICE)
    assert caught.value.error_code == "FORBIDDEN"
    assert "Omit 'who'" in caught.value.suggestion


def test_the_callers_arguments_are_not_changed():
    arguments = {"who": None, "items": [{"who": None}]}
    resolve_person_arguments(arguments, {"who": CALLER, "items[].who": OPTIONAL}, ALICE)
    assert arguments == {"who": None, "items": [{"who": None}]}


def test_an_optional_argument_left_out_records_no_one():
    assert resolve_person_arguments({}, {"who": OPTIONAL}, ALICE) == {}
    assert resolve_person_arguments({}, {"who": OPTIONAL}, Principal.local()) == {}


def test_a_list_items_person_field_follows_the_same_rule():
    spec = {"children[].who": OPTIONAL}
    resolved = resolve_person_arguments({"children": [{"who": "user:7"}, {}]}, spec, ALICE)
    assert resolved == {"children": [{"who": "user:7"}, {}]}
    with pytest.raises(Forbidden):
        resolve_person_arguments({"children": [{"who": "bob"}]}, spec, ALICE)


def test_a_scoped_caller_with_no_account_records_no_one():
    with pytest.raises(Forbidden):
        resolve_person_arguments({}, {"who": CALLER}, Principal.anonymous("write"))
    with pytest.raises(Forbidden):
        resolve_person_arguments({}, {"who": CALLER}, None, scoped=True)


def test_without_a_session_identity_the_argument_is_a_label():
    for principal in (Principal.local(), Principal.from_api_key("write"), None):
        assert resolve_person_arguments({"who": "bob"}, {"who": CALLER}, principal) == {
            "who": "bob"
        }


def test_without_a_session_identity_a_missing_caller_argument_is_refused_clearly():
    with pytest.raises(ValidationError) as caught:
        resolve_person_arguments({}, {"who": CALLER}, Principal.local())
    assert "who" in str(caught.value) and "who" in caught.value.suggestion
