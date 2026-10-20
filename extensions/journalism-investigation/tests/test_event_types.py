"""Round-trip tests for journalism-investigation event entry types."""

import pytest
from pyrite_journalism_investigation.entry_types import (
    InvestigationEventEntry,
    LegalActionEntry,
    TransactionEntry,
)


class TestInvestigationEventEntry:
    def test_participants_alias_populates_actors(self):
        entry = InvestigationEventEntry.from_frontmatter(
            {"id": "event-alias", "title": "Event", "participants": ["alice"]}, ""
        )
        assert entry.actors == ["alice"]
        assert entry.participants == ["alice"]
        frontmatter = entry.to_frontmatter()
        assert frontmatter["actors"] == ["alice"]
        assert "participants" not in frontmatter
        reloaded = InvestigationEventEntry.from_frontmatter(frontmatter, "")
        assert reloaded.actors == reloaded.participants == ["alice"]

    @pytest.mark.control(reason="Preserve canonical precedence and empty-value handling")
    @pytest.mark.parametrize(
        ("fields", "expected"),
        [
            ({"actors": ["alice"]}, ["alice"]),
            ({"actors": ["alice"], "participants": ["bob"]}, ["alice"]),
            ({"actors": [], "participants": ["bob"]}, []),
            ({"actors": None, "participants": ["bob"]}, []),
            ({"actors": [], "participants": []}, []),
            ({"participants": None}, []),
        ],
    )
    def test_actor_alias_precedence(self, fields, expected):
        entry = InvestigationEventEntry.from_frontmatter({"title": "Event", **fields}, "")
        assert entry.actors == entry.participants == expected
        frontmatter = entry.to_frontmatter()
        assert frontmatter.get("actors", []) == expected
        assert "participants" not in frontmatter

    def test_entry_type(self):
        e = InvestigationEventEntry(id="event-panama-papers-leak", title="Panama Papers Leak")
        assert e.entry_type == "investigation_event"

    def test_round_trip(self):
        meta = {
            "id": "event-panama-papers-leak",
            "title": "Panama Papers Leak Published",
            "type": "investigation_event",
            "date": "2016-04-03",
            "actors": ["[[icij]]", "suddeutsche-zeitung"],
            "source_refs": ["[[panama-papers-doc-4427]]"],
            "importance": 10,
            "verification_status": "verified",
            "location": "Global",
            "tags": ["panama-papers", "leak"],
        }
        body = "ICIJ publishes the Panama Papers, the largest leak in history."
        entry = InvestigationEventEntry.from_frontmatter(meta, body)

        assert entry.id == "event-panama-papers-leak"
        assert entry.date == "2016-04-03"
        assert entry.actors == ["[[icij]]", "suddeutsche-zeitung"]
        assert entry.source_refs == ["[[panama-papers-doc-4427]]"]
        assert entry.importance == 10
        assert entry.verification_status == "verified"
        assert entry.location == "Global"
        assert entry.body == body

        fm = entry.to_frontmatter()
        assert fm["type"] == "investigation_event"
        assert fm["date"] == "2016-04-03"
        assert fm["actors"] == ["[[icij]]", "suddeutsche-zeitung"]
        assert fm["verification_status"] == "verified"

    def test_defaults(self):
        entry = InvestigationEventEntry.from_frontmatter({"title": "Unknown Event"}, "")
        assert entry.actors == []
        assert entry.source_refs == []
        assert entry.verification_status == "unverified"

    def test_verification_status_values(self):
        valid = ["unverified", "partially_verified", "verified", "disputed"]
        for v in valid:
            entry = InvestigationEventEntry.from_frontmatter(
                {"title": "Test", "verification_status": v}, ""
            )
            assert entry.verification_status == v

    def test_to_frontmatter_field_presence(self):
        # 44f81f3: meaningful enum defaults are written so readers/agents
        # don't have to know the default; empty lists stay omitted.
        entry = InvestigationEventEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "actors" not in fm  # empty list omitted
        assert "source_refs" not in fm  # empty list omitted
        assert fm["verification_status"] == "unverified"  # default written


