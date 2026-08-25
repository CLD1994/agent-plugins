# Draft Recover 生命周期边界修复实施计划

> **执行 Agent：** 必须使用 `superpowers:subagent-driven-development`（推荐）或
> `superpowers:executing-plans` 按任务实施；步骤使用复选框（`- [ ]`）跟踪。

**目标：** 使公开 `draft recover` 在所有锁内 trash 状态下只收敛已经存在的 recovery-pending operation，不从 `trash_retained` 发起到期移交或最终清理。

**架构：** `recover_interrupted_operations` 是公开 recover 的应用边界，`advance_trash_lifecycle` 是允许的普通受管命令前置生命周期边界。`_recover_locked_trash` 按 journal phase 显式分派恢复动作；删除同时组合恢复与到期推进的旧 `recover_draft` 边界，测试分别调用两个真实入口。

**技术栈：** Python 3.12、pytest、uv、Ruff、Pyright

**规格：** `.scratch/draft-durable-link-index-maintenance/spec.md` §13.5、§14；`docs/design.md` §13.5

## 全局约束

- `recover ROOT` 只收敛 recovery-pending operation，不推进 `trash_retained` 到期、系统回收站移交或最终清理。
- 到期生命周期只由允许的普通受管命令在业务逻辑前置阶段调用 `advance_trash_lifecycle` 推进。
- 已经耐久进入 `trash_handoff_pending` 的 operation 属于恢复现场；recover 可以继续该次已经开始的移交。
- `trash_handoff_succeeded` 与 `trash_purge_committed` 是已提交清理状态；recover 只能完成清理，不能再次调用系统回收站适配器。
- 不改变 journal schema、合法 phase/transition、错误码、退出码、revision 或链接索引协议。
- 不保留把 recovery 与生命周期推进组合在一个函数中的兼容 wrapper，也不通过伪造 `now` 绕过状态分派。
- 根 `AGENTS.md` 的用户修改不得修改、暂存或提交；network integration 与 Windows 验证不在本计划保障范围内，最终必须明确报告未运行。

## 锁内 Trash 恢复决策表

| journal phase | recover 动作 | 允许的外部副作用 | 结果 |
| --- | --- | --- | --- |
| `allocating`、`prepared`、`applying`、`validating`、`rolling_back` | 委托 `operations.recover` | 只按原 mutation journal 前向完成或回滚 | 原 operation 的合法终态 |
| `link_index_updating`、`link_index_published`、`link_index_rebuild_required` | 委托 `operations.recover` | 只收敛同一 revision 的索引恢复 | `trash_retained` |
| `trash_retained` | 解除同 operation 的崩溃残留锁 | 不调用 handoff，不写 phase，不删权威包 | 仍为 `trash_retained` |
| `trash_handoff_pending` | 调用持锁生命周期恢复 | 可以继续已经耐久开始的 handoff/purge 尝试 | pending 或合法清理终态 |
| `trash_handoff_succeeded`、`trash_purge_committed` | 完成已提交清理 | 不再次调用 handoff | `completed` |
| `completed`、`rolled_back` | 委托 `operations.recover` 的终态校验与清理 | 只清当前 operation 材料并解锁 | 原终态 |

非法 kind/phase、损坏 journal、错配锁或异常节点继续按既有稳定错误保留现场；不得解释为 `trash_retained` 或成功。

---

### 任务 1：锁定并修复 `trash_retained` 恢复分区

**文件：**
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:545-595`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py:658-696`

**接口：**
- 输入：`recover_interrupted_operations(root, *, now=None, handoff=None) -> tuple[LifecycleResult, ...]`、`operations.recover(root) -> OperationResult`
- 输出：`_recover_locked_trash` 对上表全部合法 phase 的显式恢复路由

- [ ] **步骤 1：把到期 retained 锁恢复用例改成可证伪的 RED**

  将 `test_locked_retained_recovery_advances_and_releases_lock` 改为直接调用 `recover_interrupted_operations(root, now=EXPIRY, handoff=...)`，并断言：handoff 调用列表为空、journal 仍为 `trash_retained`、权威包仍存在、锁已释放。当前实现会调用 handoff 并进入 `completed`，因此必须失败。

