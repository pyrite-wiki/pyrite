"""
Review Service

Manages QA review lifecycle: create reviews with content hashing,
check review currency against file state.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config import PyriteConfig
from ..exceptions import EntryNotFoundError
from ..storage.database import PyriteDB
from ..storage.repository import KBRepository

logger = logging.getLogger(__name__)


class ReviewService:
    """Service for QA review operations."""

    def __init__(self, config: PyriteConfig, db: PyriteDB):
        self.config = config
        self.db = db

    def create_review(
        self,
        entry_id: str,
        kb_name: str,
        reviewer: str,
        reviewer_type: str,
        result: str,
        details: str | None = None,
    ) -> dict[str, Any]:
        """Create a review, computing content_hash from the file on disk."""
        from ..utils.hashing import git_blob_hash

        kb_config = self.config.require_kb(kb_name, db=self.db)  # the one resolver (#952)

        repo = KBRepository(kb_config)
        file_path = repo.find_file(entry_id)
        if not file_path:
            raise EntryNotFoundError(f"Entry not found on disk: {entry_id}")

        content = Path(file_path).read_bytes()
        content_hash = git_blob_hash(content)

        return self.db.create_review(
            entry_id=entry_id,
            kb_name=kb_name,
            content_hash=content_hash,
            reviewer=reviewer,
            reviewer_type=reviewer_type,
            result=result,
            details=details,
        )

    def get_reviews(self, entry_id: str, kb_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """Get reviews for an entry."""
        return self.db.get_reviews(entry_id, kb_name, limit=limit)

    def get_latest_review(self, entry_id: str, kb_name: str) -> dict[str, Any] | None:
        """Get the latest review for an entry."""
        return self.db.get_latest_review(entry_id, kb_name)

    def get_review(self, review_id: int) -> dict[str, Any] | None:
        """One review by id, or ``None``."""
        return self.db.get_review(review_id)

    def delete_review(self, review_id: int) -> bool:
        """Delete a review by id. True when a review was deleted."""
        return self.db.delete_review(review_id)

    def is_review_current(self, entry_id: str, kb_name: str) -> dict[str, Any]:
        """Check if the latest review is still current (file unchanged).

        Returns dict with ``current`` bool and ``review`` (latest review or None).
        """
        from ..utils.hashing import git_blob_hash

        review = self.db.get_latest_review(entry_id, kb_name)
        if not review:
            return {"current": False, "review": None}

        kb_config = self.config.get_kb(kb_name)
        if not kb_config:
            return {"current": False, "review": review}

        repo = KBRepository(kb_config)
        file_path = repo.find_file(entry_id)
        if not file_path:
            return {"current": False, "review": review}

        content = Path(file_path).read_bytes()
        current_hash = git_blob_hash(content)

        return {
            "current": current_hash == review["content_hash"],
            "review": review,
        }
