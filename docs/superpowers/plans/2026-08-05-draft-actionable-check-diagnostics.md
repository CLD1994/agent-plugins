# Draft 可行动检查诊断实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `draft check ROOT` 的普通输出为人和 Agent 提供可直接定位的单行诊断，同时让 `--json` 保存同一批诊断的稳定原始参数。

**Architecture:** 检测错误的生产者在事实尚未丢失的最窄边界构造完整 `CheckIssue`；新增 `draft.diagnostics` 作为错误码目录和纯渲染边界；CLI 只负责遍历同一份 `CheckReport`、选择普通或 JSON 表示并保持退出语义。现有 `CheckIssue(code, severity, path, line, details)` 形状、检查规则和排序不变。

**Tech Stack:** Python `>=3.12,<3.15`、标准库 `dataclasses`/`json`/`pathlib`、pytest、pyright strict、ruff、uv。

## Global Constraints

- 设计规格是 `docs/superpowers/specs/2026-08-05-draft-actionable-check-diagnostics-design.md`；实现不得偏离其已确认行为契约。
- `check` 在普通和 JSON 模式下都必须只读，不修改草稿任何字节。
- 不改变 Markdown 接受集合、检查规则、问题排序、严重级别、退出码、结构写入或恢复语义。
- 不加入自动修复、源码摘录、ANSI 颜色、多语言框架或新依赖。
- `path` 始终是来源位置；相关目标只进入 `details` 和人类事实文本。
- `line` 使用一基行号；不存在具体结构行时必须保持 `None`，不得伪造 `0` 或 `1`。
- `details` 只保存错误码判别的稳定字符串参数，不保存预渲染中文整句。
- 普通输出固定为 `<path>[:<line>]: <级别> <code>：<事实>`；根级位置固定显示 `<ROOT>`。
- JSON 四个分类数组保持，条目增加 `severity`，并继续包含 `code`、`path`、`line`、`details`。
- runtime 与两个插件清单从 `0.1.0` 同步提升到 `0.2.0`，`uv.lock` 必须同步。
- 每个任务先写行为测试并确认失败，再写最小实现；不得通过删除测试、放宽 pyright/ruff 或吞掉诊断让门禁通过。

---

## File Structure

- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
  - 唯一职责：声明错误码目录，验证位置与 `details` 参数，并把一个 `CheckIssue` 渲染为一行文本或一个 JSON 字典。
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py`
  - 唯一职责：独立约束错误码集合、位置规则、参数规则、普通文本、JSON 载荷和保真回退。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
  - 只把会进入检查报告的解析错误详情改成稳定原因字面量和原始参数。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
  - 在发现死链、越界链接或受管理路径符号链接时保留字面目标、规范目标和来源行；同一关系链接只报告一次链接错误。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
  - 保留关系边来源行、闭合环路、状态分支、容量计数和生命周期参数。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
  - 委托 `diagnostics.py` 渲染，增加四档计数摘要和 JSON `severity`，合并候选检查提示。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_markdown.py`
  - 约束解析错误的稳定原因参数。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py`
  - 约束链接、关系、环路、容量和回收诊断的完整事实。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_operations.py`
  - 约束操作状态诊断继续使用原始阶段和操作标识。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py`
  - 精确约束普通多错误输出、摘要、候选提示、JSON 条目和只读性。
- Modify: `plugins/draft/skills/draft/references/draft-format.md`
  - 发布普通输出语法、JSON 字段和逐错误码 `details` 参数协议。
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Modify: `plugins/draft/skills/draft/scripts/runtime/uv.lock`
- Modify: `plugins/draft/.codex-plugin/plugin.json`
- Modify: `plugins/draft/.claude-plugin/plugin.json`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py`
  - 同步 `0.2.0` 环境路径断言并执行复制 skill 烟雾测试。

## Diagnostic Contract Table

位置模式：`line` 要求 `path` 和正整数 `line`；`file` 要求 `path` 且 `line=None`；`line|file` 允许二者；`root` 要求 `path=None` 和 `line=None`。

