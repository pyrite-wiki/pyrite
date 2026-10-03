"""Journalism Investigation entry types for investigative research."""

from dataclasses import dataclass, field
from typing import Any

from pyrite.models.base import Entry
from pyrite.models.core_types import DocumentEntry, EventEntry
from pyrite.schema import EventStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_event_status(meta: dict[str, Any]) -> "EventStatus":
    """Parse EventStatus from frontmatter, defaulting to CONFIRMED."""
    status_str = meta.get("status", "confirmed")
    try:
        return EventStatus(status_str)
    except ValueError:
        return EventStatus.CONFIRMED


def _str_or_empty(value: Any) -> str:
    """Convert a value to string, treating None as empty string."""
    if value is None:
        return ""
    return str(value)


# ---------------------------------------------------------------------------
# Entity types
# ---------------------------------------------------------------------------

# Enum tuples for validation
ASSET_TYPES = (
    "real_estate",
    "vehicle",
    "vessel",
    "aircraft",
    "luxury_good",
    "intellectual_property",
    "other",
)
ACCOUNT_TYPES = (
    "bank",
    "brokerage",
    "crypto_wallet",
    "shell_company",
    "trust",
    "other",
)
RELIABILITY_LEVELS = ("high", "medium", "low", "unknown")
CLASSIFICATIONS = (
    "public",
    "leaked",
    "foia",
    "court_filing",
    "financial_disclosure",
    "corporate_registry",
    "other",
)

# Event enum tuples
VERIFICATION_STATUSES = ("unverified", "partially_verified", "verified", "disputed")
TRANSACTION_METHODS = ("wire", "cash", "crypto", "check", "other")
TRANSACTION_TYPES = (
    "payment",
    "grant",
    "donation",
    "loan",
    "investment",
    "bribe",
    "kickback",
    "other",
)
CASE_TYPES = (
    "criminal",
    "civil",
    "regulatory",
    "sanctions",
    "indictment",
    "subpoena",
    "other",
)
CASE_STATUSES = (
    "filed",
    "pending",
    "settled",
    "dismissed",
    "convicted",
    "acquitted",
)

# Connection enum tuples
FUNDING_MECHANISMS = (
    "grant",
    "donation",
    "contract",
    "lobbying",
    "dark_money",
    "other",
)

# Evidence enum tuples
EVIDENCE_TYPES = (
    "document",
    "testimony",
    "record",
    "data",
    "photo",
    "video",
    "other",
)

# Claim enum tuples
CLAIM_STATUSES = (
    "unverified",
    "partially_verified",
    "corroborated",
    "disputed",
    "retracted",
)
CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass
class AssetEntry(Entry):
    """A tracked asset — real estate, vehicle, vessel, etc."""

    asset_type: str = ""
    value: str = ""
    currency: str = ""
    jurisdiction: str = ""
    registered_owner: str = ""
    acquisition_date: str = ""
    description: str = ""

    @property
    def entry_type(self) -> str:
        return "asset"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "asset"
        if self.asset_type:
            meta["asset_type"] = self.asset_type
        if self.value:
            meta["value"] = self.value
        if self.currency:
            meta["currency"] = self.currency
        if self.jurisdiction:
            meta["jurisdiction"] = self.jurisdiction
        if self.registered_owner:
            meta["registered_owner"] = self.registered_owner
        if self.acquisition_date:
            meta["acquisition_date"] = self.acquisition_date
        if self.description:
            meta["description"] = self.description
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "AssetEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            asset_type=meta.get("asset_type", ""),
            value=_str_or_empty(meta.get("value", "")),
            currency=meta.get("currency", ""),
            jurisdiction=meta.get("jurisdiction", ""),
            registered_owner=meta.get("registered_owner", ""),
            acquisition_date=_str_or_empty(meta.get("acquisition_date", "")),
            description=meta.get("description", ""),
        )


