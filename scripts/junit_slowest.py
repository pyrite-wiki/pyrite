#!/usr/bin/env python3
"""Print the slowest tests of a junit XML report as a markdown table.

    python scripts/junit_slowest.py junit-test-3.12.xml "test (3.12)" >> "$GITHUB_STEP_SUMMARY"

CI appends this to the step summary of every gating pytest run so "which tests
are slow" has an answer without a local run. Missing or unreadable input prints
a one-line note and exits 0: a job that died before pytest wrote the report must
not hide the real failure behind this step.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

TOP = 25


def slowest(path: str, top: int = TOP) -> list[tuple[float, str]]:
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        name = f"{case.get('classname', '')}::{case.get('name', '')}"
        cases.append((float(case.get("time", 0) or 0), name))
    return sorted(cases, reverse=True)[:top]


def main(argv: list[str]) -> int:
    path = argv[1]
    label = argv[2] if len(argv) > 2 else path
    try:
        rows = slowest(path)
    except (OSError, ET.ParseError) as exc:
        print(f"### Slowest tests: {label}\n\nno junit report ({exc.__class__.__name__}).")
        return 0
    print(f"### Slowest {len(rows)} tests: {label}\n\n| seconds | test |\n|---:|---|")
    for secs, name in rows:
        print(f"| {secs:.2f} | `{name}` |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