| Code | Location | `details` schema | Human fact |
| --- | --- | --- | --- |
| `candidate.already_registered` | `file` | `()` | `目标已经登记，--candidate 仅适用于未登记候选` |
| `candidate.internal_state` | `file` | `()` | `候选目录包含 Draft 内部状态` |
| `format.missing` | `file` | `()` | `登记文件不存在` |
| `format.invalid` | `file` | `()` | `登记文件格式无效` |
| `format.unsupported` | `file` | `()` | `登记文件版本不受支持` |
| `root.missing_file` | `file` | `()` | `必需文件或目录不存在` |
| `page.unmanaged_path` | `file` | `()` | `Markdown 文件不在受管理路径中` |
| `page.missing_title` | `file` | `()` | `缺少一级标题` |
| `page.missing_machine_block` | `file` | `()` | `一级标题后缺少机器引用块` |
| `page.missing_status` | `file` | `()` | `机器引用块缺少“状态”` |
| `page.duplicate_status` | `line` | `()` | `重复声明“状态”` |
| `page.unknown_label` | `line` | `(label,)` | `机器引用块包含未知标签：{label}` |
| `page.invalid_status` | `line` | `(not_single_code_span,)` / `(empty_explanation,)` / `(unknown_prefix, prefix)` | 分别说明状态值不是单一反引号值、说明为空、前缀无效 |
| `page.invalid_relation` | `line` | `(link_cardinality_or_image, label)` / `(extra_content, label)` | 分别说明关系必须恰有一个非图片链接、链接外有额外内容 |
| `page.parse_error` | `line|file` | `(link_destination_missing,)` / `(image_destination_missing,)` / `(source_alignment_failed, destination)` / `(lazy_continuation,)` / `(machine_block_map_missing,)` | 对应说明解析器无法取得目标、无法对齐源码、出现懒惰延续行或缺少行映射 |
| `link.missing` | `line` | `(literal_destination, normalized_target)` | 相同时 `链接目标不存在：{normalized_target}`；不同时说明 `{literal_destination}` 解析到 `{normalized_target}` 后不存在 |
| `link.escape` | `line|file` | `(destination, literal_destination)` / `(managed_path, relative_path)` | 分别说明链接越出草稿根、受管理路径是越界符号链接 |
| `path.symlink_escape` | `file` | `()` | `路径包含越出受管理树的符号链接` |
| `relation.duplicate` | `line` | `(label, target)` | `{label} 重复指向 {target}` |
| `parent.root_declared` | `line` | `()` | `根页面不得声明“父主题”` |
| `parent.missing` | `file` | `()` | `缺少“父主题”声明` |
| `parent.multiple` | `line` | `()` | `声明了多个“父主题”` |
| `layout.parent_mismatch` | `line` | `(declared_target, physical_target)` | `父主题声明为 {declared_target}，物理父主题应为 {physical_target}` |
| `parent.cycle` | `file` | `(path_1, ..., path_1)` | `父主题形成环路：path_1 → ... → path_1` |
| `parent.unreachable` | `file` | `()` | `父主题链无法到达根页面` |
| `parent.navigation_missing` | `file` | `(child,)` | `正文缺少对子页面 {child} 的导航链接` |
| `hub.empty` | `file` | `()` | `非根枢纽没有子页面` |
| `dependency.self` | `line` | `()` | `页面不能依赖自己` |
| `dependency.cycle` | `file` | `(path_1, ..., path_1)` | `依赖形成环路：path_1 → ... → path_1` |
| `replacement.pair_missing` | `line` | `(target, expected_counterpart)` | `{target} 缺少对应的“{expected_counterpart}”关系` |
| `replacement.status_mismatch` | `line|file` | `(relation_requires_status,)` / `(status_requires_relation,)` | 分别说明“被取代”关系要求已取代状态、已取代状态要求“被取代”关系 |
| `replacement.cycle` | `file` | `(path_1, ..., path_1)` | `取代形成环路：path_1 → ... → path_1` |
| `challenge.pair_missing` | `line` | `(target, expected_counterpart)` | `{target} 缺少对应的“{expected_counterpart}”关系` |
| `output.forbidden` | `line` | `()` | `当前页面或状态不允许声明“产出”` |
| `output.missing` | `file` | `()` | `已确认页面缺少“产出”声明` |
| `output.wrong_target` | `line` | `(duplicate,)` / `(not_conclusions, literal_destination)` | 分别说明重复产出、产出必须指向根级 `conclusions.md` 并显示当前目标 |
| `output.backlink_missing` | `file` | `(source_page,)` | `缺少对已确认页面 {source_page} 的正文反向链接` |
| `page.capacity` | `file` | `(line_count, byte_count)` | `页面容量为 {line_count} 行 / {byte_count} 字节，建议审查是否拆分` |
| `operation.journal_corrupt` | `file` | `()` | `操作锁或日志损坏` |
| `operation.recovery_required` | `file` | `(operation_id,)` | `操作 {operation_id} 需要恢复` |
| `operation.orphan_allocating` | `file` | `()` | `发现未持锁的 allocating 操作日志` |
| `operation.inconsistent_state` | `file` | `(phase,)` | `操作处于无锁不一致阶段：{phase}；完整上下文请使用 --json` |
| `trash.deadlines_invalid` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的期限字段无效` |
| `trash.package_missing` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的权威删除包不存在` |
| `trash.package_corrupt` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的权威删除包损坏` |
| `trash.retained` | `file` | `(operation_id,)` | `回收操作 {operation_id} 仍处于保留态` |
| `trash.handoff_pending` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的系统回收站移交等待重试` |
| `trash.pending_unexplained` | `file` | `(operation_id,)` | `回收操作 {operation_id} 处于无有效失败记录的待移交态` |

