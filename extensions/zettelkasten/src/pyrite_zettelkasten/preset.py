"""Zettelkasten KB preset definition."""

import copy

from pyrite.models.task import TASK_KB_PRESET

ZETTELKASTEN_PRESET = {
    "name": "my-zettelkasten",
    "description": "Personal knowledge garden",
    "types": {
        "zettel": {
            "description": "Atomic knowledge note",
            "required": ["title"],
            "optional": ["zettel_type", "maturity", "source_ref", "processing_stage"],
            "subdirectory": "zettels/",
        },
        "literature_note": {
            "description": "Note capturing ideas from a source work",
            "required": ["title", "source_work"],
            "optional": ["author", "page_refs"],
            "subdirectory": "literature/",
        },
        # #860: the task type lives in one place (pyrite.models.task); take a
        # deepcopy so this preset and the built-in template share no mutable dict.
        "task": copy.deepcopy(TASK_KB_PRESET["types"]["task"]),
    },
    "policies": {
        "private": True,
        "single_author": True,
    },
    "validation": {
        "enforce": True,
        "rules": [
            {"field": "zettel_type", "enum": ["fleeting", "literature", "permanent", "hub"]},
            {"field": "maturity", "enum": ["seed", "sapling", "evergreen"]},
            {
                "field": "processing_stage",
                "enum": ["capture", "elaborate", "question", "review", "connect"],
            },
        ],
    },
    "directories": ["zettels", "literature", "tasks"],
}
