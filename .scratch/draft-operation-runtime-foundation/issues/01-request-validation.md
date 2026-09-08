# 无效请求在读取 Draft 根之前被拒绝

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-foundation/spec.md

## 交付结果与范围

CLI 和非 CLI 公共调用使用同一套封闭请求校验规则。请求无效时，在选择、读取或认证 Draft 根之前
返回规定的单一错误；不形成计划、不推进生命周期，也不产生持久或外部副作用。

本任务交付从调用方输入到拒绝结果的完整路径。合法输入必须被正确接受并交给其所属入口，
但本任务不承担这些入口后续业务执行、查询游标续页、持久提交或恢复状态机的完整实现。

## 父任务与权威来源

- 父任务：[Draft operation runtime 基础实施任务](../spec.md)。本任务只收窄父范围，不修改父任务。
- 从[上下文路由](../../../CONTEXT-MAP.md)、[Draft 词汇](../../../plugins/draft/CONTEXT.md)和[规格入口](../../../plugins/draft/docs/spec/index.md)读取当前行为。
- [运行时公共接口](../../../plugins/draft/docs/spec/runtime/interface.md)：完整读取公开入口、共用值、输入校验、各封闭 request、CLI 语法及 mode 规则；它是输入规则的唯一来源。
- [准入与操作计划](../../../plugins/draft/docs/spec/runtime/admission-and-effects.md)：request shape 先于 root 和所有副作用的顺序与零写边界。
- [公共结果、错误与可观测性](../../../plugins/draft/docs/spec/runtime/errors-and-observability.md)：请求错误的 envelope、字段定位、reason、退出码和输出通道。

本任务不复制请求字段全集或错误登记表；实现与测试都必须能对应到上述 owner 的具体规则。

## 实施顺序与覆盖

1. 追踪 CLI token／公共调用值进入请求、root 选择、runtime 准入和错误输出的实际路径；确认哪些验证目前发生在读取事实之后。
   如需先隔离共同校验责任，先做保持行为不变的预重构并验证；具体模块和内部接口是实施选择。
2. 从接口 owner 推导先失败的校验测试，涵盖每类公共入口的合法形状与全部拒绝类别，再连接 CLI 与公共调用。
   对多个错误同时出现的输入，用 owner 的既定优先级检验唯一结果，不能只测试每个错误单独出现。
3. 通过真实入口集成验证错误输出；通过受接口约束的替身证明拒绝发生在 root 选择、领域读取及副作用协作者之前。

| 输入分区 | 本任务必须验证的结果 |
| --- | --- |
| request 无效，root 尚未取得、合法或无效 | 同一个请求错误；不选择、读取或认证 root，后续协作者均不获得执行机会。 |
| request 同时包含多项问题 | 按接口 owner 的优先级只选一个错误；字段定位保留原始 token／数组位置。 |
| request 合法，root 无效 | 请求校验通过后才进入根选择／认证，返回根错误；不把它改成请求错误。 |
| request 合法，后续协作者可接收 | 交付规范化后的请求及所属 mode；不在输入层推导 parent、managedness、计划、Check 或其他领域事实。 |
| 合法帮助或版本请求 | 按接口 owner 成功退出，不选择 root 或读取仓库；混用其他参数的情况仍按其规则拒绝。 |

## 验收条件

- [ ] 普通操作、Trash、Restore、查询、通知、恢复以及初始化的公开输入约束分别有覆盖；不能仅让 status 命令通过。
- [ ] 类型、字段、值、路径、枚举、UUID、列表成员和跨字段组合遵守接口 owner；普通选择列表与 Check 筛选列表按各自规则处理重复值及规范化。
- [ ] 参数前后顺序、两种长选项赋值方式、重复参数、选项终止符及帮助／版本组合，均按 CLI 合同处理。
- [ ] 同一无效语义从 CLI 与公共调用进入时产生相同的机器分类及逻辑字段定位；CLI 专属 token 使用接口 owner 规定的位置表达。
- [ ] 无效 request 与无效 root 同时出现的反例证明：返回请求错误，且根选择／读取、计划、自动阶段、维护、恢复和外部调用均未发生。
- [ ] 对合法输入保留正向测试，防止通过拒绝全部请求满足零副作用断言；通知和恢复的无业务字段边界也有正反例。
- [ ] 请求错误按 owner 输出规定的单一 JSON／人类结果、退出码和通道；不泄露底层异常或未经允许的输入内容。
- [ ] 按[插件测试规范](../../../plugins/draft/AGENTS.md)分别运行受影响的核心与适配层单元、集成及静态检查，记录结果与未运行项；审计所有共用此校验责任的入口。

## 依赖、集成与停止条件

本任务与[页面头部的读取、编辑和生成统一使用 YAML](../../draft-page-frontmatter/issues/01-yaml-page-io.md)没有阻塞边。
路径值或错误投影如有共同消费者，使用同一权威规则，不能建立互相漂移的两份协议。

runtime 的集成安排见[实施路线图](../map.md)。本任务完成只证明输入拒绝路径，不表示整个公共 runtime
已经可用；它不负责为尚未接通的合法业务路径新增临时错误或兼容协议。

发现 owner 未定义的输入、相互冲突的错误优先级、必须读取领域事实才能作出的资格判断，或需要扩张到新的
业务执行语义时，停止相关部分并列出具体规则和反例。资格判断留在对应领域／runtime 阶段，不移入请求校验器。

## Comments

交付切片见[第 01 票：公共请求校验](../../draft-runtime-tracer-bullets/issues/01-request-validation.md)。两个入口引用同一执行记录，分别按各自完整范围验收。

2026-09-06：用户批准本任务粒度及与页面 YAML 任务互不阻塞的安排。
