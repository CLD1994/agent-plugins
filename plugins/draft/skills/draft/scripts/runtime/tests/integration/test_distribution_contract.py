from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path

from draft.diagnostic_codes import CheckCode
from tests.support.paths import RUNTIME_ROOT

SKILL_ROOT = RUNTIME_ROOT.parents[1]
PLUGIN_ROOT = SKILL_ROOT.parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]

# Modules that exist only to serve plugin responsibilities. The core runtime
# must never ship them: their presence would mean plugin code leaked back into
# the self-contained runtime package.
PLUGIN_ONLY_MODULES = (
    "entry.py",
    "plugin_context.py",
    "bindings.py",
    "session_hook.py",
)


def test_runtime_belongs_to_the_skill() -> None:
    assert RUNTIME_ROOT == SKILL_ROOT / "scripts/runtime"
    assert (SKILL_ROOT / "SKILL.md").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()


def test_skill_is_a_self_contained_runtime_unit() -> None:
    scripts = SKILL_ROOT / "scripts"
    assert (scripts / "runtime/pyproject.toml").is_file()
    assert (scripts / "runtime/uv.lock").is_file()
    assert (scripts / "runtime/src/draft/__main__.py").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()


def test_runtime_ruff_configuration_is_auto_discovered() -> None:
    """核心检查必须从 runtime 项目自身发现 Ruff 配置和源码根。"""
    result = subprocess.run(
        ["ruff", "check", "--show-settings", str(RUNTIME_ROOT / "src/draft/cli.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f'Settings path: "{RUNTIME_ROOT / "pyproject.toml"}"' in result.stdout
    assert f'linter.project_root = "{RUNTIME_ROOT}"' in result.stdout
    assert f'linter.src = [\n\t"{RUNTIME_ROOT / "src"}",\n]' in result.stdout
    assert "linter.unresolved_target_version = 3.12" in result.stdout
    assert "linter.line_length = 100" in result.stdout
    assert "unsorted-imports (I001)" in result.stdout
    assert "assignment-to-os-environ (B003)" in result.stdout
    assert "collapsible-if (SIM102)" in result.stdout


def test_runtime_provides_draft_cli_main_entry_point() -> None:
    pyproject = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"].get("scripts", {})
    assert scripts.get("draft") == "draft.cli:main"


def test_runtime_src_contains_no_plugin_only_modules() -> None:
    draft_pkg = RUNTIME_ROOT / "src" / "draft"
    assert draft_pkg.is_dir()
    for module in PLUGIN_ONLY_MODULES:
        assert not (draft_pkg / module).exists(), f"plugin-only module {module} present in runtime"


# 插件清单、市场清单与 Hook JSON 的精确字段只由适配层契约测试
# plugins/draft/scripts/tests/integration/test_distribution_contract.py 拥有；
# 核心测试不复制这些产物字段，避免同一契约在两个测试树漂移。

DIAGNOSTIC_CODE_LITERAL_CONSUMERS = (
    RUNTIME_ROOT / "src/draft/diagnostics.py",
    RUNTIME_ROOT / "src/draft/graph.py",
    RUNTIME_ROOT / "src/draft/markdown.py",
    RUNTIME_ROOT / "src/draft/lifecycle.py",
    RUNTIME_ROOT / "src/draft/paths.py",
)


def _raw_registered_code_literals(path: Path) -> list[tuple[int, str]]:
    registered = frozenset(code.value for code in CheckCode)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sorted(
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value in registered
    )


def test_registered_diagnostic_codes_have_one_literal_owner() -> None:
    offenders = {
        path.relative_to(RUNTIME_ROOT).as_posix(): literals
        for path in DIAGNOSTIC_CODE_LITERAL_CONSUMERS
        if (literals := _raw_registered_code_literals(path))
    }
    assert offenders == {}
