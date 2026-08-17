"""适配层唯一权威门禁入口 check-adapters.sh：cwd 无关（回归 M3）。

从仓库根、docs/、runtime 子目录调用必须得到完全一致的输出与文件集合；仓库外调用
稳定非零。入口在仓库外、runtime 缺失、无 Python 文件或工具失败时都必须非零退出，
绝不出现"未检查任何文件却 All checks passed"。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.support.paths import RUNTIME_ROOT, SCRIPTS_ROOT

GATE = SCRIPTS_ROOT / "check-adapters.sh"


def _run_gate(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(GATE)],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=600,
    )


def test_gate_entry_is_cwd_independent() -> None:
    """仓库根、docs/、runtime 子目录调用：退出码与输出完全一致。"""
    repo_root = SCRIPTS_ROOT.parents[2]
    docs_dir = repo_root / "docs"
    runtime_dir = RUNTIME_ROOT

    baseline = _run_gate(repo_root)
    assert baseline.returncode == 0, baseline.stderr
    assert "适配层质量门禁全部通过" in baseline.stdout

    for cwd in (docs_dir, runtime_dir):
        result = _run_gate(cwd)
        assert result.returncode == 0, f"{cwd}: {result.stderr}"
        assert result.stdout == baseline.stdout


def test_gate_entry_fails_outside_repo(tmp_path: Path) -> None:
    """仓库外调用：稳定非零，不误报通过。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    result = _run_gate(outside)
    assert result.returncode != 0
