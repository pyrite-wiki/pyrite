"""What a caller is told: Pyrite's own words always; a fault's text only to
the local operator.

Three properties, each driven through the surface a caller uses:

- the local process (the CLI) sees a fault's own text; a network caller
  (REST, MCP over HTTP) sees Pyrite's fixed message (``public_text`` decides
  by ``acting.is_local_process`` unless told);
- validation text that Pyrite writes for a caller -- a title that is not a
  string, a value that cannot be written, a value the index cannot store --
  is a Pyrite refusal, so every surface shows it, network callers included;
- no REST body or result message carries a non-Pyrite exception's text or
  class name.
"""

from __future__ import annotations

import json
import logging

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

SENTINEL = "/srv/sentinel-b72e/private/path"
FIXED = "Internal error; the server log has the details."
KB = "k"


@pytest.fixture
def world(tmp_path):
    kb = tmp_path / "kb"
    (kb / "notes").mkdir(parents=True)
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: One\ntype: note\n---\nbody\n")
    w = build_world(tmp_path / "world", kbs=[KBConfig(name=KB, path=kb, kb_type="generic")])
    w.index(KB)
    yield w, kb
    w.close()


def _unreadable(monkeypatch):
    """Every entry file load fails with an OS error naming a server path: a
    genuine fault, not a validation."""
    from pyrite.storage.repository import KBRepository

    def boom(self, path, *a, **k):
        raise PermissionError(13, "Permission denied", SENTINEL)

    monkeypatch.setattr(KBRepository, "load_entry_from_file", boom)


# -- 1. the local process sees the fault's text; the network does not ----------


def test_cli_qa_validate_shows_the_local_operator_the_faults_text(world, monkeypatch):
    w, _kb = world
    _unreadable(monkeypatch)
    r = w.cli("qa", "validate", KB, "--entry", "one")
    assert SENTINEL in r.output, r.output


@pytest.mark.control(reason="network callers already got the fixed message")
def test_rest_qa_validate_shows_the_fixed_message_for_the_same_fault(world, monkeypatch):
    w, _kb = world
    _unreadable(monkeypatch)
    r = w.client.get(f"/api/qa/validate/one?kb={KB}")
    assert r.status_code == 200, r.text
    assert SENTINEL not in r.text
    assert FIXED in r.text


# -- 2. Pyrite's validation text reaches network callers ----------------------------


def test_rest_qa_validate_names_a_title_that_is_not_a_string(world):
    w, kb = world
    (kb / "notes" / "one.md").write_text("---\nid: one\ntitle: [a, b]\ntype: note\n---\nb\n")
    r = w.client.get(f"/api/qa/validate/one?kb={KB}")
    assert r.status_code == 200, r.text
    assert "title must be a string" in r.text, r.text


def test_rest_import_names_a_title_an_id_cannot_be_derived_from(world):
    w, _kb = world
    payload = json.dumps([{"title": ["a", "b"], "entry_type": "note", "body": "b"}])
    r = w.client.post(
        f"/api/entries/import?kb={KB}", files={"file": ("x.json", payload, "application/json")}
    )
    assert r.status_code in (200, 207), r.text
    assert "title is a list" in r.text, r.text
    assert FIXED not in r.text


def test_rest_import_names_a_value_that_cannot_be_written(world):
    w, _kb = world
    md = "---\ntitle: Has a set\ntype: note\nextra: !!set {a, b}\n---\nbody\n"
    r = w.client.post(f"/api/entries/import?kb={KB}", files={"file": ("x.md", md, "text/markdown")})
    assert r.status_code in (200, 207), r.text
    assert FIXED not in r.text, r.text


def test_the_validation_classes_are_pyrite_refusals_and_keep_their_base():
    """Callers that catch the old built-in classes keep working."""
    from pyrite.exceptions import EntryTypeError, EntryValueError, PyriteError
    from pyrite.storage.file_operations import OperationRefusedError

    assert issubclass(EntryTypeError, TypeError) and issubclass(EntryTypeError, PyriteError)
    assert issubclass(EntryValueError, ValueError) and issubclass(EntryValueError, PyriteError)
    assert issubclass(OperationRefusedError, PyriteError)


