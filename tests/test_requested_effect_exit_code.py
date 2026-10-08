"""Exit 0 means every effect the caller asked for happened (#526).

The user's model: "Exit 0 means it did what I asked. If it did only part, it
tells me which part, and a script can tell."

The exit codes (docs/json-contracts.md, "Exit codes (CLI)"):

- ``0``  everything asked for happened;
- ``1``  refused: nothing was done (or the subject does not exist);
- ``2``  usage (click's);
- ``3``  the command ran and did only part of it, or left items for a retry.
  It says which, so a script reads the output instead of repeating the call.

Three kinds of test live here, each entering at the command line:

1. *Behaviour*: one test per command that used to print a failure and exit 0.
   They run the real entry point in a subprocess with a scratch config, so
   the exit status is the process's, not a CliRunner's.
2. *The registry*: every command Typer registers, in the three CLIs, is
   classified below; a new command fails ``test_every_registered_command_is_classified``
   until someone says whether it can do part of its job.
3. *The scan*: every ``except`` handler in the CLI modules that does not exit
   or raise is listed in ``HANDLERS`` with a verdict. A new swallowed failure
   fails ``test_every_swallowing_handler_is_judged``.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import typer

REPO = Path(__file__).resolve().parent.parent

PARTIAL = 3
REFUSED = 1


# --------------------------------------------------------------------------
# The real command line, in a scratch HOME
# --------------------------------------------------------------------------


class Cli:
    def __init__(self, root: Path):
        self.root = root
        self.kb = root / "kb"
        self.env = {
            **os.environ,
            "HOME": str(root / "home"),
            "PYRITE_CONFIG_DIR": str(root / "cfg"),
            # An empty model cache and no network: the embedding model cannot load.
            "HF_HOME": str(root / "hf"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "PYRITE_AUTO_EMBED": "0",
            "NO_COLOR": "1",
            "COLUMNS": "200",
        }
        (root / "home").mkdir()

    def run(self, entry: str, *args: str, env: dict | None = None):
        module, _, func = entry.partition(":")
        code = f"import sys; from {module} import app; sys.argv[0]='pyrite'; app()"
        return subprocess.run(
            [sys.executable, "-c", code, *args],
            capture_output=True,
            text=True,
            env={**self.env, **(env or {})},
            cwd=self.root,
            timeout=120,
        )

    def pyrite(self, *args: str, **kw):
        return self.run("pyrite.cli", *args, **kw)


@pytest.fixture
def cli(tmp_path):
    c = Cli(tmp_path)
    made = c.pyrite("init", "-t", "research", "-p", str(c.kb), "-n", "demo", "--format", "rich")
    assert made.returncode == 0, made.stdout + made.stderr
    return c


def _create(cli: Cli, title: str, *extra: str):
    return cli.pyrite("create", "-k", "demo", "--type", "note", "--title", title, *extra)


def _unreadable(path: Path, text: str) -> None:
    """A file the process cannot read: the one failure that does not depend on a parser's leniency."""
    path.write_text(text)
    path.chmod(0)
    if os.access(path, os.R_OK):
        path.chmod(0o644)
        pytest.skip("running as a user that can read mode-000 files")


def _file_for(cli: Cli, entry_id: str) -> Path:
    found = list(cli.kb.rglob(f"{entry_id}.md"))
    assert found, f"{entry_id}.md was not written under {cli.kb}"
    return found[0]


# --------------------------------------------------------------------------
# create --link
# --------------------------------------------------------------------------


class TestCreateWithFailedLink:
    def test_bad_link_keeps_the_entry_exits_partial_and_says_what_to_run(self, cli):
        assert _create(cli, "Target A").returncode == 0
        out = _create(
            cli, "Dangling", "--link", "target-a:informs", "--link", "does-not-exist:informs"
        )
        assert out.returncode == PARTIAL, out.stdout + out.stderr
        text = out.stdout + out.stderr
        # The entry exists, and the answer says so.
        body = _file_for(cli, "dangling").read_text()
        assert "created" in text.lower()
        # The good link landed; the bad one is named.
        assert "target-a" in body
        assert "does-not-exist" not in body
        assert "does-not-exist" in text
        # The retry trap: the caller is told what finishes the job.
        assert "pyrite link dangling does-not-exist -r informs -k demo" in text

    @pytest.mark.control(reason="control: the retry answer was ENTRY_EXISTS before and stays so")
    def test_retry_of_a_partial_create_is_entry_exists(self, cli):
        _create(cli, "Dangling", "--link", "nope:informs")
        retry = _create(cli, "Dangling", "--link", "nope:informs")
        assert retry.returncode == REFUSED
        assert "ENTRY_EXISTS" in retry.stdout + retry.stderr

    @pytest.mark.control(reason="control: a create whose links all resolve still exits 0")
    def test_all_links_good_exits_zero(self, cli):
        _create(cli, "Target A")
        out = _create(cli, "Whole", "--link", "target-a:informs")
        assert out.returncode == 0, out.stdout + out.stderr

    @pytest.mark.control(reason="control: a create with no link still exits 0")
    def test_no_link_exits_zero(self, cli):
        assert _create(cli, "Plain").returncode == 0


# --------------------------------------------------------------------------
# backlinks
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "argv"),
    [("pyrite.cli", ["backlinks"]), ("pyrite.read_cli", ["backlinks"])],
    ids=["pyrite", "pyrite-read"],
)
class TestBacklinks:
    def test_absent_subject_is_not_found_in_json(self, cli, entry, argv):
        out = cli.run(entry, *argv, "nope", "-k", "demo", *(["--format", "json"]))
        assert out.returncode == REFUSED, out.stdout + out.stderr
        payload = json.loads(out.stdout)
        # Which spelling is #610's to settle; this theme needs the not-found family.
        assert payload["error_code"].endswith("NOT_FOUND")

    def test_absent_subject_is_not_found_in_rich(self, cli, entry, argv):
        out = cli.run(entry, *argv, "nope", "-k", "demo", "--format", "rich")
        assert out.returncode == REFUSED, out.stdout + out.stderr
        assert "NOT_FOUND" in out.stdout + out.stderr or "not found" in (out.stdout + out.stderr)

    def test_existing_entry_with_none_is_an_empty_list(self, cli, entry, argv):
        _create(cli, "Lonely")
        out = cli.run(entry, *argv, "lonely", "-k", "demo", "--format", "json")
        assert out.returncode == 0, out.stdout + out.stderr
        payload = json.loads(out.stdout)
        assert payload["entries"] == []
        assert payload["total"] == 0

    @pytest.mark.control(reason="control: a backlink that exists is still listed")
    def test_existing_entry_with_one_lists_it(self, cli, entry, argv):
        _create(cli, "Target A")
        _create(cli, "Linker", "--link", "target-a:informs")
        out = cli.run(entry, *argv, "target-a", "-k", "demo", "--format", "json")
        assert out.returncode == 0, out.stdout + out.stderr
        assert json.loads(out.stdout)["total"] == 1

    def test_unknown_kb_is_kb_not_found(self, cli, entry, argv):
        out = cli.run(entry, *argv, "x", "-k", "no-such-kb", "--format", "json")
        assert out.returncode == REFUSED
        assert json.loads(out.stdout)["error_code"] == "KB_NOT_FOUND"

    def test_entry_on_disk_but_not_yet_indexed_is_not_absent(self, cli, entry, argv):
        # Principle 5: a miss in the index is not an answer.
        (cli.kb / "notes").mkdir(exist_ok=True)
        (cli.kb / "notes" / "handwritten.md").write_text(
            "---\nid: handwritten\ntitle: Handwritten\ntype: note\n---\nbody\n"
        )
        out = cli.run(entry, *argv, "handwritten", "-k", "demo", "--format", "json")
        assert out.returncode == 0, out.stdout + out.stderr
        assert json.loads(out.stdout)["total"] == 0


