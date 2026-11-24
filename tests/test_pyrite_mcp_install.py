"""pyrite-mcp installs from the release tag, as the main README does (#1369),
and carries Pyrite's version (#1582).

No `pyrite-mcp` project exists on PyPI, and the `pyrite` name there is not
ours (ADR-0025), so neither `pip install pyrite-mcp` nor a plain `pyrite>=`
requirement installs what the docs promise.

Pyrite has one version, the one in `pyproject.toml`. pyrite-mcp's own version,
its `pyrite @ git+...@v<tag>` dependency and the install tag in its README
all equal it; `scripts/release.py` stops a release when any of them drifts.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = "git+https://github.com/pyrite-wiki/pyrite@"


def _pyrite_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def _tag() -> str:
    """The release tag the whole repository must name: pyproject.toml's version."""
    return f"v{_pyrite_version()}"


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
    tag = _tag()
    assert (
        f'pip install "pyrite @ {GIT}{tag}" \\\n  "pyrite-mcp @ {GIT}{tag}#subdirectory=pyrite-mcp"'
    ) in text


def test_pyproject_depends_on_pyrite_at_the_same_tag():
    deps = tomllib.loads((ROOT / "pyrite-mcp" / "pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    assert deps == [f"pyrite @ {GIT}{_tag()}"]


def test_pyrite_mcp_carries_pyrites_version():
    declared = tomllib.loads((ROOT / "pyrite-mcp" / "pyproject.toml").read_text())["project"][
        "version"
    ]
    assert declared == _pyrite_version(), (
        f"pyrite-mcp/pyproject.toml says {declared}, pyproject.toml says "
        f"{_pyrite_version()}: they move together with every release (#1582)"
    )


def test_pyrite_mcp_readme_names_only_pyrites_tag():
    text = (ROOT / "pyrite-mcp" / "README.md").read_text()
    tags = re.findall(r"pyrite-wiki/pyrite@(v[^\s\"'#]+)", text)
    assert len(tags) >= 2, "pyrite-mcp/README.md lost an install line"
    assert set(tags) == {_tag()}, f"pyrite-mcp/README.md names {sorted(set(tags))}, not {_tag()}"
