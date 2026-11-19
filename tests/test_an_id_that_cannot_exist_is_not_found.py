"""An id that cannot exist answers NOT_FOUND like any other missing id (#1542).

An entry id longer than the filesystem's name limit cannot name a file, so
the entry is not there. v0.26.3 let the OS's "name too long" out of
``KBFiles.present`` through ``KBRepository._filename_candidates``: a
traceback on ``pyrite get --format json`` and ``delete``, ``NO_OUTCOME`` on
``update``, and 500 on REST. Every surface must answer exactly as it does for
an ordinary missing id: the same status and the same bytes, once the id
itself is taken out.

Medium: one real world (config on disk, the app as ``pyrite serve`` builds
it, MCP over the same config, the CLI in-process); no filesystem mock.
"""

from __future__ import annotations

import json

import pytest

from pyrite.config import KBConfig
from tests._world import build_world

KB = "longid"
#: Longer than NAME_MAX (255 bytes on every filesystem Pyrite runs on), so
#: ``<id>.md`` makes the OS answer ENAMETOOLONG.
LONG = "a" * 300
#: An ordinary id with no file: the answer the long id must match.
MISSING = "no-such-entry"


@pytest.fixture
def world(tmp_path):
    w = build_world(
        tmp_path / "w",
        kbs=[KBConfig(name=KB, path=tmp_path / "w" / "kb", kb_type="generic")],
    )
    w.add_entry(KB, "---\nid: here\ntype: note\ntitle: Here\n---\nBody\n")
    try:
        yield w
    finally:
        w.close()


def _same(long_answer: str, missing_answer: str) -> None:
    assert long_answer.replace(LONG, "<id>") == missing_answer.replace(MISSING, "<id>")


# -- CLI -------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["get", "{id}", "-k", KB, "--format", "json"],
        ["get", "{id}", "-k", KB],
        ["delete", "{id}", "-k", KB, "--force", "--format", "json"],
        ["delete", "{id}", "-k", KB, "--force"],
        ["update", "{id}", "-k", KB, "--title", "T", "--format", "json"],
        ["update", "{id}", "-k", KB, "--title", "T"],
    ],
    ids=["get-json", "get", "delete-json", "delete", "update-json", "update"],
)
def test_cli_answers_an_over_long_id_as_a_missing_one(world, args):
    def run(entry_id):
        return world.cli(*[a.replace("{id}", entry_id) for a in args])

    long_result, missing_result = run(LONG), run(MISSING)

    assert not isinstance(long_result.exception, OSError), repr(long_result.exception)
    assert long_result.exit_code == missing_result.exit_code != 0
    _same(long_result.output, missing_result.output)
    if "json" in args:
        assert json.loads(long_result.output)["error_code"] == "NOT_FOUND"


# -- MCP -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "extra"),
    [("kb_get", {}), ("kb_update", {"title": "T"}), ("kb_delete", {})],
)
def test_mcp_answers_an_over_long_id_exactly_as_a_missing_one(world, tool, extra):
    server = world.mcp()

    def call(entry_id):
        return server._dispatch_tool(tool, {"entry_id": entry_id, "kb_name": KB, **extra})

    long_res, missing_res = call(LONG), call(MISSING)

    assert long_res["error_code"] == "ENTRY_NOT_FOUND", long_res
    _same(json.dumps(long_res, sort_keys=True), json.dumps(missing_res, sort_keys=True))


# -- REST ------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_rest_answers_an_over_long_id_exactly_as_a_missing_one(world, method):
    def call(entry_id):
        if method == "PUT":
            return world.client.put(f"/api/entries/{entry_id}", json={"kb": KB, "title": "T"})
        return world.client.request(method, f"/api/entries/{entry_id}", params={"kb": KB})

    long_resp, missing_resp = call(LONG), call(MISSING)

    assert long_resp.status_code == missing_resp.status_code == 404, long_resp.text
    _same(long_resp.text, missing_resp.text)


# -- templates -------------------------------------------------------------


def test_an_over_long_template_name_is_not_found(world):
    from pyrite.services.template_service import TemplateService

    # The folder must exist: with it missing the OS answers ENOENT first.
    (world.kb_path(KB) / "_templates").mkdir()
    svc = TemplateService(world.config)
    answers = []
    for name in (LONG, MISSING):
        with pytest.raises(FileNotFoundError) as caught:
            svc.get_template(KB, name)
        answers.append(str(caught.value))
    _same(*answers)
