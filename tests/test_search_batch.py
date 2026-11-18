"""`pyrite search --batch queries.json`: many searches, one process (#1692).

The property: for each query in the batch, the slot equals what a single
`pyrite search` with the same arguments prints. Every expected value that has
a single twin is a real single `pyrite search` run on the same world, never a
hand-copied result; the shapes with no twin (the envelope, the cap, a missing
file) are written out by hand from the issue.

A real KB on disk and a real index through `tests/_world.py`; a fake embedding
model at the process edge (`tests/search_verdict_seed.py`) so the semantic leg
runs without torch.
"""

from __future__ import annotations

import json

import pytest

import pyrite.services.embedding_service as es
from pyrite.config import KBConfig
from tests.cli_help import plain
from tests.search_verdict_seed import install_fake_model, vector_at

KB = "birds"
CAP = 50

ENTRIES = [
    ("kestrel", "Kestrel notes", "a small falcon that hovers over fields", 0.60),
    ("heron", "Heron notes", "a wading bird of the marsh", 0.50),
    ("wren", "Wren notes", "a very small brown bird, also a falcon's prey", 0.40),
]
LONG = "falcon " * 400  # past MAX_SEARCH_QUERY_LENGTH


class Batch:
    """A world plus helpers: run a single search, run a batch."""

    def __init__(self, world, model_loads):
        self.world = world
        self.model_loads = model_loads

    def single(self, *args):
        result = self.world.cli("search", *args, "--format", "json")
        return result.exit_code, json.loads(result.stdout)

    def batch(self, queries, *flags, raw=None, stdin=False):
        text = raw if raw is not None else json.dumps(queries)
        if stdin:
            result = self.world.cli("search", "--batch", "-", *flags, input=text)
        else:
            path = self.world.root / "queries.json"
            if isinstance(text, bytes):
                path.write_bytes(text)
            else:
                path.write_text(text)
            result = self.world.cli("search", "--batch", str(path), *flags)
        return result


@pytest.fixture
def b(cli_world, tmp_path, monkeypatch):
    model_loads = install_fake_model(monkeypatch)
    world = cli_world(kbs=[KBConfig(name=KB, path=tmp_path / KB, kb_type="generic")])
    if not world.db.vec_available:
        pytest.skip("sqlite-vec unavailable; the vector leg cannot run")
    for axis, (entry_id, title, body, cosine) in enumerate(ENTRIES, start=1):
        world.add_entry(KB, f"---\nid: {entry_id}\ntype: note\ntitle: {title}\n---\n{body}\n")
        assert world.db.backend.upsert_embedding(entry_id, KB, vector_at(cosine, axis))
    return Batch(world, model_loads)


def payload(result):
    assert result.stdout, result.output
    return json.loads(result.stdout)


# --- the invariant: a slot is what a single search prints -------------------

SAME_AS_SINGLE = [
    pytest.param({"query": "falcon", "kb": KB}, ["falcon", "-k", KB], id="keyword"),
    pytest.param(
        {"query": "bird", "kb": KB, "limit": 1}, ["bird", "-k", KB, "--limit", "1"], id="limit"
    ),
    pytest.param(
        {"query": "falcon", "kb": KB, "type": ["note"]},
        ["falcon", "-k", KB, "--type", "note"],
        id="type-list",
    ),
    pytest.param(
        {"query": "falcon", "kb": KB, "type": ["note", "event"]},
        ["falcon", "-k", KB, "--type", "note", "--type", "event"],
        id="type-repeated",
    ),
    pytest.param(
        {"query": "falcon", "kb": KB, "mode": "semantic"},
        ["falcon", "-k", KB, "--mode", "semantic"],
        id="semantic",
    ),
    pytest.param(
        {"query": "falcon", "kb": KB, "mode": "hybrid"},
        ["falcon", "-k", KB, "--mode", "hybrid"],
        id="hybrid",
    ),
    pytest.param(
        {"query": "falcon", "kb": KB, "fields": "id,title"},
        ["falcon", "-k", KB, "--fields", "id,title"],
        id="fields",
    ),
    pytest.param({"query": "zebra", "kb": KB}, ["zebra", "-k", KB], id="no-hit"),
    pytest.param(
        {"query": "falcon", "kb": KB, "tag": "nope"},
        ["falcon", "-k", KB, "--tag", "nope"],
        id="tag",
    ),
    pytest.param(
        {"query": "falcon", "kb": KB, "status": "done"},
        ["falcon", "-k", KB, "--status", "done"],
        id="status",
    ),
]


