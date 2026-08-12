# Draft 候选根增量迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Agent 在固定的 `candidate` 根中复用普通 Draft CLI 渐进完成机械迁移，按需
执行完整检查，并通过同一套日志与恢复协议把该根原子推进为 `registered`。

**Architecture:** `.draft/draft.json` 是根身份的唯一权威来源；CLI 在共同边界把
`--root`、`DRAFT_ROOT` 或物理 `pwd` 解析成一个受管根。读取层同时提供局部事实和容错全根
事实，候选写命令只消费表中规定的事实闭包。计划器在内存中证明操作自有不变量，并把所有
输入观察写入普通操作日志；执行器在锁内复核观察、应用精确 staged bytes（暂存字节）并按
摘要提交。正式根继续使用全局健康门禁。`adopt` 是 `kind="adopt"` 的普通日志操作，以
marker 原子替换为单向提交点，由统一 `recover` 按 operation kind（操作种类）收敛。

**Tech Stack:** Python 3.12+、`argparse`、`pathlib`、`dataclasses`、`StrEnum`、
`markdown-it-py`、`Send2Trash`、pytest/pytest-mock、Pyright strict、Ruff、uv。

**Approved specification:**
[`2026-08-12-draft-candidate-root-incremental-migration-design.md`](../specs/2026-08-12-draft-candidate-root-incremental-migration-design.md)

---

## 全局约束

### 已确认的行为契约

- 本次直接重定义未发布的 `format_version=1`；不保留旧 marker、旧 CLI、provisional
  marker 或开发数据的兼容读取和升级路径。
- 只有 `draft init ROOT` 与 `draft init --candidate ROOT` 使用位置 `ROOT`；其他需根命令
  统一使用可选 `--root ROOT`，然后依次回退到绝对非空的 `DRAFT_ROOT` 和物理 `pwd`
  祖先发现。较高优先级存在但无效时不得回退。
- `candidate` 与 `registered` 共享页面协议和 `.draft/` 内部状态协议；差异只在普通业务
  写命令的门禁。候选写命令不调用完整检查器。
- `check` 始终完整扫描、严格只读，不推进生命周期；过滤和分页只限制输出，不改变
  `ok`、退出码或完整扫描计数。
- 查询不能把未知解释为空。候选全根集合查询按规格返回 `complete=false`；
  `query page` 的全根计数不可靠时以 `query.incomplete` 拒绝。
- 所有受管根的普通业务命令、查询、预演、`restore`、`recover` 和
  `generate-conclusions` 在业务前恰好推进一次生命周期；`check`、`adopt`、`init`、帮助
  和版本不推进。
- 候选写计划必须记录并复核它实际依赖的全部输入事实。不能只校验待写文件的
  `before_sha256`，因为入站链接扫描、判环和页面集合发现还读取了不会被改写的文件与目录
  成员集合。
- `adopt` 的 marker 替换是登记生效点。marker 已是 after 时只能完成提交和清理，不能
  回滚为 candidate。
- CLI 不接受来源路径。端到端测试必须证明来源目录字节不变。

### 项目规则

- 先写能够证伪行为契约的测试，再写最小生产实现。
- 单元测试不访问真实文件系统、不修改真实环境变量、不启动子进程；文件系统、环境、
  原子替换、锁和故障注入放到集成测试。
- 核心测试与插件适配层测试分开运行；适配层生产代码保持只依赖标准库且不导入核心包，
  核心也不导入适配层。
- 新增的有限字符串状态、operation kind 和错误码必须由生产枚举或等价受约束类型统一
  拥有；测试从生产权威来源读取合法集合，不复制清单。
- 不通过删除测试、放宽静态检查或吞掉错误使门禁变绿。
- 规格已经批准；以下模块名和内部函数签名是基于当前数据流的实现建议，不是新的外部
  行为限制。若实施时调整边界，必须保留同样的输入、输出、不变量、失败和恢复证据。

### 建议的跨任务内部接口

这些接口用来避免同一判断散落在 CLI、检查器和各业务模块：

```python
class RootState(StrEnum):
    CANDIDATE = "candidate"
    REGISTERED = "registered"


@dataclass(frozen=True)
class ManagedRoot:
    path: Path
    marker: MarkerState
    source: RootSource


@dataclass(frozen=True)
class OperationFacts:
    graph: DraftGraph
    root_state: RootState
    observations: ObservationManifest
```

- `graph.py` 拥有 marker 解析与容错页面发现；`paths.py` 拥有受管根选择。
- 建议新增 `observations.py`，统一捕获和复核普通操作与 `adopt` 的文件类型、摘要和目录
  成员集合。
- 建议新增 `operation_facts.py`，把 registered 全局健康加载、candidate 局部页面加载和
  candidate 全根可判定加载收束在一个边界。业务模块仍负责选择自己的事实闭包和验证自有
  不变量。
- `OperationPlan` 和 `Journal` 持有 `root_state`、受约束的 `OperationKind` 与观察清单。
  对 candidate，计划器先证明最终语义，执行器再证明“输入未变 + 输出字节等于计划”；
  对 registered，执行器继续在应用后运行完整健康验证。

## 文件映射

### 生产代码

- Marker、图与检查：
  `models.py`、`diagnostic_codes.py`、`diagnostics.py`、`graph.py`、`checker.py`
- 根、CLI 与结果信封：
  `paths.py`、`cli.py`、`cli_results.py`、`query.py`
- 事务证据与恢复：
  `operations.py`、建议新增的 `observations.py`、建议新增的 `operation_facts.py`
- 业务计划器：
  `page_create.py`、`page_metadata.py`、`structure.py`、`trash.py`、`conclusions.py`
- 生命周期与状态转换：
  `lifecycle.py`、`trash_lifecycle.py`、`trash_package.py`
- 适配层受影响输出：
  `plugins/draft/scripts/adapter/session_hook.py`

