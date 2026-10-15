"""URL liveness checking service for QA validation.

Checks HTTP status codes for source URLs across KB entries, with caching
to avoid rechecking unchanged URLs.
"""

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from .search_service import storage_error_from

logger = logging.getLogger(__name__)


@dataclass
class URLCheckResult:
    """Result of checking a single URL."""

    url: str
    status_code: int
    ok: bool
    error: str = ""
    checked_at: str = ""
    redirect_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"url": self.url, "status_code": self.status_code, "ok": self.ok}
        if self.error:
            d["error"] = self.error
        if self.redirect_url:
            d["redirect_url"] = self.redirect_url
        if self.checked_at:
            d["checked_at"] = self.checked_at
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "URLCheckResult":
        return cls(
            url=d["url"],
            status_code=d.get("status_code", 0),
            ok=d.get("ok", False),
            error=d.get("error", ""),
            checked_at=d.get("checked_at", ""),
            redirect_url=d.get("redirect_url", ""),
        )


class URLChecker:
    """Checks source URLs in KB entries for liveness."""

    def __init__(
        self,
        db: Any,
        cache_path: Path | None = None,
        timeout: int = 10,
        max_concurrent: int = 5,
    ):
        self.db = db
        self.cache_path = cache_path
        self.timeout = timeout
        self.max_concurrent = max_concurrent

    def collect_urls(
        self,
        kb_name: str,
        entry_types: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Collect all source URLs from KB entries.

        Returns {url: [entry_id, ...]} mapping. With no type filter, scans
        every stored entry type. Each type retains the existing 10,000-entry limit.
        Database failures propagate rather than claiming there are no URLs.
        """
        try:
            types_to_scan = (
                entry_types
                if entry_types is not None
                else self.db.get_distinct_types(kb_name=kb_name)
            )

            url_entries: dict[str, list[str]] = defaultdict(list)

            for etype in types_to_scan:
                results = self.db.list_entries(kb_name=kb_name, entry_type=etype, limit=10000)

                for r in results:
                    entry_id = r.get("id", "")
                    # list_entries doesn't include sources; fetch full entry
                    full = self.db.get_entry(entry_id, kb_name)
                    if not full:
                        continue
                    sources = full.get("sources") or []

                    for src in sources:
                        url = ""
                        if isinstance(src, dict):
                            url = src.get("url", "")
                        elif isinstance(src, str):
                            url = src
                        if url and url.startswith("http"):
                            url_entries[url].append(entry_id)

            return dict(url_entries)
        except (SQLAlchemyError, sqlite3.Error) as exc:
            raise storage_error_from(getattr(exc, "orig", exc), "URL collection failed") from exc

    def check_url(self, url: str) -> URLCheckResult:
        """Check a single URL for liveness."""
        import urllib.error
        import urllib.request

        try:
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "Pyrite-QA/1.0 (URL liveness check)")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return URLCheckResult(
                    url=url,
                    status_code=resp.status,
                    ok=200 <= resp.status < 400,
                    checked_at=datetime.now(UTC).isoformat(),
                    redirect_url=resp.url if resp.url != url else "",
                )
        except urllib.error.HTTPError as e:
            return URLCheckResult(
                url=url,
                status_code=e.code,
                ok=False,
                checked_at=datetime.now(UTC).isoformat(),
            )
        except Exception as e:
            return URLCheckResult(
                url=url,
                status_code=0,
                ok=False,
                error=str(e)[:200],
                checked_at=datetime.now(UTC).isoformat(),
            )

    def check_urls(
        self,
        urls: list[str],
        use_cache: bool = True,
    ) -> list[URLCheckResult]:
        """Check multiple URLs, using cache when available."""
        cached = self.load_cache() if use_cache and self.cache_path else {}
        results: list[URLCheckResult] = []

        for url in urls:
            if url in cached:
                results.append(cached[url])
            else:
                result = self.check_url(url)
                results.append(result)
                cached[url] = result

        if self.cache_path:
            self.save_cache(cached)

        return results

    def build_report(
        self,
        url_entries: dict[str, list[str]],
        results: list[URLCheckResult],
    ) -> dict[str, Any]:
        """Build a report from check results."""
        ok_count = sum(1 for r in results if r.ok)
        broken_count = sum(1 for r in results if not r.ok)

        broken_details = []
        for r in results:
            if not r.ok:
                broken_details.append(
                    {
                        "url": r.url,
                        "status_code": r.status_code,
                        "error": r.error,
                        "entry_ids": url_entries.get(r.url, []),
                    }
                )

        return {
            "total_urls": len(results),
            "ok": ok_count,
            "broken": broken_count,
            "broken_details": broken_details,
        }

    def save_cache(self, cache: dict[str, URLCheckResult]) -> None:
        """Save check results to cache file."""
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = {url: r.to_dict() for url, r in cache.items()}
        self.cache_path.write_text(json.dumps(data, indent=2))

    def load_cache(self) -> dict[str, URLCheckResult]:
        """Load cached check results."""
        if not self.cache_path or not self.cache_path.exists():
            return {}
        try:
            data = json.loads(self.cache_path.read_text())
            return {url: URLCheckResult.from_dict(d) for url, d in data.items()}
        except (json.JSONDecodeError, KeyError):
            return {}
