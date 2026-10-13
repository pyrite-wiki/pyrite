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


def install_fake_model(monkeypatch) -> list[str]:
    """Make every model load a `FakeModel`; return the list of names asked for."""
    names: list[str] = []

    def _load(name):
        names.append(name)
        return FakeModel()

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
