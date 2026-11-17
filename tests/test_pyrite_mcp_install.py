"""pyrite-mcp installs from the release tag, as the main README does (#1369).

No `pyrite-mcp` project exists on PyPI, and the `pyrite` name there is not
ours (ADR-0025), so neither `pip install pyrite-mcp` nor a plain `pyrite>=`
requirement installs what the docs promise.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = "git+https://github.com/pyrite-wiki/pyrite@"


def _main_readme_tag() -> str:
    m = re.search(
        r"pyrite\[[^\]]*\] @ git\+https://github\.com/pyrite-wiki/pyrite@(v[\d.]+)",
        (ROOT / "README.md").read_text(),
    )
    assert m, "the main README no longer shows a git-tag install"
    return m.group(1)


def test_readme_does_not_tell_anyone_to_pip_install_a_pypi_name():
    text = (ROOT / "pyrite-mcp" / "README.md").read_text()
    assert not re.search(r"pip install\s+\"?pyrite-mcp\"?\s*$", text, re.MULTILINE)


def test_the_install_command_names_pyrite_by_the_same_tag():
    """pyrite-mcp at v0.26.3 depends on `pyrite>=0.20.0`, a name pip looks up
    on the index, where it is not ours (ADR-0025): installing pyrite-mcp's URL
    alone fails today ("No matching distribution found for pyrite>=0.20.0")
    and would install whatever is published there. Naming pyrite by its git
    URL in the same command satisfies the requirement from the tag."""
    text = (ROOT / "pyrite-mcp" / "README.md").read_text()
    tag = _main_readme_tag()
    assert (
        f'pip install "pyrite @ {GIT}{tag}" \\\n  "pyrite-mcp @ {GIT}{tag}#subdirectory=pyrite-mcp"'
    ) in text


def test_pyproject_depends_on_pyrite_at_the_same_tag():
    deps = tomllib.loads((ROOT / "pyrite-mcp" / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    assert deps == [f"pyrite @ {GIT}{_main_readme_tag()}"]
