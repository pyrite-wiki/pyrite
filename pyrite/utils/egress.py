"""The one constructor of an HTTP client to the GitHub API (ADR-0052 N2, D-U2).

Whose network settings a request uses is decided here, from the reach
(``acting.is_local_process``), never at the call site:

* the local process (the CLI, stdio MCP): the operator's own environment, as
  httpx reads it (proxy variables, netrc, CA bundle);
* a network caller: nothing of the server account's environment
  (``trust_env=False``: no proxy variable, which can carry a proxy
  credential, and no netrc) except the CA-bundle variables network git also
  keeps (``SSL_CERT_FILE``, ``SSL_CERT_DIR``). The one proxy it may use is
  the operator's ``git_proxy`` grant, passed in as `proxy`.

The token a request carries is the caller's to choose and is not this
module's concern; what it adds is that no credential of the account rides
along. ``tests/test_http_clients_borrow_nothing.py`` holds every httpx and
urllib client in ``pyrite/`` and ``extensions/`` to this constructor or to an
explicit ``trust_env=False`` / ``ProxyHandler({})``.
"""

from __future__ import annotations

from typing import Any


def _network_reach() -> bool:
    from ..services.acting import is_local_process

    return not is_local_process()


def _ca_bundle() -> Any:
    """What a network caller's client verifies servers against: the CA-bundle
    variables network git also keeps (``SSL_CERT_FILE``, ``SSL_CERT_DIR``; they
    say which servers to trust and authenticate no one, U3), else httpx's
    default. ``trust_env=False`` would otherwise drop them with the proxies."""
    import os
    import ssl

    cafile = os.environ.get("SSL_CERT_FILE") or None
    capath = os.environ.get("SSL_CERT_DIR") or None
    if cafile is None and capath is None:
        return True
    return ssl.create_default_context(cafile=cafile, capath=capath)


def _settings(proxy: str | None, kwargs: dict[str, Any]) -> dict[str, Any]:
    if {"trust_env", "proxy", "mounts", "verify"} & set(kwargs):
        raise TypeError("github_client decides trust_env, proxy, mounts and verify itself")
    if _network_reach():
        return {**kwargs, "trust_env": False, "proxy": proxy, "verify": _ca_bundle()}
    return kwargs


def github_client(*, proxy: str | None = None, **kwargs: Any):
    """An ``httpx.Client`` for a GitHub API request; `proxy` is the operator's
    ``git_proxy`` (None: no proxy for a network caller)."""
    import httpx

    return httpx.Client(**_settings(proxy, kwargs))


def github_async_client(*, proxy: str | None = None, **kwargs: Any):
    """`github_client`, as an ``httpx.AsyncClient``."""
    import httpx

    return httpx.AsyncClient(**_settings(proxy, kwargs))
