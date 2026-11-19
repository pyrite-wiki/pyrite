"""Unit tests for GitService status typing and hook context signatures (#1047)."""

from __future__ import annotations

import tempfile
import typing
from pathlib import Path

from pyrite.models.base import Entry
from pyrite.models.core_types import NoteEntry
from pyrite.plugins.context import PluginContext
from pyrite.services.git_service import GitService, GitWorkingTreeStatus
from pyrite.services.hook_runner import HookRunner
from pyrite.services.kb_service import KBService


def test_git_service_get_status_structure_and_types():
    """GitService.get_status returns a typed GitWorkingTreeStatus dict."""
    hints = typing.get_type_hints(GitService.get_status)
    assert hints.get("return") is GitWorkingTreeStatus
    assert set(GitWorkingTreeStatus.__annotations__) == {
        "clean",
        "staged",
        "unstaged",
        "untracked",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        non_git_path = Path(tmpdir)
        status = GitService.get_status(non_git_path)

        assert isinstance(status, dict)
        assert status["clean"] is True
        assert isinstance(status["staged"], list)
        assert isinstance(status["unstaged"], list)
        assert isinstance(status["untracked"], list)

        # Mutability of lists works as typed without object.append errors
        status["untracked"].append("new_file.txt")
        assert "new_file.txt" in status["untracked"]


def test_hook_runner_accepts_plugin_context_and_dict():
    """HookRunner dispatches hooks with either PluginContext or a dict."""
    assert "PluginContext" in str(HookRunner.run_before_save.__annotations__.get("context"))
    assert "PluginContext" in str(KBService._run_hooks.__annotations__.get("context"))
    runner = HookRunner(plugin_registry=None)
    called = []

    def sample_hook(entry: Entry, ctx):
        called.append(ctx.get("op", "unknown") if hasattr(ctx, "get") else "none")
        return entry

    runner.register_core_hook("before_save", sample_hook)
    entry = NoteEntry(id="test-entry", title="Test Entry", body="")

    # Call with dict
    res1 = runner.run_before_save(entry, {"op": "dict_op"})
    assert res1.id == "test-entry"
    assert called[-1] == "dict_op"

    # Call with PluginContext
    ctx = PluginContext(config=None, db=None, operation="create", extra={"op": "plugin_op"})
    res2 = runner.run_before_save(entry, ctx)
    assert res2.id == "test-entry"
    assert called[-1] == "plugin_op"