### 测试与夹具

- 核心单元测试：
  `runtime/tests/unit/test_{graph,paths,checker,cli,query,operations,page_create,page_metadata,relation_edit,structure,trash,conclusions,lifecycle}.py`
- 核心集成测试：
  `runtime/tests/integration/conftest.py`、`support.py`、
  `test_{identity,paths,cli,checker,queries,operations,page_commands,structure,trash,trash_lifecycle,conclusions,lifecycle,distribution_contract}.py`
- 适配层：
  `plugins/draft/scripts/tests/unit/test_session_hook.py` 与相关集成/分发测试。

### 文档

- `plugins/draft/skills/draft/SKILL.md`
- `plugins/draft/skills/draft/references/draft-format.md`
- `plugins/draft/skills/draft/references/migration.md`
- `docs/design.md`

### 规格覆盖索引

| 批准规格 | 主要任务 | 横切复核 |
|---|---|---|
| §5 根状态、marker、内部状态 | Task 1、4 | Task 8、14 |
| §6 初始化 | Task 2 | Task 13、14 |
| §7 根定位与 CLI | Task 3 | Task 12–14 |
| §8 普通命令与生命周期资格 | Task 9–12 | Task 13、14 |
| §9 操作级门禁与逐命令闭包 | Task 7、9–11 | Task 14 |
| §10 完整检查 | Task 4、5 | Task 13、14 |
| §11 查询完整性 | Task 6 | Task 12、14 |
| §12 adopt 与恢复 | Task 7、8 | Task 12、14 |
| §13–15 数据流、错误与安全 | Task 3、7、8、12 | Task 13、14 |
| §16–17 测试与验收 | 每个任务的红—绿步骤 | Task 13、14 |

---

## Task 1：建立根状态与错误码权威类型

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`

- [ ] **Step 1: 写 marker 精确形状和错误码所有权测试**

  覆盖两个 `root_state` × 两个 `conclusions_state`、未知字段、缺字段、布尔版本、未知整数
  版本。测试必须明确拒绝旧的两键 marker 和把 provisional 字段塞入 `draft.json`：

  ```python
  @pytest.mark.parametrize("root_state", tuple(RootState))
  @pytest.mark.parametrize("conclusions_state", tuple(ConclusionsState))
  def test_parse_marker_accepts_exact_managed_root_state(
      root_state: RootState,
      conclusions_state: ConclusionsState,
  ) -> None:
      marker = parse_marker_value(
          {
              "format_version": 1,
              "root_state": root_state.value,
              "conclusions_state": conclusions_state.value,
          },
          Path(".draft/draft.json"),
      )
      assert marker.root_state is root_state
      assert marker.conclusions_state is conclusions_state
  ```

  同时断言 `CheckCode` 增加 `internal.unexpected_entry`，删除两个 `candidate.*`；新增非检查
  错误码枚举拥有 `root.not_found`、`root.ambiguous`、`query.incomplete`。

- [ ] **Step 2: 运行测试并确认按预期失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_graph.py" \
    "$RUNTIME/tests/unit/test_diagnostics.py" \
    "$RUNTIME/tests/integration/test_distribution_contract.py" -q
  ```

  预期失败：当前 `MarkerState` 没有 `root_state`，旧 candidate 检查码仍已登记。

- [ ] **Step 3: 实现受约束状态和精确 marker 解析**

  - 在 `models.py` 定义 `RootState`；在 `diagnostic_codes.py` 让新错误码各有一个生产字面量
    所有者。
  - `MarkerState` 只暴露 `version`、`root_state`、`conclusions_state`。
  - `parse_marker_value` 只接受三个精确键；版本非整数报 `format.invalid`，未知整数报
    `format.unsupported`。
  - 更新诊断渲染注册表，删除旧 candidate 文案并增加未知内部条目文案。
  - 分发契约用 AST 检查新的已登记字面量仍只有权威枚举拥有，不用源码字符串搜索替代
    导入/结构检查。

- [ ] **Step 4: 运行定向测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_graph.py" \
    "$RUNTIME/tests/unit/test_diagnostics.py" \
    "$RUNTIME/tests/integration/test_distribution_contract.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "refactor(draft): 建立受管根状态协议"
  ```

## Task 2：重写初始化并切换共享测试夹具

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/conftest.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py`

- [ ] **Step 1: 写两种初始化状态和失败原子性测试**

  测试 `init ROOT` 写 registered，`init --candidate ROOT` 写 candidate；二者都保留精确
  三键 marker。对非空目录、文件目标、发布前写失败和 marker 发布失败注入故障，断言不会
  留下可被识别为完成根的半初始化目录。

  ```python
  def test_init_candidate_writes_exact_marker(tmp_path: Path) -> None:
      root = tmp_path / "candidate"
      assert main(["init", "--candidate", str(root), "--json"]) == 0
      assert json.loads((root / ".draft/draft.json").read_text()) == {
          "format_version": 1,
          "root_state": "candidate",
          "conclusions_state": "not_generated",
      }
  ```

- [ ] **Step 2: 运行生命周期用例并确认旧 provisional 流程导致失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_lifecycle.py" \
    "$RUNTIME/tests/integration/test_lifecycle.py" \
    "$RUNTIME/tests/integration/test_identity.py" -q -k 'init or initialize'
  ```

- [ ] **Step 3: 实现显式状态初始化**

  建议签名为 `initialize(root: Path, *, root_state: RootState) -> None`。用同父目录暂存并
  耐久发布，或保持等价的可证明回滚协议；不得再通过 candidate check + provisional marker
  绕一圈创建正式根。CLI 的 `init` 增加 `--candidate`，仍要求位置 `ROOT`。

- [ ] **Step 4: 把所有真实根夹具切到新 marker**

  - `draft_factory(files, root_state=RootState.REGISTERED)` 取代 `registered: bool`。
  - `build_valid_candidate` 必须创建 candidate marker，不再表示“没有 `.draft/` 的目录”。
  - `write_raw_journal` 使用任务 7 最终日志版本前的当前权威常量，避免夹具复制版本号。

- [ ] **Step 5: 运行定向测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/integration/test_lifecycle.py" \
    "$RUNTIME/tests/integration/test_identity.py" -q -k 'init or initialize'
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 初始化候选或正式受管根"
  ```

