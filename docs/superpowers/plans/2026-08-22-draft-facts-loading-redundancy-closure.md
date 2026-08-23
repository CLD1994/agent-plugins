# Draft 事实加载冗余消除执行计划（2026-08-22）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 draft 运行时事实加载中的三类冗余：structure.py 的死参数 `now`、`DraftGraph.page_bytes` 冗余字节副本、写命令链上 marker 的重复读取（方案 B：全链状态穿透）。

**Architecture:** 三个互相独立的修复。F1/F3 是行为不变的删减；F2 在 `load_graph_tolerant` / `load_graph` / `check_draft` / `load_tolerant_view` 四个边界新增可选 `marker_state` 形参（缺省 `None` 保持现状），由持有同根 marker 事实的入口传入，使一次写命令内 marker 文件恰好读取一次且全部分类同源。

**Tech Stack:** Python 3.12+ 标准库；pytest（core/adapters 各自的 unit/integration 树）；ruff + pyright（strict）。

**Spec:** 无独立 spec 文件。契约来自 2026-08-22 会话中用户批准的设计（三项一起实施；Q2 采用方案 B 全链穿透），完整展开于本文第一节，是唯一权威。

依据的历史提交（因果链，执行者不需要读，仅供理解）：

- `598051a` 引入 `page_bytes`：当时 text 被换行规范化，与磁盘字节不同源。
- `5ea3afa` 移除换行规范化：此后 `text = data.decode("utf-8")`，`text.encode("utf-8")` 与磁盘字节逐字节相等，`page_bytes` 失去存在理由。
- `0da797e` 把生命周期前置推进集中到 CLI：structure.py 三个领域函数的 `now` 从此无人消费。
- `751846c`（round-15 H1）：MarkerSnapshot 单次读取原则——同一操作计划的 marker 事实不得来自后续 live 读取。本计划的 F2 是该原则在加载/检查边界的落实。

本计划区分：**已确认行为契约**（硬性要求）与**实现预估**（文件与步骤安排，可按实际代码结构调整，但不得改变契约）。所有步骤使用 checkbox 跟踪；发现计划与代码事实不一致、遇到计划未定义的状态、无法满足验收条件时，停止并报告具体证据，不得自行选择语义。

## Global Constraints

- 交流与注释用简体中文；代码、命令、字段名、协议字面量保留原文。
- 测试命令（来自 `plugins/draft/AGENTS.md`，核心与适配层分开运行）：
  - 核心 unit：`uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q`（`RUNTIME=plugins/draft/skills/draft/scripts/runtime`）
  - 适配层 unit：`uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q`
  - 核心 integration（非网络）：`uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'`
  - 适配层 integration（非网络）：`uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/integration -q -m 'not network'`
  - 核心 lint/类型：`uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"` 与 `uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"`（当前基线均为全绿）
- 工作区已有改动不得夹带：`AGENTS.md` 有未提交修改、`docs/review-notes/` 与 `docs/superpowers/plans/` 有未跟踪文件——本计划的提交只包含本计划列出的文件。
- 禁止用删除测试、放宽配置、吞掉错误的方式让门禁变绿；禁止把 CRLF 字节保真回归测试删除或放宽（它们是 F3 契约的守卫）。
- 提交信息以 `refactor(draft):` / `fix(draft):` 开头，主题用中文，结尾附 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。
- 最终产物只表达当前目标状态：删除 `now` / `page_bytes` 时不留"曾经存在"的注释痕迹。

---

## 一、已确认行为契约

### F1：删除 structure.py 三个结构命令的死参数 `now`

1. `move_page`、`promote_page`、`collapse_hub` 的签名删除 `now` 形参与函数体首行的 `del now`；`structure.py` 顶部的 `from datetime import datetime`（第 5 行）一并删除（仅服务这三个签名）。
2. 行为零变化：全仓库（`src/`、`scripts/`、全部测试）无任何调用方传 `now`；`cli.py:777/800/821` 与单元测试的 `assert_called_once_with` 断言均不含 `now`。
3. 禁止顺手删除其他模块的 `now` 形参：`trash.py:947/978` 与 `trash_lifecycle.py` 的 `now` 被 `normalize_utc(now)` 真实消费（保留窗口期限），是活参数。

### F3：删除 `DraftGraph.page_bytes` 冗余字节副本

