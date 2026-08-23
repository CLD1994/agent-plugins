# Draft 恢复协议闭合实施计划

> **面向执行 Agent：** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 闭合第十轮评审确认的 journal 状态合法性、受管 artifact 所有权、正向/回滚恢复、cleanup evidence 和 restore 双日志恢复协议，使普通进程中断后能够安全收敛或保留可诊断现场。

**架构：** 先把批准的本地协作式故障模型写回规格，再以三个纯决策边界表达协议：`OperationKind × OperationPhase` 状态图、mutation 方向与 artifact 状态表、cleanup evidence 生命周期。真实文件系统代码只负责采集事实和执行决策；公开 `recover`/trash 恢复入口按耐久 phase 选择证据，不在提交后重新要求已获准删除的完整包。

**技术栈：** Python 3.11+、`dataclass`/`StrEnum`、`pathlib`、POSIX 文件系统原语、pytest、Ruff、Pyright strict、uv。

## 全局约束

- 已批准设计规格是公开行为的权威来源；第十轮评审记录只提供缺陷证据和修复边界，不能替代规格。
- 当前安全边界是本地文件系统、一个受根锁协调的 CLI 写者、命令执行期间用户或 Agent 不直接修改目标根；支持普通 I/O 失败与进程中断。
- 本轮不保证非协作进程在相邻系统调用之间抢占名字、NFS 语义、机器掉电后的完整目录链耐久或恶意篡改 `.draft/`；这些状态若被检测到仍必须停止并保留现场。
- 来源路径保持零读取、零扫描、零写入；不得为恢复功能把来源重新带入核心 CLI、日志或删除包。
- 未经所有权证明的既有节点不得覆盖、移动或删除；未知目录成员不得递归删除。
- 恢复只在方向和授权都可证明时自动收敛；歧义状态报告稳定领域错误并保留锁、日志和节点。
- 不拆分 `operations.py`，不做与本轮契约无关的模块重构，不引入平台专用 `renameat2` 或新依赖。
- 新的有限状态字面量必须进入既有枚举或单一权威映射；不得在生产代码和测试中分别维护完整清单。
- 严格执行 TDD：先写能证伪契约的测试并看到预期失败，再写最小实现；不得删除测试、放宽配置或吞掉错误换取绿灯。
- 纯决策函数的测试属于 unit，不访问真实文件系统；真实节点类型、锁、pending/quarantine、进程中断和公开恢复入口属于 integration。
- 开始 Task 1 前记录 `git status --short`。当前用户的 `AGENTS.md` 与 `docs/review-notes/` 属于基线改动，任何任务不得暂存、覆盖或要求它们消失。
- 预计影响文件和建议接口不是封闭约束；若真实数据流要求调整，执行 Agent必须在任务汇报中说明偏差及原因，但不得改变本计划的行为契约。
- 每个任务结束时运行该任务定向测试、核心 unit、Ruff 和 Pyright；只有门禁通过后才按可独立回滚的行为边界提交。

---

## 预计影响范围（非穷尽）

- `docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md`：安全边界和恢复保证的权威规格。
- `docs/superpowers/plans/2026-08-12-draft-candidate-root-incremental-migration.md`：原功能计划的验收边界。
- `docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-9-fix-record.md`：更正“真正原子隔离”等过强完成声明。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`：`OperationPhase` 唯一枚举来源。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`：journal 状态图、节点分类、artifact 所有权、正向/回滚恢复。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`：cleanup evidence 状态分类与收敛。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`：restore 终态解决器按 phase 加载证据。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`：公开恢复入口不再提前加载完整包。
- `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`：只读检查复用 journal/evidence 协议分类。
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`：journal、所有权和 mutation 纯决策矩阵。
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`：真实 pending/quarantine、rollback 与稳定 I/O 错误。
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_package.py`：cleanup evidence 节点事务。
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`：restore R/T/包端到端恢复。
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`：两个公开生命周期恢复入口。
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`：只读内部状态诊断。

## 任务间建议接口

这些名称是为了减少执行 Agent 的自由解释空间；若现有结构要求改名，类型和语义必须保持一致，且在汇报中列出映射。

```python
class OperationPhase(StrEnum):
    ALLOCATING = "allocating"
    PREPARED = "prepared"
    APPLYING = "applying"
    VALIDATING = "validating"
    ROLLING_BACK = "rolling_back"
    TRASH_RETAINED = "trash_retained"
    TRASH_HANDOFF_PENDING = "trash_handoff_pending"
    TRASH_HANDOFF_SUCCEEDED = "trash_handoff_succeeded"
    TRASH_PURGE_COMMITTED = "trash_purge_committed"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


