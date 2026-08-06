# Draft 测试体系分层与提速实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **并行执行：** 在执行并行批次前，必须使用 `superpowers:using-git-worktrees` 建立独立工作树，并使用 `superpowers:dispatching-parallel-agents` 按本计划的文件所有权分派任务。协调者最多同时运行三个实现任务；不得让多个执行者共享同一工作树。

**Goal:** 将 Draft 当前混合的测试套件重构为职责清晰的函数级单元测试和端到端集成测试，使默认测试反馈稳定、快速、可局部维护，同时保留对真实文件系统、子进程、依赖解析、构建安装和 uv 环境隔离的验证。

**Architecture:** 核心运行时（runtime；目录和包标识保留英文）与插件适配层各自建立 `tests/unit/` 和 `tests/integration/` 两级目录。单元测试默认以一个具有独立语义契约的生产函数为被测单元，在稳定协作者边界使用受接口约束的测试替身；集成测试验证真实运行链路。`network` 只作为集成测试的执行属性。实施工作按核心、适配层和慢集成测试拆成互不重叠的文件所有权，在独立 Git 工作树（worktree；Git 术语保留英文）中并行完成，汇合后再执行跨层契约清理和完整门禁。

**Tech Stack:** Python `>=3.12,<3.15`、pytest、`unittest.mock`、uv/uvx、Pyright 严格模式、ruff、Git 工作树。

## Global Constraints

- 以根目录 `AGENTS.md` 的“测试规范”章节和新建的 `plugins/draft/AGENTS.md` 为规范基线；进入 Draft 子树执行任务前必须先读取两者。
- `plugins/draft/CLAUDE.md` 必须是指向同目录 `AGENTS.md` 的相对软链接，链接目标字面量固定为 `AGENTS.md`。
- 本计划重构测试、测试配置和作用域指令，不改变 Draft 的生产行为、CLI、Hook、binding、bridge（插件上下文桥接）、文件格式、退出码或错误码。允许为适配层严格类型检查增加 `cast`、泛型参数或等价类型收窄，但不得改变运行分支和返回值。
- 若现有函数无法在不模拟标准库内部细节的情况下隔离，停止该项并修订计划；不得借测试重构顺手拆分生产函数。
- 除解释器导入被测源码外，单元测试不得使用 `tmp_path`、真实文件系统、网络、数据库、子进程、包构建、安装、依赖解析、uv 缓存或进程级环境状态。允许将 `Path` 当作不可变值参与纯词法计算。
- 测试替身必须替换具有独立语义的协作者，并使用 `create_autospec`、`Mock(spec_set=...)` 或带 `autospec=True` 的 `patch` 约束真实接口。时钟、UUID 和环境映射是允许替换的显式非确定性边界。
- 单元测试优先断言返回值、异常和被测函数拥有的状态；只有调用次数、顺序或参数属于公开协议、安全不变量或副作用契约时才断言交互。
- 具有独立行为契约的私有函数可以直接单元测试；严格类型检查所需的私有访问豁免必须精确到访问行并写明所验证的契约，不得在文件或项目级关闭 `reportPrivateUsage`。
- 所有现有真实运行测试必须先原样迁移到集成目录并证明收集数和行为未丢失，再补单元测试和删除重复项；不得提前删除安全矩阵。
- 对已有正确行为新增表征测试时必须执行局部变异证明（mutation proof）：临时把对应生产分支改错，确认目标测试失败，立即恢复；变异不得暂存或提交。
- 核心与适配层继续用两条独立 pytest 命令运行；本计划不把两个测试树合并到一次 pytest 收集中。
- `network` 标记只用于会访问索引、解析或下载依赖、或者依赖冷 uv 缓存的集成测试。普通真实文件系统和本地子进程测试不标记 `network`。
- 默认单元测试目标为核心、适配层各不超过 5 秒，合计不超过 10 秒。耗时只测量和汇报，不编码为 pytest 断言。
- 并行任务只能修改各自 `Files` 列出的文件。发现必须跨所有权修改时，执行者停止并向协调者报告，不自行扩大范围。
- 每个并行任务从同一个已汇合基线提交创建独立分支和工作树。协调者按任务编号顺序拣选提交（cherry-pick），每次汇合后运行该任务的定向门禁。
- 开始创建并行工作树前，当前计划、新建的 Draft 子树指令以及已批准但尚未提交的根目录 `AGENTS.md` 测试规范必须进入同一个可追溯基线；不得把其他未提交改动带入任务提交。

---

## File Structure

### 作用域指令

- Modify: `.gitignore`
  - 忽略项目内后备工作树目录 `.worktrees/`，避免并行工作树出现在 Git 状态中。
- Preserve/Modify: `AGENTS.md`
  - 保留当前已经批准的顶层测试原则；若执行时仍未提交，在任务 1 中一并提交，不重写其他章节。
- Create: `plugins/draft/AGENTS.md`
  - 记录 Draft 子树测试目录、测试边界、稳定命令和适配层特殊约束。
- Create symlink: `plugins/draft/CLAUDE.md -> AGENTS.md`
  - 让 Claude Code 读取同一份 Draft 子树指令，不维护第二份文本。

### 核心运行时

- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/conftest.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/conftest.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/support/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/support/paths.py`
- Move: 现有核心测试模块，具体归属见任务 2。
- Create/Modify: 核心函数级单元测试，具体文件所有权见任务 4、5、6。
- Split: 核心发布契约与慢安装测试，具体文件见任务 10。

### 插件适配层

- Create: `plugins/draft/scripts/pytest.ini`
- Create: `plugins/draft/scripts/pyrightconfig.json`
- Delete: `plugins/draft/scripts/tests/conftest.py`
- Create: `plugins/draft/scripts/tests/__init__.py`
- Create: `plugins/draft/scripts/tests/unit/__init__.py`
- Create: `plugins/draft/scripts/tests/integration/__init__.py`
- Create: `plugins/draft/scripts/tests/integration/conftest.py`
- Create: `plugins/draft/scripts/tests/support/__init__.py`
- Create: `plugins/draft/scripts/tests/support/paths.py`
- Create: `plugins/draft/scripts/tests/support/entry.py`
- Move: 现有适配层测试模块，具体归属见任务 3。
- Create/Modify: 适配层函数级单元测试，具体文件所有权见任务 7、8、9。
- Split: 适配层发布契约与慢安装测试，具体文件见任务 11。

---

## 并行执行拓扑

| 任务 | 依赖          | 文件所有权                                     | 可并行关系              |
| ---- | ------------- | ---------------------------------------------- | ----------------------- |
| 1    | 无            | 根与 Draft 子树指令                            | 必须最先独立完成        |
| 2    | 1             | 核心测试目录与 pytest 配置                     | 与任务 3 并行           |
| 3    | 1             | 适配层测试目录与 pytest 配置                   | 与任务 2 并行           |
| 4    | 2、3 汇合     | 核心 `graph`/`checker`/`markdown` 测试         | 与任务 5–11 并行        |
| 5    | 2、3 汇合     | 核心 CLI/`lifecycle`/`operations`/`paths` 测试 | 与任务 4、6–11 并行     |
| 6    | 2、3 汇合     | 核心 `structure`/`trash` 测试                  | 与任务 4、5、7–11 并行  |
| 7    | 2、3 汇合     | 适配层 `errors`/`bindings`/`bridge` 测试       | 与任务 4–6、8–11 并行   |
| 8    | 2、3 汇合     | 适配层 SessionStart 测试                       | 与任务 4–7、9–11 并行   |
| 9    | 2、3 汇合     | 插件 CLI 测试                                  | 与任务 4–8、10、11 并行 |
| 10   | 2、3 汇合     | 核心慢集成与发布测试                           | 与任务 4–9、11 并行     |
| 11   | 2、3 汇合     | 适配层慢集成与发布测试                         | 与任务 4–10 并行        |
| 12   | 4–11 全部汇合 | 跨层静态契约与严格类型检查收敛                 | 独立顺序执行            |
| 13   | 12            | 全部测试与指令文件                             | 最后独立执行            |

执行批次：

1. 批次 A：任务 1。
2. 批次 B：任务 2、3 并行；全部通过后汇合。
3. 批次 C：任务 4–11 进入就绪队列；受四个总并发槽限制，协调者每次最多派发三个，实现完成一个再补充一个。
4. 批次 D：任务 12。
5. 批次 E：任务 13。

协调者在没有原生工作树工具时使用以下后备流程；`.worktrees/example` 必须先通过 `git check-ignore -q .worktrees/example`。任务 2、3 使用：

```bash
BASE_COMMIT=$(git rev-parse HEAD)
for task_number in 2 3; do
  git worktree add ".worktrees/draft-tests-task-$task_number" \
    -b "test/draft-tests-task-$task_number" "$BASE_COMMIT"
done
```

汇合检查点 1 通过后，任务 4–11 使用新的共同基线：

```bash
BASE_COMMIT=$(git rev-parse HEAD)
for task_number in $(seq 4 11); do
  git worktree add ".worktrees/draft-tests-task-$task_number" \
    -b "test/draft-tests-task-$task_number" "$BASE_COMMIT"
done
```

每个任务只提交自己的文件。协调者分别使用以下命令按编号拣选分支顶端的唯一任务提交：

```bash
for task_number in 2 3; do
  git cherry-pick "test/draft-tests-task-$task_number"
done

for task_number in $(seq 4 11); do
  git cherry-pick "test/draft-tests-task-$task_number"
done
```

两个循环分别只能在对应批次全部完成后执行。任务 2、3 汇合后必须重新运行两侧收集门禁；任务 4–11 汇合后必须重新运行两侧全部单元测试和非联网集成测试。只有汇合门禁通过，才能进入下一批次。

---

## Task 1: 建立 Draft 子树测试指令和 Claude 入口

**Files:**

- Modify: `.gitignore`
- Preserve/Modify: `AGENTS.md`
- Create: `plugins/draft/AGENTS.md`
- Create symlink: `plugins/draft/CLAUDE.md -> AGENTS.md`

**Interfaces:**

- Produces: 后续任务必须读取的 Draft 子树测试规范。
- Produces: Claude Code 可发现且与 `AGENTS.md` 完全同源的软链接。
- Produces: 可安全容纳后备工作树的已忽略 `.worktrees/` 目录。

- [ ] **Step 1: 审核根目录待提交测试规范并配置工作树忽略规则**

Run:

```bash
git diff -- AGENTS.md
rg -n '^\.worktrees/$' .gitignore || true
```

Expected: `AGENTS.md` 若有差异，只包含已批准的“测试规范”章节和与其直接相关的去重；不得包含其他新改动。当前 `.gitignore` 尚无 `.worktrees/`，追加且只追加这一行，然后运行：

```bash
git check-ignore -q .worktrees/example
```

Expected: 命令退出 `0`。

- [ ] **Step 2: 创建 Draft 子树指令**

按以下完整内容创建 `plugins/draft/AGENTS.md`：

````markdown
# AGENTS.md

本文件适用于 `plugins/draft/` 子树。执行本子树任务前，先读取仓库根目录
`AGENTS.md`，再读取本文件。

## 测试布局

- 核心测试位于 `skills/draft/scripts/runtime/tests/`，插件适配层测试位于
  `scripts/tests/`；两侧都只使用 `unit/` 和 `integration/` 两个顶层分类。
- `network` 是集成测试的执行属性，不是第三类测试。它只标记依赖外部索引、
  依赖解析或下载、冷 uv 缓存的测试。
- 除解释器导入被测源码外，单元测试不得访问真实文件系统、启动子进程或修改
  进程级环境；涉及这些边界的用例必须放入 `integration/`。
- 核心和适配层测试必须分开运行，不得在一条 pytest 命令中同时收集两个测试树。

## 测试替身

- 本子树采用偏模拟派策略。测试替身必须受真实接口约束，并放在具有独立语义的
  协作者边界；不要把私有调用图复制为交互断言。
- 适配层单元测试可以替换适配层拥有的验证、持久化、输出和非确定性协作者；真实
  所有权、权限、符号链接、锁、原子写和清理失败必须保留集成测试。
- 适配层生产代码只依赖标准库且不得导入核心 `draft` 包；核心不得导入插件适配层。

## 测试命令

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime

uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q

uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'

uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```

目录迁移完成前，使用当前实施计划为迁移任务指定的定向命令；迁移汇合后，以上命令
成为稳定门禁。
````

- [ ] **Step 3: 创建并验证 Claude 软链接**

Run:

```bash
ln -s AGENTS.md plugins/draft/CLAUDE.md
test -L plugins/draft/CLAUDE.md
test "$(readlink plugins/draft/CLAUDE.md)" = "AGENTS.md"
cmp -s plugins/draft/AGENTS.md plugins/draft/CLAUDE.md
```

Expected: 四条命令退出 `0`；`readlink` 只输出 `AGENTS.md`。

- [ ] **Step 4: 验证范围并提交基线**

Run:

```bash
git diff --check
git status --short
git add .gitignore AGENTS.md plugins/draft/AGENTS.md plugins/draft/CLAUDE.md \
  docs/superpowers/plans/2026-08-06-draft-test-suite-restructuring.md
git diff --cached --check
git commit -m "docs(draft): define subtree testing workflow"
```

Expected: 提交只包含当前计划、工作树忽略规则、已批准的根测试原则、Draft 子树指令和软链接。此提交是所有并行工作树的初始基线。