@dataclass
class AccountEntry(Entry):
    """A financial account — bank, brokerage, crypto wallet, etc."""

    account_type: str = ""
    institution: str = ""
    jurisdiction: str = ""
    holder: str = ""
    opened_date: str = ""
    closed_date: str = ""

    @property
    def entry_type(self) -> str:
        return "account"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "account"
        if self.account_type:
            meta["account_type"] = self.account_type
        if self.institution:
            meta["institution"] = self.institution
        if self.jurisdiction:
            meta["jurisdiction"] = self.jurisdiction
        if self.holder:
            meta["holder"] = self.holder
        if self.opened_date:
            meta["opened_date"] = self.opened_date
        if self.closed_date:
            meta["closed_date"] = self.closed_date
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "AccountEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            account_type=meta.get("account_type", ""),
            institution=meta.get("institution", ""),
            jurisdiction=meta.get("jurisdiction", ""),
            holder=meta.get("holder", ""),
            opened_date=_str_or_empty(meta.get("opened_date", "")),
            closed_date=_str_or_empty(meta.get("closed_date", "")),
        )


@dataclass
class DocumentSourceEntry(DocumentEntry):
    """A source document with reliability and classification metadata."""

    reliability: str = "unknown"
    classification: str = ""
    obtained_date: str = ""
    obtained_method: str = ""

    @property
    def entry_type(self) -> str:
        return "document_source"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "document_source"
        meta["reliability"] = self.reliability
        if self.classification:
            meta["classification"] = self.classification
        if self.obtained_date:
            meta["obtained_date"] = self.obtained_date
        if self.obtained_method:
            meta["obtained_method"] = self.obtained_method
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "DocumentSourceEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            date=meta.get("date", ""),
            author=meta.get("author", ""),
            document_type=meta.get("document_type", ""),
            url=meta.get("url", ""),
            reliability=meta.get("reliability", "unknown"),
            classification=meta.get("classification", ""),
            obtained_date=_str_or_empty(meta.get("obtained_date", "")),
            obtained_method=meta.get("obtained_method", ""),
        )


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


@dataclass
class InvestigationEventEntry(EventEntry):
    """An event in an investigation with actors, source references, and verification status."""

    actors: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    verification_status: str = "unverified"

    @property
    def entry_type(self) -> str:
        return "investigation_event"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "investigation_event"
        if self.actors:
            meta["actors"] = self.actors
        if self.source_refs:
            meta["source_refs"] = self.source_refs
        meta["verification_status"] = self.verification_status
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "InvestigationEventEntry":
        kw = cls._base_kwargs(meta, body)

        return cls(
            **kw,
            date=_str_or_empty(meta.get("date", "")),
            status=_parse_event_status(meta),
            location=meta.get("location", ""),
            participants=meta.get("actors", meta.get("participants", [])) or [],
            notes=meta.get("notes", ""),
            actors=meta.get("actors", []) or [],
            source_refs=meta.get("source_refs", []) or [],
            verification_status=meta.get("verification_status", "unverified"),
        )


@dataclass
class TransactionEntry(EventEntry):
    """A financial transaction — payment, bribe, grant, etc."""

    amount: str = ""
    currency: str = ""
    sender: str = ""
    receiver: str = ""
    method: str = ""
    purpose: str = ""
    transaction_type: str = ""

    @property
    def entry_type(self) -> str:
        return "transaction"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "transaction"
        if self.amount:
            meta["amount"] = self.amount
        if self.currency:
            meta["currency"] = self.currency
        meta["sender"] = self.sender
        meta["receiver"] = self.receiver
        if self.method:
            meta["method"] = self.method
        if self.purpose:
            meta["purpose"] = self.purpose
        if self.transaction_type:
            meta["transaction_type"] = self.transaction_type
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "TransactionEntry":
        kw = cls._base_kwargs(meta, body)

        return cls(
            **kw,
            date=_str_or_empty(meta.get("date", "")),
            status=_parse_event_status(meta),
            location=meta.get("location", ""),
            participants=meta.get("actors", meta.get("participants", [])) or [],
            notes=meta.get("notes", ""),
            amount=_str_or_empty(meta.get("amount", "")),
            currency=meta.get("currency", ""),
            sender=meta.get("sender", ""),
            receiver=meta.get("receiver", ""),
            method=meta.get("method", ""),
            purpose=meta.get("purpose", ""),
            transaction_type=meta.get("transaction_type", ""),
        )