1. 依据（已核实）：当前 `_load` 中 `text = data.decode("utf-8")` 且 `parse_page` 原样保存 `text`（`PageDocument(text=text)`）；严格 UTF-8 编解码无损往返，故对每个磁盘加载页 `pages[path].text.encode("utf-8") == page_bytes[path]` 逐字节相等。字段是 `5ea3afa` 之后遗留的纯冗余副本（每页内容常驻两份）。
2. 删除内容：`DraftGraph.page_bytes` 字段及其注释、`page_digest` 对字段的引用、`_load` 中的 `page_bytes` 字典与收集行、`DraftGraph(...)` 构造中的 `page_bytes=...` 行、`trash.py:199` 对 `graph.page_bytes` 的读取。
3. `page_digest` 新契约：`page_digest(path) == sha256_hex(pages[path].text.encode("utf-8"))`，对磁盘加载页与内存合成图同式成立；docstring 改写为当前语义（不存在"实际字节 vs 规范化文本"两分支）。
4. `trash.py:199` 改为 `graph.pages[page].text.encode("utf-8")`（回收包载荷与 mutation before 摘要的取值逐字节不变）。
5. `observations.py:189-190` 的过时注释更新（`getattr(graph, "page_digest", None)` 的协议容忍结构保留：`_GraphLike` 只要求 `root` 与 `pages`）。
6. 验收：所有既有 digest 值逐位不变——观察清单、mutation before 摘要、trash 包内容的既有集成测试（含 CRLF 字节保真回归）全绿；`ruff`/`pyright` 无新告警。

### F2（方案 B）：marker 状态穿透写命令链

**核心不变量（INV）**：持有同一根 marker 事实的调用方必须把状态传给图加载/检查边界；边界收到非 `None` 的 `marker_state` 时不得读取 marker 文件，受管分类（conclusions.md 所有权）与 `root_state` 门禁全部以传入状态为权威。边界缺省 `None` 时保持现状：自行读取一次，marker 损坏按既有语义转换为 issue（tolerant）或吞掉（checker 辅助）。

**边界参数决策表**（`load_graph_tolerant` / `load_graph` / `check_draft` / `load_tolerant_view` 对 `marker_state` 的求值；均为 keyword-only、缺省 `None`）：

| marker_state 实参 | marker 文件读取次数 | conclusions 受管分类 | marker issue | load_graph 的 root_state 门禁 |
|---|---|---|---|---|
| 非 None（调用方持有） | 0 | 传入状态的 conclusions_state | 无 | 传入状态的 root_state；非 registered 抛 `root.candidate` |
| None（缺省） | 恰好 1（边界自读） | 该次读取结果；读取失败 -> None -> 维持现状受管 | 读取失败 -> 记录既有 marker issue（现状） | 读取失败 -> 跳过候选检查，由 blocking 分支报出首个结构 issue（现状） |

组件契约：

1. `LoadResult` 新增字段 `marker_state: MarkerState | None = None`（带默认值，兼容既有全部构造点）；仅 `load_graph_tolerant` 填充：传入状态时为传入值，缺省自读成功时为读取结果，失败时为 `None`。`_load` 内部构造的 `LoadResult` 不填（默认 `None`）。
2. `load_graph(root, *, marker_state=None)`：删除自身的第二次 `read_marker`；root_state 门禁消费 `load_graph_tolerant(...)` 返回的 `result.marker_state`。`None`（marker 无效）分支的既有语义不变：不抛 `root.candidate`，由 blocking 分支报出 tolerant 已记录的 marker issue。
3. `check_draft(root, *, include_operation_state=True, marker_state=None)`：缺省时在函数开头恰好读取一次（`read_marker`，失败置 `None`）并贯穿全部消费者；传入时直接使用。`_conclusions_required` / `_conclusions_state` 改为纯状态函数（形参 `MarkerState | None`），语义映射不变：`None -> False` / `None -> None`。
4. `load_tolerant_view(root, *, marker_state=None)`：把状态透传给 `load_graph_tolerant`；自身其余逻辑不变。
5. **穿透点全表**（INV 的全部已知入口，必须全部接线；缺一即未闭合）：
   - `operation_facts.load_operation_facts`：`check_draft(resolved, marker_state=marker.state)`、`load_graph(resolved, marker_state=marker.state)`、`load_tolerant_view(resolved, marker_state=marker.state)`。
   - `conclusions.regenerate`：`load_tolerant_view(resolved, marker_state=marker.state)` 与 `_load_graph_with_conclusions(resolved, marker.state)`。
   - `conclusions.load_generation_graph`：`_load_graph_with_conclusions(resolved, read_marker(resolved))`。
   - `conclusions._load_graph_with_conclusions`：形参从 `state: ConclusionsState` 改为 `marker_state: MarkerState | None`，内部派生 `ConclusionsState`，并向 `load_graph_tolerant` 透传。
   - `lifecycle.adopt`：`check_draft(target, marker_state=state)` 与 `load_graph_tolerant(target, marker_state=state)`（`state = snapshot.state` 既有局部变量）。
   - `trash._build_restore_plan_impl`：`check_draft(resolved, include_operation_state=False, marker_state=marker.state)`、`load_graph(resolved, marker_state=marker.state)`、`load_tolerant_view(resolved, marker_state=marker.state)`。
   - **不接线的入口**（保持缺省自读）：`query.load_complete_snapshot`、`query.load_registered_page`、`trash.py:905` 的恢复不变量验证辅助、`lifecycle.initialize`（自写模板后的检查）、CLI `check` 命令、`trash_lifecycle` 各入口（以上均为无状态在手）；`operations._check`（`execute_plan` 提交前校验，语义上必须新鲜读取：它观察 mutations 应用后的根状态，而 GENERATE_CONCLUSIONS 会把 marker 从 not_generated 推进为 generated，穿入 pre-apply snapshot 会让提交前校验按旧所有权分类、漏检 conclusions.md 必需项。candidate 根无此调用，走摘要核对分支）。
