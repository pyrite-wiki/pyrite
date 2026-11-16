"""``param_kind`` and ``param_choices`` on both shapes of Typer parameter (#1432).

The lock keeps Typer below 0.29, so the CLI walks only ever reach the click-type
branch in CI. These stand-ins have the Typer 0.29 shape (no ``.type``; the declared
annotation on ``type_descriptor``) so the branch a user on 0.29 hits is pinned too.
"""

from __future__ import annotations

import enum
import pathlib
import typing
from types import SimpleNamespace

import pytest

from tests.outcome_walk import param_choices, param_kind


# ``typing.Optional[...]`` (UP045) on purpose: it is how Typer commands spell an optional value.


class Colour(enum.Enum):
    RED = "red"
    BLUE = "blue"


def typer_029(annotation, file_annotation=None):
    """A Typer 0.29 parameter: no ``.type``, the annotation on ``type_descriptor``."""
    return SimpleNamespace(
        type_descriptor=SimpleNamespace(annotation=annotation, file_annotation=file_annotation)
    )


@pytest.mark.parametrize(
    ("annotation", "kind"),
    [
        (int, "integer"),
        (typing.Optional[int], "integer"),  # noqa: UP045
        (int | None, "integer"),
        (typing.Annotated[int, "help"], "integer"),
        (float, "float"),
        (bool, "boolean"),  # bool subclasses int: it must not read as an integer
        (typing.Optional[bool], "boolean"),  # noqa: UP045
        (pathlib.Path, "path"),
        (list[pathlib.Path], "path"),
        (typing.Optional[list[pathlib.Path]], "path"),  # noqa: UP045
        (Colour, "choice"),
        (typing.Literal["json", "yaml"], "choice"),
        (str, ""),
        (typing.Optional[str], ""),  # noqa: UP045
    ],
)
def test_param_kind_reads_the_typer_029_annotation(annotation, kind):
    assert param_kind(typer_029(annotation)) == kind


def test_a_file_annotation_is_a_path():
    assert param_kind(typer_029(typing.Optional[str], file_annotation=object())) == "path"  # noqa: UP045


@pytest.mark.parametrize(
    ("annotation", "choices"),
    [
        (Colour, ["red", "blue"]),
        (typing.Optional[Colour], ["red", "blue"]),  # noqa: UP045
        (list[Colour], ["red", "blue"]),
        (typing.Literal["json", "yaml"], ["json", "yaml"]),
        (typing.Literal[1, 2], ["1", "2"]),
        (int, []),
        (typing.Optional[list[pathlib.Path]], []),  # noqa: UP045
    ],
)
def test_param_choices_reads_the_typer_029_annotation(annotation, choices):
    assert param_choices(typer_029(annotation)) == choices


def test_click_typed_parameters_still_read_from_type():
    """Typer up to 0.28: a click type on ``param.type``."""
    integer = SimpleNamespace(type=SimpleNamespace(name="integer"))
    choice = SimpleNamespace(type=SimpleNamespace(name="choice", choices=["a", "b"]))
    assert (param_kind(integer), param_choices(integer)) == ("integer", [])
    assert (param_kind(choice), param_choices(choice)) == ("choice", ["a", "b"])
