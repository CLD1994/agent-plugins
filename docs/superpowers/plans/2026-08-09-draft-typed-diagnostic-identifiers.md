# Typed Diagnostic Identifiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Draft 已登记检查错误码及其诊断分支标识集中为类型化的唯一来源，删除测试中的重复字符串清单，同时保持现有 CLI、JSON 和未知错误码 fallback 行为不变。

**Architecture:** 新建无依赖的 `draft.diagnostic_codes` 模块，用 `StrEnum` 定义 48 个已登记 `CheckCode` 和 7 组诊断分支标识。生产代码只通过枚举成员产生已登记码；`CheckIssue.code` 继续保持 `str`，以便承载未来版本或外部输入的未知码。诊断目录与测试覆盖从 `CheckCode` 派生，集成层 AST 契约防止已登记码重新以裸字符串散落到其他源码。

**Tech Stack:** Python 3.12+、`enum.StrEnum`、pytest、标准库 `ast`、pyright strict、Ruff、uv。

## Global Constraints

- 开始前依次读取仓库根 `AGENTS.md` 与 `plugins/draft/AGENTS.md`；核心与适配层测试必须分树执行。
- 不改变任何错误码 wire value（线上字符串值）、诊断文案、严重级别、路径、行号、详情、排序、退出码或 JSON 字段。
- `CheckIssue.code` 必须继续声明为 `str`；未知码（如测试中的 `future.code`）必须继续无损 fallback，不得强制转换失败。
- 只有 `diagnostic_codes.py` 可以包含 48 个已登记检查码的字符串字面量；其他生产模块和 `test_diagnostics.py` 使用枚举成员。
- `DraftError` 的非检查错误码不在本计划范围内；例如 `link.external`、`adopt.recovery_required`、`runtime.os_error` 继续使用字符串。
- 分支枚举只覆盖已经跨生产者、校验器、渲染器和测试重复的 7 组 discriminator（判别标识）；普通路径、名称、操作 ID 和任意详情仍为 `str`。
- `EXPECTED_CODES` 必须删除；`VARIANT_CODES` 和闭环错误码集合可以保留语义分组，但元素必须是 `CheckCode`，不得再写 wire value。
- `RenderCase.case_id` 必须删除，pytest 展示 ID 从 `case.issue.code` 自动生成；展示名称不是行为契约。
- 不引入新依赖，不修改 `pyproject.toml`、`uv.lock`、生产输出文案或多语言结构。
- 单元测试不得读取源码文件；AST 源码边界检查放在 integration 测试。
- 每个任务按红—绿—重构执行并单独提交；不得删除测试、放宽类型检查或忽略门禁失败。
- 工作区可能包含用户改动；只暂存本计划列出的文件，不得使用 `git reset --hard` 或 `git checkout --` 清理工作区。

## File Structure

- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
  - 唯一拥有已登记检查码与分支 wire value。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
  - 以类型化 key 定义渲染目录，并在入口处区分已登记码与未知字符串。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
  - 所有 `_error`、`_warning`、`_info`、`_review` 调用改用 `CheckCode`。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
  - 图加载产生的已登记问题和分支标识改用类型。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
  - 页面解析产生的已登记问题和分支标识改用类型。
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`
  - 将与 `CheckCode` 重合的稳定错误码引用改为枚举；其他 `DraftError` 字符串不动。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
  - 删除重复错误码集合和手写 case ID，使用类型化用例。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
  - 用 AST 保证已登记码只在权威类型模块中出现字面量。

---

### Task 1: 建立类型化诊断词汇与目录边界

**Files:**
- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py:1-105,580-585`

**Interfaces:**
- Produces: `CheckCode(StrEnum)`，48 个成员；每个成员的 `.value` 是现有 wire value。
- Produces: `RootMissingKind`、`InvalidStatusReason`、`InvalidRelationReason`、`ParseErrorReason`、`LinkEscapeKind`、`ReplacementStatusReason`、`WrongOutputReason`。
- Preserves: `KNOWN_CHECK_CODES: frozenset[str]`、`render_issue(CheckIssue) -> str`、`issue_payload(CheckIssue) -> dict[str, object]`。

- [ ] **Step 1: 先把目录覆盖测试改成依赖尚不存在的权威类型**

删除 `test_diagnostics.py` 中从 `EXPECTED_CODES = {` 开始、到该集合右花括号结束的整个赋值，添加导入并替换两个覆盖断言：

