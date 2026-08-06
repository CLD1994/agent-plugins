from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.support.paths import SCRIPTS_ROOT

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """返回 (data_dir, root)：均为 tmp_path 下真实、安全的目录。"""
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    return data_dir, root
