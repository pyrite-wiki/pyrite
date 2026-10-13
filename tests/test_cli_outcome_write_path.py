"""The write path on the outcome contract (ADR-0046, slice 2).

``pyrite create|add|update|rename|link`` and ``pyrite links
check|bulk-create|suggest|discover|batch-suggest|orphans|asymmetric`` (twelve
commands) return an
``Outcome`` (or raise a ``PyriteError``) and the root renders it: no command of
these two modules prints its own result or picks its own exit code. ``get``
(markdown and csv are taught for it) and ``delete`` (it asks a question, which a
command on the contract may not) stay off the contract; the tests below pin that.

``tests/test_cli_outcome.py`` plants every way out in every command that
declares the shared option, so this file does not repeat that walk. It holds
what only these commands know: which refusal each class of failure is, that
``create --link`` with a dangling link is partial, what a bulk batch of zero,
all-refused and some-refused items exits, and a missing KB in both formats.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import typer.main
import yaml
from typer.testing import CliRunner

from pyrite.cli import app
from pyrite.cli.outcome import Outcome
from pyrite.cli.output import declares_shared_format
from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.storage.database import PyriteDB

runner = CliRunner()

ENTRY_COMMANDS = ["create", "add", "update", "rename", "link"]
LINK_COMMANDS = [
    "check",
    "bulk-create",
    "suggest",
    "discover",
    "batch-suggest",
    "orphans",
    "asymmetric",
]
WRITE_PATH = [(c,) for c in ENTRY_COMMANDS] + [("links", c) for c in LINK_COMMANDS]


def _leaf(path: tuple[str, ...]):
    command = typer.main.get_command(app)
    for part in path:
        command = command.commands[part]
    return command


# --------------------------------------------------------------------------
# Registry: the twelve commands are on the contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", WRITE_PATH, ids=[" ".join(p) for p in WRITE_PATH])
def test_the_command_declares_the_shared_option_and_is_annotated_outcome(path):
    command = _leaf(path)
    assert declares_shared_format(command), f"pyrite {' '.join(path)} has no shared --format"
    import inspect

    annotation = inspect.signature(command.callback).return_annotation
    assert annotation in (Outcome, "Outcome"), (
        f"pyrite {' '.join(path)} is annotated {annotation!r}"
    )


def test_the_private_exit_helpers_are_gone():
    from pyrite.cli import entry_commands

    assert not hasattr(entry_commands, "_cli_error")
    assert not hasattr(entry_commands, "_refusal_exit")


# --------------------------------------------------------------------------
# A scratch config with a free-form KB and a typed one
# --------------------------------------------------------------------------

TYPED = """name: typed
kb_type: generic
types:
  ticket:
    description: A ticket
    fields:
      kind:
        type: select
        options: [bug, feature]
