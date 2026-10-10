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


def _result(stdout: str) -> dict:
    """The JSON object in the output, after any warnings printed before it."""
    return json.JSONDecoder().raw_decode(stdout[stdout.index("{") :])[0]


class _Venv:
    """A throwaway interpreter that can see pyrite's dependencies but none of its plugins.

    Verification is about what a *new interpreter* finds after an editable
    install, so the tests install real editable packages into a real (empty)
    venv and run the real command line in it. Mocking ``entry_points`` is how
    the first version of these tests passed with the feature absent (#786).
    """

    def __init__(self, root: Path):
        import site

        self.root = root
        venv = root / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True)
        self.python = venv / "bin" / "python"
        site_dir = next(venv.glob("lib/python*/site-packages"))
        # pyrite's dependencies (and setuptools, for the offline editable build).
        (site_dir / "parent.pth").write_text(
            "\n".join(p for p in site.getsitepackages() if "site-packages" in p) + "\n"
        )
        self.env = {
            **os.environ,
            "HOME": str(root / "home"),
            "PYRITE_CONFIG_DIR": str(root / "cfg"),
            "PYTHONPATH": str(REPO),
            "PIP_NO_BUILD_ISOLATION": "0",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "HF_HUB_OFFLINE": "1",
            "PYRITE_AUTO_EMBED": "0",
            "NO_COLOR": "1",
            "COLUMNS": "200",
        }
        (root / "home").mkdir()

    def package(self, name: str, plugins: dict[str, str], module_body: str) -> Path:
        """An extension directory: ``plugins`` maps entry-point name to ``module:attr``."""
        pkg = name.replace("-", "_")
        d = self.root / "src" / name
        d.mkdir(parents=True)
        eps = "\n".join(f'{k} = "{v}"' for k, v in plugins.items())
        (d / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n'
            f'[project]\nname = "{name}"\nversion = "0.1"\n'
            f'[project.entry-points."pyrite.plugins"]\n{eps}\n'
            f'[tool.setuptools]\npy-modules = ["{pkg}"]\n'
        )
        (d / f"{pkg}.py").write_text(module_body)
        return d

    def install(self, path: Path, *args: str):
        return subprocess.run(
            [
                str(self.python),
                "-c",
                "import sys; from pyrite.cli import app; sys.argv[0]='pyrite'; app()",
                "extension",
                "install",
                str(path),
                *args,
            ],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=self.root,
            timeout=300,
        )


_GOOD = "class Plugin:\n    name = 'good'\n"
_RAISES = "raise RuntimeError('boom at import')\n"


@pytest.fixture
def venv(tmp_path):
    return _Venv(tmp_path)