@dataclass
class LegalActionEntry(EventEntry):
    """A legal or regulatory action — case, indictment, sanctions, etc."""

    case_type: str = ""
    jurisdiction: str = ""
    parties: list[str] = field(default_factory=list)
    case_status: str = ""
    outcome: str = ""
    case_number: str = ""

    @property
    def entry_type(self) -> str:
        return "legal_action"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = super().to_frontmatter()
        meta["type"] = "legal_action"
        if self.case_type:
            meta["case_type"] = self.case_type
        if self.jurisdiction:
            meta["jurisdiction"] = self.jurisdiction
        if self.parties:
            meta["parties"] = self.parties
        if self.case_status:
            meta["case_status"] = self.case_status
        if self.outcome:
            meta["outcome"] = self.outcome
        if self.case_number:
            meta["case_number"] = self.case_number
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "LegalActionEntry":
        kw = cls._base_kwargs(meta, body)

        return cls(
            **kw,
            date=_str_or_empty(meta.get("date", "")),
            status=_parse_event_status(meta),
            location=meta.get("location", ""),
            participants=meta.get("actors", meta.get("participants", [])) or [],
            notes=meta.get("notes", ""),
            case_type=meta.get("case_type", ""),
            jurisdiction=meta.get("jurisdiction", ""),
            parties=meta.get("parties", []) or [],
            case_status=meta.get("case_status", ""),
            outcome=meta.get("outcome", ""),
            case_number=meta.get("case_number", ""),
        )


# ---------------------------------------------------------------------------
# Evidence type
# ---------------------------------------------------------------------------


@dataclass
class EvidenceEntry(Entry):
    """A piece of evidence linked to a source document and supporting claims."""

    evidence_type: str = ""
    source_document: str = ""
    reliability: str = "unknown"
    obtained_date: str = ""
    chain_of_custody: str = ""

    @property
    def entry_type(self) -> str:
        return "evidence"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "evidence"
        if self.evidence_type:
            meta["evidence_type"] = self.evidence_type
        if self.source_document:
            meta["source_document"] = self.source_document
        meta["reliability"] = self.reliability
        if self.obtained_date:
            meta["obtained_date"] = self.obtained_date
        if self.chain_of_custody:
            meta["chain_of_custody"] = self.chain_of_custody
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "EvidenceEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            evidence_type=meta.get("evidence_type", ""),
            source_document=meta.get("source_document", ""),
            reliability=meta.get("reliability", "unknown"),
            obtained_date=_str_or_empty(meta.get("obtained_date", "")),
            chain_of_custody=meta.get("chain_of_custody", ""),
        )


# ---------------------------------------------------------------------------
# Claim type
# ---------------------------------------------------------------------------

# Status transition map: from_status -> set of valid to_statuses
_CLAIM_TRANSITIONS: dict[str, set[str]] = {
    "unverified": {"partially_verified"},
    "partially_verified": {"corroborated", "disputed"},
    "disputed": {"retracted", "corroborated"},
    "corroborated": set(),
    "retracted": set(),
}


@dataclass
class ClaimEntry(Entry):
    """A factual assertion with verification lifecycle tracking."""

    assertion: str = ""
    confidence: str = "low"
    claim_status: str = "unverified"
    evidence_refs: list[str] = field(default_factory=list)
    disputed_by: list[str] = field(default_factory=list)

    @property
    def entry_type(self) -> str:
        return "claim"

    @staticmethod
    def valid_transitions(from_status: str) -> set[str]:
        """Return valid target statuses for a given current status."""
        return _CLAIM_TRANSITIONS.get(from_status, set())

    def can_transition_to(self, target_status: str) -> bool:
        """Check if the claim can transition to the target status."""
        return target_status in self.valid_transitions(self.claim_status)

    def auto_confidence(self, source_tiers: dict[str, int] | None = None) -> str:
        """Calculate confidence from evidence count, tiers, and dispute status.

        Args:
            source_tiers: Optional mapping of evidence_ref -> tier number.
                If provided and sources span multiple tiers, confidence is "high".
        """
        if self.disputed_by:
            return "low"
        if len(self.evidence_refs) < 2:
            return "low"
        # Check for cross-tier corroboration
        if source_tiers is not None:
            tiers = {source_tiers.get(ref, 0) for ref in self.evidence_refs}
            # Filter out default tier (0 = unknown ref)
            tiers.discard(0)
            if len(tiers) >= 2:
                return "high"
        return "medium"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "claim"
        if self.assertion:
            meta["assertion"] = self.assertion
        meta["confidence"] = self.confidence
        meta["claim_status"] = self.claim_status
        if self.evidence_refs:
            meta["evidence_refs"] = self.evidence_refs
        if self.disputed_by:
            meta["disputed_by"] = self.disputed_by
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "ClaimEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            assertion=meta.get("assertion", ""),
            confidence=meta.get("confidence", "low"),
            claim_status=meta.get("claim_status", "unverified"),
            evidence_refs=meta.get("evidence_refs", []) or [],
            disputed_by=meta.get("disputed_by", []) or [],
        )


