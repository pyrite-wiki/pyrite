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
    """Ordered `pyrite sw <verb>` workflow verbs in a section or skill pair.

    Collapses only consecutive duplicate invocations (e.g. a second `claim`
    demonstrating refusal) without masking reordered steps across phases.
    """
    verbs: list[str] = []
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
            if verb in session_set and (not verbs or verbs[-1] != verb):
                verbs.append(verb)
    return verbs


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(item in it for item in needle)


def test_hand_cycle_and_session_skills_follow_documented_sw_verb_sequences():
    """#1895 acceptance 4: the page's Act 3 hand cycle and the session-start +
    session-wrap skills share the documented `pyrite sw` workflow backbone in
    order (`board`, `backlog`, `pull-next`, `transition`, `context-for-item`,
    `claim`, `log`, `submit`) while preserving each sequence's step order."""
    tutorial_text = TUTORIAL.read_text(encoding="utf-8")
    act3_text = tutorial_text.split("## Act 3:")[1].split("## Act 4:")[0]
    # Strip the introductory prompt block so we measure the deterministic hand cycle blocks.
    hand_cycle_text = re.sub(r"```text\n.*?```", "", act3_text, flags=re.S)

    start_steps = (SKILLS_DIR / "session-start" / "SKILL.md").read_text(encoding="utf-8")
    wrap_text = (SKILLS_DIR / "session-wrap" / "SKILL.md").read_text(encoding="utf-8")
    # In session-wrap, Step 0 only notes pull-next/claim as a recovery fallback; Steps 1–5 are the wrap sequence.
    wrap_main = wrap_text.split("### 1. Log the work session", 1)[1]

    backbone = [
        "board",
        "backlog",
        "pull-next",
        "transition",
        "context-for-item",
        "claim",
        "log",
        "submit",
    ]
    hand_cycle = _sw_session_verbs(hand_cycle_text)
    skill_flow = _sw_session_verbs(start_steps) + _sw_session_verbs(wrap_main)

    assert _is_subsequence(backbone, hand_cycle)
    assert _is_subsequence(backbone, skill_flow)
    assert hand_cycle == [
        "board",
        "backlog",
        "pull-next",
        "transition",
        "pull-next",
        "context-for-item",
        "claim",
        "log",
        "submit",
    ]
    assert skill_flow == [
        "board",
        "backlog",
        "pull-next",
        "backlog",
        "transition",
        "context-for-item",
        "claim",
        "pull-next",
        "log",
        "submit",
    ]
