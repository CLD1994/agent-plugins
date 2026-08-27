# 01 Init、平台能力、根锁与 runtime v1 记录

Status: ready-for-human

Blocked by: 无

## 确认合同

实施 spec 第 2--5、7 节：`Lifecycle.initialize(root, root_state)` 是不经 RuntimeFactory 构造的公开静态/模块入口，在 marker durable 前、持临时 OS init gate 建立完整 runtime v1；CLI init 路由只规范化显式 root 后直接调用该入口。marker durable 后进入相同 runtime recovery cleanup path，取得 stable OS root lock、durable 发布 `holder_kind=recovery` 记录、按 `InitDescriptor` 精确清理，再按最终 unlink/fsync/release 顺序完成。若 marker/runtime/descriptor 匹配但 cleanup remnants 仍在，initialize、ordinary、read、DryRun 均返回 `runtime.recovery_required` 且零写；`InitDescriptor` 为唯一完整权威；仅严格空 target/`.draft`/`.draft/runtime` 可接受；相同 descriptor 续建，不同或不可认证现场失败关闭。普通 recover 不处理无 marker 的初始化现场。实现 Windows/macOS/Linux `PlatformAdapter`，不合格卷在 target write 前拒绝。

运行期根锁记录是 `.draft/operation.lock`。当前调用必须先取得 live OS lock，发布 canonical record，并完成 record file fsync 与 `.draft/` fsync；在此之前不得产生根副作用。有效记录但无 live owner 时必须保留并返回 `runtime.recovery_required`，普通入口不得删除、扫描 terminal 或隐式恢复；`recover()` 负责保持该记录排他，完成所有 authority/content/catalog/head/outcome commit 和不可延期授权 cleanup 的 durable 要求，并使 projection 的结果已 durable 或已安全分类为可重建/可降级且无未认证现场，再 unlink 记录并 fsync `.draft/`。记录损坏或无法认证返回 `runtime.corrupt` 并保留。`holder_kind` 是机器字段；FD/inode/handle/backing resource 算法由三平台 `PlatformAdapter` 内部决定。`HandoffAdapter` 是 runtime Implementation 的唯一 external seam；Implementation 可依赖该抽象并在内部装配本地 concrete Adapter，但该 seam 不成为 CLI 或插件适配层的注入点。不得实现产品清除接口、无 marker 自动处理或首次 Execute 建 runtime。

## 建议实现与顺序

1. 先写 descriptor codec、init 分类、运行期根锁记录状态与平台能力分类的失败单元测试。完成条件：spec 第 3--5 节每个 init/lock 分区，以及 `replace_exact_regular` 的 missing/expected regular/symlink/directory/identity-change/replace-error/parent-fsync-error 分区均有独立断言；记录 file fsync 或 `.draft/` fsync 未完成时断言零根副作用，marker-ready cleanup 的缺失/精确成员/unknown member 分区均可证伪，测试不访问真实文件系统；证据为测试清单与通过输出。
2. 写 Windows/macOS/Linux 三平台真实文件系统集成与故障矩阵：marker publish 后无 record、recovery record publish、每个 cleanup member、记录 unlink/最终目录 fsync、同 descriptor 并发、descriptor conflict、live/有效记录但无 live owner/corrupt 运行期根锁记录，以及 `replace_exact_regular` 的全部七个反例。完成条件：每个平台的合格卷覆盖各中断点和反例；无 live owner 的记录保留并要求 `runtime.recovery_required`；cleanup 缺失成员可跳过，unknown member/symlink/type/digest mismatch 返回 `runtime.corrupt` 且不删除；不合格卷在任何相关 target write 前返回 `runtime.platform_unsupported`，异常 target 零替换；证据为按平台归档的 integration 输出和树摘要。
3. 实现 internal PlatformAdapter、init gate、runtime v1 writer、运行期 root lock 与 record codec；所有副作用经过 capability。完成条件：静态调用图显示仅 runtime internal owner 能调用平台写入和运行期 root lock，RuntimeFactory 只在内部装配 OperationRuntime 协作者且不暴露 deps；Lifecycle.initialize 的协作者同样只留在其 implementation 内；证据为 import/call-site 审计和 codec round-trip 结果。
4. 运行平台集成门禁并验证两条构造路径的内部装配边界。完成条件：三平台门禁、核心 unit 与本任务 integration 全通过；marker 前 runtime 已完整；记录发布、根副作用、全部 authority/content/catalog/head/outcome commit、不可延期 cleanup、projection 安全分类、记录删除和最终目录 fsync 顺序可由 trace 证明；CLI init 仅调用公开 `Lifecycle.initialize`，其余受管理命令仅通过 `RuntimeFactory.for_root` 取得 OperationRuntime；CLI、插件适配层生产代码及插件适配层 unit/integration tests 对 `RuntimeDeps`、`PlatformAdapter`、`HandoffAdapter` 及其他 runtime 内部协作者的导入为零，插件适配层不得导入核心 draft 包；核心 runtime unit/integration tests 不得导入 concrete local HandoffAdapter、`RuntimeDeps`、`PlatformAdapter` concrete implementations、worker、Clock/ID concrete sources、运行期根锁、recovery、index-settled 或其他内部协作者；核心 runtime tests 如需隔离 external handoff，只能引用抽象 `HandoffAdapter` Interface 并使用受真实 Interface 约束的 mock/fake，三平台 integration 通过公开行为 Interface 触发 runtime 内部装配的真实本地 concrete Adapter；证据为命令输出、故障点矩阵和 AST/import/export 审计。

## 禁止捷径与完成标准

禁止 POSIX-only 假设、按时间抢锁、删除无 live owner 的有效记录、递归删未知树、marker 重复 init 身份或以目录存在代替 descriptor。完成时每个 init/运行期根锁记录状态和三平台故障表行都可注入失败并给出精确错误；marker 前 runtime 已完整；并发同 descriptor 收敛，不同 descriptor 冲突；未知/旧 schema 保留失败关闭。
