# Draft 单文件 Python 启动入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 skill 内唯一的 `scripts/draft.py` 取代 POSIX、PowerShell 和 runtime `entry.py` 三层入口，使 Agent 与两个宿主在 Windows 和 POSIX 上使用同一条 `uv run` 协议，并按 Draft 版本在 `{data_dir}/envs/` 中运行非可编辑安装的 runtime。

**Architecture:** `scripts/draft.py` 是只依赖标准库的部署边界，从 skill 协议规定的自身位置推导布局，在 runtime 安装前发布或读取两字段 bridge，并为内层 `uv run --project` 选择版本环境。已安装的 `draft` 包继续拥有领域 CLI，并接收内部 `_session-start` 分派；每次普通命令和 SessionStart 都由 uv 按锁文件精确同步目标环境。

**Tech Stack:** Python `>=3.12,<3.15`、PEP 723、uv、pytest、pyright strict、ruff、JSON 插件 hook 清单。

## Global Constraints

- 规范设计为 `docs/superpowers/specs/2026-08-04-draft-single-python-launcher-design.md`；冲突时以该规格为准。
- 唯一公开命令形态是 `uv run <skill_root>/scripts/draft.py <arguments>`；不得要求 Agent 传 `skill_root` 参数或宿主数据目录。
- 公开命令不得包含 `--python`、`--project`、`--no-project`、`.env`、shell 包装器或 PowerShell 包装器。
- `draft.py` 只依赖标准库，不在引导阶段导入 `draft` 包或第三方模块。
- `skill_root` 只能从规范 `draft.py` 的父级 `scripts/` 结构推导；宿主根只用于 hook 一致性校验。
- 环境路径固定为 `{data_dir}/envs/<draft_version>`；`environment_key` 只等于 runtime `project.version`。
- 同一版本共享环境，不同版本隔离；不增加源码、清单、入口或平台摘要。本地同版本并行开发副本不在保证范围内。
- runtime 和锁定运行依赖都安装进目标环境；使用 `--no-editable`，不得通过 `PYTHONPATH` 执行源码树。
- 每次 SessionStart 无条件同步目标环境，再解析 hook 载荷和绑定。
- bridge JSON 继续严格只有 `schema_version` 与 `data_dir`；不存在、无效、冲突不得折叠。
- 保留任意字符串 `session_id`，包括空字符串；不得改变绑定、草稿格式、结构操作或恢复协议。
- 保留用户当前对 `runtime/pyproject.toml` 和 `uv.lock` 的 Python 范围改动，并在其基础上完成 package 安装配置与锁文件更新。
- 当前 `0.1.0` 是预发布开发版本，本计划不为本地迁移强制改号；发布不同内容前必须由发布流程提升并统一 runtime 与两个 plugin manifest 的版本。
- 不创建 `.env`、环境 ready 文件、Draft 自有环境锁、运行时清单或生成式启动器。
- 不修改 `docs/discussion/**`，不创建 Git 提交。

---

## File Structure

- Create: `plugins/draft/skills/draft/scripts/draft.py` — PEP 723 公共入口、布局/版本解析、bridge、环境选择和内层 uv 分派。
- Delete: `plugins/draft/skills/draft/scripts/draft` — 被 Python 入口取代的 POSIX 包装器。
- Delete: `plugins/draft/skills/draft/scripts/draft.ps1` — 被 Python 入口取代的 PowerShell 包装器。
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/entry.py` — 部署逻辑迁出已安装 runtime。
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/plugin_context.py` — bridge 逻辑迁入首次启动可执行的单文件入口。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py` — 在公开 argparse 解析前分派内部 `_session-start`。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/session_hook.py` — 从 stdin 和注入环境处理宿主无关 SessionStart。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/__init__.py` — 从安装元数据读取 runtime 版本，删除重复版本常量。
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml` — 强制 package 安装并把 launcher 纳入静态检查。
- Modify: `plugins/draft/skills/draft/scripts/runtime/uv.lock` — 根据最终 pyproject 重新锁定可安装的本地项目。
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py` — 单文件入口、版本环境键、bridge 与子进程环境的定向测试。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py` — 每次 hook 同步后的内部分派状态矩阵。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py` — 复制 skill 后只通过 `uv run scripts/draft.py` 的子进程烟雾测试。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py` — 安装产物、hook 命令和版本一致性门禁。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py` — 安装元数据版本来源和 console script 门禁。
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py` — 由 `test_launcher.py` 取代。
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py` — 用例迁入 `test_launcher.py`。
- Modify: `plugins/draft/hooks/codex-hooks.json` — 直接 `uv run .../draft.py _session-start codex`。
- Modify: `plugins/draft/hooks/claude-hooks.json` — 直接 `uv run .../draft.py _session-start claude`。
- Modify after code switch: `plugins/draft/skills/draft/SKILL.md`, `plugins/draft/skills/draft/references/platform-setup.md`, `docs/design.md` — 与实际入口和环境生命周期同步。

---

### Task 1: 建立单文件入口的布局、版本和 runtime 执行边界

**Files:**

- Create: `plugins/draft/skills/draft/scripts/draft.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Modify: `plugins/draft/skills/draft/scripts/runtime/uv.lock`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/__init__.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py`

**Interfaces:**

- Consumes: skill 协议布局 `SKILL.md + scripts/draft.py + scripts/runtime/{pyproject.toml,uv.lock}`；runtime `project.version` 与 `requires-python`。
- Produces: `RuntimeLayout`, `LauncherError`, `resolve_layout()`, `runtime_version()`, `environment_key()`, `environment_path()`, `runtime_arguments()`, `runtime_environment()` 和 `run_command()`；后续任务只通过这些接口扩展 bridge 与 hook。

- [x] **Step 1: 先写布局和版本环境键的失败测试**

在 `test_launcher.py` 用独立模块名加载脚本，避免文件名 `draft.py` 与已安装包冲突：

```python
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]
LAUNCHER_PATH = SKILL_ROOT / "scripts/draft.py"


