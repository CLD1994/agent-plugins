# Draft 自包含 skill（技能）运行时实现计划

> **面向执行计划的 Agent：** 必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐项实施本计划。步骤使用复选框（`- [ ]`）跟踪状态。

**目标：** 使 `plugins/draft/skills/draft/` 成为可独立执行的自包含技能，只向 Agent 暴露一个稳定的 `scripts/draft` 入口；Codex 与 Claude Code 钩子通过严格的两字段 bridge（桥接定位器）透明增强持久环境和会话绑定。

**架构：** POSIX 与 PowerShell wrapper（包装脚本）把同一技能内 `SKILL.md` 和 runtime（运行时）的准确路径注入仅依赖 Python 标准库的入口层。该入口层解析当前安装实例的桥接定位器，选择由清单键标识的临时或持久 uv 环境，在不安装 Draft 项目本身的前提下同步依赖，并执行当前技能携带的源码；宿主钩子通过同一入口发布桥接定位器，并恢复不含 `platform` 维度的会话绑定。

**技术栈：** Python 3.14、Python 标准库、uv、pytest、pyright strict、ruff、POSIX shell、PowerShell、JSON 插件清单。

## 全局约束

- 规范性设计为 `docs/design.md` 与 `docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md`。
- Agent 唯一可见的命令入口是当前技能的 `scripts/draft`；PowerShell 通过 `scripts/draft.ps1` 提供相同行为契约。
- 完整运行时位于 `plugins/draft/skills/draft/scripts/runtime/`；不得保留插件根级 `runtime/` 或 `bootstrap/` 子树。
- `plugin-context.json` 严格只有 `schema_version` 和 `data_dir`；拒绝未知字段。
- 桥接定位器不得增加 `platform`、`runtime_version`、`entry_path`、`entry_digest`、`session_id` 或环境路径。
- binding（会话绑定）不得包含 `platform`；物理文件名是宿主原始 `session_id` 的 SHA-256，payload（载荷）保留原始字符串。
- 把宿主提供的每一个 `session_id` 字符串视为不透明值；不得增加长度、字符集、外形、UUID、前缀或 Unicode 规范化规则。
- 使用 Python `>=3.14,<3.15`；不增加兼容矩阵或新的运行时依赖。
- 环境只包含 Python 和锁定的第三方运行时依赖。始终执行当前 `scripts/runtime/src/`，绝不把 Draft 项目安装进环境。
- 不创建 `environment.lock`、`environment.json`、生成式启动器、setup 状态文件、运行时清单或旧布局兼容读取器。
- 绝不在临时运行目录与 `data_dir` 之间移动、复制或重命名虚拟环境。
- 保持 `format_version = 1`、`journal_version = 2`、插件版本 `0.1.0` 以及既有 Markdown 和领域行为不变。
- 保持 `docs/discussion/**` 不变，继续作为历史证据。
- 保留当前未提交改动。除非用户明确要求，不创建 Git 提交。

---

## 文件结构

- `plugins/draft/skills/draft/scripts/draft`：POSIX 稳定包装脚本；只计算同级资源路径并调用标准库入口。
- `plugins/draft/skills/draft/scripts/draft.ps1`：PowerShell 包装脚本；输入、输出和退出码契约与 POSIX 入口相同。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/entry.py`：负责桥接定位器解析、环境选择、uv 同步、`setup` 和内部钩子分派的部署边界；引导阶段的导入图仅依赖标准库。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/plugin_context.py`：负责严格桥接模式、当前用户运行目录、安装键、操作系统锁、原子发布、冲突哨兵、消费和 `reset`。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/session_hook.py`：负责宿主无关的 `SessionStart` 绑定查找、摘要验证、诊断和短上下文渲染。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/bindings.py`：负责无 `platform` 维度的绑定持久化和会话载荷精确验证。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/__main__.py`：从当前源码树通过 `python -m draft` 执行领域 CLI。
- `plugins/draft/skills/draft/scripts/runtime/tests/`：保存既有领域测试，以及入口、桥接定位器、钩子、绑定、包装脚本和复制产物测试。
- `plugins/draft/hooks/codex-hooks.json`：Codex `SessionStart` 命令，只使用 `PLUGIN_ROOT` 和 `PLUGIN_DATA`。
- `plugins/draft/hooks/claude-hooks.json`：Claude Code `SessionStart` 命令，只使用 `CLAUDE_PLUGIN_ROOT` 和 `CLAUDE_PLUGIN_DATA`。
- `plugins/draft/.codex-plugin/plugin.json` 与 `plugins/draft/.claude-plugin/plugin.json`：分别声明各自的钩子文件。
- `plugins/draft/skills/draft/SKILL.md` 与 `references/platform-setup.md`：说明唯一稳定入口，以及独立模式和插件增强模式的行为。

---

### 任务 1：把运行时移入技能，并保持领域命令可直接执行

**文件：**

- 移动：`plugins/draft/runtime/` → `plugins/draft/skills/draft/scripts/runtime/`
- 新建：`plugins/draft/skills/draft/scripts/runtime/src/draft/__main__.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py`

**接口：**

- 输入：当前 `draft.cli:main`、Python 包身份 `draft`、运行时版本 `0.1.0` 以及所有既有领域测试。
- 输出：`python -m draft [ARGS...]`，以及所有路径都相对于已安装技能的运行时子树。

- [ ] **步骤 1：为目标归属边界增加失败测试**

移动文件前，更新 `test_distribution.py`，使其不依赖仓库特定绝对路径即可推导各层根目录：

```python
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]
PLUGIN_ROOT = SKILL_ROOT.parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]


def test_runtime_belongs_to_the_skill() -> None:
    assert RUNTIME_ROOT == SKILL_ROOT / "scripts/runtime"
    assert (SKILL_ROOT / "SKILL.md").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()
