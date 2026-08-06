"""Lowest-bound and highest-available dependency-window subprocess suites.

Step 2 of Task 6. These tests prove the declared dependency windows in
``pyproject.toml`` are satisfiable at both extremes and that the runtime's
domain test suite passes under each resolution.

* Lowest-bound: a fresh cache with ``--constraints minimum-runtime.txt`` and
  ``--build-constraints minimum-build.txt`` pins every direct dependency to
  the exact minimum declared in ``pyproject.toml``. The fresh-cache local
  build plus ``--build-constraints`` proves the build backend works at
  ``hatchling==1.31.0``.
* Highest-available: another empty cache with ``--refresh --resolution
  highest`` resolves every dependency to the latest version inside its
  declared window.

For each bound, a ``python -c`` metadata probe (using
``importlib.metadata.version``, never imports of the packages under test) runs
first in the SAME cache, asserting the resolved versions; then the domain
suite runs in that same cache. Sharing the cache means the probe verifies the
exact wheel versions the domain suite will install. The domain suite runs via
``uvx --isolated --from .`` with ``cwd=RUNTIME_ROOT`` and targets only
``tests/unit``, so the slow integration suites never recurse under the
constrained environment.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.support.paths import CONSTRAINTS_ROOT, RUNTIME_ROOT

CONSTRAINTS_RUNTIME = CONSTRAINTS_ROOT / "minimum-runtime.txt"
CONSTRAINTS_BUILD = CONSTRAINTS_ROOT / "minimum-build.txt"

# Metadata probe: uses importlib.metadata.version (stdlib) with the package
# name as a string. Never imports markdown_it or send2trash.
VERSION_PROBE = (
    "from importlib.metadata import version\n"
    "print(f\"markdown-it-py={version('markdown-it-py')}\")\n"
    "print(f\"Send2Trash={version('Send2Trash')}\")"
)

# Generous timeout: resolution + build + install + full domain suite.
BOUNDS_TIMEOUT = 600


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "UV_CACHE_DIR",
        "UV_TOOL_DIR",
        "UV_PYTHON",
        "UV_PYTHON_DOWNLOADS",
        "UV_OFFLINE",
        "UV_RESOLUTION",
        "UV_PROJECT_ENVIRONMENT",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ):
        env.pop(name, None)
    return env


def _run_uvx(cache_dir: Path, *uvx_args: str) -> subprocess.CompletedProcess[str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["uvx", "--isolated", "--cache-dir", str(cache_dir), *uvx_args],
        cwd=RUNTIME_ROOT,
        env=_clean_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=BOUNDS_TIMEOUT,
    )


def _parse_probe(output: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in output.strip().splitlines():
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        pairs[name] = value
    return pairs


def _major_minor(version: str) -> tuple[int, int]:
    match = re.match(r"^(\d+)\.(\d+)", version)
    assert match is not None, f"unparseable version: {version!r}"
    return int(match.group(1)), int(match.group(2))


@pytest.mark.network
def test_lowest_bound_probe_and_domain_suite_share_cache(tmp_path: Path) -> None:
    cache = tmp_path / "low-cache"

    # Probe first in the shared cache: assert exact minimum versions resolved.
    probe = _run_uvx(
        cache,
        "--constraints",
        str(CONSTRAINTS_RUNTIME),
        "--build-constraints",
        str(CONSTRAINTS_BUILD),
        "--from",
        ".",
        "python",
        "-c",
        VERSION_PROBE,
    )
    assert probe.returncode == 0, probe.stderr
    pairs = _parse_probe(probe.stdout)
    assert pairs.get("markdown-it-py") == "4.2.0", pairs
    assert pairs.get("Send2Trash") == "2.1.0", pairs

    # Domain suite in the SAME cache: the constrained wheels the probe verified
    # are the ones the suite installs. The fresh-cache local build plus
    # ``--build-constraints`` proves the build backend works at hatchling 1.31.0.
    suite = _run_uvx(
        cache,
        "--constraints",
        str(CONSTRAINTS_RUNTIME),
        "--build-constraints",
        str(CONSTRAINTS_BUILD),
        "--from",
        ".",
        "--with",
        "pytest",
        "pytest",
        "tests/unit",
        "-q",
    )
    assert suite.returncode == 0, suite.stderr + suite.stdout


@pytest.mark.network
def test_highest_available_probe_and_domain_suite_share_cache(tmp_path: Path) -> None:
    cache = tmp_path / "high-cache"

    # Probe first in the shared cache: assert major.minor stays inside the
    # declared window (do not pin patch or transitive versions).
    probe = _run_uvx(
        cache,
        "--refresh",
        "--resolution",
        "highest",
        "--from",
        ".",
        "python",
        "-c",
        VERSION_PROBE,
    )
    assert probe.returncode == 0, probe.stderr
    pairs = _parse_probe(probe.stdout)
    assert _major_minor(pairs["markdown-it-py"]) == (4, 2), pairs
    assert _major_minor(pairs["Send2Trash"]) == (2, 1), pairs

    # Domain suite in the SAME cache at the highest-available resolution.
    suite = _run_uvx(
        cache,
        "--refresh",
        "--resolution",
        "highest",
        "--from",
        ".",
        "--with",
        "pytest",
        "pytest",
        "tests/unit",
        "-q",
    )
    assert suite.returncode == 0, suite.stderr + suite.stdout
