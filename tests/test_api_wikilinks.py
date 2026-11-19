"""
Tests for wikilink-related API endpoints: /api/entries/titles and /api/entries/resolve.
"""

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed")


def _seed_entries(db):
    """Insert test entries into the database."""
    # Must insert KB first due to FK constraint: entry.kb_name -> kb.name
    db._raw_conn.execute(
        "INSERT OR IGNORE INTO kb (name, kb_type, path) VALUES (?, ?, ?)",
        ("test-kb", "generic", "/tmp/test-kb"),
    )
    entries = [
        ("john-doe", "test-kb", "person", "John Doe", "A person entry"),
        ("acme-corp", "test-kb", "organization", "Acme Corporation", "An org entry"),
        ("meeting-2024", "test-kb", "event", "Board Meeting 2024", "A meeting"),
        ("project-alpha", "test-kb", "note", "Project Alpha", "A project note"),
        ("project-beta", "test-kb", "note", "Project Beta", "Another project"),
    ]
    for entry_id, kb, etype, title, body in entries:
        db._raw_conn.execute(
            """INSERT OR REPLACE INTO entry (id, kb_name, entry_type, title, body, date, importance)
               VALUES (?, ?, ?, ?, ?, '2024-01-01', 5)""",
            (entry_id, kb, etype, title, body),
        )
    db._raw_conn.commit()


@pytest.fixture
def client(make_client):
    client, _config, db = make_client(api_key="")
    _seed_entries(db)
    return client


@pytest.fixture
def empty_client(make_client):
    client, _config, _db = make_client(api_key="")
    return client


# =============================================================================
# GET /api/entries/titles
# =============================================================================


class TestEntryTitles:
    """Tests for the lightweight entry titles endpoint."""

    def test_returns_all_titles(self, client):
        """Should return id and title for all entries."""
        response = client.get("/api/entries/titles")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data
        assert len(data["entries"]) == 5
        # Each entry should have id and title
        for entry in data["entries"]:
            assert "id" in entry
            assert "title" in entry

    def test_entries_have_correct_fields(self, client):
        """Each entry should have id, title, kb_name, and entry_type."""
        response = client.get("/api/entries/titles")
        data = response.json()
        entry = data["entries"][0]
        assert "id" in entry
        assert "title" in entry
        assert "kb_name" in entry
        assert "entry_type" in entry

    def test_filter_by_kb(self, client):
        """Should filter by KB name."""
        response = client.get("/api/entries/titles?kb=test-kb")
        assert response.status_code == 200
        data = response.json()
        assert len(data["entries"]) == 5

    def test_a_kb_that_does_not_exist_is_refused(self, client):
        """A KB filter naming no KB is `KB_NOT_FOUND`, not an empty list (#952)."""
        response = client.get("/api/entries/titles?kb=nonexistent")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "KB_NOT_FOUND"

    def test_search_filter(self, client):
        """Should filter by search query matching title."""
        response = client.get("/api/entries/titles?q=Project")
        assert response.status_code == 200
        data = response.json()
        assert len(data["entries"]) == 2
        titles = {e["title"] for e in data["entries"]}
        assert "Project Alpha" in titles
        assert "Project Beta" in titles

    def test_search_case_insensitive(self, client):
        """Search should be case insensitive."""
        response = client.get("/api/entries/titles?q=project")
        data = response.json()
        assert len(data["entries"]) == 2

    def test_empty_database(self, empty_client):
        """Should return empty list for empty database."""
        response = empty_client.get("/api/entries/titles")
        assert response.status_code == 200
        data = response.json()
        assert data["entries"] == []

    def test_limit(self, client):
        """Should respect limit parameter."""
        response = client.get("/api/entries/titles?limit=2")
        data = response.json()
        assert len(data["entries"]) == 2


# =============================================================================
# GET /api/entries/resolve
# =============================================================================


class TestEntryResolve:
    """Tests for wikilink target resolution."""

    def test_resolve_by_exact_id(self, client):
        """Should resolve entry by exact ID match."""
        response = client.get("/api/entries/resolve?target=john-doe")
        assert response.status_code == 200
        data = response.json()
        assert data["resolved"] is True
        assert data["entry"]["id"] == "john-doe"
        assert data["entry"]["title"] == "John Doe"

    def test_resolve_by_title(self, client):
        """Should resolve entry by exact title match."""
        response = client.get("/api/entries/resolve?target=John Doe")
        assert response.status_code == 200
        data = response.json()
        assert data["resolved"] is True
        assert data["entry"]["id"] == "john-doe"

    def test_resolve_case_insensitive_title(self, client):
        """Title resolution should be case insensitive."""
        response = client.get("/api/entries/resolve?target=john doe")
        assert response.status_code == 200
        data = response.json()
        assert data["resolved"] is True
        assert data["entry"]["id"] == "john-doe"

    def test_resolve_not_found(self, client):
        """Should return resolved=false for unknown targets."""
        response = client.get("/api/entries/resolve?target=nonexistent-entry")
        assert response.status_code == 200
        data = response.json()
        assert data["resolved"] is False
        assert data["entry"] is None

    def test_resolve_missing_param(self, client):
        """Should return 422 when target param is missing."""
        response = client.get("/api/entries/resolve")
        assert response.status_code == 422

    def test_resolve_with_kb_filter(self, client):
        """Should resolve within a specific KB."""
        response = client.get("/api/entries/resolve?target=john-doe&kb=test-kb")
        assert response.status_code == 200
        data = response.json()
        assert data["resolved"] is True

    def test_resolve_in_a_kb_that_does_not_exist_is_refused(self, client):
        """A KB filter naming no KB is `KB_NOT_FOUND`, not `resolved: false` (#952)."""
        response = client.get("/api/entries/resolve?target=john-doe&kb=other-kb")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "KB_NOT_FOUND"