6. 行为等价性声明：无并发修改时，全部命令的输出、错误码与产物逐位不变。唯一的语义变化是并发窗口内的一致性收紧——snapshot 后 live marker 被切换时，下游分类不再重新采信新文件（这正是 round-15 H1 的要求方向）。修复记录必须如实声明，不得表述为"无行为变更"。
7. 读取次数验收：`load_operation_facts`（candidate）与 `adopt` 两条链路的 marker snapshot 读取恰好各 1 次；`move_page`（registered）恰好 2 次（facts snapshot 1 次 + `operations._check` 提交前校验 1 次，两次为不同时点、不同目的的合法读取；新测试断言。修复前分别为 9、2、5 次）。若实测计数与计划不符（例如执行器内部另有合法读取点），停止并报告实际调用栈，不得自行调整断言值。

---

## 二、实现预估：任务分解

### Task 1：删除 now 死参数（F1）

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`

**Interfaces:**
- Produces: `move_page(root: Path, source: Path, target: Path) -> OperationResult`；`promote_page(root: Path, page: Path, children: list[Path]) -> OperationResult`；`collapse_hub(root: Path, hub: Path) -> OperationResult`（签名唯一变化：删除 keyword-only `now`）。

- [ ] **Step 1：修改 structure.py**

删除三处签名中的 `now: datetime | None = None` 与对应的 `del now  # ...` 行（`move_page` 510-512、`promote_page` 526-528、`collapse_hub` 541-542），并删除第 5 行 `from datetime import datetime`。示例（`move_page`）：

```python
def move_page(root: Path, source: Path, target: Path) -> OperationResult:
    resolved = resolve_draft_root(root)
    facts = _load_clean_graph(resolved)
    plan = plan_move(
        facts.graph,
        source,
        target,
        marker=facts.marker,
        observations=facts.observations,
    )
    return execute_plan(plan)
```

`promote_page`、`collapse_hub` 同式：仅删参数与 `del` 行，函数体其余不变。

- [ ] **Step 2：运行守卫测试**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_page_commands.py" "$RUNTIME/tests/integration/test_structure.py" -q -m 'not network'`
Expected: 全部 PASS（`tests/unit/test_cli.py` 的三个 `assert_called_once_with` 断言不含 `now`，是本契约的既有守卫）。

- [ ] **Step 3：lint/类型**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" && uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"`
Expected: `All checks passed!`；`0 errors, 0 warnings`。

- [ ] **Step 4：Commit**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py
git commit -m "refactor(draft): 删除结构命令的死参数 now

前置推进由 CLI 编排统一完成后，move/promote/collapse 的 now 形参已无任何
调用方传参，删除形参与仅服务该形参的 datetime 导入。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 2：删除 page_bytes 冗余副本（F3）

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py:199`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py:189-190`（仅注释）

**Interfaces:**
- Produces: `DraftGraph` 不再有 `page_bytes` 字段；`page_digest(path: Path) -> str` 语义为 `sha256_hex(pages[path].text.encode("utf-8"))`。消费方（structure/trash/page_metadata/page_create/conclusions/observations 经 `page_digest` 或 `text.encode()`）取值逐字节不变。

- [ ] **Step 1：修改 graph.py**

(a) `DraftGraph` 删除 `page_bytes` 字段（含两行注释），`page_digest` 改为：

```python
    def page_digest(self, path: Path) -> str:
        """页面内容摘要：加载文本保留原始字节（含 \\r\\n），编码后即磁盘字节。"""
        return _sha256_hex(self.pages[path].text.encode("utf-8"))
```

(b) `_load` 的页面读取循环改为（删除 `page_bytes` 字典声明与收集行）：

```python
    pages: dict[Path, PageDocument] = {}
    for relative in managed_paths:
        try:
            # 解析与重写基于原始解码文本保留 \r\n：编码回 UTF-8 即磁盘原始字节，
            # 机械命令只修改协议要求的跨度，未修改区域的字节表示保持不变。
            text = (root / relative).read_bytes().decode("utf-8")
            pages[relative] = parse_page(relative, text)
```

（`except` 分支保持原样。）(c) `DraftGraph(...)` 构造删除 `page_bytes={path: page_bytes[path] for path in sorted(page_bytes)},` 一行。

- [ ] **Step 2：修改 trash.py:199**

```python
    page_bytes = graph.pages[page].text.encode("utf-8")
```

（原行为 `graph.page_bytes.get(page, graph.pages[page].text.encode("utf-8"))`，两分支在新前提下逐字节相等。）

- [ ] **Step 3：更新 observations.py 注释（189-190 行）**

```python
        # 摘要基于加载文本（保留 \r\n，编码即磁盘字节）；内存合成图与磁盘图同式。