```

建议函数签名为
`validate_journal_kind_phase(journal: Journal, *, path: Path) -> None`、
`classify_node_or_error(path: Path, *, code: str, code_path: Path) -> EntryKind` 和
`artifact_plan_time_absent(observations: ObservationManifest, artifact: Path, *,
planned_created_directories: frozenset[Path]) -> bool`。
后续任务的决策表定义它们的完整返回和失败语义。

---

### Task 1：同步已确认的安全边界

**预计文件：**

- 修改：`docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md`
- 修改：`docs/superpowers/plans/2026-08-12-draft-candidate-root-incremental-migration.md`
- 修改：`docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-9-fix-record.md`

**行为契约：** 文档必须同时说明当前保证、明确延期项和仍不可放宽的硬约束；不能继续一处声称“检测任意 read/commit 间修改”，另一处又承认 link+unlink 微窗口不在模型内。

- [ ] **Step 1：记录工作区基线**

运行：

```bash
git status --short
```

预期：至少看到用户已有的 `AGENTS.md` 和临时 `docs/review-notes/`；把完整输出保存在任务汇报中，后续提交不得夹带这些基线改动。

- [ ] **Step 2：在设计规格增加明确的故障模型小节**

写入以下四组事实，保留来源零写入、路径安全、锁和日志关联、未知内容不删除等原有条款：

| 类别 | 规格结论 |
|---|---|
| 支持 | 本地文件系统；一个受根锁协调的 CLI 写者；普通 I/O 失败；进程在任意已定义耐久步骤后中断 |
| 调用方前提 | 命令执行期间用户或 Agent 不直接修改目标根；发现不符合前提的变化仍保守停止 |
| 延期 | 非协作进程在相邻系统调用间抢占名字；NFS；机器掉电后的全目录链耐久；恶意 `.draft/` 篡改 |
| 不可放宽 | 来源零写入；路径不逃逸；未知内容不递归删除；既有节点不静默覆盖；歧义保留现场 |

- [ ] **Step 3：修订原计划的验收语句**

把“任意外部并发修改”“完全原子”等超出上表的表述改成“在已批准故障模型内”；保留进程中断恢复、摘要复核和锁竞争测试。不得删除与 H1–H4 相关的恢复要求。

- [ ] **Step 4：更正第九轮修复记录**

将“真正原子隔离”更正为“在本地协作式故障模型内，以锚定句柄、摘要和逐步耐久证据保护节点”；明确 link+unlink 的非协作抢占窗口是延期项，不是已经消除的性质。

- [ ] **Step 5：验证文档没有互相矛盾的强声明**

运行：

```bash
rg -n "真正原子|任意外部|read.*commit|系统调用间隙|非协作|故障模型" \
  docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md \
  docs/superpowers/plans/2026-08-12-draft-candidate-root-incremental-migration.md \
  docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-9-fix-record.md
```

预期：每个强保证都位于支持范围内；系统调用微窗口只作为延期边界出现。

- [ ] **Step 6：建议提交边界**

只提交本任务实际修改的三份文档，建议提交信息：

```text
docs(draft): 明确恢复协议的本地协作式故障边界
```

---

### Task 2：按 OperationKind 闭合 journal 状态图、登记 rollback 意图并稳定节点分类错误

**预计文件：**

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**接口：**

- Consumes: `OperationKind`、`OperationPhase`、`Journal`、`EntryKind`、`DraftError`。
- Produces: 单一 `kind × phase/edge` 权威表；`OperationPhase.ROLLING_BACK`；`validate_journal_kind_phase`；调用方指定错误码的 `classify_node_or_error`。

**状态图硬契约：**

| kind 族 | 允许的 phase/边 |
|---|---|
| CREATE/MOVE/PROMOTE/COLLAPSE/STATUS_SET/RELATION_ADD/RELATION_REMOVE/GENERATE_CONCLUSIONS | allocating→prepared→applying→validating→completed；allocating 可直接 rolled_back；prepared/applying/validating 只能经 Task 3 的 rolling_back 到 rolled_back |
| ADOPT | 通用正向阶段；额外允许 prepared/applying→completed 的 marker 已生效恢复；不允许 trash 生命周期阶段 |
| TRASH | 通用写阶段；validating→trash_retained；retained→handoff_pending 或 rolled_back；handoff_pending→purge_committed/handoff_succeeded；两个 committed phase→completed |
| RESTORE | 通用正向与 Task 3 回滚阶段；不允许 retained/handoff/purge |
| RECOVER | 不允许形成持久 journal；只允许作为无操作 `OperationResult.kind` |

- [ ] **Step 1：写 kind × phase 和 kind × edge 的失败单元矩阵**

在 `test_operations.py` 添加参数化测试，至少明确断言：

```python
@pytest.mark.parametrize(
    ("kind", "old_phase", "new_phase"),
    [
        (OperationKind.CREATE, OperationPhase.PREPARED, OperationPhase.COMPLETED),
        (OperationKind.CREATE, OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationKind.ADOPT, OperationPhase.VALIDATING, OperationPhase.TRASH_RETAINED),
        (OperationKind.RESTORE, OperationPhase.TRASH_RETAINED, OperationPhase.ROLLED_BACK),
    ],
)
def test_journal_transition_rejects_cross_kind_edge(kind, old_phase, new_phase):
    frozen = (_m(),)
    old = _j(old_phase, kind=kind, mutations=frozen)
    new = _j(new_phase, kind=kind, mutations=frozen)
    with pytest.raises(DraftError, match="operation.journal_corrupt"):
        validate_journal_transition(old, new, path=Path("journal.json"))