```python
from draft.diagnostic_codes import CheckCode
from draft.diagnostics import KNOWN_CHECK_CODES, issue_payload, render_issue


def test_catalog_covers_every_typed_check_code() -> None:
    assert KNOWN_CHECK_CODES == frozenset(code.value for code in CheckCode)


def test_valid_render_cases_cover_every_published_code() -> None:
    assert {case.issue.code for case in VALID_RENDER_CASES} == KNOWN_CHECK_CODES
```

本步骤只改 import、删除 `EXPECTED_CODES`、替换上述两个测试；其余 render case 暂不迁移。

- [ ] **Step 2: 运行测试并确认红灯来自缺失类型模块**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_diagnostics.py" -q
```

Expected: collection ERROR，明确包含 `ModuleNotFoundError: No module named 'draft.diagnostic_codes'`；不得接受其他失败原因。

- [ ] **Step 3: 创建唯一诊断标识模块**

创建 `src/draft/diagnostic_codes.py`，内容如下：

```python
from __future__ import annotations

from enum import StrEnum


class CheckCode(StrEnum):
    CANDIDATE_ALREADY_REGISTERED = "candidate.already_registered"
    CANDIDATE_INTERNAL_STATE = "candidate.internal_state"
    FORMAT_MISSING = "format.missing"
    FORMAT_INVALID = "format.invalid"
    FORMAT_UNSUPPORTED = "format.unsupported"
    ROOT_MISSING_FILE = "root.missing_file"
    PAGE_UNMANAGED_PATH = "page.unmanaged_path"
    PAGE_MISSING_TITLE = "page.missing_title"
    PAGE_MISSING_MACHINE_BLOCK = "page.missing_machine_block"
    PAGE_MISSING_STATUS = "page.missing_status"
    PAGE_DUPLICATE_STATUS = "page.duplicate_status"
    PAGE_UNKNOWN_LABEL = "page.unknown_label"
    PAGE_INVALID_STATUS = "page.invalid_status"
    PAGE_INVALID_RELATION = "page.invalid_relation"
    PAGE_PARSE_ERROR = "page.parse_error"
    PAGE_CAPACITY = "page.capacity"
    LINK_MISSING = "link.missing"
    LINK_ESCAPE = "link.escape"
    PATH_SYMLINK_ESCAPE = "path.symlink_escape"
    RELATION_DUPLICATE = "relation.duplicate"
    PARENT_ROOT_DECLARED = "parent.root_declared"
    PARENT_MISSING = "parent.missing"
    PARENT_MULTIPLE = "parent.multiple"
    LAYOUT_PARENT_MISMATCH = "layout.parent_mismatch"
    PARENT_CYCLE = "parent.cycle"
    PARENT_UNREACHABLE = "parent.unreachable"
    PARENT_NAVIGATION_MISSING = "parent.navigation_missing"
    HUB_EMPTY = "hub.empty"
    DEPENDENCY_SELF = "dependency.self"
    DEPENDENCY_CYCLE = "dependency.cycle"
    REPLACEMENT_PAIR_MISSING = "replacement.pair_missing"
    REPLACEMENT_STATUS_MISMATCH = "replacement.status_mismatch"
    REPLACEMENT_CYCLE = "replacement.cycle"
    CHALLENGE_PAIR_MISSING = "challenge.pair_missing"
    OUTPUT_FORBIDDEN = "output.forbidden"
    OUTPUT_MISSING = "output.missing"
    OUTPUT_WRONG_TARGET = "output.wrong_target"
    OUTPUT_BACKLINK_MISSING = "output.backlink_missing"
    OPERATION_JOURNAL_CORRUPT = "operation.journal_corrupt"
    OPERATION_RECOVERY_REQUIRED = "operation.recovery_required"
    OPERATION_ORPHAN_ALLOCATING = "operation.orphan_allocating"
    OPERATION_INCONSISTENT_STATE = "operation.inconsistent_state"
    TRASH_DEADLINES_INVALID = "trash.deadlines_invalid"
    TRASH_PACKAGE_MISSING = "trash.package_missing"
    TRASH_PACKAGE_CORRUPT = "trash.package_corrupt"
    TRASH_RETAINED = "trash.retained"
    TRASH_HANDOFF_PENDING = "trash.handoff_pending"
    TRASH_PENDING_UNEXPLAINED = "trash.pending_unexplained"


class RootMissingKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class InvalidStatusReason(StrEnum):
    NOT_SINGLE_CODE_SPAN = "not_single_code_span"
    EMPTY_EXPLANATION = "empty_explanation"
    UNKNOWN_PREFIX = "unknown_prefix"


class InvalidRelationReason(StrEnum):
    LINK_CARDINALITY_OR_IMAGE = "link_cardinality_or_image"
    EXTRA_CONTENT = "extra_content"


class ParseErrorReason(StrEnum):
    LINK_DESTINATION_MISSING = "link_destination_missing"
    IMAGE_DESTINATION_MISSING = "image_destination_missing"
    SOURCE_ALIGNMENT_FAILED = "source_alignment_failed"
    LAZY_CONTINUATION = "lazy_continuation"
    MACHINE_BLOCK_MAP_MISSING = "machine_block_map_missing"


class LinkEscapeKind(StrEnum):
    DESTINATION = "destination"
    MANAGED_PATH = "managed_path"


class ReplacementStatusReason(StrEnum):
    RELATION_REQUIRES_STATUS = "relation_requires_status"
    STATUS_REQUIRES_RELATION = "status_requires_relation"


class WrongOutputReason(StrEnum):
    DUPLICATE = "duplicate"
    NOT_CONCLUSIONS = "not_conclusions"
```

Do not add `KNOWN_CHECK_CODES` to this module; the enum defines the published vocabulary, while `diagnostics.py` continues to expose the actually registered catalog.

- [ ] **Step 4: 将诊断目录改为类型化 key 和分支标识**

在 `diagnostics.py` 导入全部八个枚举。将 `_SPECS` 的 key 类型和每个 key 表达式按一一对应关系迁移；每个 `DiagnosticSpec` 右侧表达式保持不变。例如第一项必须从：

```python
_SPECS: dict[str, DiagnosticSpec] = {
    "candidate.already_registered": DiagnosticSpec(
        _FILE, _fixed(0), _text("目标已经登记，--candidate 仅适用于未登记候选")
    ),
}
```

变为：

```python
_SPECS: dict[CheckCode, DiagnosticSpec] = {
    CheckCode.CANDIDATE_ALREADY_REGISTERED: DiagnosticSpec(
        _FILE, _fixed(0), _text("目标已经登记，--candidate 仅适用于未登记候选")
    ),
}

KNOWN_CHECK_CODES: frozenset[str] = frozenset(code.value for code in _SPECS)
```

七个调用 `_variant` 的诊断规则都使用对应枚举 `.value` 作为 key；`_variant` 本身继续接收 `Mapping[str, int]`，不用重写。`page.invalid_status` 必须变为：

```python
_variant(
    {
        InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value: 1,
        InvalidStatusReason.EMPTY_EXPLANATION.value: 1,
        InvalidStatusReason.UNKNOWN_PREFIX.value: 2,
    }
)
```

所有 renderer 分支比较只把右侧字符串替换为相应枚举 `.value`，分支返回值保持原样。例如 `branch == "not_single_code_span"` 改为 `branch == InvalidStatusReason.NOT_SINGLE_CODE_SPAN.value`；`details[0] == "duplicate"` 改为 `details[0] == WrongOutputReason.DUPLICATE.value`。

`render_issue` 必须显式区分未知字符串，不能直接构造枚举并让 `ValueError` 泄漏：

```python
try:
    registered_code = CheckCode(issue.code)
except ValueError:
    spec = None
else:
    spec = _SPECS.get(registered_code)
```

保留后续位置校验、详情校验和 fallback 逻辑不变。

- [ ] **Step 5: 运行诊断测试并修复类型问题**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_diagnostics.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/src/draft/diagnostic_codes.py" \
  "$RUNTIME/src/draft/diagnostics.py" \
  "$RUNTIME/tests/unit/test_diagnostics.py"
uv run --project "$RUNTIME" --locked ruff format --check \
  "$RUNTIME/src/draft/diagnostic_codes.py" \
  "$RUNTIME/src/draft/diagnostics.py" \
  "$RUNTIME/tests/unit/test_diagnostics.py"
```

Expected: diagnostics tests PASS；pyright 为 `0 errors`；Ruff 两条命令 exit `0`。

- [ ] **Step 6: 提交权威类型与目录改造**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py
git diff --cached --check
git commit -m "refactor(draft): type registered diagnostic identifiers"
```

---

### Task 2: 类型化解析与加载路径的诊断产生位置

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py`

