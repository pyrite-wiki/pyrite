"""Every `uses:` in a workflow must name a full 40-character commit SHA.

A tag such as `actions/checkout@v4` can be moved by whoever controls the
action's repository; a commit SHA cannot. Pins keep the tag as a trailing
comment, and `.github/dependabot.yml` keeps them current.
"""

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*-?\s*uses:\s*(?P<ref>\S+)", re.MULTILINE)
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def _unpinned(text: str) -> list[str]:
    out = []
    for m in USES.finditer(text):
        ref = m.group("ref").strip("'\"")
        if ref.startswith("./"):  # a local action lives in this repo
            continue
        if not PINNED.match(ref):
            out.append(ref)
    return out


def test_workflows_exist():
    assert WORKFLOWS, "no workflows found; the pin test would pass vacuously"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_uses_is_pinned_to_a_commit_sha(workflow):
    assert _unpinned(workflow.read_text()) == []


def test_the_check_rejects_a_tag_and_a_short_sha():
    sample = (
        "steps:\n"
        "  - uses: actions/checkout@v4\n"
        "  - uses: actions/cache@0057852\n"
        "  - uses: ./local\n"
        f"  - uses: actions/setup-node@{'a' * 40} # v4\n"
    )
    assert _unpinned(sample) == ["actions/checkout@v4", "actions/cache@0057852"]
