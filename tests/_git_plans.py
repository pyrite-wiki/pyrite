"""Seams for tests of git network commands (``GitService._network_plan``).

The host rule, the transport guards and the credential are decided in one
constructor. Tests about something else (who may export, how a failure is
worded) stand in for an operator who allowed the destination with these,
rather than each stubbing a piece of the rule.
"""

from __future__ import annotations

from collections.abc import Sequence

from pyrite.exceptions import RemoteNotAllowedError
from pyrite.services.git_service import GitService


def plan_for(token, urls: Sequence[str], *, credentials=None, kb_name=None, registered=False):
    """The plan GitService builds for `urls` with every host they name
    allowed, or None when the rule refuses them anyway (the scheme, an ssh
    remote with no operator key). `registered`: the URLs are a registered
    KB's configured remote (D-U1); by default, URLs a caller named."""
    hosts = [e[1] for e in (GitService._endpoint(u, True) for u in urls) if e] or ["github.com"]
    try:
        return GitService._network_plan(
            urls,
            token=token,
            allowed_hosts=hosts,
            credentials=credentials,
            kb_name=kb_name,
            allow_userinfo=True,
            registered_remote=registered,
        )
    except RemoteNotAllowedError:
        return None


def allowed_remote_plan(*args, **kwargs):
    """A stand-in for ``GitService._remote_plan`` whose remote is allowed
    (``https://github.com/o/r``): the rest of the plan (guards, credential)
    is the real one."""
    return GitService._network_plan(
        ["https://github.com/o/r"],
        token=kwargs.get("token"),
        allowed_hosts=None,
        credentials=kwargs.get("credentials"),
        kb_name=kwargs.get("kb_name"),
        allow_userinfo=True,
        registered_remote=kwargs.get("registered_remote", False),
        proxy=kwargs.get("proxy"),
    )


def allow_local_destinations(monkeypatch) -> None:
    """For tests that clone from or push to a local repository standing in for
    an allowed host: every URL counts as https on github.com, and git's
    transport allowlist admits the file transport. Nothing else of the plan
    (credential, the closed environment) changes."""
    from pyrite.services import git_service

    monkeypatch.setattr(
        GitService, "_endpoint", staticmethod(lambda url, allow_userinfo: ("https", "github.com"))
    )
    monkeypatch.setattr(GitService, "check_remote_allowed", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(git_service, "_ALLOWED_GIT_PROTOCOLS", "https:file")
