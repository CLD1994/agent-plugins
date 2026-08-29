# 平台搭建与故障恢复参考

本页是插件适配层的导航消费者。适配层只认证宿主事实、路由核心调用并投影已认证结果；其责任边界由 [P-BOUNDARY：责任边界](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界) 定义。本页不拥有行为合同，也不复制 owner 的状态表、字段、错误、重试、恢复或清理协议。

## 身份与入口导航

公开插件身份和发布入口必须由 [P-IDENTITY：身份](../docs/spec/delivery/plugin-and-entrypoints.md#身份) 统一维护。插件入口对核心调用的路由、宿主事实的认证和结果投影遵循 [P-BOUNDARY：责任边界](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界)。

具体安装步骤、宿主变量名、入口参数和发布产物字段的适配层 ABI 当前没有逐项批准的 owner，状态为 `unresolved`；不能从实现、测试或历史文档补定。该边界仍受 [P-IDENTITY](../docs/spec/delivery/plugin-and-entrypoints.md#身份) 与 [P-BOUNDARY](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界) 约束。

## 适配层边界

宿主提供的 root、bridge、binding 和 SessionStart 事实，只有在适配层完成认证后才能交给核心；适配层不得重新定义领域、runtime 或错误 ABI，不得安排重试、隐式调用 `recover()`，也不得把错误投影为成功。以上行为约束均来自 [P-BOUNDARY：责任边界](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界)。

以下适配层细节没有当前正式 owner 的字段级批准，保持 `unresolved`，本页不提供协议形状：

| 适配层项目 | 状态 | 约束来源 |
| --- | --- | --- |
| host 名称、环境变量和 SessionStart 输入 envelope | `unresolved` | 只能按 [P-BOUNDARY](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界) 认证宿主事实；本页不选择字段或解析顺序。 |
| bridge/binding 的路径、载荷 schema、安装键、锁和 reset 范围 | `unresolved` | 只能按 [P-BOUNDARY](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界) 作为宿主事实边界；不能由实现或测试反推持久协议。 |
| 适配层命令参数、输入限制和持久化清理顺序 | `unresolved` | 适配层不得越过 [P-BOUNDARY](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界) 扩张 runtime 责任；缺少 owner 时停止补定。 |

## 诊断投影

检测层产生的机器诊断事实由 [P-DIAGNOSTICS：诊断](../docs/spec/draft/query-check-and-output.md#诊断) 持有；delivery 页面只投影这些事实，并遵循 [诊断投影边界](../docs/spec/delivery/plugin-and-entrypoints.md#诊断投影)。普通输出、JSON 和 notification 必须保持同一机器结论，不能在本页重新选择 `Outcome` ABI。

诊断字段名、字段顺序、可选性、编码、notification envelope，以及适配层错误到宿主呈现的精确映射均为 `unresolved`，除非对应 owner 已明确批准；不可用人类文案、实现或测试夹具替代 [P-DIAGNOSTICS](../docs/spec/draft/query-check-and-output.md#诊断) 与 [R-ERRORS：公共结果与错误投影](../docs/spec/runtime/errors-and-observability.md#公共结果与错误投影)。不可认证事实必须保留为可诊断失败，不得被投影成空结果或成功。

## 运行时导航

本页只把操作主题引回唯一 runtime owner；各 owner 的完整状态、失败、恢复和公开 ABI 不在这里重述。

| 主题 | 唯一 owner |
| --- | --- |
| 公开调用、request 与 mode | [R-INTERFACE：运行时接口](../docs/spec/runtime/interface.md#运行时接口) |
| 初始化与平台能力 | [R-INIT：初始化与平台前置](../docs/spec/runtime/initialization-and-platform.md#初始化与平台前置) |
| 准入、DryRun 与零写 | [R-ADMISSION：准入与操作计划](../docs/spec/runtime/admission-and-effects.md#准入与操作计划) |
| authority 提交与派生 projection | [R-AUTHORITY：Authority 提交与派生投影](../docs/spec/runtime/authority-and-projections.md#authority-提交与派生投影) |
| 自动生命周期与 handoff | [R-LIFECYCLE：自动生命周期与交接](../docs/spec/runtime/lifecycle-and-handoff.md#自动生命周期与交接) |
| 显式恢复与保留现场 | [R-RECOVERY：显式恢复与 RecoveryOutcome](../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) |
| 结果、错误和 notification 投影 | [R-ERRORS：公共结果与错误投影](../docs/spec/runtime/errors-and-observability.md#公共结果与错误投影) |

## 故障恢复边界

核心 recovery 的准入、证据、前后状态和保留现场规则由 [R-RECOVERY：显式恢复与 RecoveryOutcome](../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) 持有；自动生命周期及重试拥有者由 [R-LIFECYCLE：自动生命周期与交接](../docs/spec/runtime/lifecycle-and-handoff.md#自动生命周期与交接) 持有。适配层不得自行重试或隐式恢复，遵循 [P-BOUNDARY：责任边界](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界)。

bridge reset、会话解绑、宿主重载、外部 handoff 调度和适配层 cleanup 的具体命令、字段与返回 envelope 尚未形成已批准 ABI，全部保持 `unresolved`；本页只提供上述 owner 导航，不把这些未决项解释为成功、失败或 cut-over 条件。