---

## Task 2: 建立核心测试分类边界并保持既有覆盖

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/conftest.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/conftest.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/support/__init__.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/support/paths.py`
- Move: 当前核心测试模块，归属见下方分类。

**Interfaces:**

- Produces: 默认只收集 `tests/unit/` 的核心 pytest 配置。
- Produces: 任务 4、5、6、10 使用的稳定核心测试目录和路径助手。

**行为契约：** 迁移前后的既有用例总数保持 378；迁移本身不改变任何测试断言。

**分类：**

- 原样迁入单元测试：`test_diagnostics.py`（78）、`test_markdown.py`（32），合计 110。
- 原样迁入集成测试：其余 14 个模块，合计 268。

- [ ] **Step 1: 记录迁移前收集基线**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests" --collect-only -q
```

Expected: 最后一行是 `378 tests collected`。若实际计数不同，停止并更新计划中的计数和分类，不得忽略。

- [ ] **Step 2: 机械迁移测试和夹具**

将 `test_diagnostics.py`、`test_markdown.py` 移到 `tests/unit/`。将 `test_bootstrap.py`、`test_cache_isolation.py`、`test_checker.py`、`test_cli.py`、`test_dependency_bounds.py`、`test_distribution.py`、`test_identity.py`、`test_lifecycle.py`、`test_operations.py`、`test_paths.py`、`test_structure.py`、`test_trash.py`、`test_trash_lifecycle.py`、`test_trash_package.py` 移到 `tests/integration/`。

将原 `conftest.py` 中 `DraftFactory`、`write_raw_journal`、`build_valid_candidate`、`add_valid_child`、`snapshot_tree` 的完整定义移动到 `tests/integration/support.py`；`conftest.py` 只保留 pytest 夹具（fixture；pytest 术语保留英文）。所有直接导入改为：

```python
from tests.integration.support import (
    DraftFactory,
    add_valid_child,
    build_valid_candidate,
    snapshot_tree,
    write_raw_journal,
)
```

- [ ] **Step 3: 建立共享路径助手和 pytest 配置**

创建 `tests/support/paths.py`：

```python
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
CONSTRAINTS_ROOT = RUNTIME_ROOT / "tests" / "constraints"
```

将散落的运行时根路径计算改为导入该常量。pytest 配置固定为：

```toml
[tool.pytest.ini_options]
testpaths = ["tests/unit"]
addopts = ["--strict-markers"]
pythonpath = ["."]
markers = [
    "network: 需要外部依赖解析、下载或冷 uv 缓存的集成测试",
]
```

- [ ] **Step 4: 验证迁移完整性**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" --collect-only -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" --collect-only -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q
(cd "$RUNTIME" && uv run --locked pyright)
```

Expected: 分别收集 110 和 268 个测试，全部通过；核心 Pyright 无诊断。

- [ ] **Step 5: 提交独立分支**

```bash
git add plugins/draft/skills/draft/scripts/runtime/pyproject.toml \
  plugins/draft/skills/draft/scripts/runtime/tests
git commit -m "test(draft): separate core unit and integration suites"
```

---

## Task 3: 建立适配层测试分类边界并消除入口导入歧义

**Files:**

- Create: `plugins/draft/scripts/pytest.ini`
- Create: `plugins/draft/scripts/pyrightconfig.json`
- Delete: `plugins/draft/scripts/tests/conftest.py`
- Create: `plugins/draft/scripts/tests/__init__.py`
- Create: `plugins/draft/scripts/tests/unit/__init__.py`
- Create: `plugins/draft/scripts/tests/integration/__init__.py`
- Create: `plugins/draft/scripts/tests/integration/conftest.py`
- Create: `plugins/draft/scripts/tests/support/__init__.py`
- Create: `plugins/draft/scripts/tests/support/paths.py`
- Create: `plugins/draft/scripts/tests/support/entry.py`
- Move: 当前五个适配层测试模块到 `tests/integration/`。

**Interfaces:**

- Produces: 默认只收集 `tests/unit/` 的适配层 pytest 配置。
- Produces: 覆盖入口、适配包和测试的适配层 Pyright 严格类型检查配置。
- Produces: `load_plugin_entry() -> ModuleType`，供任务 9 使用。

**行为契约：** 全部 385 个既有用例继续由集成套件收集；即使核心 `draft` 包已在 `sys.modules` 中，入口测试仍加载插件 `scripts/draft.py`。

- [ ] **Step 1: 记录迁移前收集基线**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests --collect-only -q
```

Expected: `385 tests collected`。

- [ ] **Step 2: 写入口冲突回归测试并确认红灯**

```python
def test_plugin_entry_loader_does_not_reuse_core_draft_package() -> None:
    import draft as core_package

    loaded = load_plugin_entry()

    assert loaded is not core_package
    assert loaded.__file__ == str(ENTRY_FILE)
    assert callable(loaded.dispatch_plugin_command)
```

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/test_cli.py \
  -q -k plugin_entry_loader_does_not_reuse_core_draft_package
```

Expected: 因 `load_plugin_entry` 尚不存在而失败。

- [ ] **Step 3: 创建路径常量和入口加载器**

创建 `tests/support/paths.py`：

```python
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = SCRIPTS_ROOT.parent
ENTRY_FILE = SCRIPTS_ROOT / "draft.py"
RUNTIME_ROOT = PLUGIN_ROOT / "skills" / "draft" / "scripts" / "runtime"
```

创建 `tests/support/entry.py`：

```python
import sys
from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType

from tests.support.paths import ENTRY_FILE