@pytest.mark.parametrize(("spec", "single_args"), SAME_AS_SINGLE)
def test_a_slot_equals_the_single_search(b, spec, single_args):
    code, expected = b.single(*single_args)
    assert code == 0

    result = b.batch([spec])

    assert result.exit_code == 0, result.output
    body = payload(result)
    assert body["count"] == 1
    assert body["searches"] == [expected]


def test_the_batch_is_in_input_order_and_each_slot_is_its_own_search(b):
    specs = [
        {"query": "wren", "kb": KB},
        {"query": "falcon", "kb": KB, "mode": "semantic"},
        {"query": "marsh", "kb": KB},
        {"query": "falcon", "kb": KB},
    ]
    expected = [
        b.single("wren", "-k", KB)[1],
        b.single("falcon", "-k", KB, "--mode", "semantic")[1],
        b.single("marsh", "-k", KB)[1],
        b.single("falcon", "-k", KB)[1],
    ]

    body = payload(b.batch(specs))

    assert body["count"] == 4
    assert [s["query"] for s in body["searches"]] == ["wren", "falcon", "marsh", "falcon"]
    assert body["searches"] == expected
    # The slots differ, so an order-blind or de-duplicating batch fails above.
    assert body["searches"][1] != body["searches"][3]


def test_command_line_flags_are_defaults_and_a_slot_key_overrides_them(b):
    specs = [{"query": "bird"}, {"query": "bird", "limit": 1}, {"query": "bird", "kb": KB}]
    expected = [
        b.single("bird", "-k", KB, "--limit", "2")[1],
        b.single("bird", "-k", KB, "--limit", "1")[1],
        b.single("bird", "-k", KB, "--limit", "2")[1],
    ]
    assert expected[0]["count"] == 2 and expected[1]["count"] == 1

    body = payload(b.batch(specs, "-k", KB, "--limit", "2"))

    assert body["searches"] == expected


def test_fields_default_applies_to_every_slot_and_a_slot_can_override(b):
    specs = [{"query": "falcon"}, {"query": "falcon", "fields": "id,title"}]
    expected = [
        b.single("falcon", "-k", KB, "--fields", "id")[1],
        b.single("falcon", "-k", KB, "--fields", "id,title")[1],
    ]

    body = payload(b.batch(specs, "-k", KB, "--fields", "id"))

    assert body["searches"] == expected
    assert all(set(row) <= {"id", "kb_name"} for row in body["searches"][0]["results"])
    assert any("title" in row for row in body["searches"][1]["results"])


def test_a_fields_list_in_a_slot_is_the_same_as_the_comma_form(b):
    expected = b.single("falcon", "-k", KB, "--fields", "id,title")[1]

    body = payload(b.batch([{"query": "falcon", "kb": KB, "fields": ["id", "title"]}]))

    assert body["searches"] == [expected]


# --- a refused slot does not fail its siblings ------------------------------

REFUSED = [
    pytest.param(
        {"query": "falcon AND", "kb": KB}, ["falcon AND", "-k", KB], "QUERY_SYNTAX", id="syntax"
    ),
    pytest.param({"query": LONG, "kb": KB}, [LONG, "-k", KB], "QUERY_TOO_LONG", id="too-long"),
    pytest.param(
        {"query": "falcon", "kb": "no-such-kb"},
        ["falcon", "-k", "no-such-kb"],
        "KB_NOT_FOUND",
        id="unknown-kb",
    ),
]


