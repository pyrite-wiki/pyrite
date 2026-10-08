"""
Extension management CLI commands.

Commands: init, install, list, uninstall
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..utils.errors import PARTIAL_EXIT, cli_error

logger = logging.getLogger(__name__)

extension_app = typer.Typer(help="Extension management")
console = Console()


def _format_output(data: dict, fmt: str) -> str | None:
    from .output import format_output

    return format_output(data, fmt)


def _normalize_name(name: str) -> dict:
    """Normalize extension name into various forms."""
    snake = name.replace("-", "_").lower()
    parts = snake.split("_")
    pascal = "".join(p.capitalize() for p in parts)
    pkg = f"pyrite_{snake}"
    return {"snake": snake, "pascal": pascal, "pkg": pkg, "original": name}


@extension_app.command("init")
def extension_init(
    name: str = typer.Argument(..., help="Extension name (e.g. 'my-plugin')"),
    path: Path | None = typer.Option(
        None, "--path", "-p", help="Output directory (default: ./<name>)"
    ),
    types: str | None = typer.Option(
        None, "--types", "-t", help="Comma-separated entry type names to scaffold"
    ),
    description: str = typer.Option(
        "A Pyrite extension", "--description", "-d", help="Extension description"
    ),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Scaffold a new Pyrite extension with plugin boilerplate."""
    names = _normalize_name(name)
    snake = names["snake"]
    pascal = names["pascal"]
    pkg = names["pkg"]

    # Resolve output path
    out_path = (path or Path(name)).expanduser().resolve()

    # Idempotency check
    if (out_path / "pyproject.toml").exists():
        cli_error(
            f"{out_path}/pyproject.toml already exists",
            output_format,
            error_code="VALIDATION_FAILED",
            suggestion="choose a different --path or remove the existing extension",
        )

    # Parse types
    type_names = [t.strip() for t in types.split(",") if t.strip()] if types else []

    # Create directory structure
    src_dir = out_path / "src" / pkg
    tests_dir = out_path / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    # 1. pyproject.toml
    (out_path / "pyproject.toml").write_text(f'''[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{pkg.replace("_", "-")}"
version = "0.1.0"
description = "{description}"
requires-python = ">=3.11"
dependencies = []

[project.entry-points."pyrite.plugins"]
{snake} = "{pkg}.plugin:{pascal}Plugin"
''')

    # 2. __init__.py
    (src_dir / "__init__.py").write_text(f'''"""{pascal} extension for Pyrite."""

from .plugin import {pascal}Plugin

__all__ = ["{pascal}Plugin"]
''')

    # 3. plugin.py
    type_import = "\nfrom .entry_types import ENTRY_TYPES" if type_names else ""
    type_return = "ENTRY_TYPES" if type_names else "{}"
    validator_import = "\nfrom .validators import get_validators" if type_names else ""
    validator_return = "get_validators()" if type_names else "[]"
    preset_import = f"\nfrom .preset import {snake.upper()}_PRESET"
    preset_return = f'{{"{snake}": {snake.upper()}_PRESET}}'

    (src_dir / "plugin.py").write_text(f'''"""{pascal} plugin for Pyrite."""
{type_import}{validator_import}{preset_import}


class {pascal}Plugin:
    """Pyrite plugin: {description}."""

    name = "{snake}"

    def get_entry_types(self):
        """Return custom entry types."""
        return {type_return}

    def get_validators(self):
        """Return custom validators."""
        return {validator_return}

    def get_kb_presets(self):
        """Return KB presets."""
        return {preset_return}

    def get_cli_commands(self):
        """Return CLI commands."""
        return []

    def get_mcp_tools(self, tier: str):
        """Return MCP tools for the given tier."""
        return {{}}

    def set_context(self, ctx):
        """Receive shared context."""
        self.ctx = ctx
''')

    # 4. entry_types.py
    if type_names:
        type_classes = []
        type_map_entries = []
        for t in type_names:
            t_snake = t.replace("-", "_").lower()
            t_pascal = "".join(p.capitalize() for p in t_snake.split("_"))
            type_classes.append(f'''
class {t_pascal}Entry:
    """Custom entry type: {t}."""

    entry_type = "{t_snake}"

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
''')
            type_map_entries.append(f'    "{t_snake}": {t_pascal}Entry,')

        types_content = '"""Custom entry types."""\n'
        types_content += "\n".join(type_classes)
        types_content += "\n\nENTRY_TYPES = {\n"
        types_content += "\n".join(type_map_entries)
        types_content += "\n}\n"
        (src_dir / "entry_types.py").write_text(types_content)
    else:
        (src_dir / "entry_types.py").write_text('"""Custom entry types."""\n\nENTRY_TYPES = {}\n')

    # 5. validators.py
    (src_dir / "validators.py").write_text(f'''"""Custom validators for {snake} extension."""


def _validate_{snake}(entry_type, data, context):
    """Validate {snake} entries."""
    issues = []
    # Add custom validation logic here
    return issues


def get_validators():
    """Return list of validator functions."""
    return [_validate_{snake}]
''')

    # 6. preset.py
    if type_names:
        preset_types = {}
        preset_dirs = []
        for t in type_names:
            t_snake = t.replace("-", "_").lower()
            plural = t_snake + "s"
            preset_types[t_snake] = {
                "description": f"{t} entry",
                "required": ["title"],
                "optional": [],
                "subdirectory": f"{plural}/",
            }
            preset_dirs.append(plural)

        # Build preset dict string manually for readability
        types_str = "{\n"
        for t_name, t_def in preset_types.items():
            types_str += f'        "{t_name}": {{\n'
            types_str += f'            "description": "{t_def["description"]}",\n'
            types_str += '            "required": ["title"],\n'
            types_str += '            "optional": [],\n'
            types_str += f'            "subdirectory": "{t_def["subdirectory"]}",\n'
            types_str += "        }},\n"
        types_str += "    }"
        dirs_str = str(preset_dirs)
    else:
        types_str = "{}"
        dirs_str = "[]"

    (src_dir / "preset.py").write_text(f'''"""{pascal} KB preset definition."""

{snake.upper()}_PRESET = {{
    "name": "my-{snake.replace("_", "-")}",
    "description": "{description}",
    "types": {types_str},
    "policies": {{}},
    "validation": {{"enforce": False}},
    "directories": {dirs_str},
}}
''')

    # 7. tests/test_{snake}.py
    (tests_dir / f"test_{snake}.py").write_text(f'''"""Tests for {snake} extension."""

from {pkg}.plugin import {pascal}Plugin


class Test{pascal}Plugin:
    def test_plugin_name(self):
        plugin = {pascal}Plugin()
        assert plugin.name == "{snake}"

    def test_plugin_entry_types(self):
        plugin = {pascal}Plugin()
        types = plugin.get_entry_types()
        assert isinstance(types, dict)

    def test_plugin_validators(self):
        plugin = {pascal}Plugin()
        validators = plugin.get_validators()
        assert isinstance(validators, list)

    def test_plugin_kb_presets(self):
        plugin = {pascal}Plugin()
        presets = plugin.get_kb_presets()
        assert "{snake}" in presets

    def test_plugin_mcp_tools(self):
        plugin = {pascal}Plugin()
        tools = plugin.get_mcp_tools("read")
        assert isinstance(tools, dict)
''')

    # Count generated files
    generated_files = [
        "pyproject.toml",
        f"src/{pkg}/__init__.py",
        f"src/{pkg}/plugin.py",
        f"src/{pkg}/entry_types.py",
        f"src/{pkg}/validators.py",
        f"src/{pkg}/preset.py",
        f"tests/test_{snake}.py",
    ]

    result = {
        "status": "created",
        "name": name,
        "package": pkg,
        "path": str(out_path),
        "files": generated_files,
        "types": type_names,
    }

    formatted = _format_output(result, output_format)
    if formatted is not None:
        typer.echo(formatted)
    else:
        console.print(f"[green]Created extension:[/green] {name}")
        console.print(f"  Package: {pkg}")
        console.print(f"  Path: {out_path}")
        console.print(f"  Files: {len(generated_files)}")
        if type_names:
            console.print(f"  Types: {', '.join(type_names)}")
        console.print(f"\n  Install with: pyrite extension install {out_path}")


