"""A version list offers only versions a read can serve (#453), driven through
`pyrite index build --with-attribution` (CLI) and the version endpoints (REST).

`entry_version` is derived from git; an attribution pass replaces an entry's
rows with the commits the history walk accepts. A row the walk rejects
(another entry's commit at a reused path, #432) must not stay listed while the
read refuses it with a 404. The rows gate which commits a read may serve
(#415), so removing one only narrows. The storage-level regimes (stale-only
entry, since_commit, empty walk, not-git) are in test_entry_version_integrity.py.
"""

import pytest
import yaml
from typer.testing import CliRunner

from pyrite.server.api import get_config, get_db
from tests.test_entry_version_integrity import _note, _Repo, _seed_row


@pytest.fixture
def repo(tmp_path):
    r = _Repo(tmp_path)
    yield r
    r.close()


@pytest.fixture
def reused(repo):
    repo.write("kb/notes.md", _note("s1", "Notes", "Private draft: text only s1 has."))
    c_s1 = repo.commit("s1")
    (repo.kb / "notes.md").unlink()
    repo.commit("delete s1")
    repo.write("kb/notes.md", _note("p1", "Notes", "Public text."))
    c_p1 = repo.commit("p1 at the same path")
    repo.index()
    _seed_row(repo, "p1", c_s1)  # a row an earlier build recorded
    return repo, c_s1, c_p1


def test_cli_build_then_rest_list_and_read_agree(reused, make_client, tmp_path, monkeypatch):
    import pyrite.config as pyrite_config
    from pyrite.cli import app

    repo, c_s1, c_p1 = reused
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(repo.config.to_dict()))
    monkeypatch.setattr(pyrite_config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(pyrite_config, "CONFIG_FILE", cfg_dir / "config.yaml")

    client, _, _ = make_client(
        dependency_overrides={get_config: lambda: repo.config, get_db: lambda: repo.db}
    )

    def listed():
        r = client.get("/api/entries/p1/versions", params={"kb": "k"})
        return [v["commit_hash"] for v in r.json()["versions"]]

    # Before the repair: the list offers c_s1, the read refuses it.
    assert c_s1 in listed()
    assert client.get(f"/api/entries/p1/versions/{c_s1}", params={"kb": "k"}).status_code == 404

    result = CliRunner().invoke(app, ["index", "build", "--with-attribution"])
    assert result.exit_code == 0, result.output

    assert listed() == [c_p1]
    for h in listed():
        r = client.get(f"/api/entries/p1/versions/{h}", params={"kb": "k"})
        assert r.status_code == 200, (h, r.text)
    # Narrowing, not widening: the foreign commit stays refused.
    assert client.get(f"/api/entries/p1/versions/{c_s1}", params={"kb": "k"}).status_code == 404


@pytest.mark.control(
    reason="Dev keeps every row, so this passes there; it fails against a "
    "delete-everything-outside-the-walk implementation (round 0 of #453)."
)
def test_a_rename_git_does_not_detect_keeps_its_older_servable_versions(
    repo, make_client, tmp_path, monkeypatch
):
    """Attribution never deletes a row a read still serves. A manual move plus
    a rewrite (git sees a delete and an add) ends the walk at the new path's
    add, but the older commit is still this entry's, at its recorded path."""
    import pyrite.config as pyrite_config
    from pyrite.cli import app

    repo.write("kb/q.md", _note("n1", "N", "alpha beta gamma " * 40))
    c1 = repo.commit("c1")
    repo.index()
    repo.attribute()
    assert [h for h, _ in repo.versions("n1")] == [c1]
    (repo.kb / "q.md").unlink()
    repo.write("kb/p.md", _note("n1", "N", "totally different words here " * 40))
    c2 = repo.commit("move and rewrite")
    repo.index()

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(repo.config.to_dict()))
    monkeypatch.setattr(pyrite_config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(pyrite_config, "CONFIG_FILE", cfg_dir / "config.yaml")
    client, _, _ = make_client(
        dependency_overrides={get_config: lambda: repo.config, get_db: lambda: repo.db}
    )

    result = CliRunner().invoke(app, ["index", "build", "--with-attribution"])
    assert result.exit_code == 0, result.output

    r = client.get("/api/entries/n1/versions", params={"kb": "k"})
    listed = [v["commit_hash"] for v in r.json()["versions"]]
    assert c1 in listed and c2 in listed
    for h in listed:
        assert client.get(f"/api/entries/n1/versions/{h}", params={"kb": "k"}).status_code == 200


def test_a_crlf_version_reads_over_rest_as_the_lf_text_dev_returned(
    repo, make_client, tmp_path, monkeypatch
):
    """A version read returns the text dev returned for every file dev could
    read. Dev read with `text=True` (universal newlines), so a committed CRLF
    file came back with `\\n`; the one read function keeps that, and the
    servability verdict on the normalised text is unchanged."""
    import pyrite.config as pyrite_config
    from pyrite.cli import app
    from pyrite.services.git_service import SERVABLE, GitService

    crlf = b"---\r\nid: n1\r\ntitle: N\r\ntype: note\r\n---\r\n\r\nold body\rlone cr\r\n"
    (repo.kb / "n.md").write_bytes(crlf)
    c1 = repo.commit("crlf version")
    repo.write("kb/n.md", _note("n1", "N", "new body"))
    repo.commit("lf version")
    repo.index()

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(repo.config.to_dict()))
    monkeypatch.setattr(pyrite_config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(pyrite_config, "CONFIG_FILE", cfg_dir / "config.yaml")
    client, _, _ = make_client(
        dependency_overrides={get_config: lambda: repo.config, get_db: lambda: repo.db}
    )
    result = CliRunner().invoke(app, ["index", "build", "--with-attribution"])
    assert result.exit_code == 0, result.output

    r = client.get(f"/api/entries/n1/versions/{c1}", params={"kb": "k"})
    assert r.status_code == 200, r.text
    lf = "---\nid: n1\ntitle: N\ntype: note\n---\n\nold body\nlone cr\n"
    assert r.json()["content"] == lf
    assert GitService.version_servability(repo.kb, c1, "n.md", "n.md", "n1") == (SERVABLE, lf)