```

更新 `test_identity.py`，只允许保留公开的开发期脚本：

```python
assert config["project"]["scripts"] == {"draft": "draft.cli:main"}
```

- [ ] **步骤 2：运行归属测试并确认处于红灯状态**

运行：

```bash
uv run --isolated --project plugins/draft/runtime pytest -q \
  plugins/draft/runtime/tests/test_identity.py \
  plugins/draft/runtime/tests/test_distribution.py
```

预期：测试失败，因为运行时仍位于插件根，`scripts/runtime/` 尚不存在，并且插件根的 `bootstrap/` 仍然存在。

- [ ] **步骤 3：移动运行时，不改变领域模块**

创建 `plugins/draft/skills/draft/scripts/`，把完整的已跟踪运行时子树移入其中；不得复制缓存或创建第二份运行时。`pyproject.toml`、`uv.lock`、`src/draft/` 和 `tests/` 必须整体保留。

在 `pyproject.toml` 中保留：

```toml
[project.scripts]
draft = "draft.cli:main"
```

删除 `draft-hook-claude`；任务 5 将用稳定的内部钩子分派替代这个宿主专用控制台脚本。

- [ ] **步骤 4：增加模块执行入口**

新建 `src/draft/__main__.py`：

```python
from __future__ import annotations

from draft.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **步骤 5：运行迁移后的运行时测试并确认处于绿灯状态**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime \
  python -m draft --version
```

预期：定向测试通过，模块命令输出 `0.1.0`。

- [ ] **步骤 6：评审移动边界**

运行：

```bash
find plugins/draft -maxdepth 4 -type d | sort
git status --short
```

预期：`skills/draft/scripts/` 下只存在一份运行时；旧 bootstrap 仅暂时保留到任务 3 完成替换；除 `__main__.py` 和项目脚本元数据外，没有领域源码发生变化。

---

### 任务 2：实现严格的两字段插件上下文桥接

**文件：**

- 新建：`plugins/draft/skills/draft/scripts/runtime/src/draft/plugin_context.py`
- 新建：`plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py`
- 仅在确实需要共享错误渲染辅助函数时修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/errors.py`

**接口：**

- 输入：规范的 POSIX 命名入口路径 `scripts/draft`、宿主提供的绝对 `data_dir` 以及当前进程环境。
- 输出：
  - `PluginContext(data_dir: Path)`
  - `BridgePaths(json: Path, conflict: Path, lock: Path)`
  - `per_user_runtime_dir(environ: Mapping[str, str] | None = None) -> Path`
  - `installation_key(entry_path: Path) -> str`
  - `bridge_paths(entry_path: Path, runtime_dir: Path | None = None) -> BridgePaths`
  - `publish(entry_path: Path, data_dir: Path, runtime_dir: Path | None = None) -> PluginContext`
  - `load(entry_path: Path, runtime_dir: Path | None = None) -> PluginContext | None`
  - `reset(entry_path: Path, runtime_dir: Path | None = None) -> None`

- [ ] **步骤 1：编写安装键、路径和模式的失败测试**

新建 `test_plugin_context.py`，并在测试中独立计算预期值：

```python
def test_installation_key_hashes_the_resolved_posix_entry(tmp_path: Path) -> None:
    entry = tmp_path / "skill/scripts/draft"
    entry.parent.mkdir(parents=True)
    entry.write_text("#!/bin/sh\n", encoding="utf-8")
    expected = hashlib.sha256(str(entry.resolve()).encode("utf-8")).hexdigest()
    assert installation_key(entry) == expected


def test_publish_writes_exact_two_field_schema(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    payload = json.loads(bridge_paths(entry, runtime_dir).json.read_text(encoding="utf-8"))
    assert payload == {"schema_version": 1, "data_dir": str(data_dir.resolve())}


def test_unknown_bridge_field_is_invalid(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    path = bridge_paths(entry, runtime_dir).json
    path.write_text(
        json.dumps({"schema_version": 1, "data_dir": str(data_dir), "platform": "codex"}),
        encoding="utf-8",
    )
    with pytest.raises(DraftError, match="plugin.context_invalid"):
        load(entry, runtime_dir)
```

还要覆盖以下状态：桥接定位器不存在时返回 `None`、未知模式版本、相对 `data_dir`、目标不存在或不是目录、JSON 损坏，以及 JSON 顶层值不是对象。

- [ ] **步骤 2：编写文件状态和冲突的失败测试**

为下列互不相同的状态增加测试：

```python
def test_different_data_dirs_create_sticky_conflict(tmp_path: Path) -> None:
    entry, first, runtime_dir = make_paths(tmp_path)
    second = tmp_path / "data-two"
    second.mkdir()
    publish(entry, first, runtime_dir)

    with pytest.raises(DraftError, match="plugin.context_conflict"):
        publish(entry, second, runtime_dir)

    paths = bridge_paths(entry, runtime_dir)
    assert paths.conflict.read_bytes() == b""
    with pytest.raises(DraftError, match="plugin.context_conflict"):
        load(entry, runtime_dir)


def test_reset_removes_only_json_and_conflict(tmp_path: Path) -> None:
    entry, data_dir, runtime_dir = make_paths(tmp_path)
    publish(entry, data_dir, runtime_dir)
    paths = bridge_paths(entry, runtime_dir)
    paths.conflict.touch()
    reset(entry, runtime_dir)
    assert not paths.json.exists()
    assert not paths.conflict.exists()
    assert paths.lock.exists()
```

对桥接 JSON、冲突哨兵、锁文件和父目录分别参数化以下状态：符号链接、目录占据文件位置、POSIX 下所有者错误，以及组用户或其他用户可写。每种不安全状态都必须报告 `plugin.context_invalid`，且不得跟随或替换该文件系统对象。

