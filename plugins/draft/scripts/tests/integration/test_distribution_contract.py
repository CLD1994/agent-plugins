"""插件级分发静态契约：manifest、Hook 命令、物理边界、版本关系、导入边界。

静态断言（不启动 uvx、不访问网络、不依赖冷缓存）：
- 两个 manifest 声明插件版本 ``0.3.0``、``./skills/`` 与各自 Hook 文件；
- 两个插件版本彼此相等，但不与 runtime 版本比较；
- Codex/Claude marketplace 选择普通 ``plugins/draft`` 目录，不需要递归 Git 操作；
- Hook 命令字面量严格使用 ``scripts/draft.py hook session-start HOST``；
- 插件入口、skill、runtime 项目和适配层模块都是普通文件；
- 适配层从仓库根检查时自动发现自己的 Ruff 配置和源码根；
- skill 级 ``scripts/draft.py``、``.gitmodules``、gitlink 模式 ``160000``、生成式发布树、
  核心源码下的插件导入都不存在；
- 适配层源码没有非标准库导入，也没有对 Draft 核心的执行/导入引用。

需要真实子进程、网络和冷 uv 缓存的宿主流程与运行时安装冒烟测试位于
``test_distribution_smoke.py``。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from tests.support.paths import PLUGIN_ROOT, RUNTIME_ROOT, SCRIPTS_ROOT

SKILL_ROOT = PLUGIN_ROOT / "skills" / "draft"
REPO_ROOT = PLUGIN_ROOT.parents[1]
ADAPTER_ROOT = SCRIPTS_ROOT / "adapter"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast("dict[str, Any]", value)


def load_pyproject_version(path: Path) -> str:
    assert path.is_file(), f"missing pyproject: {path}"
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match is not None, f"no version in {path}"
    return match.group(1)


# ---------------------------------------------------------------------------
# manifest、物理边界、版本关系
# ---------------------------------------------------------------------------


def test_both_manifests_declare_plugin_version_0_3_0() -> None:
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["version"] == "0.3.0", codex
    assert claude["version"] == "0.3.0", claude


def test_both_manifests_declare_skills_and_own_hook_file() -> None:
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["skills"] == "./skills/"
    assert claude["skills"] == "./skills/"
    assert codex["hooks"] == "./hooks/codex-hooks.json"
    assert claude["hooks"] == "./hooks/claude-hooks.json"
    assert (PLUGIN_ROOT / "hooks/codex-hooks.json").is_file()
    assert (PLUGIN_ROOT / "hooks/claude-hooks.json").is_file()


def test_plugin_manifest_versions_match_each_other() -> None:
    """两个 manifest 版本必须相等；本测试刻意不与 runtime 版本比较。

    设计规格要求插件 manifest 版本与 runtime 版本之间不再有数值耦合。
    """
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["version"] == claude["version"]


def test_plugin_manifest_versions_are_independent_of_runtime_version() -> None:
    """显式记录独立性：runtime 版本与插件版本可能巧合相同（如本次 0.3.0），
    但测试不把二者绑定。这里只断言当前 runtime 版本可读，不与 manifest 比较。"""
    runtime_version = load_pyproject_version(RUNTIME_ROOT / "pyproject.toml")
    assert runtime_version, "runtime version must be declared"
    # 不写 assert codex_version == runtime_version：那将违背独立性语义。


def test_marketplace_registries_select_ordinary_plugins_draft_dir() -> None:
    codex = load_json(REPO_ROOT / ".agents/plugins/marketplace.json")
    claude = load_json(REPO_ROOT / ".claude-plugin/marketplace.json")
    assert codex["plugins"][0]["name"] == "draft"
    # Codex marketplace 使用 source.path 指向普通目录
    codex_source = codex["plugins"][0]["source"]
    assert codex_source["source"] == "local"
    assert codex_source["path"] == "./plugins/draft"
    assert claude["plugins"][0]["name"] == "draft"
    assert claude["plugins"][0]["source"] == "./plugins/draft"
    # 不要求递归 Git 操作：源是本地普通目录，不引用 submodule/递归初始化
    codex_blob = json.dumps(codex, ensure_ascii=False)
    claude_blob = json.dumps(claude, ensure_ascii=False)
    for forbidden in ("submodule", "recursive", "gitmodules", "init"):
        assert forbidden.lower() not in codex_blob.lower()
        assert forbidden.lower() not in claude_blob.lower()


def test_hook_command_literals_match_specification() -> None:
    codex = load_json(PLUGIN_ROOT / "hooks/codex-hooks.json")
    claude = load_json(PLUGIN_ROOT / "hooks/claude-hooks.json")
    codex_command = codex["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    claude_command = claude["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert codex_command == 'uv run "${PLUGIN_ROOT}/scripts/draft.py" hook session-start codex'
    assert claude_command == (
        'uv run "${CLAUDE_PLUGIN_ROOT}/scripts/draft.py" hook session-start claude'
    )
    # 旧式私有拼写不应出现
    for command in (codex_command, claude_command):
        assert "_session-start" not in command
        assert "--python" not in command
        assert "--project" not in command
        assert "--no-project" not in command


def test_plugin_entry_skill_runtime_and_adapter_modules_are_ordinary_files() -> None:
    entry = PLUGIN_ROOT / "scripts" / "draft.py"
    skill_md = SKILL_ROOT / "SKILL.md"
    runtime_pyproject = RUNTIME_ROOT / "pyproject.toml"
    runtime_main = RUNTIME_ROOT / "src" / "draft" / "__main__.py"
    assert entry.is_file()
    assert not entry.is_symlink()
    assert skill_md.is_file()
    assert not skill_md.is_symlink()
    assert runtime_pyproject.is_file()
    assert not runtime_pyproject.is_symlink()
    assert runtime_main.is_file()
    assert not runtime_main.is_symlink()
    for module in ("__init__.py", "bridge.py", "bindings.py", "session_hook.py", "errors.py"):
        path = ADAPTER_ROOT / module
        assert path.is_file(), f"adapter module missing: {module}"
        assert not path.is_symlink(), f"adapter module is symlink: {module}"


def test_skill_level_launcher_does_not_exist() -> None:
    """核心外层 launcher 已删除：skill 级 ``scripts/draft.py`` 不得存在。"""
    skill_scripts = SKILL_ROOT / "scripts"
    assert skill_scripts.is_dir()
    assert not (skill_scripts / "draft.py").exists(), "skill-level launcher must not exist"
    # 核心开发配置归 runtime 项目所有，不在 skill scripts 上级保留第二份 Ruff 配置。
    assert not (skill_scripts / "ruff.toml").exists()
    assert (skill_scripts / "runtime").is_dir()


def test_plugin_adapter_ruff_configuration_is_auto_discovered() -> None:
    """适配层从仓库根检查时也必须使用自己负责的 Ruff 配置。"""
    result = subprocess.run(
        ["ruff", "check", "--show-settings", str(SCRIPTS_ROOT / "draft.py")],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert f'Settings path: "{SCRIPTS_ROOT / "ruff.toml"}"' in result.stdout
    assert f'linter.project_root = "{SCRIPTS_ROOT}"' in result.stdout
    assert f'linter.src = [\n\t"{SCRIPTS_ROOT}",\n]' in result.stdout
    assert "linter.unresolved_target_version = 3.12" in result.stdout
    assert "linter.line_length = 100" in result.stdout
    assert "unsorted-imports (I001)" in result.stdout
    assert "assignment-to-os-environ (B003)" in result.stdout
    assert "collapsible-if (SIM102)" in result.stdout


def test_no_gitmodules_or_gitlink_in_skill_or_plugin() -> None:
    assert not (REPO_ROOT / ".gitmodules").exists()
    assert not (PLUGIN_ROOT / ".gitmodules").exists()
    assert not (SKILL_ROOT / ".gitmodules").exists()
    # 检查 git 索引中 skill 路径不是 gitlink (mode 160000)
    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", str(SKILL_ROOT.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        # 形如 "<mode> <hash> <stage>\t<path>";gitlink 模式为 160000
        mode = line.split(" ", 1)[0]
        assert mode != "160000", f"gitlink found: {line}"


def test_no_generated_distribution_trees() -> None:
    """不存在生成式发布树（dist/、build/、release/ 等）。"""
    for forbidden in ("dist", "build", "release", "publish"):
        assert not (PLUGIN_ROOT / forbidden).exists(), forbidden
        assert not (SKILL_ROOT / forbidden).exists(), forbidden
        assert not (RUNTIME_ROOT / forbidden).exists(), forbidden


def test_no_plugin_imports_under_core_source() -> None:
    """核心源码下不得导入插件专用模块（adapter、bridge、bindings、session_hook、entry）。"""
    draft_pkg = RUNTIME_ROOT / "src" / "draft"
    plugin_modules = ("adapter", "bridge", "bindings", "session_hook", "plugin_context", "entry")
    for path in sorted(draft_pkg.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".")[0]
                    assert root_name not in plugin_modules, (
                        f"{path} imports plugin module {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                root_name = node.module.split(".")[0]
                assert root_name not in plugin_modules, (
                    f"{path} imports from plugin module {node.module}"
                )
        # 字符串引用也禁止（exec/eval/dynamic import 防护）
        for forbidden in (
            "import adapter",
            "from adapter",
            "import bridge",
            "from bridge",
            "import bindings",
            "from bindings",
            "import session_hook",
            "from session_hook",
        ):
            assert forbidden not in text, f"{path}: forbidden reference {forbidden!r}"


def test_adapter_source_has_no_non_stdlib_imports() -> None:
    """适配层源码只依赖 Python 标准库。"""
    stdlib = set(sys.stdlib_module_names)
    # adapter 包内部模块互相导入允许
    adapter_modules = {"adapter", "bridge", "bindings", "session_hook", "errors"}
    for module_file in sorted(ADAPTER_ROOT.glob("*.py")):
        text = module_file.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(module_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".")[0]
                    assert root_name in stdlib or root_name in adapter_modules, (
                        f"{module_file}: non-stdlib import {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                root_name = node.module.split(".")[0]
                assert root_name in stdlib or root_name in adapter_modules, (
                    f"{module_file}: non-stdlib from-import {node.module}"
                )


def test_adapter_source_has_no_draft_core_references() -> None:
    """适配层源码不得有对 Draft 核心的执行或导入引用。"""
    for module_file in sorted(ADAPTER_ROOT.glob("*.py")):
        text = module_file.read_text(encoding="utf-8")
        for forbidden in (
            "import draft",
            "from draft",
            "check_draft",
            "load_graph",
            "importlib.import_module",
            "__import__",
        ):
            assert forbidden not in text, f"{module_file}: forbidden reference {forbidden!r}"
        # 不得引用核心 CLI 模块路径
        for forbidden_module in (
            "draft.cli",
            "draft.checker",
            "draft.operations",
            "draft.structure",
            "draft.trash",
            "draft.lifecycle",
            "draft.markdown",
            "draft.graph",
        ):
            assert forbidden_module not in text, (
                f"{module_file}: forbidden core module reference {forbidden_module!r}"
            )


def test_skill_md_does_not_reference_plugin_concepts() -> None:
    """SKILL.md 不得引用 Step 5 审计覆盖的插件概念。

    审计正则：``PLUGIN_ROOT|PLUGIN_DATA|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA|
    DRAFT_DATA|DRAFT_SKILL_PATH|SessionStart|bridge|binding|marketplace``。
    另外验证 bind/unbind/plugin-context 命令形式不出现。
    """
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    for forbidden in (
        "PLUGIN_ROOT",
        "PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_DATA",
        "DRAFT_DATA",
        "DRAFT_SKILL_PATH",
        "SessionStart",
        "bridge",
        "binding",
        "marketplace",
    ):
        assert forbidden not in text, f"SKILL.md references {forbidden!r}"
    # 命令形式（比 bare word 更精确，避免误报英文/中文同形词）
    for command_form in (
        "bind ROOT",
        "unbind --session",
        "plugin-context reset",
        "hook session-start",
    ):
        assert command_form not in text, f"SKILL.md references {command_form!r}"


def test_skill_references_does_not_link_plugin_platform_setup() -> None:
    """skill references 目录不得包含 platform-setup.md（已迁移到插件根）。"""
    refs = SKILL_ROOT / "references"
    assert refs.is_dir()
    assert not (refs / "platform-setup.md").exists(), (
        "skill-level platform-setup.md must be deleted"
    )
    # SKILL.md 不得链接到 plugin-only platform-setup.md
    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert "platform-setup.md" not in text, "SKILL.md must not link to platform-setup.md"


def test_draft_format_reference_has_no_plugin_material() -> None:
    """draft-format.md 不得包含 bridge/binding/marketplace 等插件概念。"""
    text = (SKILL_ROOT / "references" / "draft-format.md").read_text(encoding="utf-8")
    for forbidden in (
        "PLUGIN_ROOT",
        "PLUGIN_DATA",
        "CLAUDE_PLUGIN_ROOT",
        "CLAUDE_PLUGIN_DATA",
        "DRAFT_DATA",
        "DRAFT_SKILL_PATH",
        "SessionStart",
        "bridge",
        "marketplace",
        "bind ROOT",
        "unbind --session",
        "plugin-context",
    ):
        assert forbidden not in text, f"draft-format.md references {forbidden!r}"
