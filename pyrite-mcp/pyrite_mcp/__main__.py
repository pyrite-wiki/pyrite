"""Standalone MCP server entry point for Pyrite knowledge bases.

Usage:
    pyrite-mcp serve [--tier read|write|admin]   (default: write)
    pyrite-mcp init <name> [--path <dir>]
"""

import argparse
import sys
from pathlib import Path


def cmd_serve(args):
    """Start the MCP server over stdio."""
    from pyrite.server.mcp_server import PyriteMCPServer

    server = PyriteMCPServer(tier=args.tier)
    try:
        server.run_stdio()
    finally:
        server.close()


def cmd_init(args):
    """Initialize a new knowledge base directory."""
    from pyrite.config import load_config, save_config
    from pyrite.storage.database import PyriteDB
    from pyrite.storage.index import IndexManager

    kb_path = Path(args.path or ".") / args.name
    kb_path.mkdir(parents=True, exist_ok=True)

    # Create a default kb.yaml
    kb_yaml = kb_path / "kb.yaml"
    if not kb_yaml.exists():
        kb_yaml.write_text(
            f"name: {args.name}\n"
            f"description: Knowledge base created by pyrite-mcp\n"
            f"types:\n"
            f"  note:\n"
            f"    required: [title]\n"
            f"  event:\n"
            f"    required: [title, date]\n"
        )

    # Register and build index
    config = load_config()
    from pyrite.config import KBConfig, KBType

    if not config.get_kb(args.name):
        config.add_kb(KBConfig(name=args.name, path=kb_path.resolve(), kb_type=KBType.RESEARCH))
        save_config(config)

    db = PyriteDB(config.settings.index_path)
    try:
        index_mgr = IndexManager(db, config)
        index_mgr.sync_incremental(args.name)
    finally:
        db.close()

    print(f"Initialized KB '{args.name}' at {kb_path.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        prog="pyrite-mcp",
        description="Standalone MCP server for Pyrite knowledge bases",
    )
    subparsers = parser.add_subparsers(dest="command")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start MCP server over stdio")
    serve_parser.add_argument(
        "--tier",
        choices=["read", "write", "admin"],
        # write, as `pyrite mcp` (ADR-0006; #582).
        default="write",
        help="Access tier (default: write)",
    )

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new knowledge base")
    init_parser.add_argument("name", help="Name for the knowledge base")
    init_parser.add_argument("--path", help="Parent directory (default: current dir)")

    args = parser.parse_args()

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "init":
        cmd_init(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