```

（`getattr(graph, "page_digest", None)` 与回退分支的结构保留，`_GraphLike` 协议只要求 `root` 与 `pages`。）

- [ ] **Step 4：运行全量核心测试**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'`
Expected: 全部 PASS。CRLF 字节保真回归（test_page_commands/test_structure/test_trash 中 5ea3afa 新增的用例）是本契约守卫，必须全绿。

- [ ] **Step 5：lint/类型（同 Task 1 Step 3 命令）**

Expected: 全绿。

- [ ] **Step 6：Commit**

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py
git commit -m "refactor(draft): 移除图加载的 page_bytes 冗余副本

5ea3afa 移除解析文本的换行规范化后，text 编码回 UTF-8 与磁盘字节逐字节
相等，page_bytes 成为纯内存冗余。摘要统一从加载文本计算，每页内容不再
常驻两份。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 3：graph 层 marker 状态穿透（F2 核心）

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`

**Interfaces:**
- Produces: `LoadResult(graph, issues, resource_facts, marker_state: MarkerState | None = None)`；`load_graph_tolerant(root: Path, *, marker_state: MarkerState | None = None) -> LoadResult`；`load_graph(root: Path, *, marker_state: MarkerState | None = None) -> DraftGraph`。Task 4/5 依赖这三个签名。
- Consumes: 既有 `read_marker` / `MarkerState` / `_load(root, conclusions_state)`。

- [ ] **Step 1：新建测试文件（红）**

```python
"""marker 状态穿透契约：持有状态的调用方传入后，边界不再读取 marker 文件。

计数方式：用计数 wrapper 包住真实 read_marker_snapshot，同时替换
draft.graph 与 draft.operation_facts 两个命名空间（后者在模块级绑定了该函数，
单独 patch draft.graph 拦截不到 facts 层调用；read_marker 及各处惰性导入
最终都落到 draft.graph.read_marker_snapshot）。真实文件系统协作者在集成层验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import draft.graph as graph_module
import draft.operation_facts as facts_module
from draft.diagnostic_codes import CheckCode
from draft.graph import FORMAT_VERSION, MarkerSnapshot, MarkerState, load_graph_tolerant
from draft.models import CheckReport, RootState
from draft.vocabulary import ConclusionsState
from tests.integration.support import DraftFactory, managed_marker_text

PAGE_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
"""

PAGE_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲。
"""


@pytest.fixture
def marker_reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """计数并放行全部 marker snapshot 读取；返回按序记录的根路径。"""
    real = graph_module.read_marker_snapshot
    calls: list[Path] = []

    def counting(root: Path) -> MarkerSnapshot:
        calls.append(root)
        return real(root)

    monkeypatch.setattr(graph_module, "read_marker_snapshot", counting)
    monkeypatch.setattr(facts_module, "read_marker_snapshot", counting)
    return calls


def _registered(conclusions: ConclusionsState) -> MarkerState:
    return MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.REGISTERED,
        conclusions_state=conclusions,
    )


def test_load_graph_tolerant_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入状态：不读取 marker 文件，marker_state 原样回传，图正常返回。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    state = _registered(ConclusionsState.NOT_GENERATED)

    result = load_graph_tolerant(root, marker_state=state)

    assert marker_reads == []
    assert result.marker_state == state
    assert set(result.graph.pages) == {Path("index.md"), Path("pages/a.md")}


def test_load_graph_tolerant_classification_follows_passed_state(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """权威性：磁盘 marker 为 not_generated 时，受管分类按传入状态求值。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    (root / "conclusions.md").write_text("# 结论\n\n机器列表。\n", encoding="utf-8")

    managed = load_graph_tolerant(root, marker_state=_registered(ConclusionsState.GENERATED))
    assert Path("conclusions.md") in managed.graph.pages
    assert marker_reads == []

    unmanaged = load_graph_tolerant(root, marker_state=_registered(ConclusionsState.NOT_GENERATED))
    assert Path("conclusions.md") not in unmanaged.graph.pages
    assert any(
        issue.code == CheckCode.PAGE_UNMANAGED_PATH.value and issue.path == Path("conclusions.md")
        for issue in unmanaged.issues
    )


def test_load_graph_tolerant_passed_state_equivalent_to_self_read(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """等价：传入与磁盘一致的 state 时，issues 与 resource_facts 逐项相等。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    state = _registered(ConclusionsState.NOT_GENERATED)

    threaded = load_graph_tolerant(root, marker_state=state)
    marker_reads.clear()
    direct = load_graph_tolerant(root)

    assert threaded.issues == direct.issues
    assert threaded.resource_facts == direct.resource_facts
    assert set(threaded.graph.pages) == set(direct.graph.pages)
    assert threaded.marker_state == direct.marker_state == state
    assert marker_reads == [root]


def test_load_graph_tolerant_marker_state_none_when_disk_invalid(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """缺省自读失败：marker_state 为 None，marker issue 照常记录（现状语义）。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    (root / ".draft" / "draft.json").write_text("{broken json", encoding="utf-8")

    result = load_graph_tolerant(root)

    assert result.marker_state is None
    assert any(issue.code == CheckCode.FORMAT_INVALID.value for issue in result.issues)
    assert marker_reads == [root]
```

同文件再追加 `load_graph` 的两个用例（import 需加 `load_graph` 与 `DraftError`）：

```python
def test_load_graph_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入 registered 状态：返回图且全程不读 marker。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})

    graph = load_graph(root, marker_state=_registered(ConclusionsState.NOT_GENERATED))

    assert marker_reads == []
    assert set(graph.pages) == {Path("index.md"), Path("pages/a.md")}