## Task 3：统一非初始化命令的根选择与 CLI 语法

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_paths.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_paths.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`

- [ ] **Step 1: 用纯替身写完整根选择决策表**

  单元测试通过受真实签名约束的 probe（探针）提供目录、marker 和祖先事实，不访问真实
  文件系统或环境。至少逐项覆盖：CLI > env > pwd、较高层非法不回退、显式相对路径按物理
  pwd 解析、env 必须绝对且非空、最近损坏 `.draft/` 阻断外层、两个合法祖先根歧义、无根。

  ```python
  def test_invalid_environment_root_does_not_fall_back_to_pwd(
      root_probe: RootProbe,
  ) -> None:
      with pytest.raises(DraftError) as caught:
          select_managed_root(
              explicit=None,
              environment_value="relative/root",
              physical_cwd=Path("/work/inner"),
              probe=root_probe,
          )
      assert caught.value.code == "root.invalid"
      assert root_probe.inspected == ()
  ```

- [ ] **Step 2: 写真实文件系统集成矩阵**

  验证根内深层 `pwd`、嵌套合法根歧义、损坏近根、绝对 `DRAFT_ROOT`、显式 `--root`
  覆盖环境、缺目录与非目录。精确断言新错误码的 `path` 和 `details`。

- [ ] **Step 3: 运行用例并确认旧位置 ROOT 语法失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_paths.py" "$RUNTIME/tests/unit/test_cli.py" \
    "$RUNTIME/tests/integration/test_paths.py" "$RUNTIME/tests/integration/test_cli.py" \
    -q -k 'root or parser or positional'
  ```

- [ ] **Step 4: 实现单一 `ManagedRoot` 选择边界**

  - `main` 允许注入 `environ: Mapping[str, str]` 和物理 cwd 提供者，生产缺省才读取
    `os.environ`/`os.getcwd()`；单元测试不改进程全局状态。
  - 所有非 init 叶命令删除位置 root 并增加 `--root`。页面操作数保留原相对顺序；
    `status set`、`relation add/remove` 和各 `query` 叶命令也各自接受该选项。
  - 在分派、生命周期推进和页面操作数解析前只解析一次根，并把规范绝对根传给后续层。
  - `init` 不调用选择器且忽略 `DRAFT_ROOT`；为此增加一个环境中同时存在另一合法根的
    集成用例。帮助、版本不解析根。

- [ ] **Step 5: 运行 CLI/路径测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_paths.py" "$RUNTIME/tests/unit/test_cli.py" \
    "$RUNTIME/tests/integration/test_paths.py" "$RUNTIME/tests/integration/test_cli.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 统一受管根定位与命令语法"
  ```

## Task 4：让两种根共享完整检查与内部状态协议

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`

- [ ] **Step 1: 写受管根内部状态分类测试**

  对 candidate 和 registered 运行同一参数化矩阵：合法 marker、完成日志、活动锁、trash
  包、非法顶层条目、已知目录损坏、符号链接。断言非法顶层条目使用
  `internal.unexpected_entry`，已知协议损坏仍使用最具体的现有代码。

  ```python
  @pytest.mark.parametrize("root_state", tuple(RootState))
  def test_managed_roots_share_operation_state_diagnostics(
      draft_factory: DraftFactory,
      root_state: RootState,
  ) -> None:
      root = draft_factory(VALID_FILES, root_state=root_state)
      write_corrupt_operation_journal(root)
      report = check_draft(root)
      assert CheckCode.OPERATION_JOURNAL_CORRUPT.value in {
          issue.code for issue in report.errors
      }
  ```

- [ ] **Step 2: 写 `check` 根状态推断和只读测试**

  删除 `--candidate`；对两种根都用 `check --root ROOT`。检查前后用 `snapshot_tree` 比较
  全树，活动锁时仍应同时报告可安全扫描的页面问题和恢复问题。

- [ ] **Step 3: 运行并确认 candidate 内部拒绝分支造成失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_graph.py" "$RUNTIME/tests/unit/test_checker.py" \
    "$RUNTIME/tests/integration/test_checker.py" \
    "$RUNTIME/tests/integration/test_operations.py" -q -k 'candidate or internal or readonly'
  ```

- [ ] **Step 4: 合并加载与检查路径**

  - 删除 `_candidate_internal_issues` 及 `_LOAD_CANDIDATE_ISSUES`。
  - `load_graph_tolerant` 从 marker 得到 `RootState`，页面发现协议保持一致。
  - 抽出受管内部命名空间检查；候选也检查日志、锁、trash、结论所有权。
  - `check_draft(root)` 不再接受外部 mode；内部测试若需要纯图检查，继续直接测试
    `check_graph`。
  - `check` 不调用生命周期、恢复或清理函数。

- [ ] **Step 5: 运行检查相关测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_graph.py" "$RUNTIME/tests/unit/test_checker.py" \
    "$RUNTIME/tests/integration/test_checker.py" \
    "$RUNTIME/tests/integration/test_operations.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "refactor(draft): 统一候选与正式根检查协议"
  ```

## Task 5：实现完整检查的过滤、统一分页与计数信封

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

