"""The tutorial runner's directives, and the 20-minute tutorial's wiring.

`scripts/run_tutorial.py` runs a document's bash blocks and, for the
20-minute tutorial, also reads `<!-- runner: ... -->` comments: the entries a
search must find, the exit code a refused command must give, a fixture that
stands in for an agent's work. These tests pin the parsing and the checks
without running Pyrite, so a change to the runner that silently stops
asserting something fails here rather than passing a tutorial that no longer
proves anything.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "tutorials" / "pyrite-in-20-minutes.md"
KNOWN_DIRECTIVES = {"expect-ids", "expect-text", "expect-exit", "seed", "health-kb"}
# Caches pytest and pytest-cov write into the repository root during a run.
# Hypothesis is not one: the root conftest moves its home out of the checkout.
RUNNER_CACHES = (".pytest_cache", ".coverage")


def _is_runner_cache(name: str) -> bool:
    return name.startswith(RUNNER_CACHES)


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("run_tutorial", REPO / "scripts/run_tutorial.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _doc(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "doc.md"
    path.write_text(text)
    return path


class TestExtraction:
    def test_blocks_and_directives_come_back_in_document_order(self, runner, tmp_path):
        doc = _doc(
            tmp_path,
            "```bash\npyrite one\n```\n\n"
            "<!-- runner: expect-ids kb:an-id -->\n\n"
            "```text\nnot a command\n```\n\n"
            "```bash\npyrite two\n```\n",
        )
        assert runner.extract_items(doc) == [
            ("block", "pyrite one\n"),
            ("directive", "expect-ids kb:an-id"),
            ("block", "pyrite two\n"),
        ]

    def test_a_directive_inside_a_fence_is_example_text_not_a_directive(self, runner, tmp_path):
        doc = _doc(tmp_path, "```markdown\n<!-- runner: expect-ids x -->\n```\n")
        assert runner.extract_items(doc) == []

    def test_a_plain_comment_is_not_a_directive(self, runner, tmp_path):
        doc = _doc(tmp_path, "<!-- just a note -->\n```bash\npyrite x\n```\n")
        assert [kind for kind, _ in runner.extract_items(doc)] == ["block"]


class TestSkipping:
    def test_cloning_pyrite_is_the_install_step_and_is_skipped(self, runner):
        assert runner.should_skip(
            "git clone https://github.com/pyrite-wiki/pyrite.git && cd pyrite"
        )
        assert runner.should_skip("git clone https://github.com/pyrite-wiki/pyrite\nls")

    def test_cloning_the_demo_kbs_is_the_documents_claim_and_runs(self, runner):
        assert not runner.should_skip("git clone https://github.com/pyrite-wiki/pyrite-kb-demo.git")

    def test_pip_install_is_still_skipped(self, runner):
        assert runner.should_skip('pip install -e ".[all]"')


class TestExpectedIds:
    @staticmethod
    def _out(*pairs):
        rows = [{"kb_name": kb, "id": entry_id} for kb, entry_id in pairs]
        return json.dumps({"count": len(rows), "results": rows})

    def test_every_named_entry_present_passes(self, runner):
        out = self._out(("pyrite", "adr-0001"), ("tps", "andon"))
        runner.assert_expected_ids("pyrite search x", out, ["pyrite:adr-0001", "andon"])

    def test_the_right_id_in_the_wrong_kb_fails(self, runner):
        out = self._out(("tps", "andon"))
        with pytest.raises(runner.TutorialError, match="pyrite:andon"):
            runner.assert_expected_ids("pyrite search x", out, ["pyrite:andon"])

    def test_a_search_that_returns_other_entries_fails_naming_the_missing_one(self, runner):
        out = self._out(("pyrite", "something-else"))
        with pytest.raises(runner.TutorialError, match="adr-0001"):
            runner.assert_expected_ids("pyrite search x", out, ["adr-0001"])

    def test_output_that_is_not_json_cannot_satisfy_it(self, runner):
        with pytest.raises(runner.TutorialError, match="JSON"):
            runner.assert_expected_ids("echo hi", "hi", ["adr-0001"])


class TestStandInForTheClone:
    def test_copies_the_kb_so_a_run_leaves_the_checkout_as_it_found_it(self, runner, tmp_path):
        before = sorted(p.name for p in REPO.iterdir())
        target = tmp_path / "pyrite"
        runner.stand_in_for_clone(target)
        assert (target / "kb" / "kb.yaml").is_file()
        assert not (target / "kb").is_symlink(), (
            "a software KB loaded with its extension gets a kb/_templates/"
        )
        assert (target / ".claude" / "skills").is_dir()
        assert (target / ".venv" / "bin" / "activate").is_file()
        # Other xdist workers write the test runner's own caches into the root
        # while this runs (coverage data files at worker exit); those are not
        # the tutorial's writes, so they are left out of the comparison.
        after = sorted(p.name for p in REPO.iterdir())
        assert [n for n in after if not _is_runner_cache(n)] == [
            n for n in before if not _is_runner_cache(n)
        ]


@pytest.fixture(scope="module")
def items(runner):
    return runner.extract_items(DOC)


class TestTheTwentyMinuteTutorial:
    def test_every_directive_is_one_the_runner_knows(self, items):
        directives = [text for kind, text in items if kind == "directive"]
        assert directives, "the tutorial promises results; the runner is what checks them"
        for text in directives:
            assert text.split()[0] in KNOWN_DIRECTIVES, text

    def test_the_seed_fixture_exists_and_is_entries(self, items):
        seeds = [t.split() for k, t in items if k == "directive" and t.startswith("seed ")]
        assert len(seeds) == 1
        fixture = REPO / seeds[0][1]
        assert list(fixture.rglob("*.md")), f"{fixture} has no entries to seed"

    def test_every_search_the_tutorial_promises_names_the_entries_it_finds(self, items):
        """A search block with no `expect-ids` after it proves only that it exits 0."""
        unasserted = []
        for index, (kind, text) in enumerate(items):
            if kind != "block" or "pyrite search" not in text or "--mode semantic" in text:
                continue
            follows = items[index + 1] if index + 1 < len(items) else ("", "")
            if follows[0] != "directive" or not follows[1].startswith("expect-ids"):
                unasserted.append(text.strip().splitlines()[0])
        # Allowed: the first keyword demo ("andon", which Act one repeats under an
        # assertion) and the semantic-only block (needs a model CI does not have).
        assert unasserted == ['pyrite search "andon" -k tps --fields id,title'], unasserted

    def test_ci_and_the_release_script_run_it(self):
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        release = (REPO / "scripts" / "release.py").read_text()
        assert "run_tutorial.sh docs/tutorials/pyrite-in-20-minutes.md" in ci
        assert "docs/tutorials/pyrite-in-20-minutes.md" in release


class TestSearchesAreAssertedOneByOne:
    """#583/#43: a zero-result search used to hide behind a sibling, and the
    semantic and hybrid modes were exempt from any assertion."""

    @staticmethod
    def _out(count):
        return json.dumps({"count": count, "results": [{"id": str(i)} for i in range(count)]})

    def test_two_searches_in_one_block_are_refused(self, runner):
        block = 'pyrite search "algorithm" -k kb\npyrite search "mathematics" -k kb\n'
        with pytest.raises(runner.TutorialError, match="its own block"):
            runner.assert_search_returned_results(block, self._out(1))

    def test_a_keyword_search_with_no_results_fails(self, runner):
        with pytest.raises(runner.TutorialError, match="0 results"):
            runner.assert_search_returned_results('pyrite search "x" -k kb', self._out(0))

    def test_a_semantic_search_with_no_results_and_no_warning_fails(self, runner):
        with pytest.raises(runner.TutorialError, match="0 results"):
            runner.assert_search_returned_results(
                'pyrite search "x" -k kb --mode semantic', self._out(0)
            )

    def test_a_semantic_search_with_no_results_passes_when_it_says_why(self, runner):
        runner.assert_search_returned_results(
            'pyrite search "x" -k kb --mode semantic',
            self._out(0),
            "warning: semantic leg skipped: run `pyrite index embed` to build them",
        )

    def test_a_keyword_search_does_not_get_the_semantic_pass(self, runner):
        with pytest.raises(runner.TutorialError, match="0 results"):
            runner.assert_search_returned_results(
                'pyrite search "x" -k kb', self._out(0), "run `pyrite index embed`"
            )

    def test_the_semantic_exemption_is_gone(self, runner):
        assert not hasattr(runner, "EMBEDDING_MODE_RE")


class TestAndListsAreRefused:
    def test_a_failing_first_command_would_otherwise_pass(self, runner):
        with pytest.raises(runner.TutorialError, match="&&"):
            runner.assert_no_and_lists("cd nowhere && true\n")

    def test_and_inside_quotes_or_a_comment_is_text(self, runner):
        runner.assert_no_and_lists('git commit -m "a && b"  # then c && d\n')

    def test_errexit_really_does_not_stop_a_failed_first_command(self):
        """The reason for the rule, pinned against the shell itself."""
        import subprocess

        result = subprocess.run(
            ["bash", "-c", "set -e; false && true; echo ran"], text=True, capture_output=True
        )
        assert result.returncode == 0 and "ran" in result.stdout

    def test_the_pages_the_runner_runs_have_none(self, runner):
        for doc in (
            REPO / "docs" / "getting-started.md",
            REPO / "docs" / "tutorials" / "pyrite-in-20-minutes.md",
        ):
            for block in runner.extract_blocks(doc):
                if not runner.should_skip(block):
                    runner.assert_no_and_lists(block)

    def test_getting_started_has_one_search_per_block(self, runner):
        for block in runner.extract_blocks(REPO / "docs" / "getting-started.md"):
            assert len(runner.SEARCH_RE.findall(block)) <= 1


class TestFirstHourClaims:
    """#583: claims the first hour makes that a test can hold to the code."""

    def test_the_readme_types_no_counts_of_tests_or_adrs(self):
        readme = (REPO / "README.md").read_text()
        assert "~4100" not in readme
        assert not __import__("re").search(r"\(\d+ ADRs\)", readme)

    def test_no_first_hour_page_says_hosted_instance(self):
        for name in ("README.md", "docs/getting-started.md"):
            assert "hosted instance" not in (REPO / name).read_text(), name

    @pytest.mark.parametrize("name", ["render.yaml", "fly.toml"])
    def test_deploy_templates_say_the_operator_creates_the_first_admin(self, name):
        text = (REPO / name).read_text()
        assert "pyrite-admin user create" in text
        assert "first account registered" not in text

    def test_help_opens_with_the_one_tagline(self):
        from typer.testing import CliRunner

        from pyrite.cli import TAGLINE, app

        assert "citizen journalists" not in TAGLINE
        out = CliRunner().invoke(app, ["--help"]).output
        assert TAGLINE in " ".join(out.split())