def test_load_graph_rejects_candidate_from_passed_state(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """权威性：磁盘 registered + 传入 candidate -> root.candidate，不读磁盘。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    candidate = MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.CANDIDATE,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    )

    with pytest.raises(DraftError) as caught:
        load_graph(root, marker_state=candidate)

    assert caught.value.code == "root.candidate"
    assert marker_reads == []
```

（注：`CheckReport` 留给 Task 4 使用，本任务可不导入；`managed_marker_text` 留给 Task 4/5，本任务可不导入——以 ruff F401 全绿为准增删导入。）

- [ ] **Step 2：运行确认红**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_marker_state_threading.py" -q`
Expected: FAIL——旧签名不接受 `marker_state`（`TypeError: ... unexpected keyword argument 'marker_state'`），字段断言为 `AttributeError`。

- [ ] **Step 3：实现 graph.py**

(a) `LoadResult` 增加字段（放在 `resource_facts` 之后）：

```python
@dataclass(frozen=True)
class LoadResult:
    graph: DraftGraph
    issues: list[CheckIssue]
    # 正文链接目标事实（missing/invalid/unavailable）：是查询结果数据，不阻止命令。
    resource_facts: list[CheckIssue]
    # marker 一次读取的结果（None = 未传入且自读失败，详情见 issues）：供持有
    # 状态的调用方在同一次事实内完成 root_state 门禁，不再二次读取。
    marker_state: MarkerState | None = None
```

(b) `load_graph` 与 `load_graph_tolerant` 改为：

```python
def load_graph(root: Path, *, marker_state: MarkerState | None = None) -> DraftGraph:
    resolved = resolve_draft_root(root)
    result = load_graph_tolerant(resolved, marker_state=marker_state)
    state = result.marker_state
    # marker 无效（state 为 None）时不在此处拒绝：tolerant 已记录为阻断 issue，
    # 由下方 blocking 分支报出（既有语义）。
    if state is not None and state.root_state is not RootState.REGISTERED:
        # 候选根普通写命令在 Task 9–11 按操作级门禁开放前，注册模式入口
        # 显式拒绝，不能因为 marker 可读就意外放行。此代码随门禁落地删除。
        raise DraftError("root.candidate", resolved)
    blocking = structural_issues(result)
    if blocking:
        first = blocking[0]
        raise DraftError(
            first.code,
            *(() if first.path is None else (first.path,)),
            details=first.details,
            line=first.line,
        )
    return result.graph


def load_graph_tolerant(
    root: Path, *, marker_state: MarkerState | None = None
) -> LoadResult:
    """容错加载：candidate 与 registered 共享同一页面/关系/链接/路径协议。

    marker 是根身份的唯一权威：``load_graph_tolerant`` 读取并校验 marker，不按外部
    mode 区分分支。发现的受管分类消费同一次读取的所有权状态（round-18 M1/A1），
    不新增第二次 marker 文件读取。调用方已持有同根 marker 事实时必须通过
    ``marker_state`` 传入：本函数不再读取 marker 文件，受管分类以传入状态为权威；
    缺省 ``None`` 表示由本函数自行读取一次。
    """
    resolved = root if root.is_absolute() else resolve_draft_root(root)
    marker_issues: list[CheckIssue] = []
    state = marker_state
    if state is None:
        try:
            state = read_marker(resolved)
        except DraftError as error:
            marker_issues.append(
                CheckIssue(
                    code=error.code,
                    severity=IssueSeverity.ERROR,
                    path=MARKER_PATH,
                    line=None,
                )
            )
    conclusions_state = state.conclusions_state if state is not None else None
    result = _load(resolved, conclusions_state)
    return LoadResult(
        graph=result.graph,
        issues=[*marker_issues, *result.issues],
        resource_facts=result.resource_facts,
        marker_state=state,
    )
```

- [ ] **Step 4：运行新测试与既有回归**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_marker_state_threading.py" "$RUNTIME/tests/integration/test_operation_facts.py" "$RUNTIME/tests/integration/test_checker.py" "$RUNTIME/tests/integration/test_queries.py" -q`
Expected: 新测试 PASS；既有测试 PASS（缺省路径行为未变；`test_operation_facts.py` 的 snapshot 切换测试继续通过）。

- [ ] **Step 5：lint/类型 + Commit**

Run: Task 1 Step 3 命令。Expected: 全绿。

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
        plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py
git commit -m "fix(draft): graph 边界接受 marker_state 并停止重复读取

load_graph_tolerant/load_graph 增加 keyword-only marker_state：传入时不再
读取 marker 文件，受管分类与 root_state 门禁以传入状态为权威；LoadResult
暴露同一次读取的 marker_state 供调用方门禁消费。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 4：checker 与 query 边界穿透（F2）

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py`

**Interfaces:**
- Consumes: Task 3 的 `load_graph_tolerant(root, *, marker_state)`。
- Produces: `check_draft(root, *, include_operation_state=True, marker_state: MarkerState | None = None) -> CheckReport`；`load_tolerant_view(root, *, marker_state: MarkerState | None = None) -> TolerantView`。

- [ ] **Step 1：追加红测试**

在 `test_marker_state_threading.py` 追加（import 增加 `from draft.checker import check_draft`、`from draft.graph import load_graph` 已有则忽略、`from draft.query import load_tolerant_view`）：

```python
def _issue_tuples(report: CheckReport) -> list[tuple[str, Path | None, tuple[str, ...]]]:
    return [(issue.code, issue.path, issue.details) for issue in report.issues]


def test_check_draft_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入状态：不读取 marker；报告与磁盘 marker 同状态的报告逐项一致。"""
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})
    generated = _registered(ConclusionsState.GENERATED)

    report = check_draft(root, marker_state=generated)
    assert marker_reads == []

    # 磁盘 marker 改写为同一状态后自读：两条路径的报告必须一致（含结论必需项）。
    (root / ".draft" / "draft.json").write_text(
        managed_marker_text(conclusions_state=ConclusionsState.GENERATED), encoding="utf-8"
    )
    expected = check_draft(root)
    assert marker_reads == [root]
    assert _issue_tuples(report) == _issue_tuples(expected)