class TestExtensionVerify:
    """`extension install --verify` loads the installed distribution's own
    ``pyrite.plugins`` entry points in a fresh interpreter (#786)."""

    def test_a_good_editable_plugin_verifies(self, venv):
        """Control: with the fix absent this fails, because the installing process
        cannot yet see an editable install's finder (#786)."""
        good = venv.package("ext-good", {"good": "ext_good:Plugin"}, _GOOD)
        out = venv.install(good, "--verify")
        text = out.stdout + out.stderr
        assert out.returncode == 0, text
        assert _result(out.stdout)["verified"] is True, text

    def test_an_entry_point_that_raises_exits_partial_and_names_it(self, venv):
        bad = venv.package("ext-bad", {"bad": "ext_bad:Plugin"}, _RAISES)
        out = venv.install(bad, "--verify")
        text = out.stdout + out.stderr
        assert out.returncode == PARTIAL, text
        assert "bad" in text and "boom at import" in text, text
        assert _result(out.stdout)["verified"] is False, text

    def test_an_unrelated_broken_plugin_does_not_change_the_answer(self, venv):
        other = venv.package("ext-other", {"other": "ext_other:Plugin"}, _RAISES)
        assert venv.install(other).returncode == 0
        good = venv.package("ext-good", {"good": "ext_good:Plugin"}, _GOOD)
        out = venv.install(good, "--verify")
        assert out.returncode == 0, out.stdout + out.stderr
        assert "ext-other" not in out.stdout + out.stderr

    @pytest.mark.control(
        reason="control: the old in-process check also exited 3 here (the dist was not importable); pins that the new check keeps refusing"
    )
    def test_a_distribution_with_no_plugin_entry_points_is_not_verified(self, venv):
        bare = venv.package("ext-bare", {}, "X = 1\n")
        out = venv.install(bare, "--verify")
        assert out.returncode == PARTIAL, out.stdout + out.stderr


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
        assert json.loads(out.stdout)["outcome"] == "partial"

    # The regimes below are ADR-0046 slice 1's: the task module returns an
    # Outcome and the root group renders it, through the real entry point.

    def test_children_that_all_failed_are_refused_and_say_so_in_json(self, cli):
        cli.pyrite("task", "create", "Parent", "-k", "demo")
        cli.pyrite("task", "decompose", "parent", "-k", "demo", "-c", "Same")
        out = cli.pyrite("task", "decompose", "parent", "-k", "demo", "-c", "Same")
        assert out.returncode == REFUSED, out.stdout + out.stderr
        # `decomposed: true` was all a JSON reader saw when every child failed.
        assert json.loads(out.stdout)["outcome"] == "nothing"

    @pytest.mark.control(reason="regime: a batch of zero items stays click's usage error")
    def test_decompose_with_no_child_is_a_usage_error(self, cli):
        out = cli.pyrite("task", "decompose", "parent", "-k", "demo")
        assert out.returncode == 2, out.stdout + out.stderr

    def test_a_lost_claim_says_nothing_happened_in_json(self, cli):
        cli.pyrite("task", "create", "T1", "-k", "demo")
        cli.pyrite("task", "claim", "t1", "-k", "demo", "-a", "me")
        out = cli.pyrite("task", "claim", "t1", "-k", "demo", "-a", "you")
        assert out.returncode == REFUSED, out.stdout + out.stderr
        body = json.loads(out.stdout)
        assert (body["claimed"], body["outcome"]) == (False, "nothing")

    def test_a_missing_kb_is_refused_in_json_and_in_rich(self, cli):
        out = cli.pyrite("task", "create", "T", "-k", "nope")
        assert out.returncode == REFUSED, out.stdout + out.stderr
        body = json.loads(out.stdout)
        assert (body["error_code"], body["outcome"]) == ("KB_NOT_FOUND", "error")
        rich = cli.pyrite("task", "create", "T", "-k", "nope", "--format", "rich")
        assert rich.returncode == REFUSED
        assert "ERROR [KB_NOT_FOUND]" in rich.stdout

    def test_json_is_the_default_and_pyrite_format_sets_another(self, cli):
        """#303, folded into slice 1 for the task module."""
        cli.pyrite("task", "create", "T1", "-k", "demo")
        listed = cli.pyrite("task", "list", "-k", "demo")
        assert listed.returncode == 0, listed.stdout + listed.stderr
        assert json.loads(listed.stdout)["count"] == 1
        rich = cli.pyrite("task", "list", "-k", "demo", env={"PYRITE_FORMAT": "rich"})
        assert "Tasks" in rich.stdout and not rich.stdout.lstrip().startswith("{")


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
# The outcome contract (ADR-0046): the rule the tables above stand in for
# --------------------------------------------------------------------------
#
# A command is on the contract when it declares the shared --format option
# (pyrite.cli.output.declares_shared_format). What such a command can and
# cannot do is not checked here, or anywhere, by reading source: the root group
# enforces it when the command runs, and tests/test_cli_outcome.py walks every
# command on the contract with each way out planted (ADR-0046, decisions 6-8).
# This section holds what the registry can say: which commands are still
# legacy, and that a command on the contract is declared the way the root
# expects.

