"""A q=0 range excludes a representation, including the JSON fast path."""

import pytest
from starlette.requests import Request

from pyrite.formats import negotiate_format
from pyrite.server.api import negotiate_response


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        ("text/csv;q=0", None),
        ("application/json;q=0", None),
        ("application/json;q=0.1, text/csv;q=0.9", "csv"),
        ("application/json;q=0, */*;q=1", "markdown"),
        ("text/*;q=0.8, application/json;q=0.1", "markdown"),
        ("text/csv; q=0.8, application/json;q=0.1", "csv"),
    ],
)
def test_quality_and_specific_exclusions_choose_the_acceptable_format(accept, expected):
    assert negotiate_format(accept) == expected
    request = Request({"type": "http", "headers": [(b"accept", accept.encode())]})
    response = negotiate_response(request, {"results": [{"id": "a", "title": "A"}]})
    if expected is None:
        assert response is not None and response.status_code == 406
    else:
        assert (
            response is not None
            and response.media_type == {"csv": "text/csv", "markdown": "text/markdown"}[expected]
        )


@pytest.mark.parametrize("accept", ["text/csv;q=NaN", "text/csv;q=2", "text/csv;q=-1"])
def test_invalid_quality_does_not_make_a_representation_acceptable(accept):
    assert negotiate_format(accept) is None