def test_load_tolerant_view_consumes_passed_state_without_reading(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """传入状态：不读取 marker；视图与自读路径的阻碍集合一致。"""
    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": PAGE_A},
        root_state=RootState.CANDIDATE,
    )
    candidate = MarkerState(
        version=FORMAT_VERSION,
        root_state=RootState.CANDIDATE,
        conclusions_state=ConclusionsState.NOT_GENERATED,
    )

    threaded = load_tolerant_view(root, marker_state=candidate)
    assert marker_reads == []

    direct = load_tolerant_view(root)
    assert marker_reads == [root]
    assert threaded.blocking_paths == direct.blocking_paths
    assert set(threaded.graph.pages) == set(direct.graph.pages)
```

- [ ] **Step 2：运行确认红**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_marker_state_threading.py" -q`
Expected: 新增两个用例 FAIL（`TypeError: unexpected keyword argument 'marker_state'`）。

- [ ] **Step 3：实现 checker.py**

(a) 模块级 `from draft.graph import (...)` 块增加 `MarkerState`。(b) 两个辅助函数改为纯状态函数：

```python
def _conclusions_state(state: MarkerState | None) -> ConclusionsState | None:
    """所有权状态；marker 缺失/无效时为 None（不在此处报错，由加载期 marker issue 报告）。"""
    if state is None:
        return None
    return state.conclusions_state


def _conclusions_required(state: MarkerState | None) -> bool:
    """conclusions.md 是否必须存在：标记有效时由所有权状态决定。

    标记缺失/无效时不在此处要求；对应诊断由加载期 marker issue 报告。
    """
    return state is not None and state.conclusions_state is ConclusionsState.GENERATED
```

(c) `check_draft` 改为（函数体其余部分不动，仅三处消费点换参）：

```python
def check_draft(
    root: Path,
    *,
    include_operation_state: bool = True,
    marker_state: MarkerState | None = None,
) -> CheckReport:
    from draft.graph import read_marker

    resolved = resolve_draft_root(root)
    # marker 事实单次读取：缺省时本函数读取一次并贯穿全部消费者；调用方已持有
    # 同根状态时必须传入，本函数不再读取（与 load_graph_tolerant 同一不变量）。
    state = marker_state
    if state is None:
        try:
            state = read_marker(resolved)
        except DraftError:
            # marker 损坏记为 issue 由 tolerant 加载报出，此处不重复报错。
            pass
```

随后函数体内：`conclusions_required = _conclusions_required(state)`（原 `(resolved)`）、`result = load_graph_tolerant(resolved, marker_state=state)`（原无参）、`conclusions_state = _conclusions_state(state)`（原 `(resolved)`）。注意：`state` 为 `None`（自读失败）时传给 tolerant 的仍是 `None`，由 tolerant 自读并记录 marker issue——缺省路径的报错语义与现状一致。

- [ ] **Step 4：实现 query.py**

`load_tolerant_view` 改为：

```python
def load_tolerant_view(
    root: Path, *, marker_state: MarkerState | None = None
) -> TolerantView:
```