---

### Task 1: 建立诊断目录与纯渲染边界

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `draft.models.CheckIssue`、`IssueSeverity`。
- Produces: `KNOWN_CHECK_CODES: frozenset[str]`、`render_issue(issue: CheckIssue) -> str`、`issue_payload(issue: CheckIssue) -> dict[str, object]`。
- Later tasks must use the exact code and `details` schemas in the Diagnostic Contract Table.

- [x] **Step 1: 写错误码集合和普通渲染失败测试**

```python
from pathlib import Path

from draft.diagnostics import KNOWN_CHECK_CODES, render_issue
from draft.models import CheckIssue, IssueSeverity

EXPECTED_CODES = {
    "candidate.already_registered", "candidate.internal_state",
    "format.missing", "format.invalid", "format.unsupported", "root.missing_file",
    "page.unmanaged_path", "page.missing_title", "page.missing_machine_block",
    "page.missing_status", "page.duplicate_status", "page.unknown_label",
    "page.invalid_status", "page.invalid_relation", "page.parse_error", "page.capacity",
    "link.missing", "link.escape", "path.symlink_escape", "relation.duplicate",
    "parent.root_declared", "parent.missing", "parent.multiple",
    "layout.parent_mismatch", "parent.cycle", "parent.unreachable",
    "parent.navigation_missing", "hub.empty", "dependency.self", "dependency.cycle",
    "replacement.pair_missing", "replacement.status_mismatch", "replacement.cycle",
    "challenge.pair_missing", "output.forbidden", "output.missing",
    "output.wrong_target", "output.backlink_missing", "operation.journal_corrupt",
    "operation.recovery_required", "operation.orphan_allocating",
    "operation.inconsistent_state", "trash.deadlines_invalid", "trash.package_missing",
    "trash.package_corrupt", "trash.retained", "trash.handoff_pending",
    "trash.pending_unexplained",
}

def test_catalog_covers_the_published_check_codes() -> None:
    assert KNOWN_CHECK_CODES == EXPECTED_CODES

def test_link_missing_is_a_complete_single_line() -> None:
    issue = CheckIssue(
        code="link.missing", severity=IssueSeverity.ERROR,
        path=Path("conclusions.md"), line=12,
        details=("pages/missing.md", "pages/missing.md"),
    )
    assert render_issue(issue) == (
        "conclusions.md:12: 错误 link.missing：链接目标不存在：pages/missing.md"
    )
```

- [x] **Step 2: 写位置、复杂摘要和保真回退失败测试**

```python
def test_file_location_omits_a_fake_line() -> None:
    issue = CheckIssue("parent.missing", IssueSeverity.ERROR, Path("pages/a.md"), None)
    assert render_issue(issue) == "pages/a.md: 错误 parent.missing：缺少“父主题”声明"

def test_complex_issue_explicitly_points_to_json() -> None:
    issue = CheckIssue(
        "operation.inconsistent_state", IssueSeverity.ERROR,
        Path(".draft/operations/a/journal.json"), None, ("prepared",),
    )
    assert render_issue(issue).endswith("操作处于无锁不一致阶段：prepared；完整上下文请使用 --json")

def test_unknown_or_invalid_issue_fallback_is_one_line_and_lossless() -> None:
    issue = CheckIssue("future.code", IssueSeverity.WARNING, None, None, ("a\nb", "值"))
    assert render_issue(issue) == (
        '<ROOT>: 警告 future.code：未登记诊断参数：["a\\nb", "值"]'
    )

def test_known_code_with_invalid_location_uses_the_same_fallback() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), None, ("x", "x"))
    assert "未登记诊断参数" in render_issue(issue)
```

- [x] **Step 3: 写 JSON 载荷失败测试**

```python
from draft.diagnostics import issue_payload

def test_issue_payload_adds_explicit_severity_and_keeps_raw_details() -> None:
    issue = CheckIssue(
        "page.capacity", IssueSeverity.REVIEW, Path("pages/a.md"), None, ("601", "70000")
    )
    assert issue_payload(issue) == {
        "code": "page.capacity",
        "severity": "review",
        "path": "pages/a.md",
        "line": None,
        "details": ["601", "70000"],
    }
```

- [x] **Step 4: 运行新测试并确认因模块不存在而失败**

