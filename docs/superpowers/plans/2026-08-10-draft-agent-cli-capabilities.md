# Draft 面向代理的命令行能力实施计划

> **给实施代理（Agent）：** 按任务执行本计划时，使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`。使用复选框（`- [ ]`）记录进度。

**目标：** 为 Draft 运行时增加低令牌消耗的结构查询、安全创建、状态与关系修改、按需结论生成、统一预演（`dry-run`）和静默回收生命周期，同时让所有命令共享类型化状态、关系与正文资源规则。

**架构：** Markdown 继续是唯一事实来源；每次命令按需建立一次内存页面或完整图，不保存跨命令缓存。建议将稳定词汇、正文资源解析、查询、公开计划影响、结论生成和页面元数据修改分别放在职责集中的模块中；现有图构建、检查、结构修改、回收和命令行入口通过这些边界协作，所有正式写入继续使用 `OperationPlan` 与崩溃恢复日志。

**技术栈：** Python `>=3.12,<3.15`、标准库 `pathlib`/`urllib.parse`/`enum.StrEnum`/`dataclasses`、markdown-it-py `>=4.2,<4.3`、Send2Trash `>=2.1,<2.2`、pytest、pytest-mock、Pyright 严格模式、Ruff、uv。

**已批准规格：** `docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md`

## 必须遵守的全局约束

- 开始每个任务前依次读取根 `AGENTS.md`、`plugins/draft/AGENTS.md` 和批准规格；若实施过程中这些文件变化，重新读取后再继续。
- 不引入 Obsidian、数据库、后台索引、文件监视器、跨命令缓存或新第三方依赖；不得修改 `pyproject.toml` 依赖窗口与 `uv.lock`。
- 保持 `FORMAT_VERSION = 1`；项目仍在开发，不实现旧 `output`/`产出` 格式兼容、迁移命令或双读双写。
- CLI 和 JSON 使用稳定的 `StatusCode`、`RelationCode`、资源类型、范围、目标状态、`result_type` 和预演 `operation`；有限机器标识由有约束的生产类型统一拥有，中文标签只存在于生产本地化映射、Markdown 与人类输出边界。
- 正文资源链接允许 Draft 外路径、Linux/Windows 相对路径及两种绝对路径；正式关系和受管理页面参数仍只能指向同一 Draft 内页面。
- 正文资源的 `kind` 按词法命名空间判定，不依赖目标是否存在；`index.md`、`conclusions.md` 和 `pages/` 下任意深度的 Markdown 是页面命名空间，其他 Markdown 是本地资源。
- `conclusions.md` 不实时维护；只有 `generate-conclusions` 完整重建，普通结构命令不得逐条改写结论列表，也不得让旧结论链接阻止 `move` 或 `trash`。
- 所有已登记 Draft 命令，包括查询和 `--dry-run`，在业务逻辑前静默推进回收生命周期；临时移交失败不附加推进活动、保留重试，损坏状态明确失败，`check` 仍可独立诊断推进后的持久化操作状态。
- 查询无法证明结果完整时整体失败；目标的 `missing`、`invalid`、`unavailable`、`unchecked` 是成功查询到的事实。
- 所有公开 `changed_paths` 从公开语义效果或等价公开结果模型派生；底层 journal mutation 只服务执行与恢复，不能作为公开路径权威来源，也不能靠过滤 `.draft/` 前缀替代语义模型。
- 人类输出测试只验证动态事实、顺序、计数、单行和转义；不得复制完整中文文案。稳定 JSON 字段与机器协议值必须精确断言。
- 单元测试同进程、无真实文件系统、无子进程；使用注入的文件状态探针、时间以及受真实接口约束的桩件或伪实现。真实文件、符号链接、操作日志、恢复和 CLI 组合属于集成测试。
- 结构和依赖边界使用抽象语法树（AST）、真实导入或运行行为验证，不以源码普通文本包含或排除代替语义检查。
- 每个任务按红—绿—重构执行。任务是可独立验证和审查的行为边界，不强制对应一个固定提交。门禁失败时先定位根因，不删除测试、不放宽 Pyright 或 Ruff、不吞错误。
- 工作区可能包含用户或其他代理的修改；开始任务前记录 `git status --short`，提交前根据实际差异确认归属，不清理、覆盖或误提交其他人的改动。

## 计划内容的约束层级

- 上一节和批准规格中的行为契约是硬性要求。
- 下文列出的模块、内部函数名、签名、预计修改文件和检查点是帮助实施的当前建议，不是封闭清单。实施者在追踪真实数据流后可以调整，但必须保持行为契约，并在交付时说明重要偏差及原因。
- 明确属于公开机器协议的命令、字段、枚举值和退出码不得自行改变；仅为组织实现而建议的内部接口可以在不改变行为的前提下调整。
- 不要求恰好形成 11 个提交。应按可独立验证、可独立审查的行为组织提交，避免为了符合计划制造无意义的中间状态。

## 预计的文件组织

以下路径用于说明建议的职责归属，并帮助实施者定位代码。实际实施可能增加、减少或调整文件；不要为了匹配清单而修改无关文件。

### 新建的生产模块

- `plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py`
  - 唯一拥有稳定状态/关系代码与当前中文 Markdown 标签映射。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py`
  - 区分正式关系目标与正文资源，解析 Linux/Windows 路径、资源身份、范围、可用状态和移动后重写。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
  - 定义紧凑查询结果，并从单页或完整 `DraftGraph` 计算 `page`、`links`、`backlinks`、`relations`、`status`。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/plan_effects.py`
  - 定义 `PlanEffect`、公开 `changed_paths` 和 JSON 影响序列化所需的纯逻辑。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py`
  - 规划和执行按需结论生成，拥有固定文件形状、导航顺序和 `generated` 所有权收敛。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py`
  - 校验新叶子路径、生成页面骨架并原子更新父导航。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
  - 规划和执行状态、依赖、取代、质疑的幂等修改与最终图校验。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
  - 统一拥有受约束的 `result_type`/预演 `operation` 值、分页信封构造，并将查询、计划和写结果转换为稳定 JSON 与最小人类输出；不读取 Draft。

### 主要修改模块

- `src/draft/models.py`：页面模型改用稳定代码；`OperationPlan` 携带公开 `effects`，正式结果边界携带或接收从同一效果推导的公开路径。
- `src/draft/markdown.py`：中文标签边界、机器区域编辑、普通文本转义、结论导航插入。
- `src/draft/graph.py`：构建页面图与正文资源反向索引，提供单页/完整快照加载边界，读取结论所有权状态。
- `src/draft/checker.py`：删除 `output` 规则，复用资源状态，检查懒生成结论结构并容忍结论列表过时。
- `src/draft/structure.py`、`src/draft/trash.py`：使用资源重写和公开 `effects`，跳过机器结论列表。
- `src/draft/lifecycle.py`：初始化/接管写入 `conclusions_state=not_generated`，不预建结论页。
- `src/draft/trash_lifecycle.py`：通用静默前置推进、只恢复中断操作、可恢复项查询。
- `src/draft/operations.py`：允许结论生成计划原子写指定登记标记，验证公开 `effects` 与底层文件变更一致。
- `src/draft/cli.py`：只保留命令参数解析、命令编排、前置推进、退出码和错误边界。
- `src/draft/diagnostic_codes.py`、`src/draft/diagnostics.py`：删除 `output` 诊断，增加资源与结论结构诊断。

### 新建测试文件

- `tests/unit/test_vocabulary.py`
- `tests/unit/test_resources.py`
- `tests/unit/test_query.py`
- `tests/unit/test_plan_effects.py`
- `tests/unit/test_conclusions.py`
- `tests/unit/test_page_create.py`
- `tests/unit/test_page_metadata.py`
- `tests/integration/test_queries.py`
- `tests/integration/test_page_commands.py`
- `tests/integration/test_conclusions.py`

现有测试文件只在其既有契约被本设计改变时修改；不要把全部新场景继续堆入已经很大的 `test_cli.py`、`test_checker.py` 或 `test_structure.py`。

---

### 任务 1：建立稳定状态和关系词汇，并删除 `output` 协议

**预计影响范围（非穷尽）：**
- 建议新建：`plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py`
- 建议新建：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_vocabulary.py`
- 预计修改：模型、Markdown 解析、图构建、检查、结构操作和诊断模块
- 预计修改：相关词汇、解析、检查、诊断和结构操作的单元测试与集成测试

**建议的任务间接口：**
- 建议提供：`StatusCode`、`RelationCode`、`EditableRelationCode`。
- 建议提供：`status_label(code) -> str`、`status_code(label) -> StatusCode`、`relation_label(code) -> str`、`relation_code(label) -> RelationCode`。
- 预计调整：将 `StatusValue.prefix` 改为 `StatusValue.code`，将 `Relation.label` 改为 `Relation.code`，并让 `DraftGraph.relations` 使用 `RelationCode` 作为键。
- 预计淘汰内部的 `StatusPrefix` 和 `RelationLabel`；公开关系类型不得再包含 `RelationCode.OUTPUT`，诊断类型不得再包含四个 `CheckCode.OUTPUT_*` 成员，仅服务这些诊断的 `WrongOutputReason` 也必须删除。

