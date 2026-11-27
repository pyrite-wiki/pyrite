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
    tags = re.findall(r"git\+https://github\.com/pyrite-wiki/pyrite@v([0-9][^\s\"'#]*)", readme)
    assert tags, "README no longer shows a git+...@v<version> install line"
    assert set(tags) == {version}, f"README installs v{tags}, pyproject declares {version}"


def test_software_kb_readme_install_tag_is_the_declared_version():
    """The extension's README names the release too, and goes stale with it."""
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    readme = (REPO / "extensions" / "software-kb" / "README.md").read_text()
    tags = re.findall(
        r"pyrite-software-kb @ git\+https://github\.com/pyrite-wiki/pyrite@v([0-9][^\s\"'#]*)"
        r"#subdirectory=extensions/software-kb",
        readme,
    )
    assert tags, "the software-kb README no longer shows a git+...@v<version> install line"
    assert set(tags) == {version}, (
        f"software-kb README installs v{tags}, pyproject declares {version}"
    )


EXTENSION_DISTRIBUTIONS = {
    "software-kb": "pyrite-software-kb",
    "zettelkasten": "pyrite-zettelkasten",
    "encyclopedia": "pyrite-encyclopedia",
    "social": "pyrite-social",
    "journalism-investigation": "pyrite-journalism-investigation",
}


def _no_clone_line(directory: str, version: str) -> str:
    return (
        f'pip install "{EXTENSION_DISTRIBUTIONS[directory]} @ '
        f'git+https://github.com/pyrite-wiki/pyrite@v{version}#subdirectory=extensions/{directory}"'
    )


def test_the_docs_give_the_no_clone_extension_lines_at_the_declared_version():
    """#1457: the README shows each extension's no-clone line, and getting-started
    the software-kb one, at the declared version: the same lines `pyrite sw`
    and `pyrite init -t software` print (tests/test_cli_usage_error_plain.py)."""
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    readme = (REPO / "README.md").read_text()
    for directory in EXTENSION_DISTRIBUTIONS:
        assert _no_clone_line(directory, version) in readme, directory
    started = (REPO / "docs" / "getting-started.md").read_text()
    assert _no_clone_line("software-kb", version) in started
    core_no_clone = (
        f'pip install "pyrite[server,cli] @ git+https://github.com/pyrite-wiki/pyrite@v{version}"'
    )
    assert core_no_clone in readme and core_no_clone in started
    first_bash = re.search(r"```bash\n(.*?)```", started, re.S)
    assert first_bash and "git+https://github.com/pyrite-wiki/pyrite@v" in first_bash.group(1), (
        "getting-started.md's first install block must lead with the pinned no-clone git+ line"
    )
    tags = re.findall(r"pyrite@v([0-9][^\s\"'#]*)", started)
    assert tags and set(tags) == {version}, tags


def test_agentic_development_tutorial_install_tag_is_the_declared_version():
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    tutorial = (REPO / "docs" / "tutorials" / "agentic-development.md").read_text()
    assert _no_clone_line("software-kb", version) in tutorial
    tags = re.findall(r"pyrite@v([0-9][^\s\"'#]*)", tutorial)
    assert tags and set(tags) == {version}, tags