# --------------------------------------------------------------------------
# index embed, pyrite-admin index embed
# --------------------------------------------------------------------------


class TestEmbed:
    def test_offline_embed_that_could_not_embed_exits_partial(self, cli):
        _create(cli, "Needs a vector")
        out = cli.pyrite("index", "embed")
        text = out.stdout + out.stderr
        if "sentence-transformers is not installed" in text or "DEPENDENCY_MISSING" in text:
            pytest.skip("semantic extra not installed")
        assert "Errors:" in text, text
        # None was embedded: the batch rule says 1, not 3.
        assert out.returncode == REFUSED, text

    @pytest.mark.control(reason="control: an empty index was refused with 1 before and still is")
    def test_embed_on_an_empty_index_is_refused_not_partial(self, cli):
        out = cli.pyrite("index", "embed")
        assert out.returncode == REFUSED, out.stdout + out.stderr

    def test_the_batch_rule(self):
        from pyrite.utils.errors import exit_unless_whole

        exit_unless_whole(0, 0)
        exit_unless_whole(0, 5)
        for failed, done, code in ((2, 3, PARTIAL), (2, 0, REFUSED)):
            with pytest.raises(typer.Exit) as raised:
                exit_unless_whole(failed, done)
            assert raised.value.exit_code == code

    def test_admin_embed_offline_follows_the_batch_rule_and_does_not_crash(self, cli):
        # It used to call a method EmbeddingService does not have.
        _create(cli, "Needs a vector")
        cli.pyrite("index", "sync", "--no-embed")
        out = cli.run("pyrite.admin_cli", "index", "embed", "demo")
        text = out.stdout + out.stderr
        if "DEPENDENCY_MISSING" in text:
            pytest.skip("semantic extra not installed")
        assert "Traceback" not in text, text
        assert out.returncode == REFUSED, text  # none embedded: the same rule as `index embed`

    def test_admin_embed_of_an_unknown_kb_is_kb_not_found(self, cli):
        out = cli.run("pyrite.admin_cli", "index", "embed", "nosuchkb")
        text = out.stdout + out.stderr
        if "sentence-transformers is not installed" in text:
            pytest.skip("semantic extra not installed")
        assert out.returncode == REFUSED, text
        assert "KB_NOT_FOUND" in text, text

    def test_admin_embed_without_the_extra_exits_one(self, cli, tmp_path):
        stub = tmp_path / "stub"
        (stub / "sentence_transformers").mkdir(parents=True)
        (stub / "sentence_transformers" / "__init__.py").write_text(
            "raise ImportError('no semantic extra')\n"
        )
        cli.pyrite("index", "sync")
        out = cli.run(
            "pyrite.admin_cli",
            "index",
            "embed",
            "demo",
            env={"PYTHONPATH": f"{stub}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"},
        )
        assert "DEPENDENCY_MISSING" in out.stdout + out.stderr
        assert out.returncode == REFUSED, out.stdout + out.stderr


# --------------------------------------------------------------------------
# schema migrate
# --------------------------------------------------------------------------


class TestSchemaMigrate:
    def test_a_file_that_cannot_be_migrated_exits_partial(self, cli):
        _create(cli, "Fine")
        _unreadable(cli.kb / "broken.md", "---\nid: broken\ntitle: x\n---\nbody\n")
        out = cli.pyrite("schema", "migrate", "-k", "demo")
        text = out.stdout + out.stderr
        assert "Errors:" in text, text
        assert out.returncode == PARTIAL, text

    @pytest.mark.control(reason="control: a clean migrate still exits 0")
    def test_clean_migrate_exits_zero(self, cli):
        _create(cli, "Fine")
        out = cli.pyrite("schema", "migrate", "-k", "demo")
        assert out.returncode == 0, out.stdout + out.stderr

    @pytest.mark.control(reason="control: a migrate with nothing to do still exits 0")
    def test_empty_kb_exits_zero(self, cli):
        out = cli.pyrite("schema", "migrate", "-k", "demo")
        assert out.returncode == 0, out.stdout + out.stderr


# --------------------------------------------------------------------------
# In-process cases: the failure needs a seam the command line does not offer
# --------------------------------------------------------------------------


@pytest.fixture
def inproc(tmp_path):
    """A KB and config the Typer app can be invoked against in this process."""
    from unittest.mock import patch

    from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
    from pyrite.storage.database import PyriteDB
    from pyrite.storage.index import IndexManager

    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test-kb", path=kb_path, kb_type=KBType.GENERIC)],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    IndexManager(db, config).index_all()
    db.close()
    with (
        patch("pyrite.cli.load_config", return_value=config),
        patch("pyrite.cli.context.load_config", return_value=config),
    ):
        yield {"config": config, "tmp": tmp_path, "kb": kb_path}


def _invoke(*args):
    from typer.testing import CliRunner

    from pyrite.cli import app

    return CliRunner().invoke(app, list(args))


class TestReconcile:
    def test_entries_that_could_not_be_checked_exit_partial(self, inproc, monkeypatch):
        from pyrite.services.kb_service import KBService, ReconcileResult

        monkeypatch.setattr(
            KBService,
            "reconcile_templated_subdirectories",
            lambda self, kb, apply=False: ReconcileResult(errors=[("e1", "bad template")]),
        )
        out = _invoke("index", "reconcile", "test-kb")
        assert "Could not reconcile e1" in out.output
        assert out.exit_code == PARTIAL

    @pytest.mark.control(reason="control: a clean reconcile still exits 0")
    def test_nothing_wrong_exits_zero(self, inproc, monkeypatch):
        from pyrite.services.kb_service import KBService, ReconcileResult

        monkeypatch.setattr(
            KBService,
            "reconcile_templated_subdirectories",
            lambda self, kb, apply=False: ReconcileResult(),
        )
        assert _invoke("index", "reconcile", "test-kb").exit_code == 0


