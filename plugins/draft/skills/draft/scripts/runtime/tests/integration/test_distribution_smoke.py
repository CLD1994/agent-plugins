"""Path-based uvx install smoke tests (the network half of the distribution split).

The only core invocation prefix is
``uvx --isolated --from "<skill_root>/scripts/runtime" draft``; uv owns
environment selection and cache reuse, while uv startup failures retain uv's
stderr and exit status.

Each test copies only ``skills/draft`` beneath an unrelated parent directory
(no ``.git``, no repository parent) and computes the runtime path from that
copy. The three successful-install cases need the external index, dependency
resolution, download, or a cold uv cache and are marked ``network``; the
missing-interpreter case fails before any index access and stays unmarked.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support.paths import RUNTIME_ROOT

SKILL_ROOT = RUNTIME_ROOT.parents[1]

# Legacy launcher/bootstrap tokens that must never reappear in the core
# invocation path or its outputs.
FORBIDDEN_TOKENS = (
    "bootstrap",
    "data/bin",
    "installed-runtime-manifest",
    "setup-status.json",
)

# Host environment variables that would leak host state into the isolated uvx
# subprocess. Stripped from every clean baseline environment.
_STRIP_ENV = (
    "PLUGIN_ROOT",
    "PLUGIN_DATA",
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PLUGIN_DATA",
    "DRAFT_DATA",
    "DRAFT_SKILL_PATH",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
    "UV_CACHE_DIR",
    "UV_TOOL_DIR",
    "UV_PYTHON",
    "UV_PYTHON_DOWNLOADS",
    "UV_OFFLINE",
    "UV_RESOLUTION",
)

# Generous timeout for every uvx subprocess: resolution plus build plus install
# plus command execution. Correctness over speed.
UVX_TIMEOUT = 600


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in _STRIP_ENV:
        env.pop(name, None)
    return env


def copy_skill(tmp_path: Path, *, drop_lock: bool = False) -> Path:
    """Copy only ``skills/draft`` beneath an unrelated parent directory.

    The parent has no ``.git`` and no repository parent, proving the runtime is
    self-contained and does not walk up to find a repository.
    """
    parent = tmp_path / "host"
    parent.mkdir()
    artifact = parent / "draft-skill"
    shutil.copytree(
        SKILL_ROOT,
        artifact,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".venv",
            ".pytest_cache",
            ".ruff_cache",
        ),
    )
    if drop_lock:
        (artifact / "scripts" / "runtime" / "uv.lock").unlink()
    return artifact


def run_uvx(
    artifact: Path,
    env: dict[str, str],
    cache_dir: Path,
    *draft_args: str,
    extra_uvx_args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """Run ``uvx --isolated --from <copy>/scripts/runtime draft ...``.

    The cache dir is passed explicitly via ``--cache-dir`` so the exact cache
    path is under test control (snapshotting, fresh-cache isolation).
    """
    runtime = artifact / "scripts" / "runtime"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            "uvx",
            "--isolated",
            "--cache-dir",
            str(cache_dir),
            *extra_uvx_args,
            "--from",
            str(runtime),
            "draft",
            *draft_args,
        ],
        cwd=artifact.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=UVX_TIMEOUT,
    )


def _assert_no_forbidden_outputs(result: subprocess.CompletedProcess[str]) -> None:
    for token in FORBIDDEN_TOKENS:
        assert token not in result.stdout, f"{token!r} leaked into stdout"
        assert token not in result.stderr, f"{token!r} leaked into stderr"


def _assert_no_draft_managed_envs(artifact: Path, *roots: Path) -> None:
    """No ``.venv`` or Draft-managed ``envs/`` appears in the skill or business root.

    uvx's tool environment lives beneath the cache dir's ``environments-v2/``,
    never inside the copied skill or a Draft business root.
    """
    assert not list(artifact.rglob(".venv"))
    for root in roots:
        assert not list(root.rglob(".venv"))
    # The old launcher created ``envs/<version>`` beneath a Draft data dir; the
    # path-based uvx runtime must not create any ``envs`` directory inside the
    # copied skill or business root.
    for base in (artifact, *roots):
        assert not list(base.rglob("envs"))


@pytest.mark.network
def test_version_reports_runtime_version_from_copy(tmp_path: Path) -> None:
    artifact = copy_skill(tmp_path)
    env = clean_env()
    cache = tmp_path / "uv-cache"
    result = run_uvx(artifact, env, cache, "--version")
    assert result.returncode == 0, result.stderr
    # The existing output shape is the bare version on stdout.
    assert result.stdout.strip() == "0.3.0"
    _assert_no_forbidden_outputs(result)
    # The launcher script is absent and no Draft-managed envs were created.
    assert not (artifact / "scripts" / "draft.py").exists()
    _assert_no_draft_managed_envs(artifact)


@pytest.mark.network
def test_init_and_check_pass_without_plugin_files_or_repo_parents(
    tmp_path: Path,
) -> None:
    artifact = copy_skill(tmp_path)
    env = clean_env()
    cache = tmp_path / "uv-cache"
    root = tmp_path / "draft-root"

    init = run_uvx(artifact, env, cache, "init", str(root))
    assert init.returncode == 0, init.stderr
    _assert_no_forbidden_outputs(init)

    # Add a valid child page so check has real structural work beyond the
    # freshly-initialized skeleton.
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n",
        encoding="utf-8",
    )
    (root / "pages" / "a.md").write_text(
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n",
        encoding="utf-8",
    )

    check = run_uvx(artifact, env, cache, "check", "--root", str(root))
    assert check.returncode == 0, check.stderr
    _assert_no_forbidden_outputs(check)

    # No plugin files or repository parents: the copied skill sits beneath an
    # unrelated host dir with no ``.git`` and no plugin/manifest ancestors.
    assert not (artifact / "scripts" / "draft.py").exists()
    assert not (artifact / ".git").exists()
    assert not (artifact.parent / ".git").exists()
    # No plugin manifest or plugin-only directories carried into the skill.
    assert not (artifact / ".codex-plugin").exists()
    assert not (artifact / ".claude-plugin").exists()
    assert not (artifact / "hooks").exists()
    _assert_no_draft_managed_envs(artifact, root)


def test_missing_interpreter_returns_uv_error_not_draft_json(
    tmp_path: Path,
) -> None:
    artifact = copy_skill(tmp_path)
    env = clean_env()
    env["UV_PYTHON"] = str(tmp_path / "nonexistent" / "python")
    env["UV_PYTHON_DOWNLOADS"] = "never"
    cache = tmp_path / "uv-cache"
    result = run_uvx(artifact, env, cache, "--version")
    # uv's nonzero status, not a Draft exit code.
    assert result.returncode != 0
    # uv's stderr is present and reports the interpreter failure.
    assert result.stderr, "expected uv stderr for missing interpreter"
    assert "interpreter" in result.stderr.lower() or "python" in result.stderr.lower()
    # Must NOT be a Draft JSON error envelope on stdout.
    assert not result.stdout.strip().startswith("{")
    assert '"code"' not in result.stdout
    assert '"result_type"' not in result.stdout


@pytest.mark.network
def test_uvx_resolves_from_pyproject_when_lock_absent(tmp_path: Path) -> None:
    artifact = copy_skill(tmp_path, drop_lock=True)
    assert not (artifact / "scripts" / "runtime" / "uv.lock").exists()
    env = clean_env()
    cache = tmp_path / "uv-cache"
    result = run_uvx(artifact, env, cache, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.3.0"
    _assert_no_forbidden_outputs(result)
    assert not (artifact / "scripts" / "draft.py").exists()
    _assert_no_draft_managed_envs(artifact)
