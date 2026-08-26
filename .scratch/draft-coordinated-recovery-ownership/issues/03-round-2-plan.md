# Issue 03 第 2 轮实施计划

## 已确认契约与范围

本轮只闭合 Issue 03 的 R/T 全图关系授权与机器码字面量所有权；不实施 Issue 01 的材料闭包
preflight，也不实施 Issue 05 的 descriptor close 改造。恢复材料、`attempt`、package、evidence
及其摘要的整组只读验证仍由 Issue 01 负责，文件描述符的 exactly-once 矩阵仍由 Issue 05 负责。

1. 对同一组 current/pending journal，`check`、未持锁 discovery、锁内 relation reader、
   `recover`、`recover_interrupted_operations` 与 lifecycle 必须按相同四阶段得到相同机器码：
   (a) 全量 raw/schema/metadata 类型，(b) 所有 Restore 的 forward claim，(c) 全图 R/T 图，
   (d) relation 的 phase/action。此顺序不得依赖 operation ID、candidate 遍历顺序或
   current/pending 选择顺序。
2. 仅 `PRECLAIM`、`MATCHED`、`POST_UNCLAIM_CLEANUP` 授予 Restore owner、两个 participant
   ID 和对应 `RestoreTrashAction`。T-only、R-only、dangling、错配、多重或 phase 非法图没有
   capability，不能由 Trash reverse claim、局部 R/T 快照或旧的 `RestoreLinkState` 推断。
3. 恢复动作必须只消费 matching lock 内新读取的全图 facts。`validate_restore_link()` 保留给
   restore 计划创建/预分配等非恢复路径；恢复链路不得调用它、不得因它发布 pending，也不得用它
   决定 owner 或关系。每次会改变 effective journal、claim、phase/action 的耐久 transition
   后，后续关系决策必须重新读取并分类；在 R/T 的任何 pending 发布前，非法图失败关闭。
4. `OPERATION_PLAN_INVALID` 是稳定机器码，必须与 `CheckCode`/`RootCode`/`QueryCode` 一样受
   literal-owner gate 约束；除 `diagnostic_codes.py` 的权威定义外，消费者只导入该常量。

## 数据流与实现边界

1. 在 `operations.py` 将 effective journal 构造拆为无副作用阶段：先收集所有 current/pending
   原始解析结果并完成 raw/type/identity 检查；再对所有 effective Restore 检查 forward claim；
   然后验证 current/pending transition 并选择 effective journal；最后将完整 index 交给纯
   `classify_restore_trash_ownership()`。若 transition 的现有错误会与前两层冲突，延后到前两层
   成功后再报告，避免它遮蔽高优先级错误。
2. 让 `validate_restore_trash_relations()` 自身按批次执行 metadata raw/type、全部 R forward、
   全部 T reverse lexical check，再执行 relation graph，最后 relation phase。它只读取
   `Mapping[str, Journal]`，不访问文件系统、不发布 pending。
3. `read_restore_trash_relation()`、`_classify_unlocked_recovery_candidates()`、checker 和
   lifecycle 继续经同一 effective graph reader/ownership classifier 取得 facts。不能在任何
   调用点另行调用 `validate_journal_transition()`、`validate_restore_link()` 或按 operation ID
   局部解析来覆盖该结论。
4. 在 `trash.py` 与 `trash_lifecycle.py` 将恢复路径从 `RestoreLinkState` / 局部
   `validate_restore_link()` 改为当前全图 `RestoreTrashRelationFacts` 的 relation、owner、
   participant IDs 与 action。R owner 持锁后在进入会写入/发布/清理的步骤前读取 facts；若
   `recover_locked_operation()` 推进了 R，则丢弃旧 facts 并重新读取；解关联或终态写入后同样
   重新读取后才继续。禁止以旧 action 进入下一项副作用，禁止从反向 claim 补 forward claim。
5. 修改 distribution literal gate 的注册集合，导入并登记 `OPERATION_PLAN_INVALID`，迁移所有
   gate 报出的非输出层裸 literal，并添加一个局部临时文件含该 literal 时 `_literal_owner_offenders`
   会报告的契约测试。不要为此放宽扫描集合。