Run:

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'draft.diagnostics'`.

- [x] **Step 5: 实现位置、参数验证和安全单行辅助函数**

```python
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from draft.models import CheckIssue, IssueSeverity

class LocationKind(StrEnum):
    LINE = "line"
    FILE = "file"
    ROOT = "root"

DetailValidator = Callable[[tuple[str, ...]], bool]
MessageRenderer = Callable[[tuple[str, ...]], str]

@dataclass(frozen=True)
class DiagnosticSpec:
    locations: frozenset[LocationKind]
    valid_details: DetailValidator
    render_message: MessageRenderer

_SEVERITY_NAMES = {
    IssueSeverity.ERROR: "错误",
    IssueSeverity.WARNING: "警告",
    IssueSeverity.INFO: "信息",
    IssueSeverity.REVIEW: "提醒",
}

def _safe(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]

def _location(issue: CheckIssue) -> tuple[LocationKind, str]:
    if issue.path is None:
        return LocationKind.ROOT, "<ROOT>"
    path = issue.path.as_posix()
    if issue.line is None:
        return LocationKind.FILE, path
    return LocationKind.LINE, f"{path}:{issue.line}"

def _fixed(count: int) -> DetailValidator:
    return lambda details: len(details) == count

def _variant(arities: Mapping[str, int]) -> DetailValidator:
    return lambda details: bool(details) and arities.get(details[0]) == len(details)

def _cycle(details: tuple[str, ...]) -> bool:
    return len(details) >= 3 and details[0] == details[-1]
```

`_location` 还必须把 `line <= 0` 判为无效位置；不能把它渲染成合法来源位置。

- [x] **Step 6: 按 Diagnostic Contract Table 完整建立 `_SPECS`**

固定文本使用 `_fixed(0)`；固定参数使用 `_fixed(n)`；三个环路使用 `_cycle`；分支错误使用以下精确校验：

```python
_variant({
    "not_single_code_span": 1,
    "empty_explanation": 1,
    "unknown_prefix": 2,
})
_variant({"link_cardinality_or_image": 2, "extra_content": 2})
_variant({
    "link_destination_missing": 1,
    "image_destination_missing": 1,
    "source_alignment_failed": 2,
    "lazy_continuation": 1,
    "machine_block_map_missing": 1,
})
_variant({"destination": 2, "managed_path": 2})
_variant({"relation_requires_status": 1, "status_requires_relation": 1})
_variant({"duplicate": 1, "not_conclusions": 2})
```

每个 `render_message` 必须产生表中完整事实。所有参数先经 `_safe`；环路只在渲染时用 ` → ` 连接。`operation.inconsistent_state` 是唯一明确要求附加 `完整上下文请使用 --json` 的现有错误码。

- [x] **Step 7: 实现公共渲染函数和 JSON 载荷函数**

```python
KNOWN_CHECK_CODES = frozenset(_SPECS)

def _fallback(details: tuple[str, ...]) -> str:
    encoded = json.dumps(list(details), ensure_ascii=False)
    return f"未登记诊断参数：{encoded}"

def render_issue(issue: CheckIssue) -> str:
    location_kind, location = _location(issue)
    spec = _SPECS.get(issue.code)
    valid = (
        spec is not None
        and location_kind in spec.locations
        and (issue.line is None or issue.line > 0)
        and spec.valid_details(issue.details)
    )
    message = spec.render_message(issue.details) if valid and spec is not None else _fallback(issue.details)
    return f"{location}: {_SEVERITY_NAMES[issue.severity]} {issue.code}：{message}"

def issue_payload(issue: CheckIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "path": None if issue.path is None else issue.path.as_posix(),
        "line": issue.line,
        "details": list(issue.details),
    }
```

- [x] **Step 8: 扩充参数化测试以逐项覆盖表中所有错误码**

测试数据必须手写 `CheckIssue`、预期完整中文事实和合法位置；不能从 `_SPECS` 生成预期。为六个分支协议分别增加每个分支的正例，并为环路增加未闭合反例。

- [x] **Step 9: 运行诊断目录测试并确认通过**

Run:

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py -q
```

Expected: all tests pass.

- [x] **Step 10: 运行静态检查并提交任务**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py
git add plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py
git commit -m "feat(draft): add check diagnostic catalog"
```

Expected: pyright and ruff pass; commit contains only the catalog and its tests.

---

### Task 2: 保留 Markdown 与链接诊断事实

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py:390-635`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py:52-53, 200-291`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py:220-270`

**Interfaces:**
- Consumes: Task 1 的错误码与 `details` 协议。
- Produces: `page.*`、`link.missing`、`link.escape` 的完整 `CheckIssue`；后续 CLI 可以直接渲染，不重新读取文件。

