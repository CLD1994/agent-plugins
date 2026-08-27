# 受协调 writer 的显式恢复边界

Draft 只在同一根上遵守共同协议的受协调 writer 之间提供串行化。OS root lock 与运行期根锁记录 `.draft/operation.lock` 的串行化身份跨越记录删除和最终 `.draft/` fsync；记录保留崩溃后的显式恢复要求。所有 authority/content/catalog/head/outcome commit 和不可延期授权 cleanup 必须 durable，projection 已 durable 或安全分类为可重建/可降级且无未认证现场后才可完成记录删除。只有 `recover()` 可收敛需要恢复的持久现场：有效运行期根锁记录仍存在、表示此前受协调调用尚未取得 durable completion proof 的现场；或 marker/runtime/descriptor 匹配、无运行期根锁记录但 `InitDescriptor` 精确列举的 cleanup artifacts 仍存在。有效记录但无 live owner 时普通入口返回 `runtime.recovery_required` 并保留记录；无运行期根锁记录且无 InitDescriptor cleanup artifacts 时不创建 recovery 记录或枚举 terminal progress。marker durable 后的 init remnants 也由该 recovery path 取得锁、发布 recovery 记录并清理。

marker 可见前首次初始化使用独立的 temporary init gate，它不是运行期锁。锁、原子发布、no-follow 和 durability 的具体原生算法由 Windows、macOS、Linux 各自的 `PlatformAdapter` 内部实现；本 ADR 不固定 FD、inode、handle、hard-link 或其他 backing resource。各平台必须在任何目标写入前证明所需性质，不合格本地卷直接拒绝。该保障不承诺阻止同权限、绕过协议的本地进程修改命名空间。