**Interfaces:**
- Consumes: Task 1 的 `CheckCode`、`RootMissingKind`、`InvalidStatusReason`、`InvalidRelationReason`、`ParseErrorReason`、`LinkEscapeKind`。
- Produces: `error_issue(code: CheckCode, path: Path | None, line: int | None = None, *details: str) -> CheckIssue`。
- Produces: AST 契约 `test_registered_diagnostic_codes_have_one_literal_owner()`，覆盖 48 个已登记 code wire value。分支值由各生产者和 renderer 的枚举引用审计，不纳入全局值扫描，因为 `file` 同时也是 `LocationKind` 的独立合法值。

- [ ] **Step 1: 添加源码唯一来源的失败契约**

在 `test_distribution_contract.py` 添加 `ast`、`Path` 和 `CheckCode` 导入，并加入：

```python
import ast
from pathlib import Path

from draft.diagnostic_codes import CheckCode


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
```

`diagnostic_codes.py` 不得放进 consumer 集合，因为它是唯一合法字面量 owner（拥有者）。

- [ ] **Step 2: 运行架构契约并确认它列出四个待迁移模块**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" -q
```

Expected: FAIL；offenders 至少包含 `graph.py`、`markdown.py`、`lifecycle.py`、`paths.py`，不得因 AST 解析或路径错误失败。

- [ ] **Step 3: 迁移 graph.py**

导入 `CheckCode` 与 `LinkEscapeKind`。将 `error_issue` 收窄并在模型边界转回普通字符串：

```python
def error_issue(
    code: CheckCode,
    path: Path | None,
    line: int | None = None,
    *details: str,
) -> CheckIssue:
    return CheckIssue(
        code=code.value,
        severity=IssueSeverity.ERROR,
        path=path,
        line=line,
        details=details,
    )
```

把传给 `error_issue` 的已登记码替换为 `CheckCode` 成员；直接传给 `DraftError` 的已登记码使用对应成员的 `.value`，保证异常对象继续保存普通字符串。把 `managed_path`、`destination` 替换为 `LinkEscapeKind` 的 `.value`。以下字符串不属于已登记 check catalog，必须保持原样：

```python
"link.external"
"adopt.recovery_required"
```

从捕获的 `DraftError` 直接复制 `error.code` 的位置保持动态字符串，不做强制枚举转换；其来源将在本任务的 markdown/marker 生产点类型化。

- [ ] **Step 4: 迁移 markdown.py 的已登记码和分支原因**

导入 `CheckCode`、`InvalidRelationReason`、`InvalidStatusReason`、`ParseErrorReason`。所有属于 48 个已登记码的 `DraftError` 第一参数改为 `CheckCode` 成员的 `.value`；详情首项改为对应原因的 `.value`。例如：

```python
raise DraftError(
    CheckCode.PAGE_PARSE_ERROR.value,
    details=(ParseErrorReason.LINK_DESTINATION_MISSING.value,),
)

raise DraftError(
    CheckCode.PAGE_INVALID_STATUS.value,
    path,
    details=(InvalidStatusReason.UNKNOWN_PREFIX.value, prefix_text),
    line=line,
)
```

`markdown.invalid_edit` 与 `markdown.overlapping_edits` 不属于检查目录，保持字符串。

- [ ] **Step 5: 迁移 lifecycle.py 与 paths.py 的重合码**

两文件导入 `CheckCode`，只替换 AST 契约报告的值，并在传给 `DraftError` 时使用 `.value`：

```python
CheckCode.FORMAT_INVALID.value
CheckCode.PATH_SYMLINK_ESCAPE.value
```

其他生命周期、路径和操作错误码保持字符串，不扩大本任务范围。

- [ ] **Step 6: 验证架构契约和受影响行为**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_markdown.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/src/draft/graph.py" \
  "$RUNTIME/src/draft/markdown.py" \
  "$RUNTIME/src/draft/lifecycle.py" \
  "$RUNTIME/src/draft/paths.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py"
```

Expected: all selected tests PASS；pyright `0 errors`；Ruff exit `0`。

- [ ] **Step 7: 提交解析与加载路径迁移**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py
git diff --cached --check
git commit -m "refactor(draft): type parser diagnostic producers"
```

---

### Task 3: 类型化检查器与操作路径的诊断产生位置

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`

