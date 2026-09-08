# 正常写入与恢复共用根锁并安全处理准备现场

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-foundation/spec.md

## 交付结果与边界

正常写入、显式恢复及会接触同一持久现场的生命周期和维护入口使用同一根锁责任。
恢复不能删除仍被活动写入者使用的准备目录、记录或锁；写入者必须能在发布操作记录之前
持锁重新读取事实、重建计划并完成门禁。准备、发布、清理和释放的每个崩溃窗口都应能够
按已有 owner 唯一分类，不能产生被正常恢复误删的活动现场。

本任务不改变页面、关系、结论或 Trash 的业务语义，不完成整个 runtime v1，也不代替
初始化内容、请求校验、首条普通操作和全量 authority/投影的其他实施任务。锁相关的私有布局、
句柄持有与必要准备/清理顺序由执行计划明确；不把当前 JSON 标记的行为当作规格。

## 权威来源

先读取[上下文路由](../../../CONTEXT-MAP.md)、[Draft 词汇](../../../plugins/draft/CONTEXT.md)和
[规格入口](../../../plugins/draft/docs/spec/index.md)，再按下列 owner 定义行为：

- [准入与操作计划](../../../plugins/draft/docs/spec/runtime/admission-and-effects.md)：live owner、stale record、无 record、try-lock、模式差异、锁内重验及发布顺序。
- [显式恢复](../../../plugins/draft/docs/spec/runtime/recovery.md)：不抢占 live owner，只有认证证据授权收敛或清理；证据不足、矛盾与耐久失败分别处理。
- [初始化与平台](../../../plugins/draft/docs/spec/runtime/initialization-and-platform.md)：live OS lock 能力证明、初始化和运行期锁的责任分界；不从本任务推定新的初始页面集合。
- [权威与派生投影](../../../plugins/draft/docs/spec/runtime/authority-and-projections.md)：私有记录边界、所有权证明、耐久清理和释放约束。
- [公共错误与结果](../../../plugins/draft/docs/spec/runtime/errors-and-observability.md)：锁/恢复相关既有机器码与输出，不另造公共字段。

架构理由见[受协调 writer](../../../plugins/draft/docs/adr/0001-coordinated-recovery-writers.md)、
[私有记录](../../../plugins/draft/docs/adr/0004-runtime-records-are-private.md)及
[恢复受阻与损坏](../../../plugins/draft/docs/adr/0009-distinguish-blocked-recovery-from-corruption.md)。

## 实施与验收

实施前必须有完整执行计划，沿锁取得、准备材料、锁内重读、记录发布、执行、失败、恢复和释放
追踪所有实际入口。计划明确状态模型和验证矩阵，区分已确认行为与私有实现建议；实施者逐个
行为分支先写失败测试，再实现及按需重构。

- [ ] 枚举当前所有写入、恢复、生命周期、维护和只读/预演接缝，证明它们不能绕过共同锁或错误地抢占 live owner。
- [ ] 进程持锁且尚未发布记录时，另一正常恢复和写入者按 owner 拒绝，不修改持锁者现场；读取和 DryRun 保持各自允许的只读行为。
- [ ] 锁内取得的是新事实和新计划，不能把 preflight 的快照当作最终认证；门禁失败不发布业务操作记录、不写业务内容。
- [ ] 覆盖准备前后、记录发布前后、材料删除、record unlink、目录 fsync 和锁释放的失败/崩溃；恢复不会以目录为空推断无主，不把可解释现场误报为损坏。
- [ ] 连续恢复、并发恢复及 normal writer/recover 的组合验证唯一动作、现场保留、耐久失败和重复调用结果。
- [ ] 锁能力不可证明、类型/身份变化和损坏使用 owner 的既有错误；不得靠 PID、mtime、等待时间或锁文件存在性猜活性。
- [ ] 单元测试使用受真实接口约束的替身；文件系统、进程互斥与崩溃窗口用独立集成测试；核心和适配层分开执行。
- [ ] 由独立评审核验生产改动和真实证据，之后与 YAML 写前 Check 组合验证；不能以锁模块局部通过声称 YAML 或整个 runtime 完成。

## 依赖与停止

本任务是[页面 YAML 读写](../../draft-page-frontmatter/issues/01-yaml-page-io.md)中实际写入接入的前置；
页面纯解析、正文读取和纯计划修复可独立继续。与[请求校验](01-request-validation.md)仍是不同责任。
完整 runtime 路线及独立分支整合安排见[实施路线图](../map.md)。

如果现有 owner 不能决定某种可观察行为或必须扩大到其他完整业务协议，停止相关部分并提供
具体状态与调用链；授权锁与恢复修复不等于授权自动迁移未知版本、清理无法证明归属的材料，
或重写其他领域状态机。

## Comments

2026-09-06：用户同意把正常写入与恢复共用的锁及其崩溃处理加入当前实施范围，作为实际写入的独立前置修复。

2026-09-07：用户确认两项可观察规则。(1) live owner 就是此刻持有平台 live OS lock 的写入者；不做 PID、mtime、锁文件存在性等额外活性证明。try-lock 失败，或无 record 但 OS lock live，都只表示有活的锁持有者。(2) 任何公开入口一旦返回给调用方，无论成功还是错误，都不得继续持有 OS 根锁。函数内部执行期间必须持锁；进程崩溃由内核释放活锁。
