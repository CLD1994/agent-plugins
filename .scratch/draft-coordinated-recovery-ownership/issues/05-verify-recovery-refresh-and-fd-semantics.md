# 05: 验证恢复刷新竞态与 FD 资源语义

**What to build:** 在 Issue 01 的整组副作用门完成后，以三个已批准的公开恢复 seam 验收锁刷新、竞争、错误分类，并统一闭合全图 reader、preflight 与 resolver 的文件描述符资源协议和端到端证据。

**Blocked by:** 01: 在锁内收敛通用未持锁恢复候选。

Status-Schema: triage
Status: needs-triage
Tracker-State: closed
Resolution: superseded
Promoted-to: plugins/draft/docs/spec/runtime/recovery.md
Base-Spec: a694d1458335ed38900e31067b5f370ffe2c7e6a
Affects:
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: .scratch/draft-coordinated-recovery-ownership/spec.md
Scope: child-issue-only
Current-Owner: plugins/draft/docs/spec/runtime/recovery.md
Dispatch: blocked
Dispatch-Diagnostic: this recovery refresh/FD verification brief is retained history; the signed Q32 runtime recovery owner supersedes the parent feature and this child cannot be dispatched.

- [ ] `recover`、`recover_interrupted_operations` 和 `advance_trash_lifecycle` 均覆盖扫描后 lock 竞争、锁内重读、当前事实变化、稳定恢复错误及零副作用不变量。
- [ ] 竞争场景断言 active lock、获胜 writer 的 journal、恢复副本、目录、package 和 R/T 关系不变；释放 retained lock 后的新 writer 材料不会被生命周期路径消费。
- [ ] 测试覆盖普通 allocating、空/非空无 journal、pending、R/T 关系分区、waiting/terminal/error、已提交 handoff/purge 和重复恢复，并验证 checker 与恢复分类一致。
- [ ] R/T 分类测试按优先级覆盖不可读 JSON、v4 字段类型错误、metadata key/value 为 string 之外类型（含 null/number/list）、符号链接、R forward 缺失/非法、T 缺失或 kind 错、T claim 非法 ID、PRECLAIM、POST_UNCLAIM_CLEANUP、其他单向、错配、多重、T-only 及合法图后的非法 phase；分别断言 `operation.journal_corrupt`、`path.symlink_escape`、`operation.plan_invalid` 或 `operation.inconsistent_state`。
- [ ] 竞争测试断言只有锁竞争或合法候选被其他 owner 持有时返回 `operation.recovery_required`；所有损坏分类均失败关闭且 journal、copies、目录、关系和锁现场保留。
- [ ] 真实文件系统集成测试统一覆盖全图 reader、participant preflight 与 resolver 使用的 recovery leaf、operation directory、恢复上下文及材料 descriptor 的 fstat、读取 close、leaf close、目录 close 和上下文 close 失败；每个 descriptor 至多关闭一次，无主异常返回稳定错误，有主异常保留主错误。
- [ ] 单元测试只验证纯分类和状态转换；涉及真实文件系统、锁竞争、journal durability、handoff 和 close 故障的测试归入集成测试，并按公开行为断言而非私有 helper 调用图。
- [ ] 三个入口各自执行一次和重复执行后，journal、恢复材料、目录、关系、锁和 handoff 计数收敛到同一可观察结果，且测试输出记录所有未验证前提或外部环境限制。