def load_plugin_entry() -> ModuleType:
    spec = spec_from_file_location("draft_plugin_entry_under_test", ENTRY_FILE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load plugin entry: {ENTRY_FILE}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module
```

- [ ] **Step 4: 机械迁移并配置 pytest**

将 `test_bindings.py`、`test_bridge.py`、`test_cli.py`、`test_distribution.py`、`test_session_hook.py` 移入 `tests/integration/`。将 `workspace` 夹具和导入路径设置移入 `tests/integration/conftest.py`。

创建 `plugins/draft/scripts/pytest.ini`：

```ini
[pytest]
testpaths = tests/unit
addopts = --strict-markers
pythonpath = .
markers =
    network: 需要外部依赖解析、下载或冷 uv 缓存的集成测试
```

创建 `plugins/draft/scripts/pyrightconfig.json`：

```json
{
  "include": ["draft.py", "adapter", "tests"],
  "extraPaths": ["."],
  "pythonVersion": "3.12",
  "typeCheckingMode": "strict"
}
```

该配置会在当前基线暴露 14 个既有严格类型错误；它们由汇合后的任务 12 集中修复，不在本迁移任务中跨范围修改，也不阻塞任务 7–9 的并行执行。

- [ ] **Step 5: 验证迁移与入口回归**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration --collect-only -q
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q
```

Expected: 收集 386 个测试（385 个既有测试加 1 个回归测试），全部通过。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/scripts/pytest.ini plugins/draft/scripts/pyrightconfig.json \
  plugins/draft/scripts/tests
git commit -m "test(draft): separate adapter unit and integration suites"
```

---

## 汇合检查点 1：汇合任务 2 和任务 3

协调者按任务编号拣选两个提交，然后运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" --collect-only -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" --collect-only -q
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration --collect-only -q
git diff --check
```

Expected: 分别收集 110、268、386 个测试；无导入冲突和空白错误。将汇合提交记录为任务 4–11 的共同 `BASE_COMMIT`。

---

## Task 4: 为核心解析和检查函数建立内存单元测试

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**Interfaces:**

- Tests: `parse_marker_value`、`is_external_destination`、`resolve_local_destination`、`check_graph` 和 Markdown 字符串转换。
- Produces: 不依赖真实文件系统的图规则回归套件。

**行为契约：** 合法、非法和边界输入产生完整值或稳定 `DraftError`；`check_graph` 只根据内存 `DraftGraph` 返回全部问题，并保持确定顺序。

- [ ] **Step 1: 添加图解析矩阵**

覆盖注册标记、临时接管标记、布尔/不支持版本、额外/缺失字段、非法清单路径、外部协议方案（scheme）、本地相对路径、百分号解码、锚点、绝对路径和根逃逸。

```python
def test_resolve_local_destination_decodes_and_normalizes() -> None:
    assert resolve_local_destination(
        Path("pages/a.md"), "../conclusions%2Emd#结论", Path("/unused")
    ) == (Path("conclusions.md"), "结论")


def test_resolve_local_destination_rejects_root_escape() -> None:
    with pytest.raises(DraftError) as caught:
        resolve_local_destination(Path("index.md"), "../outside.md", Path("/unused"))
    assert caught.value.code == "link.escape"
```

- [ ] **Step 2: 建立局部内存图构造器**

构造器只放在 `test_checker.py`，接受 `pages`、`parents`、`children`、`inbound_links`、`relations`，缺省映射使用 `None` 后在函数内归一为空映射；不得放入全局 `conftest.py`。

- [ ] **Step 3: 按规则分支覆盖 check_graph**

分别覆盖父子互反、缺失父页、依赖环、重复关系、取代方向与状态、质疑互反、必需产出、容量提醒和稳定排序。至少一个测试同时构造两个独立错误并比较完整 `CheckIssue` 序列。

- [ ] **Step 4: 执行变异证明**

临时让 `check_graph` 漏掉一个已知问题，确认对应测试失败后恢复；再临时丢弃 `resolve_local_destination` 的锚点，确认锚点测试失败后恢复。确认 `git diff -- plugins/draft/skills/draft/scripts/runtime/src/draft` 为空。

- [ ] **Step 5: 运行并审核重复集成用例**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_graph.py" \
         "$RUNTIME/tests/unit/test_checker.py" \
         "$RUNTIME/tests/unit/test_markdown.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
```

只删除纯粹重复内存图规则、且不涉及 Markdown 解析、发现、路径安全、宽容加载或操作状态的集成用例。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/skills/draft/scripts/runtime/tests/unit \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py
git commit -m "test(draft): add in-memory graph rule units"
```

---

## Task 5: 为核心 CLI、时间和不可变状态转换建立单元测试

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_lifecycle.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_paths.py`
- Modify: 同名核心集成测试文件。

**Interfaces:**

- Tests: `build_parser`、`_dispatch`、CLI 载荷/退出码映射、`provisional_marker_bytes`、`TrashDeadlines`、journal（操作日志对象）不可变转换、`physical_parent` 和 `relative_markdown_destination`。

**行为契约：** 纯转换直接断言完整返回值；CLI 使用带接口约束的替身证明正确分派，不创建真实 draft 目录；journal 转换保留全部无关字段。

- [ ] **Step 1: 添加纯函数合法、非法和边界矩阵**

覆盖 UTC、无时区 datetime（naive datetime）、非 UTC、期限序列化/解析、每个不一致期限字段、完整/不完整移交元数据、operation ID、journal 阶段/元数据转换、相邻/父级/空格/锚点链接和临时标记稳定字节。

```python
def test_trash_deadlines_freeze_all_absolute_times() -> None:
    created = datetime(2026, 8, 6, 12, tzinfo=UTC)
    deadlines = TrashDeadlines.from_created_at(created)
    assert deadlines.to_metadata() == {
        "created_at": "2026-08-06T12:00:00Z",
        "restore_expires_at": "2026-08-13T12:00:00Z",
        "purge_eligible_at": "2026-09-12T12:00:00Z",
        "next_handoff_attempt_at": "2026-08-13T12:00:00Z",
    }
```

- [ ] **Step 2: 用受约束替身覆盖 CLI 分派**

```python
def test_dispatch_init_calls_initialize_and_returns_success() -> None:
    args = cli.build_parser().parse_args(["init", "/draft"])

    with patch("draft.lifecycle.initialize", autospec=True) as initialize:
        payload, human, exit_code = cli._dispatch(args)

    initialize.assert_called_once_with(Path("/draft"))
    assert payload == {"result_type": "init", "root": "/draft"}
    assert human == "已初始化草稿：/draft"
    assert exit_code == 0
```

为 `_dispatch` 的每个分支各写一个测试；`check` 由 `main` 拥有，单独通过 `main` 测试。替换函数定义所在模块的名称，不假设延迟导入已成为 `draft.cli` 属性。

直接访问 `_dispatch`、`_operation_payload` 等具有独立映射契约的私有函数时，只在对应访问行添加带中文理由的 `reportPrivateUsage` 豁免；不得对整个测试模块关闭私有访问检查。

- [ ] **Step 3: 限制交互断言**

不可变转换比较完整 dataclass（数据类）对象，不断言内部助手调用。CLI 只在重复调用会造成重复写入时断言恰好一次；不冻结参数解析器构建顺序或格式化助手调用。

- [ ] **Step 4: 执行变异证明**

临时让 `with_phase` 丢失 metadata，确认保留字段测试失败后恢复；临时将一个命令路由到错误协作者，确认对应分派测试失败后恢复。

- [ ] **Step 5: 运行并保留跨边界覆盖**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_cli.py" \
         "$RUNTIME/tests/unit/test_lifecycle.py" \
         "$RUNTIME/tests/unit/test_operations.py" \
         "$RUNTIME/tests/unit/test_paths.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
```

保留文件耐久性、恢复、符号链接拒绝、磁盘解析、真实标准输出/标准错误和多模块组合集成用例。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/skills/draft/scripts/runtime/tests/unit \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_paths.py
git commit -m "test(draft): isolate core command and state units"
```

---

## Task 6: 为核心结构规划和回收规划建立内存单元测试

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**Interfaces:**

- Tests: `plan_path_remap`、`plan_move`、`plan_promote`、`plan_collapse`、`trash_plan`、`require_retained_restore_window`。
- 保留在集成测试：读取活动文件（live files；边界术语保留英文）的 `build_restore_plan`。

**行为契约：** 给定内存 `DraftGraph` 和显式时间，规划函数产生确定的完整 `OperationPlan` 或稳定错误；单元测试不执行计划、不写 journal、不读取删除包。

- [ ] **Step 1: 建立任务局部图场景**

每个测试在本文件中明确展示相关 `pages`、`parents`、`children` 和 `links`。允许两个测试模块各自保留小型构造器，不引入跨任务共享文件。

- [ ] **Step 2: 覆盖结构规划状态**

分别覆盖叶页移动、子树移动、入站链接重定向、缺失源、非法目标、移入后代、选定子页升格、未选子页保留、空枢纽页（hub）折叠、非空/非枢纽页拒绝、mutation（变更项）确定排序和未变页面省略。比较完整 `OperationPlan`。

- [ ] **Step 3: 覆盖回收规划状态**

分别覆盖父、子、依赖、取代、质疑、产出阻碍；合法 trash 规划；导航和链接重写；恢复窗口之前、恰好边界和之后；确定候选排序和稳定错误码。UUID 通过显式非确定性边界替换为固定值。

- [ ] **Step 4: 执行变异证明**

临时从 `plan_path_remap` 丢弃一个 mutation，确认完整规划测试失败后恢复；临时改变恢复窗口边界比较符，确认边界测试失败后恢复。

- [ ] **Step 5: 运行并保留真实恢复覆盖**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_structure.py" \
         "$RUNTIME/tests/unit/test_trash.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
```

保留 Markdown 文件写入、operation 执行/恢复、文件身份、真实删除包和 Send2Trash 集成用例。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/skills/draft/scripts/runtime/tests/unit \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
git commit -m "test(draft): add in-memory planning units"
```

---

## Task 7: 为适配层错误、会话绑定和桥接建立单元测试

**Files:**

- Create: `plugins/draft/scripts/tests/unit/test_errors.py`
- Create: `plugins/draft/scripts/tests/unit/test_bindings.py`
- Create: `plugins/draft/scripts/tests/unit/test_bridge.py`
- Modify: `plugins/draft/scripts/tests/integration/test_bindings.py`
- Modify: `plugins/draft/scripts/tests/integration/test_bridge.py`

**Interfaces:**

- Tests: `AdapterError`、`error_payload`、`render_error`、会话标识验证、`binding_key`/`binding_path`、`bridge_paths`、`_parse_data_dir` 和可隔离的 `bind` 编排分支。

**行为契约：** 纯值函数覆盖合法、非法和 UTF-8 边界；编排测试替换适配层拥有的独立验证/持久化协作者；真实权限、锁和原子写安全矩阵保留集成测试。

- [ ] **Step 1: 添加纯函数矩阵**

覆盖文本/JSON 错误、`cleanup.*` 次要详情、合法会话标识、空值、控制字符、孤立代理码位、长度边界、会话绑定哈希/路径、安装键和桥接路径。

- [ ] **Step 2: 用受约束替身覆盖 bind 编排**

精确替换 `_require_data_dir`、`_canonical_root`、`_require_hint_current`、`_ensure_container`、`_require_safe_target_for_overwrite` 和 `_write_binding_atomic`，并对真实函数使用 `create_autospec`。保留 `binding_path`、`_lexical_hint` 和 `_binding_json` 的真实纯计算。优先断言返回 `Binding` 和交给 `_write_binding_atomic` 的完整序列化载荷；因为重复耐久写是可见副作用，只对该写入断言恰好一次。

- [ ] **Step 3: 覆盖桥接路径和载荷解析**

对 `bridge_paths` 只替换 `installation_key` 和缺省路径分支中的 `per_user_runtime_dir`，比较完整 `BridgePaths`。对 `_parse_data_dir` 只替换 `_canonical_data_dir`，覆盖非对象、字段集合错误、布尔版本、错误版本、非字符串目录和有效载荷。`installation_key` 的真实路径解析，以及 `publish_context`、`load_context`、`reset_context` 的不存在/有效/冲突路由继续由集成测试验证；不得为把它们塞入单元测试而模拟 `Path.mkdir`、`Path.read_text`、锁或 `os.stat` 调用图。

- [ ] **Step 4: 执行变异证明**

临时让 `session_id_is_valid` 接受孤立代理码位，确认边界测试失败后恢复；临时跳过一次 binding 持久化，确认编排测试失败后恢复；临时让 `_parse_data_dir` 接受布尔版本，确认载荷测试失败后恢复。

- [ ] **Step 5: 运行并审核重复项**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/unit/test_errors.py \
         plugins/draft/scripts/tests/unit/test_bindings.py \
         plugins/draft/scripts/tests/unit/test_bridge.py -q
```

只删除纯渲染或纯哈希重复项；保留全部真实路径、所有权、权限、锁、原子替换和清理失败集成矩阵。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/scripts/tests/unit/test_errors.py \
  plugins/draft/scripts/tests/unit/test_bindings.py \
  plugins/draft/scripts/tests/unit/test_bridge.py \
  plugins/draft/scripts/tests/integration/test_bindings.py \
  plugins/draft/scripts/tests/integration/test_bridge.py
git commit -m "test(draft): isolate binding and bridge units"
```

---

## Task 8: 为 `SessionStart` 钩子建立函数级单元测试

**Files:**

- Create: `plugins/draft/scripts/tests/unit/test_session_hook.py`
- Modify: `plugins/draft/scripts/tests/integration/test_session_hook.py`

**Interfaces:**

- Tests: `read_hook_payload`、渲染函数（render functions；代码类别保留英文）和 `run_session_start`。

**行为契约：** Hook 对所有上下文和协议错误输出模型可见诊断并返回 `0`；八步控制流在稳定协作者边界可单元验证，真实文件布局和日志文件仍由集成测试负责。

- [ ] **Step 1: 添加载荷和渲染纯函数矩阵**

使用 `io.BytesIO` 覆盖有效 JSON、UTF-8 错误、孤立代理码位不可表示状态、类型错误、大小边界、宿主/来源/会话验证，以及会话事实、重新绑定、诊断渲染和允许的 `cleanup.*` 详情。

- [ ] **Step 2: 建立受接口约束的编排替身**

为 `resolve_host_context`、`publish_context`、`read_hook_payload`、`load_binding`、会话绑定当前性检查、渲染函数、`log_hook`、`_write_stdout` 使用基于真实签名的替身。助手只返回这些替身，不记录完整私有调用图。

- [ ] **Step 3: 覆盖八步控制流分支**

分别覆盖未绑定、当前会话绑定、根失效、提示失效、桥接发布后载荷无效、上下文失败阻止后续工作、主错误附带 `cleanup.*`、日志失败追加诊断、输出失败语义。只对“上下文校验早于桥接、桥接早于载荷”断言顺序。

- [ ] **Step 4: 执行变异证明**

临时丢弃 `render_diagnostic` 的 `cleanup.*` 详情，确认测试失败后恢复；临时交换桥接和载荷读取顺序，确认顺序测试失败后恢复。

- [ ] **Step 5: 运行并保留真实边界覆盖**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/unit/test_session_hook.py -q
```

保留环境变量、真实插件布局、标准输入/标准输出字节边界、日志目标、符号链接和权限集成测试。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/scripts/tests/unit/test_session_hook.py \
  plugins/draft/scripts/tests/integration/test_session_hook.py
git commit -m "test(draft): isolate session hook units"
```

---

## Task 9: 为插件 CLI 建立函数级单元测试

**Files:**

- Create: `plugins/draft/scripts/tests/unit/test_cli.py`
- Modify: `plugins/draft/scripts/tests/integration/test_cli.py`

**Interfaces:**

- Consumes: 任务 3 的 `load_plugin_entry() -> ModuleType`。
- Tests: `dispatch_plugin_command`、`_extract_json_for_ordinary`、`_dispatch_ordinary` 和输出选择。

**行为契约：** 不启动子进程即可证明 `hook`/`reset`/`bind`/`unbind` 路由、人类可读/JSON 输出和 `0/2/4` 退出语义；真实 PEP 723 入口和标准输出/标准错误进程行为保留集成测试。

- [ ] **Step 1: 覆盖 `--json` 归一化**

分别覆盖子命令之前、之后、末尾、重复和非法位置；比较完整清理后的参数序列，不调用桥接层。

- [ ] **Step 2: 用入口加载器覆盖路由**

通过 `load_plugin_entry()` 加载模块，对入口模块已经导入的协作者使用 `autospec` 替身。分别验证 `_session-start`、`plugin-context reset`、`bind`、`unbind`、参数错误、上下文/持久化错误和防御性未知路由。

- [ ] **Step 3: 限制交互断言**

只断言选中了正确外部协作者、危险副作用没有重复、提前错误没有加载桥接层；不冻结 `argparse` 内部构建和私有输出助手调用顺序。

- [ ] **Step 4: 执行变异证明**

临时将 `unbind` 路由到 `bind`，确认对应测试失败后恢复；临时把运行错误退出码改为 `2`，确认退出语义测试失败后恢复。

- [ ] **Step 5: 运行并审核重复项**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/unit/test_cli.py -q
```

保留子进程入口、PEP 723、真实标准输出/标准错误和复制插件布局集成测试。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/scripts/tests/unit/test_cli.py \
  plugins/draft/scripts/tests/integration/test_cli.py
git commit -m "test(draft): isolate plugin cli units"
```

---

## Task 10: 修复核心依赖递归并优化慢集成测试

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_dependency_bounds.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_bootstrap.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cache_isolation.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_smoke.py`

**Interfaces:**

- Produces: 可用 `-m network` 单独选择的核心慢集成测试。
- Preserves: 不同核心运行时版本使用物理独立 Python 环境的全部强身份断言。

**行为契约：** 依赖上下界只嵌套运行 `tests/unit`；冷缓存测试与非联网集成测试可分离；uv 版本门禁不在每个用例重复执行。

- [ ] **Step 1: 固化当前递归缺陷证据**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
rg -n 'IGNORED_INTEGRATION_TESTS|pytest|tests' \
  "$RUNTIME/tests/integration/test_dependency_bounds.py"
```

Expected: 当前嵌套命令以 `tests` 为目标，却没有排除 `test_cache_isolation.py`。

- [ ] **Step 2: 用显式单元目录替代忽略清单**

删除 `IGNORED_INTEGRATION_TESTS`，让最低和最高依赖边界进程都只执行：

```text
pytest tests/unit -q
```

元数据探针（metadata probe；测试术语保留英文）和单元套件继续共享同一缓存。

- [ ] **Step 3: 标记联网测试并提升 uv 门禁作用域**

对以下精确用例标记 `network`：

- `test_lowest_bound_probe_and_domain_suite_share_cache`
- `test_highest_available_probe_and_domain_suite_share_cache`
- `test_version_reports_runtime_version_from_copy`
- `test_init_and_check_pass_without_plugin_files_or_repo_parents`
- `test_uvx_resolves_from_pyproject_when_lock_absent`
- `test_offline_run_reuses_online_environment_snapshot`
- `test_distinct_releases_share_cache_without_corruption`
- `test_isolated_uvx_ignores_conflicting_global_tool`

`test_missing_interpreter_returns_uv_error_not_draft_json` 和 `test_version_gate_rejects_unsupported_versions` 不访问索引，保留为非联网集成测试。将 `uv_version_gate` 改为 `scope="module"` 且 `autouse=True`，让 uv/uvx 版本子进程探针每个模块只运行一次；可写运行时副本和缓存仍保持函数作用域。

- [ ] **Step 4: 保留并精简环境隔离证据**

必须继续证明：两个版本并发执行；第二层引用不同；真实目录和索引节点（inode；文件系统术语保留英文）不同；版本集合恰为 `{0.3.0, 0.3.1}`；再次运行 `0.3.0` 仍报告 `0.3.0`。只有这些强断言全部保留时才删除冗余 `archive_refs >= 2`。

- [ ] **Step 5: 拆分发布契约和安装冒烟测试**

将原 `test_distribution.py` 的全部静态清单、目录布局、抽象语法树、导入边界和 ruff 配置发现测试原样移入 `test_distribution_contract.py`。将原 `test_bootstrap.py` 的四个 uvx 用例及其局部助手原样移入 `test_distribution_smoke.py`；其中三个成功安装用例按步骤 3 标记 `network`，缺失解释器用例保持非联网。拆分时不复制助手，也不改变断言。

- [ ] **Step 6: 运行分组测试**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network' --durations=20
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network --durations=20
(cd "$RUNTIME" && uv run --locked pyright)
```

Expected: 两组全部通过，核心 Pyright 无诊断；依赖边界不再递归收集 `test_cache_isolation`。

- [ ] **Step 7: 提交独立分支**

```bash
git add plugins/draft/skills/draft/scripts/runtime/tests/integration
git commit -m "test(draft): isolate core network integration work"
```

---

## Task 11: 拆分并优化适配层发布集成测试

**Files:**

- Delete: `plugins/draft/scripts/tests/integration/test_distribution.py`
- Create: `plugins/draft/scripts/tests/integration/test_distribution_contract.py`
- Create: `plugins/draft/scripts/tests/integration/test_distribution_smoke.py`

**Interfaces:**

- Produces: 静态插件发布契约测试和可单独选择的联网安装测试。

**行为契约：** 宿主无关（host-independent；测试维度保留英文）的运行时冷安装只执行一次；Codex 和 Claude 共享同一份只读插件分发副本，但各自使用独立的数据目录、运行目录和草稿根。宿主测试分别验证环境变量名、Hook JSON 和会话绑定流程，不重复执行 uvx。

- [ ] **Step 1: 记录现有四个慢用例耗时**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration/test_distribution.py \
  -q --durations=20
```

Expected: 输出列出当前 uvx 冒烟用例（smoke cases；测试术语保留英文）的耗时基线。

- [ ] **Step 2: 拆分静态契约和冒烟测试**

插件清单、Hook 命令、复制布局、抽象语法树和导入边界移入 `test_distribution_contract.py`。在 `test_distribution_smoke.py` 中建立两个明确场景：

- `test_runtime_distribution_online_then_offline`：只运行一次，标记 `network`；在同一来源路径和缓存上用 `online-root` 验证在线 `--version`/`init`/`check`，再用独立的 `offline-root` 以离线模式重复这些命令，避免第二次 `init` 命中已有草稿。
- `test_host_plugin_flow`：以 `codex`、`claude` 参数化，不标记 `network`；只验证各宿主的 SessionStart、`bind`、`unbind`、日志、桥接和数据目录隔离，不执行 uvx。

- [ ] **Step 3: 消除宿主间重复冷安装**

使用 `tmp_path_factory` 建立模块作用域的 `distribution_copy` 夹具，只复制一次普通插件目录；该副本的插件入口、技能目录和核心运行时路径在三个场景中完全相同且不得被测试修改。再建立模块作用域的 `host_cases` 夹具，同时为两个宿主创建独立的 `data_dir`、安全运行目录和草稿根，参数化测试只从该映射选取当前宿主。`test_runtime_distribution_online_then_offline` 使用同一分发副本下的唯一缓存。不得给两个宿主分别复制核心运行时后仅共享 `UV_CACHE_DIR`，因为来源路径不同会产生不同的 uv 工具环境，无法消除重复安装。

- [ ] **Step 4: 验证宿主隔离仍成立**

两个宿主用例必须使用各自 `PLUGIN_*` 与 `CLAUDE_PLUGIN_*` 变量；断言一个宿主的数据目录、日志、会话绑定和桥接产物不出现在另一个宿主的目录中。不得把插件数据目录（`data_dir`，代码标识保留英文）合并为用户级全局 Draft 目录。

- [ ] **Step 5: 运行分组测试**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network' --durations=20
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network --durations=20
```

Expected: 两组全部通过；联网组只收集一个运行时冒烟测试，非联网组收集两个宿主流程实例和静态契约测试，冷运行时构建不再按宿主重复。

- [ ] **Step 6: 提交独立分支**

```bash
git add plugins/draft/scripts/tests/integration
git commit -m "test(draft): isolate adapter distribution smoke tests"
```

---

## 汇合检查点 2：汇合任务 4–11

协调者按任务编号顺序拣选所有提交。每次拣选后运行该任务自己的定向命令；全部拣选后运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
git diff --check
```

Expected: 四组全部通过。若拣选提交时出现同一文件冲突，说明任务违反文件所有权；停止汇合并回到对应任务修正，不在协调分支临时拼接两套实现。

---

## Task 12: 清理脆弱变更探测器并收敛严格类型检查

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py`
- Modify: `plugins/draft/scripts/adapter/bindings.py`
- Modify: `plugins/draft/scripts/adapter/session_hook.py`
- Modify: `plugins/draft/scripts/tests/integration/test_distribution_contract.py`
- Modify: `plugins/draft/scripts/tests/integration/test_bridge.py`
- Modify: `plugins/draft/scripts/tests/integration/test_cli.py`
- Modify if required: `plugins/draft/scripts/tests/unit/test_errors.py`
- Modify if required: `plugins/draft/scripts/tests/unit/test_bindings.py`
- Modify if required: `plugins/draft/scripts/tests/unit/test_bridge.py`
- Modify if required: `plugins/draft/scripts/tests/unit/test_cli.py`
- Modify: `plugins/draft/scripts/tests/unit/test_session_hook.py`
- Modify: `plugins/draft/scripts/tests/integration/test_session_hook.py`

**Interfaces:**

- Consumes: 任务 8、10、11 汇合后的测试文件。
- Produces: 只冻结外部产物契约（artifact contract；测试术语保留英文）和架构边界（architecture boundary；测试术语保留英文）的静态测试。
- Produces: 适配层独立严格类型配置下无诊断的生产代码和测试代码。

**行为契约：** 测试不冻结当前源码文本、私有常量表或内部调用图；插件清单、模式和 Hook 字面量以及插件与核心依赖边界继续受保护。类型收窄只描述既有运行时检查，不增加、删除或重排生产分支。

- [ ] **Step 1: 分类每个静态断言**

将断言分为：外部产物契约、架构依赖边界、重复实现/变更探测器。保留前两类；第三类删除或替换为行为测试。用测试名和就地注释记录归属，不创建额外清单文件。

- [ ] **Step 2: 修正版本身份断言**

使用标准库 `re.fullmatch` 验证两个插件清单的语义版本格式并断言二者相等；运行时版本独立验证。不得要求插件版本等于运行时版本，也不得写死 `0.3.0`。

- [ ] **Step 3: 删除关键词扫描和跨层重复契约**

删除适配层契约测试中对 `SKILL.md`、`draft-format.md` 的否定关键词扫描。删除核心 `test_distribution_contract.py` 中读取市场清单、插件清单和 Hook 文件的四个插件专属测试及其局部 JSON 助手；这些精确产物字段只由适配层契约测试拥有。

将 `test_identity.py` 中扫描源码字符串的 `test_core_runtime_owns_no_plugin_responsibilities` 改为基于标准库 `ast` 的导入边界测试，只拒绝核心源码导入 `adapter`、`bindings`、`bridge`、`session_hook` 等插件模块；字符串、注释和错误消息不参与判断。随后删除适配层契约中重复扫描核心源码导入的 `test_no_plugin_imports_under_core_source`。保留禁止启动器文件、核心包不存在插件专属模块、适配层只依赖标准库，以及插件清单和 Hook JSON 精确字段等真正契约。

- [ ] **Step 4: 用行为矩阵取代私有常量表冻结**

当 `host`、`source`、载荷大小的接受/拒绝行为已经覆盖时，删除 `HOST_ENV`、`HOST_SOURCES`、`MAX_STDIN` 的直接等值断言。`cleanup.*` 白名单通过最终渲染行为验证，不比较私有元组。

- [ ] **Step 5: 集中修复适配层严格类型诊断**

任务 3 的独立配置在当前基线上暴露 14 个既存诊断；任务 7–9 新增测试若产生诊断，也属于本步骤范围。任务 7–9 并行时不得各自运行完整适配层检查，因为尚未汇合的其他任务会污染分支结果；在本步骤统一收敛：

- 在 `bindings.py` 的 `isinstance(raw, dict)` 检查后使用 `typing.cast` 收窄为 `dict[object, object]`，保留字段的逐项运行时检查。
- 在 `session_hook.py` 确认 `json.loads` 结果为 `dict` 后收窄为 `dict[str, object]`；不得用 `Any` 或文件级忽略绕过载荷字段检查。
- 将测试中未使用的捕获变量改为 `_`；为转发参数补全 `tuple[object, ...]`、`Mapping[str, object]`，或使用与真实协作者一致的显式签名。
- 对 `_parse_data_dir`、插件入口私有分派函数以及唯一有意直接验证 `_fsync_directory` 安全语义的访问，逐行添加带中文契约理由的 `reportPrivateUsage` 豁免；没有独立行为契约的私有助手不得因类型门禁而新增直接测试，也不得全局关闭规则。

运行适配层完整严格类型检查：

```bash
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pyright)
```

Expected: `0 errors, 0 warnings, 0 informations`。

- [ ] **Step 6: 执行变异证明和定向门禁**

临时加入一个禁止的非标准库适配层导入，确认架构测试失败后恢复；临时修改一个插件清单中的 Hook 命令，确认产物契约测试失败后恢复。

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration/test_distribution_contract.py" \
         "$RUNTIME/tests/integration/test_identity.py" -q
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/unit \
         plugins/draft/scripts/tests/integration -q -m 'not network'
(cd "$RUNTIME" && uv run --locked pyright)
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pyright)
```

- [ ] **Step 7: 提交**

```bash
git add plugins/draft/skills/draft/scripts/runtime/tests \
  plugins/draft/scripts/adapter/bindings.py \
  plugins/draft/scripts/adapter/session_hook.py \
  plugins/draft/scripts/tests
