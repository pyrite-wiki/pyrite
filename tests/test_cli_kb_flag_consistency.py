"""Contract test for `-k` / `--kb` flag consistency across common commands.

Regression coverage for `consistent-kb-flag-across-commands`: the common
read/index commands that filter by KB must all accept the `-k` / `--kb`
shorthand (the bug's original repro `index sync -k` used to fail with
"No such option: -k"). This locks the flag so it can't regress.

Commands that take a single *required* KB as a positional argument
(`kb info`, `qa assess`, `index reconcile`, …) are intentionally excluded —
positional is their established convention and converting them would break
existing callers.
"""

import pytest
from typer.testing import CliRunner

from tests.cli_help import plain

from pyrite.cli import app

runner = CliRunner()

# (argv-prefix, ...) for commands that should expose -k / --kb.
_KB_FLAG_COMMANDS = [
    ["search", "--help"],
    ["get", "--help"],
    ["index", "sync", "--help"],
    ["index", "embed", "--help"],
    ["tags", "--help"],
    ["backlinks", "--help"],
    ["task", "list", "--help"],
    ["task", "get", "--help"],
]


@pytest.mark.cli
@pytest.mark.parametrize("argv", _KB_FLAG_COMMANDS, ids=lambda a: " ".join(a[:-1]))
def test_command_accepts_kb_shorthand(argv):
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.stdout
    help_text = plain(result.stdout)
    assert "--kb" in help_text, f"{' '.join(argv[:-1])} missing --kb"
    assert "-k" in help_text, f"{' '.join(argv[:-1])} missing -k shorthand"
