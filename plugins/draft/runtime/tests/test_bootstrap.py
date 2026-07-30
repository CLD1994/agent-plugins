from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _write_fake_uv(bin_dir: Path) -> None:
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
case "$*" in
    "--version") echo "uv 1.0" ;;
    sync\\ *) exit 0 ;;
    *" draft --version") echo "0.1.0" ;;
    *" python --version") echo "Python 3.14.0" ;;
    *) echo "unexpected fake uv call: $*" >&2; exit 99 ;;
esac
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def run_setup(
    tmp_path: Path,
    platform: str,
    *,
    cwd: Path | None = None,
    **values: str,
) -> subprocess.CompletedProcess[str]:
    case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=tmp_path))
    artifact = case_dir / "draft"
    shutil.copytree(PLUGIN_ROOT, artifact)
    fake_bin = case_dir / "bin"
    fake_bin.mkdir()
    _write_fake_uv(fake_bin)
    work = cwd or case_dir / "work"
    work.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.pop("PLUGIN_DATA", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.update(values)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["sh", str(artifact / "bootstrap/setup.sh"), platform],
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_codex_setup_uses_only_plugin_data(tmp_path: Path) -> None:
    codex_data = tmp_path / "codex"
    claude_data = tmp_path / "claude"
    codex_data.mkdir()
    claude_data.mkdir()
    result = run_setup(
        tmp_path,
        "codex",
        PLUGIN_DATA=str(codex_data),
        CLAUDE_PLUGIN_DATA=str(claude_data),
    )
    assert result.returncode == 0, result.stderr
    launcher = (codex_data / "bin/draft").read_text(encoding="utf-8")
    assert f"DRAFT_DATA='{codex_data}'" in launcher
    assert "skills/draft/SKILL.md" in launcher
    assert not (claude_data / "bin").exists()


def test_claude_setup_uses_only_claude_plugin_data(tmp_path: Path) -> None:
    data = tmp_path / "claude"
    data.mkdir()
    result = run_setup(tmp_path, "claude", CLAUDE_PLUGIN_DATA=str(data))
    assert result.returncode == 0, result.stderr
    assert (data / "bin/draft").is_file()
    assert (data / "bin/draft-hook-claude").is_file()


def test_setup_rejects_unknown_platform_and_missing_data(tmp_path: Path) -> None:
    assert run_setup(tmp_path, "other").returncode == 2
    work = tmp_path / "fallback-check"
    work.mkdir()
    assert run_setup(tmp_path, "codex", cwd=work).returncode != 0
    assert list(work.iterdir()) == []
