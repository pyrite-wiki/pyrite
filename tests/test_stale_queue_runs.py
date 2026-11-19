"""Exercise the queue cleanup against a real HTTP API, without GitHub credentials."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "queue_cleanup", Path(__file__).parents[1] / "scripts/cancel_stale_queue_runs.py"
)


def test_cleanup_cancels_only_deleted_queue_refs():
    spec = SPEC
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(("GET", self.path))
            if "/actions/" in self.path:
                runs = (
                    [
                        {
                            "id": 1,
                            "head_branch": "gh-readonly-queue/dev/pr-1-old",
                            "head_sha": "orphan",
                        },
                        {"id": 2, "head_branch": "gh-readonly-queue/dev/pr-2-live"},
                        {"id": 3, "head_branch": "dev"},
                    ]
                    if "status=queued" in self.path and "page=1&" in self.path
                    else []
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"workflow_runs": runs}).encode())
            elif "/compare/" in self.path:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"diverged"}')
            else:
                self.send_response(404 if self.path.endswith("pr-1-old") else 200)
                self.end_headers()
                self.wfile.write(b"{}")

        def do_POST(self):
            requests.append(("POST", self.path))
            self.send_response(202)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        client = module.Client("owner/repo", "synthetic", f"http://127.0.0.1:{server.server_port}")
        assert module.cleanup(client) == [1]
        assert not any(path.endswith("/git/ref/heads/dev") for _, path in requests)
        assert [p for method, p in requests if method == "POST"] == [
            "/repos/owner/repo/actions/runs/1/cancel"
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_api_permission_failure_is_not_a_missing_ref():
    spec = SPEC
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from urllib.error import HTTPError

    with pytest.raises(HTTPError):
        module.is_missing_ref_error(HTTPError("http://example.invalid", 403, "forbidden", {}, None))


def test_queue_cleanup_is_automatic_and_runs_only_trusted_dev_code():
    import yaml

    path = Path(__file__).parents[1] / ".github/workflows/cleanup-stale-queue.yml"
    assert path.is_file(), "obsolete groups have no automatic cleanup workflow"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get(True, workflow.get("on"))
    assert "merge_group" not in triggers and triggers["push"]["branches"] == ["dev"]
    assert triggers["schedule"]
    assert workflow["permissions"] == {"contents": "read", "actions": "write"}
    job = workflow["jobs"]["cleanup"]
    assert job["timeout-minutes"] <= 10
    assert job["steps"][0]["with"]["ref"] == "dev"


def test_cleanup_paginates_protects_merged_runs_and_continues_after_errors(caplog):
    from urllib.parse import parse_qs, urlparse

    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    calls = []

    def run(number, sha):
        return {"id": number, "head_branch": f"gh-readonly-queue/dev/pr-{number}", "head_sha": sha}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data=None):
            self.send_response(code)
            self.end_headers()
            self.wfile.write(json.dumps(data or {}).encode())

        def do_GET(self):
            calls.append(("GET", self.path))
            url = urlparse(self.path)
            if url.path.endswith("/runs"):
                query = parse_qs(url.query)
                if query["status"] == ["in_progress"]:
                    rows = [run(9, "diverged")]
                elif query["page"] == ["1"]:
                    rows = [run(1, "behind")] + [
                        {"id": 100 + i, "head_branch": "dev"} for i in range(99)
                    ]
                else:
                    rows = [
                        run(2, "ahead"),
                        run(3, "identical"),
                        run(4, "behind"),
                        run(5, "behind"),
                        run(6, "behind"),
                        run(7, "error"),
                        run(8, None),
                    ]
                self.reply(200, {"workflow_runs": rows})
            elif "/compare/" in url.path:
                sha = url.path.rsplit("/", 1)[-1].split("...")[0]
                self.reply(403 if sha == "error" else 200, {"status": sha})
            else:
                self.reply(403 if url.path.endswith("pr-4") else 404)

        def do_POST(self):
            calls.append(("POST", self.path))
            number = int(self.path.split("/")[-2])
            self.reply({5: 409, 6: 403}.get(number, 202))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        client = module.Client("owner/repo", "synthetic", f"http://127.0.0.1:{server.server_port}")
        assert module.cleanup(client) == [1, 9]
        assert [int(path.split("/")[-2]) for method, path in calls if method == "POST"] == [
            1,
            5,
            6,
            9,
        ]
        assert any("page=2" in path for _, path in calls)
        assert all(str(number) in caplog.text for number in [4, 5, 6, 7, 8])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
