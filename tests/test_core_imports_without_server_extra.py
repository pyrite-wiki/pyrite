"""The base CLI import path must not require the `server` extra.

`pip install pyrite` (no extras) installs only the core dependencies; `pip
install pyrite[cli]` adds `typer`/`rich` for the CLI entry point. Neither
pulls in `fastapi`, `jinja2`, or any other package listed only under the
`server` extra in `pyproject.toml`. If a core import path reaches one of
those modules at import time, a plain CLI install breaks with
`ModuleNotFoundError` the moment any command is run (#533 CI: the `kb` job
installs pyrite without `server` and runs `pyrite schema validate kb/`,
which crashed importing `pyrite.cli` -> ... -> `jinja2`).

Each module is blocked by setting `sys.modules[name] = None` *before* the
import under test: the import machinery treats a `None` entry as "this
module does not exist" and raises `ModuleNotFoundError` immediately, without
needing the package to be literally uninstalled from the test environment.
Run in a subprocess so the poisoned `sys.modules` never leaks into the rest
of the suite.
"""

import subprocess
import sys

# Every package listed only under `[project.optional-dependencies].server`
# in pyproject.toml (never in core `dependencies`, `cli`, or another extra
# a core import path might legitimately reach).
SERVER_EXTRA_MODULES = [
    "fastapi",
    "starlette",  # fastapi's dependency; blocked too in case something imports it directly
    "uvicorn",
    "jinja2",
    "slowapi",
    "bcrypt",
    "watchdog",
    "alembic",
    "markdownify",
    "httpx",
]


def _block_modules_and_import(modules: list[str], import_stmt: str) -> subprocess.CompletedProcess:
    block = "\n".join(f"sys.modules[{m!r}] = None" for m in modules)
    code = f"import sys\n{block}\n{import_stmt}\n"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_pyrite_cli_imports_without_jinja2():
    """`import pyrite.cli` must not require jinja2 (the `server` extra)."""
    result = _block_modules_and_import(["jinja2"], "import pyrite.cli")
    assert result.returncode == 0, (
        f"pyrite.cli import failed with jinja2 blocked:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


def test_kb_registry_service_imports_without_jinja2():
    """`kb_registry_service` (imported by the CLI's context) must not need jinja2."""
    result = _block_modules_and_import(["jinja2"], "import pyrite.services.kb_registry_service")
    assert result.returncode == 0, (
        f"kb_registry_service import failed with jinja2 blocked:\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


def test_pyrite_cli_imports_without_any_server_extra_module():
    """The whole base CLI import path avoids every `server`-extra module.

    Blocks fastapi, starlette, uvicorn, jinja2, slowapi, bcrypt, watchdog,
    alembic, markdownify and httpx all at once and imports `pyrite.cli`.
    """
    result = _block_modules_and_import(SERVER_EXTRA_MODULES, "import pyrite.cli")
    assert result.returncode == 0, (
        "pyrite.cli import failed with the server extra blocked:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
