"""`cascade_network` advertises and honours limit/offset (#63).

The cascade tool delegates to the shared `query_network`, so this pins the two
things that live on this side: the schema a caller reads, and that the handler
passes the arguments through rather than dropping them.
"""

from pyrite_cascade.plugin import CascadePlugin


class _FakeDB:
    def __init__(self, outlinks, backlinks=()):
        self._outlinks = list(outlinks)
        self._backlinks = list(backlinks)

    def get_entry(self, entry_id, kb_name):
        return {"id": entry_id, "title": "Hub"}

    def get_outlinks(self, entry_id, kb_name, readable_kbs=None):
        return list(self._outlinks)

    def get_backlinks(self, entry_id, kb_name, limit=0, offset=0, readable_kbs=None):
        return list(self._backlinks)


def _links(prefix, count):
    return [
        {"id": f"{prefix}-{index:03d}", "title": f"{prefix} {index:03d}"} for index in range(count)
    ]


def test_schema_advertises_limit_and_offset():
    plugin = CascadePlugin()

    schema = plugin.get_mcp_tools("read")["cascade_network"]["inputSchema"]

    assert "limit" in schema["properties"]
    assert "offset" in schema["properties"]
    assert schema["required"] == ["entry_id", "kb_name"]


def test_handler_honours_limit_and_reports_truncation(monkeypatch):
    plugin = CascadePlugin()
    monkeypatch.setattr(plugin, "_get_db", lambda: (_FakeDB(_links("out", 7)), False))

    result = plugin._mcp_network({"kb_name": "kb", "entry_id": "hub", "limit": 2})

    assert len(result["outlinks"]) == 2
    assert result["totals"] == {"outlinks": 7, "backlinks": 0}
    assert result["truncated"] is True


def test_handler_defaults_leave_a_small_node_alone(monkeypatch):
    plugin = CascadePlugin()
    monkeypatch.setattr(plugin, "_get_db", lambda: (_FakeDB(_links("out", 3)), False))

    result = plugin._mcp_network({"kb_name": "kb", "entry_id": "hub"})

    assert len(result["outlinks"]) == 3
    assert result["truncated"] is False
