"""A server with no credential is reachable only from its own machine, by default.

Property: the local compose file publishes on loopback; when an operator binds
a credential-free server wider, both entry points say so at startup and name
the ways out (bind to loopback, turn on auth); with a credential, on loopback,
or when the compose file says the host side is loopback, they say nothing.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.config import AuthConfig, PyriteConfig, Settings

ROOT = Path(__file__).resolve().parent.parent
ACK = "PYRITE_PUBLISHED_ON_LOOPBACK"


@pytest.fixture(autouse=True)
def _no_ack(monkeypatch):
    monkeypatch.delenv(ACK, raising=False)


def _in_container(monkeypatch, value=True):
    """Simulate (or not) running inside a container; the real check reads /.dockerenv."""
    monkeypatch.setattr("pyrite.config._in_container", lambda: value)


def unauthenticated_bind_warning(*a, **k):
    from pyrite.config import unauthenticated_bind_warning as real

    return real(*a, **k)


def _cfg(**settings) -> PyriteConfig:
    return PyriteConfig(settings=Settings(**settings))


class TestComposeFile:
    def _service(self):
        return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["pyrite"]

    def test_publishes_only_on_loopback(self):
        ports = self._service()["ports"]
        assert ports, "the local compose file publishes a port"
        for p in ports:
            parts = str(p).split(":")
            assert len(parts) == 3 and parts[0] == "127.0.0.1", p

    def test_does_not_share_the_host_network(self):
        """network_mode: host ignores `ports:`; the loopback publish would mean nothing."""
        svc = self._service()
        assert svc.get("network_mode") != "host"
        assert svc.get("ports"), "no ports: key means the publish is not what limits exposure"

    def test_says_the_host_side_is_loopback(self):
        env = self._service()["environment"]
        env = dict(e.split("=", 1) for e in env) if isinstance(env, list) else env
        assert env.get(ACK) in ("true", True), env


class TestWarningRegimes:
    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "192.168.1.5", "pyrite.example.org"])
    def test_wider_bind_without_credential_warns_and_names_the_ways_out(self, host):
        text = unauthenticated_bind_warning(_cfg(host=host))
        assert text
        assert "127.0.0.1" in text and "auth" in text.lower()

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    @pytest.mark.control(reason="loopback never warned")
    def test_loopback_bind_is_silent(self, host):
        assert unauthenticated_bind_warning(_cfg(host=host)) is None

    @pytest.mark.control(reason="a credentialed server never warned")
    def test_auth_enabled_is_silent(self):
        cfg = _cfg(host="0.0.0.0", auth=AuthConfig(enabled=True))
        assert unauthenticated_bind_warning(cfg) is None

    @pytest.mark.control(reason="a keyed server never warned")
    def test_api_key_is_silent(self):
        assert unauthenticated_bind_warning(_cfg(host="0.0.0.0", api_key="k")) is None

    def test_anonymous_write_tier_counts_as_no_credential(self):
        cfg = _cfg(host="0.0.0.0", auth=AuthConfig(enabled=True, anonymous_tier="write"))
        assert unauthenticated_bind_warning(cfg)

    def test_explicit_host_argument_overrides_the_configured_one(self):
        assert unauthenticated_bind_warning(_cfg(host="127.0.0.1"), host="0.0.0.0")
        assert unauthenticated_bind_warning(_cfg(host="0.0.0.0"), host="127.0.0.1") is None

    def test_acknowledgement_in_a_container_silences_it(self, monkeypatch):
        _in_container(monkeypatch)
        monkeypatch.setenv(ACK, "true")
        assert unauthenticated_bind_warning(_cfg(host="0.0.0.0")) is None

    def test_acknowledgement_outside_a_container_does_nothing(self, monkeypatch):
        """A shell export on a bare host cannot be true: there is no port mapping."""
        _in_container(monkeypatch, False)
        monkeypatch.setenv(ACK, "true")
        assert unauthenticated_bind_warning(_cfg(host="0.0.0.0"))

    @pytest.mark.parametrize("value", ["1", "yes", "True", "TRUE", "false", ""])
    def test_only_the_exact_value_true_acknowledges(self, monkeypatch, value):
        _in_container(monkeypatch)
        monkeypatch.setenv(ACK, value)
        assert unauthenticated_bind_warning(_cfg(host="0.0.0.0"))

    def test_acknowledgement_does_not_cover_anonymous_write(self, monkeypatch):
        """The promise is about the published address, not about who may write."""
        _in_container(monkeypatch)
        monkeypatch.setenv(ACK, "true")
        cfg = _cfg(host="0.0.0.0", auth=AuthConfig(enabled=True, anonymous_tier="write"))
        assert unauthenticated_bind_warning(cfg)

    def test_acknowledgement_leaves_the_request_guard_unchanged(self, monkeypatch):
        from pyrite.server.request_guard import acts_without_credential, allowed_hosts

        settings = _cfg(host="0.0.0.0").settings
        before = (allowed_hosts(settings), acts_without_credential(settings))
        _in_container(monkeypatch)
        monkeypatch.setenv(ACK, "true")
        assert (allowed_hosts(settings), acts_without_credential(settings)) == before

    @pytest.mark.parametrize(
        "host", ["127.0.0.2", "127.1", "LOCALHOST", "localhost.", "[::1]", "127.0.0.1:8088"]
    )
    def test_every_loopback_form_is_silent(self, host):
        assert unauthenticated_bind_warning(_cfg(host=host)) is None

    @pytest.mark.parametrize("host", ["128.0.0.1", "10.0.0.1", "127.example.org", "0.0.0.0"])
    def test_lookalikes_still_warn(self, host):
        assert unauthenticated_bind_warning(_cfg(host=host))


class TestEntryPoints:
    def test_pyrite_serve_host_flag_warns(self, tmp_path, monkeypatch):
        from pyrite.cli import app

        monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
        with patch("uvicorn.run"):
            res = CliRunner().invoke(app, ["serve", "--dev", "--host", "0.0.0.0"])
        assert "127.0.0.1" in res.output and "auth" in res.output.lower(), res.output
        assert "without a credential" in res.output

    @pytest.mark.control(reason="the default loopback serve never warned")
    def test_pyrite_serve_default_is_silent(self, tmp_path, monkeypatch):
        from pyrite.cli import app

        monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
        with patch("uvicorn.run"):
            res = CliRunner().invoke(app, ["serve", "--dev"])
        assert "without a credential" not in res.output

    def test_pyrite_serve_acknowledged_in_a_container_is_silent(self, tmp_path, monkeypatch):
        from pyrite.cli import app

        monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("PYRITE_HOST", "0.0.0.0")
        monkeypatch.setenv(ACK, "true")
        _in_container(monkeypatch)
        with patch("uvicorn.run"):
            res = CliRunner().invoke(app, ["serve", "--dev"])
        assert "without a credential" not in res.output

    def test_pyrite_serve_acknowledged_outside_a_container_still_warns(self, tmp_path, monkeypatch):
        from pyrite.cli import app

        monkeypatch.setenv("PYRITE_DATA_DIR", str(tmp_path))
        monkeypatch.setenv(ACK, "true")
        _in_container(monkeypatch, False)
        with patch("uvicorn.run"):
            res = CliRunner().invoke(app, ["serve", "--dev", "--host", "0.0.0.0"])
        assert "without a credential" in res.output

    def _server(self, tmp_path, env_host, container=False, ack=False):
        import os

        pre = (
            "from unittest.mock import patch; import uvicorn; "
            "patch('uvicorn.run', side_effect=lambda *a, **k: None).start(); "
            f"patch('pyrite.config._in_container', return_value={container}).start()"
        )
        code = f"{pre}; from pyrite.server.api import main; main()"
        # Clean environment: an ambient PYRITE_AUTH_ENABLED or PYRITE_API_KEY
        # would change what "no credential" means.
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYRITE_")}
        env.update({"PYRITE_DATA_DIR": str(tmp_path), "PYRITE_HOST": env_host})
        if ack:
            env[ACK] = "true"
        return subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=tmp_path
        )

    def test_pyrite_server_wildcard_warns_on_stderr(self, tmp_path):
        proc = self._server(tmp_path, "0.0.0.0")
        assert "without a credential" in proc.stderr, proc.stderr

    @pytest.mark.control(reason="a loopback pyrite-server never warned")
    def test_pyrite_server_loopback_is_silent(self, tmp_path):
        proc = self._server(tmp_path, "127.0.0.1")
        assert "without a credential" not in proc.stderr, proc.stderr

    def test_pyrite_server_acknowledged_in_a_container_is_silent(self, tmp_path):
        proc = self._server(tmp_path, "0.0.0.0", container=True, ack=True)
        assert "without a credential" not in proc.stderr, proc.stderr

    def test_pyrite_server_acknowledged_outside_a_container_still_warns(self, tmp_path):
        proc = self._server(tmp_path, "0.0.0.0", container=False, ack=True)
        assert "without a credential" in proc.stderr, proc.stderr