```

复用现有 `_j`/`_m` helper，并确保 prepared/applying/validating 始终携带真实非空 mutation，
不得复制零 mutation 中段 fixture。

- [ ] **Step 2：运行 RED 测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_operations.py" -q -k 'cross_kind or kind_phase'
```

预期：CREATE 的 adopt/trash 边当前被接受，新增测试失败。

- [ ] **Step 3：实现 kind-scoped 单一权威表**

在 `models.py` 增加 `OperationPhase.ROLLING_BACK`。在 `operations.py` 用
`Mapping[OperationKind, frozenset[OperationPhase]]` 和
`Mapping[OperationKind, frozenset[tuple[OperationPhase, OperationPhase]]]` 定义合法 phase 和 edge；
`_parse_journal` 构造 `Journal` 前验证 `(kind, phase)`，`validate_journal_transition` 再验证
`(kind, old_phase, new_phase)`。`_JOURNAL_METADATA_DELTA` 仍约束字段变化，但不能替代 kind
状态图。

- [ ] **Step 4：写 rollback 方向意图的失败测试**

在 unit 层断言 generic kind 只能经 `ROLLING_BACK` 到 `ROLLED_BACK`；在 integration 层给
`_rollback` 的第一次 `_apply_write`/`_apply_delete` 注入异常，重读 journal 必须是
`ROLLING_BACK`，目标仍处于开始回滚前的可分类状态。

- [ ] **Step 5：运行 rollback intent 的 RED 测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_operations.py" \
  "$RUNTIME/tests/integration/test_operations.py" -q \
  -k 'rolling_back or rollback_intent'
```

预期：当前没有 `ROLLING_BACK` phase，新增测试失败。

- [ ] **Step 6：在首次 rollback mutation 前耐久写入方向意图**

`_rollback` 在检查全部目标可分类之后、执行第一个目标 mutation 之前，把 journal 写为
`ROLLING_BACK`。状态图只允许：

```text
PREPARED -> ROLLING_BACK
APPLYING -> ROLLING_BACK
VALIDATING -> ROLLING_BACK
ROLLING_BACK -> ROLLING_BACK
ROLLING_BACK -> ROLLED_BACK
```

删除 generic kind 的 `PREPARED/APPLYING/VALIDATING -> ROLLED_BACK` 直达边；保留
`ALLOCATING -> ROLLED_BACK` 和 TRASH `TRASH_RETAINED -> ROLLED_BACK`，因为这两类路径不执行
mutation 回滚。把 `ROLLING_BACK` 纳入 checker 的中段状态和 `_recover_locked` 可识别集合；
Task 3 才负责让其中断现场自动收敛，本任务至少保证方向已经耐久且正常 rollback 全流程通过。

- [ ] **Step 7：写节点分类错误的失败集成测试**

分别在 lock、journal pending、cleanup evidence/package 的调用边界注入 `os.lstat` 的
`PermissionError` 与一般 `OSError`，断言：

```python
with pytest.raises(DraftError) as caught:
    read_lock(root)
assert caught.value.code == "operation.journal_corrupt"
assert isinstance(caught.value.__cause__, PermissionError)
assert lock_path.exists()
```

各入口使用自己已有的稳定错误码；不得强制所有消费者都返回 `journal_corrupt`。

- [ ] **Step 8：运行 RED 测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_operations.py" \
  "$RUNTIME/tests/integration/test_trash_package.py" -q \
  -k 'lstat or classify_error'
```

预期：至少 `read_lock` 泄漏原始 `PermissionError`。

- [ ] **Step 9：实现调用方拥有错误语义的分类边界**

实现等价于以下精确语义的 helper：

```python
def classify_node_or_error(path: Path, *, code: str, code_path: Path) -> EntryKind:
    try:
        return classify_node(path)
    except OSError as exc:
        raise DraftError(code, code_path) from exc
```

审计 `read_lock`、`_read_optional_journal`、`resolve_journal_pending`、cleanup evidence/package
分类，使同一入口的 classify/read/decode 共享领域错误码。保留 `classify_node` 作为低层事实
函数时，必须让其原始 OSError 语义只在明确包装后的调用边界出现。