def load_launcher(path: Path = LAUNCHER_PATH) -> ModuleType:
    spec = importlib.util.spec_from_file_location("draft_test_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_layout(tmp_path: Path, version: str = "1.2.3") -> Path:
    skill = tmp_path / "skill"
    scripts = skill / "scripts"
    runtime = scripts / "runtime"
    runtime.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: draft\n---\n", encoding="utf-8")
    (runtime / "pyproject.toml").write_text(
        f'[project]\nname = "draft-runtime"\nversion = "{version}"\n'
        'requires-python = ">=3.12,<3.15"\n',
        encoding="utf-8",
    )
    (runtime / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    entry = scripts / "draft.py"
    entry.write_text(LAUNCHER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return entry


def test_layout_is_derived_from_launcher_protocol_position(tmp_path: Path) -> None:
    launcher = load_launcher()
    entry = make_layout(tmp_path)
    layout = launcher.resolve_layout(entry)
    assert layout.entry_path == entry.resolve()
    assert layout.skill_root == entry.parent.parent.resolve()
    assert layout.skill_path == layout.skill_root / "SKILL.md"
    assert layout.project_path == layout.skill_root / "scripts/runtime"


def test_environment_key_is_exact_runtime_version(tmp_path: Path) -> None:
    launcher = load_launcher()
    first = launcher.resolve_layout(make_layout(tmp_path / "first", "1.2.3"))
    second = launcher.resolve_layout(make_layout(tmp_path / "second", "1.2.3"))
    changed = launcher.resolve_layout(make_layout(tmp_path / "changed", "1.2.4"))
    assert launcher.environment_key(first) == launcher.environment_key(second) == "1.2.3"
    assert launcher.environment_key(changed) == "1.2.4"


@pytest.mark.parametrize("version", ["", ".", "..", "../x", r"x\\y", "x\x00y"])
def test_unsafe_runtime_version_is_rejected(tmp_path: Path, version: str) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path, version))
    with pytest.raises(launcher.LauncherError, match="runtime.version_invalid"):
        launcher.environment_key(layout)
```

再增加布局缺少 `SKILL.md`、`runtime`、`pyproject.toml`、`uv.lock`，以及这些位置被目录或
符号链接占据的参数化测试。每种状态必须是 `entry.layout_invalid`，不能向上搜索替代文件。

- [x] **Step 2: 运行测试并确认正确红灯**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py -q
```

Expected: FAIL because `scripts/draft.py` and all launcher interfaces are absent; no collection or fixture error is acceptable.

- [x] **Step 3: 写 PEP 723 头和最小布局/版本实现**

创建 `scripts/draft.py`，头部和核心接口固定为：

```python
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

EXIT_RUNTIME = 4


class LauncherError(Exception):
    def __init__(
        self,
        code: str,
        *paths: Path,
        details: Sequence[str] = (),
        exit_code: int = EXIT_RUNTIME,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.paths = paths
        self.details = tuple(details)
        self.exit_code = exit_code


@dataclass(frozen=True)
class RuntimeLayout:
    entry_path: Path
    skill_root: Path
    skill_path: Path
    project_path: Path
    pyproject_path: Path
    lock_path: Path


def resolve_layout(entry_path: Path | None = None) -> RuntimeLayout:
    entry = Path(__file__) if entry_path is None else entry_path
    try:
        canonical = entry.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("entry.layout_invalid", entry) from exc
    scripts = canonical.parent
    skill_root = scripts.parent
    layout = RuntimeLayout(
        entry_path=canonical,
        skill_root=skill_root,
        skill_path=skill_root / "SKILL.md",
        project_path=scripts / "runtime",
        pyproject_path=scripts / "runtime/pyproject.toml",
        lock_path=scripts / "runtime/uv.lock",
    )
    if scripts.name != "scripts":
        raise LauncherError("entry.layout_invalid", canonical)
    _require_regular(layout.skill_path)
    _require_directory(layout.project_path)
    _require_regular(layout.pyproject_path)
    _require_regular(layout.lock_path)
    return layout


def runtime_version(layout: RuntimeLayout) -> str:
    try:
        raw = tomllib.loads(layout.pyproject_path.read_text(encoding="utf-8"))
        value = raw["project"]["version"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise LauncherError("runtime.version_invalid", layout.pyproject_path) from exc
    if not isinstance(value, str):
        raise LauncherError("runtime.version_invalid", layout.pyproject_path)
    return value


def environment_key(layout: RuntimeLayout) -> str:
    value = runtime_version(layout)
    if not value or value in {".", ".."} or any(x in value for x in ("/", "\\", "\x00")):
        raise LauncherError("runtime.version_invalid", layout.pyproject_path)
    return value


def environment_path(data_dir: Path, layout: RuntimeLayout) -> Path:
    return data_dir / "envs" / environment_key(layout)
```

`_require_regular()` 使用 `lstat()` 拒绝符号链接和非普通文件；`_require_directory()` 同样拒绝
符号链接和非目录。二者只把布局异常转换为 `entry.layout_invalid`。

- [x] **Step 4: 为内层 uv 命令和环境清洗增加失败测试**

```python
def test_runtime_command_installs_project_non_editably(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    assert launcher.runtime_arguments(layout, ["check", "root"]) == [
        "uv", "run", "--project", str(layout.project_path), "--locked", "--exact",
        "--no-dev", "--no-editable", "--no-env-file", "draft", "check", "root",
    ]


def test_runtime_environment_overrides_all_authoritative_selectors(tmp_path: Path) -> None:
    launcher = load_launcher()
    layout = launcher.resolve_layout(make_layout(tmp_path))
    data_dir = tmp_path / "data"
    inherited = {
        "PATH": "bin",
        "UV_PROJECT_ENVIRONMENT": "wrong",
        "UV_NO_SYNC": "1",
        "UV_NO_INSTALL_PROJECT": "1",
        "DRAFT_DATA": "wrong",
        "DRAFT_SKILL_PATH": "wrong",
        "PYTHONPATH": "wrong",
        "VIRTUAL_ENV": "wrong",
    }
    env = launcher.runtime_environment(layout, data_dir, True, inherited)
    assert env["UV_PROJECT_ENVIRONMENT"] == str(data_dir / "envs/1.2.3")
    assert env["DRAFT_DATA"] == str(data_dir)
    assert env["DRAFT_SKILL_PATH"] == str(layout.skill_path)
    for name in ("UV_NO_SYNC", "UV_NO_INSTALL_PROJECT", "PYTHONPATH", "VIRTUAL_ENV"):
        assert name not in env
```

再覆盖独立模式移除 `DRAFT_DATA`、`setup` 映射到 `draft --version`、子进程退出码原样传播和
`FileNotFoundError` 转换为 `runtime.uv_unavailable`。

- [x] **Step 5: 实现内层 uv 参数、环境和 runner**

```python
def runtime_arguments(layout: RuntimeLayout, arguments: Sequence[str]) -> list[str]:
    command = ["--version"] if list(arguments) == ["setup"] else list(arguments)
    return [
        "uv", "run", "--project", str(layout.project_path), "--locked", "--exact",
        "--no-dev", "--no-editable", "--no-env-file", "draft", *command,
    ]


def runtime_environment(
    layout: RuntimeLayout,
    data_dir: Path,
    plugin_mode: bool,
    environ: Mapping[str, str],
) -> dict[str, str]:
    env = dict(environ)
    for name in (
        "DRAFT_DATA", "DRAFT_SKILL_PATH", "PYTHONPATH", "VIRTUAL_ENV",
        "UV_NO_SYNC", "UV_NO_INSTALL_PROJECT",
    ):
        env.pop(name, None)
    env["UV_PROJECT_ENVIRONMENT"] = str(environment_path(data_dir, layout))
    env["DRAFT_SKILL_PATH"] = str(layout.skill_path)
    if plugin_mode:
        env["DRAFT_DATA"] = str(data_dir)
    return env


def run_command(arguments: Sequence[str], env: Mapping[str, str]) -> int:
    try:
        return subprocess.run(list(arguments), env=dict(env), check=False).returncode
    except FileNotFoundError as exc:
        raise LauncherError("runtime.uv_unavailable") from exc
```

在调用 uv 前对 `environment_path` 执行既有安全目录验证：不存在允许 uv 创建；存在的符号
链接或非目录报告 `runtime.environment_invalid`。

- [x] **Step 6: 强制 runtime package 安装并统一版本来源**

在 `pyproject.toml` 保留用户已经改成的 `requires-python = ">=3.12,<3.15"`，增加：

```toml
[tool.uv]
package = true

[tool.pyright]
include = ["src", "tests", "../draft.py"]
```

把 `src/draft/__init__.py` 改为安装元数据来源：

```python
from importlib.metadata import version

__version__ = version("draft-runtime")
```

在 `test_identity.py` 断言 `[tool.uv].package is True`、console script 仍只有
`draft = "draft.cli:main"`，并断言 `__version__` 等于 `project.version`。重新生成锁文件：

```bash
uv lock --project plugins/draft/skills/draft/scripts/runtime
```

Expected: lock 保留 `requires-python = ">=3.12, <3.15"`，本地 `draft-runtime` 是可安装项目，
不再是阻止项目安装的 virtual root。

- [x] **Step 7: 运行 Task 1 定向门禁**

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py -q
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pyright plugins/draft/skills/draft/scripts/draft.py \
          plugins/draft/skills/draft/scripts/runtime/src \
          plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py
```

Expected: PASS; `uv run plugins/draft/skills/draft/scripts/draft.py` 能由 PEP 723 自行选择兼容
Python，命令中没有 `--python`。

---

### Task 2: 把严格 bridge 和普通模式选择迁入 `draft.py`

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/draft.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py`
- Modify temporarily: `plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py`

**Interfaces:**

- Consumes: Task 1 的 `RuntimeLayout`, `LauncherError`, `environment_path()`, `runtime_arguments()`, `runtime_environment()`。
- Produces: `PluginContext`, `BridgePaths`, `per_user_runtime_dir()`, `bridge_paths()`, `publish_context()`, `load_context()`, `reset_context()` 和普通 `main()`；Task 3 使用同一发布接口处理 hook。

- [x] **Step 1: 把现有 bridge 状态矩阵改为加载 launcher 并确认红灯**

把 `test_plugin_context.py` 的生产导入替换为 Task 1 的 `load_launcher()`，保留并重新指向以下
所有行为：

- 两字段发布与读取；
- bridge 不存在返回 `None`；
- 未知字段、schema、相对/不存在/不可写 `data_dir` 和损坏 JSON；
- bridge 目录、JSON、冲突哨兵、锁的符号链接、非普通文件、所有者与权限拒绝；
- 相同数据目录幂等发布；
- 不同数据目录形成粘滞冲突；
- 线程和进程竞争；
- reset 只删除当前安装键 JSON 与哨兵，不删除锁。

安装键预期必须独立计算：

```python
expected = hashlib.sha256(str(entry.resolve()).encode("utf-8")).hexdigest()
assert launcher.installation_key(entry) == expected
assert launcher.bridge_paths(entry, runtime_dir).json.name == f"{expected}.json"
```

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py -q
```

Expected: FAIL because bridge functions do not yet exist in `draft.py`.

- [x] **Step 2: 迁移 bridge 实现，不改变其安全协议**

把当前 `src/draft/plugin_context.py` 的标准库实现迁入 `scripts/draft.py`，只做以下命名和错误
边界变化：

```python
@dataclass(frozen=True)
class PluginContext:
    data_dir: Path


@dataclass(frozen=True)
class BridgePaths:
    json: Path
    conflict: Path
    lock: Path


def installation_key(entry_path: Path) -> str: ...
def per_user_runtime_dir(environ: Mapping[str, str] | None = None) -> Path: ...
def bridge_paths(entry_path: Path, runtime_dir: Path | None = None) -> BridgePaths: ...
def publish_context(entry_path: Path, data_dir: Path, runtime_dir: Path | None = None) -> PluginContext: ...
def load_context(entry_path: Path, runtime_dir: Path | None = None) -> PluginContext | None: ...
def reset_context(entry_path: Path, runtime_dir: Path | None = None) -> None: ...
```

所有 `DraftError` 改成相同 code/path/details 的 `LauncherError`。保留 `lstat` 不跟随、POSIX
所有者/权限、Windows/POSIX 文件锁、`0600`、临时文件 + `fsync` + `os.replace`、冲突哨兵
和严格两字段解析，不借迁移放宽任何分支。

- [x] **Step 3: 为普通 `main()` 的模式选择写失败测试**

```python
def test_normal_main_uses_published_plugin_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = load_launcher()
    entry = make_layout(tmp_path / "layout")
    layout = launcher.resolve_layout(entry)
    data = tmp_path / "plugin-data"
    data.mkdir()
    runtime_dir = tmp_path / "runtime-dir"
    runtime_dir.mkdir(mode=0o700)
    launcher.publish_context(entry, data, runtime_dir)
    calls: list[tuple[list[str], dict[str, str]]] = []
    code = launcher.main(
        ["check", "root"],
        entry_path=entry,
        environ={"PATH": "bin"},
        runtime_dir=runtime_dir,
        runner=lambda args, env: calls.append((list(args), dict(env))) or 0,
    )
    assert code == 0
    assert calls[0][1]["UV_PROJECT_ENVIRONMENT"] == str(data.resolve() / "envs/1.2.3")
    assert calls[0][1]["DRAFT_DATA"] == str(data.resolve())
```

再覆盖：bridge 不存在选择 `<per-user-runtime-dir>/draft/envs/<version>` 且不传
`DRAFT_DATA`；无效/冲突 bridge 不调用 runner；`plugin-context reset` 在 load 前执行；runner
退出 `2/3/4/5` 全部原样传播；LauncherError 统一输出 code/path/details 并退出 `4`。

- [x] **Step 4: 实现普通 `main()` 和独立 `data_dir`**

接口保持可注入，便于不复制实现计算路径的测试：

```python
def standalone_data_dir(runtime_dir: Path | None = None) -> Path:
    root = per_user_runtime_dir() if runtime_dir is None else runtime_dir
    return root / "draft"


def main(
    argv: Sequence[str] | None = None,
    *,
    entry_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    runtime_dir: Path | None = None,
    runner: Runner = run_command,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    try:
        layout = resolve_layout(entry_path)
        if arguments == ["plugin-context", "reset"]:
            reset_context(layout.entry_path, runtime_dir)
            return 0
        context = load_context(layout.entry_path, runtime_dir)
        data_dir = standalone_data_dir(runtime_dir) if context is None else context.data_dir
        plugin_mode = context is not None
        _validate_environment_target(environment_path(data_dir, layout))
        return runner(
            runtime_arguments(layout, arguments),
            runtime_environment(layout, data_dir, plugin_mode, env),
        )
    except LauncherError as error:
        _report(error)
        return error.exit_code
```

文件末尾只保留 `raise SystemExit(main())`，不提供第二个公开函数入口。

- [x] **Step 5: 运行 bridge 与 launcher 定向门禁**

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py -q
```

Expected: PASS, including POSIX/Windows-specific lock tests only on supported platform; no `.env` or
环境状态文件 is created.

---

### Task 3: 让每次 SessionStart 先同步，再由已安装 runtime 处理载荷

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/draft.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/session_hook.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py`

**Interfaces:**

- Consumes: Task 2 的 `publish_context()` 与普通 runtime runner；既有 `bindings`、日志和重新绑定渲染。
- Produces: launcher `_session-start HOST` 内部分派；runtime `session_hook.main(argv, environ, stdin)`；公开 CLI 仍不展示内部命令。

- [x] **Step 1: 重写 SessionStart 测试期望并确认红灯**

测试矩阵继续参数化 `codex` / `claude` 及各自 source，但把旧的“无绑定不启动 uv”期望改成：

```python
assert runner.calls[0].args[:3] == ["uv", "run", "--project"]
assert runner.calls[0].args[-3:] == ["draft", "_session-start", host]
assert runner.calls[0].env["UV_PROJECT_ENVIRONMENT"] == str(
    setup.data_dir.resolve() / "envs" / setup.runtime_version
)
assert runner.calls[0].env["DRAFT_DATA"] == str(setup.data_dir.resolve())
```

增加以下独立行为测试：

- bridge 在 stdin 缺少/非法 `session_id` 前发布；
- 无绑定、空字符串 session、绑定存在、根缺失、skill 摘要不匹配、绑定损坏都调用一次内层 uv；
- stdin 非 JSON、非对象、超过 64 KiB、错误 `hook_event_name` 只记录 ignored，不输出绑定；
- Codex 忽略 Claude 别名，Claude 忽略 Codex 变量；
- 宿主根与 `skill_root.parent.parent` 不一致时 uv 完全不启动；
- 内层同步失败保留 bridge，并传播 uv 退出码；
- stdin 字节内容不被 launcher 读取或改写，直接继承给子进程。

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py -q
```

Expected: FAIL because launcher has no host branch and runtime still依赖 entry 的预解析。

- [x] **Step 2: 在 launcher 中发布 hook bridge 并调用统一 runtime 命令**

```python
HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}


def _session_start(
    layout: RuntimeLayout,
    arguments: Sequence[str],
    environ: Mapping[str, str],
    runtime_dir: Path | None,
    runner: Runner,
) -> int:
    if len(arguments) != 2 or arguments[1] not in HOST_ENV:
        raise LauncherError("entry.invalid_arguments", details=arguments)
    host = arguments[1]
    root_name, data_name = HOST_ENV[host]
    raw_root, raw_data = environ.get(root_name), environ.get(data_name)
    missing = tuple(name for name, value in ((root_name, raw_root), (data_name, raw_data)) if not value)
    if missing:
        raise LauncherError("hook.environment_missing", details=missing)
    plugin_root = _canonical_plugin_root(Path(raw_root))
    expected_root = layout.skill_root.parent.parent
    if plugin_root != expected_root:
        raise LauncherError("hook.plugin_root_mismatch", plugin_root, expected_root)
    context = publish_context(layout.entry_path, Path(raw_data), runtime_dir)
    target = environment_path(context.data_dir, layout)
    _validate_environment_target(target)
    return runner(
        runtime_arguments(layout, arguments),
        runtime_environment(layout, context.data_dir, True, environ),
    )
```

`main()` 必须在 `plugin-context reset` 之后、普通 `load_context()` 之前识别 `_session-start`。

- [x] **Step 3: 把 stdin 解析和绑定分支完整移入 `session_hook.py`**

删除模块注释中“引导解释器直接导入”的旧边界，增加：

```python
MAX_STDIN = 64 * 1024


def read_hook_payload(stream: TextIO) -> tuple[dict[str, object] | None, str]:
    text = stream.read(MAX_STDIN + 1)
    if len(text) > MAX_STDIN:
        return None, "hook.stdin_oversized"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None, "hook.stdin_invalid"
    if not isinstance(value, dict):
        return None, "hook.stdin_not_object"
    payload = cast("dict[str, object]", value)
    if payload.get("hook_event_name") != "SessionStart":
        return None, "hook.unexpected_event"
    return payload, ""
```

`main()` 的准确契约：

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
) -> int:
```

它只接受一个 `host`，从 `DRAFT_DATA` 取得已经验证的插件目录；payload 无效时记录 ignored 并
返回 `0`；`session_id` 不是字符串时记录 `binding.session_unavailable` 并返回 `0`；任意字符串
包括 `""` 进入既有 `run_binding_branch()`。不得重新读取宿主 root/data 变量。

- [x] **Step 4: 在 `draft.cli.main()` 的 argparse 之前分派内部 hook**

```python
def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["_session-start"]:
        from draft import session_hook

        return session_hook.main(arguments[1:])
    parser = build_parser()
    args = parser.parse_args(arguments)
    ...
```

增加 `test_cli.py` 测试，证明 `draft --help` 不展示 `_session-start`，而内部调用原样传给
`session_hook.main`。不要把 hook 加进公开 argparse 子命令集合。

- [x] **Step 5: 更新会话可见文本中的旧包装器名称**

`render_session_fact()` 不拼装绝对 skill 路径，只提示按当前 skill 的唯一入口使用原始 ID：

```python
return f"Draft 宿主 session_id：{session_id}\n在 Draft bind/unbind 命令中原样使用此值。"
```

这避免模型继续生成已删除的 `scripts/draft`，也不把数据目录或动态命令注入上下文。

- [x] **Step 6: 运行 SessionStart 与 CLI 定向门禁**

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py -q
```

Expected: PASS; every SessionStart branch records exactly one inner uv invocation, and `session_id=""`
remains legal.

---

### Task 4: 切换双宿主 hook 并删除所有旧入口

**Files:**

- Modify: `plugins/draft/hooks/codex-hooks.json`
- Modify: `plugins/draft/hooks/claude-hooks.json`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py`
- Rewrite: `plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py`
- Delete: `plugins/draft/skills/draft/scripts/draft`
- Delete: `plugins/draft/skills/draft/scripts/draft.ps1`
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/entry.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/plugin_context.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py`
- Delete after migration: `plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py`

**Interfaces:**

- Consumes: Task 2/3 已覆盖的单文件 launcher、bridge 与内部 `_session-start`。
- Produces: 安装产物只剩一个跨平台公共入口；两个 hook JSON 具有相同命令形状。

- [x] **Step 1: 先把分发测试改成目标结构并确认红灯**

```python
def test_skill_has_one_python_launcher() -> None:
    scripts = SKILL_ROOT / "scripts"
    assert (scripts / "draft.py").is_file()
    assert not (scripts / "draft").exists()
    assert not (scripts / "draft.ps1").exists()
    assert not (scripts / "runtime/src/draft/entry.py").exists()
    assert not (scripts / "runtime/src/draft/plugin_context.py").exists()


def test_hook_commands_run_the_same_python_launcher() -> None:
    codex_command, claude_command = hook_commands()
    assert codex_command == (
        'uv run "${PLUGIN_ROOT}/skills/draft/scripts/draft.py" _session-start codex'
    )
    assert claude_command == (
        'uv run "${CLAUDE_PLUGIN_ROOT}/skills/draft/scripts/draft.py" _session-start claude'
    )
    for command in (codex_command, claude_command):
        assert "--python" not in command
        assert "--project" not in command
        assert "--no-project" not in command
        assert ".ps1" not in command
```

版本门禁从 runtime pyproject 独立读取期望值：

```python
runtime_version = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text())["project"]["version"]
assert codex_manifest["version"] == claude_manifest["version"] == runtime_version
```

Run and expect FAIL on existing wrappers and hook strings.

- [x] **Step 2: 精确替换两个 hook command**

Codex：

```json
"command": "uv run \"${PLUGIN_ROOT}/skills/draft/scripts/draft.py\" _session-start codex"
```

Claude Code：

```json
"command": "uv run \"${CLAUDE_PLUGIN_ROOT}/skills/draft/scripts/draft.py\" _session-start claude"
```

保持 matcher、事件和其他 manifest 字段不变。宿主根只用于定位文件和入口内一致性校验；hook
command 不显式展开数据变量。

- [x] **Step 3: 重写复制产物烟雾测试，不依赖 `/bin/sh` 或 PowerShell**

`test_bootstrap.py` 的所有子进程调用改成：

```python
subprocess.run(
    ["uv", "run", str(artifact / "scripts/draft.py"), *args],
    cwd=case_dir,
    env=env,
    text=True,
    capture_output=True,
    check=False,
)
```

fake uv 必须区分外层 `uv run <script.py>` 与内层
`uv run --project ... --no-editable ... draft ...`；外层用当前测试 Python 执行复制后的脚本，
内层记录 `UV_PROJECT_ENVIRONMENT` 并调用测试替身。断言：

- `setup`, `init`, `check`, `move`, `check` 依次成功；
- 所有环境状态只在隔离的临时 `data_dir/envs/0.1.0`；
- 内层命令包含 `--locked --exact --no-dev --no-editable --no-env-file`；
- 内层环境没有 `PYTHONPATH`、旧 `DRAFT_DATA`、旧 `VIRTUAL_ENV`；
- skill 树和用户工作目录中没有 `.env`；
- 子进程退出码完整传播。

- [x] **Step 4: 删除旧实现和已迁移测试**

删除文件列表中四个生产入口/模块和三个旧测试文件。先用 `rg` 证明没有剩余生产导入：

```bash
rg -n "draft\.entry|draft\.plugin_context|scripts/draft\.ps1|scripts/draft(\s|`|$)" \
  plugins/draft --glob '!skills/draft/SKILL.md' --glob '!skills/draft/references/platform-setup.md'
