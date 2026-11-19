"""`pyrite get --format csv` prints the entry (#1234).

A single-entry read in CSV is a header row plus exactly one data row,
whatever keys the entry has. Without `--fields`, `csv_serialize` looked for
an envelope key first and an entry's `tags` list matched the tag-count
table: empty output for an untagged entry, a bare `tag,count` header for a
tagged one. The REST read took the same branch.
"""

import csv
import io

from pyrite.formats import format_response

ID, KB, TITLE = "alpha", "test-kb", "Alpha"


def _note(entry_id: str, title: str, body: str = "hello", tags=None) -> str:
    front = f"---\nid: {entry_id}\ntype: note\ntitle: {title}"
    if tags:
        front += "\ntags: [{}]".format(", ".join(tags))
    return front + "\n---\n\n" + body + "\n"


def _run(world, args):
    result = world.cli(*args)
    assert result.exit_code == 0, result.output
    return result.stdout


def _rows(output: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(output)))


def test_serializer_renders_one_entry_without_fields():
    content, _ = format_response(
        {"id": ID, "kb_name": KB, "title": TITLE, "tags": []}, "csv", single_entry=True
    )
    rows = _rows(content)
    assert len(rows) == 2, rows
    assert rows[0] == ["id", "kb_name", "title", "tags"]
    assert rows[1][0] == ID and rows[1][2] == TITLE


def test_serializer_keeps_the_fields_path(cli_world, console_width):
    world = cli_world()
    world.add_entry(KB, _note(ID, TITLE))
    output = _run(world, ["get", ID, "-k", KB, "--format", "csv", "--fields", "id,title"])
    assert _rows(output) == [["id", "kb_name", "title"], [ID, KB, TITLE]]


def test_get_csv_prints_the_entry_without_tags(cli_world, console_width):
    world = cli_world()
    world.add_entry(KB, _note(ID, TITLE))
    rows = _rows(_run(world, ["get", ID, "-k", KB, "--format", "csv"]))
    assert len(rows) == 2, rows
    header, row = rows
    # The single-entry render shows whatever keys the entry has, so the
    # header is the payload's own key set (entry_type, body, summary, ...),
    # not a hand-picked projection: pin the keys the read must carry.
    assert "id" in header and "title" in header and "tags" in header, header
    assert row[header.index("id")] == ID
    assert row[header.index("title")] == TITLE
    assert row[header.index("tags")] == "[]", row  # cell_text json-dumps the empty list


def test_get_csv_stdout_ends_with_exactly_one_newline(cli_world, console_width):
    """Echo used to add a newline after the CSV's own line terminator, leaving
    a trailing blank record that streaming CSV parsers read as an empty row
    (#1234). CliRunner normalizes the CSV's \r\n to \n, so the tail is
    asserted in the runner's normalized form."""
    world = cli_world()
    world.add_entry(KB, _note(ID, TITLE))
    output = _run(world, ["get", ID, "-k", KB, "--format", "csv"])
    assert not output.endswith("\n\n"), repr(output[-10:])
    rows = _rows(output)
    assert len(rows) == 2, rows
    assert rows[-1] != [], rows


def test_get_csv_prints_tags_and_tricky_cells(cli_world, console_width):
    world = cli_world()
    body = 'has, "quotes"\nand a newline'
    world.add_entry(KB, _note(ID, TITLE, body=body, tags=["a", "b"]))
    rows = _rows(_run(world, ["get", ID, "-k", KB, "--format", "csv"]))
    header, row = rows
    assert len(rows) == 2, rows
    # One quoted cell, exactly as written (commas, quotes and newline stay inside).
    assert row[header.index("body")] == body, row
    assert row[header.index("tags")] == '["a", "b"]', row


def test_get_csv_renders_nested_metadata_as_json(cli_world, console_width):
    world = cli_world()
    text = f"---\nid: {ID}\ntype: note\ntitle: {TITLE}\nmetadata:\n  owner: me\n---\n\nhello\n"
    world.add_entry(KB, text)
    rows = _rows(_run(world, ["get", ID, "-k", KB, "--format", "csv"]))
    header, row = rows
    cell = row[header.index("metadata")]
    assert cell == '{"owner": "me"}', cell
