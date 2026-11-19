#!/usr/bin/env python3
"""Cancel active merge-group CI only after its temporary branch is deleted (#1189)."""

import json
import logging
import os
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class Client:
    def __init__(self, repo, token, api_url="https://api.github.com"):
        self.base = f"{api_url}/repos/{repo}"
        self.token = token

    def request(self, path, method="GET"):
        request = Request(
            self.base + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
            method=method,
        )
        with urlopen(request, timeout=30) as response:
            data = response.read()
        return json.loads(data) if data else {}


def is_missing_ref_error(error):
    if error.code == 404:
        return True
    raise error


def cleanup(client):
    cancelled = []
    for status in ("queued", "in_progress"):
        # Collect before mutating: cancellation must not shift pagination.
        runs = []
        page = 1
        while True:
            query = urlencode(
                {"event": "merge_group", "status": status, "page": page, "per_page": 100}
            )
            batch = client.request("/actions/workflows/ci.yml/runs?" + query)["workflow_runs"]
            runs.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        for run in runs:
            branch = run.get("head_branch") or ""
            if not branch.startswith("gh-readonly-queue/"):
                continue
            try:
                try:
                    client.request("/git/ref/heads/" + quote(branch, safe="/"))
                except HTTPError as error:
                    if not is_missing_ref_error(error):
                        continue
                else:
                    continue
                sha = run.get("head_sha")
                if not sha:
                    logger.warning("Skipping run %s: missing head_sha", run["id"])
                    continue
                # The comparison is base=run, head=dev. Ahead/identical means
                # dev already contains this run; let its checks finish.
                comparison = client.request("/compare/" + quote(sha, safe="") + "...dev")
                if comparison.get("status") not in ("behind", "diverged"):
                    continue
                client.request(f"/actions/runs/{int(run['id'])}/cancel", method="POST")
                cancelled.append(run["id"])
            except (OSError, ValueError) as error:
                logger.warning("Skipping run %s after API error: %s", run["id"], error)
    return cancelled


if __name__ == "__main__":
    print(
        json.dumps(
            {"cancelled": cleanup(Client(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"]))}
        )
    )
