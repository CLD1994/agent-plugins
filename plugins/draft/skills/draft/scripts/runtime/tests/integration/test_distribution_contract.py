from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path

from draft.diagnostic_codes import (
    CheckCode,
    InvalidRelationReason,
    InvalidStatusReason,
    LinkEscapeKind,
    ParseErrorReason,
    ReplacementStatusReason,
    RootMissingKind,
    WrongOutputReason,
)
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

DIAGNOSTIC_CODE_OWNER = RUNTIME_ROOT / "src/draft/diagnostic_codes.py"
DIAGNOSTIC_CODE_LITERAL_CONSUMERS = tuple(
    path
    for path in sorted((RUNTIME_ROOT / "src/draft").rglob("*.py"))
    if path != DIAGNOSTIC_CODE_OWNER
) + tuple(sorted((RUNTIME_ROOT / "tests").rglob("*.py")))


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


# 分支判别值（如 "not_single_code_span"、"duplicate"）与普通业务字符串（如
# LocationKind 的 "file"、parametrize 参数名 "destination"）共享字符串空间，不能像
# 检查码一样做全局字面量扫描。这里只检查出现在诊断 details 位置的裸字符串：details
# 元组元素、诊断构造调用（_issue / CheckIssue / DraftError / error_issue）实参、
# 以及 details 下标比较的另一侧。其余出现位置不属于分支值，允许保留裸字符串。
_BRANCH_VALUE_ENUMS = (
    RootMissingKind,
    InvalidStatusReason,
    InvalidRelationReason,
    ParseErrorReason,
    LinkEscapeKind,
    ReplacementStatusReason,
    WrongOutputReason,
)
_BRANCH_VALUES: frozenset[str] = frozenset(
    member.value for enum in _BRANCH_VALUE_ENUMS for member in enum
)
_DIAGNOSTIC_DETAIL_CALLS = frozenset({"_issue", "CheckIssue", "DraftError", "error_issue"})


def _branch_literals_in_detail_positions(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value not in _BRANCH_VALUES:
            continue
        parent = parents.get(node)
        if isinstance(parent, ast.Tuple):
            offenders.append((node.lineno, node.value))
        elif isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name):
            if parent.func.id in _DIAGNOSTIC_DETAIL_CALLS:
                offenders.append((node.lineno, node.value))
        elif isinstance(parent, ast.Compare):
            for operand in (parent.left, *parent.comparators):
                if operand is node:
                    continue
                subscript_details = (
                    isinstance(operand, ast.Subscript)
                    and isinstance(operand.value, ast.Attribute)
                    and operand.value.attr == "details"
                )
                attribute_details = isinstance(operand, ast.Attribute) and operand.attr == "details"
                if subscript_details or attribute_details:
                    offenders.append((node.lineno, node.value))
                    break
    return sorted(set(offenders))


def test_branch_discriminators_have_no_bare_literals_in_detail_positions() -> None:
    offenders = {
        path.relative_to(RUNTIME_ROOT).as_posix(): literals
        for path in sorted((RUNTIME_ROOT / "tests").rglob("*.py"))
        if (literals := _branch_literals_in_detail_positions(path))
    }
    assert offenders == {}