- [ ] **步骤 2：运行单个 RED 用例**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
    pytest plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py::test_locked_retained_recovery_does_not_advance_lifecycle -q
  ```

  预期：FAIL，至少一个反例断言证明 handoff 被调用、phase 被推进或包被删除；不得因导入/拼写错误产生伪 RED。

- [ ] **步骤 3：按决策表修复 `_recover_locked_trash`**

  保留 succeeded/purge committed 的既有已提交清理分支。对 `TRASH_RETAINED` 显式 `release_lock(root, operation_id)` 后返回 `None`；对 `TRASH_HANDOFF_PENDING` 才调用 `_advance_one_locked`；其余通过 `operations.recover(root)` 恢复原 operation，再按既有 `TrashResult` 字段包装结果。不要比较当前时间来选择 recovery 责任，也不要让 retained 分支读取或验证 handoff 副本。

- [ ] **步骤 4：增加普通写入 phase 的公共恢复反例**

  在现有真实 trash crash fixture 上至少覆盖一个 `prepared/applying/validating` phase，经 `recover_interrupted_operations` 后必须按原 mutation journal 收敛，而不是仅释放锁。若现有 `test_trash.py` 用例已经从该入口覆盖同一分区，保留并引用它，不复制 fixture。

- [ ] **步骤 5：运行恢复边界定向测试**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py -q
  ```

  预期：全部 PASS。

- [ ] **步骤 6：提交行为修复**

  ```bash
  git add plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py
  git commit -m "fix(draft): keep recover out of retained trash lifecycle"
  ```

### 任务 2：删除旧的组合边界并迁移测试

**文件：**
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:768-835,886-897`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**接口：**
- 输入：任务 1 的 `_recover_locked_trash` phase 路由
- 输出：唯一 recovery 应用边界 `recover_interrupted_operations`；唯一到期推进边界 `advance_trash_lifecycle`

- [ ] **步骤 1：删除 `recover_draft` 生产函数**

  完整删除同时执行“崩溃恢复 + 无锁 retained/pending 批量推进”的函数。更新 `recover_interrupted_operations` docstring，直接说明它处理锁内恢复和无锁 recovery-pending operation；不得再引用旧函数。

- [ ] **步骤 2：迁移恢复类测试**

  `test_lifecycle.py` 与 `test_trash.py` 中验证 adopt/trash/restore crash recovery、错误保留和幂等性的调用统一改为 `recover_interrupted_operations`。原来同时参数化 `recover_draft` 与 `recover_interrupted_operations` 的测试删除重复轴，只保留真实公开 recovery 边界；测试名和 docstring 不再称两个公开入口。

- [ ] **步骤 3：迁移生命周期类测试**

  `test_trash_lifecycle.py` 中验证到期、节流、handoff、purge、批量首错停止的测试统一调用 `advance_trash_lifecycle`；验证锁内 crash 恢复、提交后清理和 restore 协调的测试调用 `recover_interrupted_operations`。不得创建测试 helper 再把两个入口串联成旧组合行为。

- [ ] **步骤 4：更新模块身份契约**

  `test_identity.py` 不再断言 `recover_draft` 存在，改为分别断言 `advance_trash_lifecycle` 和 `recover_interrupted_operations` 存在，明确两个责任边界。

- [ ] **步骤 5：证明旧边界和旧表述已消失**

  ```bash
  rg -n "recover_draft|check.*严格只读|recover.*推进.*retained trash|recover.*推进.*回收" \
    plugins/draft/skills/draft/scripts/runtime plugins/draft/references plugins/draft/skills/draft
  ```

  预期：`recover_draft` 零结果；其他模式只能命中明确的否定契约，不能命中把公共 `check` 写成严格只读或把 retained lifecycle 归给 recover 的陈述。

- [ ] **步骤 6：运行受影响集成测试**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py -q
  ```

  预期：全部 PASS。

- [ ] **步骤 7：提交边界清理**

  ```bash
  git add plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
  git commit -m "refactor(draft): separate recovery from trash lifecycle advancement"
  ```

### 任务 3：全量门禁与复审