- [ ] **步骤 1：编写词汇边界和删除旧协议的失败测试**

新建 `test_vocabulary.py`，用生产类型驱动往返测试；同时更新检查器测试，证明已确认页面不声明“产出”也合法：

```python
from draft.vocabulary import (
    EditableRelationCode,
    RelationCode,
    StatusCode,
    relation_code,
    relation_label,
    status_code,
    status_label,
)


def test_every_status_code_round_trips_through_the_markdown_label() -> None:
    for code in StatusCode:
        assert status_code(status_label(code)) is code


def test_every_relation_code_round_trips_through_the_markdown_label() -> None:
    for code in RelationCode:
        assert relation_code(relation_label(code)) is code


def test_editable_relations_are_only_active_directions() -> None:
    assert tuple(EditableRelationCode) == (
        EditableRelationCode.DEPENDS_ON,
        EditableRelationCode.SUPERSEDES,
        EditableRelationCode.CHALLENGES,
    )


def test_removed_relation_is_not_published() -> None:
    assert "OUTPUT" not in RelationCode.__members__
```

将只验证旧 `output` 规则的检查器预期替换为“`confirmed` 状态本身已经足够”的行为用例。删除仅验证 `output.missing`、`output.forbidden`、`output.wrong_target` 或旧反向维护规则的测试。

增加解析行为测试：机器区域继续出现 `产出：` 时，不再把它识别为关系，而是按普通未知机器标签报告 `page.unknown_label`。这不是旧协议兼容路径。

- [ ] **步骤 2：运行最窄测试并确认预期的失败状态**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_vocabulary.py" \
  "$RUNTIME/tests/unit/test_markdown.py" \
  "$RUNTIME/tests/unit/test_checker.py" -q
```

预期：测试收集因 `draft.vocabulary` 和 `StatusCode` 尚不存在而失败；清理旧夹具前，已有 `output` 测试也可能失败。

- [ ] **步骤 3：实现唯一的词汇权威来源**

建议在 `vocabulary.py` 中提供以下公开类型和映射。内部函数组织可以调整，但合法值集合和往返行为必须一致：

```python
from __future__ import annotations

from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class StatusCode(StrEnum):
    PENDING = "pending"
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class RelationCode(StrEnum):
    PARENT = "parent"
    DEPENDS_ON = "depends_on"
    SUPERSEDES = "supersedes"
    SUPERSEDED_BY = "superseded_by"
    CHALLENGES = "challenges"
    CHALLENGED_BY = "challenged_by"


class EditableRelationCode(StrEnum):
    DEPENDS_ON = RelationCode.DEPENDS_ON.value
    SUPERSEDES = RelationCode.SUPERSEDES.value
    CHALLENGES = RelationCode.CHALLENGES.value


_STATUS_LABELS: Mapping[StatusCode, str] = MappingProxyType(
    {
        StatusCode.PENDING: "待讨论",
        StatusCode.PROPOSED: "当前提案",
        StatusCode.CONFIRMED: "已确认",
        StatusCode.SUPERSEDED: "已取代",
        StatusCode.CLOSED: "已关闭",
    }
)
_RELATION_LABELS: Mapping[RelationCode, str] = MappingProxyType(
    {
        RelationCode.PARENT: "父主题",
        RelationCode.DEPENDS_ON: "依赖",
        RelationCode.SUPERSEDES: "取代",
        RelationCode.SUPERSEDED_BY: "被取代",
        RelationCode.CHALLENGES: "质疑",
        RelationCode.CHALLENGED_BY: "被质疑",
    }
)


def status_label(code: StatusCode) -> str:
    return _STATUS_LABELS[code]


def status_code(label: str) -> StatusCode:
    try:
        return next(code for code, value in _STATUS_LABELS.items() if value == label)
    except StopIteration as exc:
        raise ValueError(label) from exc


def relation_label(code: RelationCode) -> str:
    return _RELATION_LABELS[code]


def relation_code(label: str) -> RelationCode:
    try:
        return next(code for code, value in _RELATION_LABELS.items() if value == label)
    except StopIteration as exc:
        raise ValueError(label) from exc
```

在 `markdown.py` 中，把词汇转换产生的 `ValueError` 转换为现有稳定解析错误，不要直接泄漏该异常。解析器和所有 Markdown 渲染器都使用词汇映射函数；仍受支持的状态、关系和机器字段中文标签必须只有一个生产代码权威来源，不得在检查、结构操作或回收模块中重复维护。`产出` 不得留在已知标签集合中。

将所有消费者和测试改为使用 `.code` 与枚举身份比较。删除 `_check_outputs`、旧 `output` 诊断、`WrongOutputReason` 及其夹具。现有常驻结论页暂时仍可作为合法页面；任务 7 再调整它的生命周期。

- [ ] **步骤 4：增加稳定机器协议值的抽象语法树所有权测试**

扩展 `tests/integration/test_distribution_contract.py`：解析 Python 抽象语法树中的常量，证明原始状态和关系协议值只由 `vocabulary.py` 拥有。不要搜索源码文本；文档中的 JSON 示例不属于这个生产代码和测试代码的所有权契约。

- [ ] **步骤 5：运行定向测试和完整核心单元门禁**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
```

预期：所有命令退出码均为 `0`；`CheckCode` 和 `KNOWN_CHECK_CODES` 中不再存在旧 `output` 诊断码。

- [ ] **步骤 6：形成可审查检查点**

```bash
git status --short
git diff --stat
```

确认实际差异只服务于本任务的行为契约。如果这一阶段适合独立提交，建议提交说明为 `refactor(draft): stabilize status and relation codes`；否则与紧密耦合的下一阶段一起提交，不为匹配计划强行拆分。

---

### 任务 2：统一正文资源解析与检查状态

**预计影响范围（非穷尽）：**
- 建议新建：`plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py`
- 建议新建：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_resources.py`
- 预计修改：图构建、检查器、诊断码和诊断渲染模块
- 预计修改：相关图、检查器、诊断单元测试以及检查器集成测试

**建议的任务间接口：**
- 建议提供：`ResourceKind`、`ResourceScope`、`TargetState`、`HostOS`、`ResourceIdentity`、`ResolvedResource`、`ResourceReference`。
- 建议提供：正式关系使用 `resolve_managed_destination(source, destination) -> tuple[Path, str | None]`。
- 建议提供：正文链接使用 `resolve_body_resource(root, source, destination, *, host_os=None, target_probe=None) -> ResolvedResource` 或同等纯边界；注入的目标探针必须能区分缺失、普通文件、目录、符号链接和其他文件类型，具体类型签名由实现决定。
- 建议提供：`resolve_page_resources(root, page, *, host_os=None, target_probe=None) -> tuple[ResourceReference, ...]` 或同等边界，并排除已经解析为正式关系的链接位置。
- 建议提供：命令行反向链接目标使用 `resource_identity_from_operand(root: Path, value: str, *, host_os: HostOS | None = None) -> ResourceIdentity`。
- 预计调整：`DraftGraph` 增加 `body_resources` 和 `inbound_resources`；现有 `inbound_links` 继续作为结构修改和回收使用的受管理页面索引。

- [ ] **步骤 1：编写纯内存资源状态矩阵**

使用参数化单元测试覆盖以下分类：

| 目标写法 | 当前系统 | 预期类型 | 范围 | 状态 |
|---|---|---|---|---|
| `pages/a.md` 中的 `b.md#one` | `linux` | `page`（规范目标符合受管理页面路径） | `draft` | `present` |
| `pages/a.md` 中的 `missing.md` | `linux` | `page` | `draft` | `missing` |
| `pages/a.md` 中的 `../conclusions.md`（尚未生成） | `linux` | `page` | `draft` | `missing` |
| `pages/a.md` 中的 `../notes.md` | `linux` | `local` | `draft` | `present` |
| `pages/a.md` 中的 `../../notes.md` | `linux` | `local` | `outside` | `present` |
| `pages/a.md` 中的 `b.md`（目标是目录或含符号链接） | `linux` | `page` | `draft` | `invalid` |
| `../asset.png` | `linux` | `local` | `draft` | `present` |
| `../../shared.png` | `linux` | `local` | `outside` | `missing` |
| `..\\asset.png` | `linux` | `local` | `draft` | `present` |
| `C:\\docs\\a.pdf` | `linux` | `local` | `unknown` | `unavailable` |
| `C:docs\\a.pdf` | `linux` | `local` | `unknown` | `unavailable` |
| `\\\\server\\share\\a.pdf` | `linux` | `local` | `unknown` | `unavailable` |
| `/opt/shared/a.pdf` | `linux` | `local` | `outside` | `present` |
| `https://example.test/a#part` | `linux` | `external` | `unknown` | `unchecked` |
| 非法百分号转义 | `linux` | `local` | `unknown` | `invalid` |