"""


@pytest.fixture
def env(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    typed = tmp_path / "typed"
    typed.mkdir()
    (tmp_path / "ro").mkdir()
    (typed / "kb.yaml").write_text(TYPED, encoding="utf-8")
    db_path = tmp_path / "index.db"
    PyriteDB(db_path).close()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="notes", path=notes, kb_type=KBType.GENERIC),
            KBConfig(name="typed", path=typed, kb_type=KBType.GENERIC),
            KBConfig(name="ro", path=tmp_path / "ro", kb_type=KBType.GENERIC),
        ],
        settings=Settings(index_path=db_path),
    )
    return {"config": config, "tmp": tmp_path, "notes": notes}


def run(env, *args, input=None):
    with patch("pyrite.cli.context.load_config", return_value=env["config"]):
        with patch("pyrite.cli.load_config", return_value=env["config"]):
            return runner.invoke(app, list(args), input=input)


def seed(env, title="Hello", kb="notes"):
    out = run(env, "create", "-k", kb, "-t", "note", "--title", title)
    assert out.exit_code == 0, out.output


def make_read_only(env):
    """Seed the `ro` KB, then flip it read-only (a KB cannot be seeded after)."""
    seed(env, "Hello", kb="ro")
    seed(env, "World", kb="ro")
    for kb in env["config"].knowledge_bases:
        if kb.name == "ro":
            kb.read_only = True


def doc(result):
    """The one document a machine format puts on stdout."""
    return json.loads(result.stdout)


# --------------------------------------------------------------------------
# Regime: refusals, by the class's own code, in json and in rich
# --------------------------------------------------------------------------

REFUSALS = [
    # (id, argv, code, hint): `hint` is a substring of the suggestion dev gave for the
    # same input, or None when it gave none.
    ("create-kb", ["create", "-k", "nokb", "-t", "note", "--title", "x"], "KB_NOT_FOUND", None),
    ("update-kb", ["update", "hello", "-k", "nokb", "--title", "x"], "KB_NOT_FOUND", None),
    ("link-kb", ["link", "hello", "hello", "-k", "nokb"], "KB_NOT_FOUND", None),
    ("rename-kb", ["rename", "hello", "other", "-k", "nokb"], "KB_NOT_FOUND", None),
    ("add-kb", ["add", "{f_ok}", "-k", "nokb"], "KB_NOT_FOUND", None),
    (
        "bulk-kb",
        ["links", "bulk-create", "{links}", "-k", "nokb"],
        "KB_NOT_FOUND",
        "pyrite kb list",
    ),
    ("create-ro", ["create", "-k", "ro", "-t", "note", "--title", "x"], "KB_READ_ONLY", None),
    ("update-ro", ["update", "hello", "-k", "ro", "--title", "x"], "KB_READ_ONLY", None),
    ("rename-ro", ["rename", "hello", "other", "-k", "ro"], "KB_READ_ONLY", None),
    ("link-ro", ["link", "hello", "world", "-k", "ro"], "KB_READ_ONLY", None),
    ("add-ro", ["add", "{f_ok}", "-k", "ro"], "KB_READ_ONLY", None),
    ("bulk-ro", ["links", "bulk-create", "{links}", "-k", "ro"], "KB_READ_ONLY", None),
    ("update-missing", ["update", "nope", "-k", "notes", "--title", "x"], "NOT_FOUND", None),
    ("link-missing", ["link", "hello", "nope", "-k", "notes"], "NOT_FOUND", None),
    ("rename-missing", ["rename", "nope", "other", "-k", "notes"], "NOT_FOUND", None),
    ("rename-same", ["rename", "hello", "hello", "-k", "notes"], "VALIDATION_FAILED", None),
    ("rename-taken", ["rename", "hello", "world", "-k", "notes"], "VALIDATION_FAILED", None),
    (
        "create-exists",
        ["create", "-k", "notes", "-t", "note", "--title", "Hello"],
        "ENTRY_EXISTS",
        None,
    ),
    (
        "create-undeclared",
        ["create", "-k", "typed", "-t", "note", "--title", "Nope"],
        "UNDECLARED_TYPE",
        "pyrite kb schema show",
    ),
    ("add-undeclared", ["add", "{f_ok}", "-k", "typed"], "UNDECLARED_TYPE", "--allow-undeclared"),
    (
        "create-schema",
        ["create", "-k", "typed", "-t", "ticket", "--title", "T", "--field", "kind=chore"],
        "SCHEMA_VIOLATION",
        None,
    ),
    ("create-no-title", ["create", "-k", "notes"], "VALIDATION_FAILED", None),
    (
        "create-bad-field",
        ["create", "-k", "notes", "--title", "F", "--field", "novalue"],
        "VALIDATION_FAILED",
        None,
    ),
    (
        "update-bad-field",
        ["update", "hello", "-k", "notes", "--field", "novalue"],
        "VALIDATION_FAILED",
        None,
    ),
    (
        "create-truncated",
        ["create", "-k", "notes", "--title", "Cut", "--field", "body_truncated=true"],
        "VALIDATION_FAILED",
        "kb_read_body",
    ),
    ("add-truncated", ["add", "{f_trunc}", "-k", "notes"], "VALIDATION_FAILED", "kb_read_body"),
    ("add-no-file", ["add", "nofile.md", "-k", "notes"], "NOT_FOUND", None),
    (
        "bulk-no-file",
        ["links", "bulk-create", "nofile.yaml", "-k", "notes"],
        "NOT_FOUND",
        "existing YAML file",
    ),
    (
        "asymmetric-both",
        ["links", "asymmetric", "-k", "notes", "--kb-a", "notes"],
        "VALIDATION_FAILED",
        None,
    ),
    ("asymmetric-neither", ["links", "asymmetric"], "VALIDATION_FAILED", None),
    ("suggest-missing", ["links", "suggest", "ghost", "-k", "notes"], "NOT_FOUND", None),
    ("discover-missing", ["links", "discover", "ghost", "-k", "notes"], "NOT_FOUND", None),
]


def _argv(env, argv):
    tmp = env["tmp"]
    (tmp / "links.yaml").write_text(
        yaml.dump([{"source": "hello", "target": "hello"}]), encoding="utf-8"
    )
    (tmp / "f_ok.md").write_text("---\ntype: note\ntitle: Added\n---\nb\n", encoding="utf-8")
    (tmp / "f_trunc.md").write_text(
        "---\ntype: note\ntitle: Cut\nbody_truncated: true\n---\nb\n", encoding="utf-8"
    )
    subs = {
        "{links}": str(tmp / "links.yaml"),
        "{f_ok}": str(tmp / "f_ok.md"),
        "{f_trunc}": str(tmp / "f_trunc.md"),
    }
    return [subs.get(a, a) for a in argv]


@pytest.mark.parametrize(
    ("argv", "code", "hint"), [r[1:] for r in REFUSALS], ids=[r[0] for r in REFUSALS]
)
def test_a_refusal_has_its_class_code_and_dev_s_hint_in_json_and_rich(env, argv, code, hint):
    seed(env)
    seed(env, "World")
    make_read_only(env)
    argv = _argv(env, argv)

    machine = run(env, *argv, "--format", "json")
    assert machine.exit_code == 1, machine.output
    payload = doc(machine)
    assert payload["error_code"] == code
    assert payload["outcome"] == "error"
    if hint:
        assert hint in payload["suggestion"], payload
    else:
        assert "suggestion" not in payload, payload

    rich = run(env, *argv, "--format", "rich")
    assert rich.exit_code == 1, rich.output
    assert f"ERROR [{code}]" in rich.stdout
    if hint:
        assert "hint:" in rich.stdout and hint in rich.stdout
    else:
        assert "hint:" not in rich.stdout


def test_the_undeclared_type_hint_is_phrased_for_the_cli(env):
    out = run(env, "create", "-k", "typed", "-t", "note", "--title", "N", "--format", "json")
    hint = doc(out)["suggestion"]
    assert "pyrite kb schema show" in hint and "--allow-undeclared" in hint


def test_add_validate_only_with_errors_is_a_validation_refusal(env):
    bad = env["tmp"] / "bad.md"
    bad.write_text("---\ntype: ticket\ntitle: Bad\nkind: chore\n---\nbody\n", encoding="utf-8")
    out = run(env, "add", str(bad), "-k", "typed", "--validate-only", "--format", "json")
    assert out.exit_code == 1, out.output
    assert doc(out)["error_code"] == "VALIDATION_FAILED"
    assert not list((env["tmp"] / "typed").glob("**/*.md"))


@pytest.mark.control(reason="control: get is deliberately left on the legacy path")
def test_get_stays_off_the_contract_and_keeps_markdown():
    """`get --format markdown` is taught in the tutorial; the shared option has no
    markdown. `get` moves with `pyrite-read get` (slice 3)."""
    assert not declares_shared_format(_leaf(("get",)))


@pytest.mark.control(reason="control: get behaves as before")
def test_get_still_refuses_a_missing_entry_in_json_and_rich(env):
    seed(env)
    machine = run(env, "get", "nope", "-k", "notes")
    assert machine.exit_code == 1 and doc(machine)["error_code"] == "NOT_FOUND"
    rich = run(env, "get", "nope", "-k", "notes", "--format", "rich")
    assert rich.exit_code == 1 and "ERROR [NOT_FOUND]" in rich.stdout
    assert run(env, "get", "hello", "-k", "notes", "--format", "markdown").exit_code == 0


# --------------------------------------------------------------------------
# What a success prints: one document in json, the old text in rich
# --------------------------------------------------------------------------


def test_create_update_get_round_trip_in_json_and_rich(env):
    made = run(env, "create", "-k", "notes", "-t", "note", "--title", "Hello")
    assert made.exit_code == 0, made.output
    assert doc(made)["outcome"] == "done"
    assert doc(made)["entry_id"] == "hello"

    rich = run(env, "create", "-k", "notes", "-t", "note", "--title", "Second", "--format", "rich")
    assert rich.exit_code == 0 and "Created: second" in rich.stdout

    upd = run(env, "update", "hello", "-k", "notes", "--title", "Hi")
    assert doc(upd)["updated"] is True and doc(upd)["entry_id"] == "hello"
    assert (
        "Updated: hello"
        in run(env, "update", "hello", "-k", "notes", "-i", "7", "--format", "rich").stdout
    )

    got = run(env, "get", "hello", "-k", "notes")
    assert doc(got)["title"] == "Hi"
    assert "Hi" in run(env, "get", "hello", "-k", "notes", "--format", "rich").stdout


@pytest.mark.control(reason="control: delete asks a question, so it stays off the contract")
def test_delete_stays_off_the_contract_and_asks_as_dev_did(env):
    """A command on the contract does not prompt (ADR-0046, amendment); the prompt
    rule is the maintainer's open question, so `delete` is exactly dev's."""
    assert not declares_shared_format(_leaf(("delete",)))
    seed(env)
    declined = run(env, "delete", "hello", "-k", "notes", input="n\n")
    assert declined.exit_code == 1 and "Aborted" in declined.output, declined.output
    assert run(env, "get", "hello", "-k", "notes").exit_code == 0

    # An answer that is not yes or no is asked again, as typer.confirm does.
    accepted = run(env, "delete", "hello", "-k", "notes", input="maybe\ny\n")
    assert accepted.exit_code == 0 and "Deleted: hello" in accepted.output, accepted.output
    assert run(env, "get", "hello", "-k", "notes").exit_code == 1


@pytest.mark.control(reason="control: delete refuses as dev did")
def test_delete_refuses_a_missing_entry_and_kb_as_dev_did(env):
    missing = run(env, "delete", "nope", "-k", "notes", "--force")
    assert missing.exit_code == 1 and "ERROR [NOT_FOUND]" in missing.stdout
    nokb = run(env, "delete", "hello", "-k", "nokb", "--force")
    assert nokb.exit_code == 1 and "ERROR [KB_NOT_FOUND]" in nokb.stdout


def test_rename_dry_run_and_link_report(env):
    seed(env)
    seed(env, "World")
    linked = run(env, "link", "hello", "world", "-k", "notes", "-r", "informs")
    assert doc(linked)["outcome"] == "done"
    again = run(env, "link", "hello", "world", "-k", "notes", "-r", "informs", "--format", "rich")
    assert "Already linked" in again.stdout
    plan = run(env, "rename", "hello", "hi", "-k", "notes", "--dry-run", "--format", "rich")
    assert "Would rename" in plan.stdout and "Dry run" in plan.stdout


def test_a_bidirectional_link_whose_inverse_fails_is_partial(env):
    seed(env)
    seed(env, "World")
    from pyrite.services.kb_service import KBService

    real = KBService.add_link
    calls = []

    def flaky(self, *a, **kw):
        calls.append(a)
        if len(calls) == 2:
            raise ValueError("inverse refused")
        return real(self, *a, **kw)

    with patch.object(KBService, "add_link", flaky):
        out = run(env, "link", "hello", "world", "-k", "notes", "--bidi")
    assert out.exit_code == 3, out.output
    payload = doc(out)
    assert payload["outcome"] == "partial"
    assert "inverse refused" in payload["failed"][0]["error"]


def test_create_template_is_the_skeleton_in_rich_and_a_document_in_json(env):
    rich = run(env, "create", "--template", "-t", "note", "--format", "rich")
    assert rich.exit_code == 0 and rich.stdout.startswith("---\n")
    machine = run(env, "create", "--template", "-t", "note")
    assert machine.exit_code == 0 and "id:" in doc(machine)["template"]


# --------------------------------------------------------------------------
# Regime: create --link with a dangling link is partial, and says what finishes it
# --------------------------------------------------------------------------


def test_create_with_a_dangling_link_is_partial_with_the_finish_up_command(env):
    seed(env, "Target A")
    args = [
        "create",
        "-k",
        "notes",
        "-t",
        "note",
        "--title",
        "Dangling",
        "--link",
        "target-a:informs",
        "--link",
        "does-not-exist:informs",
    ]
    machine = run(env, *args)
    assert machine.exit_code == 3, machine.output
    payload = doc(machine)
    assert payload["outcome"] == "partial" and payload["entry_id"] == "dangling"
    assert [f["target"] for f in payload["failed_links"]] == ["does-not-exist"]
    assert (
        payload["failed_links"][0]["finish_up"]
        == "pyrite link dangling does-not-exist -r informs -k notes"
    )
    assert (env["notes"] / "notes" / "dangling.md").exists() or list(
        env["notes"].rglob("dangling.md")
    )

    rich = run(
        env, *args[:-4], "--title", "Dangling2", "--link", "gone:informs", "--format", "rich"
    )
    assert rich.exit_code == 3, rich.output
    assert "pyrite link dangling2 gone -r informs -k notes" in rich.stdout


def test_create_whose_links_all_resolve_is_done(env):
    seed(env, "Target A")
    out = run(
        env, "create", "-k", "notes", "-t", "note", "--title", "Whole", "--link", "target-a:informs"
    )
    assert out.exit_code == 0 and doc(out)["outcome"] == "done"


# --------------------------------------------------------------------------
# Regime: the links bulk-create batch
# --------------------------------------------------------------------------


def _bulk(env, specs, *extra):
    path = env["tmp"] / "batch.yaml"
    path.write_text(yaml.dump(specs), encoding="utf-8")
    return run(env, "links", "bulk-create", str(path), "-k", "notes", *extra)


def test_a_batch_of_zero_items_is_done(env):
    out = _bulk(env, [])
    assert out.exit_code == 0, out.output
    payload = doc(out)
    assert payload["outcome"] == "done"
    assert (payload["created"], payload["skipped"], payload["failed"]) == (0, 0, 0)


def test_a_batch_where_every_item_is_refused_is_nothing(env):
    seed(env)
    out = _bulk(
        env, [{"source": "ghost-a", "target": "hello"}, {"source": "ghost-b", "target": "hello"}]
    )
    assert out.exit_code == 1, out.output
    payload = doc(out)
    assert payload["outcome"] == "nothing" and payload["failed"] == 2


def test_a_batch_where_some_items_are_refused_is_partial(env):
    seed(env)
    seed(env, "World")
    out = _bulk(
        env, [{"source": "hello", "target": "world"}, {"source": "ghost", "target": "world"}]
    )
    assert out.exit_code == 3, out.output
    payload = doc(out)
    assert payload["outcome"] == "partial"
    assert (payload["created"], payload["failed"]) == (1, 1)
    assert [r["status"] for r in payload["results"]] == ["created", "failed"]

    rich = _bulk(
        env,
        [{"source": "hello", "target": "world"}, {"source": "ghost", "target": "world"}],
        "--format",
        "rich",
    )
    assert rich.exit_code == 3 and "1 failed" in rich.stdout


def test_a_dry_run_batch_writes_nothing(env):
    seed(env)
    seed(env, "World")
    out = _bulk(env, [{"source": "hello", "target": "world"}], "--dry-run", "--format", "rich")
    assert out.exit_code == 0 and "Dry run" in out.stdout
    assert "informs" not in next(iter(env["notes"].rglob("hello.md"))).read_text()


# --------------------------------------------------------------------------
# The read half of links: json by default, rich on request
# --------------------------------------------------------------------------


def test_links_reads_default_to_json_and_keep_their_rich_view(env):
    seed(env)
    for name, extra in [("check", []), ("orphans", ["-k", "notes"])]:
        machine = run(env, "links", name, *extra)
        assert machine.exit_code == 0, machine.output
        assert doc(machine)["outcome"] == "done"
    rich = run(env, "links", "check", "--format", "rich")
    assert "No broken links found." in rich.stdout


def test_links_suggest_on_a_missing_entry_is_not_found(env):
    out = run(env, "links", "suggest", "ghost", "-k", "notes")
    assert out.exit_code == 1 and doc(out)["error_code"] == "NOT_FOUND"
    out = run(env, "links", "discover", "ghost", "-k", "notes", "--format", "rich")
    assert out.exit_code == 1 and "ERROR [NOT_FOUND]" in out.stdout


# --------------------------------------------------------------------------
# What a success is in json, by default
# --------------------------------------------------------------------------


def test_add_rename_and_link_succeed_as_one_json_document_by_default(env):
    seed(env)
    seed(env, "World")
    md = env["tmp"] / "a.md"
    md.write_text("---\ntype: note\ntitle: Added\n---\nb\n", encoding="utf-8")

    added = run(env, "add", str(md), "-k", "notes")
    assert added.exit_code == 0, added.output
    assert doc(added) == {
        "added": True,
        "entry_id": "added",
        "entry_type": "note",
        "outcome": "done",
    }
    valid = run(env, "add", str(md), "-k", "typed", "--validate-only", "--allow-undeclared")
    assert doc(valid)["valid"] is True and doc(valid)["outcome"] == "done"

    linked = run(env, "link", "hello", "world", "-k", "notes", "-r", "informs")
    assert doc(linked) == {
        "links": [
            {
                "created": True,
                "source": "hello",
                "relation": "informs",
                "target": "world",
                "target_kb": "notes",
            }
        ],
        "outcome": "done",
    }

    renamed = run(env, "rename", "hello", "hi", "-k", "notes")
    payload = doc(renamed)
    assert payload["renamed"] is True and payload["new_id"] == "hi"
    assert payload["outcome"] == "done"


@pytest.mark.parametrize(
    ("argv", "keys"),
    [
        (["links", "suggest", "hello", "-k", "notes"], {"entry_id", "count", "suggestions"}),
        (["links", "discover", "hello", "-k", "notes"], {"entry_id", "count", "discoveries"}),
        (
            ["links", "batch-suggest", "--source-kb", "notes", "--target-kb", "notes"],
            {"source_kb", "count", "pairs"},
        ),
        (["links", "asymmetric", "-k", "notes"], {"kb_a", "count", "asymmetric_links"}),
        (["links", "orphans", "-k", "notes"], {"kb_name", "count", "orphans"}),
        (["links", "check"], {"missing_targets", "total_references", "targets"}),
    ],
    ids=["suggest", "discover", "batch-suggest", "asymmetric", "orphans", "check"],
)
def test_the_links_reads_succeed_as_one_json_document_by_default(env, argv, keys):
    seed(env)
    seed(env, "World")
    out = run(env, *argv)
    assert out.exit_code == 0, out.output
    payload = doc(out)
    assert keys <= set(payload), payload
    assert payload["outcome"] == "done"


# --------------------------------------------------------------------------
# Round 2: one name for a link's target KB, and a failed check exits 1
# --------------------------------------------------------------------------


def _target_kb_names(node, path="$"):
    """Every (path, key) in a document whose key names a KB and whose mapping
    also names a link target (``target``/``target_id``): that key is the link's
    target KB and must be ``target_kb``."""
    found = []
    if isinstance(node, dict):
        if "target" in node or "target_id" in node:
            found += [(path, k) for k in node if k in ("kb", "kb_name", "tkb", "target_kb_name")]
        for key, value in node.items():
            found += _target_kb_names(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _target_kb_names(value, f"{path}[{i}]")
    return found


def _link_family_outcomes(env):
    """The JSON of every outcome in the link family that can name a target."""
    seed(env)
    seed(env, "World")
    ok = run(env, "create", "-k", "notes", "-t", "note", "--title", "Ghosty", "--body", "[[ghost]]")
    assert ok.exit_code == 0, ok.output
    specs = env["tmp"] / "specs.yaml"
    specs.write_text("- source: hello\n  target: world\n  relation: informs\n", encoding="utf-8")
    argvs = {
        "link": ["link", "hello", "world", "-k", "notes", "-r", "informs", "--bidi"],
        "link-missing-inverse": ["link", "hello", "world", "-k", "notes", "-r", "cites"],
        "bulk-create": ["links", "bulk-create", str(specs), "-k", "notes", "--dry-run"],
        "check": ["links", "check"],
    }
    return {name: run(env, *argv) for name, argv in argvs.items()}


def test_every_link_outcome_names_the_targets_kb_target_kb(env):
    """A walk, not one assertion per command: a link command that joins the
    family under another name (`kb`) fails here without a new test."""
    outs = _link_family_outcomes(env)
    seen = 0
    for name, out in outs.items():
        document = doc(out)
        assert _target_kb_names(document) == [], (name, document)
        seen += json.dumps(document).count('"target_kb"')
    assert seen >= 4, "the walk reached no link payloads"
    # The bidirectional link is the one with a payload of each direction.
    bidi = doc(outs["link"])["links"]
    assert [link["target_kb"] for link in bidi] == ["notes", "notes"]


def test_a_failed_bidi_inverse_names_its_target_kb_too(env):
    seed(env)
    seed(env, "World")
    from pyrite.services.kb_service import KBService

    real = KBService.add_link
    calls = []

    def flaky(self, *a, **kw):
        calls.append(a)
        if len(calls) == 2:
            raise ValueError("inverse refused")
        return real(self, *a, **kw)

    with patch.object(KBService, "add_link", flaky):
        out = run(env, "link", "hello", "world", "-k", "notes", "--bidi")
    document = doc(out)
    assert out.exit_code == 3
    assert document["failed"][0]["target_kb"] == "notes"
    assert _target_kb_names(document) == []


def _broken_link_env(env):
    seed(env)
    made = run(
        env, "create", "-k", "notes", "-t", "note", "--title", "Ghosty", "--body", "[[ghost]]"
    )
    assert made.exit_code == 0, made.output


def test_links_check_exits_1_with_the_findings_when_a_link_is_broken(env):
    _broken_link_env(env)
    out = run(env, "links", "check")
    document = doc(out)
    assert out.exit_code == 1, out.output
    assert document["outcome"] == "nothing"
    assert document["missing_targets"] == 1
    assert [t["target_id"] for t in document["targets"]] == ["ghost"]
    assert document["targets"][0]["references"][0]["source_id"] == "ghosty"
    rich = run(env, "links", "check", "--format", "rich")
    assert rich.exit_code == 1 and "ghost" in rich.stdout


@pytest.mark.control(
    reason="control: the clean case exited 0 before the change and must keep doing so"
)
def test_links_check_exits_0_when_no_link_is_broken(env):
    seed(env)
    out = run(env, "links", "check")
    assert out.exit_code == 0, out.output
    document = doc(out)
    assert document["outcome"] == "done" and document["missing_targets"] == 0
    assert run(env, "links", "check", "--format", "rich").exit_code == 0
