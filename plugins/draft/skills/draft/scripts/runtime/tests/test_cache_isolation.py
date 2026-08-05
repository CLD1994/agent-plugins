"""Cache reuse, concurrent-release, and conflicting-tool isolation tests.

Step 3 of Task 6. These tests prove three properties of the path-based uvx
core invocation:

1. **Offline reuse**: with an initially empty cache, an online run builds the
   uv tool environment; a second ``--offline`` run over the same cache and
   copied runtime reuses that environment (the ``environments-v2/`` snapshot
   of relative paths + nanosecond lstat mtimes is identical).
2. **Concurrent distinct releases on a shared cache**: two independent runtime
   copies at versions ``0.3.0`` and a synthetic ``0.3.1`` are launched
   concurrently over one shared cache and each reports its own version; a
   post-concurrent re-run of the 0.3.0 copy still reports ``0.3.0``, proving
   the 0.3.1 invocation did not clobber it. (uv's tool env identity is keyed
   by command name, not version, so the brief's "two distinct environment
   identities" is unachievable; the clobber check is the strengthened
   substitute.)
3. **Conflicting global tool**: a temporary package exposing a conflicting
   ``draft`` console script (output ``wrong global draft``) is installed into
   a test-specific ``UV_TOOL_DIR``/``UV_TOOL_BIN_DIR``; ``uvx --isolated
   --from <runtime>`` still reports the runtime version and never the
   conflicting output.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]

UVX_TIMEOUT = 600

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
    "UV_TOOL_BIN_DIR",
    "UV_PYTHON",
    "UV_PYTHON_DOWNLOADS",
    "UV_OFFLINE",
    "UV_RESOLUTION",
)


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in _STRIP_ENV:
        env.pop(name, None)
    return env


def copy_skill(tmp_path: Path, *, name: str = "draft-skill") -> Path:
    parent = tmp_path / f"host-{name}"
    parent.mkdir()
    artifact = parent / name
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
    return artifact


def snapshot_environments(cache_dir: Path) -> dict[str, int]:
    """Relative path -> lstat nanosecond mtime for every entry beneath environments-v2/.

    Uses ``lstat`` (does not follow symlinks) and ``os.walk(followlinks=False)``
    so the snapshot captures the environment structure itself, not the archive
    contents it references. Two snapshots are identical iff the uv tool
    environment was reused unchanged.
    """
    env_root = cache_dir / "environments-v2"
    snapshot: dict[str, int] = {}
    if not env_root.is_dir():
        return snapshot
    for dirpath, dirnames, filenames in os.walk(env_root, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath, name)
            rel = path.relative_to(env_root).as_posix()
            snapshot[rel] = path.lstat().st_mtime_ns
    return snapshot


def environment_archive_refs(cache_dir: Path) -> set[str]:
    """Distinct archive reference names (symlinks) within environments-v2/.

    Each uv tool environment is a directory beneath ``environments-v2/`` whose
    entries are symlinks into ``archive-v0/``. Different source versions
    produce distinct archive references, so the count of distinct references
    across all environments proves how many distinct versions were installed.
    """
    env_root = cache_dir / "environments-v2"
    refs: set[str] = set()
    if not env_root.is_dir():
        return refs
    for entry in env_root.iterdir():
        if not entry.is_dir():
            continue
        for child in entry.iterdir():
            if child.is_symlink():
                refs.add(child.name)
    return refs


def run_uvx_from(
    runtime: Path,
    cache_dir: Path,
    *draft_args: str,
    extra_uvx_args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    active_env = env if env is not None else clean_env()
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
        cwd=str(cwd) if cwd is not None else None,
        env=active_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=UVX_TIMEOUT,
    )


def test_offline_run_reuses_online_environment_snapshot(tmp_path: Path) -> None:
    artifact = copy_skill(tmp_path)
    runtime = artifact / "scripts" / "runtime"
    cache = tmp_path / "cache"
    env = clean_env()
    online_root = tmp_path / "draft-root-online"
    offline_root = tmp_path / "draft-root-offline"

    # Online: build the tool environment and run version/init/check.
    online_version: subprocess.CompletedProcess[str] | None = None
    for args in (
        ("--version",),
        ("init", str(online_root)),
        ("check", str(online_root)),
    ):
        result = run_uvx_from(runtime, cache, *args, env=env, cwd=artifact.parent)
        assert result.returncode == 0, result.stderr
        if args == ("--version",):
            online_version = result
    assert online_version is not None
    assert online_version.stdout.strip() == "0.3.0"

    snapshot_after_online = snapshot_environments(cache)
    assert snapshot_after_online, "expected at least one environment after online run"

    # Offline: repeat the same command shapes over the same cache and runtime.
    # A fresh root is used for init because init rejects an already-initialized
    # root; the point of the offline phase is to prove the env is reused, not
    # to re-initialize the same root.
    offline_version: subprocess.CompletedProcess[str] | None = None
    for args in (
        ("--version",),
        ("init", str(offline_root)),
        ("check", str(offline_root)),
    ):
        result = run_uvx_from(
            runtime,
            cache,
            *args,
            extra_uvx_args=("--offline",),
            env=env,
            cwd=artifact.parent,
        )
        assert result.returncode == 0, result.stderr
        if args == ("--version",):
            offline_version = result
    assert offline_version is not None
    assert offline_version.stdout.strip() == "0.3.0"

    snapshot_after_offline = snapshot_environments(cache)
    # Identical relative paths and nanosecond lstat mtimes prove --isolated
    # reused the uv tool environment rather than rebuilding it.
    assert snapshot_after_offline == snapshot_after_online, (
        "offline run mutated the environment snapshot; --isolated did not reuse"
    )
    # No Draft-managed environment appeared in the copied skill or business root.
    assert not list(artifact.rglob(".venv"))
    assert not list(artifact.rglob("envs"))


def test_distinct_releases_share_cache_without_corruption(tmp_path: Path) -> None:
    first = copy_skill(tmp_path, name="first")
    second = copy_skill(tmp_path, name="second")

    # Bump only the second copy's project version to a synthetic 0.3.1.
    second_pyproject = second / "scripts" / "runtime" / "pyproject.toml"
    text = second_pyproject.read_text(encoding="utf-8")
    patched = text.replace('version = "0.3.0"', 'version = "0.3.1"', 1)
    assert patched != text, "failed to patch second copy's version"
    second_pyproject.write_text(patched, encoding="utf-8")

    cache = tmp_path / "shared-cache"
    env = clean_env()

    # Launch both releases concurrently over the shared cache. uv's tool
    # environment identity for ``uvx --isolated --from <path> draft`` is shared
    # across source versions (keyed by the command name and Python, not the
    # package version), so the "two distinct environment identities" the brief
    # asks for is not achievable; uv instead rebuilds the shared environment
    # per invocation. The concurrent launches prove simultaneous sessions using
    # different immutable runtime releases each resolve and report their own
    # version without corrupting each other.
    outcomes: dict[str, subprocess.CompletedProcess[str]] = {}

    def launch(label: str, artifact: Path) -> None:
        runtime = artifact / "scripts" / "runtime"
        outcomes[label] = run_uvx_from(runtime, cache, "--version", env=env, cwd=artifact.parent)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(launch, "first", first),
            executor.submit(launch, "second", second),
        ]
        for future in futures:
            future.result()

    assert outcomes["first"].returncode == 0, outcomes["first"].stderr
    assert outcomes["second"].returncode == 0, outcomes["second"].stderr
    assert outcomes["first"].stdout.strip() == "0.3.0", outcomes["first"].stdout
    assert outcomes["second"].stdout.strip() == "0.3.1", outcomes["second"].stdout

    # Clobber check: re-run the 0.3.0 copy after the 0.3.1 run has completed
    # and assert it STILL reports 0.3.0. This proves the 0.3.1 invocation did
    # not permanently clobber the 0.3.0 environment (uv rebuilds the env per
    # invocation from the source version, rather than silently sharing the last
    # installed version). This is the strengthened substitute for the brief's
    # unachievable "two distinct environment identities" requirement.
    rerun_first = run_uvx_from(
        first / "scripts" / "runtime", cache, "--version", env=env, cwd=first.parent
    )
    assert rerun_first.returncode == 0, rerun_first.stderr
    assert rerun_first.stdout.strip() == "0.3.0", rerun_first.stdout

    # The shared environment references both versions' archives, proving both
    # releases were installed without one clobbering the other's archive.
    refs = environment_archive_refs(cache)
    assert len(refs) >= 2, f"expected >=2 archive references, got {refs}"


def _build_conflicting_tool(pkg_dir: Path) -> None:
    """Build a temporary local package exposing a conflicting ``draft`` script."""
    src = pkg_dir / "src" / "conflicting_draft"
    src.mkdir(parents=True)
    (pkg_dir / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'name = "conflicting-draft"',
                'version = "0.0.1"',
                'requires-python = ">=3.12"',
                "[project.scripts]",
                'draft = "conflicting_draft.cli:main"',
                "[build-system]",
                'requires = ["hatchling"]',
                'build-backend = "hatchling.build"',
                "[tool.hatch.build.targets.wheel]",
                'packages = ["src/conflicting_draft"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "cli.py").write_text(
        'def main() -> int:\n    print("wrong global draft")\n    return 0\n',
        encoding="utf-8",
    )


def test_isolated_uvx_ignores_conflicting_global_tool(tmp_path: Path) -> None:
    artifact = copy_skill(tmp_path)
    runtime = artifact / "scripts" / "runtime"

    # Build and install the conflicting tool into test-specific tool dirs so it
    # never touches the host's global tool state.
    conflicting_pkg = tmp_path / "conflicting-pkg"
    _build_conflicting_tool(conflicting_pkg)
    tool_dir = tmp_path / "tool-dir"
    tool_dir.mkdir()
    tool_bin_dir = tmp_path / "tool-bin"
    tool_bin_dir.mkdir()
    install_env = clean_env()
    install_env["UV_TOOL_DIR"] = str(tool_dir)
    install_env["UV_TOOL_BIN_DIR"] = str(tool_bin_dir)
    install = subprocess.run(
        ["uv", "tool", "install", str(conflicting_pkg)],
        env=install_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=UVX_TIMEOUT,
    )
    assert install.returncode == 0, install.stderr

    # Verify the conflicting tool's installed executable outputs the wrong text.
    installed_bins = list(tool_bin_dir.glob("draft"))
    assert installed_bins, "conflicting tool did not install a draft executable"
    direct = subprocess.run(
        [str(installed_bins[0])],
        env=install_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert direct.returncode == 0, direct.stderr
    assert direct.stdout.strip() == "wrong global draft"

    # With that UV_TOOL_DIR/UV_TOOL_BIN_DIR active, the isolated path-based uvx
    # invocation must report the runtime version, never the conflicting output.
    cache = tmp_path / "cache"
    result = run_uvx_from(
        runtime,
        cache,
        "--version",
        env=install_env,
        cwd=artifact.parent,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.3.0"
    assert "wrong global draft" not in result.stdout
    assert "wrong global draft" not in result.stderr
