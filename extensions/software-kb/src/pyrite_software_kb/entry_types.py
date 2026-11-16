"""Software KB entry types."""

from dataclasses import dataclass, field
from typing import Any, ClassVar

from pyrite.models.core_types import DocumentEntry, NoteEntry
from pyrite.models.protocols import Assignable, Statusable, Temporal

# Enum tuples
ADR_STATUSES = ("proposed", "accepted", "rejected", "deprecated", "superseded")
DESIGN_DOC_STATUSES = ("draft", "review", "approved", "implemented", "obsolete", "active")
STANDARD_CATEGORIES = ("coding", "testing", "api", "git", "documentation", "security", "deployment")
VALIDATION_CATEGORIES = STANDARD_CATEGORIES
CONVENTION_CATEGORIES = STANDARD_CATEGORIES
COMPONENT_KINDS = (
    "module",
    "service",
    "package",
    "library",
    "cli",
    "api",
    "database",
    "application",
    "utility",
    "endpoint",
    "docs",
)
BACKLOG_KINDS = (
    "feature",
    "bug",
    "tech_debt",
    "improvement",
    "spike",
    "enhancement",
    "documentation",
    "docs",
    "task",
    "epic",
    "refactor",
    "chore",
    "process",
)
BACKLOG_STATUSES = (
    "proposed",
    "planned",
    "accepted",
    "in_progress",
    "review",
    "done",
    "retired",
    "deferred",
    "superseded",
    "wont_do",
)
BACKLOG_PRIORITIES = ("critical", "high", "medium", "low")
BACKLOG_EFFORTS = ("XS", "S", "M", "L", "XL")
RUNBOOK_KINDS = ("howto", "troubleshooting", "setup", "operations", "onboarding")
MILESTONE_STATUSES = ("open", "closed")


@dataclass
class ADREntry(Statusable, Temporal, NoteEntry):
    """Architecture Decision Record."""

    adr_number: int = 0
    status: str = "proposed"  # overrides Statusable default
    deciders: list[str] = field(default_factory=list)
    superseded_by: str = ""

    #: Assigned by `pyrite sw new-adr`; a field update must not renumber.
    managed_fields: ClassVar[frozenset[str]] = frozenset({"adr_number"})

    @property
    def entry_type(self) -> str:
        return "adr"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "adr"
        meta["adr_number"] = self.adr_number
        meta["status"] = self.status
        if self.deciders:
            meta["deciders"] = self.deciders
        if self.date:
            meta["date"] = self.date
        if self.superseded_by:
            meta["superseded_by"] = self.superseded_by
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ADREntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            adr_number=int(meta.get("adr_number", 0)),
            status=meta.get("status", "proposed"),
            deciders=meta.get("deciders", []) or [],
            date=meta.get("date", ""),
            superseded_by=meta.get("superseded_by", ""),
        )


@dataclass
class DesignDocEntry(DocumentEntry):
    """Design document or specification."""

    status: str = "draft"
    reviewers: list[str] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "design_doc"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "design_doc"
        if self.status != "draft":
            meta["status"] = self.status
        if self.reviewers:
            meta["reviewers"] = self.reviewers
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "DesignDocEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            date=meta.get("date", ""),
            author=meta.get("author", ""),
            document_type=meta.get("document_type", ""),
            url=meta.get("url", ""),
            status=meta.get("status", "draft"),
            reviewers=meta.get("reviewers", []) or [],
        )


@dataclass
class StandardEntry(NoteEntry):
    """Coding standard or convention."""

    category: str = ""
    enforced: bool = False

    @property
    def entry_type(self) -> str:
        return "standard"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "standard"
        if self.category:
            meta["category"] = self.category
        if self.enforced:
            meta["enforced"] = self.enforced
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "StandardEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            category=meta.get("category", ""),
            enforced=bool(meta.get("enforced", False)),
        )


@dataclass
class ProgrammaticValidationEntry(NoteEntry):
    """Automated check with verifiable pass/fail criteria."""

    category: str = ""
    check_command: str = ""
    pass_criteria: str = ""

    @property
    def entry_type(self) -> str:
        return "programmatic_validation"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "programmatic_validation"
        if self.category:
            meta["category"] = self.category
        if self.check_command:
            meta["check_command"] = self.check_command
        if self.pass_criteria:
            meta["pass_criteria"] = self.pass_criteria
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ProgrammaticValidationEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            category=meta.get("category", ""),
            check_command=meta.get("check_command", ""),
            pass_criteria=meta.get("pass_criteria", ""),
        )


