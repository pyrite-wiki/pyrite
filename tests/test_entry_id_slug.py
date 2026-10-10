"""Entry ids from any title: non-empty, filesystem-safe, bounded (#16, #17).

`generate_entry_id` deleted everything outside [a-z0-9]: a non-Latin title gave
an empty id ("Entry must have an ID", with no hint why), accents were dropped
rather than transliterated (caf-r-sum-na-ve), and a 300-character title became
an OSError. Two other places hand-rolled their own slug and let `/` and `:`
through into ADR filenames. One function, used everywhere.
"""

import re

import pytest

from pyrite.models.core_types import NoteEntry
from pyrite.schema import generate_entry_id

SAFE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


@pytest.mark.parametrize(
    "title",
    [
        "Hello World",
        "Some  Complex!! Title",
        "日本語のタイトル",
        "🚀 !!! ???",
        "Café résumé naïve",
        "Use A/B testing: now, or later?",
        "../../escape me",
        "-rf everything",
        "long title word " * 20,
        "",
    ],
)
def test_every_title_yields_a_safe_nonempty_id(title):
    entry_id = generate_entry_id(title)
    assert SAFE.match(entry_id), f"{title!r} -> {entry_id!r}"


def test_ascii_ids_are_unchanged():
    # No mass rename of existing entries.
    assert generate_entry_id("Hello World") == "hello-world"
    assert generate_entry_id("Some  Complex!! Title") == "some-complex-title"
    assert generate_entry_id("Use A/B testing: now, or later?") == "use-a-b-testing-now-or-later"


def test_non_string_yaml_scalar_titles_are_stringified_before_slugging():
    # #704: `title: 1e3` / `title: 2024` / `title: true` load as non-strings.
    assert generate_entry_id(2024) == "2024"
    assert generate_entry_id(1000.0) == "1000-0"
    assert generate_entry_id(True) == "true"
    assert generate_entry_id(False) == "false"
    assert generate_entry_id(0) == "0"
    assert generate_entry_id(None).startswith("entry-")
    for value in (2024, 1000.0, True, False, 0):
        assert SAFE.match(generate_entry_id(value)), repr(value)


def test_accents_transliterate_instead_of_vanishing():
    assert generate_entry_id("Café résumé naïve") == "cafe-resume-naive"


def test_non_latin_titles_get_a_stable_fallback():
    a = generate_entry_id("日本語のタイトル")
    b = generate_entry_id("日本語のタイトル")
    c = generate_entry_id("別のタイトル")
    assert a == b and a != c, "fallback must be deterministic per title"
    assert a.startswith("entry-") and len(a) == len("entry-") + 8


def test_long_titles_are_capped_at_a_word_boundary():
    entry_id = generate_entry_id("long title word " * 20)
    assert len(entry_id) <= 80 and not entry_id.endswith("-")
    assert entry_id.startswith("long-title-word-")


def test_markdown_ends_with_exactly_one_newline():
    # `pyrite create` wrote a trailing blank line, so every new entry failed the
    # end-of-file hook once (#17, second half).
    for body in ("body", "body\n", "body\n\n\n", ""):
        text = NoteEntry(id="n", title="N", body=body).to_markdown()
        assert text.endswith("\n") and not text.endswith("\n\n"), repr(text[-6:])