**Interfaces:**
- Consumes: Task 1 的 `CheckCode`、`RootMissingKind`、`ReplacementStatusReason`、`WrongOutputReason`。
- Produces: `_error`、`_warning`、`_info`、`_review` 的 `code` 参数均为 `CheckCode`，返回 `CheckIssue`。
- Extends: AST 唯一来源契约覆盖全部核心生产模块。

- [ ] **Step 1: 先把 AST consumer 集合扩展到整个核心包并制造红灯**

将 Task 2 的固定 consumer tuple 替换为动态发现；权威 owner 明确排除，未来新增模块会自动进入契约：

```python
DIAGNOSTIC_CODE_OWNER = RUNTIME_ROOT / "src/draft/diagnostic_codes.py"
DIAGNOSTIC_CODE_LITERAL_CONSUMERS = tuple(
    path
    for path in sorted((RUNTIME_ROOT / "src/draft").glob("*.py"))
    if path != DIAGNOSTIC_CODE_OWNER
)
```

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" -q
```

Expected: FAIL，并列出 `checker.py`、`operations.py`、`structure.py`、`trash.py`、`trash_lifecycle.py`、`trash_package.py`；已经迁移的 Task 2 文件不得再次出现。

- [ ] **Step 2: 收窄 checker.py 的问题构造边界**

导入 `CheckCode`、`RootMissingKind`、`ReplacementStatusReason`、`WrongOutputReason`。四个 helper 的 `code` 参数都改成 `CheckCode`，存入 `CheckIssue` 时使用 `.value`：

```python
def _error(
    code: CheckCode,
    path: Path | None,
    line: int | None = None,
    *details: str,
) -> CheckIssue:
    return CheckIssue(
        code=code.value,
        severity=IssueSeverity.ERROR,
        path=path,
        line=line,
        details=details,
    )
```

`_warning`、`_info`、`_review` 使用相同转换规则。把 checker 中全部 32 个已登记码替换为成员，并替换四组分支详情：

```python
RootMissingKind.FILE.value
RootMissingKind.DIRECTORY.value
ReplacementStatusReason.RELATION_REQUIRES_STATUS.value
ReplacementStatusReason.STATUS_REQUIRES_RELATION.value
WrongOutputReason.DUPLICATE.value
WrongOutputReason.NOT_CONCLUSIONS.value
```

不要把 `mode == "registered"`、文件名、阶段值、关系值等普通业务字符串改成枚举。

- [ ] **Step 3: 迁移六个操作与回收模块**

每个文件导入 `CheckCode`，将 AST 契约报告的完整已登记码替换为对应成员的 `.value`，确保 `DraftError.code` 的运行时值仍是普通字符串。只迁移以下值的实际出现位置：

```python
CheckCode.HUB_EMPTY.value
CheckCode.OPERATION_INCONSISTENT_STATE.value
CheckCode.OPERATION_JOURNAL_CORRUPT.value
CheckCode.OPERATION_RECOVERY_REQUIRED.value
CheckCode.PATH_SYMLINK_ESCAPE.value
CheckCode.TRASH_PACKAGE_CORRUPT.value
```

若某文件没有其中某个值，不新增无用 import 或引用。所有不属于 `CheckCode` 的操作错误码保持字符串。

- [ ] **Step 4: 验证全部生产模块不再复制已登记码**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" \
  "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration" -q -m 'not network'
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME"
```

Expected: architecture test PASS；核心 unit 与非 network integration 全部 PASS；pyright `0 errors`；Ruff exit `0`。

- [ ] **Step 5: 提交检查器与操作路径迁移**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
  plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py
git diff --cached --check
git commit -m "refactor(draft): type check diagnostic producers"
```

---

### Task 4: 删除诊断测试中的非类型重复清单与显示标签

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`

**Interfaces:**
- Consumes: 全部 `CheckCode` 与 7 组分支枚举。
- Preserves: `VALID_RENDER_CASES` 对全部已登记码的合法渲染覆盖。
- Produces: 类型化 `VARIANT_CODES: tuple[CheckCode, ...]` 和 `CYCLE_CODES: tuple[CheckCode, ...]`。

- [ ] **Step 1: 先删除 RenderCase 的手写 ID 字段并扩展 AST 契约**

将 `RenderCase` 改为：

```python
@dataclass(frozen=True)
class RenderCase:
    issue: CheckIssue
    visible_detail_indexes: tuple[int, ...] = ()
```

并把测试文件追加到动态 AST consumer tuple：