（模块级 `from draft.graph import DraftGraph, load_graph` 处增加 `MarkerState`；函数体内 `result = load_graph_tolerant(resolved, marker_state=marker_state)`；docstring 补一句：调用方已持有同根 marker 事实时必须传入，本函数不再二次读取。）

- [ ] **Step 5：运行新测试与既有回归**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_marker_state_threading.py" "$RUNTIME/tests/integration/test_checker.py" "$RUNTIME/tests/integration/test_conclusions.py" "$RUNTIME/tests/integration/test_lifecycle.py" "$RUNTIME/tests/integration/test_cli.py" -q -m 'not network'`
Expected: 全部 PASS（check 命令、conclusions、adopt 的既有行为由这些套件守卫）。

- [ ] **Step 6：lint/类型 + Commit**

Run: Task 1 Step 3 命令。Expected: 全绿。

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/query.py \
        plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py
git commit -m "fix(draft): check 与 tolerant 视图边界接受 marker_state

check_draft 缺省时只读取一次 marker 并贯穿 conclusions 门禁、tolerant 加载
与所有权检查；load_tolerant_view 透传状态。传入状态时边界零读取。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 5：持有者接线与链路计数（F2 闭合）

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py`

**Interfaces:**
- Consumes: Task 3/4 的四个边界签名。
- Produces: 契约 F2-5 的全部接线；`_load_graph_with_conclusions(root: Path, marker_state: MarkerState | None) -> DraftGraph`。

- [ ] **Step 1：追加链路红测试**

在 `test_marker_state_threading.py` 追加（import 增加 `from draft.lifecycle import adopt`、`from draft.operation_facts import load_operation_facts`、`from draft.structure import move_page`、`from tests.integration.support import build_valid_candidate`）：

```python
def test_registered_write_command_reads_marker_twice(
    draft_factory: DraftFactory, marker_reads: list[Path]
) -> None:
    """registered move 全链 marker 事实恰好读取两次。

    第 1 次是 load_operation_facts 的 snapshot；第 2 次是 execute_plan 提交前
    校验（operations._check）对应用后状态的新鲜读取。加载链内的
    check_draft/load_graph/load_graph_tolerant 消费传入状态，不再各自读取
    （round-15 H1：同一操作计划的 marker 事实不得来自后续 live 读取）。
    提交前校验不得改传 snapshot：GENERATE_CONCLUSIONS 会把 marker 推进为
    generated，校验必须按应用后的所有权分类。
    """
    root = draft_factory({"index.md": PAGE_INDEX, "pages/a.md": PAGE_A})

    move_page(root, Path("pages/a.md"), Path("pages/c.md"))

    assert (root / "pages/c.md").is_file()
    assert marker_reads == [root, root]


def test_candidate_facts_read_marker_once(
    tmp_path: Path, marker_reads: list[Path]
) -> None:
    """candidate facts 链：snapshot 一次，tolerant 视图消费传入状态。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)

    facts = load_operation_facts(root)

    assert facts.root_state is RootState.CANDIDATE
    assert marker_reads == [root]


def test_adopt_reads_marker_once(tmp_path: Path, marker_reads: list[Path]) -> None:
    """adopt 全链 marker 事实恰好读取一次（preallocate 前的 snapshot）。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)

    adopt(root)

    assert marker_reads == [root]
```

- [ ] **Step 2：运行确认红**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_marker_state_threading.py" -q`
Expected: 三个新用例 FAIL——此时 Task 3/4 已落地，`marker_reads` 应分别为 4/2/3 项：move 链 = facts snapshot + check_draft 缺省自读 + load_graph 内 tolerant 自读 + `_check` 提交前校验自读（Task 4 已把 check_draft 的读取合并为开头一次，其内部 tolerant 消费传入状态不再自读）；candidate 链 = snapshot + tolerant 自读；adopt 链 = snapshot + check_draft 缺省自读 + tolerant 自读。若计数与该量级明显不符，报告实际调用栈。

- [ ] **Step 3：接线五个持有者**

(a) `operation_facts.py`：

```python
    marker = read_marker_snapshot(resolved)
    if marker.state.root_state is RootState.REGISTERED:
        report = check_draft(resolved, marker_state=marker.state)
```

（errors 过滤与抛错不变；）`graph = load_graph(resolved, marker_state=marker.state)`；末尾 `view = load_tolerant_view(resolved, marker_state=marker.state)`。docstring 补一句：marker.state 贯穿 check 与加载边界，加载链 marker 恰好读取一次（另有 execute_plan 提交前校验对应用后状态的独立读取）。

(b) `conclusions.py` 的 `regenerate`：`view = load_tolerant_view(resolved, marker_state=marker.state)`；`graph = _load_graph_with_conclusions(resolved, marker.state)`。`load_generation_graph`：`marker_state = read_marker(resolved)` 后 `return _load_graph_with_conclusions(resolved, marker_state)`。`_load_graph_with_conclusions` 改为：