@pytest.mark.parametrize(("spec", "single_args", "code"), REFUSED)
def test_a_refused_query_fills_its_slot_with_the_single_searchs_error(b, spec, single_args, code):
    single_code, single_error = b.single(*single_args)
    assert single_code == 1
    assert single_error["error_code"] == code
    good = {"query": "falcon", "kb": KB}
    good_expected = b.single("falcon", "-k", KB)[1]

    result = b.batch([good, spec, good])

    assert result.exit_code == 3, result.output  # partial: ADR-0046
    body = payload(result)
    assert body["count"] == 3
    assert body["searches"][0] == good_expected
    assert body["searches"][2] == good_expected
    slot = body["searches"][1]
    assert {k: slot[k] for k in single_error} == single_error
    assert slot["query"] == spec["query"]
    assert slot["count"] == 0 and slot["results"] == []


def test_a_batch_of_only_refused_queries_exits_1(b):
    result = b.batch([{"query": "falcon AND", "kb": KB}, {"query": "x", "kb": "no-such-kb"}])

    assert result.exit_code == 1, result.output
    body = payload(result)
    assert body["count"] == 2
    assert [s["error_code"] for s in body["searches"]] == ["QUERY_SYNTAX", "KB_NOT_FOUND"]


@pytest.mark.parametrize(
    "spec",
    [
        {"query": "falcon", "bogus": 1},
        {"query": "falcon", "expand": True},
        {"kb": KB},
        {"query": 7},
        {"query": ""},
        {"query": "falcon", "type": "note"},
        {"query": "falcon", "limit": "ten"},
        {"query": "falcon", "limit": 0},
        {"query": "falcon", "mode": "telepathy"},
        "falcon",
    ],
)
def test_a_malformed_slot_is_refused_in_its_own_slot(b, spec):
    good = {"query": "falcon", "kb": KB}
    result = b.batch([spec, good])

    assert result.exit_code == 3, result.output
    body = payload(result)
    assert body["searches"][1] == b.single("falcon", "-k", KB)[1]
    slot = body["searches"][0]
    assert slot["error_code"] == "VALIDATION_FAILED"
    assert slot["error"] and slot["retryable"] is False
    assert slot["count"] == 0 and slot["results"] == []


def test_an_unknown_key_is_named_in_the_refusal(b):
    body = payload(b.batch([{"query": "falcon", "bogus": 1}]))

    assert "bogus" in body["searches"][0]["error"]


def test_expand_is_refused_in_a_batch_and_says_why(b):
    body = payload(b.batch([{"query": "falcon", "expand": True}]))

    message = body["searches"][0]["error"]
    assert "expand" in message and "batch" in message


# --- the whole batch is refused ---------------------------------------------


def test_fifty_queries_run_and_fifty_one_are_refused_with_the_cap_named(b):
    fifty = [{"query": "falcon", "kb": KB}] * CAP
    ok = b.batch(fifty)
    assert ok.exit_code == 0, ok.output
    assert payload(ok)["count"] == CAP

    over = b.batch(fifty + [{"query": "falcon", "kb": KB}])

    assert over.exit_code == 1
    body = payload(over)
    assert body["error_code"] == "VALIDATION_FAILED"
    assert "50" in body["error"] and "51" in body["error"]
    assert "searches" not in body  # nothing ran


def test_an_empty_batch_is_a_valid_empty_answer(b):
    result = b.batch([])

    assert result.exit_code == 0, result.output
    assert payload(result) == {"count": 0, "searches": []}


def test_a_missing_file_is_refused(b):
    result = b.world.cli("search", "--batch", str(b.world.root / "nope.json"))

    assert result.exit_code == 1
    assert payload(result)["error_code"] == "NOT_FOUND"


@pytest.mark.parametrize("raw", ["not json", '{"query": "falcon"}', "[1, 2", '"falcon"'])
def test_input_that_is_not_a_json_list_is_refused(b, raw):
    result = b.batch(None, raw=raw)

    assert result.exit_code == 1, result.output
    body = payload(result)
    assert body["error_code"] == "VALIDATION_FAILED"
    assert "searches" not in body


