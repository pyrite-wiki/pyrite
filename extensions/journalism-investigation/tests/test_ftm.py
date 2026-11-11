"""Tests for FollowTheMoney import/export."""

import pytest
from pyrite_journalism_investigation.ftm import (
    export_ftm,
    ftm_to_pyrite,
    import_ftm,
    pyrite_to_ftm,
)

from pyrite.config import KBConfig, PyriteConfig, Settings
from pyrite.services.kb_service import KBService
from pyrite.storage.database import PyriteDB

# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def svc(tmp_path):
    """A KBService over one investigation KB: the batch writes go through it."""
    kb_path = tmp_path / "test-kb"
    kb_path.mkdir()
    config = PyriteConfig(
        knowledge_bases=[KBConfig(name="test", path=kb_path, kb_type="journalism-investigation")],
        settings=Settings(index_path=tmp_path / "index.db"),
    )
    db = PyriteDB(tmp_path / "index.db")
    db.register_kb("test", "journalism-investigation", str(kb_path))
    yield KBService(config, db)
    db.close()


@pytest.fixture
def db(svc):
    return svc.db


# =========================================================================
# ftm_to_pyrite tests
# =========================================================================


class TestFtmToPyrite:
    def test_person(self):
        """FtM Person converts to pyrite person entry."""
        ftm = {
            "id": "person-123",
            "schema": "Person",
            "properties": {
                "name": ["John Doe"],
                "nationality": ["US"],
                "birthDate": ["1970-01-01"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "person"
        assert result["title"] == "John Doe"
        assert result["id"] == "ftm-person-123"
        assert result["metadata"]["nationality"] == "US"
        assert result["metadata"]["birthDate"] == "1970-01-01"

    def test_organization(self):
        """FtM Organization converts to pyrite organization entry."""
        ftm = {
            "id": "org-456",
            "schema": "Organization",
            "properties": {
                "name": ["Acme Corp"],
                "jurisdiction": ["DE"],
                "registrationNumber": ["HRB12345"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "organization"
        assert result["title"] == "Acme Corp"
        assert result["id"] == "ftm-org-456"
        assert result["metadata"]["jurisdiction"] == "DE"

    def test_company_maps_to_organization(self):
        """FtM Company also maps to pyrite organization."""
        ftm = {
            "id": "co-1",
            "schema": "Company",
            "properties": {"name": ["Shell Ltd"]},
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "organization"

    def test_legal_entity_maps_to_organization(self):
        """FtM LegalEntity also maps to pyrite organization."""
        ftm = {
            "id": "le-1",
            "schema": "LegalEntity",
            "properties": {"name": ["Trust Fund"]},
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "organization"

    def test_ownership(self):
        """FtM Ownership converts to pyrite ownership entry."""
        ftm = {
            "id": "own-1",
            "schema": "Ownership",
            "properties": {
                "owner": ["alice-id"],
                "asset": ["corp-id"],
                "percentage": ["51"],
                "startDate": ["2020-01-01"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "ownership"
        assert result["id"] == "ftm-own-1"
        assert result["metadata"]["owner"] == "alice-id"
        assert result["metadata"]["asset"] == "corp-id"
        assert result["metadata"]["percentage"] == "51"

    def test_membership(self):
        """FtM Membership converts to pyrite membership entry."""
        ftm = {
            "id": "mem-1",
            "schema": "Membership",
            "properties": {
                "member": ["bob-id"],
                "organization": ["org-id"],
                "role": ["Director"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "membership"
        assert result["metadata"]["person"] == "bob-id"
        assert result["metadata"]["organization"] == "org-id"
        assert result["metadata"]["role"] == "Director"

    def test_payment(self):
        """FtM Payment converts to pyrite transaction entry."""
        ftm = {
            "id": "pay-1",
            "schema": "Payment",
            "properties": {
                "payer": ["sender-id"],
                "beneficiary": ["receiver-id"],
                "amount": ["1000000"],
                "currency": ["USD"],
                "date": ["2021-06-15"],
                "purpose": ["consulting"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "transaction"
        assert result["metadata"]["sender"] == "sender-id"
        assert result["metadata"]["receiver"] == "receiver-id"
        assert result["metadata"]["amount"] == "1000000"
        assert result["metadata"]["currency"] == "USD"
        assert result["date"] == "2021-06-15"

    def test_bank_account(self):
        """FtM BankAccount converts to pyrite account entry."""
        ftm = {
            "id": "ba-1",
            "schema": "BankAccount",
            "properties": {
                "bankName": ["Swiss National Bank"],
                "iban": ["CH93-0000-0000-0000-0000-0"],
                "holder": ["john-id"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "account"
        assert result["metadata"]["account_type"] == "bank"
        assert result["metadata"]["institution"] == "Swiss National Bank"
        assert result["metadata"]["holder"] == "john-id"

    def test_real_estate(self):
        """FtM RealEstate converts to pyrite asset entry."""
        ftm = {
            "id": "re-1",
            "schema": "RealEstate",
            "properties": {
                "name": ["Manhattan Penthouse"],
                "country": ["US"],
                "registrationNumber": ["LOT-123"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "asset"
        assert result["metadata"]["asset_type"] == "real_estate"
        assert result["metadata"]["jurisdiction"] == "US"

    def test_court_case(self):
        """FtM CourtCase converts to pyrite legal_action entry."""
        ftm = {
            "id": "cc-1",
            "schema": "CourtCase",
            "properties": {
                "name": ["US v. Smith"],
                "caseNumber": ["2021-CR-001"],
                "court": ["SDNY"],
                "date": ["2021-03-01"],
            },
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["entry_type"] == "legal_action"
        assert result["metadata"]["case_number"] == "2021-CR-001"
        assert result["metadata"]["jurisdiction"] == "SDNY"
        assert result["date"] == "2021-03-01"

    def test_unmappable_schema_returns_none(self):
        """Unknown FtM schema returns None."""
        ftm = {
            "id": "x-1",
            "schema": "Airplane",
            "properties": {"name": ["Boeing 747"]},
        }
        result = ftm_to_pyrite(ftm)
        assert result is None

    def test_missing_name_uses_schema_and_id(self):
        """When name is missing, title is generated from schema + id."""
        ftm = {
            "id": "p-1",
            "schema": "Person",
            "properties": {},
        }
        result = ftm_to_pyrite(ftm)
        assert result is not None
        assert result["title"]  # should not be empty


# =========================================================================
# pyrite_to_ftm tests
# =========================================================================


class TestPyriteToFtm:
    def test_person_round_trip(self):
        """Pyrite person converts to FtM Person."""
        entry = {
            "id": "ftm-p-1",
            "title": "Jane Smith",
            "entry_type": "person",
            "metadata": {
                "nationality": "GB",
                "birthDate": "1985-05-20",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "Person"
        assert result["properties"]["name"] == ["Jane Smith"]
        assert result["properties"]["nationality"] == ["GB"]
        assert result["properties"]["birthDate"] == ["1985-05-20"]

    def test_organization_to_ftm(self):
        """Pyrite organization converts to FtM Organization."""
        entry = {
            "id": "org-1",
            "title": "Bad Corp",
            "entry_type": "organization",
            "metadata": {"jurisdiction": "RU"},
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "Organization"
        assert result["properties"]["name"] == ["Bad Corp"]
        assert result["properties"]["jurisdiction"] == ["RU"]

    def test_transaction_to_ftm_payment(self):
        """Pyrite transaction converts to FtM Payment."""
        entry = {
            "id": "tx-1",
            "title": "Bribe payment",
            "entry_type": "transaction",
            "date": "2021-06-15",
            "metadata": {
                "sender": "sender-id",
                "receiver": "receiver-id",
                "amount": "50000",
                "currency": "EUR",
                "purpose": "kickback",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "Payment"
        assert result["properties"]["payer"] == ["sender-id"]
        assert result["properties"]["beneficiary"] == ["receiver-id"]
        assert result["properties"]["amount"] == ["50000"]
        assert result["properties"]["currency"] == ["EUR"]
        assert result["properties"]["date"] == ["2021-06-15"]

    def test_ownership_to_ftm(self):
        """Pyrite ownership converts to FtM Ownership."""
        entry = {
            "id": "own-1",
            "title": "Alice owns Corp",
            "entry_type": "ownership",
            "metadata": {
                "owner": "alice-id",
                "asset": "corp-id",
                "percentage": "25",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "Ownership"
        assert result["properties"]["owner"] == ["alice-id"]
        assert result["properties"]["asset"] == ["corp-id"]
        assert result["properties"]["percentage"] == ["25"]

    def test_account_to_ftm(self):
        """Pyrite account converts to FtM BankAccount."""
        entry = {
            "id": "acct-1",
            "title": "Swiss Account",
            "entry_type": "account",
            "metadata": {
                "account_type": "bank",
                "institution": "UBS",
                "holder": "john-id",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "BankAccount"
        assert result["properties"]["bankName"] == ["UBS"]
        assert result["properties"]["holder"] == ["john-id"]

    def test_asset_to_ftm(self):
        """Pyrite asset converts to FtM RealEstate."""
        entry = {
            "id": "asset-1",
            "title": "London Flat",
            "entry_type": "asset",
            "metadata": {
                "asset_type": "real_estate",
                "jurisdiction": "GB",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "RealEstate"
        assert result["properties"]["name"] == ["London Flat"]
        assert result["properties"]["country"] == ["GB"]

    def test_legal_action_to_ftm(self):
        """Pyrite legal_action converts to FtM CourtCase."""
        entry = {
            "id": "la-1",
            "title": "US v. Doe",
            "entry_type": "legal_action",
            "date": "2022-01-01",
            "metadata": {
                "case_number": "22-CR-100",
                "jurisdiction": "EDNY",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "CourtCase"
        assert result["properties"]["caseNumber"] == ["22-CR-100"]
        assert result["properties"]["court"] == ["EDNY"]
        assert result["properties"]["date"] == ["2022-01-01"]

    def test_membership_to_ftm(self):
        """Pyrite membership converts to FtM Membership."""
        entry = {
            "id": "mem-1",
            "title": "Bob in Org",
            "entry_type": "membership",
            "metadata": {
                "person": "bob-id",
                "organization": "org-id",
                "role": "CEO",
            },
        }
        result = pyrite_to_ftm(entry)
        assert result is not None
        assert result["schema"] == "Membership"
        assert result["properties"]["member"] == ["bob-id"]
        assert result["properties"]["organization"] == ["org-id"]
        assert result["properties"]["role"] == ["CEO"]

    def test_unmappable_type_returns_none(self):
        """Unknown pyrite entry type returns None."""
        entry = {
            "id": "x-1",
            "title": "Some claim",
            "entry_type": "claim",
            "metadata": {},
        }
        result = pyrite_to_ftm(entry)
        assert result is None


# =========================================================================
# import_ftm tests
# =========================================================================


class TestImportFtm:
    def test_import_batch(self, svc, db):
        """Import a batch of FtM entities into the KB."""
        entities = [
            {
                "id": "p-1",
                "schema": "Person",
                "properties": {"name": ["Alice"]},
            },
            {
                "id": "o-1",
                "schema": "Organization",
                "properties": {"name": ["Evil Corp"]},
            },
            {
                "id": "pay-1",
                "schema": "Payment",
                "properties": {
                    "payer": ["p-1"],
                    "beneficiary": ["o-1"],
                    "amount": ["500"],
                    "currency": ["USD"],
                    "date": ["2021-01-01"],
                },
            },
        ]
        result = import_ftm(svc, "test", entities)
        assert result["imported"] == 3
        assert result["skipped"] == 0
        assert result["unmapped"] == 0
        assert result["errors"] == 0
        assert len(result["entries"]) == 3

        # Verify entries are in DB
        entry = db.get_entry("ftm-p-1", "test")
        assert entry is not None
        assert entry["title"] == "Alice"

    def test_import_skips_unmapped_schemas(self, svc, db):
        """Unmappable schemas are skipped and counted."""
        entities = [
            {"id": "p-1", "schema": "Person", "properties": {"name": ["Alice"]}},
            {"id": "x-1", "schema": "Airplane", "properties": {"name": ["Jet"]}},
        ]
        result = import_ftm(svc, "test", entities)
        assert result["imported"] == 1
        assert result["unmapped"] == 1
        assert "Airplane" in result["unmapped_schemas"]

    def test_import_skips_duplicates(self, svc, db):
        """Entities with existing IDs are skipped."""
        entities = [
            {"id": "p-1", "schema": "Person", "properties": {"name": ["Alice"]}},
        ]
        # First import
        result1 = import_ftm(svc, "test", entities)
        assert result1["imported"] == 1

        # Second import — duplicate
        result2 = import_ftm(svc, "test", entities)
        assert result2["imported"] == 0
        assert result2["skipped"] == 1

    def test_import_dry_run(self, svc, db):
        """Dry run reports what would happen without persisting."""
        entities = [
            {"id": "p-1", "schema": "Person", "properties": {"name": ["Alice"]}},
        ]
        result = import_ftm(svc, "test", entities, dry_run=True)
        assert result["imported"] == 1  # count of what would be imported
        assert len(result["entries"]) == 1
        assert result["entries"][0]["status"] == "would_import"

        # Nothing in DB
        entry = db.get_entry("ftm-p-1", "test")
        assert entry is None


# =========================================================================
# export_ftm tests
# =========================================================================


class TestExportFtm:
    def test_export_entries(self, db):
        """Export KB entries as FtM JSON."""
        # Insert some entries
        db.upsert_entry(
            {
                "id": "alice",
                "kb_name": "test",
                "title": "Alice",
                "entry_type": "person",
                "metadata": {"nationality": "US"},
            }
        )
        db.upsert_entry(
            {
                "id": "evil-corp",
                "kb_name": "test",
                "title": "Evil Corp",
                "entry_type": "organization",
                "metadata": {"jurisdiction": "RU"},
            }
        )

        result = export_ftm(db, "test")
        assert len(result) == 2
        schemas = {e["schema"] for e in result}
        assert "Person" in schemas
        assert "Organization" in schemas

    def test_export_with_type_filter(self, db):
        """Export filters by entry type."""
        db.upsert_entry(
            {
                "id": "alice",
                "kb_name": "test",
                "title": "Alice",
                "entry_type": "person",
                "metadata": {"nationality": "US"},
            }
        )
        db.upsert_entry(
            {
                "id": "evil-corp",
                "kb_name": "test",
                "title": "Evil Corp",
                "entry_type": "organization",
                "metadata": {"jurisdiction": "RU"},
            }
        )

        result = export_ftm(db, "test", entry_types=["person"])
        assert len(result) == 1
        assert result[0]["schema"] == "Person"

    def test_export_skips_unmappable_types(self, db):
        """Entries with unmappable types are skipped."""
        db.upsert_entry(
            {
                "id": "claim-1",
                "kb_name": "test",
                "title": "Some Claim",
                "entry_type": "claim",
                "metadata": {},
            }
        )
        db.upsert_entry(
            {
                "id": "alice",
                "kb_name": "test",
                "title": "Alice",
                "entry_type": "person",
                "metadata": {},
            }
        )

        result = export_ftm(db, "test")
        assert len(result) == 1
        assert result[0]["schema"] == "Person"
