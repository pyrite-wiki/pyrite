"""Tag filtering and empty results through both CLI entry points (#1417)."""

import json

import pytest
import yaml

from pyrite.cli import app as pyrite_app
from pyrite.read_cli import app as read_app

KB = "test-kb"
APPS = [pytest.param(pyrite_app, id="pyrite"), pytest.param(read_app, id="pyrite-read")]
ZEBRA = {"name": "zebra", "count": 2}
ZETA = {"name": "zeta", "count": 1}


@pytest.fixture
def tagged_kb(cli_world):
    world = cli_world()
    notes = ["common, popular, zebra", "common, popular, zebra", "common, popular", "zeta"]
    for i, tags in enumerate(notes):
        world.add_entry(
            KB, f"---\nid: note-{i}\ntype: note\ntitle: Note {i}\ntags: [{tags}]\n---\n\nBody.\n"
        )
    return world


def _tags(world, app, *args):
    result = world.cli("tags", "-k", KB, *args, app=app)
    assert result.exit_code == 0, result.output
    return result.stdout


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize(
    ("prefix", "limit", "expected"),
    [
        pytest.param("ze", "2", [ZEBRA, ZETA], id="prefix-before-limit"),
        pytest.param("ze", "1", [ZEBRA], id="limit-matching-tags"),
        pytest.param("ZE", "2", [ZEBRA, ZETA], id="sqlite-case-insensitive"),
        pytest.param("z_t", "2", [ZETA], id="like-wildcard"),
    ],
)
def test_tags_filters_prefix_before_limit(tagged_kb, app, prefix, limit, expected):
    data = json.loads(
        _tags(tagged_kb, app, "--prefix", prefix, "--limit", limit, "--format", "json")
    )
    assert data == {"tags": expected, "count": len(expected)}


@pytest.mark.parametrize("app", APPS)
@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_tags_no_match_prints_empty_envelope(tagged_kb, app, fmt):
    output = _tags(tagged_kb, app, "--prefix", "nope", "--format", fmt)
    parse = json.loads if fmt == "json" else yaml.safe_load
    assert parse(output) == {"tags": [], "count": 0}


@pytest.mark.parametrize("app", APPS)
def test_tags_no_match_prints_empty_csv(tagged_kb, app):
    assert _tags(tagged_kb, app, "--prefix", "nope", "--format", "csv") == ""


@pytest.mark.control(reason="Rich output should still report that there are no matches")
@pytest.mark.parametrize("app", APPS)
def test_tags_no_match_keeps_rich_message(tagged_kb, app):
    assert _tags(tagged_kb, app, "--prefix", "nope", "--format", "rich").strip() == "No tags found."


@pytest.mark.control(reason="Both flags should still work independently")
@pytest.mark.parametrize("app", APPS)
def test_tags_prefix_and_limit_work_independently(tagged_kb, app):
    by_prefix = json.loads(_tags(tagged_kb, app, "--prefix", "ze", "--format", "json"))
    by_limit = json.loads(_tags(tagged_kb, app, "--limit", "2", "--format", "json"))

    assert by_prefix == {"tags": [ZEBRA, ZETA], "count": 2}
    assert by_limit["count"] == 2
    assert {tag["name"]: tag["count"] for tag in by_limit["tags"]} == {"common": 3, "popular": 3}
