# pyrite-mcp

Standalone MCP (Model Context Protocol) server for [Pyrite](https://github.com/pyrite-wiki/pyrite) knowledge bases.

Provides a lightweight package for running the Pyrite MCP server without the full web server or CLI dependencies.

## Installation

`pyrite-mcp` is not published on PyPI. Install it and Pyrite from the same
release tag, the way the [main README](../README.md) installs Pyrite:

```bash
pip install "pyrite @ git+https://github.com/pyrite-wiki/pyrite@v0.26.3" \
  "pyrite-mcp @ git+https://github.com/pyrite-wiki/pyrite@v0.26.3#subdirectory=pyrite-mcp"
```

Name both. At v0.26.3, pyrite-mcp's own requirement is `pyrite>=0.20.0`,
which pip would otherwise look up on PyPI, where the `pyrite` name is not
this project's (ADR-0025).

## Usage

### Initialize a knowledge base

```bash
pyrite-mcp init my-kb
```

### Start the MCP server

```bash
pyrite-mcp serve --tier read
```

### Tiers

- **read**: Search, browse, retrieve entries
- **write** (default): Read + create/update/delete entries
- **admin**: Write + KB management, index rebuild

## Claude Code Integration

Add to your MCP configuration:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "pyrite-mcp",
      "args": ["serve", "--tier", "write"]
    }
  }
}
```