- [ ] **Step 1: 写纯投影函数测试**

  建议用 `project_check(report, codes, path_prefixes, offset, limit, count_only)` 形成一个
  `CheckSlice`。测试必须从唯一规范 issue 流先过滤、再分页、最后按严重级别分组：

  ```python
  def test_check_filters_union_within_dimension_and_intersection_between_dimensions() -> None:
      result = project_check(
          REPORT,
          codes=(CheckCode.PARENT_MISSING, CheckCode.LINK_MISSING),
          path_prefixes=(Path("pages/a"), Path("pages/b")),
          offset=0,
          limit=20,
          count_only=False,
      )
      assert tuple(issue.path for issue in result.items) == (
          Path("pages/a/index.md"),
          Path("pages/b.md"),
      )
  ```

  另测路径段边界（`pages/a` 不匹配 `pages/ab.md`）、重复值幂等、无 path 诊断不匹配
  prefix、绝对/含 `..`/空前缀的参数拒绝、默认 20、最大 100、count-only 数组为空但计数
  不变。

- [ ] **Step 2: 固定 JSON 字段**

  建议序列化为 `severity_counts`（键为 `error|warning|info|review`）、`code_counts`
  （只含出现过的码并按码排序）、`filtered_total`、`offset`、`limit`、
  `returned_count`、`has_more`、`next_offset` 和四个现有分组数组。`ok` 和退出码只读未过滤
  report；人类输出只展示当前页，但最终摘要必须包含未过滤错误总数和过滤后总数。

- [ ] **Step 3: 写 CLI 参数前置拒绝测试**

  `--code` 和 `--path-prefix` 均可重复。未知检查码由生产 `CheckCode` 生成的 choices 或
  等价验证在根解析和扫描前以退出码 2 拒绝；用 spy 断言检查协作者未调用。

- [ ] **Step 4: 运行测试并确认新投影协议失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_cli.py" "$RUNTIME/tests/integration/test_cli.py" \
    "$RUNTIME/tests/integration/test_checker.py" -q -k 'check and (filter or page or count or code)'
  ```

  预期失败：当前 CLI 没有过滤参数，并且 `_render_check` 直接序列化全部四组结果。

- [ ] **Step 5: 实现检查投影和 CLI 参数**

  让 parser、纯投影函数、人类渲染和 JSON 渲染共享同一个 `CheckSlice`；不得在四个严重级别
  内分别分页，也不得让渲染层重新计算 `ok`。

- [ ] **Step 6: 运行同一组测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_cli.py" "$RUNTIME/tests/integration/test_cli.py" \
    "$RUNTIME/tests/integration/test_checker.py" -q -k 'check and (filter or page or count or code)'
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 为完整检查增加有界结果投影"
  ```

## Task 6：实现查询完整性与阻碍集合双分页

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_query.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py`

- [ ] **Step 1: 写七类查询的状态决策表测试**

  每类至少覆盖 candidate 完整、candidate 有无关坏页、registered 同样损坏。局部查询只因
  目标页失败；全根候选查询返回已证明项和 `complete=false`；registered 传播最具体损坏。
  `query recoverable` 只看内部证据，不受 Markdown 坏页影响。

- [ ] **Step 2: 写双分页与 `query.incomplete` 精确协议测试**

  ```python
  def test_partial_query_pages_items_and_blockers_with_same_window() -> None:
      payload = collection_payload(
          items=ITEMS,
          blocking_paths=BLOCKERS,
          complete=False,
          offset=20,
          limit=20,
          count_only=False,
      )
      assert payload["offset"] == 20
      assert payload["blocking_total"] == len(BLOCKERS)
      assert payload["blocking_next_offset"] == 40
  ```

  `count-only` 时 `items` 与 `blocking_paths` 都为空，但两套计数和 next offset 不变。
  `query page` 的错误 path 是请求页，details 第一项是阻碍总数，之后最多 20 个排序路径。
  部分集合查询仍以退出码 0 成功；成功的 `query page` 必须显式返回 `complete=true` 与
  `blocking_paths=[]`。

- [ ] **Step 3: 运行测试并确认旧查询无法表达不完整结果**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_query.py" "$RUNTIME/tests/unit/test_cli.py" \
    "$RUNTIME/tests/integration/test_queries.py" -q
  ```

  预期失败：当前全根查询整体失败，所有成功信封也缺少 `complete` 和 blocker 分页字段。

- [ ] **Step 4: 实现容错查询视图**

  - 复用 `LoadResult.graph` 中成功解析的页面，并把每页加载失败规范化为去重排序的
    `blocking_paths`。
  - 只有能够归属到具体页面的解析失败可变成候选部分结果。marker、内部状态、根布局或
    无法安全归属的损坏继续整体失败，不能伪造一个路径占位。
  - 局部查询使用单页加载器；全根查询使用容错视图；registered 的全根视图发现任何
    blocker 都传播对应损坏，局部查询仍只受目标页影响。
  - 所有成功查询增加 `complete`；所有集合增加空或非空 `blocking_paths` 及其分页字段。

- [ ] **Step 5: 运行同一组测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_query.py" "$RUNTIME/tests/unit/test_cli.py" \
    "$RUNTIME/tests/integration/test_queries.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 显式表达候选查询完整性"
  ```

## Task 7：把全部输入观察纳入操作计划、日志和恢复

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_observations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`

- [ ] **Step 1: 写观察清单纯模型和比较矩阵**

  `ObservationManifest` 必须能表达根内相对路径的 `missing`、普通文件摘要、目录成员集合、
  符号链接和其他非法类型。测试用 fake probe，覆盖未变、只发生本操作已记录的增删改、
  未观察文件变化、目录新增条目、类型变化、摘要变化和排除精确路径。

  ```python
  def test_verify_rejects_new_member_in_globally_observed_pages_tree() -> None:
      manifest = ObservationManifest(
          entries=(directory_observation(Path("pages"), ("a.md",)),),
      )
      probe = fake_probe({Path("pages"): directory_state("a.md", "hidden.md")})
      assert verify_observations(probe, manifest, explained_mutations=()) == (
          Path("pages"),
      )
  ```