- [ ] **步骤 3：运行桥接测试并确认处于红灯状态**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py -q
```

预期：测试失败，因为 `draft.plugin_context` 尚不存在。

- [ ] **步骤 4：实现运行目录选择和不可变公开记录**

`plugin_context.py` 初始只导入标准库，并定义下列准确记录：

```python
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from draft.errors import DraftError

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PluginContext:
    data_dir: Path


@dataclass(frozen=True)
class BridgePaths:
    json: Path
    conflict: Path
    lock: Path


def installation_key(entry_path: Path) -> str:
    canonical = entry_path.resolve(strict=True)
    return hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()
```

`per_user_runtime_dir()` 必须满足：

- 在 POSIX 上，仅当 `lstat` 证明 `XDG_RUNTIME_DIR` 是由 `os.getuid()` 所有且没有组用户或其他用户权限位的真实目录时才接受它；
- 否则，在 `tempfile.gettempdir()` 下创建模式为 `0700`、名称包含数字 uid 的当前用户专属子目录；
- 在 Windows 上，使用 `tempfile.gettempdir()` 返回的当前用户临时目录；
- 绝不把当前工作目录或仓库路径用作回退位置。

- [ ] **步骤 5：实现单一操作系统锁和严格状态解析**

实现一个私有上下文管理器：不跟随符号链接地打开准确的 `.lock` 文件，然后在 POSIX 上调用 `fcntl.flock(..., LOCK_EX)`，在 Windows 上调用 `msvcrt.locking(..., LK_LOCK, 1)`。释放锁后保留锁文件；文件存在永远不等于锁正被持有。

在该锁内执行：

```python
expected_keys = {"schema_version", "data_dir"}
if set(payload) != expected_keys:
    raise DraftError("plugin.context_invalid", path)
if payload["schema_version"] != SCHEMA_VERSION:
    raise DraftError("plugin.context_invalid", path)
if not isinstance(payload["data_dir"], str):
    raise DraftError("plugin.context_invalid", path)
```

严格解析 `data_dir`，并要求它是绝对、真实且可写的目录。发布时，先规范化宿主路径，再与既有桥接定位器比较。

- [ ] **步骤 6：实现原子发布、冲突和 reset**

发布必须在桥接目录中写入模式为 `0600` 的临时文件，刷新缓冲区并调用 `os.fsync()`，再通过 `os.replace()` 替换 JSON；操作系统支持时还要同步父目录。如果既有合法桥接定位器指向另一个规范 `data_dir`，则以排他创建方式写入空冲突哨兵，保留原 JSON，并报告 `plugin.context_conflict`。

reset 必须取得同一把锁，先验证文件类型和所有权，再只删除当前安装实例的 JSON 和冲突哨兵。它不接受数据目录参数，且永不删除锁文件或桥接父目录。

- [ ] **步骤 7：证明并发发布会被串行化**

增加一个多进程测试，在同一屏障处同时释放两个工作进程：

- 同一入口和同一数据目录：两次调用都成功，最终保留一个合法 JSON；
- 同一入口和不同数据目录：至少一次调用报告 `plugin.context_conflict`，冲突哨兵存在，且 `load()` 报告冲突。

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py -q
```

预期：全部桥接测试在当前操作系统通过；只有当前操作系统确实不提供相应 API 时，才跳过该系统专用的锁测试。

---

### 任务 3：增加稳定包装入口和清单键环境生命周期

**文件：**

- 新建：`plugins/draft/skills/draft/scripts/draft`
- 新建：`plugins/draft/skills/draft/scripts/draft.ps1`
- 新建：`plugins/draft/skills/draft/scripts/runtime/src/draft/entry.py`
- 新建：`plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py`
- 重写：`plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py`
- 替换测试通过后删除：`plugins/draft/bootstrap/setup.sh`
- 替换测试通过后删除：`plugins/draft/bootstrap/setup.ps1`
- 文件删除后移除空目录：`plugins/draft/bootstrap/`

**接口：**

- 输入：准确的包装脚本路径、准确的 `SKILL.md`、运行时的 `pyproject.toml`、`uv.lock`、`src/` 和 `plugin_context.load()`。
- 输出：
  - `RuntimeLayout(entry_path: Path, skill_path: Path, project_path: Path, source_path: Path)`
  - `environment_key(layout: RuntimeLayout) -> str`
  - `environment_path(layout: RuntimeLayout, context: PluginContext | None, runtime_dir: Path | None = None) -> Path`
  - `sync_environment(layout: RuntimeLayout, target: Path, environ: Mapping[str, str]) -> Path`
  - `main(argv: Sequence[str] | None = None) -> int`
  - 公开命令 `scripts/draft setup` 和 `scripts/draft [draft CLI args]`。

- [ ] **步骤 1：编写环境键和模式选择的失败测试**

新建 `test_entry.py`：

```python
def test_environment_key_uses_manifest_bytes(tmp_path: Path) -> None:
    layout = make_layout(tmp_path, pyproject=b"project", lock=b"lock")
    expected = hashlib.sha256(
        b"draft-environment-v1\0"
        + hashlib.sha256(b"project").digest()
        + hashlib.sha256(b"lock").digest()
    ).hexdigest()
    assert environment_key(layout) == expected


def test_environment_path_switches_only_on_valid_context(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    runtime_dir = tmp_path / "runtime-dir"
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    key = environment_key(layout)
    assert environment_path(layout, None, runtime_dir) == runtime_dir / "draft/envs" / key
    assert environment_path(layout, PluginContext(data_dir), runtime_dir) == data_dir / "envs" / key
```

增加变更测试，证明任一清单发生字节变化都会产生不同的环境键，而只改变源码不会改变环境键。

