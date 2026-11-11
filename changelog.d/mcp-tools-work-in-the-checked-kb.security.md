Over MCP, a plugin tool that writes works in the knowledge base named by `kb_name` and in no other, and every check (read, write, read-only, where the write lands) runs on that knowledge base before the tool does. A tool whose knowledge base may be omitted names its default before the checks, chosen from the knowledge bases the caller may read (`investigation_*` write tools: the first investigation KB; `sw_create_adr`: the one ADR KB). A write call left with no `kb_name` is refused with `VALIDATION_FAILED`. Plugin tools declare the default as `default_kb` in the tool dict (alpha).

Every KB argument of an MCP tool is a knowledge base name (a string), and `kb_names` is a list of non-blank names; any other value is refused with `VALIDATION_FAILED`. `kb_timeline` reads only the named knowledge bases the caller may read.

An MCP tool that fails unexpectedly answers `INTERNAL` with a fixed message; the detail is in the server log.

A plugin's `default_kb` answer is used only when it is a knowledge base the caller may read; anything else counts as no default. Any unexpected fault during an MCP tool call, before or in the tool, answers `INTERNAL` with the fixed message, and so does one in a prompt (`prompts/get`) or a resource read (`resources/read`); a refusal there keeps its own code.

**Upgrade notes:**

- A client that passed `kb_names` as a single string now gets `VALIDATION_FAILED`; pass a list.
- Plugin authors: a read-tier MCP tool that takes a KB argument also takes `readable_kbs` and filters what it returns by it (every in-tree read tool does; a test holds new ones to it).
- Plugin authors: a write-tier (or admin-tier) MCP tool works in `args["kb_name"]` only. A call that names its knowledge base under another argument (`kb`, `source_kb`, ...) or names none, to a tool that declares no default, is now refused with `VALIDATION_FAILED` before the handler runs. Take the knowledge base as `kb_name`. If the tool may run with the knowledge base omitted, declare `"default_kb": fn` beside its `handler`, where `fn(args, readable_kbs)` returns the name of a knowledge base in `readable_kbs` (`None` means an unscoped caller) or `None`; never choose a default inside the handler. See the `get_mcp_tools` docstring in `pyrite/plugins/protocol.py`.