@pytest.mark.parametrize("stdin", [False, True], ids=["file", "stdin"])
@pytest.mark.parametrize(
    "raw", [b"\xff\xfe", b'[{"query": "fal\xffcon"}]'], ids=["bom-like", "mid-text"]
)
def test_input_that_is_not_utf8_is_a_typed_refusal_never_a_traceback(b, raw, stdin):
    result = b.batch(None, raw=raw, stdin=stdin)

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output and "UnicodeDecodeError" not in result.output
    body = payload(result)
    assert body["error_code"] == "VALIDATION_FAILED"
    assert "UTF-8" in body["error"]
    assert body["retryable"] is False
    assert "searches" not in body


@pytest.mark.parametrize("error", [PermissionError("denied"), OSError("I/O error")])
def test_an_os_error_reading_the_batch_file_is_a_typed_refusal(b, monkeypatch, error):
    import pathlib

    path = b.world.root / "queries.json"
    path.write_text("[]")
    real = pathlib.Path.read_text

    def failing(self, *args, **kwargs):
        if self == path:
            raise error
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", failing)

    result = b.world.cli("search", "--batch", str(path))

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    body = payload(result)
    assert body["error_code"] == "VALIDATION_FAILED"
    assert "searches" not in body


def test_dash_reads_the_batch_from_stdin(b):
    expected = b.single("falcon", "-k", KB)[1]

    result = b.batch([{"query": "falcon", "kb": KB}], stdin=True)

    assert result.exit_code == 0, result.output
    assert payload(result)["searches"] == [expected]


# --- usage errors (exit 2) ---------------------------------------------------


def test_query_and_batch_together_is_a_usage_error(b):
    path = b.world.root / "q.json"
    path.write_text("[]")

    result = b.world.cli("search", "falcon", "--batch", str(path))

    assert result.exit_code == 2, result.output
    assert "QUERY" in plain(result.output) and "--batch" in plain(result.output)
    assert "No such option" not in result.output


@pytest.mark.control(reason="QUERY is required today; the test pins that it stays required")
def test_no_query_and_no_batch_is_still_a_usage_error(b):
    result = b.world.cli("search")

    assert result.exit_code == 2
    assert "missing argument" in plain(result.output).lower()


@pytest.mark.parametrize("flag", [["--expand"], ["--files"]])
def test_expand_and_files_are_usage_errors_with_batch(b, flag):
    path = b.world.root / "q.json"
    path.write_text('[{"query": "falcon"}]')

    result = b.world.cli("search", "--batch", str(path), *flag)

    assert result.exit_code == 2, result.output
    assert flag[0] in plain(result.output) and "--batch" in plain(result.output)
    assert "No such option" not in result.output


# --- one process: one model load --------------------------------------------


def test_a_semantic_batch_loads_the_model_once(b):
    specs = [{"query": q, "kb": KB, "mode": "semantic"} for q in ("falcon", "marsh", "wren")]
    assert b.model_loads == []

    result = b.batch(specs)

    assert result.exit_code == 0, result.output
    assert len(payload(result)["searches"]) == 3
    assert len(b.model_loads) == 1


def test_the_index_is_opened_once_for_the_batch(b, monkeypatch):
    import pyrite.cli.context as ctx

    opened = []
    real = ctx.open_index_db

    def counting(config):
        opened.append(1)
        return real(config)

    monkeypatch.setattr(ctx, "open_index_db", counting)

    result = b.batch([{"query": q, "kb": KB} for q in ("falcon", "marsh", "wren")])

    assert result.exit_code == 0, result.output
    assert len(opened) == 1


def test_semantic_search_unavailable_is_the_single_searchs_answer_per_slot(b, monkeypatch):
    monkeypatch.setattr(es, "is_available", lambda: False)
    _, expected = b.single("falcon", "-k", KB, "--mode", "semantic")
    assert expected.get("warnings"), expected  # the single search's fallback warning

    body = payload(
        b.batch(
            [
                {"query": "falcon", "kb": KB, "mode": "semantic"},
                {"query": "falcon", "kb": KB},
                {"query": "falcon", "kb": KB, "mode": "semantic"},
            ]
        )
    )

    assert body["searches"][0] == expected
    assert body["searches"][2] == expected
    assert body["searches"][1] == b.single("falcon", "-k", KB)[1]
    assert "warnings" not in body["searches"][1]