**文件：**
- 修改：`.superpowers/sdd/2026-08-24-draft-durable-link-index-maintenance/progress.md`
- 修改：`docs/review-notes/2026-08-25-durable-link-index-docs-round-3.md`

**接口：**
- 输入：任务 1-2 提交与既有任务 13 文档提交
- 输出：可重复的门禁证据、最终残留审计和双轴独立复审结论

- [ ] **步骤 1：运行核心与适配层门禁**

  按 `plugins/draft/AGENTS.md` 分开运行 runtime/adapter unit、非网络 integration；运行 runtime Ruff、format check、strict Pyright、adapter gate 与 `git diff --check`。任何失败必须定位根因，不能删测试或放宽配置。

- [ ] **步骤 2：重跑入口职责残留审计**

  核对 notification、check/query、dry-run、adopt、recover，journal v4，以及 authority → candidate → commit → warning/error → recovery → next fresh 全链；历史 progress/review 文档中的过程记录不作为当前协议。

- [ ] **步骤 3：新范围执行 Round 3 两轴复审**

  用全新 reviewer 分别审 Standards 与 Spec；状态机规格轴使用 Sol。固定范围从
  `f33a6f4` 到任务 2 最终提交。每个 finding 单独给修复方案；如再修改则新建
  Round 4，不覆盖 Round 3。

- [ ] **步骤 4：收口 ledger 与工作树**

  progress 记录任务 13、额外恢复边界修复、门禁计数、network/Windows 未运行和最终评审结论。`git status --short` 最终只能保留用户的 `M AGENTS.md`。

### 任务 4：闭合无锁 operation 分派与 pending 恢复证据

**文件：**
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:4725-4800`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:590-655,810-860`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py:890-945`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py:345-430,1000-1065`

**接口：**
- 输入：`Journal` 的 `kind`、`phase` 与 operation 专属目录；调用方已经证明当前无锁
- 输出：`recover_unlocked_operation(root: Path, journal: Journal) -> None`，作为无锁 allocating 清理和矛盾 phase 拒绝的单一权威

无锁 operation 决策表：

| 状态 | 动作 | 不变量 |
| --- | --- | --- |
| 无 journal 且仅空分配目录 | 删除空目录 | 不复用 operation ID |
| 非 restore 的 `allocating` | 精确预检授权内容，清恢复材料、journal 和空目录 | 尚未取得写权限，不触碰受管内容 |
| restore `allocating` 且未被 T 关联 | 写 `rolled_back` 并清恢复材料 | 保留终态审计事实 |
| `prepared/applying/validating/rolling_back` | `operation.inconsistent_state` | 写入阶段必须持锁 |
| 三个 link-index phase | `operation.inconsistent_state` | 索引终态前必须持锁 |
| `trash_handoff_succeeded/trash_purge_committed` | `operation.inconsistent_state` | 已提交清理必须持锁 |
| `completed/rolled_back/trash_retained/trash_handoff_pending` | 不修改 | 终态或合法无锁生命周期状态，不属于 recovery-pending |

- [ ] **步骤 1：写无锁 trash RED 矩阵**

  通过公开 `recover_interrupted_operations` 证明：无锁 `allocating` operation 目录被精确
  清理；`prepared/applying/validating/rolling_back` 分别返回
  `operation.inconsistent_state` 并保持 tree bytes 不变。现实现会把这些状态全部静默
  跳过，因此 RED 必须由目录残留或错误缺失触发，不能由非法 journal 夹具触发。

- [ ] **步骤 2：提取并复用无锁 operation 恢复原语**

  把 `operations.recover` 现有循环中从 `Journal` 分派 allocating、写入 phase、索引 phase、
  committed cleanup phase 与合法无锁状态的逻辑提取为
  `recover_unlocked_operation(root, journal)`。`operations.recover` 和
  `trash_lifecycle._handle_no_lock_dir` 的非 restore 分支共同调用它；不得复制 phase
  清单或让 helper 扫描其他 operation。

- [ ] **步骤 3：删除 `_handle_no_lock_dir` 的无用参数**

  删除 `now`、`handoff` 形参和唯一调用点的实参。restore 的 R/T 关联恢复继续留在
  `trash_lifecycle`；非 restore 的无锁 phase 统一交共享原语。

