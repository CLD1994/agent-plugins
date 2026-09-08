# 09: 首个经确认的 YAML 写入操作

Status: ready-for-human
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/recovery.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 对人类确认的第一种页面业务操作，完成从请求校验、页面事实认证、预演计划、根锁内重新读取，到操作记录、页面和唯一事实来源耐久保存、授权清理、结果投影与显式恢复的完整路径。

Blocked by:
  - [03: 恢复与 Trash 清理只处理已证明归属的现场](03-recovery-cleanup-ownership.md)：完成恢复与清理归属认证。
  - [04: 只读、预演和通知在并发写入下保持安全](04-read-dryrun-maintenance.md)：完成只读与预演的并发边界。
  - [07: 维护过程使用一致的根目录状态快照](07-marker-consistent-maintenance.md)：完成一致快照下的维护。
  - [08: YAML 元数据变更先形成可验证预演](08-yaml-metadata-plan-dryrun.md)：完成可验证的元数据预演计划。
  - [首条普通操作决策](../../draft-operation-runtime-foundation/issues/03-status-operation-recovery-slices.md)：由用户确认第一条业务操作及其配套验收。

- [ ] 选定的业务操作在核心调用和命令行接口中使用同一请求、锁、记录、提交、清理和结果协议。
- [ ] 锁内重新认证失败、外部变化、保存不确定、并发占用和损坏分别返回设计规格规定的程序结果，并保留可恢复现场。
- [ ] 成功只在页面、操作记录、唯一事实来源和必要派生结果达到规定耐久点后报告；公开返回前释放根目录排他锁。
- [ ] 进程终止、重复恢复、重复调用和真实页面再次读取都有集成证据。

**停止条件:** 人类尚未确认首条操作，或需要改变关系、结论、回收区操作（Trash）或初始化的业务语义时停止。
