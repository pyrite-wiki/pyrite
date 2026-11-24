"""A prompt or resource fault travels as a JSON-RPC *internal* error.

``_protocol_refusal`` turns the envelope a prompt or resource read built into
the JSON-RPC error the SDK sends. A refusal (``NOT_FOUND``, ``AMBIGUOUS``, a
bad argument) is the caller's to fix: ``INVALID_PARAMS``. ``INTERNAL`` is the
server's fault, not a bad parameter: ``INTERNAL_ERROR`` (-32603).
"""

from __future__ import annotations

import pytest
from mcp.types import INTERNAL_ERROR, INVALID_PARAMS

from pyrite.server.mcp_server import _error, _protocol_refusal


def test_an_internal_fault_is_a_json_rpc_internal_error():
    err = _error("INTERNAL", "Internal error; the server log has the details.", retryable=True)
    raised = _protocol_refusal(err)
    assert raised.error.code == INTERNAL_ERROR
    assert raised.error.data == err


@pytest.mark.control(reason="a refusal keeps its category")
def test_a_refusal_stays_invalid_params():
    err = _error("NOT_FOUND", "no such entry")
    assert _protocol_refusal(err).error.code == INVALID_PARAMS
