# 当前工作树提交整理计划

## 范围、基线与判定方法

- 基线：`HEAD` 为 `da42ab3`（`docs(draft): record recovery commit boundary`）。本计划只处理
  `git status --short` 所列的 18 个未提交路径；不对已提交的规格、Issue、审查记录或
  `AGENTS.md` 重新归属。
- 权威规格：协调恢复所有权
  [spec](.scratch/draft-coordinated-recovery-ownership/spec.md)、Issue 03 第 2 轮
  [计划](.scratch/draft-coordinated-recovery-ownership/issues/03-round-2-plan.md)，以及链接索引耐久维护
  [规格](.scratch/draft-durable-link-index-maintenance/spec.md)。当前实现的独立审查结论见
  [第 8 轮记录](docs/review-notes/draft-recovery/2026-08-26-round-8.md)：Issue 03 未通过，状态仍为
  `ready-for-agent`。
- 归属证据只来自上述规范、现有提交边界和当前完整 diff。当前 diff 没有 hunk 作者或任务标记，
  因而不能把“同文件中看起来相关”的代码当作来源证明。

## 路径与 hunk 归属

| 路径或 hunk | 分类 | 证据与处理结论 |
| --- | --- | --- |
| `.gitignore` 的 `docs/tmp` | 用户/来源不明 | 两份 Draft 规格均未要求忽略临时目录，Issue 03 计划也不涉及仓库忽略规则。保留未暂存，等待修改者确认。 |
| `src/draft/operations.py` 的 journal v4、`LinkIndex*`、phase/transition、`settle_link_index_for_operation()`、`recover_link_index_phase()`、staging/revision hunk | 耐久链接索引 | 直接实现链接索引规格第 3--12 节的 revision、字段矩阵、发布、降级和恢复；但与 recovery FD、discovery、R/T 分类 hunk 交错，当前整文件不可作为纯索引提交。 |
| `src/draft/operations.py` 的 recovery FD/close、unlocked discovery、allocation residue、R/T ownership/effective graph、`recover*` hunk | 两者共享 | 协调恢复规格要求无锁 discovery、matching lock、关系分类；链接索引规格第 12 节要求其 phase 通过同一恢复器收敛。第 8 轮确认 effective graph 优先级和锁内 preflight 尚未闭合，不能提交。 |
| `src/draft/checker.py` 的 operation index、pending、residue、R/T relation projection | 两者共享 | 协调恢复要求 `check` 与 recovery 共享关系分类和 residue 语义；链接索引规格又要求 `check` 识别 operation/制品状态。与链接索引 maintenance 调用同文件共存，不能按路径拆分。 |
| `src/draft/checker.py` 的 `check_link_index_maintenance()` 及其调用 | 耐久链接索引 | 对应链接索引规格第 14、16 节的机会式维护；仍依赖同文件 operation index 和未提交 `operations.py` phase 模型，必须随整组审阅。 |
| `src/draft/trash.py`、`src/draft/trash_lifecycle.py` 的 Restore facts、owner/action、恢复/lifecycle 接线 | 协调恢复 | 直接对应 Issue 03 的唯一 owner、participant closure 和 lifecycle 入口。第 8 轮 P1 证明仍有旧局部 validator 旁路及缺少每项副作用前的全图 preflight；不可作为可回退中间提交。 |
| `src/draft/trash.py`、`src/draft/trash_lifecycle.py` 的链接索引 phase 终态处理 | 两者共享 | Trash/restore 必须在链接索引阶段后进入既有终态；该逻辑依赖 R/T 所有权与锁内恢复，不能从上述协调恢复 hunk 分开审阅。 |
| `src/draft/diagnostic_codes.py` 的 `OPERATION_PLAN_INVALID`，及 `src/draft/observations.py`、`src/draft/structure.py` 的导入替换 | 协调恢复 | Issue 03 明确要求此稳定机器码纳入唯一 literal owner。虽然文本 hunk 小，但 distribution gate 和多个混合测试共同定义其验收，应在 Issue 03 修复后连同消费者提交。 |
| `tests/integration/test_operations.py`、`tests/integration/test_trash.py`、`tests/integration/test_trash_lifecycle.py` 的 recovery、R/T、lock、residue hunk | 协调恢复 | 测试覆盖协调恢复的公开 seam，但同时覆盖 `link_index_*` phase 的恢复。第 8 轮要求补充 ID 交换、跨入口同码、零副作用与 participant preflight 反例；当前不是完成的 Issue 03 测试集。 |
| 上述三份测试及 `tests/integration/test_checker.py`、`test_cli.py`、`test_lifecycle.py` 的 `link_index_*`、check maintenance hunk | 耐久链接索引 | 覆盖索引发布、恢复与 `check`；其 operation 状态依赖共享 recovery 模型，不能在隔离 commit 中验证。 |
| `tests/integration/test_distribution_contract.py`，以及 `tests/integration/test_cli.py`、`test_operations.py`、`test_trash_lifecycle.py`、`tests/unit/test_cli.py`、`test_move_planning.py`、`test_operations.py` 的 `OPERATION_PLAN_INVALID` literal hunk | 协调恢复 | 属于 Issue 03 P2 的 literal-owner gate。第 8 轮虽认可方向，但 P1 未通过，且这些文件也含索引/恢复混合 hunk；等待修复后用完整 gate 验收。 |