```python
DIAGNOSTIC_CODE_LITERAL_CONSUMERS = (
    *DIAGNOSTIC_CODE_LITERAL_CONSUMERS,
    RUNTIME_ROOT / "tests/unit/test_diagnostics.py",
)
```

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_diagnostics.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" -q
```

Expected: FAIL；单元测试因旧 `RenderCase` 位置参数不再匹配而在收集阶段报错，AST 契约同时报告测试文件中的已登记裸字符串。

- [ ] **Step 2: 把全部合法 render case 改为类型化输入并删除重复 ID**

每个 `CheckIssue` 的已登记 code 改为对应 `CheckCode` 成员的 `.value`，保证 `CheckIssue.code` 的运行时类型仍是普通 `str`。删除每个 `RenderCase` 原第二位置参数 `case_id`；原第三位置参数 `visible_detail_indexes` 移到第二位置。三种机械转换必须分别按下例执行：

```python
# 无可见详情
RenderCase(
    CheckIssue(
        CheckCode.FORMAT_MISSING.value,
        IssueSeverity.ERROR,
        Path(".draft/draft.json"),
        None,
    )
)

# 有可见详情
RenderCase(
    CheckIssue(
        CheckCode.ROOT_MISSING_FILE.value,
        IssueSeverity.ERROR,
        None,
        None,
        (RootMissingKind.FILE.value, "index.md"),
    ),
    (1,),
)

# 有分支原因
RenderCase(
    CheckIssue(
        CheckCode.PAGE_INVALID_STATUS.value,
        IssueSeverity.ERROR,
        Path("pages/x.md"),
        3,
        (InvalidStatusReason.UNKNOWN_PREFIX.value, "进行中"),
    ),
    (1,),
)
```

替换 `@pytest.mark.parametrize` 的 ID 生成：

```python
@pytest.mark.parametrize(
    "case",
    VALID_RENDER_CASES,
    ids=lambda case: case.issue.code,
)
```

pytest 会自动为重复 code 添加序号；不得重新创建复合字符串 ID。

- [ ] **Step 3: 将变体和闭环分组改为类型化集合**

保留变体集合的业务含义，但成员使用类型：

```python
VARIANT_CODES: tuple[CheckCode, ...] = (
    CheckCode.ROOT_MISSING_FILE,
    CheckCode.PAGE_INVALID_STATUS,
    CheckCode.PAGE_INVALID_RELATION,
    CheckCode.PAGE_PARSE_ERROR,
    CheckCode.LINK_MISSING,
    CheckCode.LINK_ESCAPE,
    CheckCode.REPLACEMENT_STATUS_MISMATCH,
    CheckCode.OUTPUT_WRONG_TARGET,
)

VARIANT_GROUPS = [
    pytest.param(
        tuple(case.issue for case in VALID_RENDER_CASES if case.issue.code == code.value),
        id=code.value,
    )
    for code in VARIANT_CODES
]
```

将 `CYCLE_FALLBACK_CASES` 的三个重复 `CheckIssue` 改成类型化 code 参数：

```python
CYCLE_CODES: tuple[CheckCode, ...] = (
    CheckCode.PARENT_CYCLE,
    CheckCode.DEPENDENCY_CYCLE,
    CheckCode.REPLACEMENT_CYCLE,
)


@pytest.mark.parametrize("code", CYCLE_CODES, ids=lambda code: code.value)
def test_unclosed_cycle_fallback_is_one_line_and_lossless(code: CheckCode) -> None:
    issue = CheckIssue(
        code.value,
        IssueSeverity.ERROR,
        Path("pages/a.md"),
        None,
        ("pages/a.md", "pages/b.md"),
    )
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered
```

- [ ] **Step 4: 类型化其余已知码，保留刻意的未知字符串**

文件中下列场景使用对应类型成员：invalid location、non-positive line、complex issue、control-character path、`issue_payload` 已知码输入。完整 payload 期望中的 code 与 severity 分别来自枚举：

```python
assert issue_payload(issue) == {
    "code": CheckCode.PAGE_CAPACITY.value,
    "severity": IssueSeverity.REVIEW.value,
    "path": "pages/a.md",
    "line": None,
    "details": ["601", "70000"],
}
```

以下值必须保持普通字符串，因为它们验证开放世界输入，而不是已登记目录：

```python
"future.code"
"bad\ncode"
```

路径、详情、JSON 字段名也保持字符串。

- [ ] **Step 5: 运行诊断测试、AST 契约和静态门禁**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_diagnostics.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/tests/unit/test_diagnostics.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py"
uv run --project "$RUNTIME" --locked ruff format --check \
  "$RUNTIME/tests/unit/test_diagnostics.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py"
```