- [ ] **Step 2: 写 journal 新形状和恢复证据测试**

  直接提升 `JOURNAL_VERSION`，不双读旧日志。`OperationKind` 用 `StrEnum` 集中拥有现有
  `create`、结构、关系、trash/restore、结论、recover 与新增 `adopt` 值。日志精确持久化
  `root_state` 和 observations；未知 kind、观察路径逃逸、重复路径、摘要形状错误都报
  `operation.journal_corrupt`。

- [ ] **Step 3: 运行并确认当前日志没有读集证据**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_observations.py" "$RUNTIME/tests/unit/test_operations.py" \
    "$RUNTIME/tests/integration/test_operations.py" -q -k 'observation or journal or external'
  ```

  预期失败：当前 `OperationPlan`/`Journal` 只记录 mutation，没有 observations，锁内无法发现
  未修改输入或目录成员发生的并发变化。

- [ ] **Step 4: 实现观察捕获与事务复核**

  - `OperationPlan` 增加 `root_state` 和 `observations`，删除字符串
    `validation_mode`；`Journal` 同步。
  - mutation 的 `before_sha256` 继续拥有待改路径（包括不存在的新目标）的并发分类；
    observations 记录实际读过的其他文件和用于发现集合的目录成员。每个计划还必须观察
    当前 marker；会改 marker 的结论生成/adopt 则由 marker mutation 的 before 摘要拥有。
  - 日志预分配和加锁产生的本操作目录/锁只作为精确的 explained/excluded 条目处理；不能
    排除整个 `.draft/operations/`，否则会漏掉并发内部状态变化。
  - 取得锁后、首次业务 mutation 前复核全部观察；发生差异时不应用 mutation，安全终态化
    自己的准备日志并报告 `operation.external_change`。
  - 恢复时把当前 mutation 的 before/after 分类作为“本操作可解释变化”，再复核其余文件
    和目录成员；不能解释的差异保留锁、日志和恢复材料。
  - candidate 计划器必须在生成计划前解析 staged 文本并证明操作自有不变量；执行后只需
    核对所有 mutation 等于计划 after 及未改观察仍成立，不调用完整 checker。registered
    保留应用后的完整检查。

- [ ] **Step 5: 跑事务测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_observations.py" "$RUNTIME/tests/unit/test_operations.py" \
    "$RUNTIME/tests/integration/test_operations.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "refactor(draft): 在操作日志中固化完整输入观察"
  ```

## Task 8：把 `adopt` 接入普通日志并实现单向恢复

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`

- [ ] **Step 1: 写 adopt 计划边界测试**

  断言仅 candidate 可接管，registered 精确报 `adopt.already_registered`，任何已有未完成
  操作都在新日志产生前拒绝；先运行无过滤无分页完整检查；观察清单含受管 Markdown、
  marker、既有日志/恢复副本/trash 包、相关目录成员和完整检查读到的根内本地资源。锁内
  复核只排除本次 operation 目录与 `operation.lock`，根外资源重新观察但不持久化。

- [ ] **Step 2: 写 marker × phase × evidence 恢复决策表**

  用参数化单元测试逐格覆盖规格 12.2：before+证据匹配前向、before+staged 缺失放弃、
  before+外部变化只清理自己、after 只提交清理、终态反向组合不一致、未知 marker 外部
  变化、无匹配锁中段不一致、损坏材料使用最具体代码。

  ```python
  def test_recover_adopt_after_marker_never_reads_before_or_staged_bytes(
      adopt_journal: Journal,
      operation_doubles: OperationDoubles,
  ) -> None:
      operation_doubles.marker_sha256.return_value = adopt_journal.mutations[0].after_sha256
      result = recover_adopt(operation_doubles.root, adopt_journal)
      assert result.phase is OperationPhase.COMPLETED
      operation_doubles.read_recovery_copy.assert_not_called()
  ```

- [ ] **Step 3: 写真实故障注入集成测试**

  至少在日志预分配后、锁后复核前、marker 替换前、替换后日志提交前、清理前注入异常。
  每次重跑唯一公开 `recover`，断言 before 只在证据仍匹配时前向，after 始终收敛到
  registered。外部编辑业务文件不得被覆盖；marker 已是 after 后发生的其他业务编辑也只
  能影响自己的文件，不能触发根身份反转。

- [ ] **Step 4: 运行测试并确认 provisional 流程和普通恢复不满足决策表**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_lifecycle.py" "$RUNTIME/tests/unit/test_operations.py" \
    "$RUNTIME/tests/integration/test_lifecycle.py" \
    "$RUNTIME/tests/integration/test_operations.py" -q -k 'adopt or recover'
  ```

  预期失败：当前 adopt 不写普通日志，且无锁/中段恢复不能按 marker before/after 收敛。

- [ ] **Step 5: 实现 adopt 专用 operation-kind 分支**

  - `adopt` 生成只有 marker 这一项 managed mutation 的普通计划；after 字节只改变
    `root_state`，保留版本与 `conclusions_state`。
  - marker 是最后一项受管根 mutation。替换前推进到“业务验证完成、可以提交”的相位；
    替换后只核对 after 摘要并完成日志，不再运行全根检查。
  - 锁竞争或锁前复核失败时清理/终态化本次尚未授权业务变更的预分配日志。
  - `operations.recover` 按 `OperationKind.ADOPT` 分派上述单向规则；
    `trash_lifecycle.recover_interrupted_operations` 仍调用这一个恢复入口。
  - 删除 `PROVISIONAL_MARKER_PATH`、所有 provisional manifest 解析/写入/清理函数与
    `adopt.recovery_required` 分支，不保留死代码。