- [ ] **Step 10：运行 GREEN 与任务门禁**

运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_operations.py" \
  "$RUNTIME/tests/integration/test_checker.py" \
  "$RUNTIME/tests/integration/test_trash_package.py" -q
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：全部通过。

- [ ] **Step 11：建议提交边界**

提交本任务实际生产文件与测试，建议提交信息：

```text
fix(draft): 按操作种类约束日志阶段与回滚意图
fix(draft): 稳定持久节点分类错误
```

---

### Task 3：建立 artifact 所有权证明并显式记录 rollback 方向

**预计文件：**

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`

**接口：**

- Consumes: Task 2 的 kind-scoped 状态图和分类边界；现有 `ObservationManifest`、`JournalMutation`、`_plan_time_missing_index`。
- Produces: 计划时缺失证明查询；方向感知的 mutation artifact 纯决策与 I/O 执行边界。

建议的纯接口为
`mutation_artifact_actions(*, direction: MutationDirection, role: MutationRole,
target: ArtifactState, pending: ArtifactState, quarantine: ArtifactState,
pending_owned: bool, quarantine_owned: bool) -> Sequence[MutationRecoveryAction]`。
其中 `MutationDirection` 只有 `FORWARD/ROLLBACK`，`MutationRole` 只有
`NEW_WRITE/REWRITE_WRITE/DELETE`，`ArtifactState` 只有
`MISSING/BEFORE/AFTER/WRONG/NON_FILE`；`MutationRecoveryAction` 只允许表达按序执行的
`PUBLISH_PENDING/PUBLISH_STAGED/PUBLISH_ORIGINAL/CONSUME_PENDING/CONSUME_QUARANTINE/
RETRY_APPLY/DONE/EXTERNAL`。如果按现有结构改名，成员集合和决策语义不得变化。

**所有权硬契约：** artifact 路径可推导且摘要匹配仍不足以获得所有权。只有 journal 观察能证明该精确路径计划时不存在，恢复器才可消费受管页面旁的 pending/quarantine/temp。

**方向状态表：** 先把活节点归类为 `MISSING/BEFORE/AFTER/WRONG/NON_FILE`。下表列出全部允许模式；没有列出的组合一律 `operation.external_change` 并保持节点不变。

| 方向/角色 | 允许现场 | 动作 |
|---|---|---|
| forward new WRITE | target=MISSING, pending=MISSING/AFTER, quarantine=MISSING | 从 staged 正常发布或发布 pending；target=AFTER 时只允许 pending=MISSING/AFTER，后者消费 pending |
| forward rewrite WRITE | target=BEFORE 且无 artifact；target=BEFORE+quarantine=BEFORE；target=MISSING+quarantine=BEFORE 且 pending=MISSING/AFTER；target=AFTER 且 quarantine=BEFORE/MISSING、pending=MISSING/AFTER | 按节点事务继续发布 after，再消费精确 pending/quarantine；不得改变方向 |
| forward DELETE | target=BEFORE、target=BEFORE+quarantine=BEFORE、target=MISSING+quarantine=BEFORE、target=MISSING 且无 artifact | 继续或完成删除；pending 必须 MISSING |
| rollback new WRITE | target=AFTER、target=AFTER+quarantine=AFTER、target=MISSING+quarantine=AFTER、target=MISSING 且无 artifact | 继续或完成删除 after；pending 必须 MISSING |
| rollback rewrite WRITE | 把 forward rewrite 表中的 BEFORE/AFTER 互换；quarantine 保存 AFTER，pending 保存 BEFORE | 从 original 发布 before 并消费 artifact |
| rollback DELETE | target=MISSING/BEFORE；pending=MISSING/BEFORE；quarantine 必须 MISSING | 从 original 发布 before，或消费发布后 pending |

- [ ] **Step 1：写计划时缺失证明的纯单元测试**

覆盖：精确 MISSING、父目录成员不含名字、缺失祖先且该目录属于本操作计划创建闭包、父目录
明确含名字、没有观察、父为 FILE/SYMLINK/OTHER、缺失祖先但本操作不负责创建。后 5 类不得
被当作本操作所有；“路径计划时不可能存在”与“本操作获准在这里创建协议节点”不是同一事实。

```python
def test_plan_time_missing_does_not_infer_absence_without_evidence():
    assert artifact_plan_time_absent(
        EMPTY_OBSERVATIONS,
        Path("pages/a.md.pending"),
        planned_created_directories=frozenset(),
    ) is False
