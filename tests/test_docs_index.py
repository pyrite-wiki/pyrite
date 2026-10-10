"""Keep docs discoverable and local Markdown links resolvable (#739)."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"docs/conductor-desk.md"}  # #244 owns the retired desk.


def relative_links(path):
    text = re.sub(r"(?ms)^\s*```.*?^\s*```[^\n]*$", "", path.read_text(encoding="utf-8"))
    links = re.findall(r"\]\(\s*(<[^>]+>|[^\s)]+)", text)
    links += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", text)
    for link in links:
        url = urlsplit(link.strip("<>"))
        if not url.scheme and not url.netloc and url.path:
            yield (path.parent / unquote(url.path)).resolve()


def assert_indexed(root):
    index = root / "docs/README.md"
    assert index.is_file(), "Missing docs/README.md"
    linked = set(relative_links(index))
    missing = [
        p.relative_to(root).as_posix()
        for p in root.glob("docs/**/*.md")
        if p != index
        and p.relative_to(root).as_posix() not in EXCLUDED
        and p.resolve() not in linked
    ]
    assert not missing, f"Docs missing from index: {missing}"


def assert_links_resolve(paths):
    broken = [
        (str(p), str(target)) for p in paths for target in relative_links(p) if not target.exists()
    ]
    assert not broken, f"Broken relative links: {broken}"


def test_every_doc_has_an_index_link():
    assert_indexed(ROOT)


@pytest.mark.parametrize("name", ["README.md", "AGENTS.md"])
def test_entry_points_link_to_docs_index(name):
    assert (ROOT / "docs/README.md").resolve() in set(relative_links(ROOT / name))


def test_getting_started_and_contributing_have_next_steps():
    assert (ROOT / "docs/tutorials/pyrite-in-20-minutes.md").resolve() in set(
        relative_links(ROOT / "docs/getting-started.md")
    )
    assert (ROOT / "kb/design.md").resolve() in set(relative_links(ROOT / "CONTRIBUTING.md"))


@pytest.mark.control(reason="pins existing local links as well as future documentation additions")
def test_relative_links_resolve():
    paths = [ROOT / p for p in ("README.md", "CONTRIBUTING.md", "AGENTS.md", "CLAUDE.md")]
    assert_links_resolve([*paths, *ROOT.glob("docs/**/*.md")])


@pytest.mark.control(reason="negative oracle: newly unindexed pages must be detected")
def test_guard_rejects_an_unlisted_page(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/README.md").write_text("# Index\n")
    (tmp_path / "docs/new.md").write_text("# New page\n")
    with pytest.raises(AssertionError, match="new.md"):
        assert_indexed(tmp_path)


@pytest.mark.control(reason="negative oracle: broken inline and reference links must be detected")
@pytest.mark.parametrize("link", ["[link](missing.md#section)", "[ref]: missing.md"])
def test_guard_rejects_a_broken_link(tmp_path, link):
    page = tmp_path / "page.md"
    page.write_text(link)
    with pytest.raises(AssertionError, match="missing.md"):
        assert_links_resolve([page])
