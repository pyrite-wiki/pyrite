"""A bulk YAML document is not an entry's mapping-only frontmatter."""

import pytest

from pyrite.exceptions import FrontmatterError
from pyrite.formats.importers.yaml_importer import import_yaml
from pyrite.utils.yaml import load_yaml


@pytest.mark.parametrize("as_bytes", [False, True])
def test_bare_yaml_sequence_imports_every_entry(as_bytes):
    text = "- title: First\n  type: note\n  body: One\n- title: Second\n  tags: [two]\n"
    records = import_yaml(text.encode() if as_bytes else text)
    assert [r["title"] for r in records] == ["First", "Second"]
    assert records[0]["entry_type"] == "note"
    assert records[0]["body"] == "One"
    assert records[1]["tags"] == ["two"]


@pytest.mark.parametrize("text", ["[]", "entries: []", "", "# no entries\n"])
def test_empty_yaml_import_has_no_phantom_entry(text):
    assert import_yaml(text) == []


def test_mapping_frontmatter_still_refuses_a_sequence():
    with pytest.raises(FrontmatterError, match="mapping"):
        load_yaml("- title: Not frontmatter\n")


def test_invalid_yaml_still_has_the_public_parse_error():
    with pytest.raises(FrontmatterError, match="Invalid YAML"):
        import_yaml("- title: [unterminated")


def test_mapping_and_wrapper_imports_remain_supported():
    assert import_yaml("title: Solo\n")[0]["title"] == "Solo"
    assert import_yaml("entries:\n- title: Wrapped\n")[0]["title"] == "Wrapped"