# ---------------------------------------------------------------------------
# Connection types (edge-entities)
# ---------------------------------------------------------------------------


@dataclass
class OwnershipEntry(Entry):
    """Ownership relationship between an entity and an asset/organization."""

    owner: str = ""
    asset: str = ""
    percentage: str = ""
    start_date: str = ""
    end_date: str = ""
    legal_basis: str = ""
    beneficial: bool = False

    @property
    def entry_type(self) -> str:
        return "ownership"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "ownership"
        meta["owner"] = self.owner
        meta["asset"] = self.asset
        if self.percentage:
            meta["percentage"] = self.percentage
        if self.start_date:
            meta["start_date"] = self.start_date
        if self.end_date:
            meta["end_date"] = self.end_date
        if self.legal_basis:
            meta["legal_basis"] = self.legal_basis
        meta["beneficial"] = self.beneficial
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "OwnershipEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            owner=meta.get("owner", ""),
            asset=meta.get("asset", ""),
            percentage=_str_or_empty(meta.get("percentage", "")),
            start_date=_str_or_empty(meta.get("start_date", "")),
            end_date=_str_or_empty(meta.get("end_date", "")),
            legal_basis=meta.get("legal_basis", ""),
            beneficial=bool(meta.get("beneficial", False)),
        )


@dataclass
class MembershipEntry(Entry):
    """Membership relationship between a person and an organization."""

    person: str = ""
    organization: str = ""
    role: str = ""
    start_date: str = ""
    end_date: str = ""

    @property
    def entry_type(self) -> str:
        return "membership"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "membership"
        meta["person"] = self.person
        meta["organization"] = self.organization
        if self.role:
            meta["role"] = self.role
        if self.start_date:
            meta["start_date"] = self.start_date
        if self.end_date:
            meta["end_date"] = self.end_date
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "MembershipEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            person=meta.get("person", ""),
            organization=meta.get("organization", ""),
            role=meta.get("role", ""),
            start_date=_str_or_empty(meta.get("start_date", "")),
            end_date=_str_or_empty(meta.get("end_date", "")),
        )


@dataclass
class FundingEntry(Entry):
    """Funding relationship between entities."""

    funder: str = ""
    recipient: str = ""
    amount: str = ""
    currency: str = ""
    date_range: str = ""
    purpose: str = ""
    mechanism: str = ""

    @property
    def entry_type(self) -> str:
        return "funding"

    def to_frontmatter(self) -> dict[str, Any]:
        meta = self._base_frontmatter()
        meta["type"] = "funding"
        meta["funder"] = self.funder
        meta["recipient"] = self.recipient
        if self.amount:
            meta["amount"] = self.amount
        if self.currency:
            meta["currency"] = self.currency
        if self.date_range:
            meta["date_range"] = self.date_range
        if self.purpose:
            meta["purpose"] = self.purpose
        if self.mechanism:
            meta["mechanism"] = self.mechanism
        if self.summary:
            meta["summary"] = self.summary
        return meta

    @classmethod
    def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "FundingEntry":
        kw = cls._base_kwargs(meta, body)
        return cls(
            **kw,
            funder=meta.get("funder", ""),
            recipient=meta.get("recipient", ""),
            amount=_str_or_empty(meta.get("amount", "")),
            currency=meta.get("currency", ""),
            date_range=meta.get("date_range", ""),
            purpose=meta.get("purpose", ""),
            mechanism=meta.get("mechanism", ""),
        )