- [x] **Step 1: 扩充 Markdown 解析错误失败测试**

```python
def test_unknown_label_preserves_only_the_label_operand() -> None:
    text = VALID_PAGE.replace("> 依赖：", "> 状态依据：")
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/request.md"), text)
    assert caught.value.code == "page.unknown_label"
    assert caught.value.line == 7
    assert caught.value.details == ("状态依据",)

@pytest.mark.parametrize(
    ("value", "details"),
    [
        ("已关闭", ("not_single_code_span",)),
        ("`已关闭：`", ("empty_explanation",)),
        ("`进行中`", ("unknown_prefix", "进行中")),
    ],
)
def test_invalid_status_uses_stable_reason(value: str, details: tuple[str, ...]) -> None:
    with pytest.raises(DraftError) as caught:
        parse_page(Path("pages/x.md"), make_page(value))
    assert caught.value.code == "page.invalid_status"
    assert caught.value.details == details
```

为 `page.invalid_relation` 的两个分支增加断言：`("link_cardinality_or_image", "依赖")` 和 `("extra_content", "父主题")`。为机器区域懒惰延续行增加 `page.parse_error` 与 `("lazy_continuation",)` 断言。

- [x] **Step 2: 扩充链接事实和多错误失败测试**

```python
def test_missing_link_reports_source_line_literal_and_normalized_target(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["pages/question.md"] = QUESTION.replace(
        "使用显式状态前缀。", "见 [不存在](missing.md)。"
    )
    report = check_draft(draft_factory(files))
    issue = next(issue for issue in report.errors if issue.code == "link.missing")
    assert issue.path == Path("pages/question.md")
    assert issue.line == 9
    assert issue.details == ("missing.md", "pages/missing.md")

def test_two_missing_links_on_one_line_remain_distinguishable(
    draft_factory: DraftFactory,
) -> None:
    files = healthy()
    files["conclusions.md"] = CONCLUSIONS + "[甲](pages/a.md) [乙](pages/b.md)\n"
    issues = [i for i in check_draft(draft_factory(files)).errors if i.code == "link.missing"]
    assert [(i.line, i.details) for i in issues] == [
        (8, ("pages/a.md", "pages/a.md")),
        (8, ("pages/b.md", "pages/b.md")),
    ]
```

另加关系链接目标缺失用例，断言同一 `source + line + target` 只产生一条 `link.missing`，防止 `page.links` 与 `page.relations` 两次报告同一事实。

- [x] **Step 3: 运行定向测试并确认旧中文详情和空详情导致失败**

Run:

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_markdown.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py \
  -k 'unknown_label or invalid_status or invalid_relation or lazy_continuation or missing_link'
```

Expected: assertions fail because current `details` 是中文整句或空元组，并且关系死链可能重复。

- [x] **Step 4: 把 Markdown 错误改成稳定分支参数**

按以下精确替换构造 `DraftError.details`：

```python
("link_destination_missing",)
("image_destination_missing",)
("source_alignment_failed", token_link.destination)
("lazy_continuation",)
(candidate_label,)  # page.unknown_label
("not_single_code_span",)
("empty_explanation",)
("unknown_prefix", prefix_text)
("machine_block_map_missing",)
()  # page.duplicate_status，行号已经携带位置
("link_cardinality_or_image", machine_line.label)
("extra_content", machine_line.label)
```

不得改变异常错误码、来源路径、来源行或解析接受集合。

- [x] **Step 5: 让图加载器在第一次解析链接时保存完整结果**

把辅助函数扩展为接收原始参数：

```python
def error_issue(
    code: str,
    path: Path | None,
    line: int | None = None,
    *details: str,
) -> CheckIssue:
    return CheckIssue(code, IssueSeverity.ERROR, path, line, details)
```

链接目的地越界时使用：

```python
error_issue("link.escape", source, link.line, "destination", link.destination)
```

受管理路径符号链接使用：

```python
error_issue("link.escape", relative, None, "managed_path", relative.as_posix())
```

死链使用：

```python
error_issue(
    "link.missing", source, link.line,
    link.destination, target.as_posix(),
)
```

- [x] **Step 6: 复用第一次链接解析结果，消除关系链接的重复诊断**

在每个页面第一次遍历 `page.links` 时建立：

```python
resolved_links: dict[SourceSpan, Path | None] = {}
resolved_links[link.whole_span] = target
```

第二次遍历 `page.relations` 时只读取 `resolved_links[relation.link.whole_span]`；目标为 `None` 或不在 `managed_set` 时不再追加错误，只是不建立关系边。不得对同一链接再次调用 `resolve_link`。

- [x] **Step 7: 运行 Markdown、检查器和图相关结构测试**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_markdown.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_structure.py
```

