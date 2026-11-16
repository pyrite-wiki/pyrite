"""The YAML probes in ``storage/file_operations.py`` read one single-line
scalar and never walk what they load.

``test_untrusted_yaml_is_bounded.py`` allows them to call ruamel's ``load``
without ``check_yaml_limits`` on exactly that ground. Anchors and aliases in
a probe are harmless only while nothing walks the loaded value: an alias is a
reference, so building the document is linear, but converting a nested
collection to plain values (``_plain``) copies every reference and grows as
the fan-out to the power of the depth. ``_scalar_of`` (how a path segment
reads as a key) is the one probe that converted what it loaded. ruamel
refuses a nested collection as a key (it is unhashable), so only a flat tuple
of scalars could reach ``_plain``; it now takes a scalar key only, and a
segment that is not one line is its own text.
"""

from __future__ import annotations

import pytest

from pyrite.storage.file_operations import _scalar_of


def _fan(levels: int, fan: int) -> str:
    parts = ["&l0 [" + ", ".join(["x"] * fan) + "]"]
    for i in range(1, levels):
        parts.append(f"&l{i} [" + ", ".join([f"*l{i - 1}"] * fan) + "]")
    return "[" + ", ".join(parts) + "]"


@pytest.mark.control(
    reason="ruamel refuses a nested collection as a key (unhashable), so the "
    "exponential walk is unreachable today; the scalar-only rule keeps it so"
)
def test_a_collection_key_is_not_a_scalar_and_is_not_walked():
    segment = _fan(3, 3)
    assert _scalar_of(segment) == segment


def test_a_segment_of_more_than_one_line_is_its_own_text():
    assert _scalar_of("? true\n") == "? true\n"


@pytest.mark.control(reason="the probe still does its job")
def test_a_scalar_segment_still_reads_as_yaml_reads_it():
    assert _scalar_of("true") is True
    assert _scalar_of("1") == 1