class TestTransactionEntry:
    def test_entry_type(self):
        e = TransactionEntry(id="txn-putin-company-x", title="Putin payment to Company X")
        assert e.entry_type == "transaction"

    def test_round_trip(self):
        meta = {
            "id": "txn-bribe-001",
            "title": "Payment to Official Y",
            "type": "transaction",
            "date": "2015-08-20",
            "amount": "500000",
            "currency": "USD",
            "sender": "[[company-x]]",
            "receiver": "[[official-y]]",
            "method": "wire",
            "purpose": "Consulting fee (suspected bribe)",
            "transaction_type": "bribe",
            "importance": 9,
            "tags": ["corruption"],
        }
        body = "Wire transfer from Company X to Official Y, disguised as consulting."
        entry = TransactionEntry.from_frontmatter(meta, body)

        assert entry.id == "txn-bribe-001"
        assert entry.date == "2015-08-20"
        assert entry.amount == "500000"
        assert entry.currency == "USD"
        assert entry.sender == "[[company-x]]"
        assert entry.receiver == "[[official-y]]"
        assert entry.method == "wire"
        assert entry.purpose == "Consulting fee (suspected bribe)"
        assert entry.transaction_type == "bribe"
        assert entry.body == body

        fm = entry.to_frontmatter()
        assert fm["type"] == "transaction"
        assert fm["amount"] == "500000"
        assert fm["sender"] == "[[company-x]]"
        assert fm["receiver"] == "[[official-y]]"
        assert fm["method"] == "wire"
        assert fm["transaction_type"] == "bribe"

    def test_defaults(self):
        entry = TransactionEntry.from_frontmatter({"title": "Unknown Txn"}, "")
        assert entry.amount == ""
        assert entry.currency == ""
        assert entry.sender == ""
        assert entry.receiver == ""
        assert entry.method == ""
        assert entry.purpose == ""
        assert entry.transaction_type == ""

    def test_method_values(self):
        valid = ["wire", "cash", "crypto", "check", "other"]
        for m in valid:
            entry = TransactionEntry.from_frontmatter({"title": "Test", "method": m}, "")
            assert entry.method == m

    def test_transaction_type_values(self):
        valid = [
            "payment",
            "grant",
            "donation",
            "loan",
            "investment",
            "bribe",
            "kickback",
            "other",
        ]
        for t in valid:
            entry = TransactionEntry.from_frontmatter({"title": "Test", "transaction_type": t}, "")
            assert entry.transaction_type == t

    def test_to_frontmatter_field_presence(self):
        # 44f81f3: structural edge endpoints (sender/receiver) are always
        # written so the transaction is unambiguous; other empty fields omitted.
        entry = TransactionEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert fm["sender"] == ""  # endpoint always written
        assert fm["receiver"] == ""  # endpoint always written
        assert "amount" not in fm  # empty string omitted
        assert "currency" not in fm  # empty string omitted
        assert "method" not in fm  # empty string omitted
        assert "purpose" not in fm  # empty string omitted
        assert "transaction_type" not in fm  # empty string omitted


