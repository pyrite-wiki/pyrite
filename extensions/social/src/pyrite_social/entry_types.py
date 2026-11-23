"""Social KB entry types."""

from dataclasses import dataclass
from typing import Any

from pyrite.models.core_types import NoteEntry, PersonEntry
from pyrite.utils.parse import safe_int

WRITEUP_TYPES = ("essay", "story", "review", "howto", "opinion")


@dataclass
class WriteupEntry(NoteEntry):
    """A user-authored writeup in a social knowledge base.

    Stored in folder-per-author layout: writeups/<author_id>/slug.md
    """

    author_id: str = ""
    writeup_type: str = "essay"  # essay, story, review, howto, opinion
    allow_voting: bool = True

    @property
    def entry_type(self) -> str:
        return "writeup"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "writeup"
        if self.author_id:
            meta["author_id"] = self.author_id
        meta["writeup_type"] = self.writeup_type
        if not self.allow_voting:
            meta["allow_voting"] = self.allow_voting
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "WriteupEntry":
        kw = cls._base_kwargs(meta, body)
        kw["author_id"] = meta.get("author_id", "")
        kw["writeup_type"] = meta.get("writeup_type", "essay")
        kw["allow_voting"] = meta.get("allow_voting", True)
        return cls(**kw)


@dataclass
class UserProfileEntry(PersonEntry):
    """A user profile in a social knowledge base.

    Stored at: users/<user_id>.md
    """

    reputation: int = 0
    join_date: str = ""
    writeup_count: int = 0

    @property
    def entry_type(self) -> str:
        return "user_profile"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "user_profile"
        meta["reputation"] = self.reputation
        if self.join_date:
            meta["join_date"] = self.join_date
        meta["writeup_count"] = self.writeup_count
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "UserProfileEntry":
        from pyrite.schema import ResearchStatus

        status_str = meta.get("research_status", "stub")
        try:
            research_status = ResearchStatus(status_str)
        except ValueError:
            research_status = ResearchStatus.STUB

        kw = cls._base_kwargs(meta, body)
        kw["role"] = meta.get("role", "")
        kw["affiliations"] = meta.get("affiliations", []) or []
        kw["research_status"] = research_status
        kw["reputation"] = safe_int(meta.get("reputation", 0))
        kw["join_date"] = meta.get("join_date", "")
        kw["writeup_count"] = safe_int(meta.get("writeup_count", 0))
        return cls(**kw)
