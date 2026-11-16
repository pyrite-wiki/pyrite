"""No single client can take more than a small share of the anonymous socket budget.

``MAX_ANONYMOUS_SOCKETS`` caps the anonymous sockets; without a per-client
share one client filled it and stopped live updates for every other visitor.
The client is keyed as the rate limiter keys it (``_anonymized_key_func``).
"""

from __future__ import annotations

import asyncio

import pytest

from pyrite.server import websocket as w


# One client's share of the anonymous budget, written out (of 100 in all).
SHARE = 5


class _WS:
    def __init__(self):
        self.accepted = False
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=None):
        self.closed = code


def _scope(client):
    return w.SocketScope(readable={"pub"}, anonymous=True, client=client)


def test_one_client_cannot_exceed_its_share_and_others_still_connect():
    mgr = w.ConnectionManager()
    share = SHARE

    async def run():
        for _ in range(share):
            assert await mgr.connect(_WS(), _scope("greedy"))
        over = _WS()
        assert not await mgr.connect(over, _scope("greedy"))
        assert over.closed == w.CAP_CLOSE_CODE
        # The rest of the budget is still there for other visitors.
        assert await mgr.connect(_WS(), _scope("someone-else"))
        assert mgr.anonymous_count == share + 1

    asyncio.run(run())


def test_a_share_frees_when_a_socket_leaves():
    mgr = w.ConnectionManager()

    async def run():
        socks = [_WS() for _ in range(SHARE)]
        for s in socks:
            assert await mgr.connect(s, _scope("c"))
        mgr.disconnect(socks[0])
        assert await mgr.connect(_WS(), _scope("c"))

    asyncio.run(run())


def test_handshakes_in_flight_cannot_slip_past_the_share():
    mgr = w.ConnectionManager()

    def fill():
        for _ in range(SHARE):
            mgr._connections[object()] = _scope("c")

    class Slow(_WS):
        async def accept(self):
            await super().accept()
            fill()

    async def run():
        ws = Slow()
        assert not await mgr.connect(ws, _scope("c"))
        assert ws not in mgr._connections

    asyncio.run(run())


def test_the_real_handshake_stops_one_client_at_its_share(tmp_path):
    """Through ``/ws`` itself: the scope the handshake resolves carries the
    client, so a sixth anonymous socket from one address is refused."""
    import pytest
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from pyrite.config import AuthConfig, KBConfig, PyriteConfig, Settings
    from pyrite.server.api import create_app

    (tmp_path / "pub").mkdir()
    config = PyriteConfig(
        knowledge_bases=[
            KBConfig(name="pub", path=tmp_path / "pub", kb_type="generic", default_role="read")
        ],
        settings=Settings(
            index_path=tmp_path / "index.db",
            auth=AuthConfig(enabled=True, anonymous_tier="read"),
        ),
    )
    app = create_app(config=config)
    with TestClient(app) as c:
        held = [c.websocket_connect("/ws") for _ in range(SHARE)]
        socks = [h.__enter__() for h in held]
        try:
            with pytest.raises(WebSocketDisconnect) as exc:
                with c.websocket_connect("/ws"):
                    pass
            assert exc.value.code == w.CAP_CLOSE_CODE
        finally:
            for h in held:
                h.__exit__(None, None, None)
        assert len(socks) == SHARE


_HELD = pytest.mark.control(reason="control: held at the round-1 head (a hash of the address)")


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:ffff", True),  # one /64
        pytest.param("2001:db8:1:2::1", "2001:db8:1:3::1", False, marks=_HELD),  # another /64
        pytest.param("203.0.113.9", "203.0.113.10", False, marks=_HELD),  # IPv4 per address
        ("203.0.113.9", "::ffff:203.0.113.9", True),  # mapped is the IPv4 address
        pytest.param("203.0.113.9", "203.0.113.9", True, marks=_HELD),
    ],
)
def test_clients_are_keyed_by_their_allocation(a, b, same):
    from pyrite.server.client_key import client_key

    class Conn:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})()

    assert (client_key(Conn(a)) == client_key(Conn(b))) is same