## 先失败测试与验收矩阵

1. 单元：构造两个 operation ID 排序相反的全图反例。
   - R 缺/非法 forward 与另一 journal metadata 非字符串：两种 ID 排列都必须
     `operation.journal_corrupt`。
   - R 缺 forward 与另一 Trash reverse 非法 ID：两种 ID 排列都必须
     `operation.plan_invalid`，因为 R forward 阶段先于 graph。
   同时断言直接 relation validator、effective reader/ownership 和公开入口投影一致。
2. 集成：对同一个含 current/pending 的非法 R/T 图，分别经 `check`、unlocked recovery 与
   matching-lock recovery/lifecycle 触发，精确断言 `.code` 相同；快照断言 journal/pending、
   lock、package、attempt、目录成员和 handoff 计数均不变。
3. 集成：对 PRECLAIM、MATCHED、POST_UNCLAIM_CLEANUP 三种合法 relation 验证 R 是唯一 owner；
   为第三方 illegal claim 和 R/T pending 加反例，断言没有 pending 发布、terminal 写入、
   cleanup、handoff、claim 改写或 release。使用合法对照确认恢复仍可收敛且重复执行幂等。
4. 静态调用图审计：恢复路径（`_restore_recovery_action`、`_recover_locked_restore`、
   `resolve_restore_execution` 及其 lifecycle/direct-recover callers）不得再调用
   `validate_restore_link()`；该函数只可保留在 plan/预分配验证路径。
5. literal gate：真实注册集无 offender；临时非 owner Python 文件含
   `"operation.plan_invalid"` 时 helper 报出文件和行号。

## 实施顺序和阶段门

1. 先添加第 1、5 项最窄红测并运行它们，确认现实现分别暴露排序/注册缺陷。
2. 改造纯 relation validation 与 effective graph reader；运行对应 unit 和 checker/recovery
   窄集成测试。阶段门：四入口同码，且 ID 交换不改变结果。
3. 接线恢复和 lifecycle，删除恢复对局部 validator/旧 link-state 的关系授权依赖；每个耐久
   transition 后重取 facts。阶段门：非法图在任一 pending 发布前失败关闭，三种合法关系保留
   owner/幂等行为。
4. 迁移 literal 并补 gate 测试；运行 distribution contract。
5. 运行受影响 unit/integration、`ruff`、`pyright`、distribution literal-owner gate 和
   `git diff --check`。只有有直接证据的 Issue 03 checklist 项才更新；本轮不将整体状态改为
   完成。

## 禁止捷径

- 不按 operation ID 的局部循环交错四个分类阶段，不将 transition 校验提前为全图 R plan 的
  覆盖错误。
- 不从 T claim 推断/写入 R forward，不把未知或非法图折叠为 `recovery_required`。
- 不在恢复链路调用会 `resolve_journal_pending()` 的 `validate_restore_link()`，不先发布再回滚。
- 不以单 operation material validator、cleanup helper 或放宽 allowed members 替代 Issue 01 的
  participant material preflight；该工作不在本轮范围。
- 不改变 Issue 05 的 FD 所有权、close 错误或资源测试语义。

## 阻塞

功能语义没有需要用户裁定的阻塞：Issue 03 可在不改变顾问裁定的前提下先闭合全图分类、关系
授权和 literal-owner gate；整组材料 capability 的实现与证明由 Issue 01 后续接入本轮产出的
participant closure。

提交整理的阻塞：runtime 的 `operations.py`、`trash.py`、`trash_lifecycle.py`、`checker.py`、
`diagnostic_codes.py`、`observations.py`、`structure.py` 及对应 11 个测试文件在本轮开始前已含
3,600+ 行未提交 recovery/link-index 共同实现。第 2 轮在这些同一 hunks 中增加了优先级、facts
和 literal-owner 修改，无法仅凭当前 diff 可靠区分任务前已有内容与本轮内容；因此本轮不暂存
或提交该混合代码。已独立提交可以验证边界的规格、Issue、计划和 AGENTS 文档；代码应由持有
这组统一实现基线的负责人整体复审后提交。
