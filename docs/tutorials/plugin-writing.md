# Build a Pyrite Plugin

You will build a **reading-list** plugin that adds a `reading_item` entry type to Pyrite, complete with validation and a KB preset. By the end you will have a working plugin you can install and use from the CLI.

**Prerequisites:** Pyrite installed and available on your `PATH`.

## Option A: Scaffold with Claude Code

If you use [Claude Code](https://claude.com/claude-code), the fastest path is the built-in extension builder. Open Claude Code in the Pyrite repo and run:

```
/extension-builder
```

Tell it you want a "reading-list" extension with a `reading_item` type (fields: author, url, status, rating). Claude Code generates the full directory structure, entry type, validator, preset, tests, and `pyproject.toml` — ready to install.

The rest of this tutorial walks through building the same plugin by hand.

## Option B: Build by Hand

### 1. Create the directory structure

```
extensions/reading-list/
  pyproject.toml
  src/pyrite_reading_list/
    __init__.py
    plugin.py
    entry_types.py
    validators.py
    preset.py
  tests/
    test_reading_list.py
```

```bash
mkdir -p extensions/reading-list/src/pyrite_reading_list
mkdir -p extensions/reading-list/tests
touch extensions/reading-list/src/pyrite_reading_list/__init__.py
```

### 2. Write `pyproject.toml`

The entry point group **must** be `"pyrite.plugins"`. Pyrite discovers plugins through this entry point at startup.

```toml
# extensions/reading-list/pyproject.toml

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyrite-reading-list"
version = "0.1.0"
description = "Reading list tracker for Pyrite"
requires-python = ">=3.11"
dependencies = []

[project.entry-points."pyrite.plugins"]
reading_list = "pyrite_reading_list.plugin:ReadingListPlugin"

[tool.hatch.build.targets.wheel]
packages = ["src/pyrite_reading_list"]
```

### 3. Define the entry type

Every entry type is a dataclass that extends `NoteEntry` and implements three things: an `entry_type` property, `to_frontmatter()`, and `from_frontmatter()`.

```python
# extensions/reading-list/src/pyrite_reading_list/entry_types.py

from dataclasses import dataclass
from typing import Any

from pyrite.models.base import parse_datetime, parse_links, parse_sources
from pyrite.models.core_types import NoteEntry
from pyrite.schema import Provenance, generate_entry_id

READING_STATUSES = ("to-read", "reading", "read")


@dataclass
class ReadingItemEntry(NoteEntry):
    """A book, article, or paper on your reading list."""

    author: str = ""
    url: str = ""
    status: str = "to-read"
    rating: int = 0  # 0 means unrated, 1-5 when rated

    @property
    def entry_type(self) -> str:
        return "reading_item"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "reading_item"
        if self.author:
            meta["author"] = self.author
        if self.url:
            meta["url"] = self.url
        if self.status != "to-read":
            meta["status"] = self.status
        if self.rating:
            meta["rating"] = self.rating
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ReadingItemEntry":
        prov_data = meta.get("provenance")
        provenance = Provenance.from_dict(prov_data) if prov_data else None

        entry_id = meta.get("id", "")
        if not entry_id:
            entry_id = generate_entry_id(meta.get("title", ""))

        return cls(
            id=entry_id,
            title=meta.get("title", ""),
            body=body,
            summary=meta.get("summary", ""),
            tags=meta.get("tags", []) or [],
            sources=parse_sources(meta.get("sources")),
            links=parse_links(meta.get("links")),
            provenance=provenance,
            metadata=meta.get("metadata", {}),
            created_at=parse_datetime(meta.get("created_at")),
            updated_at=parse_datetime(meta.get("updated_at")),
            author=meta.get("author", ""),
            url=meta.get("url", ""),
            status=meta.get("status", "to-read"),
            rating=meta.get("rating", 0),
        )
```

Key rules:
- `entry_type` must return the exact key you register in `get_entry_types()`.
- `to_frontmatter()` should omit fields that hold their default value.
- `from_frontmatter()` must call `generate_entry_id(title)` when the metadata has no `id`.

### 4. Add a validator

Validators receive every entry being saved. Return `[]` immediately for types you do not own.

```python
# extensions/reading-list/src/pyrite_reading_list/validators.py

import re
from typing import Any

URL_PATTERN = re.compile(r"^https?://\S+$")


def validate_reading_item(
    entry_type: str,
    data: dict[str, Any],
    context: dict[str, Any],
) -> list[dict]:
    """Validate reading_item entries."""
    if entry_type != "reading_item":
        return []

    errors: list[dict] = []

    # URL format check
    url = data.get("url", "")
    if url and not URL_PATTERN.match(url):
        errors.append({
            "field": "url",
            "rule": "format",
            "expected": "valid HTTP(S) URL",
            "got": url,
        })

    # Rating range check
    rating = data.get("rating", 0)
    if rating:
        try:
            r = int(rating)
            if r < 1 or r > 5:
                errors.append({
                    "field": "rating",
                    "rule": "range",
                    "expected": "1-5",
                    "got": rating,
                })
        except (TypeError, ValueError):
            errors.append({
                "field": "rating",
                "rule": "type",
                "expected": "integer",
                "got": rating,
            })

    # Status enum check
    valid_statuses = ("to-read", "reading", "read")
    status = data.get("status", "to-read")
    if status not in valid_statuses:
        errors.append({
            "field": "status",
            "rule": "enum",
            "expected": list(valid_statuses),
            "got": status,
        })

    return errors
```

### 5. Add a preset

Presets let users scaffold a new KB with `pyrite init --template reading-list --path my-reading-list`.

```python
# extensions/reading-list/src/pyrite_reading_list/preset.py

READING_LIST_PRESET = {
    "name": "reading-list",
    "description": "Track books, articles, and papers you want to read",
    "types": {
        "reading_item": {
            "description": "A book, article, or paper on your reading list",
            "required": ["title"],
            "optional": ["author", "url", "status", "rating", "tags"],
            "subdirectory": "items/",
        },
    },
    "policies": {
        "private": False,
        "single_author": True,
    },
    "validation": {
        "enforce": True,
        "rules": [
            {"field": "status", "enum": ["to-read", "reading", "read"]},
            {"field": "rating", "range": [1, 5]},
        ],
    },
    "directories": ["items"],
}
```

### 6. Write the plugin class

The plugin class ties everything together. It follows the `PyritePlugin` protocol -- implement only the methods you need.

```python
# extensions/reading-list/src/pyrite_reading_list/plugin.py

from collections.abc import Callable

from .entry_types import ReadingItemEntry
from .preset import READING_LIST_PRESET
from .validators import validate_reading_item


class ReadingListPlugin:
    name = "reading_list"

    def get_entry_types(self) -> dict[str, type]:
        return {"reading_item": ReadingItemEntry}

    def get_kb_types(self) -> list[str]:
        return ["reading_list"]

    def get_validators(self) -> list[Callable]:
        return [validate_reading_item]

    def get_kb_presets(self) -> dict[str, dict]:
        return {"reading-list": READING_LIST_PRESET}

    def get_field_schemas(self) -> dict[str, dict[str, dict]]:
        return {
            "reading_item": {
                "status": {
                    "type": "select",
                    "options": ["to-read", "reading", "read"],
                    "default": "to-read",
                    "description": "Reading progress",
                },
                "rating": {
                    "type": "number",
                    "description": "Rating from 1 to 5",
                },
            },
        }

    def get_type_metadata(self) -> dict[str, dict]:
        return {
            "reading_item": {
                "ai_instructions": "Use for tracking books, articles, and papers. Set status to 'to-read' initially, 'reading' when started, 'read' when finished. Only add a rating after finishing.",
                "field_descriptions": {
                    "author": "Author or authors of the work",
                    "url": "URL to the work or its listing",
                    "status": "Reading progress: to-read, reading, or read",
                    "rating": "Rating from 1 (poor) to 5 (excellent), 0 if unrated",
                },
            },
        }
```

### 7. Write tests

```python
# extensions/reading-list/tests/test_reading_list.py

import pytest

from pyrite_reading_list.entry_types import ReadingItemEntry
from pyrite_reading_list.plugin import ReadingListPlugin
from pyrite_reading_list.validators import validate_reading_item


class TestEntryType:
    def test_entry_type_property(self):
        entry = ReadingItemEntry(id="test-1", title="Test Book")
        assert entry.entry_type == "reading_item"

    def test_roundtrip(self):
        entry = ReadingItemEntry(
            id="test-1",
            title="Designing Data-Intensive Applications",
            author="Martin Kleppmann",
            url="https://dataintensive.net",
            status="reading",
            rating=0,
        )
        meta = entry.to_frontmatter()
        restored = ReadingItemEntry.from_frontmatter(meta, entry.body)
        assert restored.title == entry.title
        assert restored.author == entry.author
        assert restored.status == "reading"

    def test_defaults_omitted_from_frontmatter(self):
        entry = ReadingItemEntry(id="test-1", title="Test")
        meta = entry.to_frontmatter()
        assert "rating" not in meta  # default 0 is omitted
        assert "status" not in meta  # default "to-read" is omitted


class TestValidator:
    def test_ignores_other_types(self):
        assert validate_reading_item("note", {"title": "x"}, {}) == []

    def test_valid_entry(self):
        data = {"url": "https://example.com", "rating": 4, "status": "reading"}
        assert validate_reading_item("reading_item", data, {}) == []

    def test_invalid_url(self):
        errors = validate_reading_item("reading_item", {"url": "not-a-url"}, {})
        assert any(e["field"] == "url" for e in errors)

    def test_rating_out_of_range(self):
        errors = validate_reading_item("reading_item", {"rating": 10}, {})
        assert any(e["field"] == "rating" for e in errors)

    def test_invalid_status(self):
        errors = validate_reading_item("reading_item", {"status": "burned"}, {})
        assert any(e["field"] == "status" for e in errors)


class TestPlugin:
    def test_registration(self):
        plugin = ReadingListPlugin()
        assert "reading_item" in plugin.get_entry_types()
        assert "reading-list" in plugin.get_kb_presets()
        assert len(plugin.get_validators()) == 1
```

## Install and Test

```bash
# Install in development mode
cd /path/to/pyrite
.venv/bin/pip install -e extensions/reading-list

# Verify the plugin loads
.venv/bin/python -c "
from pyrite.plugins.registry import get_registry
print(get_registry().list_plugins())
"

# Create a KB using the preset
cd /tmp && pyrite init --template reading-list --path reading

# Add an entry
pyrite create --type reading_item \
  --title "Designing Data-Intensive Applications" \
  -b "The big ideas behind reliable, scalable, and maintainable systems." \
  --tags distributed-systems,databases

# Run the tests
cd /path/to/pyrite
.venv/bin/pytest extensions/reading-list/tests/ -v
```

## Next Steps

This tutorial covered entry types, validators, and presets. Plugins can also provide:

- **CLI commands** via `get_cli_commands()` -- add Typer commands under `pyrite <your-command>`
- **MCP tools** via `get_mcp_tools(tier)` -- expose functionality to AI agents
- **Lifecycle hooks** via `get_hooks()` -- run logic before/after entry save or delete. A hook may refuse a write; it may not change one, and an `after_save` hook never writes another entry. Anything computed from other entries (a parent task's completion, counts, rollups) is derived at read time and returned under a `derived` key, not written into a file: core Pyrite's own parent rollup hook was replaced this way (ADR-0042 decision 4; `tests/test_after_save_hooks_write_no_other_entry.py` runs every installed plugin's `after_save` hooks and fails if one writes another entry)
- **Workflows** via `get_workflows()` -- define state machines for entry fields
- **Custom DB tables** via `get_db_tables()` -- store engagement-tier data locally

See the full plugin protocol in [`pyrite/plugins/protocol.py`](https://github.com/pyrite-wiki/pyrite/blob/main/pyrite/plugins/protocol.py) and the existing extensions in [`extensions/`](https://github.com/pyrite-wiki/pyrite/tree/main/extensions) for real-world examples.
