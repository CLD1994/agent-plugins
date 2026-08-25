# Draft Recover 生命周期边界修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使公开 `draft recover` 在所有锁内 trash 状态下只收敛已经存在的 recovery-pending operation，不从 `trash_retained` 发起到期移交或最终清理。

**Architecture:** `recover_interrupted_operations` 是公开 recover 的应用边界，`advance_trash_lifecycle` 是允许的普通受管命令前置生命周期边界。`_recover_locked_trash` 按 journal phase 显式分派恢复动作；删除同时组合恢复与到期推进的旧 `recover_draft` 边界，测试分别调用两个真实入口。

**Tech Stack:** Python 3.12、pytest、uv、Ruff、Pyright

**Spec:** `.scratch/draft-durable-link-index-maintenance/spec.md` §13.5、§14；`docs/design.md` §13.5

## Global Constraints

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

### Task 1: 锁定并修复 `trash_retained` 恢复分区

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:545-595`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py:658-696`

**Interfaces:**
- Consumes: `recover_interrupted_operations(root, *, now=None, handoff=None) -> tuple[LifecycleResult, ...]`、`operations.recover(root) -> OperationResult`
- Produces: `_recover_locked_trash` 对上表全部合法 phase 的显式恢复路由

- [ ] **Step 1: 把到期 retained 锁恢复用例改成可证伪的 RED**

  将 `test_locked_retained_recovery_advances_and_releases_lock` 改为直接调用 `recover_interrupted_operations(root, now=EXPIRY, handoff=...)`，并断言：handoff 调用列表为空、journal 仍为 `trash_retained`、权威包仍存在、锁已释放。当前实现会调用 handoff 并进入 `completed`，因此必须失败。

- [ ] **Step 2: 运行单个 RED 用例**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
    pytest plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py::test_locked_retained_recovery_does_not_advance_lifecycle -q
  ```

  预期：FAIL，至少一个反例断言证明 handoff 被调用、phase 被推进或包被删除；不得因导入/拼写错误产生伪 RED。

- [ ] **Step 3: 按决策表修复 `_recover_locked_trash`**

  保留 succeeded/purge committed 的既有已提交清理分支。对 `TRASH_RETAINED` 显式 `release_lock(root, operation_id)` 后返回 `None`；对 `TRASH_HANDOFF_PENDING` 才调用 `_advance_one_locked`；其余通过 `operations.recover(root)` 恢复原 operation，再按既有 `TrashResult` 字段包装结果。不要比较当前时间来选择 recovery 责任，也不要让 retained 分支读取或验证 handoff 副本。

- [ ] **Step 4: 增加普通写入 phase 的公共恢复反例**

  在现有真实 trash crash fixture 上至少覆盖一个 `prepared/applying/validating` phase，经 `recover_interrupted_operations` 后必须按原 mutation journal 收敛，而不是仅释放锁。若现有 `test_trash.py` 用例已经从该入口覆盖同一分区，保留并引用它，不复制 fixture。

- [ ] **Step 5: 运行恢复边界定向测试**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py -q
  ```

  预期：全部 PASS。

- [ ] **Step 6: 提交行为修复**

  ```bash
  git add plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py
  git commit -m "fix(draft): keep recover out of retained trash lifecycle"
  ```