```

Expected before docs task: only current operational docs may still mention old public command; Python 与 hook
代码不能有命中。

- [x] **Step 5: 运行产物、launcher、hook 和复制烟雾测试**

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py -q
```

Expected: PASS on POSIX without invoking `/bin/sh`; the same hook command shape is syntactically usable on
Windows because it only invokes the `uv` executable with a `.py` argument.

---

### Task 5: 原子切换 skill、平台说明和正式设计

**Files:**

- Modify: `plugins/draft/skills/draft/SKILL.md`
- Modify: `plugins/draft/skills/draft/references/platform-setup.md`
- Modify: `docs/design.md`
- Verify: `docs/superpowers/specs/2026-08-04-draft-single-python-launcher-design.md`
- Verify: `docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md`
- Verify: `docs/superpowers/plans/2026-07-31-draft-self-contained-skill-runtime.md`

**Interfaces:**

- Consumes: Task 4 已真实存在并通过测试的唯一命令。
- Produces: Agent 运行说明、用户故障恢复说明和主规范与代码一致；旧规格/计划保留明确历史指针。

- [x] **Step 1: 更新 `SKILL.md` 的唯一入口，不增加部署细节负担**

“唯一入口”只保留 Agent 必须知道的协议：

```text
始终通过 uv 运行当前 skill 自己的 `scripts/draft.py`。`<skill_root>` 使用宿主在加载本 skill
时注入的当前 skill 根，不搜索或猜测其他副本。

uv run <skill_root>/scripts/draft.py setup
uv run <skill_root>/scripts/draft.py init ROOT
uv run <skill_root>/scripts/draft.py check ROOT
uv run <skill_root>/scripts/draft.py bind ROOT --session SESSION_ID
uv run <skill_root>/scripts/draft.py unbind --session SESSION_ID
```

