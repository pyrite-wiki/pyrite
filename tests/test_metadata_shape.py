"""A handwritten metadata value is reported, not repaired or hidden (#1332)."""

from contextlib import closing

import pytest

from pyrite.models.task import TaskEntry
from pyrite.services.access_policy import UNSCOPED
from pyrite.services.qa_service import QAService


@pytest.mark.parametrize("entry_type", ["task", "note", "custom"])
@pytest.mark.parametrize(
    ("literal", "warn"),
    [
        ("abc", True),
        ("state", True),
        ("[one, two]", True),
        ("17", True),
        ("0", True),
        pytest.param("null", False, marks=pytest.mark.control(reason="null remains optional")),
        pytest.param(
            "{owner: Ada}", False, marks=pytest.mark.control(reason="a mapping is valid metadata")
        ),
    ],
)
def test_unrelated_edit_preserves_handwritten_metadata(tmp_path, entry_type, literal, warn):
    from pyrite.config import KBConfig
    from tests._world import build_world

    kb = "test-kb"
    with closing(
        build_world(tmp_path, kbs=[KBConfig(name=kb, path=tmp_path / "kb", kb_type="generic")])
    ) as world:
        _check_preservation(world, kb, entry_type, literal, warn)


def _check_preservation(world, kb, entry_type, literal, warn):
    # A note's standalone references must not turn the opaque metadata into
    # a mapping while loading; the file (including its references) stays intact.
    references = "references: [archive]\n" if entry_type == "note" and literal == "17" else ""
    original = (
        f"---\nid: example\ntype: {entry_type}\ntitle: Before\n"
        f"metadata: {literal} # keep\n{references}---\n\nBody.\n"
    )
    path = world.add_entry(kb, original)
    world.index(kb)

    indexed = world.db.get_entry("example", kb)
    assert indexed and indexed["entry_type"] == entry_type

    response = world.client.patch(
        "/api/entries/example", json={"kb": kb, "field": "title", "value": "After"}
    )
    assert response.status_code == 200, response.text
    assert path.read_bytes() == original.replace("title: Before", "title: After").encode()

    warnings = [w for w in response.json().get("warnings", []) if w["field"] == "metadata"]
    assert [(w["rule"], w["severity"]) for w in warnings] == (
        [("metadata_shape", "warning")] if warn else []
    )
    qa = QAService(world.config, world.db)
    for report in [
        qa.validate_kb(kb, readable_kbs=UNSCOPED),
        qa.validate_entry("example", kb, readable_kbs=UNSCOPED),
    ]:
        assert [
            (issue["entry_id"], issue["rule"], issue["severity"])
            for issue in report["issues"]
            if issue["field"] == "metadata" and issue["rule"] == "metadata_shape"
        ] == ([("example", "metadata_shape", "warning")] if warn else [])


@pytest.mark.parametrize("value", ["abc", [], False, 0])
def test_task_frontmatter_keeps_the_original_value(value):
    task = TaskEntry.from_frontmatter({"id": "t", "metadata": value, "extra": 42}, "")
    assert task.metadata == {"extra": 42}
    assert task.to_frontmatter()["metadata"] == value
