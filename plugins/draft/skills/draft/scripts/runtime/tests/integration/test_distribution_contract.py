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
)
from draft.cli_results import DryRunOperation, ResultType
from draft.vocabulary import RelationCode, StatusCode
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


# 分支判别值（如 "not_single_code_span"、"relation_requires_status"）与普通业务字符串（如
# LocationKind 的 "file"、parametrize 参数名 "destination"）共享字符串空间，不能像
# 检查码一样做全局字面量扫描。这里只报告与诊断 details 有可证关联的裸字符串：
# 诊断构造调用（_issue / CheckIssue / DraftError / error_issue）实参、以及和
# details 派生表达式（直接 .details、下标、list()/tuple() 保元素转换、按作用域
# 解析的简单别名）比较的另一侧容器。其余出现位置不属于分支值，允许保留裸字符串
# ——例如 `return ("relation_requires_status",)` 这样的普通元组必须不报告。
_BRANCH_VALUE_ENUMS = (
    RootMissingKind,
    InvalidStatusReason,
    InvalidRelationReason,
    ParseErrorReason,
    LinkEscapeKind,
    ReplacementStatusReason,
)
_BRANCH_VALUES: frozenset[str] = frozenset(
    member.value for enum in _BRANCH_VALUE_ENUMS for member in enum
)
_DIAGNOSTIC_DETAIL_CALLS = frozenset({"_issue", "CheckIssue", "DraftError", "error_issue"})
# 保持元素身份、只改变容器类型的调用，视作仍指向 details 的元素。
_ELEMENT_PRESERVING_CALLS = frozenset({"list", "tuple", "set", "frozenset"})


def _details_derived(node: ast.AST, aliases: frozenset[str]) -> bool:
    """表达式是否经下标、保元素调用或别名可达某个 `.details` 访问。"""
    if isinstance(node, ast.Attribute):
        return node.attr == "details"
    if isinstance(node, ast.Subscript):
        return _details_derived(node.value, aliases)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _ELEMENT_PRESERVING_CALLS
        and len(node.args) == 1
    ):
        return _details_derived(node.args[0], aliases)
    if isinstance(node, ast.Name):
        return node.id in aliases
    return False


def _scope_owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST:
    owner: ast.AST | None = node
    while owner is not None and not isinstance(
        owner, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)
    ):
        owner = parents.get(owner)
    assert owner is not None
    return owner