机械替换正文中的 `scripts/draft ...` 示例，但不重复解释 bridge、uv flags 或宿主环境变量；详细
部署知识继续只放 `references/platform-setup.md`。把硬停止条件的“Python 3.14”改成“uv 或
兼容 Python 无法由 uv 取得”。保持 frontmatter 和语义维护规则不变。

- [x] **Step 2: 重写平台参考的入口和环境生命周期**

平台参考必须准确说明：

- 前置条件只有 `uv` 在 `PATH`；Python 由 PEP 723/runtime `requires-python` 选择；
- 不传 `--python`，不要求系统级 Python，不使用 `.env`；
- `draft.py` 按自身 `scripts/` 位置推导 skill；
- 普通 Agent 无宿主数据变量，通过 bridge 取得增强模式；
- 环境键是 runtime 版本，路径为 `{data_dir}/envs/<draft_version>`；
- runtime 与依赖非可编辑安装，每次 `uv run` 按锁同步；
- SessionStart 每次先同步，包括无绑定或坏载荷；
- `plugin-context reset` 的新完整命令；
- 同版本重打包不受运行时摘要保护，发布前必须提升并同步版本；
- Windows 与 POSIX 使用同一命令，不再有 PowerShell 专用入口。

保留 bridge 安全、绑定、故障恢复和草稿生命周期中未变化的内容。