### Task 2: 删除旧的组合边界并迁移测试

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:768-835,886-897`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**Interfaces:**
- Consumes: Task 1 的 `_recover_locked_trash` phase 路由
- Produces: 唯一 recovery 应用边界 `recover_interrupted_operations`；唯一到期推进边界 `advance_trash_lifecycle`

- [ ] **Step 1: 删除 `recover_draft` 生产函数**

  完整删除同时执行“崩溃恢复 + 无锁 retained/pending 批量推进”的函数。更新 `recover_interrupted_operations` docstring，直接说明它处理锁内恢复和无锁 recovery-pending operation；不得再引用旧函数。

- [ ] **Step 2: 迁移恢复类测试**

  `test_lifecycle.py` 与 `test_trash.py` 中验证 adopt/trash/restore crash recovery、错误保留和幂等性的调用统一改为 `recover_interrupted_operations`。原来同时参数化 `recover_draft` 与 `recover_interrupted_operations` 的测试删除重复轴，只保留真实公开 recovery 边界；测试名和 docstring 不再称两个公开入口。

- [ ] **Step 3: 迁移生命周期类测试**

  `test_trash_lifecycle.py` 中验证到期、节流、handoff、purge、批量首错停止的测试统一调用 `advance_trash_lifecycle`；验证锁内 crash 恢复、提交后清理和 restore 协调的测试调用 `recover_interrupted_operations`。不得创建测试 helper 再把两个入口串联成旧组合行为。

- [ ] **Step 4: 更新模块身份契约**

  `test_identity.py` 不再断言 `recover_draft` 存在，改为分别断言 `advance_trash_lifecycle` 和 `recover_interrupted_operations` 存在，明确两个责任边界。

- [ ] **Step 5: 证明旧边界和旧表述已消失**

  ```bash
  rg -n "recover_draft|check.*严格只读|recover.*推进.*retained trash|recover.*推进.*回收" \
    plugins/draft/skills/draft/scripts/runtime plugins/draft/references plugins/draft/skills/draft
  ```

  预期：`recover_draft` 零结果；其他模式只能命中明确的否定契约，不能命中把公共 `check` 写成严格只读或把 retained lifecycle 归给 recover 的陈述。

- [ ] **Step 6: 运行受影响集成测试**

  ```bash
  uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py -q
  ```

  预期：全部 PASS。

- [ ] **Step 7: 提交边界清理**

  ```bash
  git add plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py
  git commit -m "refactor(draft): separate recovery from trash lifecycle advancement"
  ```

### Task 3: 全量门禁与复审

**Files:**
- Modify: `.superpowers/sdd/2026-08-24-draft-durable-link-index-maintenance/progress.md`
- Modify: `docs/review-notes/2026-08-25-durable-link-index-docs-round-3.md`

**Interfaces:**
- Consumes: Task 1-2 提交与既有 Task 13 文档提交
- Produces: 可重复的门禁证据、最终残留审计和双轴独立复审结论

- [ ] **Step 1: 运行核心与适配层门禁**

  按 `plugins/draft/AGENTS.md` 分开运行 runtime/adapter unit、非网络 integration；运行 runtime Ruff、format check、strict Pyright、adapter gate 与 `git diff --check`。任何失败必须定位根因，不能删测试或放宽配置。

- [ ] **Step 2: 重跑入口职责残留审计**

  核对 notification、check/query、dry-run、adopt、recover，journal v4，以及 authority → candidate → commit → warning/error → recovery → next fresh 全链；历史 progress/review 文档中的过程记录不作为当前协议。

- [ ] **Step 3: 新范围执行 Round 3 两轴复审**

  用两个全新 Terra reviewer 分别审 Standards 与 Spec；固定范围从 `f33a6f4` 到 Task 2 最终提交。每个 finding 单独给修复方案；如再修改则新建 Round 4，不覆盖 Round 3。

- [ ] **Step 4: 收口 ledger 与工作树**

  progress 记录 Task 13、额外恢复边界修复、门禁计数、network/Windows 未运行和最终评审结论。`git status --short` 最终只能保留用户的 `M AGENTS.md`。

## Self-Review

- Spec coverage：§13.5 的 recover/lifecycle 分离、§14 的 recover 只恢复已有 operation、已开始 handoff 的恢复以及提交后清理均映射到决策表和测试。
- Placeholder scan：没有 TBD/TODO 或要求执行者自行选择语义的步骤。
- Type consistency：两个应用边界签名沿用现有实现；`_recover_locked_trash` 继续返回 `TrashResult | None`，调用方公开返回仍为 `tuple[LifecycleResult, ...]`。