使用按规范化绝对路径返回分类结果的目标探针伪实现；单元测试不得创建真实文件。另加用例证明：目标文件从缺失变为存在时，页面资源的 `kind` 和 `scope` 不变，只把 `target_state` 从 `missing` 改为 `present`；图片和普通链接指向同一资源时会去重，锚点不参与资源身份比较。

- [ ] **步骤 2：运行矩阵并确认缺少模块时失败**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_resources.py" -q
```

预期：测试收集报错，并明确指出缺少 `draft.resources`。

- [ ] **步骤 3：实现资源类型和两类解析边界**

建议采用以下公开形状；只要类型表达的状态和调用契约不变，内部命名可以在实现时调整：

```python
class ResourceKind(StrEnum):
    PAGE = "page"
    LOCAL = "local"
    EXTERNAL = "external"


class ResourceScope(StrEnum):
    DRAFT = "draft"
    OUTSIDE = "outside"
    UNKNOWN = "unknown"


class TargetState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"
    UNCHECKED = "unchecked"
    INVALID = "invalid"


class HostOS(StrEnum):
    LINUX = "linux"
    WINDOWS = "windows"


@dataclass(frozen=True, order=True)
class ResourceIdentity:
    kind: ResourceKind
    value: str


@dataclass(frozen=True)
class ResolvedResource:
    identity: ResourceIdentity
    target: str
    kind: ResourceKind
    scope: ResourceScope
    target_state: TargetState
    local_absolute: str | None
    was_relative: bool
    separator: str | None
    anchor: str | None


@dataclass(frozen=True)
class ResourceReference:
    link: MarkdownLink
    resource: ResolvedResource
```

先识别 URI 方案名，再识别 Windows 盘符路径；单字母后跟 `:` 时按 Windows 路径处理。非法百分号转义标记为 `invalid`。资源身份不得调用 `Path.resolve()` 或 `realpath()`，因为符号链接和缺失目标都必须保留词法身份。

先根据规范化根相对词法路径判定资源种类，再查询文件状态。`index.md`、`conclusions.md` 和 `pages/` 下任意深度的 `.md` 属于页面命名空间，即使不存在也保持 `PAGE/DRAFT/MISSING`；路径含符号链接或存在但不是普通文件时为 `PAGE/DRAFT/INVALID`。根目录其他 Markdown、`pages/` 外的 Draft 内 Markdown 和 Draft 外 Markdown 都是 `LOCAL`，分别使用 `DRAFT` 或 `OUTSIDE`。不得用当前 `managed_set`、文件存在性或 `conclusions_state` 决定 `kind`。

`resolve_managed_destination` 只接受保持在 Draft 根目录内的相对 POSIX Markdown 目标，并返回相对根目录的 `Path`。绝对路径、URI、Windows 盘符、UNC 路径和越过根目录的路径都产生现有稳定正式链接错误。

- [ ] **步骤 4：分开处理正文资源和正式关系**

在 `resolve_page_resources` 中统一排除正式关系链接位置。图加载用它生成 `body_resources`；正式关系目标单独使用 `resolve_managed_destination`。任务 5 的查询代码复用同一个页面资源函数，不再重复判断。

增加 `CheckCode.LINK_UNAVAILABLE = "link.unavailable"`，并按以下规则报告：

- `missing` 产生错误 `link.missing`；
- `invalid` 通过新增的 `CheckCode.LINK_INVALID` 产生错误 `link.invalid`；
- `unavailable` 产生警告 `link.unavailable`；
- `present` 和外部资源的 `unchecked` 不产生问题。

无论目标状态如何，都保留对应的 `ResourceReference`。按照 `ResourceIdentity` 建立 `inbound_resources`，并保持“来源路径、声明位置”的确定顺序。

- [ ] **步骤 5：验证真实文件、图片、目录和非本机路径**

增加集成测试：创建 Draft 以及位于 Draft 外的文件、目录和图片，验证存在或缺失诊断，以及 Linux 对 Windows 绝对路径的处理。另在页面命名空间内创建目录、文件符号链接和含符号链接的路径，验证它们保持 `page/draft` 身份但状态为 `invalid`。外部 URI 不产生警告；缺失图片产生错误，并包含图片所在行。

- [ ] **步骤 6：运行定向门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_resources.py" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/unit/test_diagnostics.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对实际修改与资源解析契约一致。若适合独立提交，可使用 `feat(draft): model body resource targets` 作为提交说明建议。

---

### 任务 3：在结构变化中保持 Draft 内外正文资源目标

**预计影响范围（非穷尽）：**
- 预计修改：`resources.py`、`structure.py` 和 `trash.py`
- 预计修改：资源与结构单元测试、结构与回收集成测试

**建议的任务间接口：**
- 建议提供：`rebase_body_destination(resource: ResolvedResource, *, new_source: Path, moved_targets: Mapping[ResourceIdentity, ResourceIdentity]) -> str`。
- 必须保持：本地绝对路径和外部 URI 原文不变，只重写可解释的本地相对路径。
- 依赖任务 2 提供的 `DraftGraph.body_resources` 或具有同等语义的资源索引。

- [ ] **步骤 1：编写不复制实现公式的链接重算失败测试**

增加测试证明：

1. 将 `pages/a.md` 移到 `pages/sub/a.md` 时，`../shared.png` 变为 `../../shared.png`，资源身份保持不变；
2. Windows 相对路径 `..\\shared.png` 在同样移动后仍以反斜杠为主要分隔符；
3. 混合分隔符保留原路径中最先出现的分隔符风格；
4. Linux 绝对路径、Windows 绝对路径、UNC 路径、盘符相对路径和外部 URI 保持原文不变；
5. 相对链接指向另一个同时移动的页面时，跟随最终目标映射，而不是继续指向旧绝对位置；
6. 重新计算路径后，原锚点按原样重新附加。

测试应分别解析移动前后的目标并比较 `ResourceIdentity`，不得复制生产代码的相对路径计算公式作为预期值来源。

- [ ] **步骤 2：确认现有结构代码不能正确处理越过根目录和 Windows 相对路径**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_resources.py" \
  "$RUNTIME/tests/unit/test_structure.py" -q
```

预期：新增测试失败，显示现有结构代码会跳过或拒绝 Draft 根目录外链接与 Windows 风格链接。

- [ ] **步骤 3：按资源身份实现链接重算**

`rebase_body_destination` 接收受管理页面移动前后资源身份的映射。对于当前系统可解释的本地相对目标，从 `new_source.parent` 计算新相对路径，使用 `safe="/-._~"` 做百分号编码；记录的主要分隔符为 `\\` 时，再把 `/` 转为反斜杠，最后附加原锚点。绝对路径、不可用路径和外部目标直接返回原链接。

在 `structure._render_pages` 中，正式关系链接使用 `resolve_managed_destination`；普通正文链接查找现有 `ResourceReference`。两类链接不得经过同一个启发式解析器。

回收恢复和路径重算也使用同一资源辅助逻辑。任务 7 明确加入允许结论列表过时的例外前，保持根页面和结论页的现有保护规则。

- [ ] **步骤 4：运行单元测试以及真实移动和回收门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_resources.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
```

预期：退出码为 `0`；每个被改写的相对链接仍解析到移动前的资源身份。

- [ ] **步骤 5：形成可审查检查点**

```bash
git status --short
git diff --stat
```

核对实际修改只改变链接重算行为。若适合独立提交，可使用 `feat(draft): preserve resource targets across moves` 作为提交说明建议。

---

### 任务 4：统一静默生命周期前置推进与可恢复项清单

**预计影响范围（非穷尽）：**
- 预计修改：`trash_lifecycle.py`、命令编排、结构操作、回收和相关模型
- 预计修改：生命周期、命令行单元测试与集成测试

**建议的任务间接口：**
- 建议提供：`prepare_registered_command(root: Path, *, now: datetime | None = None, handoff: Handoff | None = None) -> None`。
- 建议提供一个只读的前置推进资格判定边界，复用正式登记标记解析语义；具体函数名和是否复用解析结果由实现决定。
- 建议提供：`recover_interrupted_operations(root: Path, *, now: datetime | None = None, handoff: Handoff | None = None) -> tuple[OperationResult | TrashResult, ...]`。
- 建议提供：`RecoverableItem(operation_id: str, page: Path, restore_expires_at: datetime)` 和 `list_recoverable(root: Path, *, as_of: datetime | None = None) -> tuple[RecoverableItem, ...]`。
- 必须从普通命令和 `recover` 的公开结果中移除生命周期警告与生命周期推进结果。

- [ ] **步骤 1：编写生命周期边界测试**

针对已过期但仍保留的回收项，为已登记 Draft 的 `check`、`move`、`restore`、`recover` 增加集成测试。在生命周期函数边界注入移交监视替身，断言每个命令在调用自身协作者前恰好推进一次。任务 5 为查询补充同样证明；任务 6 为 `--dry-run` 补充证明。

增加以下测试：

- 临时 `OSError` 移交失败保留 `trash_handoff_pending`，主命令继续返回自身结果，且不输出警告字段或文案；
- 损坏日志以退出码 3 阻止主命令；对 `check` 也只返回标准 `error` 信封，不返回部分检查报告、`lifecycle_results` 或此前推进成功的内部明细；
- `check --candidate`、`init`、登记前的 `adopt`、帮助和版本不执行前置推进；
- 根或正式登记标记缺失、无效、不支持或仍为临时接管形状时不执行前置推进；即使目录残留 `.draft/operations` 和 `.draft/trash`，这些内容也保持逐字节不变，再由命令自身产生既有根、格式或恢复结果；
- 对上述残留目录场景，普通 `check` 仍返回包含 `format.missing` 等问题的 `check` 报告，不得被资格判定改写成标准错误信封；
- `as_of == restore_expires_at` 时不再返回该项目；
- 任一候选日志损坏时，`list_recoverable` 整体失败，不返回此前读到的部分项目。
- `prepare_registered_command` 与 `list_recoverable` 接受调用方提供的同一个 UTC 边界时间；任务 5 再证明 `query recoverable` 的 CLI 编排只读取一次时钟。

保留 `check` 对当前持久化操作状态的独立诊断，包括 `TRASH_RETAINED` 和 `TRASH_HANDOFF_PENDING`。测试必须区分这些检查事实与已经删除的生命周期推进 warning，避免因前置推进静默化而误删检查能力。

- [ ] **步骤 2：运行定向生命周期和 CLI 测试并确认失败**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" \
  "$RUNTIME/tests/unit/test_cli.py" -q
```