class TestImportAndBulkLinks:
    def _file(self, inproc, entries):
        path = inproc["tmp"] / "import.json"
        path.write_text(json.dumps({"entries": entries}))
        return str(path)

    def test_import_that_wrote_some_and_refused_some_exits_partial(self, inproc):
        path = self._file(
            inproc,
            [
                {"title": "Good", "entry_type": "note", "body": "ok"},
                {"title": "Bad", "entry_type": "note", "body": "x", "body_truncated": True},
            ],
        )
        out = _invoke("import", path, "--kb", "test-kb")
        assert out.exit_code == PARTIAL, out.output

    @pytest.mark.control(reason="control: an import that wrote nothing already exited 1")
    def test_import_that_wrote_nothing_exits_one(self, inproc):
        path = self._file(
            inproc, [{"title": "Bad", "entry_type": "note", "body": "x", "body_truncated": True}]
        )
        out = _invoke("import", path, "--kb", "test-kb")
        assert out.exit_code == REFUSED, out.output

    def test_bulk_links_with_one_failed_exit_partial(self, inproc):
        assert (
            _invoke(
                "create", "-k", "test-kb", "--type", "note", "--title", "Src", "--body", "x"
            ).exit_code
            == 0
        )
        spec = inproc["tmp"] / "links.yaml"
        spec.write_text(
            "- {source: src, target: t1, relation: informs}\n"
            "- {source: ghost, target: t1, relation: informs}\n"
        )
        out = _invoke("links", "bulk-create", str(spec), "-k", "test-kb")
        assert "1 failed" in out.output, out.output
        assert out.exit_code == PARTIAL, out.output


class TestInitIndexing:
    def test_kb_created_but_not_indexed_exits_partial(self, tmp_path, monkeypatch):
        from pyrite.storage.index import IndexManager

        monkeypatch.setenv("PYRITE_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setattr(
            IndexManager, "index_kb", lambda self, name: (_ for _ in ()).throw(OSError("disk"))
        )
        out = _invoke(
            "init",
            "-t",
            "research",
            "-p",
            str(tmp_path / "newkb"),
            "-n",
            "newkb",
            "--format",
            "rich",
        )
        assert (tmp_path / "newkb").exists()
        assert "index" in out.output.lower()
        assert out.exit_code == PARTIAL, out.output


class TestExtensionVerify:
    def test_installed_but_a_plugin_entry_point_raises_exits_partial(self, tmp_path, monkeypatch):
        """`discover()` only logs a plugin that fails to load; verification has to ask for strict."""
        import importlib.metadata as md
        import subprocess as sp
        from types import SimpleNamespace

        import pyrite.plugins as plugins
        from pyrite.plugins.registry import PluginRegistry

        ext = tmp_path / "ext"
        ext.mkdir()
        (ext / "pyproject.toml").write_text('[project]\nname = "ext-x"\nversion = "0"\n')
        monkeypatch.setattr(
            sp, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")
        )

        def load():
            raise RuntimeError("entry point raises")

        broken = SimpleNamespace(name="ext-x", load=load)
        monkeypatch.setattr(
            md,
            "entry_points",
            lambda *a, **k: SimpleNamespace(select=lambda group: [broken]),
        )
        registry = PluginRegistry()
        monkeypatch.setattr(plugins, "get_registry", lambda: registry)
        out = _invoke("extension", "install", str(ext), "--verify")
        assert "verification failed" in out.output.lower(), out.output
        assert out.exit_code == PARTIAL, out.output

    @pytest.mark.control(reason="control: a plugin that loads verifies, exit 0")
    def test_a_plugin_that_loads_verifies(self, tmp_path, monkeypatch):
        import importlib.metadata as md
        import subprocess as sp
        from types import SimpleNamespace

        import pyrite.plugins as plugins
        from pyrite.plugins.registry import PluginRegistry

        ext = tmp_path / "ext"
        ext.mkdir()
        (ext / "pyproject.toml").write_text('[project]\nname = "ext-x"\nversion = "0"\n')
        monkeypatch.setattr(
            sp, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr="")
        )
        monkeypatch.setattr(
            md, "entry_points", lambda *a, **k: SimpleNamespace(select=lambda group: [])
        )
        registry = PluginRegistry()
        monkeypatch.setattr(plugins, "get_registry", lambda: registry)
        out = _invoke("extension", "install", str(ext), "--verify")
        assert out.exit_code == 0, out.output


class TestSearchFilesScanned:
    def test_unreadable_file_and_no_match_is_partial_not_refused(self, cli):
        """Files scanned are the work done: a query that matches nothing, with one
        file unreadable, ran over the others."""
        _create(cli, "Findable", "--body", "something else")
        _unreadable(cli.kb / "broken.md", "---\nid: broken\ntitle: x\n---\nneedle\n")
        out = cli.pyrite("search", "needle", "-k", "demo", "--files")
        assert out.returncode == PARTIAL, out.stdout + out.stderr


class TestSearchFiles:
    def test_a_file_that_could_not_be_read_exits_partial(self, cli):
        _create(cli, "Findable", "--body", "needle in a haystack")
        _unreadable(cli.kb / "broken.md", "---\nid: broken\ntitle: x\n---\nneedle\n")
        out = cli.pyrite("search", "needle", "-k", "demo", "--files")
        text = out.stdout + out.stderr
        assert "findable" in text.lower()
        assert out.returncode == PARTIAL, text


# --------------------------------------------------------------------------
# Failures that come back inside a result, not as an exception
# --------------------------------------------------------------------------


class TestBacklinksOfALinkedToMissingPage:
    @pytest.mark.control(
        reason="dev already answers a red link; guards the regression round 0 of this PR introduced"
    )
    def test_a_page_that_does_not_exist_but_is_linked_to_is_answered(self, cli):
        """`[[ghost-page]]` is a red link: stored, and "what links here" has an answer."""
        (cli.kb / "notes").mkdir(exist_ok=True)
        (cli.kb / "notes" / "linker.md").write_text(
            "---\nid: linker\ntitle: Linker\ntype: note\nlinks:\n"
            "  - target: ghost-page\n    relation: related_to\n---\nSee [[ghost-page]].\n"
        )
        cli.pyrite("index", "sync", "--no-embed")
        for entry in ("pyrite.cli", "pyrite.read_cli"):
            out = cli.run(entry, "backlinks", "ghost-page", "-k", "demo", "--format", "json")
            assert out.returncode == 0, out.stdout + out.stderr
            payload = json.loads(out.stdout)
            assert payload["total"] == 1, payload
            assert payload["entries"][0]["id"] == "linker"


class TestBatchRead:
    def test_an_id_that_is_not_there_is_partial(self, cli):
        _create(cli, "Here")
        out = cli.pyrite("batch-read", "demo:here", "demo:nope")
        assert out.returncode == PARTIAL, out.stdout + out.stderr
        assert "nope" in out.stdout

    def test_none_found_is_refused(self, cli):
        out = cli.pyrite("batch-read", "demo:nope")
        assert out.returncode == REFUSED, out.stdout + out.stderr


