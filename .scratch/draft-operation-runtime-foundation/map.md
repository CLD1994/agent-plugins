# Draft runtime 实施路线图

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
Parent: .scratch/draft-operation-runtime-foundation/spec.md
Labels: wayfinder:map

## 目的地

明确[Draft operation runtime 基础实施任务](spec.md)的实施切片、真实阻塞关系和阶段／整体验收，
使执行者能沿现行七份 runtime owner 完成实现，并清楚知道需要哪些领域任务配合。

## 说明与固定安排

- 行为只从[当前规格入口](../../plugins/draft/docs/spec/index.md)及 owner 读取；本图只组织实施路线。
- 用户已确认 runtime 使用独立实施分支分阶段完成，配套链路验收后再合入 `main`。各阶段验证自己交付的契约；
  最终合入还必须有覆盖所有相关切片的集成与验证任务，追踪正常、失败、重试、恢复和可观察结果。建立该任务及其
  阻塞边属于路线拆分的必要产物，不能把若干局部门禁通过当作整条链路完成。
- 已批准的首批实施入口是[页面 YAML 读写](../draft-page-frontmatter/issues/01-yaml-page-io.md)和
  [请求校验与拒绝](issues/01-request-validation.md)，二者无阻塞边。前者是本父任务之外的领域工作；后者直接归父任务。
  能独立验收的页面格式改动单独交付；共享代码的并行修改仍需隔离及整合核验。
- 用 `wayfinder` 逐项澄清决策；需要人类讨论的任务使用 `grilling` 与 `domain-modeling`，实施任务拆分使用
  `to-tickets`。决策答案记在对应任务的评论中，关闭后本图只保留摘要链接。
- [根锁与恢复](issues/04-root-lock-and-recovery.md)是已批准的独立实施任务，作为页面 YAML 实际写入接入的前置；
  不阻止其纯解析/读取/计划工作，也不代表初始化或首条普通操作的全部责任已经完成。
- 本图的决策任务与生产实施任务分开。未决的人类协同任务标为 `ready-for-human`；
  不把讨论入口当作可自动派遣的代码任务。读取或更新状态遵循[本地 tracker 规则](../../docs/agents/issue-tracker.md)。
- 本图只组织路线，各决策任务和实施任务独立维护结果；后续任务必须先认领，讨论结果由用户确认。

## 已完成的决策

尚无经本图关闭的决策任务。上面的固定安排是本图建立时的已批准输入。

## 交付入口与整体验收

实施切片、前置交付和跨领域接缝统一从[运行时与 YAML 垂直交付路线](../draft-runtime-tracer-bullets/spec.md)进入；该路线的[第 12 票](../draft-runtime-tracer-bullets/issues/12-final-integration.md)负责已覆盖切片的最终整合与故障验收。

初始化的阶段与共用责任在[初始化边界决策](issues/02-initialization-recovery-slices.md)维护；首条普通操作及其验收在[普通操作决策](issues/03-status-operation-recovery-slices.md)维护。后续实施票通过各自依赖引用这些决策结果。

本路线图、runtime 总任务、YAML 总任务和每张交付票分别按自己的完整范围验收。相同实现沿同一工作记录继续；总任务关闭前还须核对尚未覆盖的领域入口和 runtime 行为。

## 尚未明确

在初始化及首条普通操作的共同责任确定后，需要继续核对其余请求接入时的具体差异。
查询／Check、结构操作、结论与 Trash／Restore／Handoff 的现有实现可能产生额外的跨入口依赖和验证工作；
目前的有限调查不足以确定这些工作应拆成哪些独立切片。具体问题一旦能够准确表述，就转入独立决策任务。

## 范围之外

生产代码实施不在本图的决策会话内完成；页面领域合同的独立实现使用对应领域入口。
产品行为变更须先走 owner 的规格流程，本图不改变字段、状态、错误、平台保证或恢复语义。

## Comments

2026-09-06：用户确认首批两张实施任务的粒度与无阻塞安排，并确认 runtime 的独立分支配套验收方式。

2026-09-06：用户批准新增根锁与恢复前置修复，登记任务 04；沿独立分支、完整配套验证后整合，不将该授权推广为整个 runtime 重写。
