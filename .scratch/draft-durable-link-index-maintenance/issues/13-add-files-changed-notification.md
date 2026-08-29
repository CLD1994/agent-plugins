# 13: 接入外部文件变更 notification

**What to build:** 提供调用方向 Draft 声明根内文件已在 Draft 写命令之外发生变化的公共入口，并把它作为独立 maintenance 协调器的薄适配器，而不是变化清单或通用事件框架。

**Blocked by:** 10: 实现独立 `link-index.update` maintenance 协调器.

Status-Schema: triage
Status: needs-triage
Tracker-State: closed
Resolution: superseded
Promoted-to: plugins/draft/docs/spec/runtime/authority-and-projections.md
Base-Spec: 1fb633320e1e692ac6e706d826d88e4ad49e3a68
Affects:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
Parent: .scratch/draft-durable-link-index-maintenance/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Dispatch: blocked
Dispatch-Diagnostic: this notification brief contains a legacy delivered/JSON contract; the signed Q32 owners supersede it and its conflict must not be dispatched or used to choose ABI.

- [ ] 公共命令为 `draft notify files-changed --root ROOT`，candidate 和 registered 根都允许。
- [ ] 命令不接收路径、变更类型、重命名对、标准输入清单或 `--dry-run`；调用方只声明“根内发生过外部变化”。
- [ ] 根选择和 marker 验证完成后直接调用 notification 用例，不推进回收生命周期、不自动执行 recovery，也不建立持久事件、消费者注册或待消费队列。
- [ ] 已有 operation 持锁时索引消费者不处理，notification 仍视为已经送达。
- [ ] 已分类的索引维护结果由 notification 边界吸收；断言、类型错误、未知异常和其他程序缺陷不得被宽泛捕获。
- [ ] 有效根上的 notification 固定退出码 `0`；JSON 结果只含 `result_type: files_changed_notification`，普通输出只表达通知已送达。
- [ ] notification 输出不包含 warning、revision、变化文件数、operation ID、内部路径或索引维护结果。
- [ ] 参数错误、根选择失败、无有效 Draft 根和 marker 损坏仍按真实错误协议报告。
- [ ] 重复通知且根事实没有变化时，不产生 maintenance journal 或新 revision。