```python
def _load_graph_with_conclusions(root: Path, marker_state: MarkerState | None) -> DraftGraph:
    """专用容错加载：generated 时容忍缺失/损坏的结论页（可替换目标）。"""
    from draft.graph import load_graph_tolerant

    state = marker_state.conclusions_state if marker_state is not None else None
    if state is ConclusionsState.GENERATED:
        conclusions = root / CONCLUSIONS_PAGE
        if conclusions.is_file() and not conclusions.is_symlink():
            # 保留文件以便生成器重写；不在这里做普通加载期检查。
            pass
    return load_graph_tolerant(root, marker_state=marker_state).graph
```

（`MarkerState` 加入该文件的 `draft.graph` 导入；`regenerate` 内已有的 `state = marker.state.conclusions_state` 局部保持不变。）

(c) `lifecycle.py` 的 `adopt`：`report = check_draft(target, marker_state=state)`；`graph = load_graph_tolerant(target, marker_state=state).graph`（`state = snapshot.state` 既有局部）。

(d) `trash.py` 的 `_build_restore_plan_impl`：

```python
    if root_state is RootState.REGISTERED:
        report = check_draft(
            resolved, include_operation_state=False, marker_state=marker.state
        )
        if report.errors:
            codes = tuple(issue.code for issue in report.errors)
            raise DraftError("operation.validation_failed", resolved, details=codes)
        graph = load_graph(resolved, marker_state=marker.state)
    else:
        view = load_tolerant_view(resolved, marker_state=marker.state)
```

（其余行不动；该函数 docstring 已声明"marker 为调用方一次读取的 snapshot，本函数不再重新读取 marker"——接线使该声明覆盖图加载与检查边界。）

- [ ] **Step 4：运行链路测试与全量回归**

Run: `RUNTIME=plugins/draft/skills/draft/scripts/runtime && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network' && uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q`
Expected: 全部 PASS（conclusions/restore/adopt 的行为等价性由既有套件守卫）。

- [ ] **Step 5：lint/类型 + Commit**

Run: Task 1 Step 3 命令。Expected: 全绿。

```bash
git add plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py \
        plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
        plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py
git commit -m "fix(draft): 写命令链持有者传入 marker 状态，消除重复读取

operation_facts/conclusions/adopt/restore 把已持有的 marker 事实传给
check 与加载边界；加载链 marker 恰好读取一次，root_state 门禁与
conclusions 分类同源（round-15 H1 落实到加载边界）。execute_plan 提交
前校验保持对应用后状态的新鲜读取。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

### Task 6：全量门禁与完成汇报

- [ ] **Step 1：全量离线门禁**

依次运行 Global Constraints 中的四组 pytest 命令与 ruff/pyright，另加 `bash plugins/draft/scripts/check-adapters.sh`。网络集成测试若未运行，如实记录为未运行。

- [ ] **Step 2：横向审计**

按契约 F2-5 的穿透点全表逐项核对 git diff：五个持有者全部接线、七个"不接线"入口未被改动、无其他新增的 `read_marker`/`read_marker_snapshot` 调用点。发现遗漏即回 Step 补线，不得只在汇报中说明。

- [ ] **Step 3：完成汇报（写入最终回复，不新建文件）**

汇报必须包含：三个修复的最终契约形态；红灯证据（每个 Task 的 Step 2 输出摘要）；绿灯证据（本步命令与结果）；行为变化声明（并发窗口一致性收紧，见 F2-6）；未决事项（见下节原文照录并标注未处理）。

---

## 三、禁止的捷径

- 禁止用模块级缓存/全局变量保存 marker 状态代替参数穿透。
- 禁止把 `marker_state=None` 解释为"调用方断言 marker 无效"——None 仅表示未持有，边界必须自读。
- 禁止为通过计数测试而放宽断言（如改成 `<=` 或移除计数 fixture）；计数不符时报告调用栈。
- 禁止顺手重构 `resolve_draft_root`、CLI root 选择或其他无关入口；禁止给"不接线"清单里的入口强塞状态参数。
- 禁止删除或放宽 CRLF 字节保真回归测试；禁止在删除 `page_bytes`/`now` 时保留"曾经存在"的注释、命名或提交说明痕迹。
- 测试替身只允许出现在既有 fixture/monkeypatch 风格内（计数 wrapper 包住真实实现，不替换行为）。

## 四、未决事项与相邻遗留（不在本计划范围，汇报时原文照录）

1. CLI 层 `select_managed_root` 的 marker 读取与 facts snapshot 仍是两次读取：root 选择服务于 CLI 决策（`ManagedRoot.marker`），与操作计划的 facts 用途不同，是否合并需单独设计。
2. `prepare_managed_command`（命令前置推进）内部的 marker 读取情况未审计。
3. conclusions regenerate、trash restore 两条链路未新增读计数测试（行为等价由既有套件守卫；接线正确性由横向审计核对）。
4. check_draft 缺省路径在 marker 损坏时仍读取两次（自身读取失败 + tolerant 自读记录 issue）：错误路径上的残留，语义与现状一致，未消除。
