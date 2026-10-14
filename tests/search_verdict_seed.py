"""Seed for the search-verdict tests (#668): entries at a known similarity.

No embedding model is loaded. `install_fake_model` makes every query embed to
`QUERY_VECTOR`, and `add_entry(..., cosine=c)` stores a unit vector whose
cosine with it is ``c``, in a real sqlite-vec index. The similarities a test
reads are therefore the backend's own arithmetic on vectors the test chose.
"""

from __future__ import annotations

import math

import pytest

import pyrite.services.embedding_service as es
from pyrite.storage.database import PyriteDB

DIM = 384


def vector_at(cosine: float, axis: int) -> list[float]:
    """A unit vector whose cosine with the query vector (axis 0) is ``cosine``.

    Each entry gets its own ``axis``, so entries are orthogonal to one another
    apart from their shared share of the query direction.
    """
    assert 1 <= axis < DIM
    vec = [0.0] * DIM
    vec[0] = cosine
    vec[axis] = math.sqrt(1.0 - cosine * cosine)
    return vec


QUERY_VECTOR = [1.0] + [0.0] * (DIM - 1)


class _Vec(list):
    """What `model.encode` returns, as far as `embed_text` needs: `.tolist()`."""

    def tolist(self):
        return list(self)


class FakeModel:
    """Stands in for a sentence-transformers model; every text is the query."""

    def encode(self, text, **kwargs):
        if isinstance(text, list):
            return [_Vec(QUERY_VECTOR) for _ in text]
        return _Vec(QUERY_VECTOR)


class FailingModel:
    """A model that loads and then fails on use: "the model went away".

    The model is the process edge, so a test of a failure in the vector leg
    injects it here and lets the real `EmbeddingService` and `SearchService`
    run, instead of patching either.
    """

    def __init__(self, error: Exception | None = None):
        self.error = error or RuntimeError("model went away")

    def encode(self, text, **kwargs):
        raise self.error


def install_fake_model(monkeypatch, model=None) -> list[str]:
    """Make every model load a `FakeModel` (or ``model``); return the names asked for."""
    names: list[str] = []

    def _load(name):
        names.append(name)
        return model or FakeModel()

    monkeypatch.setattr(es, "is_available", lambda: True)
    monkeypatch.setattr(es, "_load_model", _load)
    monkeypatch.setattr(es, "_MODEL_CACHE", {})
    return names


def make_db(tmp_path, name="i.db"):
    db = PyriteDB(tmp_path / name)
    if not db.vec_available:
        db.close()
        pytest.skip("sqlite-vec unavailable; the vector leg cannot run")
    return db


def add_entry(db, entry_id, kb="t", *, cosine=None, axis=None, title=None, body="", **fields):
    """Index one entry; with ``cosine``, embed it at that similarity to the query."""
    db.upsert_entry(
        {
            "id": entry_id,
            "kb_name": kb,
            "entry_type": "note",
            "title": title or entry_id,
            "summary": "",
            "body": body,
            "tags": [],
            "sources": [],
            "links": [],
            **fields,
        }
    )
    if cosine is not None:
        assert db.backend.upsert_embedding(entry_id, kb, vector_at(cosine, axis))


def embed_crowd(db, top, rest, kb="t", prefix="e", start_axis=1, body=""):
    """One entry at ``top`` and one per value in ``rest``."""
    for i, cosine in enumerate([top, *rest]):
        add_entry(db, f"{prefix}{i}", kb, cosine=cosine, axis=start_axis + i, body=body)


# ----------------------------------------------------------------------------
# A small real KB for tests of the read commands and endpoints (`--fields`, the
# output formats, the console width). One entry, written through `KBService`, so
# the file, the index and every surface agree; no service is patched.


def seed_entry_world(
    config, db, entry_id="entry-1", kb="kb", title="T", body="excluded secret", **metadata
):
    """Write one note, ``status: waiting`` (override with ``metadata``), tagged ``a``.

    Its body is "excluded secret" unless ``body`` says otherwise, so `search excluded` finds it and a `--fields`
    projection that leaves the body out can be told by the words in the output.
    """
    from pyrite.services.kb_service import KBService

    KBService(config, db).create_entry(
        kb,
        entry_id,
        title,
        "note",
        body,
        tags=["a"],
        metadata={"status": "waiting", **metadata},
    )


def make_cli_world(tmp_path, kb="kb", entries=1, entry_id=None, **entry):
    """A `PyriteConfig` over a real index holding ``entry-1`` (and ``entry-2`` ...)."""
    from pyrite.config import KBConfig, PyriteConfig, Settings

    (tmp_path / kb).mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name=kb, path=tmp_path / kb, kb_type="generic")],
        settings=Settings(index_path=tmp_path / "index.db", auto_embed=False),
    )
    db = PyriteDB(tmp_path / "index.db")
    try:
        for n in range(1, entries + 1):
            seed_entry_world(config, db, entry_id or f"entry-{n}", kb, **entry)
    finally:
        db.close()
    return config


def cli_world(config):
    """Point the CLI at ``config``: the one patch a CLI test needs (config is its world)."""
    from contextlib import ExitStack
    from unittest.mock import patch

    stack = ExitStack()
    stack.enter_context(patch("pyrite.cli.context.load_config", return_value=config))
    stack.enter_context(patch("pyrite.cli.search_commands.load_config", return_value=config))
    return stack