- [x] **Step 3: 把 `docs/design.md` 从迁移中状态更新为实际完成状态**

只有 Task 1–4 全绿后才：

- 把状态改成单文件入口已实现、容器门禁已验证，真实 Windows 宿主复验仍待执行；
- 安装树改成只有 `scripts/draft.py`；
- 架构图和 CLI 示例改成 `uv run <skill_root>/scripts/draft.py`；
- bridge key 改成 `realpath(scripts/draft.py)`；
- 环境章节改成版本键、安装 runtime、`--no-editable` 和每次 uv run；
- hook 章节改成直接 uv run `.py`；
- 验收项删除 POSIX/PowerShell wrapper 与清单摘要要求；
- 后续步骤把本计划标为当前迁移依据，但在真实 Windows 复验前不宣称 P1 已关闭。

不得改动 `docs/discussion/**` 的历史结论。

- [x] **Step 4: 运行 skill 基础验证和过时文本扫描**

```bash
uv run --no-project \
  /home/cld/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/draft/skills/draft
rg -n "scripts/draft\.ps1|Python 3\.14|--no-install-project|清单键|清单摘要|不把 Draft 项目安装|draft\.entry" \
  plugins/draft/skills/draft/SKILL.md \
  plugins/draft/skills/draft/references/platform-setup.md \
  docs/design.md
```