- [ ] **Step 6: 运行生命周期和恢复测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_lifecycle.py" "$RUNTIME/tests/unit/test_operations.py" \
    "$RUNTIME/tests/integration/test_lifecycle.py" \
    "$RUNTIME/tests/integration/test_operations.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 以普通日志原子接管候选根"
  ```

## Task 9：建立候选操作事实加载器并迁移局部写命令

**Files:**

- Create: `plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operation_facts.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_create.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_relation_edit.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py`

- [ ] **Step 1: 写事实加载决策测试**

  `load_operation_facts` 至少提供三种规范能力：registered 全局健康、candidate 指定页局部
  可判定、candidate 全受管页可判定。测试 A 页坏状态不阻止 B 的 status set，坏页不阻止
  无关 create；但目标/父页坏时拒绝。用 spy 断言 candidate 路径未调用 `check_draft`。

- [ ] **Step 2: 写 create 与 status 自有不变量测试**

  - create 通过新页 mutation 的 `before_sha256=None` 证明目标不存在，并观察父页、相关
    目录成员和 generated 结论导航；新页模板、父关系、父导航和结论末位均在计划内存态
    通过解析验证。
  - status 只观察目标页与 marker 所有权；精确验证 `superseded` 当且仅当目标仍有
    `superseded_by`，不因别页关系/状态错误拒绝。
  - dry-run 与正式执行使用同一个事实加载器和计划器。

- [ ] **Step 3: 写 relation 局部/全局分界测试**

  `challenges` 只需两端；`depends_on` 和 `supersedes` 必须完整发现相应关系图。不可解析页
  可能隐藏边时拒绝；正文断链不阻止判环。测试正反配对、自引用、幂等、循环和删除最后
  一条取代关系的显式恢复状态。

- [ ] **Step 4: 运行测试并确认完整图门禁过度阻塞**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_operation_facts.py" \
    "$RUNTIME/tests/unit/test_page_create.py" \
    "$RUNTIME/tests/unit/test_page_metadata.py" \
    "$RUNTIME/tests/unit/test_relation_edit.py" \
    "$RUNTIME/tests/integration/test_page_commands.py" -q
  ```

  预期失败：当前 create/status/relation 都经完整图加载或 `check_graph`，候选根中的无关诊断
  会阻止局部命令，且计划没有携带 root state 与观察清单。

- [ ] **Step 5: 实现 operation facts 与局部计划验证**

  重构计划器从 `OperationFacts` 取得 root state 与 observations；删除
  `_validate_final_graph` 的全根健康依赖。无变化的幂等计划也必须携带正确 root state；它不
  创建日志。

- [ ] **Step 6: 运行同一组测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_operation_facts.py" \
    "$RUNTIME/tests/unit/test_page_create.py" \
    "$RUNTIME/tests/unit/test_page_metadata.py" \
    "$RUNTIME/tests/unit/test_relation_edit.py" \
    "$RUNTIME/tests/integration/test_page_commands.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 以局部事实执行候选页面命令"
  ```

## Task 10：迁移 move、promote 与 collapse 的全局相关事实闭包

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operation_facts.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`

- [ ] **Step 1: 写结构命令相关/无关损坏矩阵**

  全根中任一不可解析页面可能隐藏正文链接、正式关系或父导航，因此必须阻止结构变换；
  但已经可解析且仅有无关全局错误（例如别处的依赖环、状态/取代不匹配或容量提醒）不得
  因 `check_graph` 全量结果阻止本次操作。

- [ ] **Step 2: 写三类结构操作输出不变量测试**

  - move：移动子树、目标不存在、所有受影响正文链接/关系/父导航重写、路径不逃逸、受影响
    祖先不留下空枢纽。
  - promote：只吸收显式 children，叶到枢纽映射确定，未指定兄弟不变。
  - collapse：只有允许的空枢纽可折叠，最终父关系和链接均指向叶路径。
  - 三者在 plan 到 execute 之间新增可能引用目标的页面时，目录观察复核拒绝且文件不变。

- [ ] **Step 3: 运行并确认 `_load_clean_graph`/`check_graph` 过度阻塞**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_structure.py" \
    "$RUNTIME/tests/unit/test_operation_facts.py" \
    "$RUNTIME/tests/integration/test_structure.py" -q
  ```

  预期失败：当前 `_load_clean_graph` 要求完整检查通过，`plan_path_remap` 又以全部
  `check_graph` 错误为门禁，不能只拒绝与结构变换相关的未知事实和最终不变量。

- [ ] **Step 4: 把结构规划限定到受影响结构子图**

  - 删除 `_load_clean_graph` 对完整 checker 的依赖。
  - 全根加载只要求受管理页面集合与每页可解析，从而能证明引用扫描完整；普通正文断链是
    已知资源事实，不是缺失输入。
  - 重构 `_final_parents`、空枢纽折叠和最终验证，不要求无关页面已经满足全部父/关系/容量
    规则；只验证映射集合、受影响父子、受影响导航和所有重写目标。
  - 观察清单包含全部被扫描页面及受管理目录成员，确保恢复可证明没有遗漏并发新增引用。

- [ ] **Step 5: 运行测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_structure.py" \
    "$RUNTIME/tests/unit/test_operation_facts.py" \
    "$RUNTIME/tests/integration/test_structure.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 以相关事实闭包执行候选结构变换"
  ```

## Task 11：让 trash、restore 与结论生成支持候选根

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_conclusions.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py`

- [ ] **Step 1: 写 candidate trash/restore 事务测试**

  trash 必须完整发现目标、父导航和所有可能入站引用，忽略允许过时的机器结论列表；创建的
  包、期限和日志与 registered 完全同形。restore 从指定日志和包构造事实闭包，不要求页面
  全根健康；目标占用、过期、关联不对称和包损坏均保持既有硬失败与唯一包所有权。

- [ ] **Step 2: 写 candidate 结论生成测试**

  只有全部受管页面路径、标题、状态和导航顺序可判定时允许；候选 marker 的
  `conclusions_state` 与结论页、根导航作为同一事务修改。坏正文链接不阻止，坏机器块或
  不完整导航阻止。无 confirmed 页面保持现有 no-op 契约。

- [ ] **Step 3: 运行并确认硬编码 registered validation 失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_trash.py" "$RUNTIME/tests/unit/test_conclusions.py" \
    "$RUNTIME/tests/integration/test_trash.py" \
    "$RUNTIME/tests/integration/test_conclusions.py" -q
  ```