Expected: all tests PASS；pyright `0 errors`；Ruff 两条命令 exit `0`。

- [ ] **Step 6: 提交测试去重**

```bash
git add \
  plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py \
  plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py
git diff --cached --check
git commit -m "test(draft): derive diagnostic cases from typed codes"
```

---

### Task 5: 变异证明、完整门禁与交付审计

**Files:**
- Verify only; no permanent file changes.

**Interfaces:**
- Verifies: 已登记码字面量唯一来源、未知码 fallback、全部测试树和静态门禁。

- [ ] **Step 1: 证明 AST 契约能捕获裸字符串回归**

使用 `apply_patch` 临时把 `checker.py` 中一个枚举引用改回其 wire value，例如：

```python
_review("page.capacity", path, str(lines), str(size))
```

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_distribution_contract.py::test_registered_diagnostic_codes_have_one_literal_owner" -q
```

Expected: FAIL，offenders 精确包含 `src/draft/checker.py`、修改行号和 `page.capacity`。

立即用反向 `apply_patch` 恢复 `CheckCode.PAGE_CAPACITY`；不得用会覆盖用户改动的 Git 恢复命令。重新运行同一测试，Expected: PASS。执行 `git diff --check` 与 `git status --short`，确认临时变异没有留下未提交差异；若实施前工作区有用户基线改动，只允许显示那些原有路径。

- [ ] **Step 2: 运行核心与适配层单元测试**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
```

Expected: 两条命令均 PASS。

- [ ] **Step 3: 运行非联网集成测试**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
```

Expected: 两条命令均 PASS。

- [ ] **Step 4: 运行 network 集成测试**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```

Expected: 两条命令均 PASS。网络或索引失败必须单独诊断，不得取消测试标记或弱化断言。

- [ ] **Step 5: 运行双树类型、lint、格式、锁和差异门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
(cd "$RUNTIME" && uv run --locked pyright)
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff check plugins/draft/scripts
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff format --check plugins/draft/scripts
uv lock --project "$RUNTIME" --check
git diff --check
```

Expected: 全部 exit code `0`，两个 pyright 命令均报告 `0 errors`。

- [ ] **Step 6: 最终语义审计**

逐项确认：

- `diagnostic_codes.py` 是唯一包含 48 个已登记 wire value 字面量的文件。
- `KNOWN_CHECK_CODES == frozenset(code.value for code in CheckCode)`。
- `_SPECS` 对每个 `CheckCode` 恰有一个 entry。
- 每个 `CheckCode` 至少有一个合法 `RenderCase`。
- `EXPECTED_CODES` 与 `RenderCase.case_id` 已删除。
- `VARIANT_CODES` 与 `CYCLE_CODES` 只包含 `CheckCode` 成员。
- 七组分支 discriminator 在生产者、校验器、renderer 和测试中均使用对应枚举。
- `CheckIssue.code` 仍为 `str`，`future.code` 的单行无损 fallback 测试仍通过。
- `issue_payload` 和 CLI JSON 中的 code 仍是原字符串，不出现枚举成员名。
- `pyproject.toml`、`uv.lock`、诊断文案和 AGENTS.md 没有变化。
- `git status --short` 为空；若实施前已有用户改动，只能保留那些基线改动并明确报告。

无需新增提交；Task 1–4 的四个提交即完整实施结果。

## Implementation Agent Handoff

实施 Agent 完成后必须把以下材料原样交给 reviewer，不自行宣称最终验收完成：

- 四个实施提交的 hash、subject 和各提交文件列表。
- Task 1–4 每次红灯的失败原因，以及对应绿灯命令结果。
- Task 5 AST 临时变异的失败输出和恢复后的通过输出。
- 六组测试的 passed/deselected 计数。
- 双树 pyright、双树 Ruff、format、`uv lock --check`、`git diff --check` 的 exit code。
- 最终 `git status --short` 和 `git diff <实施前基线>..HEAD --stat`。
- 任何偏离本计划的文件、类型名、枚举成员名或测试命令；没有偏离也要明确写“无偏离”。

Reviewer 将独立检查提交边界、运行关键定向测试、审计 48 个 wire value 与七组分支值未改变，并决定是否接受或要求返工。