# Commands that still print their own result and choose their own exit code:
# for each module, how many of its registered commands (across the three CLIs)
# do not declare the shared option. The numbers match the registry exactly, so
# they move only in a diff a reviewer sees: a slice that migrates commands
# lowers its module's number (and deletes the line at zero; slice 11 deletes
# the table), and a new command is on the contract unless someone raises a
# number here. A module with no line has none: a new command module is born on
# the contract.
LEGACY_COMMANDS = {
    "extensions/cascade/src/pyrite_cascade/cli.py": 5,
    "extensions/encyclopedia/src/pyrite_encyclopedia/cli.py": 4,
    "extensions/journalism-investigation/src/pyrite_journalism_investigation/cli.py": 18,
    "extensions/social/src/pyrite_social/cli.py": 4,
    "extensions/software-kb/src/pyrite_software_kb/cli.py": 23,
    "extensions/zettelkasten/src/pyrite_zettelkasten/cli.py": 4,
    "pyrite/admin_cli.py": 27,
    "pyrite/cli/__init__.py": 14,
    "pyrite/cli/browse_commands.py": 7,
    "pyrite/cli/collection_commands.py": 2,
    "pyrite/cli/db_commands.py": 2,
    "pyrite/cli/entry_commands.py": 7,
    "pyrite/cli/export_commands.py": 2,
    "pyrite/cli/extension_commands.py": 4,
    "pyrite/cli/ids_commands.py": 2,
    "pyrite/cli/index_commands.py": 7,
    "pyrite/cli/init_command.py": 1,
    "pyrite/cli/kb_commands.py": 15,
    "pyrite/cli/link_commands.py": 7,
    "pyrite/cli/mcp_setup_command.py": 1,
    "pyrite/cli/protocol_commands.py": 2,
    "pyrite/cli/qa_commands.py": 10,
    "pyrite/cli/repo_commands.py": 6,
    "pyrite/cli/schema_commands.py": 3,
    "pyrite/cli/search_commands.py": 2,
    "pyrite/read_cli.py": 6,
}

# The total when slice 1 introduced the table. It may only get smaller.
LEGACY_COMMANDS_AT_SLICE_1 = 185


def _on_the_contract(cmd) -> bool:
    """By name, not by importing the helper at module level: so this file still
    collects on a tree without the contract."""
    from pyrite.cli.output import declares_shared_format

    return declares_shared_format(cmd)


def _returns_outcome(cmd) -> bool:
    import inspect

    ann = inspect.signature(inspect.unwrap(cmd.callback), eval_str=True).return_annotation
    return (getattr(ann, "__module__", None), getattr(ann, "__name__", None)) == (
        "pyrite.cli.outcome",
        "Outcome",
    )


def _legacy_by_module() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for name, cmd in _commands().items():
        loc = _location(cmd) if cmd.callback is not None else None
        # A command defined outside the repo is a third-party plugin's
        # (decision 5): not this table's to count.
        if loc is not None and not _on_the_contract(cmd):
            found.setdefault(loc[0], []).append(name)
    return found


def _contract_modules() -> set[str]:
    return {
        loc[0]
        for cmd in _commands().values()
        if _on_the_contract(cmd) and (loc := _location(cmd)) is not None
    }


def test_the_legacy_commands_are_exactly_the_ones_counted():
    """A command that is not on the contract is counted in LEGACY_COMMANDS, and
    the count is exact in both directions. A new command in a legacy module
    does not join the legacy path unnoticed (the number would have to go up),
    and a slice that migrates commands has to take them off (the number has to
    go down)."""
    actual = {module: len(names) for module, names in _legacy_by_module().items()}
    wrong = []
    for module in sorted(set(actual) | set(LEGACY_COMMANDS)):
        counted, found = LEGACY_COMMANDS.get(module, 0), actual.get(module, 0)
        if found > counted:
            wrong.append(
                f"{module}: {found} commands without the shared --format, {counted} counted. "
                "A new command declares OUTPUT_FORMAT and returns an Outcome: "
                f"{sorted(_legacy_by_module()[module])}"
            )
        elif found < counted:
            wrong.append(
                f"{module}: {found} legacy commands left, {counted} counted: lower the number"
                + (" (delete the line)" if not found else "")
            )
    assert not wrong, "\n".join(wrong)


@pytest.mark.control(
    reason="tests the table in this file: red when a number is raised past the slice-1 total"
)
def test_the_legacy_count_only_goes_down():
    assert sum(LEGACY_COMMANDS.values()) <= LEGACY_COMMANDS_AT_SLICE_1
    assert all(LEGACY_COMMANDS.values()), "a module with no legacy command has no line"


def test_a_command_on_the_contract_is_declared_the_way_the_root_expects():
    """The registry's half of the contract, for every command that declares
    the shared option:

    - it is annotated ``-> Outcome`` (documentation the registry can check; the
      root checks the value itself on every run);
    - none of its parameters has a callback but the shared option's own. A
      parameter callback runs while click is parsing, before the root watches
      (that is how ``--help`` works), so it could print a result and exit 0.
    """
    from pyrite.cli import output

    shared = {output._record_format, output._record_short_format}
    offences = []
    for name, cmd in sorted(_commands().items()):
        if not _on_the_contract(cmd):
            continue
        if not _returns_outcome(cmd):
            offences.append(f"{name} is not annotated -> Outcome")
        for param in cmd.params:
            callback = getattr(param.callback, "__wrapped__", param.callback)
            if callback is not None and callback not in shared:
                offences.append(f"{name}: parameter {param.name!r} has its own callback")
    assert not offences, "\n".join(offences)