Expected: validator PASS; `rg` only matches explicitly labeled historical/被取代说明，不匹配当前
命令或当前环境契约。人工检查 `SKILL.md` 仍简洁，详细平台内容没有复制回 skill 主体。

---

### Task 6: 完整质量门禁与真实宿主复验边界

**Files:**

- Verify only: all files changed by Tasks 1–5

**Interfaces:**

- Consumes: 完整迁移产物。
- Produces: 容器内可重复验证证据；Windows/Codex/Claude 外部状态的明确待验证列表。

- [x] **Step 1: 运行完整 Python 测试**

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest -q
```

Expected: all tests PASS, no skipped test except当前操作系统确实不提供的专用锁/权限能力。

- [x] **Step 2: 运行完整静态和格式门禁**

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pyright plugins/draft/skills/draft/scripts/draft.py \
          plugins/draft/skills/draft/scripts/runtime/src \
          plugins/draft/skills/draft/scripts/runtime/tests
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  ruff check plugins/draft/skills/draft/scripts/draft.py \
             plugins/draft/skills/draft/scripts/runtime
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  ruff format --check plugins/draft/skills/draft/scripts/draft.py \
                      plugins/draft/skills/draft/scripts/runtime
```

Expected: all PASS with no ignored new diagnostics and no relaxed configuration.

