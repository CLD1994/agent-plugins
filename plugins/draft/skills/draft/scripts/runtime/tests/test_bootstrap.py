from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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
case "$1" in
    run)
        shift
        while [ "$1" != "python" ]; do shift; done
        shift
        exec python "$@"
        ;;
    sync)
        mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
        cat > "$UV_PROJECT_ENVIRONMENT/bin/python" <<'EOF'
#!/bin/sh
echo "$*" >> "$PY_LOG"
exec python "$@"
EOF
        chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"
        ;;
    --version)
        echo "uv 0.8.11"
        ;;
    *)
        echo "unexpected fake uv call: $*" >&2
        exit 99
        ;;
esac
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def copy_skill(tmp_path: Path) -> Path:
    case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=tmp_path))
    artifact = case_dir / "draft-skill"
    shutil.copytree(SKILL_ROOT, artifact, ignore=shutil.ignore_patterns("__pycache__"))
    return artifact


def run_wrapper(
    tmp_path: Path,
    *args: str,
    path: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
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
    env.pop("PLUGIN_DATA", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}" if path is None else path
    env["XDG_RUNTIME_DIR"] = str(xdg)
    env["UV_LOG"] = str(uv_log)
    env["PY_LOG"] = str(py_log)
    result = subprocess.run(
        ["/bin/sh", str(artifact / "scripts/draft"), *args],
        cwd=case_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, artifact, uv_log, py_log


def _resource_args(run_line: str) -> dict[str, str]:
    tokens = shlex.split(run_line)
    return {
        option: tokens[tokens.index(option) + 1]
        for option in ("--entry-path", "--skill-path", "--runtime-path")
    }


def _assert_resources_inside_skill(run_line: str, artifact: Path) -> None:
    resources = _resource_args(run_line)
    assert Path(resources["--entry-path"]) == artifact / "scripts" / "draft"
    assert Path(resources["--runtime-path"]) == artifact / "scripts" / "runtime"
    skill = Path(os.path.normpath(resources["--skill-path"]))
    assert skill == artifact / "SKILL.md"


def _assert_no_forbidden_references(*logs: Path) -> None:
    for log in logs:
        if not log.exists():
            continue
        text = log.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            assert token not in text, f"{token} referenced in {log}"


def test_setup_syncs_and_self_checks_without_domain_cli(tmp_path: Path) -> None:
    result, artifact, uv_log, py_log = run_wrapper(tmp_path, "setup")
    assert result.returncode == 0, result.stderr
    uv_calls = uv_log.read_text(encoding="utf-8").splitlines()
    assert len(uv_calls) == 2
    run_line, sync_line = uv_calls
    assert run_line.startswith("run --no-project --python 3.14 python -m draft.entry ")
    _assert_resources_inside_skill(run_line, artifact)
    assert sync_line == (
        f"sync --project {artifact}/scripts/runtime --locked --no-install-project --no-dev"
    )
    assert py_log.read_text(encoding="utf-8").splitlines() == ["-m draft --version"]
    _assert_no_forbidden_references(uv_log, py_log)


def test_domain_command_exit_code_propagates(tmp_path: Path) -> None:
    result, artifact, uv_log, py_log = run_wrapper(tmp_path, "check", "missing-root")
    assert result.returncode == 2, result.stderr
    run_line = uv_log.read_text(encoding="utf-8").splitlines()[0]
    _assert_resources_inside_skill(run_line, artifact)
    assert py_log.read_text(encoding="utf-8").splitlines() == [
        "-m draft --version",
        "-m draft check missing-root",
    ]
    _assert_no_forbidden_references(uv_log, py_log)


def test_missing_uv_reports_runtime_uv_unavailable(tmp_path: Path) -> None:
    restricted = tmp_path / "restricted-bin"
    restricted.mkdir()
    (restricted / "dirname").symlink_to("/usr/bin/dirname")
    result, _, _, _ = run_wrapper(tmp_path, "setup", path=str(restricted))
    assert result.returncode == 4
    assert "runtime.uv_unavailable" in result.stderr


def test_wrappers_do_not_reference_host_data_or_legacy_layout() -> None:
    for name in ("draft", "draft.ps1"):
        text = (SKILL_ROOT / "scripts" / name).read_text(encoding="utf-8")
        for token in ("PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", *FORBIDDEN):
            assert token not in text, f"{token} referenced in scripts/{name}"