Expected: all tests pass; missing relation target reports once.

- [x] **Step 8: 运行静态检查并提交任务**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check \
  plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_markdown.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py
git add plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_markdown.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py
git commit -m "fix(draft): preserve link diagnostic facts"
```

---

### Task 3: 保留图关系、容量和生命周期诊断参数

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py:100-451`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py:275-820`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_operations.py:497-525`

**Interfaces:**
- Consumes: Task 1 的所有非 `page.*`/`link.*` 参数协议和 Task 2 的 `DraftGraph.relations`。
- Produces: 每个关系事实的来源行、闭合路径和原始参数；`check_draft` 返回顺序和严重级别不变。

- [x] **Step 1: 写关系来源行和对端参数失败测试**

在现有 `test_duplicate_normalized_relation_target_is_an_error`、`test_replacement_pair_missing` 和 `test_challenge_pair_missing` 中增加精确断言：

```python
assert duplicate.line == 9
assert duplicate.details == ("依赖", "pages/question.md")

assert replacement.line == 7
assert replacement.details == ("pages/question.md", "被取代")

assert challenge.line == 7
assert challenge.details == ("pages/question.md", "被质疑")
```

再增加反向分支用例，确认从“被取代”或“被质疑”出发时 `expected_counterpart` 分别是 `取代` 或 `质疑`。

- [x] **Step 2: 写闭合环路、自依赖行号和状态分支失败测试**

```python
assert parent_cycle.details == ("pages/a.md", "pages/b.md", "pages/a.md")
assert dependency_cycle.details == ("pages/a.md", "pages/b.md", "pages/a.md")
assert replacement_cycle.details == ("pages/a.md", "pages/b.md", "pages/a.md")
assert dependency_self.line == 7
assert relation_requires_status.details == ("relation_requires_status",)
assert relation_requires_status.line == 7
assert status_requires_relation.details == ("status_requires_relation",)
assert status_requires_relation.line is None
```

- [x] **Step 3: 写输出、布局、容量和生命周期参数失败测试**

```python
assert layout_mismatch.details == ("index.md", "pages/sub/index.md")
assert duplicate_output.details == ("duplicate",)
assert wrong_output.details == ("not_conclusions", "../index.md")
assert capacity.details == ("620", str(len(files["pages/question.md"].encode("utf-8"))))
assert inconsistent.details == ("prepared",)
assert recovery_required.details == ("4" * 32,)
```

容量测试中的期望行数必须写成夹具对应的确定常量；字节数可以从测试自己构造的输入字节计算，不能调用被测容量函数。

- [x] **Step 4: 运行定向检查器测试并确认失败**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_operations.py \
  -k 'duplicate_normalized or pair_missing or cycle or dependency_self or status_mismatch or wrong_target or capacity or checker_reports_operation_states'
```

Expected: failures show missing line numbers、未闭合字符串详情或中文预渲染详情。

- [x] **Step 5: 新增保留边行号的辅助结构**

```python
def _relation_lines(
    graph: DraftGraph, label: RelationLabel
) -> dict[tuple[Path, Path], tuple[int, ...]]:
    grouped: dict[tuple[Path, Path], list[int]] = {}
    for source, target, line in graph.relations.get(label, ()):
        grouped.setdefault((source, target), []).append(line)
    return {edge: tuple(sorted(lines)) for edge, lines in grouped.items()}

def _cycle_details(cycle: tuple[Path, ...]) -> tuple[str, ...]:
    return tuple(path.as_posix() for path in (*cycle, cycle[0]))
```

保留 `_multi_edges` 供环路计算使用；不能让渲染器接触 `DraftGraph`。

- [x] **Step 6: 改造关系、父主题与依赖诊断生产者**

- `relation.duplicate`: `details=(label.value, target.as_posix())`，行号使用第二次声明行。
- `layout.parent_mismatch`: `details=(declared_target.as_posix(), physical.as_posix())`。
- 三种环路：使用 `_cycle_details(cycle)`。
- `dependency.self`: 使用自依赖边的最小来源行。
- `parent.navigation_missing`: `details=(child.as_posix(),)`。

对应调用形状：

```python
_error("dependency.self", source, min(lines))
_error("parent.cycle", cycle[0], None, *_cycle_details(cycle))
```

- [x] **Step 7: 改造取代、质疑与产出诊断生产者**

- 配对检查从 `_relation_lines` 读取最小来源行。
- `replacement.pair_missing` 和 `challenge.pair_missing` 保存 `(target, expected_counterpart)`。
- 有“被取代”关系但状态错误时保存 `("relation_requires_status",)` 和关系行；状态为已取代但缺关系时保存 `("status_requires_relation",)` 且无行号。
- `output.wrong_target` 重复分支保存 `("duplicate",)`；错误目标分支保存 `("not_conclusions", declared[0].link.destination)`。
- `output.backlink_missing` 保存来源页面路径。

不要修改 `output.forbidden`、`output.missing` 的判定或严重级别。

- [x] **Step 8: 改造容量并冻结生命周期原始参数**

```python
_review("page.capacity", path, str(lines), str(size))
```

操作和回收诊断当前已经分别保存 `phase.value` 或 `operation_id`；只补充测试，不重写其状态判断。`operation.journal_corrupt` 和 `operation.orphan_allocating` 继续使用空 `details`。

- [x] **Step 9: 运行检查器、操作和回收测试**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_operations.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_trash.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_trash_lifecycle.py
```

