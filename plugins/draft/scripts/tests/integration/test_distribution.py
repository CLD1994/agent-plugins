"""插件级分发门禁：manifest、物理边界、版本关系、无仓库父目录 smoke test。

Step 1 静态断言：
- 两个 manifest 声明插件版本 ``0.3.0``、``./skills/`` 与各自 Hook 文件；
- 两个插件版本彼此相等，但不与 runtime 版本比较；
- Codex/Claude marketplace 选择普通 ``plugins/draft`` 目录，不需要递归 Git 操作；
- Hook 命令字面量严格使用 ``scripts/draft.py hook session-start HOST``；
- 插件入口、skill、runtime 项目和适配层模块都是普通文件；
- 适配层从仓库根检查时自动发现自己的 Ruff 配置和源码根；
- skill 级 ``scripts/draft.py``、``.gitmodules``、gitlink 模式 ``160000``、生成式发布树、
  核心源码下的插件导入都不存在；
- 适配层源码没有非标准库导入，也没有对 Draft 核心的执行/导入引用。

Step 2 完整 smoke test：把 ``plugins/draft/``、宿主 ``data_dir`` 和安全运行目录复制到
一个无 ``.git``、无仓库父目录的临时父目录下。每个参数化用例：
1. 只设置当前宿主的 root/data 变量，调用复制后的 ``scripts/draft.py hook session-start HOST``；
2. 解析未绑定信封，断言其入口与命令格式只指向复制后的目录；
3. 用复制后的入口 ``bind`` 一个普通存在目录，重跑 Hook，断言 root/当前 skill/当前 runtime 检查命令；
4. ``unbind`` 后断言下一次 Hook 回到未绑定；
5. 用测试专用缓存通过 ``uvx --isolated --from`` 跑复制后 runtime 的 version/init/check；
6. 断言 bindings/logs 只在所选宿主 ``data_dir``，bridge 只在该用例的安全运行目录，Draft 业务
   文件只在所选 root，uv 缓存中没有 ``bindings/``、``hook.jsonl``、``.draft/``。

再用空缓存联网跑一次，然后用同一缓存加 ``--offline`` 重跑核心 smoke；断言输出/状态中无路径
解析回仓库检出。
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from tests.support.paths import SCRIPTS_ROOT

if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

PLUGIN_ROOT = SCRIPTS_ROOT.parent
SKILL_ROOT = PLUGIN_ROOT / "skills" / "draft"
RUNTIME_ROOT = SKILL_ROOT / "scripts" / "runtime"
REPO_ROOT = PLUGIN_ROOT.parents[1]
ADAPTER_ROOT = SCRIPTS_ROOT / "adapter"

HOSTS = ("codex", "claude")
HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}

UVX_TIMEOUT = 600


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
# Step 1 静态断言：manifest、物理边界、版本关系
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


# ---------------------------------------------------------------------------
# Step 2 完整无仓库父目录 smoke test
# ---------------------------------------------------------------------------


@dataclass
class SmokeCase:
    host: str
    plugin_copy: Path
    data_dir: Path
    safe_runtime_dir: Path
    cache_dir: Path
    draft_root: Path
    parent: Path

    @property
    def entry(self) -> Path:
        return self.plugin_copy / "scripts" / "draft.py"

    @property
    def skill_root(self) -> Path:
        return self.plugin_copy / "skills" / "draft"

    @property
    def skill_md(self) -> Path:
        return self.skill_root / "SKILL.md"

    @property
    def runtime_root(self) -> Path:
        return self.skill_root / "scripts" / "runtime"

    @property
    def root_var(self) -> str:
        return HOST_ENV[self.host][0]

    @property
    def data_var(self) -> str:
        return HOST_ENV[self.host][1]


def _clean_smoke_env() -> dict[str, str]:
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
        "UV_PROJECT_ENVIRONMENT",
        "UV_CACHE_DIR",
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
        "UV_PYTHON",
        "UV_PYTHON_DOWNLOADS",
        "UV_OFFLINE",
        "UV_RESOLUTION",
    ):
        env.pop(name, None)
    return env


def _make_smoke_case(tmp_path: Path, host: str) -> SmokeCase:
    """构造无 .git、无仓库父目录的临时父目录下的完整 smoke 用例。"""
    parent = tmp_path / f"no-repo-{host}"
    parent.mkdir()
    # 显式确认父目录及其所有祖先都不含 .git
    cursor = parent
    while cursor != cursor.parent:
        assert not (cursor / ".git").exists(), f".git found at {cursor}"
        cursor = cursor.parent

    plugin_copy = parent / "plugin"
    shutil.copytree(
        PLUGIN_ROOT,
        plugin_copy,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".venv",
        ),
    )
    data_dir = (parent / "data").resolve()
    data_dir.mkdir()
    safe_runtime_dir = (parent / "xdg").resolve()
    safe_runtime_dir.mkdir()
    safe_runtime_dir.chmod(0o700)
    cache_dir = (parent / "cache").resolve()
    cache_dir.mkdir()
    draft_root = (parent / "draft-root").resolve()
    draft_root.mkdir()
    return SmokeCase(
        host=host,
        plugin_copy=plugin_copy.resolve(),
        data_dir=data_dir,
        safe_runtime_dir=safe_runtime_dir,
        cache_dir=cache_dir,
        draft_root=draft_root,
        parent=parent.resolve(),
    )


def _session_stdin(session_id: str = "smoke-session", source: str = "startup") -> str:
    return json.dumps(
        {
            "hook_event_name": "SessionStart",
            "session_id": session_id,
            "source": source,
            "transcript_path": "/not/read/transcript.jsonl",
            "cwd": "/workspace",
        },
        ensure_ascii=False,
    )


def _run_hook(
    case: SmokeCase, *, session_id: str = "smoke-session", source: str = "startup"
) -> subprocess.CompletedProcess[str]:
    env = _clean_smoke_env()
    env[case.root_var] = str(case.plugin_copy)
    env[case.data_var] = str(case.data_dir)
    env["XDG_RUNTIME_DIR"] = str(case.safe_runtime_dir)
    # 用 uv run 调用复制后的入口
    return subprocess.run(
        ["uv", "run", str(case.entry), "hook", "session-start", case.host],
        capture_output=True,
        text=True,
        env=env,
        input=_session_stdin(session_id, source),
        timeout=120,
        check=False,
    )


def _run_entry(
    case: SmokeCase, args: list[str], *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = _clean_smoke_env()
    env[case.root_var] = str(case.plugin_copy)
    env[case.data_var] = str(case.data_dir)
    env["XDG_RUNTIME_DIR"] = str(case.safe_runtime_dir)
    return subprocess.run(
        ["uv", "run", str(case.entry), *args],
        capture_output=True,
        text=True,
        env=env,
        input=stdin,
        timeout=120,
        check=False,
    )


def _run_uvx(
    case: SmokeCase, *draft_args: str, offline: bool = False, online_retries: int = 0
) -> subprocess.CompletedProcess[str]:
    env = _clean_smoke_env()
    extra: list[str] = []
    if offline:
        extra.append("--offline")
    cmd = [
        "uvx",
        "--isolated",
        "--cache-dir",
        str(case.cache_dir),
        *extra,
        "--from",
        str(case.runtime_root),
        "draft",
        *draft_args,
    ]
    # 联网冷缓存解析可能因瞬时网络抖动失败；仅对联网调用重试以吸收抖动，不掩盖持续失败
    # （重试后仍失败则原样返回，断言照常失败）。离线阶段不重试：必须使用联网阶段预热好的
    # 缓存，重试会掩盖真实的缓存未命中失败。
    attempts = 1 + (online_retries if not offline else 0)
    result = subprocess.run(
        cmd,
        cwd=str(case.parent),
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=UVX_TIMEOUT,
    )
    for _ in range(attempts - 1):
        if result.returncode == 0:
            break
        time.sleep(1)
        result = subprocess.run(
            cmd,
            cwd=str(case.parent),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=UVX_TIMEOUT,
        )
    return result


def _parse_unbound_envelope(stdout: str) -> dict[str, str]:
    """解析未绑定信封，返回关键字段。"""
    lines = stdout.strip().splitlines()
    assert lines, f"empty stdout: {stdout!r}"
    assert lines[0] == "Draft 会话未绑定", stdout
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("- "):
            fields[line[2:].split("：", 1)[0]] = line.split("：", 1)[1]
    return fields


def _parse_rebind_envelope(stdout: str) -> dict[str, str]:
    lines = stdout.strip().splitlines()
    assert lines, f"empty stdout: {stdout!r}"
    assert lines[0] == "Draft 会话已绑定", stdout
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.startswith("- "):
            body = line[2:]
            if "：" in body:
                key, _, value = body.partition("：")
                fields[key] = value
            else:
                # "- 继续受管理讨论前..." 等无冒号行
                fields[body] = body
    return fields


@pytest.mark.parametrize("host", HOSTS)
def test_smoke_full_distribution_no_repo_parent(tmp_path: Path, host: str) -> None:
    case = _make_smoke_case(tmp_path, host)

    # 1. 调用 hook session-start HOST；应输出未绑定信封，退出 0
    result = _run_hook(case)
    assert result.returncode == 0, result.stderr
    assert "Draft 会话未绑定" in result.stdout
    assert "smoke-session" in result.stdout

    # 2. 解析未绑定信封：entry 和命令格式只指向复制后的目录
    envelope = _parse_unbound_envelope(result.stdout)
    entry_str = envelope["插件入口"]
    assert entry_str == str(case.entry), envelope
    # 绑定/解除格式中的入口路径也指向复制后
    assert str(case.entry) in envelope["绑定格式"]
    assert str(case.entry) in envelope["解除格式"]
    # 不应指向仓库中的入口
    assert str(PLUGIN_ROOT) not in result.stdout
    assert str(REPO_ROOT) not in result.stdout

    # 3. bind 一个普通存在目录（draft_root）；重跑 Hook；断言已绑定信封
    bind_result = _run_entry(case, ["bind", str(case.draft_root), "--session", "smoke-session"])
    assert bind_result.returncode == 0, bind_result.stderr
    assert "绑定成功" in bind_result.stdout

    rebind_result = _run_hook(case, session_id="smoke-session", source="resume")
    assert rebind_result.returncode == 0, rebind_result.stderr
    assert "Draft 会话已绑定" in rebind_result.stdout
    rebind_fields = _parse_rebind_envelope(rebind_result.stdout)
    # root 指向复制后的 draft_root
    assert rebind_fields["草稿根"] == str(case.draft_root), rebind_fields
    # 当前 skill 指向复制后的 skill
    assert rebind_fields["当前 skill"] == str(case.skill_md), rebind_fields
    # 插件入口指向复制后的入口
    assert rebind_fields["插件入口"] == str(case.entry), rebind_fields
    # 核心检查命令指向复制后的 runtime
    expected_runtime = case.skill_root / "scripts" / "runtime"
    assert str(expected_runtime) in rebind_result.stdout, rebind_result.stdout
    assert "uvx --isolated --from" in rebind_result.stdout
    assert "draft check" in rebind_result.stdout
    # 不应指向仓库
    assert str(PLUGIN_ROOT) not in rebind_result.stdout
    assert str(REPO_ROOT) not in rebind_result.stdout

    # 4. unbind 后下一次 Hook 回到未绑定
    unbind_result = _run_entry(case, ["unbind", "--session", "smoke-session"])
    assert unbind_result.returncode == 0, unbind_result.stderr
    assert "解除成功" in unbind_result.stdout
    next_hook = _run_hook(case, session_id="smoke-session", source="clear")
    assert next_hook.returncode == 0, next_hook.stderr
    assert "Draft 会话未绑定" in next_hook.stdout

    # 5. 用测试专用缓存通过 uvx --isolated --from 跑 version/init/check
    #    联网首次解析（空缓存 + 网络）
    for args in (("--version",), ("init", str(case.draft_root)), ("check", str(case.draft_root))):
        uvx_result = _run_uvx(case, *args, offline=False, online_retries=1)
        assert uvx_result.returncode == 0, uvx_result.stderr + uvx_result.stdout
        if args == ("--version",):
            assert uvx_result.stdout.strip() == "0.3.0", uvx_result.stdout

    # 6. 状态隔离断言
    # bindings/logs 只在所选宿主 data_dir
    bindings_dir = case.data_dir / "bindings"
    logs_dir = case.data_dir / "logs"
    assert bindings_dir.exists()
    # 至少存在过 binding 文件（unbind 后可能仍存在空目录）
    assert logs_dir.exists()
    hook_log = logs_dir / "hook.jsonl"
    assert hook_log.exists()

    # bridge 只在该用例的安全运行目录
    bridge_base = case.safe_runtime_dir / "draft" / "bridges"
    assert bridge_base.exists()
    # bridge 文件中引用的 data_dir 指向复制后的 data_dir
    bridge_jsons = list(bridge_base.glob("*.json"))
    assert bridge_jsons, f"no bridge json in {bridge_base}"
    for bridge_file in bridge_jsons:
        payload = json.loads(bridge_file.read_text(encoding="utf-8"))
        assert payload["data_dir"] == str(case.data_dir), payload

    # Draft 业务文件只在所选 root
    assert (case.draft_root / "index.md").exists()
    assert (case.draft_root / "conclusions.md").exists()
    assert (case.draft_root / ".draft").exists()

    # uv 缓存中没有 bindings/、hook.jsonl、.draft/
    cache_bindings = list(case.cache_dir.rglob("bindings"))
    cache_logs = list(case.cache_dir.rglob("hook.jsonl"))
    cache_draft = list(case.cache_dir.rglob(".draft"))
    assert not cache_bindings, f"bindings in cache: {cache_bindings}"
    assert not cache_logs, f"hook.jsonl in cache: {cache_logs}"
    assert not cache_draft, f".draft in cache: {cache_draft}"

    # 输出/状态中无路径解析回仓库检出（stdout + stderr）
    for output in (
        result.stdout,
        result.stderr,
        rebind_result.stdout,
        rebind_result.stderr,
        next_hook.stdout,
        next_hook.stderr,
    ):
        assert str(REPO_ROOT) not in output, f"repo path leaked: {output}"
        assert str(PLUGIN_ROOT) not in output, f"plugin path leaked: {output}"


@pytest.mark.parametrize("host", HOSTS)
def test_smoke_offline_reuses_cache_after_online_run(tmp_path: Path, host: str) -> None:
    """空缓存联网跑一次核心 smoke，再用同一缓存 + --offline 重跑。"""
    case = _make_smoke_case(tmp_path, host)
    offline_root = (case.parent / "offline-root").resolve()
    offline_root.mkdir()

    # 联网首次解析（空缓存）
    online_version = _run_uvx(case, "--version", offline=False)
    assert online_version.returncode == 0, online_version.stderr
    assert online_version.stdout.strip() == "0.3.0"

    # 同一缓存 + --offline 重跑核心 smoke；联网与离线都不泄漏仓库路径
    for output in (online_version.stdout, online_version.stderr):
        assert str(REPO_ROOT) not in output
        assert str(PLUGIN_ROOT) not in output
    for args in (("--version",), ("init", str(offline_root)), ("check", str(offline_root))):
        offline_result = _run_uvx(case, *args, offline=True)
        assert offline_result.returncode == 0, (
            f"offline {args} failed: {offline_result.stderr}{offline_result.stdout}"
        )
        if args == ("--version",):
            assert offline_result.stdout.strip() == "0.3.0"
        for output in (offline_result.stdout, offline_result.stderr):
            assert str(REPO_ROOT) not in output, f"repo path leaked: {output}"
            assert str(PLUGIN_ROOT) not in output, f"plugin path leaked: {output}"
