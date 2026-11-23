"""Every integer field read from a file goes through ``safe_int`` (#1247).

A type's ``from_frontmatter`` that called ``int()`` on a stored value raised on
``.inf`` / ``.nan`` / ``high``, and the file then failed to load as its declared
type. The fields come from the registries (core and plugin dataclasses), so a
new ``int`` field cannot be added without joining this test.
"""

import dataclasses
import typing

import pytest

from pyrite.models import ENTRY_TYPE_REGISTRY
from pyrite.plugins import get_registry
from pyrite.utils.yaml import load_yaml

SHAPES = [".inf", "-.inf", ".nan", "high", "null"]


def _int_fields():
    types = dict(ENTRY_TYPE_REGISTRY)
    types.update(get_registry().get_all_entry_types())
    cases = []
    for name, cls in sorted(types.items()):
        if not dataclasses.is_dataclass(cls):
            continue
        hints = typing.get_type_hints(cls)
        for f in dataclasses.fields(cls):
            hint = hints.get(f.name)
            if hint is int or (typing.get_args(hint) and int in typing.get_args(hint)):
                cases.append((name, cls, f.name))
    return cases


CASES = _int_fields()


def test_int_fields_were_found():
    assert {"rank", "adr_number", "reputation", "writeup_count"} <= {c[2] for c in CASES}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize(
    ("type_name", "cls", "field"), CASES, ids=[f"{c[0]}.{c[2]}" for c in CASES]
)
def test_odd_integer_value_loads_with_the_default(type_name, cls, field, shape):
    default = cls.from_frontmatter(load_yaml(f"id: x\ntitle: T\ntype: {type_name}\n"), "")
    meta = load_yaml(f"id: x\ntitle: T\ntype: {type_name}\n{field}: {shape}\n")
    entry = cls.from_frontmatter(meta, "body")
    if (type_name, field, shape) == ("task", "priority", "high"):
        pytest.skip("task priority reads words ('high') by its own policy")
    assert getattr(entry, field) == getattr(default, field)
    assert entry.body == "body"