- [ ] **步骤 2：编写同步和失败停止的失败测试**

向 `sync_environment()` 注入子进程执行器，并断言准确的调用参数和环境：

```python
assert call.args == [
    "uv", "sync", "--project", str(layout.project_path),
    "--locked", "--no-install-project", "--no-dev",
]
assert call.env["UV_PROJECT_ENVIRONMENT"] == str(target)
```

增加测试以证明：

- 目标不存在是允许状态；
- 目标是符号链接或非目录时，在运行 uv 前报告 `runtime.environment_invalid`；
- `uv sync` 失败时返回非零，且既不执行自检，也不执行 Draft；
- 同步成功后，以当前 `source_path` 设置 `PYTHONPATH`，并运行 `<target interpreter> -m draft --version`；
- 自检失败会阻止执行请求的命令；
- `setup` 执行同步和自检，但绝不调用领域 CLI；
- 普通命令执行同样的同步和自检，再运行 `<target interpreter> -m draft ...`，并返回该进程的准确退出码；
- 独立模式移除 `DRAFT_DATA`，插件增强模式设置它，两种模式都设置准确的 `DRAFT_SKILL_PATH`。

- [ ] **步骤 3：运行入口测试并确认处于红灯状态**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py -q
```

预期：测试失败，因为 `draft.entry` 尚不存在。

- [ ] **步骤 4：实现仅依赖标准库的入口边界**

模块级导入只能包含标准库、`draft.errors` 和 `draft.plugin_context`。使用包装脚本传入的准确布局，不得通过 `__file__.parents` 发现 `SKILL.md`：

```python
@dataclass(frozen=True)
class RuntimeLayout:
    entry_path: Path
    skill_path: Path
    project_path: Path
    source_path: Path


def environment_key(layout: RuntimeLayout) -> str:
    project = hashlib.sha256((layout.project_path / "pyproject.toml").read_bytes()).digest()
    lock = hashlib.sha256((layout.project_path / "uv.lock").read_bytes()).digest()
    return hashlib.sha256(b"draft-environment-v1\0" + project + lock).hexdigest()
```

在用户命令之前解析包装脚本提供的三个准确内部路径参数（`--entry-path`、`--skill-path`、`--runtime-path`）。严格解析每个路径，要求 `entry_path` 和 `skill_path` 是普通文件、运行时和源码路径是真实目录，然后构造 `RuntimeLayout`。

- [ ] **步骤 5：实现同步、解释器选择、自检和执行**

不依赖虚拟环境 shebang，直接选择环境解释器：

```python
def environment_python(target: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return target / relative
```

每个普通命令和 `setup` 都执行：

1. 调用 `plugin_context.load()`；
2. 选择准确的环境路径；
3. 设置 `UV_PROJECT_ENVIRONMENT` 并运行 uv 同步；
4. 把 `PYTHONPATH` 替换为当前源码路径，运行 `python -m draft --version`；
5. 如果是 `setup`，到此返回；否则用原始用户参数运行 `python -m draft`。

不得创建或检查环境元数据文件。由 uv 串行化并发同步。uv 缺失时报告 `runtime.uv_unavailable`；同步或自检失败时保留子进程退出码和简洁诊断。

- [ ] **步骤 6：编写 POSIX 稳定包装脚本**

新建可执行文件 `scripts/draft`：

```sh
#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
ENTRY="$SCRIPT_DIR/draft"
SKILL="$SCRIPT_DIR/../SKILL.md"
RUNTIME="$SCRIPT_DIR/runtime"

if ! command -v uv >/dev/null 2>&1; then
    echo 'runtime.uv_unavailable: 请从 https://docs.astral.sh/uv/ 安装 uv' >&2
    exit 4
fi

PYTHONPATH="$RUNTIME/src" exec uv run --no-project --python 3.14 python -m draft.entry \
    --entry-path "$ENTRY" --skill-path "$SKILL" --runtime-path "$RUNTIME" -- "$@"
```

包装脚本不得读取 `PLUGIN_DATA`、`CLAUDE_PLUGIN_DATA` 或宿主缓存路径。

- [ ] **步骤 7：编写 PowerShell 稳定包装脚本**

新建 `scripts/draft.ps1`，使用相同的准确路径参数和退出行为：

```powershell
$ErrorActionPreference = 'Stop'
$Runtime = Join-Path $PSScriptRoot 'runtime'
$Entry = Join-Path $PSScriptRoot 'draft'
$Skill = Join-Path (Split-Path $PSScriptRoot -Parent) 'SKILL.md'
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    [Console]::Error.WriteLine('runtime.uv_unavailable: 请从 https://docs.astral.sh/uv/ 安装 uv')
    exit 4
}
$previous = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $Runtime 'src'
    & $uv.Source run --no-project --python 3.14 python -m draft.entry `
        --entry-path $Entry --skill-path $Skill --runtime-path $Runtime -- @args
    exit $LASTEXITCODE
} finally {
    $env:PYTHONPATH = $previous
}
```

只根据真实 PowerShell 解析测试的需要调整引号；不得在 Agent 可见语法中引入 `platform` 或数据目录参数。

- [ ] **步骤 8：用包装脚本行为测试替换 bootstrap 测试**

重写 `test_bootstrap.py`：只复制 `skills/draft/`，把伪造的 `uv` 放在 `PATH` 首位，并验证两个包装脚本使用的资源路径都位于复制出的技能内。伪 uv 必须记录调用，并创建测试所需的目标环境解释器；任何对插件根 `runtime/`、`bootstrap/`、`data/bin`、`installed-runtime-manifest` 或 `setup-status.json` 的引用都必须使测试失败。

运行：

```bash
sh -n plugins/draft/skills/draft/scripts/draft
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py
```

如果存在 `pwsh`，还要运行：

