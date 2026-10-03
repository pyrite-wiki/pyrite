"""The `experimental` marker is applied by path, in one place, and never to a
security property (#657).

A red required check must mean the core broke. Tests of experimental surfaces
(`kb/designs/alpha-supported-surface.md`) carry the `experimental` marker,
which the root conftest applies from the single mapping in
`tests/experimental_surface.py`; the gating CI job deselects them and a
separate, non-blocking job runs them against a known-failures ratchet.

These tests hold the mapping to its contract on a real collection of the
suite, so the marker that is asserted is the marker pytest actually applied:

- every experimental pattern still matches a collected test (a renamed file
  cannot quietly fall out of the mapping into the gate, or out of both);
- every security pattern still matches a collected test, and none of the
  tests it matches is experimental, whatever surface it goes through;
- a test marked ``@pytest.mark.core`` (the local smoke set
  scripts/test-affected always runs) is never experimental, or the pre-push
  hook would deselect part of the set it promises to run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests import experimental_surface as surface

REPO = Path(__file__).resolve().parent.parent

_DUMP_PLUGIN = textwrap.dedent(
    """
    import json, os

    def pytest_collection_finish(session):
        rows = [
            {
                "nodeid": item.nodeid,
                "experimental": item.get_closest_marker("experimental") is not None,
                "core": item.get_closest_marker("core") is not None,
            }
            for item in session.items
        ]
        with open(os.environ["PYRITE_MARKER_DUMP"], "w") as fh:
            json.dump(rows, fh)
    """
)


@pytest.fixture(scope="module")
def collected(tmp_path_factory) -> list[dict]:
    """Every test of the suite as pytest collects it, with its markers."""
    work = tmp_path_factory.mktemp("marker_dump")
    (work / "pyrite_marker_dump.py").write_text(_DUMP_PLUGIN)
    out = work / "rows.json"
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(work), os.environ.get("PYTHONPATH", "")]),
        "PYRITE_MARKER_DUMP": str(out),
    }
    env.pop("PYTEST_ADDOPTS", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "",  # no deselection: every test, slow and e2e included
            "-p",
            "pyrite_marker_dump",
            "-p",
            "no:cacheprovider",
            "tests",
            "extensions",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.exists(), proc.stdout[-3000:] + proc.stderr[-3000:]
    rows = json.loads(out.read_text())
    assert len(rows) > 1000, f"collected only {len(rows)} tests"
    return rows


# A guard that holds before the marker exists too (see verify-red's control).
VACUOUS = pytest.mark.control(
    reason=(
        "tests/experimental_surface.py is test-side, so verify-red overlays it on the "
        "base, where no conftest applies the marker: a never-experimental invariant "
        "holds there vacuously"
    )
)


def _matching(rows: list[dict], pattern: str) -> list[dict]:
    return [r for r in rows if surface.matches(r["nodeid"], pattern)]


class TestTheMarker:
    def test_is_registered(self):
        import tomllib

        pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())
        markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
        assert any(m.startswith("experimental:") for m in markers), markers

    @VACUOUS
    def test_the_mapping_cites_the_surface_list(self):
        assert surface.SURFACE_LIST == "kb/designs/alpha-supported-surface.md"
        assert surface.SURFACE_LIST in (surface.__doc__ or "")

    def test_the_conftest_applies_the_mapping(self, collected):
        """What pytest marked is exactly what the mapping says, test by test."""
        wrong = [
            r["nodeid"]
            for r in collected
            if r["experimental"] != surface.is_experimental(r["nodeid"])
        ]
        assert not wrong, wrong[:20]

    def test_both_sets_are_non_empty(self, collected):
        experimental = sum(r["experimental"] for r in collected)
        assert 0 < experimental < len(collected)


class TestEveryPatternMatches:
    """Acceptance 7: every experimental path in the surface list matches at
    least one test. A surface with no tests says so (`paths=()`) instead."""

    @pytest.mark.parametrize(
        "pattern",
        [p for s in surface.EXPERIMENTAL for p in s.paths],
    )
    def test_experimental_pattern_matches_a_test(self, collected, pattern):
        hits = _matching(collected, pattern)
        assert hits, f"{pattern!r} matches no collected test -- renamed or deleted?"
        assert any(r["experimental"] for r in hits), (
            f"every test {pattern!r} matches is overridden as a security property; "
            "drop the pattern or the override"
        )

    @VACUOUS
    @pytest.mark.parametrize("pattern", sorted(surface.NEVER_EXPERIMENTAL))
    def test_security_pattern_matches_a_test(self, collected, pattern):
        assert _matching(collected, pattern), (
            f"{pattern!r} matches no collected test -- a renamed security test "
            "would fall out of the gate unnoticed"
        )

    @VACUOUS
    def test_surfaces_without_tests_are_the_known_ones(self):
        untested = {s.name for s in surface.EXPERIMENTAL if not s.paths}
        assert untested == {
            "`mcp-setup` (experimental until #582)",
            "`protocol` CLI group",
            "Claude Code plugin",
        }


@VACUOUS
class TestSecurityIsNeverExperimental:
    """Acceptance 2: authorization, read scoping, containment of paths inside
    a KB, credential handling and the characterization oracle stay in the
    gate whatever surface they go through."""

    @pytest.mark.parametrize("pattern", sorted(surface.NEVER_EXPERIMENTAL))
    def test_no_security_test_is_experimental(self, collected, pattern):
        leaked = [r["nodeid"] for r in _matching(collected, pattern) if r["experimental"]]
        assert not leaked, leaked[:20]

    def test_the_characterization_oracle_is_in_the_gate(self, collected):
        oracle = [r for r in collected if r["nodeid"].startswith("tests/characterization/")]
        assert oracle and not any(r["experimental"] for r in oracle)

    def test_every_security_entry_names_its_property(self):
        for pattern, prop in surface.NEVER_EXPERIMENTAL.items():
            assert prop in surface.SECURITY_PROPERTIES, (pattern, prop)


@VACUOUS
def test_no_core_smoke_test_is_experimental(collected):
    """`@pytest.mark.core` is the set scripts/test-affected always runs, and
    the pre-push hook deselects experimental tests: the two must not meet."""
    both = [r["nodeid"] for r in collected if r["core"] and r["experimental"]]
    assert not both, both[:20]


@VACUOUS
class TestMatching:
    @pytest.mark.parametrize(
        ("nodeid", "pattern", "expected"),
        [
            ("tests/test_a.py::test_x", "tests/test_a.py", True),
            ("tests/test_a.py::TestK::test_x", "tests/test_a.py::TestK::*", True),
            ("tests/test_a.py::TestKX::test_x", "tests/test_a.py::TestK::*", False),
            ("tests/test_ab.py::test_x", "tests/test_a.py", False),
            ("tests/test_task_dag.py::test_x", "tests/test_task*.py", True),
            ("extensions/social/tests/test_s.py::T::t", "extensions/*/tests/*", True),
            ("tests/b/test_c.py::test_x[postgres]", "tests/b/test_c.py::*[postgres]", True),
            ("tests/b/test_c.py::test_x[sqlite]", "tests/b/test_c.py::*[postgres]", False),
        ],
    )
    def test_patterns(self, nodeid, pattern, expected):
        assert surface.matches(nodeid, pattern) is expected

    def test_a_security_override_wins(self):
        nodeid = "tests/test_export_service.py::TestPathTraversalPrevention::test_x"
        assert surface.matches(nodeid, "tests/test_export_service.py")
        assert not surface.is_experimental(nodeid)