class TestKbCommit:
    def _git(self, cli):
        for args in (
            ["init", "-q"],
            ["config", "user.email", "t@example.invalid"],
            ["config", "user.name", "T"],
        ):
            subprocess.run(["git", *args], cwd=cli.kb, check=True, capture_output=True)

    def test_a_hook_that_rejects_the_commit_is_a_failure(self, cli):
        self._git(cli)
        hook = cli.kb / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho rejected >&2\nexit 1\n")
        hook.chmod(0o755)
        _create(cli, "Something to commit")
        out = cli.pyrite("kb", "commit", "-k", "demo", "-m", "x")
        assert out.returncode == REFUSED, out.stdout + out.stderr

    @pytest.mark.control(reason="control: a commit that succeeds still exits 0")
    def test_a_commit_that_succeeds_exits_zero(self, cli):
        self._git(cli)
        _create(cli, "Something to commit")
        out = cli.pyrite("kb", "commit", "-k", "demo", "-m", "x")
        assert out.returncode == 0, out.stdout + out.stderr

    @pytest.mark.control(reason="control: nothing to commit is the asked-for end state")
    def test_nothing_to_commit_exits_zero(self, cli):
        self._git(cli)
        cli.pyrite("kb", "commit", "-k", "demo", "-m", "first")
        out = cli.pyrite("kb", "commit", "-k", "demo", "-m", "again")
        assert out.returncode == 0, out.stdout + out.stderr


class TestTasks:
    def test_a_lost_claim_exits_one_in_json_as_in_rich(self, cli):
        cli.pyrite("task", "create", "T1", "-k", "demo")
        first = cli.pyrite("task", "claim", "t1", "-k", "demo", "-a", "me", "--format", "json")
        assert first.returncode == 0, first.stdout + first.stderr
        for fmt in ("json", "rich"):
            again = cli.pyrite("task", "claim", "t1", "-k", "demo", "-a", "you", "--format", fmt)
            assert again.returncode == REFUSED, (fmt, again.stdout + again.stderr)
        assert (
            json.loads(
                cli.pyrite(
                    "task", "claim", "t1", "-k", "demo", "-a", "you", "--format", "json"
                ).stdout
            )["claimed"]
            is False
        )

    def test_children_that_failed_to_create_are_partial(self, cli):
        cli.pyrite("task", "create", "Parent", "-k", "demo")
        # The second child has the first one's title, hence its id: refused.
        out = cli.pyrite(
            "task",
            "decompose",
            "parent",
            "-k",
            "demo",
            "-c",
            "Same",
            "-c",
            "Same",
            "--format",
            "json",
        )
        children = json.loads(out.stdout)["children"]
        assert [c.get("created") for c in children] == [True, False], children
        assert out.returncode == PARTIAL, out.stdout + out.stderr


class TestRepoSync:
    def test_one_repo_failing_is_partial(self, inproc, monkeypatch):
        from pyrite.services.repo_service import RepoService

        monkeypatch.setattr(
            RepoService,
            "sync",
            lambda self, repo_name=None: {
                "success": True,
                "repos": {
                    "a": {"success": True, "message": "ok"},
                    "b": {"success": False, "error": "pull failed"},
                },
            },
        )
        out = _invoke("repo", "sync")
        assert "pull failed" in out.output
        assert out.exit_code == PARTIAL, out.output

    def test_every_repo_failing_is_refused(self, inproc, monkeypatch):
        from pyrite.services.repo_service import RepoService

        monkeypatch.setattr(
            RepoService,
            "sync",
            lambda self, repo_name=None: {
                "success": True,
                "repos": {"b": {"success": False, "error": "pull failed"}},
            },
        )
        assert _invoke("repo", "sync").exit_code == REFUSED