Expected: all tests pass; issue ordering and severity assertions remain unchanged.

- [x] **Step 10: 运行静态检查并提交任务**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_operations.py
git add plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_checker.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_operations.py
git commit -m "fix(draft): retain checker diagnostic context"
```

---

### Task 4: 集成普通单行输出与 JSON 严重级别

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py:89-181, 405-425`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py:79-225, 784-815`

**Interfaces:**
- Consumes: Task 1 的 `render_issue` 和 `issue_payload`；Tasks 2-3 产生的完整 `CheckReport`。
- Produces: `check` 普通输出、JSON 信封和既有退出码；后续文档与烟雾测试以此作为公开行为。

- [x] **Step 1: 写同文件多错误普通输出失败测试**

```python
def test_check_human_reports_actionable_multiple_locations(
    registered_draft: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (registered_draft / "index.md").write_text(
        "# 主题\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n"
        "- [结论汇总](conclusions.md)\n",
        encoding="utf-8",
    )
    (registered_draft / "conclusions.md").write_text(
        "# 结论汇总\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](index.md)\n\n"
        "[甲](pages/missing-a.md)\n[乙](pages/missing-b.md)\n",
        encoding="utf-8",
    )

    assert main(["check", str(registered_draft)]) == 1
    assert capsys.readouterr().out.splitlines() == [
        "conclusions.md:7: 错误 link.missing：链接目标不存在：pages/missing-a.md",
        "conclusions.md:8: 错误 link.missing：链接目标不存在：pages/missing-b.md",
        "检查完成：2 个错误，0 个警告，0 个信息，0 个提醒。",
    ]
```

- [x] **Step 2: 写全绿、候选提示和四档摘要失败测试**

- 全绿精确等于 `检查通过：未发现客观错误。\n`。
- `candidate.already_registered` 的诊断行自身包含 `--candidate`；输出中不再出现独立 `提示：` 行。
- 构造 `CheckReport` 或使用现有回收夹具，精确断言四档按 `errors`、`warnings`、`infos`、`reviews` 顺序输出，末行计数全部正确。
- 拍摄运行前后 `snapshot_tree(root)`，确认普通模式同样只读。

- [x] **Step 3: 扩充 JSON 失败测试**

在 `test_check_clean_and_errors` 和 `test_check_json_separates_errors_warnings_infos_and_reviews` 中逐项断言：

```python
for severity, key in (
    ("error", "errors"),
    ("warning", "warnings"),
    ("info", "infos"),
    ("review", "reviews"),
):
    for issue in as_dicts(payload[key]):
        assert issue["severity"] == severity
        assert set(issue) == {"code", "severity", "path", "line", "details"}
```

再断言普通模式和 JSON 模式对同一损坏草稿都返回 `1`，JSON `details` 保持 Tasks 2-3 的原始参数。

- [x] **Step 4: 运行 CLI 定向测试并确认当前输出导致失败**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py \
  -k 'check_human or check_clean_and_errors or candidate_check_registered or separates_errors'
```

Expected: failures show current普通输出缺行号/事实/摘要，JSON 缺 `severity`。

- [x] **Step 5: 让 CLI 委托诊断模块**

删除本地 `_issue_payload`，改为：

```python
from draft.diagnostics import issue_payload, render_issue
```

JSON 四个数组统一调用 `issue_payload`。普通模式按以下固定顺序输出：

```python
groups = (report.errors, report.warnings, report.infos, report.reviews)
if not any(groups):
    print("检查通过：未发现客观错误。")
else:
    for issues in groups:
        for issue in issues:
            print(render_issue(issue))
    print(
        f"检查完成：{len(report.errors)} 个错误，{len(report.warnings)} 个警告，"
        f"{len(report.infos)} 个信息，{len(report.reviews)} 个提醒。"
    )
```

不要改 `return 0 if ok else 1`。

- [x] **Step 6: 删除候选检查的独立提示分支**

删除 `main` 中 `_render_check` 之后打印 `提示：目标已是登记草稿...` 的条件块；Task 1 的 `candidate.already_registered` 模板已经在同一诊断行保存 `--candidate` 适用边界。

- [x] **Step 7: 运行完整 CLI 测试和只读回归**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py
```

Expected: all tests pass; no existing非 `check` 命令文本或错误信封发生变化。

- [x] **Step 8: 运行静态检查并提交任务**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py
git add plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py
git commit -m "feat(draft): render actionable check output"
```

---

### Task 5: 发布协议、版本与完整门禁

**Files:**
- Modify: `plugins/draft/skills/draft/references/draft-format.md:90-140`
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml:1-4`
- Modify: `plugins/draft/skills/draft/scripts/runtime/uv.lock:15-17`
- Modify: `plugins/draft/.codex-plugin/plugin.json:1-5`
- Modify: `plugins/draft/.claude-plugin/plugin.json:1-5`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py:107-170`

**Interfaces:**
- Consumes: Tasks 1-4 的最终公开诊断行为。
- Produces: 发布级 `0.2.0` 自包含 Draft skill、正式协议文档和全门禁证据。

- [x] **Step 1: 先把复制 skill 版本断言改为 `0.2.0` 并确认失败**

把 `test_setup_syncs_version_environment_without_domain_cli` 和 `test_copied_skill_runs_standalone_with_isolated_runtime_dir` 中的 `envs/0.1.0` 精确改为 `envs/0.2.0`。

Run:

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py \
  -k 'setup_syncs_version or copied_skill_runs_standalone'
```

Expected: fail because runtime still reports and selects `0.1.0`.

- [x] **Step 2: 同步提升三个发布版本字段**

```toml
# scripts/runtime/pyproject.toml
version = "0.2.0"
```

```json
// both plugin.json files
"version": "0.2.0"
```

不得改变插件名称、hooks、skills 路径或界面元数据。

- [x] **Step 3: 用 uv 更新锁文件并检查只改变项目版本**

```bash
uv lock --project plugins/draft/skills/draft/scripts/runtime
git diff -- plugins/draft/skills/draft/scripts/runtime/uv.lock
```

Expected: `draft-runtime` 从 `0.1.0` 变为 `0.2.0`；第三方依赖版本和来源不变。

- [x] **Step 4: 更新正式检查诊断协议**

在 `draft-format.md` 的“检查器严重级别”之前新增：

- 普通格式 `<path>[:<line>]: <级别> <code>：<事实>`；
- `<ROOT>`、一基行号、无行号状态和控制字符转义规则；
- 四档计数摘要和全绿句子；
- JSON 五字段 `code/severity/path/line/details`；
- Diagnostic Contract Table 的逐错误码位置与 `details` 参数协议；
- `operation.inconsistent_state` 普通摘要要求使用 `--json` 查看完整参数；
- 普通/JSON 都只读且退出码不变。

这一步必须复制本计划的完整参数协议，不能只链接实现源码。

- [x] **Step 5: 运行协议、版本和复制入口定向测试**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py::test_setup_syncs_version_environment_without_domain_cli \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py::test_copied_skill_runs_standalone_with_isolated_runtime_dir
```

Expected: all tests pass; copied skill creates only `envs/0.2.0` and uses public `scripts/draft.py`.

- [x] **Step 6: 运行完整 pytest**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q
```

Expected: all tests pass with no warnings promoted to failures.

- [x] **Step 7: 运行完整静态和格式门禁**

```bash
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check .
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff format --check .
```

Expected: all commands exit `0` without changing files.

- [x] **Step 8: 检查发布差异和版本一致性**

```bash
git diff --check
git diff --stat
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q \
  plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py::test_plugin_manifests_match_the_install_artifact
```

Expected: no whitespace errors; diff only包含本计划列出的诊断、测试、协议和版本文件；三个版本均为 `0.2.0`。

- [x] **Step 9: 提交协议与发布版本**

```bash
git add plugins/draft/skills/draft/references/draft-format.md \
  plugins/draft/skills/draft/scripts/runtime/pyproject.toml \
  plugins/draft/skills/draft/scripts/runtime/uv.lock \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py \
  plugins/draft/.codex-plugin/plugin.json \
  plugins/draft/.claude-plugin/plugin.json
git commit -m "docs(draft): publish actionable check diagnostics"
```

- [x] **Step 10: 提交后复验并记录证据**

```bash
git status --short
git log -5 --oneline
```

Expected: worktree clean; five implementation commits按任务顺序存在；最终交付说明列出行为契约、影响范围、完整 pytest/pyright/ruff/复制入口证据和任何未验证宿主假设。