# Runs in a fresh interpreter. Loads only the named distribution's own
# ``pyrite.plugins`` entry points, the way the registry does (load, then
# instantiate), and prints one JSON object: {"found": n, "errors": [...]}.
_VERIFY_SCRIPT = """
import json, sys
from importlib.metadata import distribution
eps = [e for e in distribution(sys.argv[1]).entry_points if e.group == "pyrite.plugins"]
errors = []
for ep in eps:
    try:
        ep.load()()
    except BaseException as exc:
        errors.append(f"entry point {ep.name!r} ({ep.value}): {type(exc).__name__}: {exc}")
print(json.dumps({"found": len(eps), "errors": errors}))
"""


def _verify_distribution(dist_name: str) -> list[str]:
    """Errors from loading ``dist_name``'s own plugin entry points; empty means verified.

    Unrelated installed plugins are not looked at: their failures are not this
    install's (#786).
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _VERIFY_SCRIPT, dist_name],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"could not start the verification interpreter: {e}"]
    try:
        report = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        detail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        return [f"verification interpreter failed (exit {proc.returncode}): {detail}"]
    if report["errors"]:
        return list(report["errors"])
    if not report["found"]:
        return [f"{dist_name} declares no 'pyrite.plugins' entry points"]
    return []


@extension_app.command("install")
def extension_install(
    path: Path = typer.Argument(..., help="Path to extension directory"),
    verify: bool = typer.Option(False, "--verify", help="Verify plugin loads after install"),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Install a Pyrite extension from a local path."""
    path = path.expanduser().resolve()

    if not (path / "pyproject.toml").exists():
        cli_error(
            f"No pyproject.toml found at {path}",
            output_format,
            error_code="NOT_FOUND",
            suggestion="point install at an extension directory created by `pyrite extension init`",
        )

    # Parse plugin name from pyproject.toml
    plugin_name = None
    try:
        import tomllib

        with open(path / "pyproject.toml", "rb") as f:
            toml_data = tomllib.load(f)
        plugin_name = toml_data.get("project", {}).get("name", path.name)
    except Exception:
        logger.debug("Could not parse pyproject.toml for %s, using directory name", path)
        plugin_name = path.name

    # Install via pip in editable mode
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", str(path)],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        cli_error(
            f"pip install failed:\n{result.stderr}",
            output_format,
            error_code="ERROR",
        )

    # Verify the plugin loads, in a fresh interpreter: an editable install's
    # finder only takes effect when an interpreter starts, so this process
    # cannot see what pip just added (#786).
    verified = False
    verify_errors: list[str] = []
    if verify:
        verify_errors = _verify_distribution(plugin_name)
        verified = not verify_errors
        for err in verify_errors:
            console.print(f"[yellow]Warning:[/yellow] Plugin verification failed: {err}")

    output = {
        "status": "installed",
        "name": plugin_name,
        "path": str(path),
        "verified": verified if verify else None,
    }
    if verify and verify_errors:
        output["verify_errors"] = verify_errors

    formatted = _format_output(output, output_format)
    if formatted is not None:
        typer.echo(formatted)
    else:
        console.print(f"[green]Installed:[/green] {plugin_name}")
        console.print(f"  Path: {path}")
        if verify:
            if verified:
                console.print("  [green]Verified: plugin loaded successfully[/green]")
            else:
                console.print("  [yellow]Verification: could not confirm plugin loaded[/yellow]")

    if verify and not verified:
        # Installed, but not confirmed to load: the verification was asked for (#526).
        raise typer.Exit(PARTIAL_EXIT)


