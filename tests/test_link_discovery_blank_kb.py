"""A blank KB name on a link-discovery read is an unplaced id, not a typo.

#846 refuses a KB name that matches no KB (KB_NOT_FOUND); #528 refuses an id
held by several KBs when the caller gave none (AMBIGUOUS). A blank ``-k ""``
names no KB (``access_policy.named_kb``), so it belongs to the second rule: the
first must not catch it. ``test_ambiguous_id_is_refused`` drives the whole CLI
path; this pins the one line that decides it.
"""

import tempfile
from pathlib import Path

import pytest

from pyrite.config import KBConfig, KBType, PyriteConfig, Settings
from pyrite.exceptions import KBNotFoundError
from pyrite.services.link_discovery_service import LinkDiscoveryService


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "kb").mkdir()
        config = PyriteConfig(
            knowledge_bases=[KBConfig(name="real", path=root / "kb", kb_type=KBType.GENERIC)],
            settings=Settings(index_path=root / "index.db"),
        )
        yield LinkDiscoveryService(config, db=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("blank", ["", "  ", None])
def test_a_blank_kb_is_not_refused_as_unknown(service, blank):
    service._require_kbs(blank)


def test_a_typo_is_still_refused_as_unknown(service):
    with pytest.raises(KBNotFoundError):
        service._require_kbs("raal")
    service._require_kbs("real")
