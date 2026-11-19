"""Real uvicorn import-string loading logs; factory and library imports do not."""

import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("factory", [False, True])
def test_import_and_factory_leave_logging_and_default_app_alone(factory):
    code = """
import json, logging
import pyrite.server.api as api
before = list(logging.getLogger('pyrite').handlers), list(logging.root.handlers)
assert 'app' not in vars(api)
assert api._default_app is None
if FACTORY:
    api.create_app()
assert before == (list(logging.getLogger('pyrite').handlers), list(logging.root.handlers))
assert api._default_app is None
print(json.dumps({'ok': True}))
""".replace("FACTORY", repr(factory))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True}


def test_uvicorn_import_string_configures_warning_once_and_caches_app():
    code = """
import logging, uvicorn
import pyrite.server.api as api
config = uvicorn.Config('pyrite.server.api:app', log_level='warning')
config.load()
assert api.app is api.app is api._default_app
logging.getLogger('pyrite.server.errors').warning('lazy-entry-point-canary')
assert logging.root.handlers == []
try:
    api.no_such_public_attribute
except AttributeError:
    pass
else:
    raise AssertionError('unknown module attributes must fail')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("lazy-entry-point-canary") == 1
    assert result.stdout == ""
