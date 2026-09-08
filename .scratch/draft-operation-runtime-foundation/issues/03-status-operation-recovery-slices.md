# 确定首条普通操作的提交与恢复闭环

Status: ready-for-human
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-foundation/map.md
Type: grilling
Labels: wayfinder:grilling

## 问题

在可认证根及共用恢复责任明确后，是否以 `status_set` 作为第一条普通操作验收路径？
若采用，哪些实施切片必须共同提供准入、纯计划、自动阶段、锁内重验、权威提交、结果投影与显式恢复，
才能证明一次写入的成功、失败、重试和恢复均闭合？

预期答案是经用户确认的首条路径、配套实施任务、阻塞边和端到端验收；不能把公共入口转发成功
或文件已经可见当作耐久提交已完成。

## 阻塞关系

Blocked by:
  - [确定初始化与初始化恢复的实施边界](02-initialization-recovery-slices.md)：确认共用责任与阶段验收。

这里依赖的是该决策的结果，不是要求初始化代码先全部写完；否则无法准确划分普通操作应复用还是补充的认证、记录与恢复责任。

## 讨论依据与所需产物

读取父任务 `Design-Refs` 指向的七份设计规格，并消费
[页面编辑](../../../plugins/draft/docs/spec/draft/page-edits.md)、
[状态与关系](../../../plugins/draft/docs/spec/draft/status-and-relations.md)以及
[Check 门禁](../../../plugins/draft/docs/spec/draft/query-check-and-output.md#check-门禁与副作用)已定义的领域事实。
领域依赖不扩张本任务的 runtime 父范围；需要领域实现时另列对应入口。

- 沿调用方请求、所消费事实、预演、自动阶段、最终计划、写入记录、内容与 authority、派生资料、
  清理及结果追踪同一次操作，核对正常、失败、重试和恢复消费者。
- 依据 owner 的既有状态表，明确候选／正式根、局部／完整闭包、有效／无效请求、live／stale／无记录、
  无变化／实际变化及耐久结果不确定时，哪些切片负责验证；不重新定义组合结果。
- 核实初始化任务交付的根、平台、认证和恢复责任能否直接消费；跨任务的失败现场必须由其真实恢复消费者验证。
- 给出第一条可演示路径的完整验收，以及它尚不能代表的其他入口；列出后续推广需要的明确调查，不能把一条路径通过视作全部 runtime 完成。
- 产出拟发布实施任务的标题、交付结果、真实阻塞边、先失败测试安排，以及包含失败注入与重复恢复的最终集成门禁。

这里只讨论切片和验收，不实施业务代码，不新增公开字段、临时错误或自动恢复入口。
