# Connecting Gemini CLI to Pyrite MCP

Pyrite exposes an MCP (Model Context Protocol) server that Google's Gemini CLI can use to search, read, and write your knowledge bases.

## Prerequisites

- Pyrite installed from source (`pip install -e ".[all]"` — no PyPI wheel yet; a bare `pip install -e .` has no CLI, so `pyrite init` and `pyrite mcp` fail)
- At least one knowledge base initialized (`pyrite init`)
- [Gemini CLI](https://github.com/google-gemini/gemini-cli) installed

## Gemini CLI Configuration

Gemini CLI reads MCP server config from `~/.gemini/settings.json`.

### Stdio transport (local)

Add a `pyrite` entry under `mcpServers` in `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "pyrite",
      "args": ["mcp"]
    }
  }
}
```

For read-only access:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "pyrite",
      "args": ["mcp", "--tier", "read"]
    }
  }
}
```

### HTTP/SSE transport (remote Pyrite instance)

`pyrite serve` mounts an MCP server over SSE at `GET /mcp/sse` (messages go to `POST /mcp/messages/`). Nothing answers at `/mcp` itself, and every request needs `Authorization: Bearer <key>` or `X-API-Key: <key>` unless the server has no auth. Gemini CLI's `url` setting is its SSE transport:

```json
{
  "mcpServers": {
    "pyrite": {
      "url": "http://<host>:8088/mcp/sse",
      "headers": {"Authorization": "Bearer <key>"}
    }
  }
}
```

The tier you get is the role of that key (see **Tool not available** below).

### Optional settings

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "pyrite",
      "args": ["mcp"],
      "timeout": 30000,
      "trust": true,
      "includeTools": ["kb_search", "kb_get", "kb_list"]
    }
  }
}
```

- `timeout`: Request timeout in milliseconds (default: 600000)
- `trust`: Skip tool confirmation prompts (default: false)
- `includeTools` / `excludeTools`: Allowlist or blocklist specific tools

See the [Gemini CLI MCP docs](https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md) for all supported fields.

## Gemini Code Assist (VS Code)

Gemini Code Assist in VS Code also supports MCP servers. Add the following to your VS Code `settings.json`:

```json
{
  "geminicodeassist.mcpServers": {
    "pyrite": {
      "command": "pyrite",
      "args": ["mcp"]
    }
  }
}
```

See the [Gemini Code Assist MCP docs](https://developers.google.com/gemini-code-assist/docs/use-agentic-chat-pair-programmer) for details.

## Verification

Start Gemini CLI and ask it to use a Pyrite tool:

```
gemini "List all knowledge bases using the pyrite MCP tools"
```

You should see Gemini call `kb_list` and return your knowledge bases.

## Troubleshooting

**"Server failed to start"** -- Verify `pyrite mcp` runs successfully on its own in a terminal. Ensure the `pyrite` command is on your PATH.

**"Command not found"** -- If you installed Pyrite in a virtualenv, use the full path:

```json
{
  "mcpServers": {
    "pyrite": {
      "command": "/path/to/.venv/bin/pyrite",
      "args": ["mcp"]
    }
  }
}
```

**Tools not showing up** -- Restart Gemini CLI after editing `settings.json`. The config is read at startup.

**Tool not available** -- Check the tier. Over stdio, `pyrite mcp` and `pyrite-admin mcp` default to `write` if `--tier` is omitted; pass `--tier admin` if you need index and git operations. Over HTTP there is no tier option (`pyrite-server` has none): the tier is the role of the API key or user you connect with, and it is `admin` when the server has no credentials at all.
