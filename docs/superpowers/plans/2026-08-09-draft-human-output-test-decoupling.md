# Draft 人类输出测试去文案耦合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 Draft 核心 runtime 生产行为的前提下，移除诊断和 CLI 测试对具体人类可读文案的依赖，改为验证结构化协议、输入事实传播、分支选择、输出编排和安全不变量。

**Architecture:** 继续以现有 `CheckIssue(code, severity, path, line, details)`、JSON payload 和退出码作为稳定语义边界，不引入 `Localizer`、locale catalog 或新的生产接口。`render_issue()` 与 CLI 人类输出通过关系断言、测试替身和变异证明验证；具体中文措辞不再充当测试 oracle（判定依据）。

**Tech Stack:** Python `>=3.12,<3.15`、pytest、pytest-mock、Pyright strict、Ruff、uv。

## Global Constraints

- 任务作用域仅包含核心 runtime：
  - `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
  - `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
  - `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- 不修改 `src/draft/diagnostics.py`、`src/draft/cli.py` 或其他生产文件；变异证明中的临时错误改动必须立即恢复，不得暂存或提交。
- 不修改适配层 `plugins/draft/scripts/tests/**/test_cli.py`；适配层属于独立子系统，不在本计划中顺手重构。
- 不引入多语言框架、`Localizer`、locale catalog、翻译资源或 catalog 契约测试。
- 不改变 CLI 文案、输出行数、输出顺序、JSON schema、错误码、命令名、路径、行号、严重级别、退出码或读写行为。
- 测试不得使用具体人类可读文案作为相等、包含、前后缀或参数化期望值；允许精确断言机器协议字面量，例如 error code、`result_type`、phase、命令名、选项名、JSON 字段和 `<ROOT>`。
- 允许关系断言：输出包含由测试输入动态生成的路径、行号、操作 ID、错误码、选项名和经过独立 JSON 转义得到的 details；这类值不是本地化目标文案。
- 允许测试替身返回运行期生成的 token，并断言 CLI 原样输出这些 token；不得把生产中文文案复制到 token 或期望值中。
- 保留 `KNOWN_CHECK_CODES == EXPECTED_CODES`、`issue_payload()` 完整字典、JSON payload、错误码和退出码的精确断言。
- 保留所有现有诊断码和合法 details 分支的执行覆盖；先添加新的语义/不变量契约，再删除旧文案断言。
- 这是测试重构而非生产功能开发，不制造虚假的初始红灯。对已有正确行为使用“基线通过 → 添加替代契约 → 局部变异证明新契约会失败 → 恢复生产代码 → 删除旧断言”的循环。
- 不提交通过扫描测试源码来冻结断言写法的测试；文案字面量审计只作为实施期间的一次性检查。
- 单元测试保持同进程、无真实文件系统、无子进程；集成测试继续使用真实 runtime 协作者和现有临时目录固件。
- 执行每个 Task 时，先在该 Task 使用的 shell session 中设置 `RUNTIME=plugins/draft/skills/draft/scripts/runtime`；若更换 shell session，必须重新设置。
- 开始 Task 1 前记录 `git status --short`；计划文件或用户已有改动属于基线状态，不得暂存、覆盖或要求最终消失。
- 任何门禁失败都先定位根因；不得删除覆盖、放宽 Pyright/Ruff 或吞掉失败。

---

## File Structure

- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
  - 保留诊断目录、所有合法分支、fallback、转义和 JSON payload 覆盖；将完整中文期望替换为结构化 case、动态事实传播和不变量断言。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
  - 将 `_dispatch()` 的人类文案断言替换为动态事实断言；新增 `_render_check()` 的渲染协作者隔离测试，验证分组顺序、次数、摘要计数和退出码。
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
  - 保留真实 checker、diagnostics 和 CLI 的组合验证；删除重复的具体中文断言，改为路径、行号、目标、行数、结构化 JSON 和状态不变量。

## Behavioral Contract

### 必须精确验证的机器契约

- `CheckIssue.code`、`severity.value`、`path`、`line`、`details`。
- `KNOWN_CHECK_CODES` 的完整集合。
- JSON 的 `ok`、`result_type`、四个严重级别数组、error code、phase、路径列表和字段集合。
- CLI 命令、选项、协作者参数、退出码以及检查只读性。

### 不得冻结的内容

- 中文诊断事实、严重级别中文名称、检查通过句、检查摘要句、成功/失败动词和提示语。
- 中文标点、空格风格和文案前后缀。
- 参数化表中的完整或局部中文渲染期望。

### 替代不变量

- 合法诊断渲染结果是非空 `str` 且恰好一行。
- 合法已登记诊断不显示机器 error code；未知或参数/位置无效的诊断保留 code 和原始 details。
- 路径、正数行号和应展示的动态 details 经过单行安全转义后出现在结果中。
- 同一 code 的不同合法变体走不同消息分支；不要求测试知道各分支的具体措辞。
- `_render_check()` 按 error、warning、info、review 分组调用 `render_issue()`，每个 issue 恰好一次；JSON 模式不调用人类渲染器。
- CLI 人类输出保留由命令输入或结构化结果产生的路径、操作 ID、phase、warning code、details 和数量事实。

---

### Task 1: 将诊断测试改为语义与不变量契约

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
- Temporary mutation only: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`

