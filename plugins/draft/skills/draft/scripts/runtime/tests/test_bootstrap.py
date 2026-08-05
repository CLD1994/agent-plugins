from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]

FORBIDDEN = ("bootstrap", "data/bin", "installed-runtime-manifest", "setup-status.json")


def _write_fake_uv(bin_dir: Path) -> None:
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
echo "$*" >> "$UV_LOG"
if [ "$1" = "--version" ]; then
    echo "uv 0.8.11"
    exit 0
fi
if [ "$1" != "run" ]; then
    echo "unexpected fake uv call: $*" >&2
    exit 99
fi
shift
if [ "$1" = "--project" ]; then
    project="$2"
    shift 2
    echo "ENV UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT" \
        "PYTHONPATH=${PYTHONPATH-unset} DRAFT_DATA=${DRAFT_DATA-unset}" \
        "VIRTUAL_ENV=${VIRTUAL_ENV-unset}" >> "$UV_LOG"
    mkdir -p "$UV_PROJECT_ENVIRONMENT"
    while [ "$1" != "draft" ]; do shift; done
    shift
    echo "draft $*" >> "$PY_LOG"
    PYTHONPATH="$project/src"
    export PYTHONPATH
    exec "$TEST_PYTHON" -m draft "$@"
fi
script="$1"
shift
exec "$TEST_PYTHON" "$script" "$@"
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def copy_skill(tmp_path: Path) -> Path:
    case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=tmp_path))
    artifact = case_dir / "draft-skill"
    shutil.copytree(SKILL_ROOT, artifact, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    return artifact


def make_case(tmp_path: Path) -> tuple[Path, Path, Path, Path, dict[str, str]]:
    artifact = copy_skill(tmp_path)
    case_dir = artifact.parent
    fake_bin = case_dir / "bin"
    fake_bin.mkdir()
    _write_fake_uv(fake_bin)
    xdg = case_dir / "xdg"
    xdg.mkdir()
    xdg.chmod(0o700)
    uv_log = case_dir / "uv.log"
    py_log = case_dir / "py.log"
    env = os.environ.copy()
    for name in (
        "PLUGIN_ROOT",
        "PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_DATA",
        "DRAFT_DATA",
        "DRAFT_SKILL_PATH",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ):
        env.pop(name, None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["XDG_RUNTIME_DIR"] = str(xdg)
    env["UV_LOG"] = str(uv_log)
    env["PY_LOG"] = str(py_log)
    env["TEST_PYTHON"] = sys.executable
    return artifact, xdg, uv_log, py_log, env


def run_launcher(
    artifact: Path, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", str(artifact / "scripts/draft.py"), *args],
        cwd=artifact.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_no_forbidden_references(*logs: Path) -> None:
    for log in logs:
        if not log.exists():
            continue
        text = log.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{token} referenced in {log}"


def test_setup_syncs_version_environment_without_domain_cli(tmp_path: Path) -> None:
    artifact, xdg, uv_log, py_log, env = make_case(tmp_path)
    result = run_launcher(artifact, env, "setup")
    assert result.returncode == 0, result.stderr

    lines = uv_log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"run {artifact}/scripts/draft.py setup"
    inner = lines[1]
    assert inner.startswith(f"run --project {artifact}/scripts/runtime ")
    for flag in ("--locked", "--exact", "--no-dev", "--no-editable", "--no-env-file"):
        assert flag in inner
    assert inner.endswith(" draft --version")
    env_line = lines[2]
    assert f"UV_PROJECT_ENVIRONMENT={xdg}/draft/envs/0.2.0" in env_line
    assert "PYTHONPATH=unset" in env_line
    assert "DRAFT_DATA=unset" in env_line
    assert "VIRTUAL_ENV=unset" in env_line
    assert py_log.read_text(encoding="utf-8").splitlines() == ["draft --version"]
    _assert_no_forbidden_references(uv_log, py_log)


def test_domain_command_exit_code_propagates(tmp_path: Path) -> None:
    artifact, _, uv_log, py_log, env = make_case(tmp_path)
    result = run_launcher(artifact, env, "check", "missing-root")
    assert result.returncode == 2, result.stderr
    lines = uv_log.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"run {artifact}/scripts/draft.py check missing-root"
    assert py_log.read_text(encoding="utf-8").splitlines() == ["draft check missing-root"]
    _assert_no_forbidden_references(uv_log, py_log)


def test_copied_skill_runs_standalone_with_isolated_runtime_dir(tmp_path: Path) -> None:
    artifact, xdg, uv_log, py_log, env = make_case(tmp_path)
    case_dir = artifact.parent

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        result = run_launcher(artifact, env, *args)
        assert result.returncode == 0, f"{args}: {result.stderr}{result.stdout}"
        return result

    root = case_dir / "draft-root"
    run("setup")
    run("init", str(root))
    index = root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n", encoding="utf-8")
    (root / "pages" / "a.md").write_text(
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n",
        encoding="utf-8",
    )
    run("check", str(root))
    run("move", str(root), "pages/a.md", "pages/b.md")
    run("check", str(root))

    assert not (root / "pages" / "a.md").exists()
    assert (root / "pages" / "b.md").is_file()

    envs = xdg / "draft" / "envs" / "0.2.0"
    assert envs.is_dir()
    assert not list((case_dir).rglob(".env"))
    assert not list(artifact.rglob(".venv"))
    internal = [path for path in case_dir.rglob(".draft")]
    assert internal and all(root in path.parents for path in internal)
    managed = [
        path
        for path in case_dir.rglob("*.md")
        if artifact not in path.parents and ".draft" not in path.parts
    ]
    assert managed and all(root in path.parents for path in managed)

    for line in uv_log.read_text(encoding="utf-8").splitlines():
        if line.startswith("ENV "):
            assert "PYTHONPATH=unset" in line
            assert "DRAFT_DATA=unset" in line
    _assert_no_forbidden_references(uv_log, py_log)


def test_launcher_does_not_reference_legacy_layout() -> None:
    text = (SKILL_ROOT / "scripts" / "draft.py").read_text(encoding="utf-8")
    for token in FORBIDDEN:
        assert token not in text, f"{token} referenced in scripts/draft.py"
