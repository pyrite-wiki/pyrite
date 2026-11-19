"""Markdown exports must carry parseable, lossless YAML metadata."""

from copy import deepcopy
from datetime import date

import pytest
import yaml

from pyrite.formats.markdown_fmt import markdown_serialize


@pytest.mark.parametrize(
    "title", ["Meeting: notes", "# heading", "yes", "a\n---\nb", "[not a list]"]
)
def test_special_titles_and_tags_round_trip_without_mutating_the_record(title):
    entry = {
        "id": "sample",
        "title": title,
        "entry_type": "note",
        "tags": ["a, b", "no", "x]"],
        "body": "Body\n---\nkept exactly\n",
    }
    before = deepcopy(entry)
    text = markdown_serialize(entry)
    front, body = text[4:].split("\n---\n\n", 1)
    assert yaml.safe_load(front) == {
        "id": "sample",
        "title": title,
        "type": "note",
        "tags": entry["tags"],
    }
    assert body == entry["body"]
    assert entry == before


@pytest.mark.parametrize("value", [None, "", []])
def test_empty_optional_values_are_omitted(value):
    entry = {"id": "sample", "title": "Title", "date": value, "tags": value}
    front = markdown_serialize(entry)[4:].split("\n---\n\n", 1)[0]
    assert yaml.safe_load(front) == {"id": "sample", "title": "Title"}


def test_date_zero_and_scalar_tags_keep_their_values():
    entry = {
        "id": "sample",
        "title": "Title",
        "date": date(2026, 10, 22),
        "importance": 0,
        "tags": "news",
    }
    before = deepcopy(entry)
    front = markdown_serialize(entry)[4:].split("\n---\n\n", 1)[0]
    assert yaml.safe_load(front) == before
    assert entry == before


def test_false_is_data_not_an_empty_value():
    front = markdown_serialize({"id": "sample", "title": "Title", "importance": False})
    assert yaml.safe_load(front[4:].split("\n---\n\n", 1)[0])["importance"] is False
