"""Grammar, directive, and skill-sync tests for docs/tutorials/agentic-development.md (#1894, #1895)."""

import re
import shlex
from pathlib import Path

from tests.test_doc_kb_lifecycle_skill import (
    _typers_click_exception,
    commands,
    parse_command,
)
from tests.test_run_tutorial_directives import KNOWN_DIRECTIVES, runner  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
TUTORIAL = REPO / "docs" / "tutorials" / "agentic-development.md"
SKILLS_DIR = REPO / "extensions" / "software-kb" / "skills"


def test_every_pyrite_command_on_agentic_development_tutorial_parses():
    errors = []
    count = 0
    for example in commands(TUTORIAL.read_text(encoding="utf-8")):
        count += 1
        try:
            parse_command(example)
        except _typers_click_exception() as exc:
            errors.append(f"{example}: {exc.format_message()}")
    assert count >= 25, f"Expected at least 25 pyrite commands on the page, found {count}"
    assert not errors, "\n".join(errors)


def test_agentic_development_directives_and_seed_fixture(runner):  # noqa: F811
    items = runner.extract_items(TUTORIAL)
    directives = [text for kind, text in items if kind == "directive"]
    assert directives
    for text in directives:
        assert text.split()[0] in KNOWN_DIRECTIVES, text

    seeds = [t.split() for k, t in items if k == "directive" and t.startswith("seed ")]
    assert len(seeds) == 1
    fixture = REPO / seeds[0][1]
    assert list(fixture.rglob("*.md")), f"{fixture} has no entries to seed"

    for index, (kind, text) in enumerate(items):
        if kind != "block" or "pyrite search" not in text:
            continue
        follows = items[index + 1] if index + 1 < len(items) else ("", "")
        assert follows[0] == "directive" and follows[1].startswith("expect-ids"), (
            f"search block missing expect-ids directive:\n{text}"
        )


def _sw_session_verbs(markdown_text: str) -> list[str]:
    """Ordered unique `pyrite sw <verb>` workflow verbs in a section or skill pair."""
    verbs: list[str] = []
    # Focus on the session cycle verbs shared between the hand cycle and the session skills.
    session_set = {
        "board",
        "backlog",
        "pull-next",
        "transition",
        "context-for-item",
        "claim",
        "log",
        "submit",
    }
    for cmd in commands(markdown_text):
        parts = shlex.split(cmd)
        if len(parts) >= 3 and parts[0] == "pyrite" and parts[1] == "sw":
            verb = parts[2]
            if verb in session_set and verb not in verbs:
                verbs.append(verb)
    return verbs


def test_hand_cycle_and_session_skills_name_the_same_sw_verbs_in_order():
    """#1895 acceptance 4: the page's Act 3 hand cycle and the session-start +
    session-wrap skills name the same `pyrite sw` verbs in the same order."""
    tutorial_text = TUTORIAL.read_text(encoding="utf-8")
    act3_text = tutorial_text.split("## Act 3:")[1].split("## Act 4:")[0]
    # Strip the introductory prompt block so we measure the deterministic hand cycle blocks.
    hand_cycle_text = re.sub(r"```text\n.*?```", "", act3_text, flags=re.S)

    skills_text = (
        (SKILLS_DIR / "session-start" / "SKILL.md").read_text(encoding="utf-8")
        + "\n"
        + (SKILLS_DIR / "session-wrap" / "SKILL.md").read_text(encoding="utf-8")
    )

    page_verbs = _sw_session_verbs(hand_cycle_text)
    skill_verbs = _sw_session_verbs(skills_text)
    assert page_verbs == [
        "board",
        "backlog",
        "pull-next",
        "transition",
        "context-for-item",
        "claim",
        "log",
        "submit",
    ]
    assert page_verbs == skill_verbs