- [ ] **Step 4: 接入 operation facts 和 observations**

  - trash 使用 candidate 全根可判定加载，但不调用完整 checker；registered 仍先全绿。
  - trash 日志保留创建时的根状态作为审计事实；restore 计划必须重新读取当前 marker。
    candidate 按包与受影响页面的操作级契约恢复，已经 adopt 为 registered 的根则按正式根
    严格门禁执行，不能让旧 trash 日志把当前根降回候选语义。
  - `_marker_bytes` 必须保留当前 `root_state`，只改变 `conclusions_state`。
  - 删除 `load_generation_graph` 无条件丢弃 tolerant issues 的路径；所有决定顺序和覆盖范围
    的输入都进入观察清单。

- [ ] **Step 5: 运行测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_trash.py" "$RUNTIME/tests/unit/test_conclusions.py" \
    "$RUNTIME/tests/integration/test_trash.py" \
    "$RUNTIME/tests/integration/test_conclusions.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "feat(draft): 在候选根复用回收与结论事务"
  ```

## Task 12：泛化生命周期推进资格并闭合 CLI 编排

**Files:**

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py`

- [ ] **Step 1: 写命令 × 根状态推进决策表**

  对 candidate/registered 参数化：query、所有业务写、dry-run、restore、recover、结论生成
  各推进恰好一次；check、adopt、init、帮助、版本为零次。根缺失、marker 非法/不支持也为
  零次。spy 只断言这个有副作用的编排协议，不复制其他调用图。

- [ ] **Step 2: 写真实到期包行为测试**

  在 candidate 根建立到期包后分别调用 query、普通写、restore、recover 和 check。前四类
  进入各自业务前推进；check 前后树完全一致并报告持久化原状。损坏包阻止需要推进的命令，
  但不得让根资格判定根据残留目录猜测所有权。

- [ ] **Step 3: 运行并确认 registered-only 边界失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_cli.py" \
    "$RUNTIME/tests/integration/test_cli.py" \
    "$RUNTIME/tests/integration/test_trash_lifecycle.py" \
    "$RUNTIME/tests/integration/test_queries.py" -q -k 'advance or lifecycle or readonly'
  ```

- [ ] **Step 4: 重命名并泛化受管根入口**

  - `prepare_registered_command` 改为 `prepare_managed_command`，注释和资格都明确接受两种
    `RootState`。
  - CLI 使用任务 3 已选出的同一个 `ManagedRoot` 决定是否推进，不能再次猜根或只检查
    “marker 可读”。
  - 捕获一次 UTC 时间并传给推进、recoverable 查询和命令业务；前置推进失败不进入业务。
  - 恢复入口能分派普通操作、trash/restore 与 adopt；无锁中段继续按 kind 的规范分类。

- [ ] **Step 5: 运行生命周期测试并提交**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/unit/test_cli.py" \
    "$RUNTIME/tests/integration/test_cli.py" \
    "$RUNTIME/tests/integration/test_trash_lifecycle.py" \
    "$RUNTIME/tests/integration/test_queries.py" -q
  git add plugins/draft/skills/draft/scripts/runtime
  git commit -m "refactor(draft): 对所有受管根推进回收生命周期"
  ```

## Task 13：更新工作流文档、适配层提示与端到端验收

**Files:**

- Modify: `plugins/draft/skills/draft/SKILL.md`
- Modify: `plugins/draft/skills/draft/references/draft-format.md`
- Modify: `plugins/draft/skills/draft/references/migration.md`
- Modify: `docs/design.md`
- Modify: `plugins/draft/scripts/adapter/session_hook.py`
- Modify: `plugins/draft/scripts/tests/unit/test_session_hook.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_candidate_migration.py`

- [ ] **Step 1: 写完整迁移验收测试**

  在真实临时目录中建立独立 source 与 target，记录 source 全树类型/字节摘要，然后执行：

  ```text
  draft init --candidate TARGET
  DRAFT_ROOT=TARGET draft create pages/a.md --title A --status pending
  Agent 直接编辑标题、正文和正文链接
  DRAFT_ROOT=TARGET draft move pages/a.md pages/topic/a.md
  DRAFT_ROOT=TARGET draft check --count-only
  Agent 按同类诊断批量修复
  DRAFT_ROOT=TARGET draft check
  DRAFT_ROOT=TARGET draft adopt
  DRAFT_ROOT=TARGET draft check
  ```

  测试中间至少保留两个无关诊断并连续执行多条普通命令，不在每条命令后 check；还要执行
  relation、trash/restore、generate-conclusions 中的代表路径。最后断言同一 target marker
  已 registered、无迁移专用目录/命令、source 摘要与开始时逐项一致。

- [ ] **Step 2: 写文档/适配层可观察契约测试**

  session hook 生成 `draft check --root <bound-root>`，不再生成位置 root。分发契约读取并
  验证实际 CLI parser/help 或执行 smoke，不用关键词搜索代替行为验证。