```bash
pwsh -NoProfile -File plugins/draft/skills/draft/scripts/draft.ps1 --version
```

预期：全部测试通过，包装脚本返回模拟的 Draft 退出码，并且没有生成式启动器或元数据文件。

- [ ] **步骤 9：删除已被替代的 bootstrap 子树**

包装脚本测试通过后，删除 `plugins/draft/bootstrap/setup.sh`、`setup.ps1` 和空目录。重新运行任务 1 的分发测试，证明只存在一种入口架构。

---

### 任务 4：从绑定契约中删除 `platform`，并验证准确会话身份

**文件：**

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/bindings.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py`

**接口：**

- 输入：由 `entry.py` 注入的准确 `DRAFT_DATA` 和 `DRAFT_SKILL_PATH`。
- 输出：
  - `Binding(session_id: str, root: Path, last_page_hint: Path | None, skill_name: str, skill_digest: str)`
  - `binding_key(session_id: str) -> str`
  - `binding_path(data_dir: Path, session_id: str) -> Path`
  - `bind(data_dir: Path, binding: Binding) -> Binding`
  - `load_binding(data_dir: Path, session_id: str) -> Binding | None`
  - `unbind(data_dir: Path, session_id: str) -> bool`
  - CLI 命令 `bind ROOT --session SESSION_ID` 和 `unbind --session SESSION_ID`。

- [ ] **步骤 1：按目标契约重写绑定测试，并确认处于红灯状态**

用下列内容替换 `platform` fixture（夹具）和断言：

```python
def make_binding(root: Path, session: str = "abc123", hint: Path | None = None) -> Binding:
    return Binding(
        session_id=session,
        root=root,
        last_page_hint=hint,
        skill_name="draft",
        skill_digest="test-digest",
    )


def test_binding_path_hashes_opaque_session_id(data_dir: Path) -> None:
    session = "../斜线/和\\unicode"
    path = binding_path(data_dir, session)
    assert path.parent == data_dir / "bindings"
    assert path.name == f"{hashlib.sha256(session.encode('utf-8')).hexdigest()}.json"
```

显式增加 `""`、`/`、`\\`、`..`、空白和非 ASCII 字符串用例。这些测试断言哈希和路径包含关系，而不是断言拒绝输入。

- [ ] **步骤 2：增加载荷错配和不安全文件测试**

在正确的哈希路径创建文件，但写入另一个会话标识：

```python
def test_load_rejects_session_payload_mismatch(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft, session="expected"))
    path = binding_path(data_dir, "expected")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session_id"] = "other"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DraftError, match="binding.session_mismatch"):
        load_binding(data_dir, "expected")
```

还必须验证以下状态都会失败且不返回草稿根：绑定文件是符号链接或目录、JSON 损坏、缺少字段、额外包含 `platform` 字段，以及字段类型错误。只有文件不存在时返回 `None`。

- [ ] **步骤 3：运行绑定和 CLI 测试并确认处于红灯状态**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py
```

预期：失败结果暴露当前仍存在的 `Binding.platform`、`PLATFORMS`、`--platform` 和 `bindings/<platform>/` 契约。

- [ ] **步骤 4：实现不含 `platform` 的模型和存储**

把 dataclass（数据类）改为：

```python
@dataclass(frozen=True)
class Binding:
    session_id: str
    root: Path
    last_page_hint: Path | None
    skill_name: str
    skill_digest: str
```

删除 `PLATFORMS` 和 `binding.invalid_platform`。严格按下列结构序列化：

```python
payload = {
    "session_id": binding.session_id,
    "root": str(binding.root),
    "last_page_hint": None if binding.last_page_hint is None else binding.last_page_hint.as_posix(),
    "skill_name": binding.skill_name,
    "skill_digest": binding.skill_digest,
}
```

读取时要求键集合严格等于这五项，解析字段类型，并在暴露载荷中任何路径之前，把解析出的 `session_id` 与请求字符串进行精确比较。错配时报告 `binding.session_mismatch`；结构损坏时使用 `binding.invalid`。

- [ ] **步骤 5：更新 CLI 边界**

从两个解析器中删除 `--platform`。argparse 已提供 Python `str` 后，不得拒绝空字符串。插件上下文缺失时改用已批准的错误：

```python
data_value = os.environ.get("DRAFT_DATA")
if data_value is None:
    raise DraftError("binding.plugin_context_unavailable")
```

`bind` 输出只包含 `result_type`、规范草稿根和成功状态，不回显 `platform`。更新 `_exit_for_error()`，使插件上下文和部署错误使用运行时退出码 `4`。

- [ ] **步骤 6：运行定向测试确认绿灯，并扫描契约残留**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py
! rg -n 'PLATFORMS|invalid_platform|--platform|bindings.*/(claude|codex)|"platform"' \
  plugins/draft/skills/draft/scripts/runtime/src \
  plugins/draft/skills/draft/scripts/runtime/tests
