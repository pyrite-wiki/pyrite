"""The suite writes its caches outside the checkout.

tests/test_run_tutorial_directives.py asserts a tutorial run leaves the
checkout as it found it, by listing the repository root before and after.
Under xdist that listing runs beside every other test, so any test that writes
into the root makes it fail at random. On 2026-10-03 Hypothesis did: even with
``database=None`` (tests/test_storage_invariants.py) it caches the constants it
collects from source in ``<cwd>/.hypothesis/constants``, and #708's CI failed
on a test its change never touched. The repo-root ``conftest.py`` points
Hypothesis's home at a session temp dir, the same way it isolates git and the
Pyrite config.
"""

from pathlib import Path

from hypothesis.configuration import storage_directory

REPO = Path(__file__).resolve().parent.parent


def test_hypothesis_storage_is_outside_the_checkout():
    path = Path(storage_directory("constants").path).resolve()
    assert not path.is_relative_to(REPO), f"Hypothesis writes into the checkout: {path}"
