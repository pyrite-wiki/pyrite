"""#684: rename follows the index's complete wikilink grammar."""

import pytest

from pyrite.config import KBConfig
from pyrite.models import NoteEntry
from pyrite.storage.repository import KBRepository


@pytest.mark.parametrize("dry_run", [False, True])
def test_rename_preserves_link_decorations(tmp_path, dry_run):
    repo = KBRepository(KBConfig(name="scratch", path=tmp_path))
    repo.save(NoteEntry(id="alpha", title="Alpha", body="Source"))
    before = (
        "[[alpha]] [[scratch:alpha]] [[alpha#H]] [[alpha^blk]] ![[alpha]] "
        "[[scratch:alpha#H^blk|display]] ![[scratch:alpha^blk|embed]] "
        "[[other-kb:alpha]] [[alphabet]] [[ alpha | spaced ]]"
    )
    after = (
        "[[gamma]] [[scratch:gamma]] [[gamma#H]] [[gamma^blk]] ![[gamma]] "
        "[[scratch:gamma#H^blk|display]] ![[scratch:gamma^blk|embed]] "
        "[[other-kb:alpha]] [[alphabet]] [[ gamma | spaced ]]"
    )
    repo.save(NoteEntry(id="beta", title="Beta", body=before))
    result = repo.rename("alpha", "gamma", dry_run=dry_run)
    assert result["links_rewritten"] == 8
    assert result["files_rewritten"] == 1
    assert repo.load("beta").body == (before if dry_run else after)


def test_rename_rewrites_source_self_links_once(tmp_path):
    repo = KBRepository(KBConfig(name="scratch", path=tmp_path))
    repo.save(NoteEntry(id="alpha", title="Alpha", body="[[scratch:alpha#H|self]]"))
    result = repo.rename("alpha", "gamma")
    assert repo.load("gamma").body == "[[scratch:gamma#H|self]]"
    assert result["links_rewritten"] == 1
    assert result["files_rewritten"] == 1


@pytest.mark.control(reason="explicitly disabling rewrites keeps all references unchanged")
def test_rename_without_rewriting_keeps_decorated_links(tmp_path):
    repo = KBRepository(KBConfig(name="scratch", path=tmp_path))
    repo.save(NoteEntry(id="alpha", title="Alpha", body="[[scratch:alpha#H]]"))
    result = repo.rename("alpha", "gamma", update_links=False)
    assert repo.load("gamma").body == "[[scratch:alpha#H]]"
    assert result["links_rewritten"] == 0
