# MCP input-schema snapshots

`tests/test_mcp_schema_snapshots.py` pins the input schemas returned by the actual MCP SDK tools/list handler for read, write and admin tiers. The core profile always runs. The in-tree profile registers the six shipped plugins explicitly, so unrelated installed entry points cannot change the inventory; it skips when those plugins are unavailable.

The two `goldens/mcp-input-schemas.*.json` files contain each schema and fixed, schema-valid arguments for every tool. These are wire-shape examples, not calls against a live KB. They do not claim that the example entry IDs exist or that an external service accepts them.

Run `python -m pytest tests/test_mcp_schema_snapshots.py -q`. Pytest never rewrites these snapshots. For an intentional contract change, export `published_schemas` from the test helper on a temporary database, update the affected inputSchema records, and review the diff. Keep the old arguments unless the public call intentionally changes; add a reviewed argument example for a newly published tool. The separate validation test ensures an added required field or narrower type cannot silently invalidate the pinned calls.

Mutation evidence: adding a required string field to kb_list made all six core tier/schema and tier/example cases fail. No production schema was changed by this test infrastructure PR.