除上述按主题可识别的 hunk 外，其余同一文件变动为**用户/来源不明**：当前证据无法把它们可靠地追溯到协调恢复或链接索引任务。它们必须保留在工作树，直至来源负责人确认；不得因相邻 hunk 属于某个任务而一并暂存。

## 提交顺序与精确暂存边界

1. **本计划提交（现在执行）**
   - 路径：仅 `.scratch/draft-coordinated-recovery-ownership/worktree-commit-plan.md`。
   - 暂存：`git add -- .scratch/draft-coordinated-recovery-ownership/worktree-commit-plan.md`；先用
     `git diff --cached --name-only` 断言只出现此路径。
   - 验证：提交前后均执行 `git diff --check`；提交前还执行
     `git diff --cached --check`。提交信息：`docs(draft): plan remaining worktree commits`。
   - 依赖：在 `da42ab3` 之后；不依赖任何未提交 runtime hunk。

2. **来源确认后的 `.gitignore` 提交（暂不执行）**
   - 精确 hunk：仅 `.gitignore` 的 `docs/tmp` 忽略规则。
   - 前置条件：原作者或任务记录确认其用途、范围和是否应保留；确认后运行仓库忽略规则相关检查及
     `git diff --check`。建议提交信息：`chore: ignore temporary agent handoffs`。
   - 依赖：可独立于运行时代码；在确认前不是可提交项。

3. **协调恢复与链接索引的整组实现提交（暂不执行）**
   - 精确路径：其余 16 个 runtime 生产和测试路径，即
     `checker.py`、`diagnostic_codes.py`、`observations.py`、`operations.py`、`structure.py`、
     `trash.py`、`trash_lifecycle.py` 及 9 个 integration、3 个 unit 测试路径。
   - 前置条件：先完成下文“后续执行者任务”并通过独立复审。建议按最终修复后的实际职责决定是否拆为
     `feat(draft): maintain durable link indexes` 与
     `fix(draft): coordinate restore and trash recovery`；只有在共享 hunk 已被重构为可独立构建、
     独立测试和可回退时才拆分。否则使用一条整组提交，提交信息应同时描述两项协议，不能假装只实现
     其中之一。
   - 依赖：必须在本计划提交之后；索引恢复依赖协调恢复器的 matching-lock 与全图事实边界，协调恢复
     的 Trash/restore 终态又依赖索引 phase。因此当前顺序不是“先索引后恢复”或反过来，而是先修复并
     复审共享契约，再决定可验证的拆分。

## 同文件混合 hunk 的暂存策略

当前不允许使用 `git add -p`、`git apply --cached` 或基于行号的补丁去拆 runtime 文件：
`operations.py` 的链接索引状态机、FD/close 改造、unlocked recovery 与 R/T ownership 互相移动了
定义和调用；`checker.py`、`trash.py`、`trash_lifecycle.py` 同时消费两类协议。虽然 Git 可以非交互
暂存文本片段，但当前没有足够证据证明片段的来源、依赖闭合或可回退性，因而不安全。

执行者应在隔离 worktree 中完成剩余修复并使接口稳定，再从隔离 worktree 生成只含已审阅 commit 的
补丁或 cherry-pick。主工作树保持这 16 个路径未暂存，直至负责人确认全部来源不明 hunk。禁止把整文件
或全部工作树直接提交来规避归属判断。

## Issue 03 的提交资格与完成资格

第 8 轮审查明确未通过：全图优先级仍可能受 operation ID 与 transition 验证顺序影响，恢复仍存在局部
validator 旁路，且缺少每个 durable transition/副作用前的 participant 全图 preflight。故当前 Issue 03
实现**不可作为可回退的中间状态提交**：单独提交会把不符合已确认恢复契约的行为固化为历史节点，也不
具备独立审阅或稳定回退价值。

“可提交”只表示一个变更集具备来源、依赖、构建和回归验证的独立边界；“可标记完成”还要求满足 Issue
验收和独立复审。当前 Issue 03 两项条件均未满足，必须保持 `ready-for-agent`，不得以本计划提交或现有
测试通过将其标记完成。

## 后续执行者任务与验收

1. 按第 8 轮记录修复统一 effective graph reader：全量 raw/type、全部 R forward、全图 relation、
   phase/action 的顺序必须与 ID 无关；`check`、discovery、锁内 recovery 与 lifecycle 共享结果。
2. 移除恢复链路对 `validate_restore_link()` 和旧局部 `RestoreLinkState` 的授权依赖；恢复只消费当前
   matching-lock 下的 `RestoreTrashRelationFacts`、owner、participant closure 与 action。
3. 建立并接入每次 durable transition、pending publish、解关联、清理、handoff、release 前的全图
   participant preflight；任一不一致都零副作用并保留现场。
4. 完成 `OPERATION_PLAN_INVALID` literal-owner gate 的所有消费者迁移，并补临时非 owner literal 被
   gate 拒绝的测试。
5. 独立复审时固定新的基线，覆盖 ID 交换双错误、三种合法关系、第三方非法关系、pending、材料/lock
   变化和重复恢复；断言稳定错误、journal/pending、lock、package、attempt、目录与 handoff 计数。
6. 通过后运行受影响 unit/integration、`tests/integration/test_distribution_contract.py`、`ruff check`、
   `pyright src/draft` 与 `git diff --check`。运行记录必须区分未运行项目。
7. 复审确认后，依据上一节隔离策略产出可审阅 commit；只有具有直接验收证据的 Issue checklist 才能更新。

Status: 本计划的文档提交可执行；其余工作树保持未暂存，等待修复、来源确认与独立复审。