class TestLegalActionEntry:
    def test_entry_type(self):
        e = LegalActionEntry(id="case-usa-v-company-x", title="USA v. Company X")
        assert e.entry_type == "legal_action"

    def test_round_trip(self):
        meta = {
            "id": "case-usa-v-company-x",
            "title": "USA v. Company X",
            "type": "legal_action",
            "date": "2019-03-15",
            "case_type": "criminal",
            "jurisdiction": "United States",
            "parties": ["[[company-x]]", "[[doj]]"],
            "case_status": "convicted",
            "outcome": "Guilty plea, $2.5B fine",
            "case_number": "1:19-cr-00123",
            "importance": 8,
        }
        body = "DOJ criminal prosecution of Company X for sanctions violations."
        entry = LegalActionEntry.from_frontmatter(meta, body)

        assert entry.id == "case-usa-v-company-x"
        assert entry.date == "2019-03-15"
        assert entry.case_type == "criminal"
        assert entry.jurisdiction == "United States"
        assert entry.parties == ["[[company-x]]", "[[doj]]"]
        assert entry.case_status == "convicted"
        assert entry.outcome == "Guilty plea, $2.5B fine"
        assert entry.case_number == "1:19-cr-00123"
        assert entry.body == body

        fm = entry.to_frontmatter()
        assert fm["type"] == "legal_action"
        assert fm["case_type"] == "criminal"
        assert fm["jurisdiction"] == "United States"
        assert fm["parties"] == ["[[company-x]]", "[[doj]]"]
        assert fm["case_status"] == "convicted"
        assert fm["case_number"] == "1:19-cr-00123"

    def test_defaults(self):
        entry = LegalActionEntry.from_frontmatter({"title": "Unknown Case"}, "")
        assert entry.case_type == ""
        assert entry.jurisdiction == ""
        assert entry.parties == []
        assert entry.case_status == ""
        assert entry.outcome == ""
        assert entry.case_number == ""

    def test_case_type_values(self):
        valid = [
            "criminal",
            "civil",
            "regulatory",
            "sanctions",
            "indictment",
            "subpoena",
            "other",
        ]
        for ct in valid:
            entry = LegalActionEntry.from_frontmatter({"title": "Test", "case_type": ct}, "")
            assert entry.case_type == ct

    def test_case_status_values(self):
        valid = [
            "filed",
            "pending",
            "settled",
            "dismissed",
            "convicted",
            "acquitted",
        ]
        for s in valid:
            entry = LegalActionEntry.from_frontmatter({"title": "Test", "case_status": s}, "")
            assert entry.case_status == s

    def test_to_frontmatter_omits_empty(self):
        entry = LegalActionEntry(id="test", title="Test")
        fm = entry.to_frontmatter()
        assert "case_type" not in fm
        assert "jurisdiction" not in fm
        assert "parties" not in fm
        assert "case_status" not in fm
        assert "outcome" not in fm
        assert "case_number" not in fm


class TestInvestigationEventEntryAsABaseClass:
    """`InvestigationEventEntry` is the load-bearing base an out-of-tree
    extension derives its own event type from (the deleted cascade
    extension's `timeline_event` did; the only tests of that contract went
    with it, #953). A subclass that adds a field and overrides nothing else
    must still read and write every JI field through the base class.
    """

    def test_a_subclass_round_trips_the_ji_fields_and_its_own(self):
        from dataclasses import dataclass, field
        from typing import Any

        @dataclass
        class HearingEntry(InvestigationEventEntry):
            capture_lanes: list[str] = field(default_factory=list)

            @property
            def entry_type(self) -> str:
                return "hearing"

            def to_frontmatter(self) -> dict[str, Any]:
                meta = super().to_frontmatter()
                meta["type"] = "hearing"
                if self.capture_lanes:
                    meta["capture_lanes"] = self.capture_lanes
                return meta

            @classmethod
            def from_frontmatter(cls, meta: dict[str, Any], body: str) -> "HearingEntry":
                entry = super().from_frontmatter(meta, body)
                entry.capture_lanes = list(meta.get("capture_lanes", []) or [])
                return entry

        meta = {
            "id": "hearing-1",
            "title": "The hearing",
            "type": "hearing",
            "date": "2026-01-01",
            "actors": ["[[jane-doe]]"],
            "source_refs": ["[[transcript-1]]"],
            "verification_status": "verified",
            "location": "Room 4",
            "importance": 7,
            "tags": ["hearing"],
            "capture_lanes": ["regulatory"],
        }
        entry = HearingEntry.from_frontmatter(meta, "Body.")
        assert isinstance(entry, HearingEntry)
        assert entry.entry_type == "hearing"
        assert (entry.actors, entry.source_refs, entry.verification_status) == (
            ["[[jane-doe]]"],
            ["[[transcript-1]]"],
            "verified",
        )
        assert (entry.date, entry.location, entry.capture_lanes) == (
            "2026-01-01",
            "Room 4",
            ["regulatory"],
        )

        out = entry.to_frontmatter()
        assert out["type"] == "hearing"
        for key in (
            "actors",
            "source_refs",
            "verification_status",
            "date",
            "location",
            "capture_lanes",
        ):
            assert out[key] == meta[key], key
        # And back again through the same class: nothing of the base's is lost.
        again = HearingEntry.from_frontmatter(out, "Body.")
        assert again.to_frontmatter() == out
