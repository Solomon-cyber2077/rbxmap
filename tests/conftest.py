import hashlib
import shutil
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixture"


def tree_hashes(root: Path) -> dict:
    """Hash of every file except rbxmap's own outputs."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith(".rbxmap/"):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@pytest.fixture
def proj(tmp_path) -> Path:
    dst = tmp_path / "game"
    shutil.copytree(FIXTURE, dst)
    return dst


@pytest.fixture
def app(proj):
    from rbxmap.api import RbxMap
    a = RbxMap(proj)
    yield a
    a.idx.db.close()
