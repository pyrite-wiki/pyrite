"""Contract test: README.md's stated facts about the code must match the code.

Regression coverage: README counts about the code have drifted before (it
once said "Ten built-in entry types" and "22 ADRs" while the code had 11
types and 31 ADRs, and ``pyrite.__version__`` sat at 0.12.0 while
``pyproject.toml`` said 0.24.1). This test locks the three facts the README
still states about the code — the built-in entry-type list, the plugin
protocol method count, and the shipped-extension table — so adding a type,
a protocol method, or an extension without updating the README goes red
instead of drifting silently. It follows the tests/test_readme_mcp_tool_table.py
pattern: read README.md, parse the line with a regex, compare with the code,
and name what is missing and what is extra in the failure message.
"""

import re
from pathlib import Path

import pyrite.models as models
from pyrite.plugins.protocol import PyritePlugin

README_PATH = Path(__file__).resolve().parent.parent / "README.md"
EXTENSIONS_DIR = README_PATH.parent / "extensions"

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}


def _as_count(token: str) -> int:
    """Parse a README count written as a word ("Eleven") or a digit ("11")."""
    if token.isdigit():
        return int(token)
    assert token.lower() in _NUMBER_WORDS, (
        f"could not parse README count {token!r} as a word or a digit"
    )
    return _NUMBER_WORDS[token.lower()]


def _public_protocol_methods() -> list[str]:
    return [
        name
        for name, value in vars(PyritePlugin).items()
        if callable(value) and not name.startswith("_")
    ]


def test_readme_entry_type_list_matches_registry():
    readme_text = README_PATH.read_text()
    match = re.search(r"(\w+) built-in entry types: ([^.]+)\.", readme_text)
    assert match, "could not find the 'built-in entry types' line in README.md"
    declared_count = _as_count(match.group(1))
    declared_types = set(re.findall(r"`([^`]+)`", match.group(2)))
    actual_types = set(models.ENTRY_TYPE_REGISTRY)

    assert declared_count == len(actual_types), (
        f"README says {declared_count} built-in entry types; "
        f"ENTRY_TYPE_REGISTRY actually has {len(actual_types)}"
    )
    assert declared_types == actual_types, (
        "README's built-in entry-type list doesn't match ENTRY_TYPE_REGISTRY. "
        f"Missing from README: {actual_types - declared_types}. "
        f"Extra in README: {declared_types - actual_types}."
    )


def test_readme_plugin_method_count_matches_protocol():
    readme_text = README_PATH.read_text()
    match = re.search(r"up to (\w+) methods", readme_text)
    assert match, "could not find the 'up to N methods' line in README.md"
    declared_count = _as_count(match.group(1))
    actual_count = len(_public_protocol_methods())

    assert declared_count == actual_count, (
        f"README says the plugin protocol has up to {declared_count} methods; "
        f"PyritePlugin actually defines {actual_count} public methods: "
        f"{sorted(_public_protocol_methods())}"
    )


def test_readme_extension_table_matches_extension_dirs():
    readme_text = README_PATH.read_text()
    match = re.search(r"(\w+) extensions ship:\s+((?:\|.*\n)+)", readme_text)
    assert match, "could not find the 'extensions ship' table in README.md"
    declared_count = _as_count(match.group(1))
    declared_extensions = set(re.findall(r"\|\s*\*\*([^*]+)\*\*", match.group(2)))
    actual_extensions = {
        path.name
        for path in EXTENSIONS_DIR.iterdir()
        if path.is_dir() and (path / "pyproject.toml").exists()
    }

    assert declared_count == len(actual_extensions), (
        f"README says {declared_count} extensions ship; "
        f"extensions/ actually has {len(actual_extensions)} directories "
        f"with a pyproject.toml"
    )
    assert declared_extensions == actual_extensions, (
        "README's extension table doesn't match extensions/. "
        f"Missing from README: {actual_extensions - declared_extensions}. "
        f"Extra in README: {declared_extensions - actual_extensions}."
    )