```

预期：测试通过，当前绑定实现不再包含 `platform` 维度。

---

### 任务 5：通过稳定入口接入 Codex 与 Claude Code 的 `SessionStart`

**文件：**

- 新建：`plugins/draft/hooks/codex-hooks.json`
- 修改：`plugins/draft/hooks/claude-hooks.json`
- 修改：`plugins/draft/.codex-plugin/plugin.json`
- 修改：`plugins/draft/.claude-plugin/plugin.json`
- 新建：`plugins/draft/skills/draft/scripts/runtime/src/draft/session_hook.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/entry.py`
- 用 `test_session_hook.py` 替换：`plugins/draft/skills/draft/scripts/runtime/tests/test_claude_hook.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py`
- 删除：`plugins/draft/skills/draft/scripts/runtime/src/draft/claude_hook.py`

**接口：**

- 输入：
  - Codex：`PLUGIN_ROOT`、`PLUGIN_DATA` 和 `SessionStart` 标准输入；
  - Claude Code：`CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA` 和 `SessionStart` 标准输入；
  - `plugin_context.publish()`、当前环境键和不含 `platform` 的绑定。
- 输出：
  - 内部入口命令 `_session-start codex|claude`；
  - `render_session_fact(session_id: str) -> str`;
  - `render_rebind(binding: Binding, source: str, hint: Path | None) -> str`;
  - 两个宿主都接受的简短标准输出上下文；
  - 持久化的 `data_dir/logs/hook.jsonl` 诊断。

- [ ] **步骤 1：重新打开宿主官方文档并记录核对事实**

编码前立即核对：

- Codex 插件钩子由 `.codex-plugin/plugin.json` 声明，或使用默认的 `hooks/hooks.json`；
- Codex 提供 `PLUGIN_ROOT`、`PLUGIN_DATA`、通用 `session_id` 和 `SessionStart` 的 `source`；
- Claude Code 提供 `CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA`、通用 `session_id` 和 `SessionStart` 的 `source`；
- 纯标准输出或当前文档规定的 `SessionStart` JSON 结构能够进入模型上下文；
- 当前 `source` 值仍然准确，其中包括 Claude Code 的 `fork`。

如果任一项发生变化，必须先更新 `docs/design.md` 和运行时规格，再修改代码。

- [ ] **步骤 2：编写插件清单失败测试**

更新 `test_distribution.py`：

```python
def test_both_manifests_declare_their_session_start_hook() -> None:
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["hooks"] == "./hooks/codex-hooks.json"
    assert claude["hooks"] == "./hooks/claude-hooks.json"


def test_hook_commands_use_the_stable_skill_entry() -> None:
    codex = load_json(PLUGIN_ROOT / "hooks/codex-hooks.json")
    claude = load_json(PLUGIN_ROOT / "hooks/claude-hooks.json")
    codex_command = codex["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    claude_command = claude["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "${PLUGIN_ROOT}/skills/draft/scripts/draft" in codex_command
    assert "${CLAUDE_PLUGIN_ROOT}/skills/draft/scripts/draft" in claude_command
    assert "PLUGIN_DATA" not in codex_command
    assert "CLAUDE_PLUGIN_DATA" not in claude_command
    assert "/bin/draft" not in codex_command + claude_command
```

- [ ] **步骤 3：编写 `SessionStart` 状态失败测试**

用宿主参数化测试替换仅覆盖 Claude Code 的 fixture，并覆盖：

- 钩子在检查会话字段之前发布桥接定位器；
- 即使 Claude 兼容别名包含不同路径，Codex 也只读取 `PLUGIN_ROOT` 和 `PLUGIN_DATA`；
- Claude Code 只读取 `CLAUDE_PLUGIN_ROOT` 和 `CLAUDE_PLUGIN_DATA`；
- `session_id` 缺失或不是字符串时报告 `binding.session_unavailable`，保留合法桥接定位器，且不创建绑定；
- 接收到的每个字符串都被接受为会话身份，包括空字符串和 Unicode 字符串；
- 未绑定会话只输出准确的当前会话事实，绝不输出活动草稿根或启动器命令；
- 已绑定会话输出草稿根、合法页面提示、技能身份及摘要和 `source`；
- 过期页面提示被省略；
- 草稿根缺失或技能摘要错配时输出诊断，但不把旧草稿根暴露为活动草稿；
- 在状态完全相同时重复调用，产生完全相同的模型可见输出；
- 损坏绑定、载荷错配、桥接冲突、环境失败和不安全路径保持可区分。

- [ ] **步骤 4：运行钩子测试并确认处于红灯状态**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py
```

预期：测试失败，因为宿主无关模块、Codex 钩子和稳定内部分派尚不存在。

- [ ] **步骤 5：实现宿主无关的绑定上下文渲染器**

新建 `session_hook.py`，使用标准库解析和当前绑定 API。模型可见函数必须保持确定性：

```python
def render_session_fact(session_id: str) -> str:
    return f"Draft 宿主 session_id：{session_id}\n在 scripts/draft bind/unbind 中原样使用此值。"


def render_rebind(binding: Binding, source: str, hint: Path | None) -> str:
    lines = [
        "Draft 重新绑定上下文",
        f"- 活动草稿根：{binding.root}",
        f"- 必须使用的 skill：{binding.skill_name}（摘要 {binding.skill_digest}）",
        f"- 宿主触发原因：{source}",
    ]
    if hint is not None:
        lines.insert(2, f"- 最后页面提示：{hint.as_posix()}")
    lines.append("继续受管理讨论前，重新完整读取当前 SKILL.md。")
    return "\n".join(lines)
```

继续使用仓库既有的简洁中文措辞。测试必须断言字段和旧数据不存在，而不是对完整自然语言文本建立快照。

- [ ] **步骤 6：扩展入口，增加宿主专用环境选择**

在普通桥接消费逻辑之前，只识别内部命令 `_session-start HOST`。准确变量映射如下：

```python
HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}
```

这条路径必须：

1. 验证宿主提供的插件根解析后正是包含当前技能的插件根；
2. 验证宿主数据目录；
3. 在解析会话身份之前调用 `plugin_context.publish()`；
4. 从标准输入最多读取 64 KiB 再加一个字节，并要求得到一个满足 `hook_event_name == "SessionStart"` 的 JSON 对象；
5. 只要求 `session_id` 是 Python 字符串；不拒绝空内容，也不检查外形；
6. 计算准确的绑定路径；
7. 如果当前独立环境安全存在，把它仅视为预热信号，并同步持久目标；不得从独立环境读取包状态或移动该环境；
8. 如果绑定不存在，输出 `render_session_fact()`；除非已触发预热，否则不要求持久环境存在；
9. 如果绑定文件存在，同步并自检持久环境，再调用当前源码中的 `session_hook` 逻辑；
10. 后续绑定分支失败时，保留桥接定位器和任何已经成功同步的环境。

引导路径必须在两个目标环境都不存在时工作。它可以使用包装脚本的 `uv run --no-project --python 3.14` 解释器，但在环境同步之前不得导入 `markdown-it-py`、`Send2Trash` 或其他第三方包。

- [ ] **步骤 7：编写两个钩子文件及其清单引用**

新建 `hooks/codex-hooks.json`：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "\"${PLUGIN_ROOT}/skills/draft/scripts/draft\" _session-start codex"
          }
        ]
      }
    ]
  }
}
```

使用相同的稳定路径和 `_session-start claude` 重写 `hooks/claude-hooks.json`，并在 `matcher` 中包含 `fork`。如果步骤 1 的强制文档复核表明 `fork` 或其他公开字段已经变化，先停止实施并修订规范性文档，再修改适配器。每份插件清单只指向自己的钩子文件。

- [ ] **步骤 8：删除旧的 Claude 专用运行时入口，并运行测试确认绿灯**

删除 `src/draft/claude_hook.py`，清除仍然存在的控制台脚本元数据，并确保没有测试继续导入它。

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_entry.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py
python3 -m json.tool plugins/draft/hooks/codex-hooks.json >/dev/null
python3 -m json.tool plugins/draft/hooks/claude-hooks.json >/dev/null
```

