# 02 Root-bound Interface、admission 与 DryRun

Status: ready-for-agent

Blocked by: 01

## 确认合同

实施 spec 第 2、5--6 节。公开面仅为 Lifecycle 与 root-bound OperationRuntime 六方法；`Lifecycle.initialize` 是不经 RuntimeFactory 构造的公开静态/模块入口，CLI init 只规范化显式 root 后直接调用；RuntimeFactory 只以 `for_root(root: AbsoluteRoot)` 构造 root-bound OperationRuntime，不返回或承诺 Lifecycle。其他受管理根命令按既定 root 优先级选择规范绝对 root，再且仅通过 RuntimeFactory 取得 OperationRuntime。CLI 不拥有 planner、snapshot、capability、锁或任何 RuntimeFactory/Lifecycle 内部协作者。所有请求执行唯一顺序及 root_state/closure 决策表。DryRun 仅支持 create、move、promote、collapse、trash、generate-conclusions、status.set、relation.add、relation.remove；在无已发现记录且成功 preflight 时使用与 Execute 相同 planner 返回 point-in-time plan/outcome，但严格零副作用。direct write、trash、restore、maintain 在无记录时才取得 stable OS root lock 并在锁内确认后发布记录；read/Check 的稳定 preflight/final projection 不取锁，仅其 AdvanceDue 或机会式 index maintenance 子阶段尝试取得该锁，live 时跳过子阶段并带既有 busy warning 继续稳定 projection。仅在已发现有效记录后，DryRun 才做一次 non-blocking try-lock：live owner 返回 `runtime.busy`，无 live owner 的同一有效记录返回 `runtime.recovery_required`，记录消失后的单次重新 admission 再次变化返回 `runtime.external_change`，这些分支均不产生 plan；corrupt 或无法证明 probe 性质返回精确错误。`adopt`、`init`、`restore`、`recover`、`notification` 不支持 DryRun。Execute no-op 仍锁内重读、但不建 operation/epoch/artifact。

## 建议实现与顺序

1. 从 spec 决策表生成参数化失败 unit tests，先证明每个拒绝分区零自动副作用。完成条件：`Lifecycle.initialize` 直达、ordinary Execute（adopt 是/否）及支持 DryRun 的 perform/trash、七类 read、maintain、recover 各恰命中一行；除 initialize 外的 OperationRuntime 六方法均由 `RuntimeFactory.for_root` 返回实例调用；断言无 lifecycle call、无 terminal progress enumerate；DryRun 成功返回 plan/outcome，阻塞/错误分支无 plan；证据为参数表与通过输出。
2. 写 Windows/macOS/Linux DryRun/Execute integration 与故障矩阵：无 record 不 probe、已发现记录 try-lock live/无 live owner/记录消失/corrupt、direct write/trash/restore/maintain 无 record 的 stable lock acquire+publish、read/Check 稳定 projection 无锁、AdvanceDue 与 Page/Backlinks/Check 机会维护的 stable lock、live busy skip/warning、记录 file/.draft fsync 未完成、锁后变化和 no-op。完成条件：成功 DryRun 前后树字节相同且返回 point-in-time plan/outcome，不创建 record/operation/epoch/artifact；try-lock 失败返回 busy，成功后同一有效记录返回 recovery-required，记录消失后的单次重新 admission 再次变化返回 external-change；read/Check 遇有效记录无 live owner、cleanup remnants 或 corrupt 时无 projection，遇 live busy 或机会维护竞争时跳过自动写并带 warning 返回稳定 projection；各分支均无持久写；Execute no-op 发生锁内重读但无 record，竞态旧 snapshot 被拒绝；证据为按平台集成日志和文件树摘要。
3. 实现封闭 request、internal request gate、capture/preflight/revalidation 与 RuntimeFactory 对 OperationRuntime 的 composition。完成条件：所有公开写入口均从同一 gate 到达业务路由，且 gate 按 spec 第 5--6 节排序；RuntimeFactory 只给 CLI 提供 root 到 OperationRuntime 的构造，不返回或承诺 Lifecycle，不暴露 deps 或内部协作者；证据为 call-site 审计和公开接口测试。
4. 静态审计 CLI/adapter 没有 private runtime import。完成条件：AST/import/export 检查零违规，CLI init 只调用公开 `Lifecycle.initialize`，其他命令仅选择 root、通过 `RuntimeFactory.for_root` 取得 OperationRuntime、构造 request、调用接口、投影；factory 返回类型不含 Lifecycle，notification 不重复路由 read/recover；证据为审计命令输出。

## 禁止捷径与完成标准

不得让调用方传计划/函数，不得把 gate 放到 CLI，不能用 cache 证明闭包，不能以写入后撤销实现 DryRun，不能让 DryRun 在无 record 时 probe lock，不能删除无 live owner 的有效运行期根锁记录或隐式 recover。完成时所有后续入口只能从此 gate 进入；注册根与候选根的局部/完整闭包矩阵、无 record/live/有效记录但无 live owner/corrupt admission 分区全部可测试。