class TestLinkBidi:
    def test_forward_written_inverse_failed_is_partial(self, inproc, monkeypatch):
        from pyrite.services.kb_service import KBService

        for title in ("A", "B"):
            _invoke("create", "-k", "test-kb", "--type", "note", "--title", title, "--body", "x")
        real = KBService.add_link
        calls = []

        def flaky(self, *args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise ValueError("inverse refused")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(KBService, "add_link", flaky)
        out = _invoke("link", "a", "b", "-k", "test-kb", "-r", "informs", "--bidi")
        assert "inverse" in out.output.lower(), out.output
        assert out.exit_code == PARTIAL, out.output


class TestJournalismAndSoftwareResults:
    def test_network_of_a_missing_entry_is_an_error_in_every_format(self, cli):
        for extra in ([], ["--json"]):
            out = cli.pyrite("investigation", "network", "nope", "-k", "demo", *extra)
            assert out.returncode == REFUSED, (extra, out.stdout + out.stderr)

    def test_evidence_chain_of_a_missing_claim_is_an_error(self, cli):
        out = cli.pyrite("investigation", "evidence-chain", "nope", "-k", "demo")
        assert out.returncode == REFUSED, out.stdout + out.stderr

    def test_bulk_edges_where_every_edge_failed_is_refused(self, cli):
        edges = cli.root / "edges.json"
        edges.write_text(json.dumps([{"source": "a", "target": "b", "type": "funding"}]))
        for extra in ([], ["--json"], ["--dry-run", "--json"]):
            out = cli.pyrite("investigation", "bulk-edges", "-f", str(edges), "-k", "demo", *extra)
            assert out.returncode == REFUSED, (extra, out.stdout + out.stderr)

    def test_bulk_edges_and_ftm_import_with_some_failed_are_partial(self, inproc, monkeypatch):
        import pyrite_journalism_investigation.bulk as bulk
        import pyrite_journalism_investigation.ftm as ftm

        monkeypatch.setattr(
            bulk,
            "create_edge_batch",
            lambda db, kb, edges, dry_run=False: {
                "created": 1,
                "skipped": 0,
                "errors": 1,
                "entries": [],
            },
        )
        monkeypatch.setattr(
            ftm,
            "import_ftm",
            lambda db, kb, entities, dry_run=False: {
                "imported": 1,
                "skipped": 0,
                "unmapped": 0,
                "errors": 1,
                "unmapped_schemas": [],
                "entries": [],
            },
        )
        edges = inproc["tmp"] / "e.json"
        edges.write_text("[]")
        for extra in ([], ["--json"]):
            out = _invoke("investigation", "bulk-edges", "-f", str(edges), "-k", "test-kb", *extra)
            assert out.exit_code == PARTIAL, (extra, out.output)
            out = _invoke("investigation", "ftm-import", "-f", str(edges), "-k", "test-kb", *extra)
            assert out.exit_code == PARTIAL, (extra, out.output)

    def test_prioritize_with_an_unknown_item_is_partial(self, cli):
        _create(cli, "A")
        out = cli.pyrite("sw", "prioritize", "a", "ghost", "-k", "demo")
        assert "ghost" in out.stdout + out.stderr
        assert out.returncode == PARTIAL, out.stdout + out.stderr


class TestImportDryRun:
    """Dry runs: a dry run that would refuse exits as the real run would."""

    def test_import_dry_run_that_would_refuse_exits_as_the_real_run(self, inproc):
        path = inproc["tmp"] / "d.json"
        path.write_text(
            json.dumps(
                {
                    "entries": [
                        {"title": "Good", "entry_type": "note", "body": "ok"},
                        {"title": "Bad", "entry_type": "note", "body": "x", "body_truncated": True},
                    ]
                }
            )
        )
        dry = _invoke("import", str(path), "--kb", "test-kb", "--dry-run")
        assert dry.exit_code == PARTIAL, dry.output
        assert not list(inproc["kb"].rglob("good.md"))  # and nothing was written
        assert _invoke("import", str(path), "--kb", "test-kb").exit_code == PARTIAL

    @pytest.mark.control(reason="control: a dry run that would not refuse exits 0")
    def test_clean_import_dry_run_exits_zero(self, inproc):
        path = inproc["tmp"] / "d.json"
        path.write_text(
            json.dumps({"entries": [{"title": "Good", "entry_type": "note", "body": "ok"}]})
        )
        assert _invoke("import", str(path), "--kb", "test-kb", "--dry-run").exit_code == 0


# --------------------------------------------------------------------------
# Admin commands that used to print success whatever the service answered
# --------------------------------------------------------------------------


class TestAdminRepo:
    def test_sync_of_a_repo_that_is_not_there_exits_one(self, cli):
        out = cli.run("pyrite.admin_cli", "repo", "sync", "nope")
        assert out.returncode == REFUSED, out.stdout + out.stderr
        assert "Synced" not in out.stdout

    def test_unsubscribe_of_a_repo_that_is_not_there_exits_one(self, cli):
        out = cli.run("pyrite.admin_cli", "repo", "unsubscribe", "nope")
        assert out.returncode == REFUSED, out.stdout + out.stderr
        assert "Unsubscribed" not in out.stdout

    def test_an_unhealthy_index_exits_one(self, cli):
        (cli.kb / "notes").mkdir(exist_ok=True)
        (cli.kb / "notes" / "unindexed.md").write_text(
            "---\nid: unindexed\ntitle: U\ntype: note\n---\nbody\n"
        )
        out = cli.run("pyrite.admin_cli", "index", "health")
        assert out.returncode == REFUSED, out.stdout + out.stderr


# --------------------------------------------------------------------------
# The registry: every command is classified
# --------------------------------------------------------------------------

# Every command Typer registers, in the three CLIs, is one of:
#   GUARDED   it can do part of its job (several effects, or a result that carries
#             a failure); the named test class runs it through its own entry point
#   SAFE      it has one effect and a failure of it exits non-zero (the reason)
#   READ_ONLY it asks for no effect
# A new command is in none of them and fails test_every_registered_command_is_classified.

GUARDED = {
    "pyrite backlinks": "TestBacklinks",
    "pyrite batch-read": "TestBatchRead",
    "pyrite create": "TestCreateWithFailedLink",
    "pyrite export collection": "TestExportCollection",
    "pyrite extension install": "TestExtensionVerify",
    "pyrite import": "TestImportAndBulkLinks",
    "pyrite index embed": "TestEmbed",
    "pyrite index reconcile": "TestReconcile",
    "pyrite init": "TestInitIndexing",
    "pyrite investigation bulk-edges": "TestJournalismAndSoftwareResults",
    "pyrite investigation evidence-chain": "TestJournalismAndSoftwareResults",
    "pyrite investigation ftm-import": "TestJournalismAndSoftwareResults",
    "pyrite investigation network": "TestJournalismAndSoftwareResults",
    "pyrite kb commit": "TestKbCommit",
    "pyrite link": "TestLinkBidi",
    "pyrite links bulk-create": "TestImportAndBulkLinks",
    "pyrite repo sync": "TestRepoSync",
    "pyrite schema migrate": "TestSchemaMigrate",
    "pyrite search": "TestSearchFiles",
    "pyrite social reputation": "TestJournalismAndSoftwareResults",
    "pyrite sw migrate-standards": "TestJournalismAndSoftwareResults",
    "pyrite sw prioritize": "TestJournalismAndSoftwareResults",
    "pyrite task claim": "TestTasks",
    "pyrite task decompose": "TestTasks",
    "pyrite-admin index embed": "TestEmbed",
    "pyrite-admin index health": "TestAdminRepo",
    "pyrite-admin repo sync": "TestAdminRepo",
    "pyrite-admin repo unsubscribe": "TestAdminRepo",
    "pyrite-read backlinks": "TestBacklinks",
    "pyrite-read search": "TestSearchFiles",
}

SAFE = {
    "pyrite add": "one file, validated by the write pipeline; a refusal exits 1",
    "pyrite auth github-login": "one credential store; failure raises",
    "pyrite auth github-logout": "one credential store; failure raises",
    "pyrite auth github-setup": "one credential store; failure raises",
    "pyrite cascade backfill-ji": "single backfill pass; scans clean",
    "pyrite ci": "exits non-zero on the checks' own result",
    "pyrite db backup": "one file copy; failure raises",
    "pyrite db restore": "one file copy; failure raises",
    "pyrite delete": "one entry; a missing id exits 1",
    "pyrite export site": "one build; a failure raises",
    "pyrite extension init": "scaffold; failure raises",
    "pyrite extension uninstall": "one pip call; failure exits 1 (cli_error)",
    "pyrite ids missing": "exits 3 when files are left (ids_commands.OUTSIDE_CONTRACT)",
    "pyrite ids pin": "exits 3 when files are left (ids_commands.OUTSIDE_CONTRACT)",
    "pyrite index build": "embedding is owed, not failed (ADR-0035); indexing errors raise",
    "pyrite index health": "exits 1 when unhealthy",
    "pyrite index sync": "malformed files are reported, not a failed sync (groom, principle 2); "
    "embedding is owed (ADR-0035)",
    "pyrite investigation dedup": "--link writes same_as links; no per-item failure is reported or "
    "swallowed",
    "pyrite investigation export": "one file",
    "pyrite investigation ftm-export": "one file",
    "pyrite investigation promote-claim": "one entry",
    "pyrite investigation qa": "report of findings",
    "pyrite investigation start": "scaffold",
    "pyrite kb add": "one registration; failure exits 1",
    "pyrite kb create": "one KB; failure exits 1",
    "pyrite kb discover": "an entry already registered is not a failure",
    "pyrite kb gc": "removes what expired; each removal is reported",
    "pyrite kb push": "one git push; failure exits 1 (cli_error)",
    "pyrite kb reindex": "one reindex; failure raises",
    "pyrite kb remove": "one registration",
    "pyrite kb schema add-type": "one schema write",
    "pyrite kb schema remove-type": "one schema write",
    "pyrite kb schema set": "one schema write",
    "pyrite kb validate": "exits 1 on structural errors, 2 on drift",
    "pyrite mcp": "long-running server",
    "pyrite mcp-setup": "one report line per client; exits 1 unless every client is ok "
    "(mcp_setup_command.py)",
    "pyrite protocol check": "exits 1 when a check fails",
    "pyrite qa compact": "one rewrite",
    "pyrite qa fix": "manual items are findings reported in the output, as qa validate does",
    "pyrite qa validate": "exits non-zero by threshold",
    "pyrite readme": "one file",
    "pyrite rename": "one rename; the plan is computed first and a failure raises",
    "pyrite repo add": "one registration",
    "pyrite repo fork": "one fork; failure exits 1",
    "pyrite repo remove": "one registration",
    "pyrite repo subscribe": "one clone; failure exits 1",
    "pyrite repo unsubscribe": "result checked, exits 1 on failure",
    "pyrite serve": "long-running server",
    "pyrite social vote": "one write; failure exits 1",
    "pyrite sw claim": "one transition; failure exits 1",
    "pyrite sw epic": "one entry",
    "pyrite sw new-adr": "one entry",
    "pyrite sw pull-next": "one claim; failure exits 1",
    "pyrite sw refine": "one transition; failure exits 1",
    "pyrite sw review": "one transition; failure exits 1",
    "pyrite sw submit": "one transition; failure exits 1",
    "pyrite sw transition": "one transition; failure exits 1",
    "pyrite task checkpoint": "one write",
    "pyrite task create": "one entry",
    "pyrite task migrate-relaxed-mode": "idempotent backfill; 'skipped' means not applicable, not "
    "failed",
    "pyrite task reset": "one transition",
    "pyrite task update": "one entry",
    "pyrite update": "one entry; a refusal exits 1",
    "pyrite wiki review": "one entry",
    "pyrite zettel new": "one entry",
    "pyrite-admin auth login": "one credential store; failure raises",
    "pyrite-admin auth logout": "one credential store; failure raises",
    "pyrite-admin config set": "one config write",
    "pyrite-admin index build": "as pyrite index build",
    "pyrite-admin index sync": "as pyrite index sync",
    "pyrite-admin kb add": "one registration",
    "pyrite-admin kb discover": "as pyrite kb discover",
    "pyrite-admin kb remove": "one registration",
    "pyrite-admin kb validate": "exits 1 on errors",
    "pyrite-admin mcp": "long-running server",
    "pyrite-admin mcp-setup": "as pyrite mcp-setup",
    "pyrite-admin repo add": "one registration",
    "pyrite-admin repo fork": "one fork",
    "pyrite-admin repo remove": "one registration",
    "pyrite-admin repo subscribe": "one clone",
    "pyrite-admin schema": "one write",
    "pyrite-admin user create": "one user",
}

READ_ONLY = [
    "pyrite auth status",
    "pyrite auth whoami",
    "pyrite cascade audit-compat",
    "pyrite cascade export",
    "pyrite cascade extract-actors",
    "pyrite cascade suggest-aliases",
    "pyrite collections list",
    "pyrite collections query",
    "pyrite config",
    "pyrite extension list",
    "pyrite get",
    "pyrite index jobs",
    "pyrite index stats",
    "pyrite investigation claims",
    "pyrite investigation entities",
    "pyrite investigation money-flow",
    "pyrite investigation ownership",
    "pyrite investigation search",
    "pyrite investigation sources",
    "pyrite investigation status",
    "pyrite investigation timeline",
    "pyrite kb health",
    "pyrite kb list",
    "pyrite kb schema show",
    "pyrite links asymmetric",
    "pyrite links batch-suggest",
    "pyrite links check",
    "pyrite links discover",
    "pyrite links orphans",
    "pyrite links suggest",
    "pyrite list",
    "pyrite list-entries",
    "pyrite orient",
    "pyrite protocol list",
    "pyrite qa assess",
    "pyrite qa check-urls",
    "pyrite qa checkers",
    "pyrite qa coverage",
    "pyrite qa gaps",
    "pyrite qa stale",
    "pyrite qa status",
    "pyrite recent",
    "pyrite repo list",
    "pyrite repo status",
    "pyrite schema diff",
    "pyrite schema validate",
    "pyrite social newest",
    "pyrite social top",
    "pyrite sw adrs",
    "pyrite sw backlog",
    "pyrite sw board",
    "pyrite sw check-ready",
    "pyrite sw components",
    "pyrite sw context-for-item",
    "pyrite sw conventions",
    "pyrite sw epics",
    "pyrite sw log",
    "pyrite sw milestones",
    "pyrite sw review-queue",
    "pyrite sw standards",
    "pyrite sw validations",
    "pyrite tags",
    "pyrite task get",
    "pyrite task list",
    "pyrite task status",
    "pyrite timeline",
    "pyrite wiki quality",
    "pyrite wiki stats",
    "pyrite wiki stubs",
    "pyrite zettel inbox",
    "pyrite zettel maturity",
    "pyrite zettel orphans",
    "pyrite-admin auth whoami",
    "pyrite-admin config show",
    "pyrite-admin index stats",
    "pyrite-admin kb list",
    "pyrite-admin repo list",
    "pyrite-admin repo status",
    "pyrite-read config",
    "pyrite-read get",
    "pyrite-read list",
    "pyrite-read tags",
    "pyrite-read timeline",
]


def _walk(cmd, path):
    # Typer vendors its own click: test for the group interface, not click.Group.
    if hasattr(cmd, "list_commands"):
        ctx = cmd.make_context(path[-1], [], resilient_parsing=True)
        for name in cmd.list_commands(ctx):
            yield from _walk(cmd.get_command(ctx, name), [*path, name])
    else:
        yield " ".join(path), cmd


def _commands() -> dict[str, object]:
    from pyrite import admin_cli, read_cli
    from pyrite.cli import app

    found: dict[str, object] = {}
    for prog, a in (
        ("pyrite", app),
        ("pyrite-read", read_cli.app),
        ("pyrite-admin", admin_cli.app),
    ):
        found.update(_walk(typer.main.get_command(a), [prog]))
    return found


def _registered() -> set[str]:
    return set(_commands())


def _location(cmd) -> tuple[str, str] | None:
    import inspect

    func = inspect.unwrap(cmd.callback)
    try:
        rel = Path(inspect.getsourcefile(func)).resolve().relative_to(REPO)
    except ValueError:
        return None
    return str(rel), func.__name__


def classification(name: str) -> str | None:
    for kind, table in (("guarded", GUARDED), ("safe", SAFE)):
        if name in table:
            return kind
    return "read-only" if name in READ_ONLY else None


@pytest.mark.control(reason="structural inventory: guards a future command, not this fix")
@pytest.mark.parametrize("command", sorted(_registered()))
def test_every_registered_command_is_classified(command):
    """The registry is the list. A command in none of GUARDED, SAFE and READ_ONLY,
    or in two, fails here, whatever its code looks like."""
    kinds = [k for k, t in (("g", GUARDED), ("s", SAFE), ("r", READ_ONLY)) if command in t]
    assert len(kinds) == 1, (
        f"{command!r} must be classified exactly once (GUARDED, SAFE or READ_ONLY); got {kinds}"
    )
    if command in GUARDED:
        assert GUARDED[command] in globals(), f"{command}: no test class {GUARDED[command]}"


@pytest.mark.control(reason="structural inventory: guards a rename, not this fix")
def test_nothing_is_classified_that_is_not_registered():
    registered = _registered()
    stale = (set(GUARDED) | set(SAFE) | set(READ_ONLY)) - registered
    assert not stale, f"classified but no such command: {sorted(stale)}"


@pytest.mark.control(reason="structural inventory: guards a future handler, not this fix")
def test_a_command_whose_function_swallows_a_failure_is_guarded():
    """Ties the two scans below to the registry: a command whose function holds a
    handler or a printed failure judged "fixed" must be GUARDED, and a SAFE or
    READ_ONLY command must not be in a function the scans flag unjudged."""
    commands = _commands()
    judged = {**HANDLERS, **PRINTS}
    for name, cmd in commands.items():
        if cmd.callback is None:
            continue
        loc = _location(cmd)
        if loc is None:
            continue
        verdict = judged.get(loc)
        if verdict is not None and verdict[0] == "fixed":
            assert name in GUARDED, f"{name} ({loc}) has a fixed failure path but no guard"


# --------------------------------------------------------------------------
# The scans: what the registry cannot see
# --------------------------------------------------------------------------

SCANNED = [
    *sorted((REPO / "pyrite" / "cli").glob("*.py")),
    REPO / "pyrite" / "read_cli.py",
    REPO / "pyrite" / "admin_cli.py",
    *sorted((REPO / "extensions").glob("*/src/*/cli*.py")),
]

# A call that ends the command. Named exactly: `logger.error(...)` does not.
EXIT_CALLS = {
    "Exit",
    "exit",
    "cli_error",
    "cli_error_from",
    "_cli_error",
    "_cli_err",
    "_refusal_exit",
    "_task_error",
    "_kb_error",
    "exit_unless_whole",
    "stop",
    "settle",
}

# (file relative to the repo, function) -> (verdict, reason or guard). Verdicts:
#   "fixed"  the command now exits PARTIAL/REFUSED; the guard is a class in this file
#   "safe"   a fallback, a probe, or a report whose findings are the answer
#   "left"   a read that degrades; named in the PR, not changed here
# HANDLERS: `except` blocks that neither raise nor call an exit.
# PRINTS: a printed failure (red text, "failed", "Error") with no exit after it in its block,
#         which is how a failure inside a result dict hides from the first scan.
HANDLERS: dict[tuple[str, str], tuple[str, str]] = {
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_quality"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stats"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/encyclopedia/src/pyrite_encyclopedia/cli.py", "wiki_stubs"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_newest"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/social/src/pyrite_social/cli.py", "social_top"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "_query_entries"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "_resolve_adr_kb"): (
        "safe",
        "tries the next KB",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_milestones"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_inbox"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("extensions/zettelkasten/src/pyrite_zettelkasten/cli.py", "zettel_maturity"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("pyrite/cli/__init__.py", "<module>"): ("safe", "plugin CLI loading: not a command's effect"),
    ("pyrite/cli/collection_commands.py", "list_collections"): (
        "safe",
        "bad metadata reads as empty",
    ),
    ("pyrite/cli/context.py", "get_config_with_registered_kbs"): (
        "safe",
        "documented fallback to YAML config",
    ),
    ("pyrite/cli/context.py", "open_index_for_validation"): (
        "safe",
        "documented fallback to YAML config",
    ),
    ("pyrite/cli/entry_commands.py", "_parse_field_value"): ("safe", "tries the next parser"),
    ("pyrite/cli/entry_commands.py", "create_entry"): ("fixed", "TestCreateWithFailedLink"),
    ("pyrite/cli/export_commands.py", "_load_entry_from_result"): ("fixed", "TestExportCollection"),
    ("pyrite/cli/export_commands.py", "export_collection"): ("fixed", "TestExportCollection"),
    ("pyrite/cli/extension_commands.py", "extension_install"): ("fixed", "TestExtensionVerify"),
    ("pyrite/cli/extension_commands.py", "extension_list"): (
        "left",
        "a plugin that cannot be inspected is listed with empty fields; read-only",
    ),
    ("pyrite/cli/index_commands.py", "index_build"): (
        "safe",
        "embedding is owed, not failed (ADR-0035)",
    ),
    ("pyrite/cli/index_commands.py", "index_reconcile"): ("safe", "path display fallback"),
    ("pyrite/cli/index_commands.py", "index_sync"): (
        "safe",
        "embedding is owed, not failed (ADR-0035)",
    ),
    ("pyrite/cli/init_command.py", "init_kb"): ("fixed", "TestInitIndexing"),
    ("pyrite/cli/kb_commands.py", "kb_discover"): ("safe", "already registered is not a failure"),
    ("pyrite/cli/mcp_setup_command.py", "_add_reproduces"): ("safe", "a probe"),
    ("pyrite/cli/mcp_setup_command.py", "_check_replaceable"): (
        "safe",
        "absent file: nothing to replace",
    ),
    ("pyrite/cli/mcp_setup_command.py", "_held"): (
        "safe",
        "parse fallback; the client is reported held",
    ),
    ("pyrite/cli/mcp_setup_command.py", "_load"): ("safe", "an absent config file reads as empty"),
    ("pyrite/cli/mcp_setup_command.py", "_read_claude_config"): (
        "safe",
        "an absent file reads as empty",
    ),
    ("pyrite/cli/mcp_setup_command.py", "_setup_file"): ("safe", "absent file: created"),
    ("pyrite/cli/mcp_setup_command.py", "_verify_starts"): (
        "safe",
        "the failure becomes the client's reported detail",
    ),
    ("pyrite/cli/mcp_setup_command.py", "holds"): ("safe", "a probe"),
    ("pyrite/cli/mcp_setup_command.py", "mcp_setup"): (
        "safe",
        "one report line per client; exits 1 unless every client is ok",
    ),
    ("pyrite/cli/mcp_setup_command.py", "run"): (
        "safe",
        "returns (ok, detail) to a caller that records it",
    ),
    ("pyrite/cli/protocol_commands.py", "protocol_check"): (
        "safe",
        "a class that cannot be inspected is skipped; failures counted exit 1",
    ),
    ("pyrite/cli/protocol_commands.py", "protocol_list"): ("safe", "registry fallback"),
    ("pyrite/cli/schema_commands.py", "_get_git_changed_md_files"): (
        "safe",
        "no git means no changed-file filter",
    ),
    ("pyrite/cli/schema_commands.py", "_parse_frontmatter"): (
        "safe",
        "appends to the errors list the command exits on",
    ),
    ("pyrite/cli/schema_commands.py", "schema_migrate"): ("fixed", "TestSchemaMigrate"),
    ("pyrite/cli/schema_commands.py", "schema_validate"): (
        "safe",
        "path outside the KB is skipped",
    ),
    ("pyrite/cli/search_commands.py", "_search_files"): ("fixed", "TestSearchFiles"),
    ("pyrite/cli/search_commands.py", "_warn_if_stale"): ("safe", "a probe for a warning"),
    ("pyrite/cli/task_commands.py", "_task_get_impl"): ("safe", "bad metadata reads as empty"),
}

PRINTS: dict[tuple[str, str], tuple[str, str]] = {
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "evidence_chain",
    ): ("fixed", "TestJournalismAndSoftwareResults"),
    (
        "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py",
        "money_flow",
    ): ("safe", "'red' is colour: a report"),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_check_ready"): (
        "safe",
        "a readiness report; failed criteria are the answer",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_context_for_item"): (
        "safe",
        "a report",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_migrate_standards"): (
        "fixed",
        "TestJournalismAndSoftwareResults",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_prioritize"): (
        "fixed",
        "TestJournalismAndSoftwareResults",
    ),
    ("extensions/software-kb/src/pyrite_software_kb/cli.py", "sw_refine_cmd"): (
        "safe",
        "a report; failed criteria are the answer",
    ),
    ("pyrite/admin_cli.py", "index_embed"): ("fixed", "TestEmbed"),
    ("pyrite/admin_cli.py", "kb_validate"): ("safe", "exits 1 after listing the errors"),
    ("pyrite/admin_cli.py", "repo_sync"): ("fixed", "TestAdminRepo"),
    ("pyrite/cli/__init__.py", "import_entries"): ("fixed", "TestImportAndBulkLinks"),
    ("pyrite/cli/entry_commands.py", "create_entry"): ("fixed", "TestCreateWithFailedLink"),
    ("pyrite/cli/extension_commands.py", "extension_install"): ("fixed", "TestExtensionVerify"),
    ("pyrite/cli/index_commands.py", "_report_health"): (
        "safe",
        "index health exits 1 when unhealthy, in its caller",
    ),
    ("pyrite/cli/kb_commands.py", "kb_gc"): ("safe", "'red' is colour: each removal is reported"),
    ("pyrite/cli/kb_commands.py", "kb_validate"): (
        "safe",
        "exits 1 on structural errors, 2 on drift",
    ),
    ("pyrite/cli/link_commands.py", "links_bulk_create"): ("fixed", "TestImportAndBulkLinks"),
    ("pyrite/cli/protocol_commands.py", "protocol_check"): ("safe", "exits 1 when a check fails"),
    ("pyrite/cli/qa_commands.py", "qa_check_urls"): (
        "safe",
        "broken URLs are the report the command exists to give, as qa validate's findings are",
    ),
    ("pyrite/cli/qa_commands.py", "qa_coverage"): ("safe", "'red' is colour: a report"),
    ("pyrite/cli/repo_commands.py", "repo_sync"): ("fixed", "TestRepoSync"),
    ("pyrite/cli/schema_commands.py", "schema_migrate"): ("fixed", "TestSchemaMigrate"),
    ("pyrite/cli/search_commands.py", "search"): ("fixed", "TestSearchFiles"),
    ("pyrite/cli/task_commands.py", "task_decompose"): ("fixed", "TestTasks"),
    ("pyrite/read_cli.py", "_emit_error"): (
        "safe",
        "the helper that prints the error; its callers raise Exit(1)",
    ),
}