```

- [ ] **Step 2：写既有 artifact 不得被消费的失败集成矩阵**

对 pending、quarantine、managed temp，覆盖 exact/wrong 普通文件、目录、live/dangling link；
至少覆盖 new WRITE、rewrite WRITE、DELETE。每个用例先在计划前创建 artifact，再执行命令和
`recover`，最后断言原路径、类型和字节均未改变。

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_operations.py" \
  "$RUNTIME/tests/integration/test_structure.py" -q \
  -k 'preexisting_artifact or artifact_ownership'
```

预期：exact pending/quarantine 当前会在 recover 中被消费，测试失败。

- [ ] **Step 3：在受管 artifact 恢复前强制所有权证明**

从 journal mutations 与内部操作路径推导本操作获准创建的目录闭包，再用
`artifact_plan_time_absent` 查询精确 artifact。在
`_recover_apply_transaction_artifacts`、`_recover_managed_temp_quarantines` 和
`_clean_owned_temp_artifacts` 处理实际存在的 artifact 前要求
该查询为真。没有正向缺失与目录创建授权时报告
`operation.external_change`；不得调用通用 pending/quarantine helper 先移动节点再复核观察。

- [ ] **Step 4：写 rollback 方向的纯单元决策表**

为上表每个允许组合写独立预期，为每类 `WRONG/NON_FILE`、未授权 artifact 和表外组合写
拒绝预期。测试直接写预期 action，不调用生产 helper 计算 expected。

- [ ] **Step 5：运行 RED 决策测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_operations.py" -q -k 'mutation_artifact or rollback_direction'
```

预期：当前没有方向感知模型，新增测试失败。

- [ ] **Step 6：实现方向感知 artifact 决策和执行**

纯函数输入必须包含 `direction`、mutation role、target/pending/quarantine 的类型与摘要分类、
pending/quarantine 的所有权布尔值；输出为有序 action tuple 或 `EXTERNAL`。I/O 层只按 action
执行锚定发布/消费，所有摘要来源分别固定为 staged=after、original=before，不在 helper 中
按 `None` 猜方向。

- [ ] **Step 7：写每个真实命名点的 rollback 中断集成测试**

对 new/rewrite/delete，在 quarantine link 后、原名删除后、pending 写完后、pending 发布后、
quarantine 消费前注入异常或子进程中断。随后调用公开 `recover`，断言：

- journal 在中断后为 `rolling_back`；
- 目标最终为 before；
- 只消费计划时证明缺失且摘要正确的 artifact；
- wrong/type/link/歧义现场保留锁、日志和节点；
- 不会把 rollback 中断重新前向提交。

- [ ] **Step 8：运行 GREEN 与任务门禁**

运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_operations.py" \
  "$RUNTIME/tests/integration/test_structure.py" -q
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：全部通过。

- [ ] **Step 9：建议提交边界**

本任务可按“所有权证明”和“rollback 方向”拆成两个可独立绿灯的提交；不得把其中一个留在
红灯状态。建议提交信息：

```text
fix(draft): 证明受管事务 artifact 所有权
fix(draft): 显式恢复被中断的 mutation 回滚
```

---

### Task 4：闭合 cleanup evidence 的 final/pending/quarantine 生命周期

**预计文件：**

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py` 或新增 `tests/unit/test_trash_package.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_package.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`

**接口：**

- Consumes: 已提交 T journal、`CleanupAuthorization`、`CleanupEvidence` schema、Task 2 分类边界。
- Produces: 只读 evidence artifact 分类；纯决策函数；cleaner 使用的有副作用收敛入口；checker 使用的只读验证入口。

建议的纯接口为
`cleanup_evidence_actions(*, intent: CleanupEvidenceIntent, final: EvidenceNodeState,
pending: EvidenceNodeState, quarantine: EvidenceNodeState,
final_pending_same_node: bool, final_quarantine_same_node: bool)
-> Sequence[CleanupEvidenceAction]`。`CleanupEvidenceIntent` 只有 `KEEP/REMOVE`；
`EvidenceNodeState` 只有 `MISSING/VALID/INVALID/NON_FILE`；action 只有
`PUBLISH_PENDING/CONSUME_PENDING/RESTORE_QUARANTINE/CONSUME_FINAL/
CONSUME_QUARANTINE/READY/DONE/CORRUPT`。

**完整决策规则：** 把每个 evidence 名分类为 `MISSING/VALID/INVALID/NON_FILE`；测试再把
`NON_FILE` 展开为 directory/live link/dangling link/other。任一 `INVALID/NON_FILE` 都报
`trash.package_corrupt` 并在首次副作用前停止。