**Interfaces:**
- Consumes: `KNOWN_CHECK_CODES`、`render_issue(issue: CheckIssue) -> str`、`issue_payload(issue: CheckIssue) -> dict[str, object]`。
- Produces: 测试内 `RenderCase`、`_escaped_fact()`、`_assert_single_line()` 和 `_assert_valid_render()`；后续任务不导入这些测试辅助对象。

- [ ] **Step 1: 记录诊断测试基线**

Run:

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
git status --short
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_diagnostics.py" -q
```

Expected: 记录当前 status 作为实施基线；pytest exit code `0`，无失败。若基线失败，停止并先报告根因，不开始重构。

- [ ] **Step 2: 添加独立转义和单行断言辅助函数**

在 import 区增加 `json` 和 `dataclass`，并在常量之后加入：

```python
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderCase:
    issue: CheckIssue
    case_id: str
    visible_detail_indexes: tuple[int, ...] = ()


def _escaped_fact(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _assert_single_line(rendered: str) -> None:
    assert rendered
    assert rendered.splitlines() == [rendered]


def _assert_valid_render(case: RenderCase) -> str:
    issue = case.issue
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code not in rendered
    if issue.path is None:
        assert "<ROOT>" in rendered
    else:
        assert _escaped_fact(issue.path.as_posix()) in rendered
    if issue.line is not None and issue.line > 0:
        assert str(issue.line) in rendered
    for index in case.visible_detail_indexes:
        assert _escaped_fact(issue.details[index]) in rendered
    return rendered
```

这里的期望全部来自结构化输入；不得在辅助函数中加入严重级别中文名、fallback 中文提示或消息正文。

- [ ] **Step 3: 将 `RENDER_CASES` 转换为无文案期望的 `VALID_RENDER_CASES`**

保留现有每个 `pytest.param` 的 `CheckIssue` 输入和 `id`，删除第二个完整渲染字符串，改写为 `RenderCase`。固定消息不设置 `visible_detail_indexes`；带动态展示参数的 case 按以下规则标记：

| Case family | `visible_detail_indexes` |
| --- | --- |
| `root.missing_file` | `(1,)`；kind 是分支选择器，不要求直接展示 |
| `page.unknown_label` | `(0,)` |
| `page.invalid_status.unknown_prefix` | `(1,)` |
| 两个 `page.invalid_relation` 变体 | `(1,)` |
| `page.parse_error.source_alignment_failed` | `(1,)` |
| `page.capacity` | `(0, 1)` |
| `link.missing.same` | `(1,)` |
| `link.missing.distinct` | `(0, 1)` |
| 两个 `link.escape` 变体 | `(1,)` |
| `relation.duplicate`、`layout.parent_mismatch` | `(0, 1)` |
| 三种 cycle 的闭合环和 self-loop | details 的全部索引 |
| `parent.navigation_missing` | `(0,)` |
| `replacement.pair_missing`、`challenge.pair_missing` | `(0, 1)` |
| `output.wrong_target.not_conclusions` | `(1,)` |
| `output.backlink_missing` | `(0,)` |
| `operation.recovery_required`、`operation.inconsistent_state` | `(0,)` |
| 六种带 operation ID 的 trash 诊断 | `(0,)` |

结构示例：

```python
VALID_RENDER_CASES = [
    RenderCase(
        CheckIssue(
            "link.missing",
            IssueSeverity.ERROR,
            Path("conclusions.md"),
            8,
            ("./a.md", "pages/a.md"),
        ),
        "link.missing.distinct",
        (0, 1),
    ),
]
```

实际列表必须覆盖旧 `RENDER_CASES` 的全部 case，不能只保留示例。

把现有 `SELF_LOOP_CASES` 的三个合法自环输入合并进 `VALID_RENDER_CASES`，删除单独的完整字符串期望表和 `test_render_issue_for_self_loop_cycles()`。新增一个 `root.missing_file` directory 输入，使 file/directory 两个合法分支都进入该矩阵。

旧的 `test_link_missing_is_a_complete_single_line()`、`test_link_missing_with_distinct_literal_and_normalized_target()` 和 `test_file_location_omits_a_fake_line()` 与矩阵重复；确认对应 `RenderCase` 已存在后删除，不保留其完整中文断言。

- [ ] **Step 4: 用结构化覆盖和不变量测试替换完整字符串参数化测试**

```python
def test_valid_render_cases_cover_every_published_code() -> None:
    assert {case.issue.code for case in VALID_RENDER_CASES} == EXPECTED_CODES


@pytest.mark.parametrize(
    "case",
    VALID_RENDER_CASES,
    ids=[case.case_id for case in VALID_RENDER_CASES],
)
def test_each_catalog_case_renders_valid_single_line(case: RenderCase) -> None:
    _assert_valid_render(case)
```

保留 `test_catalog_covers_the_published_check_codes()`；一个测试约束生产目录，一个测试约束 case 矩阵，二者责任不同。

- [ ] **Step 5: 添加同 code 变体的变形测试**

建立以下变体组，每组沿用当前合法 `CheckIssue` 输入：

- `root.missing_file`: file / directory。
- `page.invalid_status`: `not_single_code_span` / `empty_explanation` / `unknown_prefix`。
- `page.invalid_relation`: `link_cardinality_or_image` / `extra_content`。
- `page.parse_error`: 五个现有原因。
- `link.missing`: literal 与 normalized 相同 / 不同。
- `link.escape`: `destination` / `managed_path`。
- `replacement.status_mismatch`: 两个方向。
- `output.wrong_target`: `duplicate` / `not_conclusions`。

```python
VARIANT_CODES = (
    "root.missing_file",
    "page.invalid_status",
    "page.invalid_relation",
    "page.parse_error",
    "link.missing",
    "link.escape",
    "replacement.status_mismatch",
    "output.wrong_target",
)
VARIANT_GROUPS = [
    tuple(
        case.issue
        for case in VALID_RENDER_CASES
        if case.issue.code == code
    )
    for code in VARIANT_CODES
]


@pytest.mark.parametrize("issues", VARIANT_GROUPS)
def test_registered_variants_choose_distinct_messages(
    issues: tuple[CheckIssue, ...],
) -> None:
    rendered = [render_issue(issue) for issue in issues]
    for text, issue in zip(rendered, issues, strict=True):
        _assert_single_line(text)
        assert issue.code not in text
    assert len(set(rendered)) == len(rendered)
```

该测试只验证分支选择结果可区分，不规定每个分支使用哪一句中文。

- [ ] **Step 6: 将 fallback 和边界测试改为关系断言**

用以下契约替换当前对 fallback 中文正文的断言：

```python
def test_unknown_issue_fallback_is_one_line_and_lossless() -> None:
    issue = CheckIssue("future.code", IssueSeverity.WARNING, None, None, ("a\nb", "value"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_known_code_with_invalid_location_exposes_code_and_raw_details() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), None, ("x", "x"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert issue.code in rendered
    assert json.dumps(list(issue.details), ensure_ascii=False) in rendered


def test_known_code_with_non_positive_line_does_not_render_fake_line() -> None:
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path("a.md"), 0, ("x", "x"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert f"{issue.path.as_posix()}:{issue.line}" not in rendered
    assert issue.code in rendered
```

对未闭合 cycle 使用同一关系：code 可见、完整 JSON details 可见、输出单行。`operation.inconsistent_state` 继续断言机器选项 `"--json" in rendered`，不再断言其周围中文。

删除重复的 `test_root_location_renders_root_marker()` 和 `test_unknown_or_invalid_issue_fallback_is_one_line_and_lossless()` 之一，将保留的 root fallback 测试改成上述关系断言；不得留下任何完整中文 fallback 期望。

- [ ] **Step 7: 保留并收紧控制字符安全测试**

路径和未知 code 使用包含换行的输入，断言原始换行不会形成第二行，独立 JSON 转义值出现在结果中：

```python
def test_path_with_control_character_stays_one_line() -> None:
    path = "bad\nname.md"
    issue = CheckIssue("link.missing", IssueSeverity.ERROR, Path(path), 5, ("x", "x"))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert _escaped_fact(path) in rendered


def test_code_with_control_character_stays_one_line() -> None:
    code = "bad\ncode"
    issue = CheckIssue(code, IssueSeverity.ERROR, Path("a.md"), None, ("x",))
    rendered = render_issue(issue)
    _assert_single_line(rendered)
    assert _escaped_fact(code) in rendered
```

- [ ] **Step 8: 运行新诊断契约并执行局部变异证明**

先运行：

```bash
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_diagnostics.py" -q
```

Expected: PASS。

使用 `apply_patch` 临时把 `diagnostics._safe()` 的函数体改为 `return value`，仅运行两个控制字符测试：

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_diagnostics.py::test_path_with_control_character_stays_one_line" \
  "$RUNTIME/tests/unit/test_diagnostics.py::test_code_with_control_character_stays_one_line" -q
```

Expected: FAIL，原因是渲染结果包含额外换行。立即用 `apply_patch` 恢复 `_safe()` 的原实现，再次运行同一命令并确认 PASS。执行下面的命令必须无输出：

```bash
git diff -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py
```

- [ ] **Step 9: 格式化、静态检查并提交诊断测试重构**

```bash
uv run --project "$RUNTIME" --locked ruff format \
  "$RUNTIME/tests/unit/test_diagnostics.py"
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/tests/unit/test_diagnostics.py"
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_diagnostics.py" -q
git diff --check
git add "$RUNTIME/tests/unit/test_diagnostics.py"
git commit -m "test(draft): decouple diagnostics from localized copy"
```

Expected: 所有命令通过；提交不包含生产文件。

---

### Task 2: 隔离 CLI 编排与人类文案

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Temporary mutation only: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`

**Interfaces:**
- Consumes: `_dispatch(args) -> tuple[dict[str, object], str, int]`、`_render_check(report, json_mode) -> int`、公开协作者 `render_issue(issue) -> str`。
- Produces: 测试内 `_assert_human_result()`；新增 `_render_check()` 分组、摘要、JSON 与退出码契约。

- [ ] **Step 1: 记录 CLI 单元测试基线**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_cli.py" -q
```

Expected: exit code `0`，无失败。

- [ ] **Step 2: 添加动态事实辅助断言**

```python
def _assert_human_result(human: str, *facts: object) -> None:
    assert human
    assert human.splitlines() == [human]
    for fact in facts:
        assert str(fact) in human
```

该辅助函数只接受来自命令参数或结构化结果的事实，不接受预写的人类文案片段。

- [ ] **Step 3: 替换 `_dispatch()` 的具体文案断言**

逐个保留现有协作者交互、payload 和 exit code 断言，将 `human == ...` 或中文 `in human` 改为：

| Test branch | `_assert_human_result` facts |
| --- | --- |
| init / adopt | `Path("/draft")` |
| move | `Path("pages/a.md")`、`Path("pages/b.md")` |
| promote | page、`len(children)` |
| collapse | hub path |
| trash | page、`result.phase.value` |
| trash warning | warning code、operation ID、每个 details 值 |
| restore completed | operation ID |
| restore rolled back | 无额外 fact；payload 的 `ok=False`、phase 和 exit code 已精确验证 |
| recover success | `len(report.results)` |
| recover failure | failure code、failure operation ID |

示例：

```python
payload, human, exit_code = cli._dispatch(args)  # pyright: ignore[reportPrivateUsage]
assert payload["result_type"] == "move"
assert exit_code == 0
_assert_human_result(human, Path("pages/a.md"), Path("pages/b.md"))
```

不得删除任何已有 payload、协作者调用或退出码断言。

- [ ] **Step 4: 新增 `_render_check()` 人类模式编排单元测试**

构造数量分别为 error=1、warning=2、info=3、review=4 的 `CheckIssue`。输入 `CheckReport.issues` 故意交错排列；给每个 issue 分配运行期 token，并用 autospec stub 替换 `cli.render_issue`：

```python
def _mixed_severity_issues() -> tuple[CheckIssue, ...]:
    by_severity = {
        severity: tuple(
            CheckIssue(
                code=f"test.{severity.value}.{index}",
                severity=severity,
                path=Path(f"{severity.value}-{index}.md"),
                line=index + 1,
                details=(f"detail-{index}",),
            )
            for index in range(count)
        )
        for severity, count in (
            (IssueSeverity.ERROR, 1),
            (IssueSeverity.WARNING, 2),
            (IssueSeverity.INFO, 3),
            (IssueSeverity.REVIEW, 4),
        )
    }
    return (
        by_severity[IssueSeverity.REVIEW][0],
        by_severity[IssueSeverity.WARNING][0],
        by_severity[IssueSeverity.INFO][0],
        by_severity[IssueSeverity.REVIEW][1],
        by_severity[IssueSeverity.ERROR][0],
        by_severity[IssueSeverity.INFO][1],
        by_severity[IssueSeverity.WARNING][1],
        by_severity[IssueSeverity.REVIEW][2],
        by_severity[IssueSeverity.INFO][2],
        by_severity[IssueSeverity.REVIEW][3],
    )


def test_render_check_groups_issues_and_emits_each_renderer_result_once(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    issues = _mixed_severity_issues()
    report = CheckReport(issues=issues)
    grouped = [*report.errors, *report.warnings, *report.infos, *report.reviews]
    tokens = {issue: f"render-token-{index}" for index, issue in enumerate(grouped)}
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    render_issue.side_effect = tokens.__getitem__

    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        report, json_mode=False
    )
    lines = capsys.readouterr().out.splitlines()

    assert exit_code == 1
    assert lines[:-1] == [tokens[issue] for issue in grouped]
    assert [call.args[0] for call in render_issue.call_args_list] == grouped
    assert lines[-1]
    for count in (1, 2, 3, 4):
        assert str(count) in lines[-1]
```

`_mixed_severity_issues()` 是测试内纯构造器，只创建 `CheckIssue` 值，不访问文件系统。code 使用稳定机器标识，path/details 使用互不混淆的 ASCII sentinel。

- [ ] **Step 5: 新增空报告和 JSON 模式契约**

```python
def test_render_check_clean_human_output_is_one_nonempty_line(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        CheckReport(issues=()), json_mode=False
    )
    lines = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert len(lines) == 1
    assert lines[0]
    render_issue.assert_not_called()


def test_render_check_json_uses_structured_payload_without_human_renderer(
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    issue = CheckIssue(
        "output.missing", IssueSeverity.ERROR, Path("pages/a.md"), None
    )
    render_issue = mocker.patch.object(cli, "render_issue", autospec=True)
    exit_code = cli._render_check(  # pyright: ignore[reportPrivateUsage]
        CheckReport(issues=(issue,)), json_mode=True
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "ok": False,
        "result_type": "check",
        "errors": [
            {
                "code": "output.missing",
                "severity": "error",
                "path": "pages/a.md",
                "line": None,
                "details": [],
            }
        ],
        "warnings": [],
        "infos": [],
        "reviews": [],
    }
    render_issue.assert_not_called()
```

JSON 是机器协议，完整精确断言必须保留。

- [ ] **Step 6: 运行 CLI 单元测试并执行分组顺序变异证明**

先运行：

```bash
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/unit/test_cli.py" -q
```

Expected: PASS。

使用 `apply_patch` 临时把 `cli._render_check()` 的 `groups` 顺序改为 `(report.reviews, report.infos, report.warnings, report.errors)`，只运行新分组测试。Expected: FAIL，且失败显示实际 token 顺序与期望分组顺序不同。立即用 `apply_patch` 恢复原顺序，重跑并确认 PASS；下面的命令必须无输出：

```bash
git diff -- \
  plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py
```

- [ ] **Step 7: 格式化、静态检查并提交 CLI 单元测试重构**

```bash
uv run --project "$RUNTIME" --locked ruff format \
  "$RUNTIME/tests/unit/test_cli.py"
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/tests/unit/test_cli.py"
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_cli.py" -q
git diff --check
git add "$RUNTIME/tests/unit/test_cli.py"
git commit -m "test(draft): isolate cli behavior from localized copy"
```

Expected: 所有命令通过；提交不包含生产文件。

---

### Task 3: 将核心 CLI 集成测试收敛到跨边界事实

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

**Interfaces:**
- Consumes: 公共入口 `main(argv) -> int`、真实 checker、真实 `render_issue()`、真实文件系统固件。
- Produces: 真实协作者组合的路径/行号/details 传播、输出行数、JSON 一致性、退出码和只读性证明。

- [ ] **Step 1: 记录 CLI 集成基线**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_cli.py" -q -m 'not network'
```

Expected: `41 passed`，无失败。

- [ ] **Step 2: 添加按行验证动态事实的辅助函数**

```python
def assert_human_output_facts(
    output: str,
    facts_by_line: tuple[tuple[object, ...], ...],
) -> list[str]:
    lines = output.splitlines()
    assert len(lines) == len(facts_by_line)
    for line, facts in zip(lines, facts_by_line, strict=True):
        assert line
        for fact in facts:
            assert str(fact) in line
    return lines
```

调用方只能传入测试输入产生的路径、行号、目标、操作 ID、error code、CLI 选项或计数；不得传入人类文案片段。

- [ ] **Step 3: 重写 candidate 和 clean 人类输出测试**

- `test_candidate_check_registered_root_human_hint`：保留 exit code `1`；断言两行输出；第一行包含 `Path(".draft/draft.json")` 和机器选项 `--candidate`；第二行非空。不再断言中文摘要或中文提示词不存在。
- `test_check_human_clean_prints_pass_sentence`：改名为 `test_check_human_clean_prints_one_nonempty_line`；断言 exit code `0`、输出恰好一行且非空。
- `test_check_human_is_read_only` 保持原样，因为它验证文件状态而非文案。

目标代码形状：

```python
assert main(["check", str(registered_draft)]) == 0
lines = capsys.readouterr().out.splitlines()
assert len(lines) == 1
assert lines[0]
```

- [ ] **Step 4: 重写真实多位置诊断集成测试**

保留当前真实 Markdown 固件和真实 `main()` 调用，将完整三行列表替换为动态事实：

```python
missing_a = "pages/missing-a.md"
missing_b = "pages/missing-b.md"

assert main(["check", str(registered_draft)]) == 1
assert_human_output_facts(
    capsys.readouterr().out,
    (
        ("conclusions.md", 7, missing_a),
        ("conclusions.md", 8, missing_b),
        (2, 0),
    ),
)
```

Markdown 固件也使用 `missing_a`、`missing_b` 构造链接，避免测试输入与断言各自硬编码一份目标。

- [ ] **Step 5: 删除伪集成的四严重级别文案测试**

删除 `test_check_human_orders_four_severities_and_summarizes`。该用例替换 checker、重复冻结 diagnostics 文案，却仍使用真实文件系统；Task 2 新增的 `_render_check()` 单元测试已经以受约束的 `render_issue` stub 覆盖分组顺序、调用次数、摘要计数和退出码。

删除前先运行 Task 2 的新测试并确认 PASS；不得在 Task 2 合并前执行本步骤。

- [ ] **Step 6: 重写 human/JSON 一致性与回滚分支**

- `test_check_plain_and_json_agree_on_exit_code_and_raw_details`：将 literal 和 normalized target 提取为变量；human 输出只断言两个变量都出现且输出单行，JSON 继续精确断言 `details`、severity 和 exit code。
- `test_restore_cli_rolled_back_is_exit_three`：删除 `human = capsys.readouterr().out` 和对成功中文动词不存在的断言；`run_json()` 已经消费 JSON 输出，此处的 `human` 实际恒为空。保留 `code == 3`、`ok is False`、`result_type == "restore"`、`phase == "rolled_back"` 和权威删除包仍存在。
- `test_unresolvable_home_maps_to_root_missing_text` 保留 `"root.missing" in output`，因为它是未知/错误 fallback 的机器 error code，不是本地化文案。
- version、命令排除矩阵和 JSON 字段断言保持精确。

- [ ] **Step 7: 运行集成测试并审计目标文件中的文案 oracle**

```bash
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_cli.py" -q -m 'not network'
uv run --project "$RUNTIME" --locked python - <<'PY'
import ast
from pathlib import Path

paths = (
    Path("plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py"),
    Path("plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py"),
    Path("plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py"),
)
violations: list[str] = []
for path in paths:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for assertion in (node for node in ast.walk(tree) if isinstance(node, ast.Assert)):
        for node in ast.walk(assertion.test):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and any("\u4e00" <= char <= "\u9fff" for char in node.value)
            ):
                violations.append(f"{path}:{assertion.lineno}: {node.value!r}")
if violations:
    raise SystemExit("\n".join(violations))
PY
```

Expected: pytest PASS；一次性 AST 审计 exit code `0`。随后人工检查 `VALID_RENDER_CASES`、其他参数化 expected 数据和多行断言，确认不存在通过变量间接比较具体中文输出的情况。中文 Markdown 输入固件、注释和测试名说明不属于违规。该 AST 脚本只在实施期间运行，不写入仓库测试。

- [ ] **Step 8: 格式化、静态检查并提交 CLI 集成测试重构**

```bash
uv run --project "$RUNTIME" --locked ruff format \
  "$RUNTIME/tests/integration/test_cli.py"
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/tests/integration/test_cli.py"
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_cli.py" -q -m 'not network'
git diff --check
git add "$RUNTIME/tests/integration/test_cli.py"
git commit -m "test(draft): assert cli human output by facts"
```

Expected: 所有命令通过；生产文件与适配层测试均未修改。

---

### Task 4: 执行完整质量门禁与范围审计

**Files:**
- Verify only: all files changed by Tasks 1–3

**Interfaces:**
- Consumes: 三个已提交的测试重构任务。
- Produces: 完整测试、静态检查、锁文件和差异证据；不产生新代码。

- [ ] **Step 1: 核对提交范围和生产行为零改动**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
git status --short
git diff HEAD~3 --name-only
git diff HEAD~3 -- \
  plugins/draft/skills/draft/scripts/runtime/src \
  plugins/draft/scripts
```

Expected: `git status --short` 与 Task 1 开始前记录的基线状态一致；三次实现提交只修改三个目标测试文件；生产源码和适配层无差异。若执行期间提交数量不同，使用三个实际实现提交的共同父提交替代 `HEAD~3`。

- [ ] **Step 2: 运行核心与适配层单元测试**

```bash
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
```

Expected: 两条命令均 PASS，且两个测试树保持独立收集。

- [ ] **Step 3: 运行非联网集成测试**

```bash
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
```

Expected: 两条命令均 PASS。

- [ ] **Step 4: 运行 network 集成测试**

```bash
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```

Expected: 两条命令均 PASS。网络或索引不可用必须作为环境失败单独诊断，不得改测试绕过。

- [ ] **Step 5: 运行类型、lint、格式和锁文件门禁**

```bash
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

Expected: 全部 exit code `0`。基线中两个目标文件原本会被 Ruff 重新格式化；Tasks 1 和 3 已格式化它们，最终 format check 必须通过。

- [ ] **Step 6: 最终契约审计**

逐项确认：

- 所有 50 个 `KNOWN_CHECK_CODES` 仍被合法 render case 覆盖。
- 当前所有 details 变体仍至少执行一次。
- fallback、无效位置、非正行号、闭合环、自环和控制字符测试仍存在。
- `issue_payload()`、JSON schema、error code、phase、路径、行号和退出码仍精确断言。
- 三个目标测试文件不再以具体中文人类文案作为 oracle。
- 没有新增 locale、catalog、翻译资源、依赖或生产接口。
- `git status --short` 与实施前基线一致，没有新增未提交改动。

Expected: 所有条目成立；不需要额外提交。