def _name(call: ast.Call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _exits(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Raise) or (isinstance(n, ast.Call) and _name(n) in EXIT_CALLS)
        for n in ast.walk(node)
    )


def _func_of(node: ast.AST, parents: dict) -> str:
    while node in parents and not isinstance(node, ast.FunctionDef):
        node = parents[node]
    return node.name if isinstance(node, ast.FunctionDef) else "<module>"


def _scan() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    handlers: set[tuple[str, str]] = set()
    prints: set[tuple[str, str]] = set()
    for path in SCANNED:
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text())
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and not _exits(node):
                handlers.add((rel, _func_of(node, parents)))
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                for i, stmt in enumerate(block):
                    if not (
                        isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Call)
                        and _name(stmt.value) == "print"
                    ):
                        continue
                    text = " ".join(
                        c.value
                        for a in stmt.value.args
                        for c in ast.walk(a)
                        if isinstance(c, ast.Constant) and isinstance(c.value, str)
                    )
                    failure = "[red]" in text or "failed" in text.lower() or "Error" in text
                    if failure and not any(_exits(s) for s in block[i + 1 :]):
                        prints.add((rel, _func_of(stmt, parents)))
    return handlers, prints


def test_every_swallowing_handler_is_judged():
    """A handler that prints or records a failure and carries on is where "exit 0
    after a failure" comes from. Each one has a verdict in HANDLERS; a new one
    fails here until it gets one."""
    found, _ = _scan()
    assert found - set(HANDLERS) == set(), (
        "new except handlers that neither exit nor raise: judge them in HANDLERS "
        "(fix the exit status, or say why the failure is not a lost effect)"
    )
    assert set(HANDLERS) - found == set(), "HANDLERS lists handlers that no longer exist"