def test_the_task_commands_are_on_the_contract():
    """Slice 1 migrated `task`: all ten declare the shared option."""
    on = sorted(n for n, cmd in _commands().items() if _on_the_contract(cmd))
    assert len(on) == 10 and all(n.startswith("pyrite task ") for n in on), on
    assert _contract_modules() == {"pyrite/cli/task_commands.py"}


# A HINT, NOT THE GUARD. What follows reads source, so it knows only the
# spellings it was given: an alias, a `getattr` or a helper in another module
# gets past it, and that is not a finding against the contract. It exists for
# the three things no check inside the process can see, because they act after
# the root has returned (ADR-0046, "What no check in the process can see"), and
# for one thing the root has no reason to see (a command that reads its format
# has made the format change what runs). The property itself is enforced by
# PyriteCLIGroup.invoke and proved by the walk in tests/test_cli_outcome.py.
AFTER_THE_ROOT_HAS_RETURNED = {
    "_exit": "os._exit ends the process with no outcome: exit 0 and empty stdout",
    "register": "an atexit handler runs after the root has printed and set the exit code",
    "Thread": "a thread that outlives the command writes after the root has printed",
    "Timer": "a timer thread that outlives the command writes after the root has printed",
}


def _hints(source: str, rel: str = "<module>") -> list[str]:
    hints = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _name(node) in AFTER_THE_ROOT_HAS_RETURNED:
            hints.append(f"{rel}:{node.lineno} {AFTER_THE_ROOT_HAS_RETURNED[_name(node)]}")
    return hints


def test_hint_a_module_on_the_contract_does_nothing_the_root_cannot_see():
    """A hint (see above): the plain spellings of ``os._exit``,
    ``atexit.register`` and a started thread in a module with a command on the
    contract, and a command that reads its own format parameter."""
    import inspect

    hints = []
    for rel in sorted(_contract_modules()):
        hints += _hints((REPO / rel).read_text(), rel)
    for name, cmd in _commands().items():
        if not _on_the_contract(cmd) or _location(cmd) is None:
            continue
        fmt_params = {p.name for p in cmd.params if "--format" in p.opts or "-f" in p.opts}
        func = ast.parse(inspect.getsource(inspect.unwrap(cmd.callback)).lstrip())
        for node in ast.walk(func):
            if isinstance(node, ast.Name) and node.id in fmt_params:
                if isinstance(node.ctx, ast.Load):
                    hints.append(f"{name} reads its format parameter {node.id!r}")
    assert not hints, "\n".join(hints)


@pytest.mark.control(reason="tests the hint in this file, not pyrite code")
def test_hint_finds_the_plain_spellings_and_only_those():
    plain = "import os, atexit, threading\nos._exit(0)\natexit.register(print)\n"
    assert len(_hints(plain + "threading.Thread(target=print).start()\n")) == 3
    # An alias is past it. That is what makes it a hint.
    assert _hints("from os import _exit as leave\nleave(0)\n") == []


def _renderers(tree: ast.AST) -> set[str]:
    """Functions the root calls for --format rich: one named ``_render_*`` or
    passed as ``rich=``. Used by the #779 scan below, which skips them in a
    module on the contract."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_render_"):
            names.add(node.name)
        if isinstance(node, ast.Call):
            names |= {
                k.value.id
                for k in node.keywords
                if k.arg == "rich" and isinstance(k.value, ast.Name)
            }
    return names


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
    ("pyrite/cli/extension_commands.py", "_verify_distribution"): ("fixed", "TestExtensionVerify"),
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
    # A renderer in a module on the contract prints failures by design: the
    # exit code comes from the Outcome the command returned, which the root
    # reads, not from what was printed.
    migrated = _contract_modules()
    for path in SCANNED:
        rel = str(path.relative_to(REPO))
        tree = ast.parse(path.read_text())
        renderers = _renderers(tree) if rel in migrated else set()
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
                    if _func_of(stmt, parents) in renderers:
                        continue
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
