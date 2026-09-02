# 平台搭建与故障恢复参考

本页只为插件适配层提供导航。适配层认证宿主事实、构造核心请求并投影核心结果；它不拥有 Draft 的领域、runtime 或错误合同。

## 插件身份与适配边界

- 公开插件身份和发布入口遵循 [P-IDENTITY：身份](../docs/spec/delivery/plugin-and-entrypoints.md#身份)。
- root、bridge、binding 和 SessionStart 只作为宿主事实，经适配层认证后交给核心；路由边界和依赖方向遵循 [P-BOUNDARY：责任边界](../docs/spec/delivery/plugin-and-entrypoints.md#责任边界)。
- 宿主专属安装键、环境变量、bridge/binding 载荷、reset、解绑、重载和 cleanup 属于宿主实现或另行定义的集成协议，不属于 Draft 公共合同。本页不为这些协议选择字段、顺序或返回 envelope。

适配层保留核心已认证的机器语义，不安排重试、不隐式调用 `recover()`，也不把错误投影成成功。宿主专属协议的认证和失败处理由宿主集成边界负责。

## 核心合同导航

| 主题 | 唯一 owner |
| --- | --- |
| 公开入口、request、mode 和 root 选择 | [运行时公共接口](../docs/spec/runtime/interface.md#运行时公共接口) |
| 初始化与平台能力 | [初始化与平台前置](../docs/spec/runtime/initialization-and-platform.md#初始化与平台前置) |
| 准入、DryRun 与零写 | [准入与操作计划](../docs/spec/runtime/admission-and-effects.md#准入与操作计划) |
| authority 提交与派生资料 | [Authority 提交与派生投影](../docs/spec/runtime/authority-and-projections.md#authority-提交与派生投影) |
| 自动生命周期与 handoff | [自动生命周期与交接](../docs/spec/runtime/lifecycle-and-handoff.md#自动生命周期与交接) |
| 显式恢复与保留现场 | [显式恢复与 RecoveryOutcome](../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) |
| 结果、错误与 notification 投影 | [公共结果、错误与可观测性](../docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性) |
| 页面、状态、关系、资源和结论诊断 | [Check 诊断合同](../docs/spec/draft/query-check-and-output.md#check-诊断合同) |

核心请求、结果、诊断和恢复均回链上述 owner；适配层不得复制字段表、状态机、时间边界、错误码或 cleanup 授权。无法认证宿主或核心事实时保留可诊断失败，并沿核心 owner 的结果投影返回。