def test_every_printed_failure_without_an_exit_is_judged():
    """The same, for a failure that arrives in a result dict or object and is printed,
    never raised: the shape an `except` scan cannot see (task claim, repo sync,
    kb commit, ...)."""
    _, found = _scan()
    assert found - set(PRINTS) == set(), (
        "a failure is printed and the command carries on: judge it in PRINTS "
        "(exit non-zero when an asked-for effect did not happen)"
    )
    assert set(PRINTS) - found == set(), "PRINTS lists printed failures that no longer exist"


@pytest.mark.control(reason="structural inventory: guards the table, not this fix")
def test_every_fixed_verdict_names_a_guard_that_exists():
    for key, (verdict, why) in {**HANDLERS, **PRINTS}.items():
        if verdict == "fixed":
            assert why in globals(), f"{key}: guard {why} is not a test class in this file"


class TestExportCollection:
    def test_a_file_in_the_folder_that_could_not_be_loaded_exits_partial(self, cli):
        folder = cli.kb / "stuff"
        folder.mkdir()
        (folder / "ok.md").write_text("---\nid: ok\ntitle: Ok\ntype: note\n---\nbody\n")
        _unreadable(folder / "bad.md", "---\nid: bad\ntitle: Bad\ntype: note\n---\nbody\n")
        (cli.kb / "stuff.md").write_text(
            "---\nid: stuff\ntitle: Stuff\ntype: collection\nsource_type: folder\n"
            "folder_path: stuff\n---\n"
        )
        cli.pyrite("index", "sync", "--no-embed")
        out = cli.pyrite("export", "collection", "stuff", "-k", "demo", "-o", str(cli.root / "out"))
        text = out.stdout + out.stderr
        assert "Exported 1 entries" in text, text
        assert out.returncode == PARTIAL, text
