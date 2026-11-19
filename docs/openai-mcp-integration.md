# Connecting OpenAI Codex CLI to Pyrite MCP

Pyrite exposes an MCP (Model Context Protocol) server that OpenAI's Codex CLI can use to search, read, and write your knowledge bases.

## Prerequisites

- Pyrite installed from source (`pip install -e ".[all]"` — no PyPI wheel yet; a bare `pip install -e .` has no CLI, so `pyrite init` and `pyrite mcp` fail)
- At least one knowledge base initialized (`pyrite init`)
- [Codex CLI](https://github.com/openai/codex) installed

## Codex CLI Configuration

Codex stores MCP server config in `~/.codex/config.toml` (global) or `.codex/config.toml` (per-project, trusted projects only).

### Option 1: Add via CLI

```bash
codex mcp add pyrite -- pyrite mcp
```

To restrict Pyrite to read-only access:

```bash
codex mcp add pyrite -- pyrite mcp --tier read
```

### Option 2: Edit config.toml directly

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.pyrite]
command = "pyrite"
args = ["mcp"]
```

For read-only access:

```toml
[mcp_servers.pyrite]
command = "pyrite"
args = ["mcp", "--tier", "read"]
```

### Remote Pyrite instance (HTTP transport)

`pyrite serve` mounts an MCP server over **SSE only**: `GET /mcp/sse` opens the stream and `POST /mcp/messages/` takes the messages. Nothing answers at `/mcp` itself, and every request needs `Authorization: Bearer <key>` or `X-API-Key: <key>` unless the server has no auth. Codex's `url` setting expects streamable HTTP, so it cannot connect to a remote Pyrite directly. Use the local command above (`pyrite mcp`, run on the machine that holds the KBs), or put a stdio-to-SSE bridge in front, for example [`mcp-remote`](https://www.npmjs.com/package/mcp-remote):

```toml
[mcp_servers.pyrite]
command = "npx"
args = ["-y", "mcp-remote", "http://<host>:8088/mcp/sse", "--header", "Authorization: Bearer <key>"]
```

The tier you get is the role of that key (see **Tool not available** below).

### Optional settings

```toml
[mcp_servers.pyrite]
command = "pyrite"
args = ["mcp"]
startup_timeout_sec = 15
tool_timeout_sec = 120
enabled_tools = ["kb_search", "kb_get", "kb_list"]  # restrict to specific tools
```

See the [Codex MCP docs](https://developers.openai.com/codex/mcp/) for all supported fields.

## Verification

Start Codex and ask it to use a Pyrite tool:

```
codex "List all knowledge bases using the pyrite MCP tools"
```

You should see Codex call `kb_list` and return your knowledge bases. If the server fails to start, Codex will report the error on launch.

## Troubleshooting

**"Server failed to start"** -- Verify `pyrite mcp` runs successfully on its own in a terminal. Ensure the `pyrite` command is on your PATH (or use the full path, e.g., `/path/to/.venv/bin/pyrite`).

**"Command not found"** -- If you installed Pyrite in a virtualenv, use the full path to the binary:

```toml
[mcp_servers.pyrite]
command = "/path/to/.venv/bin/pyrite"
args = ["mcp"]
```

**Timeout errors** -- Increase `startup_timeout_sec` if Pyrite takes time to initialize (e.g., building the index on first run).

**Tool not available** -- Check the tier. Over stdio, `pyrite mcp` and `pyrite-admin mcp` default to `write` if `--tier` is omitted; pass `--tier admin` if you need index and git operations. Over HTTP there is no tier option (`pyrite-server` has none): the tier is the role of the API key or user you connect with, and it is `admin` when the server has no credentials at all.