| cleanup 意图 | final/pending/quarantine 合法组合 | 结果 |
|---|---|---|
| KEEP（包 exact/partial，证据必须保留） | VALID/MISSING/MISSING | ready |
| KEEP | MISSING/VALID/MISSING | 发布 pending 为 final |
| KEEP | VALID/VALID/MISSING，且两个节点/字节与同一 T 授权一致 | 消费 pending，保留 final |
| KEEP | MISSING/MISSING/VALID | 恢复 quarantine 为 final |
| KEEP | VALID/MISSING/VALID，且为同一次隔离的精确节点 | 消费 stale quarantine，保留 final |
| REMOVE（包已缺失，证据必须消失） | MISSING/MISSING/MISSING | done |
| REMOVE | 只有一个 VALID 名 | 验证后消费该名 |
| REMOVE | final+pending VALID 且证明为同一次发布 | 先消费 pending，再消费 final |
| REMOVE | final+quarantine VALID 且证明为同一次隔离 | 先完成 final 删除，再消费 quarantine |

其余多名组合无法由一个已定义命名步骤产生，按 corrupt 保留，不得“逐个删到干净”。

- [ ] **Step 1：写纯决策表单元测试**

测试 KEEP/REMOVE 的每个允许行、每个单节点 INVALID/NON_FILE、pending+quarantine、三名并存、
字节正确但 operation_id/cleanup_phase 不匹配。预期直接写 action 或 corrupt。

- [ ] **Step 2：运行 RED 单元测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit" -q -k 'cleanup_evidence_decision'
```

预期：当前没有完整 final/pending/quarantine 模型，新增测试失败。

- [ ] **Step 3：写真实发布与消费中断的失败集成测试**

至少添加以下用例：

```text
final missing + valid pending -> recover publishes and continues
final missing + invalid/type/link pending -> corrupt, all nodes unchanged
package exact + final missing + valid quarantine -> restore final
package missing + final missing + valid quarantine -> consume quarantine
package missing + final+quarantine exact -> finish delete, no orphan
checker reports invalid final/pending/quarantine and never mutates them
```

故障注入点必须落在 evidence pending 已同步、final 已发布、final 已隔离、final 已删除、
quarantine 消费前这些真实 I/O 接缝。

- [ ] **Step 4：运行 RED 集成测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash_package.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q \
  -k 'cleanup_evidence and (pending or quarantine or checker)'
```

预期：valid pending 被误判损坏，quarantine 可能在成功返回后遗留。

- [ ] **Step 5：实现共享的 evidence 分类与收敛边界**

锚定同一 operation directory，分别读取 final/pending/quarantine 的节点类型、inode 和 bytes；
每份 bytes 都调用 `parse_cleanup_evidence(data, journal, code_path)`。先完整分类全部名字，再执行
决策动作。KEEP/REMOVE 意图由权威包状态和已提交 T 授权提供，不能从“哪个文件存在”反推。

`clean_authoritative_package` 在任何包叶删除前把 evidence 收敛为 KEEP-ready；包已删除后把
意图设为 REMOVE。checker 复用只读分类与 pure decision，但不得调用发布、恢复或消费动作。

- [ ] **Step 6：删除单点补丁路径并审计 checker**

移除 `_recover_cleanup_evidence_pending` 只检查 final leaf 的逻辑；确保允许条目和 schema 检查
同时覆盖 final/pending/quarantine。terminal operation directory 仍不允许任何 evidence 残留。

- [ ] **Step 7：运行 GREEN 与任务门禁**

运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash_package.py" \
  "$RUNTIME/tests/integration/test_checker.py" -q
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：全部通过。

- [ ] **Step 8：建议提交边界**

建议提交信息：

```text
fix(draft): 闭合清理证据发布与消费恢复
```

---

### Task 5：按 R/T 提交状态恢复 restore，不提前要求完整权威包

**预计文件：**

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**接口：**

- Consumes: Task 4 可幂等恢复的 `clean_authoritative_package`；R/T journal 与双向关联验证。
- Produces: phase-first 的 restore 决策；只在提交前分支加载 `VerifiedTrashPackage` 的公开恢复控制流。

建议的纯接口为
`restore_recovery_decision(*, restore_phase: OperationPhase, trash_phase: OperationPhase,
links_match: bool, proven_unapplied: bool) -> RestoreRecoveryAction`，返回值只有
`VERIFY_AND_COMMIT_CLEANUP/RESUME_COMMITTED_CLEANUP/ABANDON_UNAPPLIED/INCONSISTENT`。

**恢复决策表：**

| R phase | T phase/关联 | 包要求 | 结果 |
|---|---|---|---|
| COMPLETED | TRASH_RETAINED 且双向关联 | 必须完整 | 复核 restore 后置状态；写 T=ROLLED_BACK；进入 committed cleanup |
| COMPLETED | ROLLED_BACK 且双向关联/锁匹配 | 可 exact/partial/missing | 不再调用完整包 loader；直接凭提交状态和 Task 4 evidence 继续清理 |
| ROLLED_BACK | T 仍被同一 R 认领 | 不要求完整包 | 解关联 T；清理 R 自有恢复材料；释放锁 |
| ALLOCATING/PREPARED | 可证明未应用且 T 被同一 R 认领 | 只读取未应用证明所需事实 | 写 R=ROLLED_BACK、解关联、清理 R 材料、释放锁 |
| APPLYING/VALIDATING/ROLLING_BACK 或关联不匹配 | 任意 | 不猜测 | `operation.inconsistent_state`，保留锁、R/T、包/evidence |