- [x] **Step 3: 运行真实 uv 独立模式烟雾测试**

在测试临时目录复制整个 `skills/draft/`，清除所有宿主和 Draft 环境变量，调用：

```bash
uv run <copied-skill>/scripts/draft.py setup
uv run <copied-skill>/scripts/draft.py init <temporary-root>
uv run <copied-skill>/scripts/draft.py check <temporary-root>
```

Expected: all succeed; 唯一应用环境是安全临时 `data_dir/envs/0.1.0`; 环境中的
`draft-runtime` 安装元数据不含 `"editable": true`; 复制 skill 和工作目录没有 `.env`、`.venv`
或生成式入口。

- [x] **Step 4: 运行插件模式和并发定向验证**

使用隔离的 bridge runtime dir 与 plugin `data_dir`：

- 两个相同版本 launcher 会话并发运行 `setup`，最终只使用 `data_dir/envs/0.1.0`；
- 一个复制产物把 `project.version` 改成 `0.1.1` 后只使用 `data_dir/envs/0.1.1`；
- 同版本只改变依赖或源码仍选择同一个路径，测试明确记录这是发布策略边界；
- 父进程设置错误的 `UV_PROJECT_ENVIRONMENT`、`DRAFT_DATA`、`PYTHONPATH`、`VIRTUAL_ENV`、
  `UV_NO_SYNC` 和 `UV_NO_INSTALL_PROJECT` 时，权威目标和同步行为不变；