@dataclass
class DevelopmentConventionEntry(NoteEntry):
    """Judgment-based guidance carried as context during work."""

    category: str = ""

    @property
    def entry_type(self) -> str:
        return "development_convention"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "development_convention"
        if self.category:
            meta["category"] = self.category
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "DevelopmentConventionEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            category=meta.get("category", ""),
        )


@dataclass
class ComponentEntry(NoteEntry):
    """Module or service documentation."""

    kind: str = ""
    path: str = ""
    owner: str = ""
    dependencies: list[str] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "component"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "component"
        if self.kind:
            meta["kind"] = self.kind
        if self.path:
            meta["path"] = self.path
        if self.owner:
            meta["owner"] = self.owner
        if self.dependencies:
            meta["dependencies"] = self.dependencies
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ComponentEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            kind=meta.get("kind", ""),
            path=meta.get("path", ""),
            owner=meta.get("owner", ""),
            dependencies=meta.get("dependencies", []) or [],
        )


@dataclass
class BacklogItemEntry(Assignable, Statusable, NoteEntry):
    """Feature, bug, or tech debt tracking item."""

    kind: str = ""
    status: str = "proposed"  # overrides Statusable default
    priority: str = "medium"  # string priority, not Prioritizable (domain-specific vocabulary)
    effort: str = ""
    rank: int = 0  # explicit ordering within priority band, 0 = unranked

    @property
    def entry_type(self) -> str:
        return "backlog_item"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "backlog_item"
        if self.kind:
            meta["kind"] = self.kind
        meta["status"] = self.status
        meta["priority"] = self.priority
        if self.assignee:
            meta["assignee"] = self.assignee
        if self.effort:
            meta["effort"] = self.effort
        # rank 0 ("unranked") is reported even at its default, because it is a
        # valid explicit choice and `sw backlog --sort rank` reads it out of
        # the index. Whether the key reaches the FILE is decided centrally, in
        # Entry._frontmatter_for_file -- which is also what now keeps `status`
        # and `priority` above off a file that never had them. Guarding `rank`
        # here by hand while `priority` two lines up went unguarded is exactly
        # the defect that made the guard central (#46).
        meta["rank"] = self.rank
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "BacklogItemEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            kind=meta.get("kind", ""),
            status=meta.get("status", "proposed"),
            priority=meta.get("priority", "medium"),
            assignee=meta.get("assignee", ""),
            effort=meta.get("effort", ""),
            rank=int(meta.get("rank", 0)),
        )


@dataclass
class RunbookEntry(NoteEntry):
    """How-to guide or operational procedure."""

    runbook_kind: str = ""
    audience: str = ""

    @property
    def entry_type(self) -> str:
        return "runbook"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "runbook"
        if self.runbook_kind:
            meta["runbook_kind"] = self.runbook_kind
        if self.audience:
            meta["audience"] = self.audience
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "RunbookEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            runbook_kind=meta.get("runbook_kind", ""),
            audience=meta.get("audience", ""),
        )


@dataclass
class WorkLogEntry(Temporal, NoteEntry):
    """Structured work session note linked to a backlog item."""

    item_id: str = ""
    decisions: str = ""
    rejected: str = ""
    open_questions: str = ""

    @property
    def entry_type(self) -> str:
        return "work_log"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "work_log"
        if self.item_id:
            meta["item_id"] = self.item_id
        if self.date:
            meta["date"] = self.date
        if self.decisions:
            meta["decisions"] = self.decisions
        if self.rejected:
            meta["rejected"] = self.rejected
        if self.open_questions:
            meta["open_questions"] = self.open_questions
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "WorkLogEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            item_id=meta.get("item_id", ""),
            date=meta.get("date", ""),
            decisions=meta.get("decisions", ""),
            rejected=meta.get("rejected", ""),
            open_questions=meta.get("open_questions", ""),
        )


@dataclass
class MilestoneEntry(Statusable, NoteEntry):
    """Project milestone for grouping backlog items."""

    status: str = "open"

    @property
    def entry_type(self) -> str:
        return "milestone"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "milestone"
        meta["status"] = self.status
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "MilestoneEntry":
        kwargs = cls._base_kwargs(meta, body)
        return cls(
            **kwargs,
            status=meta.get("status", "open"),
        )
