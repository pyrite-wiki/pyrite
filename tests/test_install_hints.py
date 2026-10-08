"""Install remedies must refer to the actual source distribution (#775)."""

import json
import shlex
from unittest.mock import Mock

import pytest

from pyrite.services import embedding_service


def test_missing_semantic_has_a_source_install_command(monkeypatch):
    monkeypatch.setattr(embedding_service, "is_available", lambda: False)
    remedy = embedding_service.semantic_unavailable(False)[2]
    assert "pip install pyrite[semantic]" not in remedy
    assert "pip install" in remedy
    assert "git+" in remedy or "-e " in remedy


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {"url": "file:///C:/My%20Projects/pyrite", "dir_info": {"editable": True}},
            ["pip", "install", "-e", "C:/My Projects/pyrite[semantic]"],
        ),
        (
            {
                "url": "https://github.com/pyrite-wiki/pyrite.git",
                "vcs_info": {"vcs": "git", "commit_id": "abc123", "requested_revision": "v0.25.6"},
            },
            [
                "pip",
                "install",
                "pyrite[semantic] @ git+https://github.com/pyrite-wiki/pyrite.git@abc123",
            ],
        ),
        (
            {
                "url": "https://github.com/pyrite-wiki/pyrite.git",
                "subdirectory": "pkg",
                "vcs_info": {"vcs": "git", "requested_revision": "v0.25.6"},
            },
            [
                "pip",
                "install",
                "pyrite[semantic] @ git+https://github.com/pyrite-wiki/pyrite.git@v0.25.6#subdirectory=pkg",
            ],
        ),
    ],
)
def test_command_preserves_install_shape(monkeypatch, metadata, expected):
    from pyrite.utils import install_hints

    dist = Mock()
    dist.read_text.return_value = json.dumps(metadata)
    monkeypatch.setattr(install_hints, "distribution", lambda name: dist)
    assert shlex.split(install_hints.extra_install_command("semantic")) == expected


@pytest.mark.parametrize("metadata", [None, "bad json", "[]", '{"url": "file:///tmp/archive"}'])
def test_unknown_install_uses_git_fallback(monkeypatch, metadata):
    from pyrite.utils import install_hints

    dist = Mock()
    dist.read_text.return_value = metadata
    monkeypatch.setattr(install_hints, "distribution", lambda name: dist)
    assert shlex.split(install_hints.extra_install_command("semantic")) == [
        "pip",
        "install",
        "pyrite[semantic] @ git+https://github.com/pyrite-wiki/pyrite.git@dev",
    ]
