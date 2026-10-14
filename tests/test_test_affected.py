"""Tests for scripts/test-affected: local test selection (#356).

Local runs are a fast feedback loop, not the gate -- CI on the PR runs the
full suite. So the selector may be lean, but it must never silently drop a
test that imports what the branch changed, and it must fall back to the full
suite when the branch touches something every test depends on (conftest,
pytest config, the hook config, CI).

Every case builds a tiny repo in tmp_path with the same shape as this one
(pyrite/, extensions/*/src, tests/, extensions/*/tests, scripts/) and asks
the selector about a changed-file list; the git tests make real commits.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test-affected"


def _load():
    loader = importlib.machinery.SourceFileLoader("test_affected_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader("test_affected_script", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["test_affected_script"] = module
    loader.exec_module(module)
    return module


ta = _load()


@pytest.fixture(autouse=True)
def _not_under_a_push(monkeypatch):
    # The pre-push hook runs this file with PRE_COMMIT_*_REF naming commits
    # of the real repository (and maybe PYRITE_PUSH_FULL); the CLI
    # subprocesses here must not inherit them.
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)
    monkeypatch.delenv("PYRITE_PUSH_FULL", raising=False)
    monkeypatch.delenv("PYRITE_PUSH_EXPERIMENTAL", raising=False)


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    files = {
        "pyrite/__init__.py": "",
        "pyrite/a.py": "from . import b\n\nTEMPLATE = 'page.html'\n",
        "pyrite/b.py": "def func():\n    return 1\n",
        "pyrite/c.py": "VALUE = 3\n",
        "pyrite/d.py": "def lazy():\n    from pyrite import c\n    return c\n",
        "pyrite/gone_user.py": "import pyrite.gone\n",
        "pyrite/templates/page.html": "<p></p>\n",
        "pyrite/pkg/__init__.py": "from .impl import Thing\nfrom .other import Other\n",
        "pyrite/pkg/impl.py": "class Thing:\n    pass\n",
        "pyrite/pkg/other.py": "class Other:\n    pass\n",
        "tests/test_pkg_reexport.py": "from pyrite.pkg import Thing\n",
        "tests/test_pkg_direct.py": "from pyrite.pkg.impl import Thing\n",
        "tests/test_pkg_other.py": "from pyrite.pkg import Other\n",
        "extensions/foo/src/pyrite_foo/__init__.py": "",
        "extensions/foo/src/pyrite_foo/plugin.py": "from pyrite.b import func\n",
        "extensions/foo/tests/test_foo.py": "from pyrite_foo.plugin import func\n",
        "scripts/tool.py": "print('tool')\n",
        "conftest.py": "",
        "pyproject.toml": "[project]\nname = 'x'\n",
        "tests/__init__.py": "",
        "tests/conftest.py": """
            import pytest

            from pyrite.c import VALUE


            def _helper():
                return VALUE


            @pytest.fixture
            def thing():
                return _helper()


            @pytest.fixture
            def other_thing(thing):
                return thing


            @pytest.fixture(autouse=True)
            def _auto():
                from pyrite import b
                yield b
        """,
        "tests/helpers.py": "from pyrite import b\n",
        "tests/test_a.py": "import pyrite.a\n\ndef test_a():\n    pass\n",
        "tests/test_b.py": "from pyrite import b\n\ndef test_b():\n    pass\n",
        "tests/test_c.py": "def test_c(thing):\n    assert thing\n",
        "tests/test_c2.py": "class TestC:\n    def test_c(self, other_thing):\n        pass\n",
        "tests/test_d.py": "import pyrite.d\n\ndef test_d():\n    pass\n",
        "tests/test_patch.py": (
            "from unittest.mock import patch\n\n"
            "def test_p():\n    with patch('pyrite.b.func'):\n        pass\n"
        ),
        "tests/test_helper_user.py": "from tests.helpers import b\n",
        "tests/test_tool.py": "from pathlib import Path\n\nTOOL = Path('scripts') / 'tool.py'\n",
        "tests/test_gone.py": "import pyrite.gone_user\n",
        "tests/test_unrelated.py": "def test_u():\n    pass\n",
        "tests/test_core.py": """
            import pytest

            @pytest.mark.core
            def test_smoke():
                pass

            def test_not_core():
                pass

            @pytest.mark.core
            class TestCoreClass:
                def test_x(self):
                    pass
        """,
        "tests/test_core_module.py": "import pytest\n\npytestmark = pytest.mark.core\n",
        "tests/test_core_bare_mark.py": (
            "from pytest import mark\n\n@mark.core\ndef test_bare():\n    pass\n"
        ),
        "tests/test_core_annotated.py": (
            "import pytest\n\npytestmark: list = [pytest.mark.core, pytest.mark.cli]\n"
        ),
    }
    for rel, text in files.items():
        _write(tmp_path, rel, text)
    return tmp_path


def _select(repo: Path, *changed: str, **kw):
    return ta.select(repo, list(changed), **kw)


class TestAffectedByImports:
    def test_direct_and_transitive_importers_are_selected(self, repo):
        sel = _select(repo, "pyrite/b.py")
        assert sel.full is None
        assert {
            "tests/test_a.py",  # pyrite.a imports b (relative import)
            "tests/test_b.py",
            "tests/test_patch.py",  # mock.patch target string
            "tests/test_helper_user.py",  # via a test helper module
            "extensions/foo/tests/test_foo.py",  # via an extension module
        } <= set(sel.files)
        assert "tests/test_unrelated.py" not in sel.files
        assert "tests/test_c.py" not in sel.files

    def test_function_level_imports_count(self, repo):
        sel = _select(repo, "pyrite/c.py")
        assert "tests/test_d.py" in sel.files

    def test_fixture_from_conftest_carries_its_imports(self, repo):
        sel = _select(repo, "pyrite/c.py")
        assert "tests/test_c.py" in sel.files
        # other_thing depends on thing, which depends on pyrite.c via a helper
        assert "tests/test_c2.py" in sel.files
        assert "tests/test_unrelated.py" not in sel.files

    def test_a_fixture_chain_across_conftests_carries_imports(self, repo):
        # tests/sub/conftest.py's sub_thing requests tests/conftest.py's
        # `thing`, which imports pyrite.c: the dependency crosses files.
        _write(repo, "tests/sub/__init__.py")
        _write(
            repo,
            "tests/sub/conftest.py",
            "import pytest\n\n@pytest.fixture\ndef sub_thing(thing):\n    return 1\n",
        )
        _write(repo, "tests/sub/test_sub.py", "def test_s(sub_thing):\n    pass\n")
        sel = _select(repo, "pyrite/c.py")
        assert "tests/sub/test_sub.py" in sel.files

    def test_autouse_fixture_imports_do_not_select_everything(self, repo):
        # _auto (autouse) imports pyrite.b and wraps every test; a test that
        # does not name it is not selected by it.
        sel = _select(repo, "pyrite/b.py")
        assert "tests/test_unrelated.py" not in sel.files
        assert "tests/test_c.py" not in sel.files

    def test_an_autouse_fixture_requested_by_name_is_attributed(self, repo):
        _write(repo, "tests/test_names_auto.py", "def test_x(_auto):\n    pass\n")
        sel = _select(repo, "pyrite/b.py")
        assert "tests/test_names_auto.py" in sel.files

    def test_usefixtures_counts_as_using_the_fixture(self, repo):
        _write(
            repo,
            "tests/test_usefixtures.py",
            "import pytest\n\n@pytest.mark.usefixtures('thing')\ndef test_x():\n    pass\n",
        )
        sel = _select(repo, "pyrite/c.py")
        assert "tests/test_usefixtures.py" in sel.files

    def test_a_file_that_does_not_parse_is_skipped_not_fatal(self, repo):
        _write(repo, "tests/test_broken.py", "def (:\n")
        _write(repo, "pyrite/broken.py", "import pyrite.b\nclass :\n")
        sel = _select(repo, "pyrite/b.py")
        assert "tests/test_b.py" in sel.files

    def test_content_trees_match_by_basename_not_directory(self, repo):
        # kb/backlog/x.md must not select every test that says "backlog".
        _write(repo, "tests/test_backlog_words.py", "KIND = 'backlog'\n")
        sel = _select(repo, "kb/backlog/some-item.md")
        assert "tests/test_backlog_words.py" not in sel.files

    def test_a_changed_test_file_is_not_matched_by_its_directory_name(self, repo):
        # tests/test_x.py must not select every test that says "tests"
        # (#362 evaluation: 109 extra files for a one-test PR).
        _write(repo, "tests/test_says_tests.py", "ROOT = 'tests'\n")
        sel = _select(repo, "tests/test_unrelated.py")
        assert "tests/test_says_tests.py" not in sel.files

    def test_an_unreferenced_script_is_not_matched_by_the_scripts_directory(self, repo):
        _write(repo, "scripts/lonely.py", "print('x')\n")
        _write(repo, "tests/test_says_scripts.py", "ROOT = 'scripts'\n")
        sel = _select(repo, "scripts/lonely.py")
        assert "tests/test_says_scripts.py" not in sel.files

    def test_other_trees_fall_back_to_the_directory_name(self, repo):
        _write(repo, "tests/test_fragments.py", "DIR = 'changelog.d'\n")
        sel = _select(repo, "changelog.d/some-change.fixed.md")
        assert "tests/test_fragments.py" in sel.files

    def test_changed_test_file_is_always_selected(self, repo):
        sel = _select(repo, "tests/test_unrelated.py")
        assert "tests/test_unrelated.py" in sel.files

    def test_deleted_module_selects_its_former_importers(self, repo):
        (repo / "pyrite" / "gone.py").unlink(missing_ok=True)  # never existed on disk
        sel = _select(repo, "pyrite/gone.py")
        assert "tests/test_gone.py" in sel.files

    def test_non_python_file_selects_modules_that_name_it(self, repo):
        sel = _select(repo, "pyrite/templates/page.html")
        assert "tests/test_a.py" in sel.files
        assert "tests/test_b.py" not in sel.files

    def test_an_extension_change_selects_tests_that_load_plugins(self, repo):
        # Core tests reach extensions through entry points, not imports.
        _write(repo, "tests/test_registry.py", "def test_r():\n    get_registry()\n")
        _write(repo, "tests/test_eps.py", "from importlib.metadata import entry_points\n")
        sel = _select(repo, "extensions/foo/src/pyrite_foo/plugin.py")
        assert {"tests/test_registry.py", "tests/test_eps.py"} <= set(sel.files)
        sel = _select(repo, "pyrite/c.py")
        assert "tests/test_registry.py" not in sel.files

    def test_a_name_matches_whole_words_only(self, repo):
        # "x.md" must not select a test that mentions "index.md"
        _write(repo, "tests/test_index.py", "NAME = 'index.md'\n")
        _write(repo, "tests/test_x.py", "NAME = 'docs/x.md'\n")
        sel = _select(repo, "docs/x.md")
        assert "tests/test_x.py" in sel.files
        assert "tests/test_index.py" not in sel.files

    def test_script_selects_tests_that_name_it(self, repo):
        sel = _select(repo, "scripts/tool.py")
        assert "tests/test_tool.py" in sel.files
        assert "tests/test_unrelated.py" not in sel.files

    def test_a_package_reexport_resolves_to_the_defining_module(self, repo):
        # pyrite/pkg/__init__.py re-exports Thing and Other. A test that uses
        # only Thing does not depend on other.py, though importing the
        # package executes both: an import-time break in other.py is caught
        # by other.py's own tests, a behaviour change only by its users.
        sel = _select(repo, "pyrite/pkg/other.py")
        assert "tests/test_pkg_other.py" in sel.files
        assert "tests/test_pkg_reexport.py" not in sel.files
        assert "tests/test_pkg_direct.py" not in sel.files
        sel = _select(repo, "pyrite/pkg/impl.py")
        assert {"tests/test_pkg_reexport.py", "tests/test_pkg_direct.py"} <= set(sel.files)
        assert "tests/test_pkg_other.py" not in sel.files

    def test_a_changed_package_init_selects_every_importer_of_the_package(self, repo):
        # Importing any submodule executes the package's __init__.
        sel = _select(repo, "pyrite/pkg/__init__.py")
        assert {
            "tests/test_pkg_reexport.py",
            "tests/test_pkg_direct.py",
            "tests/test_pkg_other.py",
        } <= set(sel.files)
        assert "tests/test_b.py" not in sel.files

    def test_explain_gives_the_import_chain(self, repo):
        sel = _select(repo, "pyrite/b.py")
        why = sel.files["tests/test_a.py"]
        assert "pyrite.a" in why and "pyrite/b.py" in why

    def test_docs_only_change_selects_only_core(self, repo):
        _write(repo, "docs/guide.md", "hello\n")
        sel = _select(repo, "docs/guide.md")
        assert sel.full is None
        assert sel.files == {}
        assert sel.core  # core always runs


class TestFullSuiteFallback:
    @pytest.mark.parametrize(
        "path",
        [
            "conftest.py",
            "tests/conftest.py",
            "pyproject.toml",
            "extensions/foo/pyproject.toml",
            ".pre-commit-config.yaml",
            ".github/workflows/ci.yml",
            "pytest.ini",
            "setup.cfg",
            "tests/fixtures/roundtrip/entry.md",
            "tox.ini",
        ],
    )
    def test_infrastructure_changes_run_everything(self, repo, path):
        sel = _select(repo, path)
        assert sel.full is not None and path in sel.full

    def test_helper_imported_by_many_tests_runs_everything(self, repo):
        sel = _select(repo, "tests/helpers.py", helper_threshold=1)
        assert sel.full is not None and "tests/helpers.py" in sel.full

    def test_helper_imported_by_few_tests_selects_its_importers(self, repo):
        sel = _select(repo, "tests/helpers.py", helper_threshold=5)
        assert sel.full is None
        assert "tests/test_helper_user.py" in sel.files


class TestCore:
    def test_core_node_ids_are_collected_statically(self, repo):
        sel = _select(repo, "docs/x.md")
        assert set(sel.core) == {
            "tests/test_core.py::test_smoke",
            "tests/test_core.py::TestCoreClass",
            "tests/test_core_module.py",
            "tests/test_core_bare_mark.py::test_bare",  # from pytest import mark
            "tests/test_core_annotated.py",  # pytestmark: list = [...]
        }

    def test_core_ids_inside_a_selected_file_are_not_run_twice(self, repo):
        sel = _select(repo, "tests/test_core.py")
        args = ta.pytest_args(sel, workers=4)
        assert "tests/test_core.py" in args
        assert not any(a.startswith("tests/test_core.py::") for a in args)


class TestPytestArgs:
    def test_selected_run_uses_n_workers(self, repo):
        sel = _select(repo, "pyrite/b.py")
        args = ta.pytest_args(sel, workers=4)
        assert args[:2] == ["-n", "4"]
        assert "tests/test_b.py" in args
        assert "tests/test_core_module.py" in args

    def test_full_run_is_tests_and_extensions(self, repo):
        sel = _select(repo, "conftest.py")
        args = ta.pytest_args(sel, workers=2)
        assert args[:2] == ["-n", "2"]
        assert "tests/" in args and "extensions/" in args
        assert not any(a.endswith(".py") for a in args)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


class TestChangedFiles:
    @pytest.fixture
    def git_repo(self, repo):
        _git(repo, "init", "-q", "-b", "dev")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "checkout", "-qb", "feature/x")
        return repo

    def test_branch_commits_working_tree_and_untracked_files(self, git_repo):
        (git_repo / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
        _git(git_repo, "commit", "-qam", "change b")
        (git_repo / "pyrite" / "c.py").write_text("VALUE = 4\n")  # unstaged
        _write(git_repo, "pyrite/new.py", "")  # untracked
        changed = ta.changed_files(git_repo, "dev")
        assert {"pyrite/b.py", "pyrite/c.py", "pyrite/new.py"} <= set(changed)

    def test_committed_only_ignores_the_working_tree(self, git_repo):
        (git_repo / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
        _git(git_repo, "commit", "-qam", "change b")
        (git_repo / "pyrite" / "c.py").write_text("VALUE = 4\n")
        changed = ta.changed_files(git_repo, "dev", committed_only=True)
        assert changed == ["pyrite/b.py"]

    def test_diff_is_against_the_merge_base_not_the_base_tip(self, git_repo):
        (git_repo / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
        _git(git_repo, "commit", "-qam", "change b")
        _git(git_repo, "checkout", "-q", "dev")
        (git_repo / "pyrite" / "c.py").write_text("VALUE = 9\n")
        _git(git_repo, "commit", "-qam", "dev moved on")
        _git(git_repo, "checkout", "-q", "feature/x")
        assert ta.changed_files(git_repo, "dev", committed_only=True) == ["pyrite/b.py"]

    def test_non_ascii_paths_are_not_quoted(self, git_repo):
        # Without -z, git prints "tests/test_caf\303\251.py" in quotes and
        # the path matches nothing.
        _write(git_repo, "tests/test_café.py", "def test_x():\n    pass\n")
        _git(git_repo, "add", ".")
        _git(git_repo, "commit", "-qm", "unicode")
        assert "tests/test_café.py" in ta.changed_files(git_repo, "dev", committed_only=True)
        assert "tests/test_café.py" in ta.changed_files(git_repo, "dev")

    def test_renames_report_both_paths(self, git_repo):
        _git(git_repo, "mv", "pyrite/c.py", "pyrite/c_new.py")
        _git(git_repo, "commit", "-qm", "rename")
        changed = ta.changed_files(git_repo, "dev", committed_only=True)
        assert {"pyrite/c.py", "pyrite/c_new.py"} <= set(changed)


class TestBaseFallback:
    def test_default_base_falls_back_to_local_dev_without_a_remote(self, repo):
        _git(repo, "init", "-q", "-b", "dev")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")
        _git(repo, "checkout", "-qb", "feature/x")
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
        _git(repo, "commit", "-qam", "change b")
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--list", "--committed"],
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        assert "tests/test_b.py" in out.stdout.split()


class TestPushedRange:
    """Under the pre-push hook, select from what is being pushed.

    pre-commit exports PRE_COMMIT_FROM_REF / PRE_COMMIT_TO_REF. The working
    tree is not what is pushed: an uncommitted edit, a committed change
    reverted in the tree, or a push of another branch all differ from it.
    """

    @pytest.fixture
    def pushed(self, repo):
        _git(repo, "init", "-q", "-b", "dev")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "checkout", "-qb", "feature/x")
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 2\n")
        _git(repo, "commit", "-qam", "change b")
        return repo, base, _git(repo, "rev-parse", "HEAD").strip()

    def _list(self, repo, **env):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--list"],
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

    def test_selects_the_pushed_range_not_the_working_tree(self, pushed):
        repo, base, head = pushed
        (repo / "pyrite" / "b.py").write_text("def func():\n    return 1\n")  # reverted in tree
        (repo / "pyrite" / "c.py").write_text("VALUE = 4\n")  # not being pushed
        out = self._list(repo, PRE_COMMIT_FROM_REF=base, PRE_COMMIT_TO_REF=head)
        assert out.returncode == 0, out.stderr
        selected = out.stdout.split()
        assert "tests/test_b.py" in selected
        assert "tests/test_c.py" not in selected

    def test_a_branch_other_than_the_checked_out_one(self, pushed):
        repo, base, head = pushed
        _git(repo, "checkout", "-q", "dev")
        out = self._list(repo, PRE_COMMIT_FROM_REF=base, PRE_COMMIT_TO_REF=head)
        assert out.returncode == 0, out.stderr
        assert "tests/test_b.py" in out.stdout.split()

    def test_an_all_zero_from_ref_diffs_against_the_base(self, pushed):
        repo, _, head = pushed
        out = self._list(repo, PRE_COMMIT_FROM_REF="0" * 40, PRE_COMMIT_TO_REF=head)
        assert out.returncode == 0, out.stderr
        assert "tests/test_b.py" in out.stdout.split()


class TestNoBase:
    def test_a_missing_base_fails_closed_with_a_hint(self, repo):
        _git(repo, "init", "-q", "-b", "main")  # neither origin/dev nor dev
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), "--run", "--dry-run"],
            capture_output=True,
            text=True,
        )
        assert out.returncode != 0
        assert "Traceback" not in out.stderr
        assert "fetch origin dev" in out.stderr and "PYRITE_PUSH_FULL=1" in out.stderr


class TestCLI:
    def _run(self, repo: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(repo), *args],
            capture_output=True,
            text=True,
        )

    def test_list_prints_selected_paths(self, repo):
        out = self._run(repo, "--list", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        lines = out.stdout.split()
        assert "tests/test_b.py" in lines
        assert "tests/test_unrelated.py" not in lines

    def test_explain_prints_reasons(self, repo):
        out = self._run(repo, "--explain", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert "tests/test_a.py" in out.stdout and "pyrite.a" in out.stdout
        assert "core" in out.stdout

    def test_explain_names_the_fallback(self, repo):
        out = self._run(repo, "--explain", "--files", "tests/conftest.py")
        assert "full suite" in out.stdout and "tests/conftest.py" in out.stdout

    def test_run_dry_run_shows_the_pytest_command_with_four_workers(self, repo):
        out = self._run(repo, "--run", "--dry-run", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert "pytest -n 4" in out.stdout
        assert "tests/test_b.py" in out.stdout

    def test_run_uses_the_repository_venv_not_the_shebang_python(self, repo):
        # `scripts/test-affected --run` from a shell runs under whatever
        # `python3` the shebang finds, which may have no pytest (or another
        # checkout's). The worktree's .venv is the interpreter under test.
        venv_python = repo / ".venv" / "bin" / "python"
        _write(repo, ".venv/bin/python", "#!/bin/sh\n")
        venv_python.chmod(0o755)
        out = self._run(repo, "--run", "--dry-run", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert out.stdout.startswith(f"{venv_python} -m pytest"), out.stdout

    def test_workers_accept_auto(self, repo):
        # PYRITE_PUSH_WORKERS=auto must not turn into an argparse error that
        # blocks the push.
        out = self._run(repo, "--run", "--dry-run", "-n", "auto", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert "pytest -n auto " in out.stdout

    def test_workers_reject_nonsense(self, repo):
        out = self._run(repo, "--run", "--dry-run", "-n", "lots", "--files", "pyrite/b.py")
        assert out.returncode != 0
        assert "auto" in out.stderr

    def test_run_deselects_experimental_tests_by_default(self, repo):
        """#657: the pre-push hook runs the core; experimental tests do not
        block a push any more than they block a merge."""
        out = self._run(repo, "--run", "--dry-run", "--files", "pyrite/b.py")
        assert "-m 'not slow and not e2e and not experimental'" in out.stdout

    def test_run_says_how_many_experimental_tests_it_left_out(self, repo):
        """A change under extensions/ can select only experimental tests; the
        run must say so and how to run them, not pass quietly on nothing."""
        _write(
            repo,
            "tests/experimental_surface.py",
            "def is_experimental(nodeid):\n    return nodeid.startswith('tests/test_b.py')\n",
        )
        out = self._run(repo, "--run", "--dry-run", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert "1 experimental test" in out.stderr and "1 file" in out.stderr
        assert "--experimental" in out.stderr

    def test_with_experimental_there_is_nothing_to_report(self, repo):
        _write(
            repo,
            "tests/experimental_surface.py",
            "def is_experimental(nodeid):\n    return True\n",
        )
        out = self._run(repo, "--run", "--dry-run", "--experimental", "--files", "pyrite/b.py")
        assert "experimental test" not in out.stderr

    def test_no_mapping_no_report(self, repo):
        out = self._run(repo, "--run", "--dry-run", "--files", "pyrite/b.py")
        assert out.returncode == 0 and "experimental test" not in out.stderr

    @pytest.mark.control(
        reason="with the variable, the base also runs everything: it never filtered"
    )
    @pytest.mark.parametrize("how", [("--experimental",), ()])
    def test_experimental_tests_are_one_flag_or_variable_away(self, repo, how, monkeypatch):
        if not how:
            monkeypatch.setenv("PYRITE_PUSH_EXPERIMENTAL", "1")
        out = self._run(repo, "--run", "--dry-run", *how, "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert "not experimental" not in out.stdout
        assert "tests/test_b.py" in out.stdout

    def test_full_flag_forces_the_full_suite(self, repo):
        out = self._run(repo, "--run", "--dry-run", "--full", "-n", "3", "--files", "pyrite/b.py")
        assert out.returncode == 0, out.stderr
        assert "pytest -n 3 tests/ extensions/" in out.stdout


class TestThisRepository:
    """The real tree: the selector parses it and the core set exists."""

    def test_the_real_mapping_loads_and_counts(self):
        tests, files = ta.experimental_left_out(SCRIPT.parent.parent, ["extensions/social/tests"])
        assert tests > 0 and files == 1

    def test_selector_parses_the_repository(self):
        root = SCRIPT.parent.parent
        sel = ta.select(root, ["pyrite/storage/database.py"])
        assert sel.full is None
        assert any(p.startswith("tests/") for p in sel.files)

    # The core set, pinned by surface (#356). Dropping a mark silently drops a
    # surface from every local run, so this list changes only on purpose.
    CORE = {
        # storage
        "tests/test_storage.py",
        "tests/test_entry_lifecycle.py",
        # the KB service
        "tests/test_services.py::TestKBService",
        # authentication, including refusal and session verification
        "tests/test_auth_service.py::TestLogin::test_login_success",
        "tests/test_auth_service.py::TestLogin::test_login_wrong_password",
        "tests/test_auth_service.py::TestSessions::test_verify_session_valid",
        # auth / read scoping, and every entry point passing the policy (ADR-0037 §5)
        "tests/test_read_scoping_is_structural.py",
        "tests/test_every_entry_point_passes_the_policy.py",
        # the REST app factory
        "tests/test_api_state_isolation.py",
        "tests/test_rest_api.py::TestKBEndpoints",
        "tests/test_rest_api.py::TestEntryEndpoints",
        # MCP dispatch
        "tests/test_mcp_tool_dispatch_smoke.py",
        # the CLI entry point
        "tests/test_cli_commands.py::TestTyperListCommand",
        "tests/test_cli_commands.py::TestTyperGetCommand",
        "tests/test_cli_commands.py::TestTyperCreateCommand",
        "tests/test_cli_commands.py::TestTopLevelHelpAdvertisesOrient",
    }

    def test_core_set_is_exactly_the_named_surfaces(self):
        root = SCRIPT.parent.parent
        assert set(ta.select(root, []).core) == self.CORE