- bridge invalid/conflict 时任何 uv 子进程都不启动，reset 后下一次 SessionStart 可重新发布。

Expected: all assertions PASS; no environment is moved between standalone and plugin data dirs.

- [x] **Step 5: 运行文档、结构和工作树审计**

```bash
rg -n "scripts/draft\.ps1|draft\.entry|draft\.plugin_context|--python 3\." \
  plugins/draft docs/design.md \
  docs/superpowers/specs/2026-08-04-draft-single-python-launcher-design.md
git status --short
git diff --check
```

Expected: only historical文件中的明确说明允许旧标识；`git diff --check` PASS；原有用户改动
`AGENTS.md`、`CLAUDE.md` 和 Python 范围调整未被丢失或无关改写。

- [ ] **Step 6: 在真实 Windows 宿主完成 P1 关闭验证**

在具有 uv、Codex 和 Claude Code 的 Windows 环境分别验证：

1. 全新插件安装后的 `startup` 能运行 `uv run .../draft.py _session-start`；
2. 没有系统 Python 时，uv 按 PEP 723 下载兼容 Python；
3. `resume`、`clear`、`compact`，以及 Claude `fork` 都先同步同一版本环境；
4. 无绑定只注入 session fact，有绑定恢复正确 root，空字符串 session 保持合法；
5. 普通 Agent 工具进程确认没有 `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA`，仍能通过 bridge 使用同一
   `data_dir/envs/<version>`；
6. 路径包含空格时 hook 和普通命令都成功；
7. 不调用 `.sh`、PowerShell wrapper 或系统 `python`。

Expected: Codex and Claude Code both PASS. 如果当前执行环境没有真实 Windows 宿主，本步骤保持
未勾选，最终汇报必须写“容器门禁通过，P1 仍待 Windows 宿主复验”，不能用 Linux 测试代替。

---

## Plan Self-Review

- Spec coverage: Tasks 1–5 cover single entry, self-derived layout, PEP 723, bridge, version key,
  non-editable install, unconditional SessionStart sync, hook migration, deletion and docs; Task 6 covers
  container and external-host evidence.
- State coverage: absent/valid/invalid/conflict bridge, standalone/plugin mode, same/different version,
  valid/invalid hook payload, unbound/bound/mismatched binding, missing uv and unsafe environment target all
  have explicit tests.
- No hidden migration: operational docs change only after the executable switch; historical plans/specs retain
  pointers instead of being rewritten as if the old implementation never existed.
- Remaining external assumption: uv is present on `PATH`; automatic Python downloads may be disabled by user
  policy, in which case uv diagnostics are authoritative and no fallback installer is attempted.

---

## 复审补充（2026-08-05）

实施后的复审发现两类缺陷，已在同一工作树修复，不改变计划的入口、命令形状、环境目录规则与
uv run 行为：

1. **bridge OSError 未收口**：`installation_key` 路径解析、锁文件 fstat/加锁/解锁/关闭、
   原子写入的 mkstemp/fdopen/文件 fsync/replace、冲突哨兵关闭和 reset 删除失败时泄漏原始
   `OSError` 与 traceback。修复方式是在知道具体操作对象与路径的函数内把 OSError 转换为
   `plugin.context_invalid`（`raise ... from exc` 保留异常链），`main()` 不增加笼统的
   `except OSError`；锁内主错误在途时解锁/关闭失败降级为 `add_note` 诊断，无主错误时清理
   失败本身转换为 `plugin.context_invalid`；`_fsync_directory` 按设计完整吸收目录 fsync 的
   所有 OSError（尽力而为）。`fdopen` 失败路径补关未托管描述符。
2. **并发测试硬编码 fork**：`multiprocessing.get_context("fork")` 在 Windows 不存在。改为
   统一 `spawn`：worker 是模块顶层函数，只接收字符串与同步原语，子进程内重新加载
   `draft.py`，不依赖父进程继承的动态模块。spawn 不能证明 `msvcrt.locking` 行为，
   Windows P1 仍需真实宿主验证。

同时把 bridge 测试从 `test_launcher.py` 拆回 `test_plugin_context.py`（bridge JSON 协议、
文件安全、缺失/有效/无效/冲突、reset、锁与并发），`test_launcher.py` 只保留布局推导、
版本环境键、uv 参数与环境、`main()`/SessionStart 分派；拆分本身先以 432 测试全绿证明是
纯重组，随后新增 17 个 OSError 失败测试按 RED→GREEN 驱动上述修复。

验证证据（容器内）：`pytest` 449 passed；`pyright` 0 errors, 0 warnings；
`ruff check` 与 `ruff format --check` 全部通过。回归语义保持不变：bridge 缺失进入独立
模式、普通调用拒绝损坏 JSON、SessionStart 允许原子修复、冲突哨兵粘滞、reset 只删除安全
普通文件、bridge OSError 经 `main()` 返回 4 且不调用内层 uv。

状态区分：实现已完成、容器门禁已通过、真实 Windows 宿主复验（Task 6 Step 6）仍待执行，
P1 不关闭。
