"""The README's install-from-a-tag line names the current release.

It pinned v0.24.3 through six later releases: nothing tied it to the version.
Release prep sets `version` in pyproject.toml, so this test makes the same
PR move the README's tag with it.
"""

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_readme_install_tag_is_the_declared_version():
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    readme = (REPO / "README.md").read_text()
    tags = re.findall(r"git\+https://github\.com/pyrite-wiki/pyrite@v([0-9][^\s\"']*)", readme)
    assert tags, "README no longer shows a git+...@v<version> install line"
    assert set(tags) == {version}, f"README installs v{tags}, pyproject declares {version}"