- [ ] **步骤 4：补齐持锁 pending 的公开恢复与幂等证据**

  扩展真实 handoff `RuntimeError` 中断场景：先证明 journal 停在
  `trash_handoff_pending`、权威包和锁存在；再用成功 handoff 调用
  `recover_interrupted_operations`，断言适配器恰好调用一次、phase=`completed`、权威包与
  锁消失。第二次 recover 返回空 tuple 且不再次调用适配器。

- [ ] **步骤 5：运行定向测试与静态门禁**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py -q
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked ruff check \
    plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
  ```

  预期：全部 PASS；strict Pyright 0 errors。

- [ ] **步骤 6：提交修复并进入 Round 4**

  ```bash
  git add docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md \
    plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
  git commit -m "fix(draft): classify unlocked recovery states"
  ```

### 任务 5：修复共享 `recover` 对 restore 的无锁分派回归

**文件：**
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:4718-4786`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py:892-941,2493-2510`

**接口：**
- 输入：`operations.recover(root_path: Path)` 在无锁时扫描到的、已经由 `read_journal`
  验证目录身份与 kind/phase 所有权的 `Journal`
- 输出：所有 kind 共用 `recover_unlocked_operation(root: Path, journal: Journal) -> None`
  的 phase 分类；restore 只有关联 `allocating` 的特殊终态化留在扫描循环

无锁 restore 决策表：

| phase/关联状态 | `operations.recover` 动作 | 不变量 |
| --- | --- | --- |
| `allocating` 且 metadata 含 `restores_trash_operation` | 清恢复副本并写 `rolled_back` | 保留关联恢复的审计事实 |
| 其他 `allocating` | 委托共享 helper 精确清理 operation 目录 | 保持修复前的通用孤儿分配语义 |
| `prepared/applying/validating/rolling_back` | `operation.inconsistent_state` | restore 写入阶段也必须持锁 |
| 三个 link-index phase | `operation.inconsistent_state` | restore 索引收敛阶段也必须持锁 |
| `completed/rolled_back` | 不修改 | 合法无锁终态只保留审计 |

- [ ] **步骤 1：写无锁 restore RED 矩阵和错误路径 RED**

  在 `test_operations.py` 增加合法 restore journal 构造器，并参数化覆盖
  `prepared`、`applying`、`validating`、`rolling_back`、`link_index_updating`、
  `link_index_published`、`link_index_rebuild_required`。索引 phase 必须携带规格 §4 要求的
  `link_index_revision/result/sha256` 字段，确保 RED 来自无锁分类而不是 journal 解析失败。
  每个状态调用 `operations.recover(root)` 都精确断言
  `CheckCode.OPERATION_INCONSISTENT_STATE`，并比较调用前后的 operation tree bytes 不变。

  ```python
  def write_restore_phase_journal(
      root: Path, operation_id: str, phase: OperationPhase
  ) -> Path:
      journal_dir = root / ".draft/operations" / operation_id
      journal_dir.mkdir(parents=True)
      payload: dict[str, object] = {
          "journal_version": JOURNAL_VERSION,
          "operation_id": operation_id,
          "kind": OperationKind.RESTORE.value,
          "phase": phase.value,
          "root_state": "registered",
          "observations": [],
          "mutations": [],
          "remove_empty_directories": [],
          "metadata": {},
      }
      if phase is OperationPhase.LINK_INDEX_UPDATING:
          payload["link_index_revision"] = "1"
      elif phase is OperationPhase.LINK_INDEX_PUBLISHED:
          payload.update(
              link_index_revision="1",
              link_index_result="published",
              link_index_sha256="a" * 64,
          )
      elif phase is OperationPhase.LINK_INDEX_REBUILD_REQUIRED:
          payload.update(
              link_index_revision="1",
              link_index_result="rebuild_required",
          )
      (journal_dir / "journal.json").write_text(json.dumps(payload), encoding="utf-8")
      return journal_dir

  @pytest.mark.parametrize(
      "phase",
      [
          OperationPhase.PREPARED,
          OperationPhase.APPLYING,
          OperationPhase.VALIDATING,
          OperationPhase.ROLLING_BACK,
          OperationPhase.LINK_INDEX_UPDATING,
          OperationPhase.LINK_INDEX_PUBLISHED,
          OperationPhase.LINK_INDEX_REBUILD_REQUIRED,
      ],
  )
  def test_no_lock_restore_nonterminal_phase_is_inconsistent(
      operation_draft: Path, phase: OperationPhase
  ) -> None:
      root = operation_draft
      journal_dir = write_restore_phase_journal(root, "2" * 32, phase)
      before = {
          path.relative_to(journal_dir): path.read_bytes()
          for path in journal_dir.rglob("*")
          if path.is_file()
      }

      with pytest.raises(DraftError) as caught:
          recover(root)

      assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
      after = {
          path.relative_to(journal_dir): path.read_bytes()
          for path in journal_dir.rglob("*")
          if path.is_file()
      }
      assert after == before
  ```

  扩展 `test_no_lock_allocating_with_unknown_content_is_inconsistent`：除错误码和现场外，
  精确断言 `DraftError.path == journal_dir`，锁定修复前的稳定错误路径协议。

- [ ] **步骤 2：运行 RED，确认当前回归可证伪**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    -q -k 'no_lock_restore or no_lock_allocating_with_unknown_content'
  ```

  预期：restore 矩阵因当前实现静默成功而 FAIL；未知成员用例因错误路径是具体成员而
  FAIL。不得接受 journal 解析失败、夹具字段非法或其他错误码造成的伪 RED。