@extension_app.command("list")
def extension_list(
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """List installed Pyrite extensions."""
    try:
        from ..plugins import get_registry

        registry = get_registry()
        plugin_names = registry.list_plugins()
    except Exception:
        logger.warning("Failed to load plugin registry", exc_info=True)
        plugin_names = []

    if not plugin_names:
        result = {"plugins": [], "count": 0}
        formatted = _format_output(result, output_format)
        if formatted is not None:
            typer.echo(formatted)
        else:
            console.print("[yellow]No extensions installed.[/yellow]")
        return

    plugins_data = []
    for name in plugin_names:
        try:
            plugin = registry.get_plugin(name)
            entry_types = (
                list(plugin.get_entry_types().keys()) if hasattr(plugin, "get_entry_types") else []
            )
            tool_count = 0
            if hasattr(plugin, "get_mcp_tools"):
                try:
                    tools = plugin.get_mcp_tools("read")
                    tool_count = len(tools) if tools else 0
                except Exception:
                    logger.warning("Failed to get MCP tools for plugin %s", name, exc_info=True)
            plugins_data.append(
                {
                    "name": name,
                    "entry_types": entry_types,
                    "tool_count": tool_count,
                }
            )
        except Exception:
            logger.warning("Failed to inspect plugin %s", name, exc_info=True)
            plugins_data.append({"name": name, "entry_types": [], "tool_count": 0})

    result = {"plugins": plugins_data, "count": len(plugins_data)}

    formatted = _format_output(result, output_format)
    if formatted is not None:
        typer.echo(formatted)
        return

    table = Table(title="Installed Extensions")
    table.add_column("Name", style="cyan")
    table.add_column("Entry Types")
    table.add_column("MCP Tools", justify="right")

    for p in plugins_data:
        table.add_row(
            p["name"],
            ", ".join(p["entry_types"]) or "-",
            str(p["tool_count"]),
        )

    console.print(table)


@extension_app.command("uninstall")
def extension_uninstall(
    name: str = typer.Argument(..., help="Extension name to uninstall"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    output_format: str = typer.Option(
        "json", "--format", help="Output format: json, rich, markdown, csv, yaml"
    ),
):
    """Uninstall a Pyrite extension."""
    pkg_name = f"pyrite-{name.replace('_', '-')}"

    if not force:
        if not typer.confirm(f"Uninstall extension '{name}' (package: {pkg_name})?"):
            raise typer.Abort()

    result = subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", pkg_name, "-y"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        cli_error(
            f"pip uninstall failed:\n{result.stderr}",
            output_format,
            error_code="ERROR",
        )

    output = {"status": "uninstalled", "name": name, "package": pkg_name}

    formatted = _format_output(output, output_format)
    if formatted is not None:
        typer.echo(formatted)
    else:
        console.print(f"[green]Uninstalled:[/green] {name} ({pkg_name})")
