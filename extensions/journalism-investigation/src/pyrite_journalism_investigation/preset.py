"""Journalism Investigation KB preset definitions."""

JOURNALISM_INVESTIGATION_PRESET = {
    "name": "my-investigation",
    "description": "Investigative journalism knowledge base with entities, events, sources, and relationship tracking",
    "types": {
        # Core types reused from pyrite
        "person": {
            "description": "A person of interest in the investigation",
            "required": ["title"],
            "optional": ["role", "affiliations", "importance", "research_status"],
            "subdirectory": "entities/",
        },
        "organization": {
            "description": "A company, government body, NGO, or other organization",
            "required": ["title"],
            "optional": ["org_type", "jurisdiction", "founded", "importance", "research_status"],
            "subdirectory": "entities/",
        },
        # Entity types from journalism-investigation plugin
        "asset": {
            "description": "A tracked asset — real estate, vehicle, vessel, aircraft, etc.",
            "required": ["title"],
            "optional": [
                "asset_type",
                "value",
                "currency",
                "jurisdiction",
                "registered_owner",
                "acquisition_date",
                "description",
                "importance",
            ],
            "subdirectory": "entities/",
        },
        "account": {
            "description": "A financial account — bank, brokerage, crypto wallet, shell company, trust",
            "required": ["title"],
            "optional": [
                "account_type",
                "institution",
                "jurisdiction",
                "holder",
                "opened_date",
                "closed_date",
                "importance",
            ],
            "subdirectory": "entities/",
        },
        "document_source": {
            "description": "A source document with reliability and classification metadata",
            "required": ["title"],
            "optional": [
                "reliability",
                "classification",
                "obtained_date",
                "obtained_method",
                "date",
                "author",
                "url",
                "importance",
            ],
            "subdirectory": "sources/",
        },
        # Event types from journalism-investigation plugin
        "investigation_event": {
            "description": "A dated event in the investigation with actors and verification status",
            "required": ["title"],
            "optional": [
                "date",
                "actors",
                "source_refs",
                "verification_status",
                "location",
                "importance",
            ],
            "subdirectory": "events/",
        },
        "transaction": {
            "description": "A financial transaction — payment, bribe, grant, etc.",
            "required": ["title"],
            "optional": [
                "date",
                "amount",
                "currency",
                "sender",
                "receiver",
                "method",
                "purpose",
                "transaction_type",
                "importance",
            ],
            "subdirectory": "events/",
        },
        "legal_action": {
            "description": "A legal or regulatory action — case, indictment, sanctions, etc.",
            "required": ["title"],
            "optional": [
                "date",
                "case_type",
                "jurisdiction",
                "parties",
                "case_status",
                "outcome",
                "case_number",
                "importance",
            ],
            "subdirectory": "events/",
        },
        # Connection types (edge-entities)
        "ownership": {
            "description": "Ownership relationship between an entity and an asset/organization",
            "edge_type": True,
            "endpoints": {
                "source": {"field": "owner", "accepts": ["person", "organization"]},
                "target": {"field": "asset", "accepts": ["organization", "asset", "account"]},
            },
            "required": ["title"],
            "optional": [
                "owner",
                "asset",
                "percentage",
                "start_date",
                "end_date",
                "legal_basis",
                "beneficial",
                "importance",
            ],
            "subdirectory": "connections/",
        },
        "membership": {
            "description": "Membership relationship between a person and an organization",
            "edge_type": True,
            "endpoints": {
                "source": {"field": "person", "accepts": ["person"]},
                "target": {"field": "organization", "accepts": ["organization"]},
            },
            "required": ["title"],
            "optional": ["person", "organization", "role", "start_date", "end_date", "importance"],
            "subdirectory": "connections/",
        },
        "funding": {
            "description": "Funding relationship between entities",
            "edge_type": True,
            "endpoints": {
                "source": {"field": "funder", "accepts": ["person", "organization"]},
                "target": {"field": "recipient", "accepts": ["person", "organization"]},
            },
            "required": ["title"],
            "optional": [
                "funder",
                "recipient",
                "amount",
                "currency",
                "date_range",
                "purpose",
                "mechanism",
                "importance",
            ],
            "subdirectory": "connections/",
        },
        # Evidence type
        "evidence": {
            "description": "A piece of evidence linked to a source document",
            "required": ["title"],
            "optional": [
                "evidence_type",
                "source_document",
                "reliability",
                "obtained_date",
                "chain_of_custody",
                "importance",
            ],
            "subdirectory": "evidence/",
        },
        # Claim type
        "claim": {
            "description": "A factual assertion with verification lifecycle tracking",
            "required": ["title"],
            "optional": [
                "assertion",
                "confidence",
                "claim_status",
                "evidence_refs",
                "disputed_by",
                "importance",
            ],
            "subdirectory": "claims/",
        },
        # General-purpose types
        "note": {
            "description": "General research note or analysis",
            "required": ["title"],
            "optional": ["importance"],
            "subdirectory": "notes/",
        },
    },
    "policies": {
        "source_tracking": True,
    },
    "validation": {
        "enforce": True,
        "rules": [],
    },
    "directories": ["entities", "events", "sources", "connections", "evidence", "claims", "notes"],
}

KNOWN_ENTITIES_PRESET = {
    "name": "known-entities",
    "description": "Reference KB of known entities (people, organizations, agencies) shared across investigations",
    "kb_type": "known-entities",
    "types": {
        "person": {
            "description": "A known person — politician, oligarch, executive, official, etc.",
            "required": ["title"],
            "optional": ["role", "affiliations", "aliases", "nationality", "importance"],
            "subdirectory": "people/",
        },
        "organization": {
            "description": "A known organization — company, government body, NGO, agency, etc.",
            "required": ["title"],
            "optional": ["org_type", "jurisdiction", "aliases", "founded", "importance"],
            "subdirectory": "organizations/",
        },
    },
    # A mapping of name -> text, as KBSchema.guidelines reads it: a bare
    # string made every schema export of a KB made from this preset raise
    # (MCP kb_schema INTERNAL; orient showed an empty schema).
    "guidelines": {
        "canonical_records": (
            "Maintain canonical entity records with stable IDs. "
            "Use aliases for alternate names, abbreviations, and transliterations. "
            "Keep entries neutral and factual — link to investigation KBs for editorial "
            "context. Update existing records rather than creating duplicates."
        ),
    },
    "directories": ["people", "organizations"],
}
