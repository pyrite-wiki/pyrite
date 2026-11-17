"""Tests for scripts/check_ratchets_only_shrink.py (#1608, slice 1).

The property: a registered ratchet list only shrinks. Removing rows passes
(and two pull requests removing different rows never touch the same line);
adding a row fails unless a commit in BASE..HEAD carries the trailer
``Ratchet-raise: <list> <reason>``. Every case builds a throwaway git
repository in ``tmp_path``; the real repository is never touched.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_ratchets_only_shrink.py"
sys.path.insert(0, str(SCRIPT.parent))

import check_ratchets_only_shrink as crs  # noqa: E402

FILE = "ratchet.py"
NAME = "NOT_YET"
REG = [(FILE, NAME)]


class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, capture_output=True, text=True, check=True
        ).stdout.strip()

    def write(self, text: str, path: str = FILE) -> None:
        (self.path / path).write_text(text)

    def commit(self, message: str = "change") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run(self, base: str, head: str = "HEAD", registry=REG) -> int:
        return crs.check_range(base, head, registry=registry, cwd=self.path)


@pytest.fixture
def repo(tmp_path):
    return Repo(tmp_path)


def literal(rows, form="set", annotated=False, name=NAME):
    body = ", ".join(repr(r) for r in rows)
    if form == "set":
        value = "{" + body + "}" if rows else "set()"
    elif form == "list":
        value = "[" + body + "]"
    elif form == "tuple":
        value = "(" + body + ("," if len(rows) == 1 else "") + ")"
    elif form == "dict":
        value = "{" + ", ".join(f"{r!r}: 'why'" for r in rows) + "}"
    else:
        raise AssertionError(form)
    ann = ": object" if annotated else ""
    return f"{name}{ann} = {value}\n"


def two_commits(repo, before, after, message="change"):
    repo.write(before)
    base = repo.commit("base")
    repo.write(after)
    repo.commit(message)
    return base


ROWS = [("a x", "limit"), ("b y", "limit"), ("c z", "offset")]


@pytest.mark.parametrize(
    "kept",
    [ROWS[:2], ROWS[1:], [ROWS[0]], []],
    ids=["one-removed", "one-removed-other", "several-removed", "all-removed"],
)
def test_removing_rows_passes(repo, kept):
    base = two_commits(repo, literal(ROWS), literal(kept))
    assert repo.run(base) == 0


def test_adding_a_row_without_trailer_fails_and_names_the_row_and_list(repo, capsys):
    base = two_commits(repo, literal(ROWS), literal(ROWS + [("d w", "limit")]))
    assert repo.run(base) == 1
    err = capsys.readouterr().err
    assert NAME in err and FILE in err
    assert "('d w', 'limit')" in err
    assert "Ratchet-raise: NOT_YET <reason>" in err


def test_each_added_row_is_printed_on_its_own_line(repo, capsys):
    base = two_commits(repo, literal(ROWS), literal(ROWS + [("d w", "limit"), ("e v", "limit")]))
    assert repo.run(base) == 1
    lines = capsys.readouterr().err.splitlines()
    assert sum("('d w', 'limit')" in ln for ln in lines) == 1
    assert sum("('e v', 'limit')" in ln for ln in lines) == 1
    assert not any("('d w', 'limit')" in ln and "('e v', 'limit')" in ln for ln in lines)


GROWN = ROWS + [("d w", "limit")]


def test_trailer_on_the_adding_commit_passes(repo):
    base = two_commits(
        repo,
        literal(ROWS),
        literal(GROWN),
        "change\n\nwhy\n\nRatchet-raise: NOT_YET a new command ships unbounded for now",
    )
    assert repo.run(base) == 0


def test_trailer_on_a_different_commit_of_the_range_passes(repo):
    repo.write(literal(ROWS))
    base = repo.commit("base")
    repo.write(literal(GROWN))
    repo.commit("adds the row")
    repo.commit("later\n\nbody\n\nRatchet-raise: NOT_YET the row is deliberate")
    assert repo.run(base) == 0


@pytest.mark.parametrize(
    "message",
    [
        "change\n\nbody\n\nRatchet-raise: OTHER_LIST a reason",
        "change\n\nbody\n\nRatchet-raise: NOT_YET",
        "change\n\nbody\n\nRatchet-raise: NOT_YET    ",
        "change\n\nRatchet-raise: NOT_YET a reason\n\nand then more prose after it",
        "change\n\nNote: Ratchet-raise: NOT_YET a reason, said in prose",
        "Ratchet-raise: NOT_YET a reason in the subject",
        "change",
    ],
    ids=[
        "other-list",
        "no-reason",
        "blank-reason",
        "not-the-final-paragraph",
        "inside-prose",
        "in-subject",
        "no-trailer",
    ],
)
def test_a_trailer_that_does_not_count_fails(repo, message):
    base = two_commits(repo, literal(ROWS), literal(GROWN), message)
    assert repo.run(base) == 1


def test_one_removed_and_a_different_one_added_fails(repo, capsys):
    swapped = [ROWS[0], ROWS[1], ("d w", "limit")]
    assert len(swapped) == len(ROWS)
    base = two_commits(repo, literal(ROWS), literal(swapped))
    assert repo.run(base) == 1
    assert "('d w', 'limit')" in capsys.readouterr().err


def test_reordering_and_comment_changes_pass(repo):
    before = "# first\n" + literal(ROWS)
    after = "# a different comment\n" + literal(list(reversed(ROWS))).replace(
        "{", "{\n    # note\n", 1
    )
    base = two_commits(repo, before, after)
    assert repo.run(base) == 0


@pytest.mark.parametrize("annotated", [False, True], ids=["plain", "annotated"])
@pytest.mark.parametrize("form", ["set", "list", "tuple", "dict"])
def test_every_container_form_is_read(repo, form, annotated):
    before = literal(ROWS, form, annotated)
    after = literal(GROWN, form, annotated)
    base = two_commits(repo, before, after)
    assert repo.run(base) == 1  # the added row was seen
    assert crs.read_rows(after, NAME) == set(GROWN)
    assert crs.read_rows(before, NAME) == set(ROWS)
    repo.write(literal(ROWS[:1], form, annotated))
    repo.commit("shrink")
    assert repo.run(base) == 0


def test_the_name_missing_at_head_fails_named(repo, capsys):
    base = two_commits(repo, literal(ROWS), literal(ROWS, name="RENAMED"))
    assert repo.run(base) == 1
    err = capsys.readouterr().err
    assert FILE in err and NAME in err


def test_the_file_missing_at_head_fails_named(repo, capsys):
    repo.write(literal(ROWS))
    base = repo.commit("base")
    repo.write("x = 1\n", "other.py")
    repo.git("rm", "-q", FILE)
    repo.commit("removes the file")
    assert repo.run(base) == 1
    err = capsys.readouterr().err
    assert FILE in err and NAME in err


def test_a_value_that_is_not_a_literal_fails_named(repo, capsys):
    base = two_commits(repo, literal(ROWS), "x = [1]\nNOT_YET = set(x)\n")
    assert repo.run(base) == 1
    err = capsys.readouterr().err
    assert FILE in err and NAME in err


def test_a_syntax_error_at_head_fails_named(repo, capsys):
    base = two_commits(repo, literal(ROWS), "NOT_YET = {\n")
    assert repo.run(base) == 1
    assert NAME in capsys.readouterr().err


def test_a_list_missing_at_base_needs_the_trailer(repo):
    repo.write("x = 1\n")
    base = repo.commit("base")
    repo.write(literal(ROWS))
    head = repo.commit("introduces the list")
    assert repo.run(base, head) == 1
    repo.commit("more\n\nbody\n\nRatchet-raise: NOT_YET a seeded list of existing rows")
    assert repo.run(base) == 0


def test_a_file_missing_at_base_counts_as_empty(repo):
    repo.write("x = 1\n", "other.py")
    base = repo.commit("base")
    repo.write(literal(ROWS))
    repo.commit("adds the file")
    assert repo.run(base) == 1


def test_a_merge_commit_that_brings_no_new_row_passes(repo):
    repo.write(literal(ROWS))
    base = repo.commit("base")
    repo.git("checkout", "-q", "-b", "topic")
    repo.write(literal(ROWS[1:]))
    repo.commit("topic removes a row")
    repo.git("checkout", "-q", "main")
    repo.write(literal(ROWS) + "# dev moved\n", "notes.py")
    repo.commit("dev moves on")
    repo.git("checkout", "-q", "topic")
    repo.git("merge", "-q", "--no-edit", "main")
    assert len(repo.git("rev-list", "--merges", f"{base}..HEAD").splitlines()) == 1
    assert repo.run(base) == 0


def test_an_unknown_revision_fails_with_gits_error(repo):
    repo.write(literal(ROWS))
    repo.commit("base")
    with pytest.raises(subprocess.CalledProcessError):
        repo.run("no-such-revision")


def test_the_trailer_must_name_the_list_exactly(repo):
    base = two_commits(
        repo, literal(ROWS), literal(GROWN), "c\n\nb\n\nRatchet-raise: NOT_YET_MORE a reason"
    )
    assert repo.run(base) == 1


# ---- the script itself --------------------------------------------------


def run_script(repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo.path, capture_output=True, text=True
    )


def test_the_script_exits_2_on_usage_error(repo):
    for args in ([], ["--range"], ["--range", "a"], ["--bogus", "a", "b"]):
        r = run_script(repo, *args)
        assert r.returncode == 2, args


def test_the_script_with_the_real_registry_fails_when_its_files_are_missing(repo):
    """Run in a repository that has none of the registered files: each is named."""
    repo.write("x = 1\n", "other.py")
    base = repo.commit("base")
    repo.commit("empty")
    r = run_script(repo, "--range", base, "HEAD")
    assert r.returncode == 1
    for path, name in crs.REGISTRY:
        assert path in r.stderr and name in r.stderr


def test_the_script_with_an_unknown_revision_exits_non_zero(repo):
    repo.write("x = 1\n")
    repo.commit("base")
    r = run_script(repo, "--range", "nope", "HEAD")
    assert r.returncode not in (0, 2)
    assert "nope" in r.stderr


# ---- the registry -------------------------------------------------------


def test_every_registered_list_exists_in_the_working_tree_and_is_readable():
    root = SCRIPT.parent.parent
    assert crs.REGISTRY, "the registry is empty"
    for path, name in crs.REGISTRY:
        source = (root / path).read_text()
        rows = crs.read_rows(source, name)
        assert rows, f"{path}:{name} is readable but empty; delete its registry entry"