预期：失败表明当前前置推进只存在于结构写包装器中，警告仍公开，并且 `recover` 混合了操作恢复与生命周期结果。

- [ ] **步骤 3：拆分三项生命周期职责**

建议采用以下接口形状：

```python
@dataclass(frozen=True)
class RecoverableItem:
    operation_id: str
    page: Path
    restore_expires_at: datetime


def prepare_registered_command(
    root: Path,
    *,
    now: datetime | None = None,
    handoff: Handoff | None = None,
) -> None:
    report = advance_trash_lifecycle(root, now=now, handoff=handoff)
    if report.error is not None:
        raise LifecycleAdvanceError(report)
```

`recover_interrupted_operations` 承担当前 `recover_draft` 中锁和孤立操作的恢复部分，但恢复后不枚举仍保留或等待移交的项目。`list_recoverable` 验证每个回收日志和权威回收包，严格按 `as_of < restore_expires_at` 过滤，并按 `(restore_expires_at, operation_id)` 排序。

删除 `collect_lifecycle_warnings` 和 `_with_preflight_warnings`。主命令编排只调用一次 `prepare_registered_command`；领域计划器和执行包装器不再重复调用。不得删除检查器自身的 `_check_operation_state` 或同等操作状态诊断边界。

- [ ] **步骤 4：统一选择哪些 CLI 命令执行前置推进**

建议增加纯函数 `_preflight_candidate_root_for(args) -> Path | None` 或同等命令分类边界。它只从参数中找出可能需要前置推进的根，不声称该根已经登记：

- 返回 `None`：版本、帮助、`init`、`adopt`、`check --candidate`；
- 返回 `Path(args.root)`：可能针对已登记 Draft 的检查、查询、所有写命令及其预演、恢复回收项、生成结论和故障恢复。

取得候选根后，先通过正式登记标记的共享解析语义做只读资格判定。只有规范标记读取边界确认根处于最终登记状态时，才调用一次 `prepare_registered_command`；根或标记缺失、无效、不支持或为临时接管形状时，不扫描任何生命周期目录，继续进入命令自身的既有验证/检查路径。任务 7 改变标记精确形状时必须同步更新这个共享边界，而不是在 CLI 复制键集合。实现可以避免重复解析，但不得改变命令原有错误形状或在资格成立前产生副作用。

资格成立后，在检查或分派业务命令前执行一次前置推进；不能让当前 `check` 的特殊分支绕过它。成功时丢弃推进细节；遇到 `LifecycleAdvanceError` 时，JSON 只渲染 `ok: false`、`result_type: error` 和结构化 `error`，内部损坏或恢复必需映射到退出码 3。不得沿用当前 `_structure_preflight_payload` 暴露 `lifecycle_results` 或 `stopped_at_operation_id` 的形状；只有错误诊断本身需要时才在 `error` 中保留 `operation_id`。

为任务 5 保留显式传入边界时间的调用方式，使 `query recoverable` 能把同一次捕获的 `now` 传给本步骤和 `list_recoverable(as_of=now)`。时钟注入的内部接口可以调整，但不允许两个协作者各自读取当前时间。

公开 `recover` 调用 `recover_interrupted_operations`，只报告已修复操作的数量和路径；生命周期移交活动保持隐藏。

- [ ] **步骤 5：运行生命周期和 CLI 门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_cli.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对生命周期活动没有进入任何普通成功输出。若适合独立提交，可使用 `refactor(draft): centralize silent lifecycle preflight` 作为提交说明建议。

---

### 任务 5：实现紧凑查询服务与 CLI 命令

**预计影响范围（非穷尽）：**
- 建议新建：查询服务、CLI 结果渲染、查询单元测试和查询集成测试模块
- 预计修改：图加载、CLI 编排及其现有测试

**建议的任务间接口：**
- 建议提供：`LocalPageSnapshot(document: PageDocument, body_resources: tuple[ResourceReference, ...])`、`load_registered_page(root: Path, page: Path) -> LocalPageSnapshot` 和 `load_complete_snapshot(root: Path) -> DraftGraph`。
- 建议提供查询函数：`page_summary`、`page_links`、`backlinks`、`outgoing_relations`、`incoming_relations`、`pages_with_status`。具体签名可以随实现调整，但局部查询和完整图查询必须保持明确边界。
- 建议提供结果类型：`LocalPageSnapshot`、`PageSummary`、`LinkItem`、`RelationItem`。
- 建议在 `cli_results.py` 或同等单一生产模块定义 `ResultType` 和受约束的预演操作代码，并由所有现有与新增 CLI 分支使用；内部类型名和模块可以调整，但批准规格第 5.3 节的字面量集合不能分散手写。
- 必须使用稳定错误码 `page.not_found` 表示合法严格页面操作数没有标识现存受管理页面；不得复用结构命令的 `structure.unknown_page`。该代码使用共享生产常量、有约束类型或同等单一权威来源，具体所属模块由实现决定。
- 依赖任务 4 的 `list_recoverable` 或同等语义边界。

- [ ] **步骤 1：编写纯内存查询行为测试**

使用内存解析页面、注入的资源探针和 `DraftGraph` 伪实现证明：

- `page_links` 排除关系链接位置，按身份去重，保持首次声明顺序，并从 `target` 去掉锚点；
- 反向链接按来源页面去重，再按来源路径和声明位置排序；
- 出站关系保持声明顺序，并在存在时包含 `parent`、`superseded_by`、`challenged_by`；
- 入站关系按来源路径和行号排序，保留来源声明的关系代码；
- 状态查询只返回按规范 POSIX 路径排序的页面；
- `PageSummary.link_count` 等于未分页去重链接总数，两个关系计数分别等于对应方向的关系查询总数。

建议采用以下结果形状；字段及语义来自批准规格，承载它们的内部类型名可以调整：

```python
@dataclass(frozen=True)
class LinkItem:
    target: str
    kind: ResourceKind
    scope: ResourceScope
    target_state: TargetState


@dataclass(frozen=True)
class RelationItem:
    relation: RelationCode
    page: Path


@dataclass(frozen=True)
class PageSummary:
    path: Path
    title: str
    status: StatusCode
    parent: Path | None
    child_count: int
    link_count: int
    backlink_count: int
    outgoing_relation_count: int
    incoming_relation_count: int
```

- [ ] **步骤 2：确认查询模块尚不存在**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_query.py" -q
```

预期：测试收集报错，并明确指出缺少 `draft.query`。

- [ ] **步骤 3：实现局部加载与完整加载边界**

`load_registered_page` 验证登记标记和一个严格页面参数，只读取并解析该文件，再通过任务 2 的 `resolve_page_resources` 生成快照。`query page`、`query links` 和 `query relations` 的 `PAGE` 都复用严格受管理页面解析语义；路径语法合法但文件不存在或不属于当前受管理页面集合时抛出 `page.not_found`，并由 CLI 映射为退出码 2。其他页面损坏时，`query links` 和出站关系仍应成功。纯函数 `page_links` 消费快照中已经解析的资源，使单元测试不访问文件系统。

`load_complete_snapshot` 发现并解析所有受管理页面。任一页面不可读、不可解析或登记所有权无效时整体失败；正文资源为 `missing`、`invalid`、`unavailable` 或 `unchecked` 本身不使加载失败。解析完整性与检查器诊断保持分离。

不要在 `load_graph()` 后隐藏部分图构建行为；明确命名局部和完整两类边界，并测试局部加载不会调用全局发现。

`query backlinks` 的 `TARGET` 不走严格页面解析。它必须使用任务 2 的宽松正文资源身份解析，接受 Draft 内外本地资源、目录、缺失目标和外部 URI；命令行相对值以 Draft 根目录为基准。为这两类操作数增加相互独立的 CLI 参数测试，防止共享分派时误用校验器。

- [ ] **步骤 4：实现分层命令解析和结果渲染**

增加以下命令：

```text
draft query page ROOT PAGE
draft query links ROOT PAGE
draft query backlinks ROOT TARGET
draft query relations ROOT PAGE --direction incoming|outgoing
draft query status ROOT CODE
draft query recoverable ROOT
```

所有集合查询接受默认值为 20、最大值为 100 的 `--limit`、非负 `--offset` 和 `--count-only`；不得提供 `--all`。`query page` 不分页。

建议在 `cli_results.py` 中定义通用纯分页器，并让查询和任务 6 的计划信封都通过同一个序列化构造边界产生六个分页字段：

```python
@dataclass(frozen=True)
class PageSlice[T]:
    items: tuple[T, ...]
    total: int
    offset: int
    limit: int
    has_more: bool
    next_offset: int | None