def test_derive_id_refuses_with_a_pyrite_error():
    from pyrite.exceptions import PyriteError
    from pyrite.schema.validators import generate_entry_id

    with pytest.raises(PyriteError, match="title is a list"):
        generate_entry_id(["a", "b"])


def test_the_index_row_refusals_are_pyrite_errors(world):
    from pyrite.exceptions import PyriteError
    from pyrite.storage.index import IndexManager

    w, _kb = world
    with pytest.raises(PyriteError, match="not found in config"):
        IndexManager(w.db, w.config).index_with_attribution("no-such-kb", git_service=object())


# -- 3. no REST body or message carries a non-Pyrite exception's text or class -------


def test_review_create_does_not_return_a_faults_text(world, monkeypatch):
    from pyrite.services.review_service import ReviewService

    w, _kb = world

    def boom(self, **k):
        raise OSError(SENTINEL)

    monkeypatch.setattr(ReviewService, "create_review", boom)
    r = w.client.post(
        "/api/reviews",
        json={
            "entry_id": "one",
            "kb_name": KB,
            "reviewer": "r",
            "reviewer_type": "user",
            "result": "pass",
        },
    )
    assert SENTINEL not in r.text, r.text


@pytest.mark.parametrize("route", ["create", "update", "patch"])
def test_entry_writes_do_not_return_a_value_errors_text(world, monkeypatch, route):
    from pyrite.services.kb_service import KBService

    w, _kb = world

    def boom(self, *a, **k):
        raise ValueError(f"{SENTINEL} is not in the subpath of /x")

    if route == "create":
        monkeypatch.setattr(KBService, "create_entry", boom)
        monkeypatch.setattr(KBService, "_prepare_and_save", boom)
        r = w.client.post(
            "/api/entries", json={"kb": KB, "entry_type": "note", "title": "T", "body": "b"}
        )
    elif route == "update":
        monkeypatch.setattr(KBService, "update", boom)
        r = w.client.put("/api/entries/one", json={"kb": KB, "body": "edit"})
    else:
        monkeypatch.setattr(KBService, "update", boom)
        r = w.client.patch("/api/entries/one", json={"kb": KB, "field": "status", "value": "x"})
    assert r.status_code >= 400, r.text
    assert SENTINEL not in r.text, r.text


def test_a_rubric_checker_fault_names_no_exception_class(world, monkeypatch, caplog):
    from pyrite.services import rubric_checkers

    w, kb = world
    (kb / "kb.yaml").write_text(
        "name: k\nevaluation_rubric:\n  - {text: Boom item, checker: boom_checker}\n"
    )

    def boom_checker(entry, schema, params):
        raise RuntimeError(SENTINEL)

    monkeypatch.setitem(rubric_checkers.NAMED_CHECKERS, "boom_checker", boom_checker)
    with caplog.at_level(logging.WARNING):
        r = w.client.get(f"/api/qa/validate/one?kb={KB}")
    assert r.status_code == 200, r.text
    assert "Boom item" in r.text  # the item is reported as not checked
    assert SENTINEL not in r.text and "RuntimeError" not in r.text, r.text


def test_the_ai_connection_test_names_no_exception_class(world):
    import dataclasses

    from pyrite.server.api import get_llm_service
    from pyrite.services.llm_service import LLMService

    w, _kb = world
    settings = dataclasses.replace(
        w.config.settings, ai_provider="anthropic", ai_api_key="k", ai_model="m"
    )
    llm = LLMService(settings)

    class ProviderSDKError(Exception):
        pass

    def boom():
        raise ProviderSDKError(SENTINEL)

    llm._get_anthropic_client = boom
    w.app.dependency_overrides[get_llm_service] = lambda: llm
    r = w.client.post("/api/ai/test")
    assert r.json()["ok"] is False
    assert "ProviderSDKError" not in r.text and SENTINEL not in r.text
