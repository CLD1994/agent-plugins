# 06 锁内恢复收敛

Status: ready-for-human

Blocked by: 01, 03, 04, 05

## 确认合同

实施 spec 第 5、9--10 节。无运行期根锁记录时，`recover()` 先取得 stable OS root lock，锁内重读；若 marker/runtime/descriptor 匹配且存在 descriptor cleanup remnants，则 durable 发布 `holder_kind=recovery` 记录后按精确清单幂等清理。有效运行期根锁记录但无 live owner 时，只有 `recover()` 可取得其对应 OS lock 并保持记录排他；普通入口返回 `runtime.recovery_required`，不删除记录、不扫描 terminal、不推进 due。`recover()` 只收敛两类需要恢复的持久现场：有效运行期根锁记录仍存在、表示此前受协调调用尚未取得 durable completion proof 的现场；或 marker/runtime/descriptor 匹配、无运行期根锁记录但 `InitDescriptor` 精确列举的 cleanup artifacts 仍存在。无 marker 的初始化现场不处理；无运行期根锁记录且无 InitDescriptor cleanup artifacts 时不枚举 terminal progress。discovery 只读，取得锁后全量重读；recover 不推进 due、不新建 handoff、不调用 Adapter。全部 authority/content/catalog/head/outcome commit 和不可延期 cleanup durable，projection 结果已 durable 或安全分类为可重建/可降级且无未认证现场，记录 unlink 和最终 `.draft/` fsync 成功后才可释放锁并返回成功。

## 建议实现与顺序

1. 从 spec 第 5、7、10 节恢复表写纯分类/route 失败单元测试。完成条件：无记录 stable lock、marker-ready cleanup、根锁记录 live/有效记录但无 live owner/corrupt、catalog/head 的 intent-only、intent+immutable record、变更前 authority 版本/下一 authority 版本、replace 前后、commit 缺失、伪造/owner/version mismatch，以及 R/T 和 handoff 每个可恢复/失败关闭分区均有单独 case；普通 admission 对无 live owner 记录和 marker-ready remnants 无副作用；证据为参数化测试输出。
2. 对 init、normal、index、R/T、authorized/prepared/accepted/purge、catalog 每个 durable 窗口写 Windows/macOS/Linux integration，且每例执行两次 recover。完成条件：仅 `recover()` 可枚举未 committed progress intent 或 marker-ready cleanup remnants；replace error、authority parent-fsync error、记录 unlink/最终目录 fsync error、marker publish 后无记录、recovery record publish、每个 cleanup member、重启变更前/下一/other target 各自收敛或失败关闭，外部可能已接受但无 durable outcome 只成为 unknown；证据为按平台双次 recover 文件树、authority generation 与记录摘要。
3. 实现 locked resolver 和精确 cleanup；增加静态审计确保 discovery 无写和无 Adapter 调用。完成条件：静态审计与 Adapter spy 均为零违规，normal AdvanceDue/check 不枚举 terminal progress，稳定 catalog 损坏返回 fatal 而不扫描 terminal；证据为审计输出、spy 断言和集成日志。

## 禁止捷径与完成标准

禁止自动推进、terminal 扫描猜状态、删除 recovery-required 记录、吞掉 proof 错误或清理未授权成员。完成时任一恢复路径两次后稳定；active R 阻止 T 的推进；不可认证状态保留并给出精确 error；三平台故障矩阵证明 durable 前不释放 OS lock、durable 后才删除记录。
