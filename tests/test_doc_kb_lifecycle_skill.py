"""The lifecycle skill's commands and cross-KB recipe are executable (#1662)."""

import re
import shlex
from pathlib import Path

import yaml
from typer import BadParameter
from typer.core import TyperGroup
from typer.main import get_command

from pyrite.cli import app
from pyrite.config import KBConfig
from tests.outcome_walk import _typers_click_exception

SKILL = Path(__file__).resolve().parents[1] / ".claude/skills/kb-lifecycle"


def commands(text):
    # Inline command snippets and standalone command lines in fenced examples.
    for match in re.finditer(r"`(pyrite [^`\n]+)`|^\s*(pyrite [^`\n]+)$", text, re.M):
        yield match.group(1) or match.group(2)


def parse_command(example):
    args = shlex.split(re.sub(r"<[^>]+>", "example", example))[1:]
    # Parser internals inspect the real CLI grammar without running callbacks or requiring a KB.
    command = get_command(app)
    reference = not any(arg.startswith("-") for arg in args) and "<" not in example
    while True:
        # Parse options/argument arity without invoking callbacks or command bodies.
        # Placeholders cannot be type-checked, and bare command mentions are not
        # complete invocations, but both still have to name real commands/options.
        ctx = command.context_class(command)
        values, rest, _ = command.make_parser(ctx).parse_args(args)
        if isinstance(command, TyperGroup):
            if not rest:
                return
            _, command, args = command.resolve_command(ctx, rest)
            continue
        if rest and not command.allow_extra_args:
            raise BadParameter(f"Unexpected arguments: {rest}")
        if not reference:
            for param in command.params:
                if param.required and values.get(param.name) in (None, ()):
                    raise BadParameter(
                        f"Missing required parameter: {param.name}", ctx=ctx, param=param
                    )
        return


def test_all_lifecycle_commands_parse():
    errors = []
    count = 0
    for path in sorted(SKILL.rglob("*.md")):
        for example in commands(path.read_text()):
            count += 1
            try:
                parse_command(example)
            except _typers_click_exception() as exc:
                errors.append(f"{path.name}: {example}: {exc.format_message()}")
    assert count > 20, "The command extractor stopped finding examples"
    assert not errors, "\n".join(errors)


def test_phase_five_creates_a_real_backlink(cli_world, tmp_path):
    world = cli_world(
        kbs=[
            KBConfig(name="source-kb", path=tmp_path / "source"),
            KBConfig(name="target-kb", path=tmp_path / "target"),
        ]
    )
    for kb, title in [("source-kb", "Source entry"), ("target-kb", "Target entry")]:
        result = world.cli(*["create", "-k", kb, "-t", "note", "--title", title, "-b", "Example"])
        assert result.exit_code == 0, result.output
    text = (SKILL / "SKILL.md").read_text().split("### Phase 5:")[1].split("### Phase 6:")[0]
    replacements = {
        "<kb>": str(tmp_path / "source"),
        "<kb-name>": "source-kb",
        "<source-entry-id>": "source-entry",
        "<target-entry-id>": "target-entry",
        "<target-kb-name>": "target-kb",
        "<relation-type>": "related_to",
        "<one-line description of the connection>": "A documented connection",
        "<target>": "target-entry",
        "<target-kb>": "target-kb",
        "<source-kb>": "source-kb",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    recipe = re.search(r"```yaml\n(.*?)```", text, re.S).group(1)
    assert isinstance(yaml.safe_load(recipe), list)
    intent = tmp_path / "source/cross-kb-links.yaml"
    intent.write_text(recipe)
    recipe_commands = [
        c
        for c in commands(text)
        if c.startswith(("pyrite links bulk-create ", "pyrite backlinks "))
    ]
    assert len(recipe_commands) == 3, "Need dry-run, apply and backlink verification"
    # Run index/QA examples against the same scratch KB; index adds --no-embed
    # rather than executing the plain documented form, to avoid model downloads.
    main_text = (SKILL / "SKILL.md").read_text()
    for example in sorted(set(commands(main_text))):
        if not example.startswith(("pyrite index build ", "pyrite qa fix ", "pyrite qa gaps ")):
            continue
        args = shlex.split(example.replace("<kb-name>", "source-kb"))[1:]
        if args[:2] == ["index", "build"] and "--no-embed" not in args:
            args.append("--no-embed")
        result = world.cli(*args)
        assert result.exit_code == 0, result.output
    source = next((tmp_path / "source").rglob("source-entry.md"))
    before = source.read_bytes()
    for index, example in enumerate(recipe_commands):
        result = world.cli(*shlex.split(example)[1:])
        assert result.exit_code == 0, result.output
        if index == 0:
            assert source.read_bytes() == before
        if index == 1:
            assert source.read_bytes() != before
        if index == 2:
            assert "source-entry" in result.output


def test_lifecycle_scope_and_creation_guidance():
    text = (SKILL / "SKILL.md").read_text()
    assert text.count("10-15") == 1
    assert "**A KB needs 30-50+ entries to justify its existence.**" in text
    assert "## First pass" in text
    assert ".research/" in text
    for path in SKILL.rglob("*.md"):
        assert "may not persist" not in path.read_text(), path
    prompt = (SKILL / "references/subagent-prompt.md").read_text()
    assert "--body-file" in prompt and "--field" in prompt


def test_subagent_create_example_persists_fields_and_body(cli_world, tmp_path):
    kb = tmp_path / "example-kb"
    kb.mkdir()
    (kb / "kb.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "example-kb",
                "types": {
                    "concept": {
                        "optional": ["research_status"],
                        "fields": {"research_status": {"type": "text", "enum": ["draft", "stub"]}},
                    }
                },
            }
        )
    )
    world = cli_world(kbs=[KBConfig(name="example-kb", path=kb)])
    body = tmp_path / "body.md"
    body.write_text("A sourced first draft.\n")
    prompt = (SKILL / "references/subagent-prompt.md").read_text()
    example = next(c for c in commands(prompt) if c.startswith("pyrite create -k"))
    for old, new in {
        "<kb-name>": "example-kb",
        "<entry-type>": "concept",
        "<Title>": "Example concept",
        "<body-path>": str(body),
    }.items():
        example = example.replace(old, new)
    result = world.cli(*shlex.split(example)[1:])
    assert result.exit_code == 0, result.output
    entry = next(kb.rglob("example-concept.md")).read_text()
    assert yaml.safe_load(entry.split("---")[1])["research_status"] == "draft"
    assert body.read_text() in entry