git commit -m "test(draft): keep behavior and architecture contracts"
```

---

## Task 13: 运行完整质量门禁并校准 Draft 子树指令

**Files:**

- Modify if required: `plugins/draft/AGENTS.md`
- Verify symlink: `plugins/draft/CLAUDE.md -> AGENTS.md`

**Interfaces:**

- Produces: 最终稳定测试命令、测试计数、耗时和剩余集成边界报告。

**行为契约：** 默认 pytest 只收集单元目录；完整门禁显式分开核心/适配层和联网/非联网测试；静态检查覆盖重构后的全部目录。

- [ ] **Step 1: 验证默认收集边界**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
(cd "$RUNTIME" && uv run --locked pytest --collect-only -q)
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pytest --collect-only -q)
```

Expected: 所有节点标识（node ID；pytest 输出术语保留英文）都位于 `tests/unit/`，不列出集成模块。

- [ ] **Step 2: 测量单元测试反馈时间**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
/usr/bin/time -f 'core unit %e s' \
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q --durations=20
/usr/bin/time -f 'adapter unit %e s' \
  uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q --durations=20
```

Expected: 环境预热后两侧各不超过 5 秒。超时时检查 `--durations=20`；不得通过放宽目标隐藏错误分类。

- [ ] **Step 3: 运行完整测试门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv lock --project "$RUNTIME" --check
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```

