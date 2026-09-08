# 10: 初始化及初始化中断恢复闭环

Status: ready-for-human
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 在人类确认的初始化边界内，先证明平台能力，再通过初始化专用门禁建立根目录标记；如果过程中断，后续初始化能够依据已认证材料安全继续或明确阻断，不猜测初始页面集合，也不让普通操作偷偷接管初始化现场。

Blocked by:
  - [02: 写入和恢复共享根目录排他锁](02-root-lock-admission.md)：完成初始化后清理所需的运行期根锁前置。
  - [初始化实施边界决策](../../draft-operation-runtime-foundation/issues/02-initialization-recovery-slices.md)：由用户确认阶段划分、共用责任和验收范围。

- [ ] 缺少必要平台能力时在任何写入前返回平台不支持，且不留下半成品。
- [ ] 标记发布前后的中断、已有现场、未知版本、损坏、认证后变化和保存不确定分别得到确定结果。
- [ ] 标记耐久发布后，清理使用运行期根锁、认证的操作记录和精确材料清单；普通恢复不会接管无标记的初始化现场。
- [ ] `created`、`resumed`、已完成和阻断结果在核心与命令行接口中一致，重复调用不重复创建业务内容。

**停止条件:** 初始化页面集合、模板内容或未定义的恢复责任没有人类确认时停止。
