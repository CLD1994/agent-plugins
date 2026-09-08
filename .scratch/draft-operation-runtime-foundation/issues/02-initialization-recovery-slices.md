# 确定初始化与初始化恢复的实施边界

Status: ready-for-human
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-foundation/map.md
Type: grilling
Labels: wayfinder:grilling

## 问题

在[路线图](../map.md)确定的集成安排下，如何把初始化的写前平台证明、现场认证、新建／续建、
marker 提交和提交后恢复清理拆成可单独验证的阶段，并确定哪些阶段必须配套验收后才能交付？

这不是重新选择初始化行为；问题的答案是一份经用户确认的实施边界、依赖和验收安排，
不是新增 descriptor 格式、公开协议或平台例外。

## 讨论依据与所需产物

先读取父任务 `Design-Refs` 指向的七份设计规格，重点沿
[初始化状态表](../../../plugins/draft/docs/spec/runtime/initialization-and-platform.md#初始化状态表)、
[marker 发布、cleanup 与恢复](../../../plugins/draft/docs/spec/runtime/initialization-and-platform.md#marker-发布cleanup-与恢复)和
[证据优先恢复表](../../../plugins/draft/docs/spec/runtime/recovery.md#证据优先恢复表)追踪同一现场：

- 明确可复用的真实平台和持久化协作者，以及必须共同改变的认证、记录、清理和结果消费者；依据代码事实，不依据模块名称猜测。
- 把 marker 前后中断、合法 pending、稳定损坏、认证后变化和保存不确定分别归到 owner 既有分区，
  写清每个阶段验证哪个分区、由谁保存证据、哪个入口可以继续以及调用方观察到什么。
- 说明三类平台所需能力的验证来源与可执行门禁；现有环境没有证据的部分如实记录，不降低规格保证。
- 产出拟发布实施任务的标题、交付结果、阻塞关系和失败注入验收；明确最终集成与验证任务及其阻塞项。
- 核实与[页面 YAML 读写](../../draft-page-frontmatter/issues/01-yaml-page-io.md)及
  [请求校验与拒绝](01-request-validation.md)的实际实施依赖。讨论本身无需等待两项代码实施完成。

事实调查由 Agent 完成；切片与阶段验收由用户确认。若发现 owner 未定义的状态或冲突，单独报告，
不在实施路线里补充产品语义。
