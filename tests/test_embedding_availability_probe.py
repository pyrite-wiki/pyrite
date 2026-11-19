"""#1101: the availability probe must not import torch to answer a yes/no question."""

import sys

import pytest

from pyrite.services import embedding_service


def _forget_heavy_modules(monkeypatch):
    for name in list(sys.modules):
        if name == "torch" or name.startswith(("torch.", "sentence_transformers")):
            monkeypatch.delitem(sys.modules, name, raising=False)


def test_the_probe_answers_without_importing_the_package(monkeypatch):
    """``find_spec`` sees the package; importing it would pull in torch (~300 MB)."""
    _forget_heavy_modules(monkeypatch)
    monkeypatch.setattr(
        "importlib.util.find_spec",
        lambda name: object() if name == "sentence_transformers" else None,
    )

    assert embedding_service.is_available() is True
    loaded = {
        m for m in sys.modules if m == "torch" or m.startswith(("torch.", "sentence_transformers"))
    }
    assert loaded == set(), loaded


def test_the_probe_answers_absent_without_importing_anything(monkeypatch):
    _forget_heavy_modules(monkeypatch)
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)

    assert embedding_service.is_available() is False
    loaded = {
        m for m in sys.modules if m == "torch" or m.startswith(("torch.", "sentence_transformers"))
    }
    assert loaded == set(), loaded


def test_a_broken_install_is_reported_when_the_model_first_loads(monkeypatch):
    """The trade the issue asks for: the finder may say "installed" for a broken
    install, so the first real import must say what is broken and how to fix it."""
    _forget_heavy_modules(monkeypatch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)

    with pytest.raises(embedding_service.EmbeddingModelUnavailableError) as exc:
        embedding_service._load_model("all-MiniLM-L6-v2")

    message = str(exc.value)
    assert "installed but cannot be imported" in message, message
    assert "semantic" in message, message