- [ ] **Step 1：写纯 restore phase 决策单元测试**

输入只包含 R phase、T phase、双向关联和“可证明未应用”布尔值；输出明确为
`VERIFY_AND_COMMIT_CLEANUP/RESUME_COMMITTED_CLEANUP/ABANDON_UNAPPLIED/INCONSISTENT`。
不要让纯函数读取 package 状态。

- [ ] **Step 2：运行 RED 单元测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/unit/test_trash.py" -q -k 'restore_recovery_decision'
```

预期：当前没有 phase-first 决策边界，新增测试失败。

- [ ] **Step 3：写 committed cleanup 的公开入口失败测试**

通过真实 restore 先得到 R=COMPLETED、T=ROLLED_BACK，再在以下点注入中断：

```text
cleanup evidence 已发布、尚未删包叶
已删 package.json，payload 仍在
包目录已删除，cleanup evidence 仍在
cleanup evidence 已清，restore 锁仍在
```

每个现场分别调用 `recover_draft` 和 `recover_interrupted_operations`。预期恢复完成、T/R/锁/
evidence 收敛；不得因缺失 `package.json` 报 `trash.package_corrupt`。

- [ ] **Step 4：写提交前完整包仍为硬要求的反例**

R=COMPLETED、T=TRASH_RETAINED 时损坏 package manifest/payload，两个公开恢复入口都必须报
`trash.package_corrupt` 并保留 R/T 关联、锁和包现场。此测试防止“延迟加载”被误写成“不再验证”。

- [ ] **Step 5：运行 RED 集成测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q \
  -k 'committed_cleanup or phase_first_restore'
```

预期：committed cleanup 的 partial/missing package 当前在 resolver 前失败。

- [ ] **Step 6：重排恢复控制流**

`recover_draft` 与 `recover_interrupted_operations` 读取锁、R、T 并验证 identity 后，先调用纯
restore 决策。只有 `VERIFY_AND_COMMIT_CLEANUP` 分支调用 `load_verified_package` 和
`verify_restore_after_state`；`RESUME_COMMITTED_CLEANUP` 直接重读 cleanup authorization 并
调用 Task 4 cleaner。

调整 `resolve_restore_execution`：不得继续要求所有分支传入必填 `VerifiedTrashPackage`。
建议让 resolver 按 action 接收 `package: VerifiedTrashPackage | None`，并在唯一需要完整包的
分支显式拒绝 `None`；也可以把 loader 作为只在该分支调用的协作者注入。

- [ ] **Step 7：核对失败后的证据和锁顺序**

对每个分支确认：T=ROLLED_BACK 耐久后才能永久删包；cleaner 完成后才能清 R 恢复材料；所有
清理成功后才释放 R 锁。任一步失败都保留下一次 recover 需要的 journal/evidence/lock。

- [ ] **Step 8：运行 GREEN 与任务门禁**

运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_trash.py" \
  "$RUNTIME/tests/integration/test_trash_lifecycle.py" \
  "$RUNTIME/tests/integration/test_trash_package.py" -q
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
```

预期：全部通过。

- [ ] **Step 9：建议提交边界**

建议提交信息：

```text
fix(draft): 按双日志提交状态恢复 restore 清理
```

---

### Task 6：修正 fixture、执行同类入口审计并运行完整门禁

**预计文件：**

- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- 修改：`docs/review-notes/2026-08-18-draft-candidate-root-incremental-migration-round-9-fix-record.md`
- Modify only if audit identifies a violated contract: Task 2–5 涉及的生产文件和测试。

**行为契约：** 不增加新功能；证明所有同类 pending/quarantine、journal phase、restore/evidence 入口均进入前述权威模型，并如实汇报未运行门禁。

- [ ] **Step 1：修正零 mutation 中段 journal fixture**

把 `test_operations.py` 中 phase regression 和 identity rewrite 的局部 JSON helper 改成共享的
协议合法非空 mutation builder。测试仍留在 integration，因为它验证真实 pending 文件发布；
继续断言 old/pending 原始 bytes 都不变。

- [ ] **Step 2：运行 fixture 定向测试**

运行：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration/test_operations.py" -q \
  -k 'phase_regression or identity_rewrite'
```

预期：通过，且 fixture 的 prepared/applying 含真实非空 mutation。

- [ ] **Step 3：沿同类入口建立审计表**

逐个列出生产代码中所有 `.pending`、`.quarantine`、`OperationPhase` 写入、
`load_verified_package`、`classify_node` 调用点，并为每项记录：