def _derived_aliases(
    tree: ast.Module, parents: dict[ast.AST, ast.AST]
) -> dict[int, frozenset[str]]:
    """每个作用域内被绑定到 details 派生表达式的名字集合（按作用域做固定点）。"""
    assignments: dict[int, dict[str, list[ast.AST]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value: ast.expr = node.value
            targets: list[ast.expr] = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value = node.value
            targets = [node.target]
        else:
            continue
        scope = id(_scope_owner(node, parents))
        bucket = assignments.setdefault(scope, {})
        for target in targets:
            if isinstance(target, ast.Name):
                bucket.setdefault(target.id, []).append(value)

    aliases: dict[int, frozenset[str]] = {}
    for scope, bucket in assignments.items():
        names: set[str] = set()
        changed = True
        while changed:
            changed = False
            for name, exprs in bucket.items():
                if name in names:
                    continue
                if any(_details_derived(expr, frozenset(names)) for expr in exprs):
                    names.add(name)
                    changed = True
        aliases[scope] = frozenset(names)
    return aliases


def _branch_literals_in_source(source: str) -> list[tuple[int, str]]:
    """返回源码中与 details 关联的分支值裸字符串（(行号, 值)）。"""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    aliases = _derived_aliases(tree, parents)

    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.value not in _BRANCH_VALUES:
            continue
        scope = id(_scope_owner(node, parents))
        names = aliases.get(scope, frozenset())

        parent = parents.get(node)
        if (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id in _DIAGNOSTIC_DETAIL_CALLS
        ):
            offenders.append((node.lineno, node.value))
            continue
        container: ast.AST = parent if isinstance(parent, (ast.Tuple, ast.List)) else node
        cparent = parents.get(container)
        if isinstance(cparent, ast.Compare):
            for operand in (cparent.left, *cparent.comparators):
                if operand is container:
                    continue
                if _details_derived(operand, names):
                    offenders.append((node.lineno, node.value))
                    break
        elif (
            isinstance(cparent, ast.Call)
            and isinstance(cparent.func, ast.Name)
            and cparent.func.id in _DIAGNOSTIC_DETAIL_CALLS
        ):
            offenders.append((node.lineno, node.value))
    return sorted(set(offenders))


def _branch_literals_in_detail_positions(path: Path) -> list[tuple[int, str]]:
    return _branch_literals_in_source(path.read_text(encoding="utf-8"))


def test_branch_discriminators_have_no_bare_literals_in_detail_positions() -> None:
    offenders = {
        path.relative_to(RUNTIME_ROOT).as_posix(): literals
        for path in sorted((RUNTIME_ROOT / "tests").rglob("*.py"))
        if (literals := _branch_literals_in_detail_positions(path))
    }
    assert offenders == {}


def test_branch_check_ignores_plain_tuples() -> None:
    """普通元组中的分支值（与 details 无关联）必须不报告。"""
    assert _branch_literals_in_source('def f():\n    return ("relation_requires_status",)\n') == []


def test_branch_check_reports_direct_details_compare() -> None:
    """直接与 `.details` 比较的元组或标量必须报告。"""
    assert _branch_literals_in_source(
        'assert issue.details == ("relation_requires_status",)\n'
    ) == [(1, "relation_requires_status")]
    assert _branch_literals_in_source('assert "relation_requires_status" in issue.details\n') == [
        (1, "relation_requires_status")
    ]


def test_branch_check_reports_details_alias_access() -> None:
    """通过局部变量别名访问 details 必须报告；非 details 别名的同名下标不报告。"""
    assert _branch_literals_in_source(
        'details = issue.details\nassert details[0] == "relation_requires_status"\n'
    ) == [(2, "relation_requires_status")]
    assert (
        _branch_literals_in_source(
            'details = ["a", "b"]\nassert details[0] == "relation_requires_status"\n'
        )
        == []
    )


def test_branch_check_reports_details_conversions() -> None:
    """list()/tuple() 等保元素转换形式必须报告。"""
    assert _branch_literals_in_source(
        'assert list(issue.details) == ["relation_requires_status"]\n'
    ) == [(1, "relation_requires_status")]
    assert _branch_literals_in_source(
        'assert tuple(issue.details) == ("relation_requires_status",)\n'
    ) == [(1, "relation_requires_status")]


# 稳定状态/关系协议值只由 vocabulary.py 拥有。这里扫描生产源码中的原始字面量，
# 而不是测试或文档：语义所有权检查不能用普通文本包含/排除代替（见 AGENTS.md）。
VOCABULARY_OWNER = RUNTIME_ROOT / "src/draft/vocabulary.py"
_VOCABULARY_VALUES: frozenset[str] = frozenset(
    member.value for enum in (StatusCode, RelationCode) for member in enum
)


def _vocabulary_literal_offenders() -> dict[str, list[tuple[int, str]]]:
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted((RUNTIME_ROOT / "src/draft").rglob("*.py")):
        if path == VOCABULARY_OWNER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        hits: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value not in _VOCABULARY_VALUES:
                continue
            parent = parents.get(node)
            # JSON 字段名（dict 键）不是协议值发射；只有作为值出现的字面量才计数。
            if isinstance(parent, ast.Dict) and node in parent.keys:
                continue
            hits.append((node.lineno, node.value))
        hits = sorted(set(hits))
        if hits:
            offenders[path.relative_to(RUNTIME_ROOT).as_posix()] = hits
    return offenders


def test_status_and_relation_values_have_one_literal_owner() -> None:
    assert _vocabulary_literal_offenders() == {}


# result_type 与预演 operation 值只由 cli_results.py 拥有。生产源码中的裸字面量
# 会被视为第二份完整定义；测试最接近输出层的精确断言属于测试预期，不参与扫描。
CLI_RESULTS_OWNER = RUNTIME_ROOT / "src/draft/cli_results.py"
_CLI_RESULTS_VALUES: frozenset[str] = frozenset(
    member.value
    for enum in (ResultType, DryRunOperation)
    for member in enum
)


def _cli_result_literal_offenders() -> dict[str, list[tuple[int, str]]]:
    """只在生产模块里检测“第二份完整字面量集合”，不扫单个合法使用。

    result_type/operation 值与子命令名、journal kind 等共享字符串空间，单个出现
    可能是 argparse 名称或 kind 比较；只有 3 个及以上不同值出现在同一个容器
    （元组/列表/集合）里，才可能构成第二份权威清单。
    """
    offenders: dict[str, list[tuple[int, str]]] = {}
    for path in sorted((RUNTIME_ROOT / "src/draft").rglob("*.py")):
        if path == CLI_RESULTS_OWNER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            values = {
                elt.value
                for elt in node.elts
                if isinstance(elt, ast.Constant)
                and isinstance(elt.value, str)
                and elt.value in _CLI_RESULTS_VALUES
            }
            if len(values) >= 3:
                hits.append((node.lineno, ",".join(sorted(values))))
        if hits:
            offenders[path.relative_to(RUNTIME_ROOT).as_posix()] = hits
    return offenders


def test_result_type_and_operation_values_have_one_literal_owner() -> None:
    assert _cli_result_literal_offenders() == {}