预期：两个宿主通过同一状态矩阵，钩子 JSON 可解析，并且没有命令引用 `data_dir/bin` 或插件根运行时。

---

### 任务 6：更新技能、setup 参考文档和复制产物契约

**文件：**

- 修改：`plugins/draft/skills/draft/SKILL.md`
- 重写：`plugins/draft/skills/draft/references/platform-setup.md`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py`

**接口：**

- 输入：任务 1 至任务 5 产出的最终入口命令、桥接模式、绑定 CLI、宿主钩子行为和环境错误码。
- 输出：绝不要求动态启动器或插件数据变量的 Agent 指令，以及能够独立执行复制后技能的安装产物测试。

- [ ] **步骤 1：记录当前文档扫描的失败基线**

运行：

```bash
rg -n 'bootstrap/setup|data.*/bin/draft|DRAFT_DATA|CLAUDE_CODE_SESSION_ID|--platform|bindings/<platform>|plugin-root runtime' \
  plugins/draft/skills/draft
```

预期：旧 setup 参考文档和绑定示例仍包含已废止接口，因此扫描产生匹配。

- [ ] **步骤 2：把 `SKILL.md` 更新为唯一入口**

第一条命令指令必须建立唯一规则：

```text
始终调用当前 skill 自己的 scripts/draft（PowerShell 使用 scripts/draft.ps1）。
不要搜索 PLUGIN_DATA/CLAUDE_PLUGIN_DATA，也不要调用在数据目录中生成的启动器。
```

只要可能产生歧义，就把所有裸 `draft ...` 示例替换为稳定入口形式。文档列出：

```text
scripts/draft setup
scripts/draft init ROOT
scripts/draft check ROOT
scripts/draft bind ROOT --session SESSION_ID
scripts/draft unbind --session SESSION_ID
```

说明 `setup` 只是可选预热，普通命令会执行相同的环境检查；独立模式下 `bind`/`unbind` 会失败，但核心命令仍然可用。

- [ ] **步骤 3：围绕行为而非宿主路径重写 `platform-setup.md`**

参考文档必须覆盖：

- 前置依赖 `uv`，Python 3.14 由 uv 管理；
- 一个稳定入口和可选的 `setup`；
- 独立模式环境路径和插件增强模式环境路径；
- 严格的两字段桥接模式及其非租约生命周期；
- Codex 与 Claude Code 的钩子信任和重新加载行为；
- 会话标识来自模型可见的 `SessionStart` 上下文，并被原样复制进 `bind`/`unbind`；
- `plugin.context_invalid`、`plugin.context_conflict`、`runtime.uv_unavailable`、`runtime.environment_invalid`、`binding.plugin_context_unavailable`、`binding.session_mismatch` 和 `binding.skill_mismatch` 的恢复动作；
- 只有停止重复加载后，才能执行 `scripts/draft plugin-context reset`。

不得指示 Agent 读取或导出宿主数据变量。不得记录生成式 `bin/` 启动器、运行时清单、setup 状态、环境 JSON 或带 `platform` 层级的绑定目录。

- [ ] **步骤 4：加强分发测试**

要求已安装插件和独立复制的技能准确包含执行所需的运行时资源：

```python
def test_skill_is_a_self_contained_runtime_unit() -> None:
    scripts = SKILL_ROOT / "scripts"
    assert (scripts / "draft").is_file()
    assert (scripts / "draft.ps1").is_file()
    assert (scripts / "runtime/pyproject.toml").is_file()
    assert (scripts / "runtime/uv.lock").is_file()
    assert (scripts / "runtime/src/draft/__main__.py").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()
    assert not (PLUGIN_ROOT / "bootstrap").exists()
```

增加复制技能的子进程测试：移除所有插件宿主变量，使用隔离的运行目录，然后通过复制后的 `scripts/draft` 运行 `setup`、`init`、`check` 和 `move`。断言创建的全部环境状态都位于注入的测试运行目录内，全部受管理内容都位于选定的 Draft 草稿根内。

- [ ] **步骤 5：运行技能和分发检查并确认处于绿灯状态**

运行：

```bash
python3 /mnt/c/Users/CLD/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/draft/skills/draft
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py
! rg -n 'bootstrap/setup|data.*/bin/draft|CLAUDE_CODE_SESSION_ID|--platform|bindings/<platform>' \
  plugins/draft/skills/draft