Expected: 六条 pytest 命令和 `uv.lock` 检查全部退出 `0`。

- [ ] **Step 4: 运行静态门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
(cd "$RUNTIME" && uv run --locked pyright)
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff check plugins/draft/scripts
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff format --check plugins/draft/scripts
git diff --check
```

Expected: 全部退出 `0`。

- [ ] **Step 5: 审计单元测试边界**

```bash
rg -n 'tmp_path|tmp_path_factory|subprocess|Popen|uvx|urlopen|requests|http|os\.environ|setenv|delenv|open\(|read_text|write_text|mkdir|unlink' \
  plugins/draft/skills/draft/scripts/runtime/tests/unit \
  plugins/draft/scripts/tests/unit
```

Expected: 除明确断言这些协作者没有被调用的测试名或注释外没有匹配；逐条人工检查，不用正则排除来掩盖。

- [ ] **Step 6: 校准并验证作用域指令**

比较 `plugins/draft/AGENTS.md` 中的六条稳定测试命令与本任务实际成功命令；仅在字面量不一致时修正。然后运行：

```bash
test -L plugins/draft/CLAUDE.md
test "$(readlink plugins/draft/CLAUDE.md)" = "AGENTS.md"
cmp -s plugins/draft/AGENTS.md plugins/draft/CLAUDE.md
git diff --check
```

- [ ] **Step 7: 输出验收报告并提交**

报告必须列出：核心/适配层的单元和集成测试计数、预热单元耗时、非联网/联网集成耗时、删除的精确重复用例及其替代契约、仍有意保留为集成测试的边界、所有并行任务提交和汇合结果。

```bash
git add plugins/draft/AGENTS.md plugins/draft/CLAUDE.md
git commit -m "docs(draft): finalize layered test workflow"
```

若作用域指令无须修改，不创建空提交；最终汇合提交本身即为完成点。

---

## 完成条件

- `plugins/draft/AGENTS.md` 成为 Draft 子树唯一测试维护说明，`plugins/draft/CLAUDE.md` 是指向它的正确相对软链接。
- 核心和适配层各自只以 `unit/`、`integration/` 作为顶层测试分类；`network` 只是集成执行属性。
- 默认 pytest 配置只收集单元目录。
- 单元测试同进程、确定、不访问真实文件系统或启动子进程，并围绕具有独立契约的生产函数组织。
- 测试替身受真实接口约束，不冻结无关私有调用图；所有重要真实协作关系有集成覆盖。
- 既有安全、耐久性、路径、子进程、发布、依赖边界和 uv 环境隔离行为继续由显式集成测试覆盖。
- 依赖边界套件只执行 `tests/unit`，不会递归收集慢集成测试。
- 联网集成测试可用 `-m network` 独立选择。
- 核心和适配层单元套件在预热环境中各不超过 5 秒。
- 并行任务使用独立工作树和不重叠文件所有权，两个汇合点的门禁全部通过。
- pytest、pyright、ruff、格式、锁（lock）和差异（diff）门禁全部通过。
- 没有生产行为变化，也没有把无关工作树改动带入提交。
