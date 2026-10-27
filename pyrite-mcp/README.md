# pyrite-mcp

Standalone MCP (Model Context Protocol) server for [Pyrite](https://github.com/pyrite-wiki/pyrite) knowledge bases.

Provides a lightweight package for running the Pyrite MCP server without the full web server or CLI dependencies.

## Installation

`pyrite-mcp` is not published on PyPI. Install it from the release tag, the
same way the [main README](../README.md) installs Pyrite:

```bash
pip install "pyrite-mcp @ git+https://github.com/pyrite-wiki/pyrite@v0.26.0#subdirectory=pyrite-mcp"
```

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