- [ ] **步骤 3：让所有 kind 经过共享 helper，并让未知 phase 失败关闭**

  扫描循环只保留关联 restore `allocating` 的特殊分支；该分支完成后 `continue`，其他
  journal 无条件调用 `recover_unlocked_operation`：

  ```python
  if (
      journal.kind is OperationKind.RESTORE
      and journal.phase is OperationPhase.ALLOCATING
      and "restores_trash_operation" in journal.metadata
  ):
      _clean_recovery_copies(journal)
      write_journal(_with_phase(journal, OperationPhase.ROLLED_BACK))
      continue
  recover_unlocked_operation(root, journal)
  ```

  在 helper 中用唯一的显式白名单表达合法无锁审计态：`completed`、`rolled_back`、
  `trash_retained`、`trash_handoff_pending`。`allocating` 清理分支之后，白名单内返回；
  其余当前或未来 phase 统一报 `operation.inconsistent_state`。不得按 kind 绕过 phase
  分类，不得用枚举补集把未来 phase 自动归入合法跳过态。allocating 未知成员继续报
  `journal.directory`，不得改为具体成员路径。

- [ ] **步骤 4：补齐 allocating 与终态 restore 的回归证据**

  增加一个未关联 restore `allocating` 用例，证明 `operations.recover` 仍精确删除其空
  operation 目录；增加 `completed/rolled_back` 参数化用例，证明日志字节保持不变。
  这些用例只验证共享 operations 边界，不替代 `trash_lifecycle` 对 R/T 反向关联的集成测试。

  ```python
  def test_no_lock_unlinked_restore_allocating_is_cleaned(operation_draft: Path) -> None:
      journal_dir = write_restore_phase_journal(
          operation_draft, "3" * 32, OperationPhase.ALLOCATING
      )
      recover(operation_draft)
      assert not journal_dir.exists()

  @pytest.mark.parametrize(
      "phase", [OperationPhase.COMPLETED, OperationPhase.ROLLED_BACK]
  )
  def test_no_lock_restore_terminal_journal_is_preserved(
      operation_draft: Path, phase: OperationPhase
  ) -> None:
      journal_dir = write_restore_phase_journal(operation_draft, "4" * 32, phase)
      journal_path = journal_dir / "journal.json"
      before = journal_path.read_bytes()
      recover(operation_draft)
      assert journal_path.read_bytes() == before
  ```

