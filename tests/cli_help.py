"""Plain text for CLI assertions, independent of terminal colour settings."""

import re

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def plain(text: str) -> str:
    r"""Strip ANSI styling, which Rich can insert between the two flag dashes.

    ``NO_COLOR=1`` cannot prevent this when ``FORCE_COLOR``/``TERM`` wins;
    normalize output instead of depending on the test runner's environment.
    Reproduce the trap with::

        FORCE_COLOR=1 TERM=xterm-256color .venv/bin/python -c "from typer.testing import CliRunner; \
        from pyrite.cli import app; \
        r = CliRunner().invoke(app, ['task', 'list', '--help']); \
        print('--priority' in r.stdout)"
    """
    return _ANSI.sub("", text)