```

预期：校验和测试通过；当前技能指令不再暴露已被替代的 setup 或绑定接口。

---

### 任务 7：验证完整源码、清单、钩子和复制产物

**文件：**

- 只有门禁暴露契约违反时才修改相应文件。
- 全部门禁通过后更新状态：`docs/design.md`
- 全部门禁通过后更新状态：`docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md`

**接口：**

- 输入：任务 1 至任务 6 的全部产物。
- 输出：目标目录树、运行时契约、两个宿主和独立复制技能的新鲜证据；不创建 Git 提交。

- [ ] **步骤 1：运行完整 Python 质量门禁**

运行：

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check .
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff format --check .
```

预期：测试失败、类型错误、lint 错误和格式差异均为零。

- [ ] **步骤 2：校验插件、技能、JSON 和包装脚本**

运行：

```bash
python3 /mnt/c/Users/CLD/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/draft
python3 /mnt/c/Users/CLD/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/draft/skills/draft
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
python3 -m json.tool .claude-plugin/marketplace.json >/dev/null
python3 -m json.tool plugins/draft/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/draft/.claude-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/draft/hooks/codex-hooks.json >/dev/null
python3 -m json.tool plugins/draft/hooks/claude-hooks.json >/dev/null
sh -n plugins/draft/skills/draft/scripts/draft
```

如果存在 `pwsh`，在临时复制的技能中解析并执行 PowerShell 包装脚本。如果不存在，明确记录运行时的 PowerShell 烟雾测试仍是环境假设；静态评审不能等同于实际执行。

- [ ] **步骤 3：证明被禁止的架构产物不存在**

运行：

```bash
test ! -e plugins/draft/runtime
test ! -e plugins/draft/bootstrap
! find plugins/draft -type f \( \
  -name 'environment.lock' -o \
  -name 'environment.json' -o \
  -name 'installed-runtime-manifest.json' -o \
  -name 'setup-status.json' \
\) -print | grep .
! rg -n 'PLATFORMS|invalid_platform|--platform|bindings/(claude|codex)|data.*/bin/draft' \
  plugins/draft
! rg -n 'draft (bind|unbind).*--platform|bootstrap/setup\.(sh|ps1)' docs/design.md
```

预期：所有命令退出码均为 `0`，且不产生任何禁止项匹配。

- [ ] **步骤 4：运行独立复制技能的烟雾测试**

使用全新目录，并清除插件变量：

```bash
DRAFT_SMOKE_DIR=$(mktemp -d)
trap 'rm -rf -- "$DRAFT_SMOKE_DIR"' EXIT
cp -R plugins/draft/skills/draft "$DRAFT_SMOKE_DIR/skill"
install -d -m 700 "$DRAFT_SMOKE_DIR/runtime"
env -u PLUGIN_DATA -u CLAUDE_PLUGIN_DATA -u PLUGIN_ROOT -u CLAUDE_PLUGIN_ROOT \
  XDG_RUNTIME_DIR="$DRAFT_SMOKE_DIR/runtime" \
  "$DRAFT_SMOKE_DIR/skill/scripts/draft" setup
env -u PLUGIN_DATA -u CLAUDE_PLUGIN_DATA -u PLUGIN_ROOT -u CLAUDE_PLUGIN_ROOT \
  XDG_RUNTIME_DIR="$DRAFT_SMOKE_DIR/runtime" \
  "$DRAFT_SMOKE_DIR/skill/scripts/draft" init "$DRAFT_SMOKE_DIR/managed"
env -u PLUGIN_DATA -u CLAUDE_PLUGIN_DATA -u PLUGIN_ROOT -u CLAUDE_PLUGIN_ROOT \
  XDG_RUNTIME_DIR="$DRAFT_SMOKE_DIR/runtime" \
  "$DRAFT_SMOKE_DIR/skill/scripts/draft" check "$DRAFT_SMOKE_DIR/managed"
```

预期：所有命令成功；环境只存在于 `runtime/draft/envs/<environment_key>/` 下；不创建桥接定位器、会话绑定、生成式启动器或临时根目录之外的状态。

- [ ] **步骤 5：为两个宿主适配器运行复制插件的 `SessionStart` 烟雾测试**

复制 `plugins/draft/`，分别提供临时 Codex 和 Claude Code 数据目录，并用真实的 `SessionStart` JSON 对象调用各自钩子命令。验证：

1. 首次未绑定调用发布准确的两字段桥接定位器，并且只输出会话事实；
2. 通过同一份复制后的 `scripts/draft` 执行 `bind`，创建不含 `platform` 的 `bindings/<sha256>.json`；
3. `compact` 或 `resume` 调用输出已绑定草稿根和当前技能摘要；
4. 改变技能字节后，钩子拒绝旧重新绑定上下文，直到 `bind` 显式覆盖记录；
5. 同一复制入口收到两个不同数据目录时形成粘滞冲突，并要求 reset。

- [ ] **步骤 6：仅在验证完成后更新文档状态**

步骤 1 至步骤 5 全部通过后，才把 `docs/design.md` 的状态从“待按实现计划落地”改为“已实现并验证”，并把运行时规格状态改为已实现。任何强制门禁仍为红灯或尚未执行时，都不得更新状态。

- [ ] **步骤 7：执行最终差异和需求审计**

运行：

```bash
git diff --check
git status --short
find plugins/draft -type l -print
```

然后，把 `docs/design.md` 第 15 至第 19 节以及运行时规格“验收标准”中的每一项映射到上面的测试或烟雾命令。预期：没有空白错误、没有意外符号链接、没有计划文件范围之外的写入，也没有任何需求仅由未覆盖其状态边界的绿灯测试来证明。不要提交。
