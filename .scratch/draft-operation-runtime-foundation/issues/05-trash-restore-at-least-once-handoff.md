# 05 Trash、Restore、active catalog 与 Handoff

Status: ready-for-agent

Blocked by: 01, 02, 03, 04

## 确认合同

实施 spec 第 7--9 节：R -> T 单向关系、7/30/24 时间规则、active catalog 为 active T/阻塞 R 的唯一发现 authority、每 invocation 一个 candidate 与一次 handoff。严格遵守 Restore×Trash 表、领域错误区分和 authorized -> copy/fsync -> prepared -> outcome 的 handoff 状态机。`HandoffAdapter` 是 runtime Implementation 的唯一 external seam；Implementation 可依赖抽象并在内部装配本地 concrete Adapter。核心 runtime unit/integration tests 对 handoff 仅可引用抽象 `HandoffAdapter` Interface；unit tests 可使用受真实 Interface 约束的 mock/fake，三平台 core runtime integration 通过公开行为 Interface 使用内部装配的真实本地 concrete Adapter，测试不直接导入该 concrete Adapter。无运行期根锁记录时，trash/restore/AdvanceDue/handoff 仍先取得 stable OS root lock，锁内重读确认仍无记录后才可发布自己的记录；记录缺失不能创建第二把锁。运行期根锁记录必须在当前调用产生根副作用前完成 file/.draft durability；authority/content/catalog/head/outcome commit 和不可延期 cleanup 必须 durable，projection 结果必须 durable 或安全分类为可重建/可降级且无未认证现场，根锁跨越 worker 实际停止、上述 durable 处理、记录删除和最终 `.draft/` fsync；无 live owner 的有效记录阻止本任务自动 candidate/handoff，要求显式 `recover()`。

## 建议实现与顺序

1. 先为 R/T fold、catalog transition、schedule rebuild、期限及 handoff transition 写失败 unit tests。完成条件：spec 第 7、9 节每个 R/T 与 catalog 变更前/下一 generation 分区都有断言，terminal operation 不参与 normal candidate 发现，无 live owner 记录阻断自动 candidate/handoff，缺失/过期 schedule 的本 invocation 是 0 candidate/0 handoff；证据为测试矩阵和通过输出。
2. 核心 runtime unit tests 使用受真实抽象 `HandoffAdapter` Interface 约束的 mock/fake，验证 authorized -> copy/fsync -> prepared -> accepted/failed/unknown、hard-timeout 与 purge crossing；Windows/macOS/Linux core runtime integration 通过公开行为 Interface 触发内部装配的真实本地 concrete Adapter，不直接导入该 concrete Adapter。两类核心测试对 handoff 均不得导入 concrete Adapter，并不得导入 `RuntimeDeps`、`PlatformAdapter` concrete implementations、worker、Clock/ID concrete sources、运行期根锁、recovery 或其他内部协作者来绕过公开行为 Interface。完成条件：外部已接受但 accepted record 未 durable 必变为 unknown；到期新 sequence 外送同一 Trash operation id，durable accepted 后调用数固定为零增量；deadline 前后未实际新建 attempt 时不重播历史 failed/unknown warning，新 sequence 写出 outcome 时恰有一个 warning；无记录 stable lock、运行期根锁记录 file/.draft fsync、worker 停止、outcome/catalog durability、cleanup/unlink/最终目录 fsync 任一失败都不返回成功；projection 失败仅在安全分类且无未认证现场时允许完成；证据为抽象 seam 的 mock/fake 调用日志、按平台真实 Adapter 故障矩阵与 progress dump。
3. 写每个 R/T durable 窗口的 integration，均接 `recover -> automatic lifecycle`；覆盖 100,000 个 terminal operation、10,000 个 active catalog entry、10,000 个 handoff entry 的 benchmark fixture。完成条件：重复 recover 从不调用 Adapter，R block、next candidate 与 retry deadline 只从 active authority 得出；普通写、trash、restore、Page、Status 的缺失/过期 schedule 或 recovery-required invocation 均为 0 candidate/0 handoff，下一 eligible invocation 至多一次 handoff；证据为故障点矩阵和性能报告。
4. 实现专用 package authorization、isolated worker、schedule rebuild、catalog/checkpoint 与业务路由。完成条件：运行期 root lock 覆盖 worker 停止和 outcome/catalog durability，schedule 重建 O(A) 后立即返回 automatic result 而非选择 candidate；证据为锁时序 trace、authority generation trace 与 integration 输出。

## 禁止捷径与完成标准

不得建立反向关系、扫描 terminal audit 找 active、删除 recovery-required 记录、无记录时绕过 stable OS root lock、外部调用中释放锁、把 local copy 算入 timeout、unknown 立即重投或因旧 failed 永久清理。完成时 accepted 永不重调，unknown 有 24h 节流，post-purge 仅当前 active explicit failed 可提交永久清理；三平台每个 handoff 与根锁 durable fault 均可证明收敛或失败关闭。