```text
入口 | artifact/phase 角色 | 权威授权 | 计划时所有权 | 方向 | 提交点 | 稳定错误码 | 公开 recover 入口 | 对应测试
```

任何缺少一列的调用点先判断是否违反 Task 2–5 契约；成立则先写 RED 测试再修复。不得只用文本
出现与否证明依赖限制，但文本搜索可用于发现候选调用点，最终要追踪真实调用关系。

- [ ] **Step 4：验证第十轮每条发现都有端到端测试**

检查以下映射完整：

| 发现 | 必须存在的证明 |
|---|---|
| H1 | evidence pending/quarantine 纯表 + 真实 I/O + checker 只读 |
| H2 | 两个公开 restore 恢复入口的 committed cleanup crash 矩阵 |
| H3 | forward/rollback × new/rewrite/delete artifact crash 矩阵 |
| H4 | 计划前既有 exact/wrong/type/link artifact 完全不变 |
| M1 | kind × phase 与 kind × edge 单元矩阵 + 合法 writer 集成正例 |
| M2 | lock/journal/evidence/package lstat 错误稳定映射 |
| L1 | 非空 mutation integration fixture |

- [ ] **Step 5：运行完整离线测试**

运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest \
  "$REPO_ROOT/plugins/draft/scripts/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest \
  "$REPO_ROOT/plugins/draft/scripts/tests/integration" -q -m 'not network'
```

预期：全部通过；报告准确写出 passed/deselected 数量。

- [ ] **Step 6：运行格式、静态和类型门禁**

运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked ruff format --check \
  "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
bash "$REPO_ROOT/plugins/draft/scripts/check-adapters.sh"
```

预期：全部通过。

- [ ] **Step 7：如实处理网络测试**

若运行：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
RUNTIME="$REPO_ROOT/plugins/draft/skills/draft/scripts/runtime"
uv run --project "$RUNTIME" --locked pytest \
  "$RUNTIME/tests/integration" -q -m network
uv run --project "$RUNTIME" --locked pytest \
  "$REPO_ROOT/plugins/draft/scripts/tests/integration" -q -m network
```

报告真实结果。若未运行，明确写“网络测试未运行”，不得使用“全量门禁全部通过”。

- [ ] **Step 8：核对工作区与提交边界**

运行：

```bash
git status --short
git diff --check
```

预期：用户基线改动仍原样存在；没有临时脚本、缓存、测试现场或无关文件。按实际行为边界
提交本任务修正，不暂存整个 `docs/` 或仓库根目录。

- [ ] **Step 9：提交最终修正并准备交接材料**

建议提交信息：

```text
test(draft): 闭合恢复协议夹具与横切验收
```

交接报告必须包含：

- 每条发现对应的提交、生产入口和回归测试；
- Task 3 三维 artifact 表、Task 4 evidence 表、Task 5 R/T 表的最终实现名称；
- 同类入口审计表和发现的额外缺口；
- 完整离线门禁、网络门禁真实状态、Pyright/Ruff 结果；
- 与本计划建议接口或预计文件范围的全部偏差及原因；
- `git status --short`，证明用户基线改动未被纳入提交。

## 禁止的实现捷径

- 不得只修 `trash_package.py` 中误检查 final leaf 的单行条件而遗漏 evidence quarantine 和 checker。
- 不得继续以“确定性文件名 + 摘要相同”代替 artifact 所有权证明。
- 不得在判断 R/T phase 前无条件调用 `load_verified_package`。
- 不得把 rollback artifact 统一解释为 forward artifact，或把合法回滚中断报成外部修改。
- 不得让 parser 只检查 phase 枚举值而不检查 `OperationKind` 拥有该 phase。
- 不得吞掉 `OSError`、降低 checker/parser 严格度、删除测试或改变错误预期换取绿灯。
- 不得在未获新批准的情况下扩张到平台专用 rename、敌对并发或掉电耐久工程。
- 不得借本轮修改拆分 `operations.py`、移动大量无关代码或引入新依赖。

## 完成判据

只有同时满足以下条件才能向评审者报告完成：

1. 规格、原计划和实现使用同一安全边界措辞。
2. H1–H4、M1–M2、L1 的 RED 测试均曾在修复前按预期失败，修复后通过。
3. journal、mutation artifact、cleanup evidence、restore R/T 四个状态模型均有独立纯单元测试。
4. 所有真实文件系统与进程中断行为由 integration 测试覆盖，公开恢复入口至少各有一个端到端正例和拒绝例。
5. 完整离线测试、Ruff、Pyright 和适配层权威门禁通过；网络测试状态如实报告。
6. 用户已有 `AGENTS.md` 和 `docs/review-notes/` 基线没有被覆盖或夹带提交。
