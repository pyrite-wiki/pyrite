"""Tests for import/export functionality."""

import json

from pyrite.formats.importers.csv_importer import import_csv
from pyrite.formats.importers.json_importer import import_json
from pyrite.formats.importers.markdown_importer import import_markdown


class TestJsonImporter:
    def test_import_array(self):
        data = json.dumps(
            [
                {"id": "e1", "title": "Entry 1", "body": "Body 1", "tags": ["a"]},
                {"id": "e2", "title": "Entry 2", "body": "Body 2"},
            ]
        )
        entries = import_json(data)
        assert len(entries) == 2
        assert entries[0]["id"] == "e1"
        assert entries[0]["title"] == "Entry 1"
        assert entries[0]["tags"] == ["a"]
        assert entries[1]["id"] == "e2"

    def test_import_wrapped(self):
        data = json.dumps({"entries": [{"title": "Test", "body": "B"}]})
        entries = import_json(data)
        assert len(entries) == 1
        assert entries[0]["title"] == "Test"

    def test_import_single_object(self):
        data = json.dumps({"id": "solo", "title": "Solo Entry", "body": "..."})
        entries = import_json(data)
        assert len(entries) == 1
        assert entries[0]["id"] == "solo"

    def test_import_bytes(self):
        data = json.dumps([{"title": "Test"}]).encode("utf-8")
        entries = import_json(data)
        assert len(entries) == 1

    def test_import_with_optional_fields(self):
        data = json.dumps(
            [
                {
                    "id": "e1",
                    "title": "T",
                    "body": "B",
                    "date": "2024-01-01",
                    "importance": 5,
                    "entry_type": "event",
                }
            ]
        )
        entries = import_json(data)
        assert entries[0]["date"] == "2024-01-01"
        assert entries[0]["importance"] == 5
        assert entries[0]["entry_type"] == "event"


class TestCsvImporter:
    def test_import_basic(self):
        csv_data = "id,title,body,tags\ne1,Entry 1,Body,a;b\ne2,Entry 2,Body2,c\n"
        entries = import_csv(csv_data)
        assert len(entries) == 2
        assert entries[0]["id"] == "e1"
        assert entries[0]["tags"] == ["a", "b"]
        assert entries[1]["tags"] == ["c"]

    def test_import_with_type(self):
        csv_data = "title,entry_type,date\nTest,event,2024-01-01\n"
        entries = import_csv(csv_data)
        assert entries[0]["entry_type"] == "event"
        assert entries[0]["date"] == "2024-01-01"

    def test_import_bytes(self):
        data = b"title,body\nTest,Body\n"
        entries = import_csv(data)
        assert len(entries) == 1

    def test_import_with_importance(self):
        data = "title,importance\nTest,7\n"
        entries = import_csv(data)
        # The cell's text, as given: the write's one rule judges it and stores 7
        # (#1588; test_a_csv_import_judges_the_cell_as_given_and_keeps_its_siblings).
        assert entries[0]["importance"] == "7"


class TestMarkdownImporter:
    def test_import_with_frontmatter(self):
        md = """---
id: test-entry
title: Test Entry
type: note
tags: [a, b]
---

This is the body content.
"""
        entries = import_markdown(md)
        assert len(entries) == 1
        assert entries[0]["id"] == "test-entry"
        assert entries[0]["title"] == "Test Entry"
        assert entries[0]["body"] == "This is the body content."
        assert entries[0]["tags"] == ["a", "b"]

    def test_import_without_frontmatter(self):
        md = "# My Title\n\nSome body content."
        entries = import_markdown(md)
        assert len(entries) == 1
        assert entries[0]["title"] == "My Title"
        assert "body content" in entries[0]["body"]

    def test_import_bytes(self):
        data = b"---\ntitle: Test\n---\nBody\n"
        entries = import_markdown(data)
        assert len(entries) == 1
        assert entries[0]["title"] == "Test"


class TestImporterRegistry:
    def test_available_formats(self):
        from pyrite.formats.importers import get_importer_registry

        registry = get_importer_registry()
        formats = registry.available_formats()
        assert "json" in formats
        assert "markdown" in formats
        assert "csv" in formats

    def test_get_importer(self):
        from pyrite.formats.importers import get_importer_registry

        registry = get_importer_registry()
        assert registry.get("json") is not None
        assert registry.get("nonexistent") is None


class TestExportRoundTrip:
    """Test that export -> import round-trips preserve data."""

    def test_json_roundtrip(self):
        from pyrite.formats.json_fmt import json_serialize

        original = [
            {"id": "e1", "title": "Entry 1", "body": "Body 1", "entry_type": "note", "tags": ["a"]},
        ]
        exported = json_serialize({"entries": original})
        reimported = import_json(exported)

        assert len(reimported) == 1
        assert reimported[0]["id"] == "e1"
        assert reimported[0]["title"] == "Entry 1"
