"""UTF-8 CSV exported by spreadsheet tools keeps its first column."""

import pytest

from pyrite.formats.importers.csv_importer import import_csv


@pytest.mark.parametrize("as_bytes", [False, True])
@pytest.mark.parametrize(
    ("header", "values", "key", "expected"),
    [
        ("title,body", "Başlık,Text", "title", "Başlık"),
        ("id,title", "stable-id,Title", "id", "stable-id"),
        ("type,title", "person,Title", "entry_type", "person"),
        ("tags,title", "one;two,Title", "tags", ["one", "two"]),
    ],
)
def test_bom_does_not_become_part_of_the_first_header(as_bytes, header, values, key, expected):
    text = "\ufeff" + header + "\r\n" + values + "\r\n"
    records = import_csv(text.encode("utf-8") if as_bytes else text)
    assert len(records) == 1
    assert records[0][key] == expected


@pytest.mark.parametrize("prefix", ["", "\ufeff"])
def test_bom_handling_preserves_quoted_delimiters_newlines_and_body_bom(prefix):
    records = import_csv(prefix + 'title,body\r\n"One, two","\ufefffirst\nsecond"\r\n')
    assert records[0]["title"] == "One, two"
    assert records[0]["body"] == "\ufefffirst\nsecond"


def test_invalid_utf8_is_not_replaced_silently():
    with pytest.raises(UnicodeDecodeError):
        import_csv(b"\xef\xbb\xbftitle\n\xff\n")


def test_header_only_bom_csv_has_no_entries():
    assert import_csv(b"\xef\xbb\xbftitle,body\r\n") == []
