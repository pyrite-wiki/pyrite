"""Guard documentation stays reachable from CONTRIBUTING (#944)."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARDS = {
    "test_test_rules.py": "Writing Tests",
    "test_tests_drive_the_surface_not_the_mock.py": "Writing Tests",
    "test_cli_color_environment.py": "Writing Tests",
    "test_storage_never_imports_services.py": "Architecture",
    "test_layer_boundaries.py": "Architecture",
    "test_cli_surfaces_through_services.py": "Architecture",
    "test_public_message_logged_once.py": "Architecture",
}


@pytest.mark.parametrize(("guard", "section"), GUARDS.items())
def test_contributing_links_live_guard_in_its_section(guard, section):
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    fragment = text.split(f" {section}\n", 1)[1].split("\n## ", 1)[0]
    assert f"(tests/{guard})" in fragment
    assert (ROOT / "tests" / guard).is_file()