def paginate[T](items: tuple[T, ...], *, offset: int, limit: int) -> PageSlice[T]:
    selected = items[offset : offset + limit]
    next_offset = offset + len(selected)
    has_more = next_offset < len(items)
    return PageSlice(
        items=selected,
        total=len(items),
        offset=offset,
        limit=limit,
        has_more=has_more,
        next_offset=next_offset if has_more else None,
    )
```

`--count-only` 只在序列化最后把 `items` 置为空数组。`total`、`offset`、`limit`、`has_more` 和 `next_offset` 必须保留相同请求正常模式的计算结果，不能根据空数组重算。页面摘要 JSON 必须使用批准规格中的字段。人类输出只包含条目事实和一行计数或分页摘要；测试验证动态事实，不逐字复制完整句子。

把现有和新增成功/失败分支的 `result_type` 改为从任务间共享的受约束类型取值。至少覆盖 `init`、`adopt`、`check`、`move`、`promote`、`collapse`、`trash`、`restore`、`recover`、六个查询值、`plan`、`create`、`status.set`、两个关系值、`generate-conclusions` 和 `error`。测试可以精确断言单个公开字面量，但不得另建完整平行清单。

- [ ] **步骤 5：增加真实 CLI 范围和失败测试**

集成测试必须证明：

- 无关页面损坏时，合法目标的链接查询和出站关系查询不受阻；
- 同一个损坏页面会阻止页面摘要、反向链接、入站关系和状态查询，并且不得返回部分 JSON；
- 链接问题状态出现在退出码为 0 的成功 JSON 中；
- 空结果退出码为 0；
- `query page`、`query links` 和两个方向的 `query relations` 在合法 `PAGE` 不存在时返回 `error.code=page.not_found`、退出码 2，且不返回部分结果；
- `--count-only` 的 `items` 为空，但其余五个分页字段与同参数正常请求完全一致，包括仍有后续结果时的 `has_more` 和 `next_offset`；
- `limit=101`、负偏移、缺少方向，以及把 `status_label(StatusCode.PROPOSED)` 的本地化标签作为状态参数时，均以退出码 2 失败；
- `query backlinks` 从 Draft 根目录解析 CLI 相对目标，从各来源页面解析正文相对链接；
- `query backlinks` 的目标即使不存在也成功返回完整匹配结果或空结果，不得进入 `page.not_found`；
- 可恢复项结果包含 `as_of`，并严格按到期规则排序；使用时钟监视替身证明前置推进、过滤和输出共享一次捕获的当前时间，而不是只测试可注入的领域函数。

- [ ] **步骤 6：运行查询和 CLI 门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_query.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_queries.py" \
  "$RUNTIME/tests/integration/test_cli.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对局部查询和全局查询的失败范围与批准规格一致。若适合独立提交，可使用 `feat(draft): add compact query commands` 作为提交说明建议。

---

### 任务 6：为现有写命令增加语义化 `PlanEffect` 与预演

**预计影响范围（非穷尽）：**
- 建议新建：公开计划影响模型及其单元测试
- 预计修改：操作计划、结构修改、回收、CLI 结果渲染及相关测试

**建议的任务间接口：**
- 建议提供：`PlanAction`、`PlanEffect`、`public_changed_paths(effects) -> tuple[Path, ...]`。
- 必须保证：每个面向用户的写计划都能提供 `OperationPlan.effects: tuple[PlanEffect, ...]` 或同等语义数据。
- 建议为 `move`、`promote`、`collapse`、`trash` 分离准备计划和正式执行；正式执行必须复用预演使用的同一个计划构造边界。
- 必须保证：正式结果边界接收同一计划的完整公开效果或已经从中推导的公开路径；底层 journal mutation 不能直接成为 `changed_paths`。

- [ ] **步骤 1：编写 `PlanEffect` 不变量和序列化测试**

建议采用以下类型形状；四种公开动作及字段语义属于批准规格：

```python
class PlanAction(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    MOVE = "move"
    REMOVE = "remove"


@dataclass(frozen=True)
class PlanEffect:
    action: PlanAction
    path: Path | None = None
    from_path: Path | None = None
    to_path: Path | None = None
```

`__post_init__` 必须要求 `MOVE` 只使用 `from_path/to_path`，其他动作只使用 `path`。测试断言非法组合抛出 `ValueError`，移动 JSON 使用 `from`/`to`，并且任何序列化路径都不以 `.draft/` 开头。

增加结构测试，证明一个移动文件表现为一个 `MOVE`，而不是 `CREATE` 加 `REMOVE`；路径不变但内容被重写的页面表现为 `MODIFY`。增加回收测试，证明公开效果包含页面删除和父导航修改，但不包含回收包路径。

- [ ] **步骤 2：运行测试并确认当前缺少公开影响**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_plan_effects.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/unit/test_trash.py" -q
```

预期：由于 `PlanEffect` 和 `OperationPlan.effects` 尚不存在，测试收集或断言失败。

- [ ] **步骤 3：由计划器直接生成效果，不从底层变更猜测**

`structure.plan_path_remap` 已经知道旧路径到新路径的映射，应在该处生成 `MOVE`。路径不变但字节变化的页面生成 `MODIFY`。回收计划为每个公开删除页面生成 `REMOVE`，为仍存在但导航被改写的页面生成 `MODIFY`。

`public_changed_paths` 在去重时保持效果顺序：`CREATE`、`MODIFY`、`REMOVE` 提供 `path`，`MOVE` 依次提供 `from_path` 和 `to_path`。该函数从类型结构上排除所有内部变更。

不要通过配对底层写入、删除或哈希来猜测移动；这种做法会丢失计划器已经知道的业务语义。

调整当前正式结果数据流：`finalize_operation` 目前直接从 `journal.mutations` 构造 `changed_paths`；若 `trash` 或结论生成直接复用该结果，就会分别暴露 `.draft/trash/outgoing/...` 或 `.draft/draft.json`。实现必须让 `OperationResult`、`TrashResult` 或同等结果边界接收计划的公开效果或 `public_changed_paths(plan.effects)`；按 `.draft/` 前缀过滤只能作为防御断言，不能作为权威推导。`restore` 和 `recover` 虽然不支持预演，其公开路径也必须来自原操作持久化的公开语义或等价结果模型。若故障恢复后仍需报告公开路径，日志或恢复模型必须保存足够的公开语义，具体持久化方式由实施者根据现有恢复数据流决定。

- [ ] **步骤 4：为现有命令增加预演参数和分派**

为 `move`、`promote`、`collapse`、`trash` 增加 `--dry-run`、`--limit`、`--offset`、`--count-only`。预演调用与正式执行相同的计划构造边界并分页输出 `plan.effects`，但不得调用 `execute_plan`、分配日志、创建回收包或获取新的业务写锁。`--count-only` 只把 `effects` 置空，其余五个分页字段与相同预演请求一致；命令必要的其他业务事实仍保留。`--limit`、`--offset`、`--count-only` 未与 `--dry-run` 同时使用时以退出码 2 拒绝。任务 4 的集中生命周期推进仍然执行。计划器的名称和签名由实施者根据现有调用关系决定，不要求统一命名为 `plan_*` 或 `prepare_*`。

生命周期前置推进后若仍存在业务写锁或未完成操作，预演不得忽略、接管或自动恢复，也不得在不完整快照上继续；必须以 `operation.recovery_required`、退出码 3 和标准错误信封失败，不返回部分 `effects`。

非回收写命令的正式成功 JSON 只保留 `result_type`、必要业务事实和 `changed_paths`，移除 `phase`、普通 `operation_id`、`warnings` 和隐式 `ok: true`。`trash` 保留 `operation_id`、`restore_expires_at`、`changed_paths`，移除 `package`、`purge_eligible_at`、`phase`、`warnings`。

- [ ] **步骤 5：证明预演与正式执行一致且预演不执行业务写入**

对每个支持预演的现有写命令，集成测试在生命周期推进后记录公开文件和 `.draft` 状态，执行预演并断言业务文件、日志和回收包没有变化；再从等价夹具执行正式命令，断言 `changed_paths` 与完整、未分页预演效果推导出的路径一致。`trash` 必须覆盖公开页面、父导航、回收包元数据和 payload 同时变化的场景，证明正式结果不包含任何 `.draft/` 路径。另覆盖 `restore` 和 `recover` 的公开结果，证明它们也不从 journal mutation 直接暴露内部路径；其中 recover 必须分别构造 `TRASH_HANDOFF_SUCCEEDED` 和 `TRASH_PURGE_COMMITTED` 的提交态 trash 日志，断言清理结果不包含 `.draft/trash/outgoing/...`。

测试 20 和 100 的分页边界，比较 `--count-only` 与正常预演除 `effects` 外的全部字段，并拒绝 `--all`。另断言正式执行携带 `--limit`、`--offset` 或 `--count-only` 时退出码为 2，且不产生业务或生命周期之外的副作用。

另建既有业务写锁夹具，证明每个预演命令退出 3、错误码为 `operation.recovery_required`、不产生业务写入且不返回部分计划。该测试不能通过让预演先执行恢复来变绿。

- [ ] **步骤 6：运行门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_plan_effects.py" \
  "$RUNTIME/tests/unit/test_structure.py" \
  "$RUNTIME/tests/unit/test_trash.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_cli.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对预演效果和正式结果都只包含公开语义。若适合独立提交，可使用 `feat(draft): expose semantic dry-run plans` 作为提交说明建议。

---

### 任务 7：将结论页改为确定、按需、显式生成

**预计影响范围（非穷尽）：**
- 建议新建：结论生成模块及其单元测试和集成测试
- 预计修改：词汇、图加载、生命周期、检查、操作计划、结构、回收、CLI 及相关测试夹具

**建议的任务间接口：**
- 建议提供：`ConclusionsState.NOT_GENERATED`、`ConclusionsState.GENERATED`。
- 正式和临时登记标记都必须携带 `conclusions_state`，同时保持 `FORMAT_VERSION = 1`；如果继续使用 `MarkerState`，应由它表达该状态。
- 建议提供：`GenerationReason.NO_CONFIRMED_PAGES`、`GenerationPlan`、结论生成计划器和正式执行入口。
- 必须只允许结论生成操作修改内部路径 `.draft/draft.json`，其他操作仍不得修改内部路径。

- [ ] **步骤 1：编写登记标记、初始化和接管的失败测试**

修改生命周期预期：`init` 只创建 `index.md`、`pages/` 和以下登记标记：

```json
{"format_version":1,"conclusions_state":"not_generated"}
```

根索引不得包含结论链接。候选夹具不得包含 `conclusions.md`；候选检查和接管必须拒绝意外文件。正式登记标记精确包含 `format_version`、`conclusions_state` 两个键；临时接管标记精确包含 `format_version`、`conclusions_state`、`adopt_state`、`candidate_manifest` 四个键，结论状态始终为 `ConclusionsState.NOT_GENERATED`。登记标记测试拒绝缺失、未知或多余的结论所有权状态，并明确断言旧的 `{"format_version":1}` 以 `format.invalid` 失败。该破坏性格式变化有意不提供迁移、双读或兼容分支，即使 `FORMAT_VERSION` 保持为 1；测试预期从生产枚举读取所有权值，不在测试常量中复制完整机器值清单。

同步任务 4 的前置推进资格测试：新正式标记允许推进；旧标记和临时接管标记即使旁边残留操作日志也不扫描、不修改，再由命令自身报告既有格式或恢复问题。CLI 不得另存一份登记键集合。

- [ ] **步骤 2：编写生成器状态转换测试**

覆盖以下状态转换：

- `NOT_GENERATED` 且没有已确认页面：`generated=False`、原因为 `no_confirmed_pages`，没有计划或效果；
- `NOT_GENERATED` 且存在已确认页面：创建结论页、修改根页面、更新标记，公开效果隐藏标记文件；
- `GENERATED` 且提案内容不变：效果为空；
- `GENERATED` 且结论页状态为 `confirmed`：重新生成并改为 `proposed`；
- `GENERATED` 且没有已确认页面：保留文件和根链接，列表为空；
- `GENERATED` 且文件缺失、格式损坏或根链接漂移：重建规范结果；
- `NOT_GENERATED` 但文件意外存在：失败且不覆盖；
- 导航图无效：失败，不退化为路径排序。

构造路径顺序与索引声明顺序不同的内存导航，断言扁平结论列表按导航深度优先顺序生成。

- [ ] **步骤 3：运行定向测试并确认当前预建行为失败**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_lifecycle.py" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_conclusions.py" -q
```

预期：失败表明 `init` 仍会创建结论页、标记缺少所有权状态、生成器模块尚不存在。

- [ ] **步骤 4：实现所有权状态和规范生成计划**

建议采用以下类型形状：

```python
class ConclusionsState(StrEnum):
    NOT_GENERATED = "not_generated"
    GENERATED = "generated"


class GenerationReason(StrEnum):
    NO_CONFIRMED_PAGES = "no_confirmed_pages"


@dataclass(frozen=True)
class GenerationPlan:
    generated: bool
    reason: GenerationReason | None
    operation: OperationPlan | None
```

生成器筛选状态为 `confirmed` 的页面，并排除根页面和结论页。输出恰好一个一级标题、`proposed` 状态、父主题关系、`## 已确认结论`，然后为每个页面输出一个标题和链接条目。顺序来自父页面导航链接位置，不得使用 `Path` 顺序或 `DraftGraph.children` 的排序结果代替。

生成器使用专用的容错加载边界：严格解析登记标记、根页面和所有讨论页面；当所有权为 `GENERATED` 时，可以忽略缺失或格式损坏的现有 `conclusions.md`，因为该文件正是可替换目标。只有根 Markdown 本身仍可解析时，才可以修复根链接缺失或重复；根页面不可解析仍是硬失败。

结论生成模块应作为固定机器产物形状的单一生产代码权威来源，向检查器、页面创建、结构修改和回收提供共享常量或结构谓词。一级标题、`## 已确认结论`、规范根导航链接和机器列表区域不得由各消费者复制字面量后分别识别；增加一致性测试，证明生成器产物能被这些消费者以同一边界识别。

首次成功生成通过一个 `OperationPlan` 原子写入结论页、根导航和登记标记。只为 `plan.kind == "generate-conclusions"` 且目标恰好为 `.draft/draft.json` 的情况扩展 `_validate_plan_paths`；继续拒绝其他内部目标。

- [ ] **步骤 5：允许结论内容过时，但不削弱普通页面检查**

当登记状态为 `GENERATED` 时：

- 图发现要求 `conclusions.md` 存在，但列表目标只是可查询的资源事实，不产生普通断链错误；
- 检查器验证固定的一级标题、状态、父主题、章节、列表形状以及根导航中唯一且位于最后的结论链接，但不把列表成员、标题和路径与当前已确认页面比较；
- 结构渲染跳过机器结论列表拥有的正文链接；
- 回收阻碍计算忽略机器结论列表产生的入站链接。
- 查询把 `conclusions.md` 当作普通受管理页面：`query page` 的父页面为 `index.md`，`query status` 按页面讨论状态包含它，链接和关系查询不作特殊排除。

所有权进入 `GENERATED` 前，不得全局忽略 `conclusions.md` 的缺失链接；文件不存在时严格 `PAGE` 查询失败，但正文资源仍可报告它为 `page/draft/missing`。任何其他页面的断链检查也不得削弱。

- [ ] **步骤 6：增加 CLI 和预演**

增加 `generate-conclusions ROOT [--dry-run] [--limit N] [--offset N] [--count-only]`。正式 JSON 返回 `generated`、可选的 `reason` 和公开 `changed_paths`。预演使用任务 6 的计划信封；`--count-only` 只隐藏 `effects` 并保留其余分页元数据和业务事实，三个分页选项未与 `--dry-run` 同时使用时退出码为 2。命令每次实际生成时都写入 `proposed` 状态，绝不自动确认。

正式结果的 `changed_paths` 必须从同一生成计划的完整公开效果推导；首次生成虽然同时写 `.draft/draft.json`，公开结果只能包含实际变化的 `conclusions.md` 和 `index.md`。增加首次生成、只更新结论页、幂等无变化三类正式/预演一致性测试。

- [ ] **步骤 7：运行结论、生命周期和结构门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_lifecycle.py" \
  "$RUNTIME/tests/unit/test_graph.py" \
  "$RUNTIME/tests/unit/test_checker.py" \
  "$RUNTIME/tests/unit/test_conclusions.py" \
  "$RUNTIME/tests/integration/test_lifecycle.py" \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/integration/test_queries.py" \
  "$RUNTIME/tests/integration/test_conclusions.py" \
  "$RUNTIME/tests/integration/test_structure.py" \
  "$RUNTIME/tests/integration/test_trash.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对首次生成、重复生成和修复损坏产物三条路径均符合所有权规则。若适合独立提交，可使用 `feat(draft): generate conclusions on demand` 作为提交说明建议。

---

### 任务 8：增加原子叶子页面创建

**预计影响范围（非穷尽）：**
- 建议新建：页面创建模块以及页面命令的单元测试和集成测试
- 预计修改：路径校验、Markdown 渲染、CLI 编排和结果渲染

**建议的任务间接口：**
- 建议提供：`resolve_new_leaf_operand(root: Path, value: str) -> ResolvedPath`。
- 建议提供：页面创建计划器和正式执行入口；二者共享相同计划构造逻辑。
- 依赖 `StatusCode.PENDING|PROPOSED`、`physical_parent`、任务 6 的公开效果以及任务 7 的结论所有权状态。

- [ ] **步骤 1：编写路径、标题、状态和导航测试**

明确拒绝：绝对路径、使用反斜杠的机器页面参数、不在 `pages/` 下的路径、非 `.md` 路径、`.` 或 `..`、空路径段、任何 `index.md`、已经存在的目标、缺失的物理父页面、`confirmed`/`superseded`/`closed` 初始状态、空标题、换行和控制字符。

接受单行标题中的 Unicode 和 Markdown 标点。通过 `parse_page` 重新解析创建结果，并断言 `PageDocument.title` 等于输入，证明转义正确，而不是断言原始 Markdown 字符串。

测试导航插入：

- 追加到普通父页面 `## 子主题` 末尾；
- 根页面已有机器结论链接时，插入到该唯一链接之前；
- 页面创建和父页面修改位于同一个 `OperationPlan` 中，公开效果依次为 `CREATE`、`MODIFY`；
- 失败时页面和父页面都保持不变。

- [ ] **步骤 2：运行新单元测试并确认接口尚不存在**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_page_create.py" -q
```

预期：测试收集因缺少 `draft.page_create` 或 `resolve_new_leaf_operand` 而失败。

- [ ] **步骤 3：实现叶子路径校验和普通文本 Markdown 转义**

建议在 `markdown.py` 增加 `escape_markdown_text(value: str) -> str`；转义反斜杠和 CommonMark 标点，使解析后的行内文本仍等于请求标题。拒绝 Unicode 控制类别以及任何 `\r` 或 `\n`。

通过 `status_label` 和 `relation_label` 获取当前本地化标签并渲染页面骨架；不要在页面创建模块中重复中文状态映射。

普通父页面复用 `append_parent_navigation`；生成过结论页的根页面可以使用新的 `insert_navigation_before_conclusions` 辅助函数。后者必须通过解析后的目标识别规范结论链接，不得搜索文本 `conclusions.md`。

识别结论链接及其固定位置时，复用任务 7 的机器产物形状定义或谓词，不得在页面创建模块复制结论标题、章节或链接字面量。增加创建集成测试，证明生成器产生的根导航能被创建逻辑识别并保持在最后。

- [ ] **步骤 4：增加 CLI 正式执行和预演集成测试**

增加：

```text
draft create ROOT PAGE --title TITLE --status pending|proposed
```

支持任务 6 的预演和分页字段，包括 `--count-only` 只隐藏 `effects`；三个分页选项未与 `--dry-run` 同时使用时退出码为 2。测试正式 JSON 中的 `result_type=create`、创建路径、状态和公开变化路径；共享生命周期推进完成后，预演不得创建目录或业务日志。

- [ ] **步骤 5：运行门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_create.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对失败路径没有留下半个页面或孤立导航。若适合独立提交，可使用 `feat(draft): add atomic page creation` 作为提交说明建议。

---

### 任务 9：增加带最终状态验证的状态修改

**预计影响范围（非穷尽）：**
- 建议新建：页面元数据修改模块及其单元测试
- 预计修改：Markdown 编辑、图替换、CLI 编排、结果渲染和页面命令集成测试

**建议的任务间接口：**
- 建议提供：`plan_set_status(root: Path, page: Path, code: StatusCode, *, note: str | None = None, clear_note: bool = False) -> OperationPlan`。
- 建议提供：基于已定位状态范围的 `replace_status(text: str, code: StatusCode, explanation: str | None) -> str`。
- 建议提供：按 `page.path` 替换内存图页面的辅助边界，供本任务和任务 10 复用。

- [ ] **步骤 1：编写状态转换矩阵测试**

覆盖 `StatusCode` 的所有有序组合；只要最终图满足既有不变量，该转换就应成功。重点包括：

- `confirmed` → `proposed` 和 `closed` → `pending` 成功；
- 缺少 `superseded_by` 时，`proposed` → `superseded` 失败；
- 仍有 `superseded_by` 时，`superseded` → `proposed` 失败；
- 状态相同且没有说明选项时无变化；
- 未提供说明选项时保留当前说明；
- `--note` 替换说明，`--clear-note` 删除说明，两者同时出现时失败；
- 说明拒绝换行和控制字符，但接受任何语言；
- 合法严格页面操作数没有标识现存受管理页面时，领域边界抛出 `page.not_found`；CLI 集成测试再断言映射为退出码 2；
- 把机器生成的结论页改为 `confirmed` 时只改变状态，不刷新条目。

测试断言解析后的 `StatusValue`、最终检查报告和 `PlanEffect`，不逐字比较完整 Markdown。

- [ ] **步骤 2：确认状态计划器尚不存在**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_page_metadata.py" -q
```

预期：测试收集因缺少 `draft.page_metadata` 而失败。

- [ ] **步骤 3：通过最终内存图规划状态修改**

加载非派生结构没有既有错误的图，只重写状态范围，解析新页面并替换到重建图中，再对最终图运行 `check_graph`。最终图有错误时，抛出包含阻碍码的稳定领域错误；不得写入无效中间页面。

只有字节实际变化时才构造一个 `MODIFY` 变更和效果。状态计划器不增加或删除关系，也不调用结论生成器。

- [ ] **步骤 4：增加分层状态命令和预演**

增加：

```text
draft status set ROOT PAGE CODE [--note TEXT | --clear-note]
```

命令参数只接受稳定代码。JSON 返回 `result_type=status.set`、路径、最终状态和公开变化路径；人类输出包含动态路径和代码。合法严格 `PAGE` 没有标识现存受管理页面时返回 `page.not_found`、退出码 2。预演复用任务 6 的机制，包括 `--count-only` 和分页选项只能与 `--dry-run` 同时使用的约束。

- [ ] **步骤 5：运行状态门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_metadata.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对所有成功转换都满足最终图不变量，失败转换没有产生文件变化。若适合独立提交，可使用 `feat(draft): add validated status mutation` 作为提交说明建议。

---

### 任务 10：增加幂等的依赖、取代与质疑修改

**预计影响范围（非穷尽）：**
- 预计修改：页面元数据、Markdown 机器区域编辑、CLI 编排和结果渲染
- 预计修改：关系单元测试、页面命令集成测试和多文件回滚集成测试

**建议的任务间接口：**
- 建议提供：`plan_add_relation(root: Path, source: Path, code: EditableRelationCode, target: Path) -> OperationPlan`。
- 建议提供：`plan_remove_relation(root: Path, source: Path, code: EditableRelationCode, target: Path, *, restored_status: StatusCode | None = None) -> OperationPlan`。
- 建议提供编辑完整引用机器行且保留无关正文的 `append_machine_relation`、`remove_machine_relation` 辅助边界。
- 只接受 `EditableRelationCode`；反向关系代码和 `parent` 在 CLI 与领域边界都必须拒绝。

- [ ] **步骤 1：扩展关系矩阵单元测试**

必须覆盖：

- `depends_on` 只修改来源页面，并拒绝自引用和依赖环；
- `challenges` 原子增加或删除 `challenges` 与 `challenged_by`；
- `supersedes` 增加两个方向、把目标状态设为 `superseded`、保留状态说明，并拒绝取代环；
- 增加已经完整存在的双向关系时无变化；
- 增加只存在一边的关系时补齐缺失一边；
- 删除不存在的关系时无变化；
- 删除只存在一边的关系时清理残留；
- 删除多条取代关系中的一条时，目标仍为 `superseded`，并拒绝多余 `--status`；
- 删除最后一条取代关系时要求提供非 `superseded` 的 `--status`，并在同一计划中更新关系和状态；
- `parent`、`superseded_by`、`challenged_by` 不能作为修改参数；任务 1 已证明删除的旧关系不在公开类型中，不再增加兼容性测试；
- 来源或目标是合法严格路径但没有标识现存受管理页面时，领域边界抛出 `page.not_found`；CLI 集成测试再断言映射为退出码 2；
- 任何会妨碍证明最终结果有效的无关图错误都阻止命令。

直接从解析后的 Markdown 构造只存在半对关系的图，证明计划器只能修复本次目标不一致，不能把任意脏图视为可写。

- [ ] **步骤 2：运行测试并确认关系计划器尚不存在**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit/test_page_metadata.py" -q
```

预期：新增测试因关系计划器或辅助函数尚不存在而失败。

- [ ] **步骤 3：精确编辑机器行并定向修复半对关系**

`append_machine_relation` 使用 `relation_label(code)` 和规范的受管理页面相对目标，在开头机器区域末尾插入新的引用关系行。`remove_machine_relation` 根据解析后的关系链接范围删除完整引用行，不得删除正文中目标相同的普通链接。

对双向关系，先计算两个页面的最终成员关系，在内存中应用全部文本修改，解析两个页面并验证最终图。初始加载只可以容忍本次来源、代码、目标所对应的缺失反向问题；其他检查错误仍然阻止命令。

增加 `supersedes` 并改变目标状态时，复用任务 9 的状态渲染器。删除最后一对取代关系时，要求 `restored_status` 属于从类型推导的集合 `set(StatusCode) - {StatusCode.SUPERSEDED}`。不得猜测先前状态，也不保存隐藏恢复历史。

- [ ] **步骤 4：增加关系 CLI 和预演**

增加：

```text
draft relation add ROOT SOURCE depends_on|supersedes|challenges TARGET
draft relation remove ROOT SOURCE depends_on|supersedes|challenges TARGET [--status CODE]
```

只有 `remove` 且最终取代关系消失时才接受 `--status`。JSON 返回 `result_type=relation.add|relation.remove`、来源、代码、目标和公开变化路径。合法严格 `SOURCE` 或 `TARGET` 没有标识现存受管理页面时返回 `page.not_found`、退出码 2。预演支持 `--count-only`，且分页选项只能与 `--dry-run` 同时使用。增加集成测试，注入第二个页面写入失败并验证整体回滚。

- [ ] **步骤 5：运行关系门禁并形成检查点**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_page_metadata.py" \
  "$RUNTIME/tests/unit/test_cli.py" \
  "$RUNTIME/tests/integration/test_page_commands.py" \
  "$RUNTIME/tests/integration/test_operations.py" -q
(cd "$RUNTIME" && uv run --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
git status --short
git diff --stat
```

核对双向关系和必要状态始终原子变化。若适合独立提交，可使用 `feat(draft): add idempotent relation mutation` 作为提交说明建议。

---

### 任务 11：更新公开文档并运行全部分发门禁

**预计影响范围（非穷尽）：**
- 预计修改：`plugins/draft/skills/draft/SKILL.md` 和 `references/draft-format.md`
- 预计修改：分发契约、分发冒烟测试及其共享集成夹具

**必须交付：**
- 文档覆盖任务 1 至 10 实际实现的每个命令、稳定代码、路径规则、按需结论流程、预演信封、分页和生命周期边界。
- 文档明确 dry-run 的 `--count-only` 与分页选项组合限制、严格现存页面的 `page.not_found`，以及只有有效正式登记标记才允许生命周期前置推进。
- 保持核心与适配层的依赖方向，并保持运行时分发物自包含。

- [ ] **步骤 1：根据实际行为重写格式和技能工作流**

`draft-format.md` 必须展示不含 `conclusions.md` 的初始目录树，将稳定机器代码与当前中文 Markdown 标签分开说明，删除所有旧 `output` 规则和示例，记录 Draft 外正文资源，并说明机器生成的结论条目可以过时，直到下一次显式生成。文档还必须列出正式和临时登记标记的精确键集合，并明确旧的 `{"format_version":1}` 标记会以 `format.invalid` 失败且没有迁移命令。

`SKILL.md` 必须包含以下最终流程：

1. 与用户确认讨论结束；
2. 执行 `draft generate-conclusions ROOT`；
3. 从生成后的 `proposed` 结论页产出设计文档；
4. 产出完成后执行 `draft status set ROOT conclusions.md confirmed`。

同时说明查询选择：代理使用 `query page` 做路由，使用带 JSON 和分页的窄集合命令获取事实，使用 `rg` 搜索正文。不得要求代理在普通编辑过程中维护结论页。

- [ ] **步骤 2：增加语义化分发和帮助契约测试**

使用 `argparse` 命令参数解析器对象，或运行 `draft --help` 及子命令帮助，验证命令存在和选项结构，包括每个预演命令公开 `--count-only`，并通过解析/分派行为验证分页选项不能脱离 `--dry-run`。不得通过搜索生产源码文本来测试帮助。扩展抽象语法树契约，证明稳定状态、关系、`result_type`、预演 `operation` 和 `page.not_found` 协议值各自只有一个生产代码权威来源，并且核心从不导入插件适配层模块。测试可以在最接近输出的层精确断言单个协议字面量；所有权检查只禁止生产代码出现第二份完整定义，不能误把测试预期当作生产所有者。

增加分页构造契约，证明所有集合查询与预演都经过同一个生产序列化边界；正常模式与 `--count-only` 比较时，查询只允许 `items` 不同，预演只允许 `effects` 不同，其余五个分页字段必须分别完全一致。不要在各命令测试中复制分页计算逻辑。

更新共享集成夹具，明确生成 `NOT_GENERATED` 和 `GENERATED` 两种根目录；夹具名称必须直接说明其创建的生命周期状态。

审计适配层 `bridge`、`bindings`、`session_hook` 以及 `plugins/draft/scripts/tests/integration/test_distribution_smoke.py` 对 CLI `--help`、`check` 错误信封和成功 JSON 的假设；只修改实际受新协议影响的断言，不为未解析成功 JSON 的适配层增加推测性改动。

- [ ] **步骤 3：运行全部非联网质量门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m 'not network'
(cd "$RUNTIME" && uv run --locked pyright)
(cd plugins/draft/scripts && \
  uv run --project ../skills/draft/scripts/runtime --locked pyright)
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff check plugins/draft/scripts
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME"
uv run --project "$RUNTIME" --locked ruff format --check plugins/draft/scripts
uv lock --project "$RUNTIME" --check
```

预期：每条命令退出码均为 `0`；核心测试树和适配层测试树分别收集。

- [ ] **步骤 4：运行两个联网依赖窗口门禁**

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked \
  pytest "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked \
  pytest plugins/draft/scripts/tests/integration -q -m network
```

预期：每条命令退出码均为 `0`。如果外部解析失败，记录准确的网络或索引错误；只有证明它属于环境问题后才重试，不得修改依赖范围或跳过测试。

- [ ] **步骤 5：审计批准规格覆盖和脏工作区边界**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_vocabulary.py" \
  "$RUNTIME/tests/integration/test_distribution_contract.py" -q
uv run --project "$RUNTIME" --locked python - <<'PY'
from draft import diagnostic_codes
from draft.diagnostic_codes import CheckCode
from draft.vocabulary import RelationCode

assert "OUTPUT" not in RelationCode.__members__
assert all(not code.value.startswith("output.") for code in CheckCode)
assert not hasattr(diagnostic_codes, "WrongOutputReason")
PY
git status --short
git diff --check
```

预期：语义契约和运行时枚举检查通过，删除的关系、诊断及仅服务于它们的 `WrongOutputReason` 都不再公开。这里有意不搜索源码文本，因为无关变量名、注释和说明文字不能证明协议是否存在。工作区状态只包含本次实现改动以及实施前已经记录的用户基线；`git diff --check` 退出码为 `0`。

人工把批准规格第 5 至 18 节分别映射到至少一个通过的测试文件。这是需求覆盖审计，不能代替测试门禁。

- [ ] **步骤 6：形成最终可审查检查点**

```bash
git status --short
git diff --stat
```

本计划预计不需要修改 `AGENTS.md`，因为任务 1 至 10 实现的是现有约束。如果实施实际改变了长期规则，应按仓库要求更新受影响的 `AGENTS.md`，不要因为它未列在预计文件中而跳过。

若文档与最终行为一致且适合独立提交，可使用 `docs(draft): publish agent cli workflow` 作为提交说明建议。

---

## 最终审查清单

实施代理必须向审查代理提供：

- 按可独立审查行为组织的提交或变更清单，以及任何偏离建议任务边界的原因；
- 最终 `git status --short` 与实施前基线的比较；
- 任务 11 中单元测试、非联网集成测试、联网测试、Pyright、Ruff 和锁文件检查结果；
- 每个新增查询和写命令在人类输出与 JSON 模式下的命令和结果矩阵；
- 预演效果与正式执行公开变化路径一致的证据；
- `trash` 和 `generate-conclusions` 的内部 mutation 从未进入 `changed_paths`，且该结论来自公开效果数据流而不是路径前缀过滤的证据；
- `--count-only` 与正常请求共享分页元数据、`query recoverable` 共享一次时间基准、预演遇到既有写锁失败的边界证据；
- 所有预演命令支持 `--count-only` 且正式执行拒绝分页选项、无有效正式登记标记时残留内部目录不被推进、严格现存页面统一返回 `page.not_found` 的证据；
- 机器结论允许过时而不阻止 `move` 或 `trash`，并在重新生成后收敛的证据；
- 所有稳定状态、关系、结果类型和预演操作代码集合都从生产类型派生，而不是在测试或生产消费者中复制字符串清单的证据；
- 仍存在的假设或外部失败；只要门禁没有全部通过，就不得声称整体全绿。
