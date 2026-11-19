# MCP input-schema snapshots

`tests/test_mcp_schema_snapshots.py` pins the input schemas returned by the actual MCP SDK tools/list handler for read, write and admin tiers. The core profile always runs. The in-tree profile registers the five shipped plugins explicitly, so unrelated installed entry points cannot change the inventory; it skips when those plugins are unavailable.

The two `goldens/mcp-input-schemas.*.json` files contain each schema and fixed, schema-valid arguments for every tool. These are wire-shape examples, not calls against a live KB. They do not claim that the example entry IDs exist or that an external service accepts them. Every example holds exactly the schema's `required` keys, so the validation test catches an added required field or a narrowed type on a required field; a change to an optional field is caught by the snapshot test.

Run `python -m pytest tests/test_mcp_schema_snapshots.py -q`. A normal run never rewrites these files.

For an intentional contract change, regenerate both files from the live `tools/list` and review the diff:

```
PYRITE_CHARACTERIZATION_REGENERATE=1 python -m pytest tests/test_mcp_schema_snapshots.py -k regenerate -p no:xdist
```

(the same switch the other goldens use). Each `inputSchema` is replaced with what the SDK publishes. A tool's pinned arguments are kept while they still validate against its new schema; a new tool, or a tool whose arguments no longer validate, gets its required fields filled from the schema (`"example"` for strings), which you should read and adjust by hand if the value is not a sensible call. A tool that is no longer published is dropped. Then run the file again without the switch.

Mutation evidence: adding a required string field to kb_list makes all six core tier/schema and tier/example cases fail. Making the argument generator return an invalid string makes the six generated-argument cases fail.