- [ ] **Step 3: 运行新增验收并确认旧适配提示契约失败**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/integration/test_candidate_migration.py" \
    plugins/draft/scripts/tests/unit/test_session_hook.py -q
  ```

  预期失败：旧 session hook 仍生成位置 ROOT；迁移验收若另有失败，则按实际跨层断点先修复
  生产契约，不能通过削弱验收绕过。

- [ ] **Step 4: 更新全部用户文档**

  - `SKILL.md`：Agent 先 `init --candidate`，可在命令间直接编辑正文，自选检查点，最后
    adopt；禁止手改路径、机器块、正式关系和父导航。
  - `draft-format.md`：写精确三键 marker、两种 root state、共同 `.draft/` 协议和新查询/
    检查信封。
  - `migration.md`：删除“一次性手工构造无标记候选”流程，改成固定受管候选根逐步 CLI；
    强调来源零写入和没有迁移专用命令。
  - `docs/design.md`：同步根解析、门禁表、生命周期、观察清单、adopt 恢复状态表；明确取代
    provisional 和 registered-only 章节。

- [ ] **Step 5: 运行端到端与适配层定向测试**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest \
    "$RUNTIME/tests/integration/test_candidate_migration.py" \
    "$RUNTIME/tests/integration/test_cli.py" \
    "$RUNTIME/tests/integration/test_distribution_contract.py" -q -m 'not network'
  uv run --project "$RUNTIME" --locked pytest \
    plugins/draft/scripts/tests/unit/test_session_hook.py \
    plugins/draft/scripts/tests/integration/test_distribution_contract.py -q -m 'not network'
  ```

- [ ] **Step 6: 执行旧协议残留审计**

  ```bash
  rg -n 'check --candidate|draft\.provisional\.json|candidate\.already_registered|candidate\.internal_state|prepare_registered_command' \
    plugins/draft docs/design.md
  rg -n 'add_argument\("root"' \
    plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py
  ```

  第一条预期无结果。第二条只允许 `init` 的位置 `ROOT`；如果输出包含其他命令，必须回到
  Task 3 修复，不能把它写成兼容别名。

- [ ] **Step 7: 提交文档和端到端验收**

  ```bash
  git add docs/design.md plugins/draft
  git commit -m "docs(draft): 发布候选根增量迁移工作流"
  ```

## Task 14：完整横切审计与质量门禁

**Files:**

- Verify all files changed in Tasks 1–13
- Modify only files whose failing contract is identified by this audit

- [ ] **Step 1: 沿端到端控制流逐项审计**

  对每个入口追踪：root 权威输入 → marker 状态 → 生命周期副作用资格 → 事实加载 → 计划
  输入观察 → 日志与锁 → mutation → 后置证明 → 失败/恢复 → CLI 可观察结果。特别核对：

  - dry-run 与正式执行共享事实闭包但不写日志；
  - candidate 成功不要求无关诊断消失，registered 仍要求全绿；
  - check 不推进、不恢复、不清理；
  - partial query 的已返回项准确，但不能被写命令当作否定证明；
  - lifecycle 推进和命令业务各只解析同一根，不交叉操作其他根；
  - adopt before/after 的恢复方向与 marker 生效点一致；
  - 来源路径从未进入核心 CLI、日志或删除包。

  若审计发现缺口，先增加能够复现该缺口的最窄单元或集成测试并确认失败，再修改生产代码；
  不允许直接凭代码阅读结论修补后只运行绿灯。

- [ ] **Step 2: 运行核心单元与非网络集成测试**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
  uv run --project "$RUNTIME" --locked \
    pytest "$RUNTIME/tests/integration" -q -m 'not network'
  ```

- [ ] **Step 3: 运行适配层单元与非网络集成测试**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
  uv run --project "$RUNTIME" --locked \
    pytest plugins/draft/scripts/tests/integration -q -m 'not network'
  ```

- [ ] **Step 4: 运行网络集成测试**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked \
    pytest "$RUNTIME/tests/integration" -q -m network
  uv run --project "$RUNTIME" --locked \
    pytest plugins/draft/scripts/tests/integration -q -m network
  ```

- [ ] **Step 5: 运行格式、静态检查和类型检查**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked ruff format --check \
    "$RUNTIME/src" "$RUNTIME/tests"
  uv run --project "$RUNTIME" --locked ruff check \
    "$RUNTIME/src" "$RUNTIME/tests"
  uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
  uv run --project "$RUNTIME" --locked ruff format --check plugins/draft/scripts
  uv run --project "$RUNTIME" --locked ruff check \
    --config plugins/draft/scripts/ruff.toml plugins/draft/scripts
  uv run --project "$RUNTIME" --locked pyright \
    -p plugins/draft/scripts/pyrightconfig.json
  ```

- [ ] **Step 6: 审计规格覆盖与实现残留**

  - 逐节核对批准规格 5–17，不能以“测试全绿”代替覆盖检查。
  - 搜索未完成实现标记、占位异常、旧 marker 和旧命令字面量；逐个证明是无关历史文档，
    或删除生产残留。
  - 检查 `git diff --check`、`git status --short` 和提交边界；不覆盖用户无关改动。

- [ ] **Step 7: 仅在审计确实产生修复时提交收尾变更**

  ```bash
  git add plugins/draft docs/design.md
  git commit -m "test(draft): 闭合候选迁移横切契约"
  ```

  若 Step 1–6 没有产生文件修改，不创建空提交。

---

## 完成定义

- 两种根由同一个精确 marker 模型识别，旧 provisional 和 candidate 专用内部拒绝机器已从
  生产代码与用户文档消失。
- 所有非 init 命令在任意 pwd 下可通过 `--root` 或绝对 `DRAFT_ROOT` 定位根，也能在根内
  通过物理 pwd 发现；歧义和损坏不回退。
- candidate 上每类普通命令只因自己的事实缺失或自有不变量失败而拒绝；成功操作的日志
  与恢复证据足以发现未改输入和目录成员的并发变化。
- check 和 query 的完整性、过滤、分页、计数、退出码和只读性均可由机器协议验证。
- candidate/registered 的回收生命周期完全同形，推进资格按命令表执行。
- adopt 的 marker 替换前后故障都能通过唯一 `recover` 收敛，after 永不回滚。
- 完整迁移验收在一个固定 target 内完成，并证明 source 全树未改变。
- 核心、适配层、网络/非网络测试、Ruff 与 Pyright 全部通过。