- [ ] **步骤 5：运行定向测试与静态门禁**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py -q
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked ruff check \
    plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked ruff format --check \
    plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py
  ```

  预期：定向测试全部 PASS；Ruff 与 format check 通过；随后按插件作用域门禁运行 strict
  Pyright，必须为 0 errors。

- [ ] **步骤 6：提交修复**

  ```bash
  git add plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py
  git commit -m "fix(draft): preserve unlocked restore classification"
  ```

### 任务 6：让未关联 restore 的无锁放弃在清理成功后才终态化

**文件：**
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:617-632`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py:974-1016`

**接口：**
- 输入：无锁、`kind=restore`、`phase=allocating`、无 mutation 且没有 trash 反向关联的
  `Journal`
- 输出：成功时清除已授权恢复副本、耐久写 `rolled_back` 并返回对应 `OperationResult`；
  任何清理验证失败时不修改 journal、恢复副本或受管内容

放弃顺序决策表：

| 恢复副本事实 | 动作 | 可观察结果 |
| --- | --- | --- |
| `originals`/`staged` 缺失或只含已登记且摘要匹配的副本 | 清副本，再写 `rolled_back` | journal 为终态，第二次 recover 不产生额外副作用 |
| 任一目录有未知成员、链接、错误类型或摘要不符 | 停止 | `operation.inconsistent_state`；journal 仍为 `allocating`；树字节不变；第二次调用报同一错误 |
| 清理成功但终态 journal 写入失败 | 停止 | 保留 `allocating` journal；后续 recover 可幂等重试写终态 |

- [ ] **步骤 1：写失败顺序 RED**

  在 `test_trash_lifecycle.py` 构造无锁、未关联 restore `allocating` journal，在其
  `originals/` 写入未登记的 `unexpected.bin`；保存 operation 目录所有文件 bytes。连续两次
  调用 `recover_interrupted_operations(root)`，每次都断言如下：

  ```python
  def tree_bytes(directory: Path) -> dict[Path, bytes]:
      return {
          path.relative_to(directory): path.read_bytes()
          for path in directory.rglob("*")
          if path.is_file()
      }

  with pytest.raises(DraftError) as caught:
      recover_interrupted_operations(root)
  assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
  assert read_journal(journal_path).phase is OperationPhase.ALLOCATING
  assert tree_bytes(journal.directory) == before
  ```

  同一组增加无副本的成功用例：调用后 `read_journal(journal_path).phase` 为
  `ROLLED_BACK`，且第二次调用不改 journal bytes。夹具不创建 trash 反向关联，避免把
  R/T 协调状态当成无锁孤儿分支。再用 `monkeypatch` 在成功清理后让
  `draft.trash_lifecycle.write_journal` 抛 `DraftError(\"operation.durability_failed\")`：第一次
  必须保留 `allocating` journal，撤销 stub 后第二次调用必须写成 `rolled_back`，证明清理与
  journal 写入之间的失败不会被终态跳过掩盖。

- [ ] **步骤 2：运行 RED**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    -q -k 'unlinked_restore or orphan_restore'
  ```

  预期：未知副本用例当前第一次会把 journal 写为 `rolled_back` 后才报错，故 phase/tree
  断言 FAIL；成功用例可为绿，不得把它作为 RED 证据。

- [ ] **步骤 3：只调换孤儿 restore 的两个耐久动作**

  在 `_handle_no_lock_dir` 的未关联 restore allocating 分支中先调用
  `clean_recovery_copies(journal)`，其成功返回后才执行
  `write_journal(with_phase(journal, OperationPhase.ROLLED_BACK))`。保持现有 R/T 反向关联
  判定、返回值与错误码；不要把清理失败解释成终态、不要清理未知顶层内容、不要新增
  catch-and-continue 分支。

- [ ] **步骤 4：运行 GREEN 与定向静态检查**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py -q
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked ruff check \
    plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
  ```

  预期：全部 PASS；随后运行 strict Pyright，必须为 0 errors。

### 任务 7：统一无锁 operation 目录身份扫描与 journal 读取

**文件：**
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:821-833,4751-4795`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:592-664,825-834`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**接口：**
- 新增：`iter_recovery_operation_directories(root: Path) -> tuple[Path, ...]`
- 新增：`read_unlocked_operation_journal(root: Path, entry: Path) -> Journal | None`
- 消费者：`operations.recover`、`recover_interrupted_operations` 与 `_restore_linked_back`

目录扫描决策表：

| `.draft/operations` 或子项状态 | 动作 | 错误/副作用 |
| --- | --- | --- |
| operations 目录不存在 | 返回空元组 | 无副作用 |
| operations 不是目录 | 停止 | `operation.inconsistent_state`，保留现场 |
| 子项是符号链接（含悬空、指向文件或目录） | 停止 | `path.symlink_escape`，不读取目标 |
| 子项不是目录，或目录名不是 32 位小写十六进制 ID | 停止 | `operation.inconsistent_state`，保留现场 |
| 合法 ID 的空目录且没有 journal/pending | 仅由顶层恢复循环删除空目录 | 不复用 ID；重复调用稳定 |
| 合法 ID 的非空目录缺 journal/pending | 停止 | `operation.inconsistent_state`，树字节不变 |
| journal 或 pending 节点类型错误/解析错误 | 停止 | 既有 `operation.journal_corrupt` 语义与路径 |
| 合法当前 journal | 解析并交现有 phase 分派 | 不在扫描器复制 phase 规则 |

- [ ] **步骤 1：为两个公开入口写共享身份 RED 矩阵**

  在两个集成测试文件分别覆盖 `operations.recover` 和
  `recover_interrupted_operations`。每个用例创建独立 root，参数化下列子项，并在失败后
  再调用一次：

  ```python
  cases = [
      ("symlink", CheckCode.PATH_SYMLINK_ESCAPE.value),
      ("regular-file", CheckCode.OPERATION_INCONSISTENT_STATE.value),
      ("invalid-id-directory", CheckCode.OPERATION_INCONSISTENT_STATE.value),
      ("nonempty-without-journal", CheckCode.OPERATION_INCONSISTENT_STATE.value),
  ]
  ```

  symlink 指向根外目录并写入 sentinel；断言两次失败的 code/path 一致且 sentinel、操作
  目录 tree bytes 均不变。另为合法 ID 空目录断言两个入口都会删除它；为合法 ID 非空无
  journal 目录断言不会删除。不得只验证 `Path.is_dir()`，必须覆盖悬空链接和指向普通文件
  的链接之一。

- [ ] **步骤 2：运行 RED**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    -q -k 'recovery_scan or no_lock_operation_directory'
  ```

  预期：至少 `operations.recover` 对链接/非目录或无 journal 目录的静默跳过断言 FAIL；
  生命周期入口对链接指向文件或普通文件的静默跳过断言 FAIL。不得用损坏 journal 代替
  目录身份 RED。

- [ ] **步骤 3：实现共享 no-follow 扫描边界**

  `iter_recovery_operation_directories` 必须先调用 `reject_symlink_components`，再用
  `classify_node_or_error`（而不是 `Path.is_dir()`）分类 operations 目录与每个子项；它
  只返回已验证为普通目录且名称已由 `validate_operation_id` 验证的子项。`iterdir` 的
  `OSError` 映射为 `operation.inconsistent_state`，不得吞掉。对子项 ID 验证失败时，将
  `DraftError` 映射为 path 指向该子项的 `operation.inconsistent_state`。

  `read_unlocked_operation_journal` 只接收上述合法子项：journal 存在时先收敛合法 pending
  再解析；journal 缺失且 pending 缺失时，只有确定目录为空才返回 `None`；非空目录报
  `operation.inconsistent_state`。它不删除目录。顶层两个入口在得到 `None` 后用 `rmdir`
  删除空目录，`rmdir` 失败也报 `operation.inconsistent_state`。`_restore_linked_back`
  必须复用目录枚举器，继续把缺 journal 的非空兄弟作为不一致证据，不得为读取兄弟而清理它。

  枚举成功不是后续 I/O 的永久授权。reader 和空目录删除 helper 在每次读取、pending
  收敛或 `rmdir` 前，必须只用 `root` 与经过验证的 `entry.name` 调用
  `operation_journal_path(root, entry.name)` 重建目录路径；不得使用早前枚举得到的
  `entry / "journal.json"` 继续跟随文件系统。这样子目录在枚举后被替换为符号链接时，
  `reject_symlink_components` 必须在读取外部目标之前返回 `path.symlink_escape`。为这一
  race 增加真实文件系统 RED：在 reader 前 monkeypatch 受控协作者把
  `operations/<id>` 改为指向根外目录的符号链接，外部 sentinel bytes 保持不变，两条
  公开入口都返回 `path.symlink_escape`。

- [ ] **步骤 4：替换两个入口的本地扫描循环**

  `operations.recover` 与 `recover_interrupted_operations` 都按以下形状消费共享边界；保留
  已有锁跳过、关联 restore allocating 和 phase 分派，不把它们移入扫描器：

  ```python
  for entry in iter_recovery_operation_directories(root):
      journal = read_unlocked_operation_journal(root, entry)
      if journal is None:
          remove_empty_recovery_operation_directory(entry)
          continue
      # 既有 kind/phase 分派
  ```

  `remove_empty_recovery_operation_directory(root: Path, entry: Path)` 是跨模块共享的公开
  helper，不能以私有导入或 `pyright: ignore[reportPrivateUsage]` 传递。它只能 `rmdir` 已由
  reader 证明为空、并在删除前重新由 `operation_journal_path` 验证的目录：

  ```python
  def remove_empty_recovery_operation_directory(root: Path, entry: Path) -> None:
      directory = operation_journal_path(root, entry.name).parent
      try:
          directory.rmdir()
      except OSError as exc:
          raise DraftError(CheckCode.OPERATION_INCONSISTENT_STATE.value, directory) from exc
  ```

  不得引入递归删除、glob、目录名字符串拼接或按 `OperationKind` 复制 phase 清单。

- [ ] **步骤 5：运行 GREEN、静态检查与提交**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py -q
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked ruff check \
    plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
  ```

  预期：全部 PASS；format check、strict Pyright 0 errors 与 `git diff --check` 通过。
  只暂存本任务的四个 runtime 文件，提交信息为
  `fix(draft): validate unlocked recovery directories`。

### 任务 8：将已完成实施计划收敛为当前契约

**文件：**
- 修改：`docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md`

**接口：**
- 输入：任务 1-7 已确认的恢复行为、`docs/design.md` 的权威协议与本轮 review notes
- 输出：只描述当前有效恢复边界、决策表和验证命令的中文契约文档

- [ ] **步骤 1：移除已完成过程表述**

  将该文件改为当前契约，而不是历史执行记录：删除“当前实现会失败”、RED/GREEN 预期、
  已完成任务的 commit 指令、修复前行为和要求执行者重现过去缺陷的复选框。不要只勾选
  复选框保留旧叙事。历史审查过程只保留在 `docs/review-notes/` 与 progress ledger。

- [ ] **步骤 2：保留可验证的当前行为**

  文档必须保留以下当前决策：recover 与到期 lifecycle 分离；locked retained/pending/
  committed cleanup 路由；无锁所有 kind 的 allocating/mid-write/index/terminal/pending
  分类；目录身份与 journal 读取决策表；失败保留、重复执行与 network/Windows 验证边界。
  验收只引用现存测试文件和当前门禁命令，不宣称尚未运行的平台已验证。

- [ ] **步骤 3：验证并提交文档收口**

  ```bash
  rg -n '当前实现会|预期：FAIL|RED|GREEN|git commit -m|recover_draft' \
    docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md
  git diff --check
  git add docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md
  git commit -m "docs(draft): record recovery lifecycle contract"
  ```

  预期：检索无结果，diff check 通过；文档保持中文主体，代码标识符和命令保持原文。

## 自检

- 规格覆盖：§13.5 的 recover/lifecycle 分离、§14 的 recover 只恢复已有 operation、无锁
  phase、已开始 handoff 的恢复、提交后清理、跨 kind 共享分派以及目录身份/失败重试均
  映射到决策表和测试。
- 占位符扫描：没有 TBD/TODO 或要求执行者自行选择语义的步骤。
- 类型一致性：两个应用边界签名沿用现有实现；`_recover_locked_trash` 继续返回
  `TrashResult | None`，`recover_unlocked_operation` 不返回业务结果，调用方公开返回仍为
  `tuple[LifecycleResult, ...]`。
