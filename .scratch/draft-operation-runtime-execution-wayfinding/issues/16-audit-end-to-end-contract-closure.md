# 审查机器合同的端到端闭合

Type: prototype
Status-Schema: triage
Status: needs-triage
Tracker-State: closed
Resolution: applied
Promoted-to: plugins/draft/docs/spec/index.md#draft-当前规格
Base-Spec: a8ccb8c3e2945ca816fefbb236d97edf0a6cd0b4
Affects:
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/draft/structure.md
  - plugins/draft/docs/spec/draft/conclusions.md
  - plugins/draft/docs/spec/draft/lifecycle.md
  - plugins/draft/docs/spec/draft/trash-and-restore.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
Parent: .scratch/draft-operation-runtime-execution-wayfinding/map.md
Scope: child-issue-only
Current-Owner: plugins/draft/docs/spec/index.md
Dispatch: blocked
Dispatch-Diagnostic: 本条目已作为过程审计应用到当前规格入口；关闭条目不可派遣，且不作为行为 authority。

本文是过程审计证据，不是行为 authority。当前行为只由 `plugins/draft/docs/spec/` 下的正式 owner 页面定义；`.scratch` wayfinding 与本 issue 只记录 tracker、provenance 或闭合证据，不能补写、覆盖或裁决行为。

## 审计范围

本审计沿每个公开入口的权威输入、派生、领域副作用、失败、重试与恢复路径，核对它们是否在当前 owner 页面中得到唯一可观察结果。下表是已应用的闭合导航，不复制各 owner 的行为合同。

## 入口端到端闭合矩阵

| Entry-ID | 当前闭合状态 | 领域或公开 owner | 闭合依据 | 验证位置 |
| --- | --- | --- | --- | --- |
| `init` | closed | [R-INIT](../../../plugins/draft/docs/spec/runtime/initialization-and-platform.md#初始化与平台前置) | 初始化状态表定义平台、descriptor、marker、cleanup 与错误分区；公共 `InitOutcome` 由 R-ERRORS 定义。 | [R-INIT 可证伪验证](../../../plugins/draft/docs/spec/runtime/initialization-and-platform.md#可证伪验证) |
| `create` | closed | [D-CREATE-EDIT](../../../plugins/draft/docs/spec/draft/page-edits.md#创建状态与关系编辑) | 页面 after-state 由领域 owner 定义，准入/逻辑提交由 R-ADMISSION，公开 operation/error 投影由 R-ERRORS。 | [D-CREATE-EDIT 验证](../../../plugins/draft/docs/spec/draft/page-edits.md#验证) |
| `move` | closed | [D-MOVE](../../../plugins/draft/docs/spec/draft/structure.md#move) | mapping、链接闭包与领域提交由结构 owner 定义；R-ADMISSION、R-AUTHORITY、R-RECOVERY 和 R-ERRORS 分别定义准入、耐久、恢复和投影。 | [D-MOVE owner](../../../plugins/draft/docs/spec/draft/structure.md#move) |
| `promote` | closed | [D-PROMOTE](../../../plugins/draft/docs/spec/draft/structure.md#promote) | 同父、保名 mapping 与自动 collapse 是同一结构提交；runtime owner 统一处理准入、耐久、恢复和投影。 | [D-PROMOTE owner](../../../plugins/draft/docs/spec/draft/structure.md#promote) |
| `collapse` | closed | [D-COLLAPSE](../../../plugins/draft/docs/spec/draft/structure.md#collapse) | 显式与自动 collapse 共用 mapping、零写和恢复边界；runtime owner 定义提交与失败投影。 | [D-COLLAPSE owner](../../../plugins/draft/docs/spec/draft/structure.md#collapse) |
| `status` | closed | [D-STATUS](../../../plugins/draft/docs/spec/draft/status-and-relations.md#页面状态与关系) | 状态与页面头部 after-state 由领域 owner 定义；D-CREATE-EDIT/R-ADMISSION/R-ERRORS 定义请求、提交和投影。 | [D-STATUS 验证](../../../plugins/draft/docs/spec/draft/status-and-relations.md#验证) |
| `relation` | closed | [D-STATUS](../../../plugins/draft/docs/spec/draft/status-and-relations.md#页面状态与关系) | 主动/反向关系、图约束和最后一条取代关系的状态由领域 owner 定义；请求和原子提交由 D-CREATE-EDIT/R-ADMISSION 闭合。 | [D-STATUS 验证](../../../plugins/draft/docs/spec/draft/status-and-relations.md#验证) |
| `generate-conclusions` | closed | [D-CONCLUSIONS](../../../plugins/draft/docs/spec/draft/conclusions.md#结论快照行为合同) | 成员资格、制品形状、结构窄改写和零写边界由领域 owner 定义；准入和公共投影由 runtime owner 定义。 | [D-CONCLUSIONS 验证](../../../plugins/draft/docs/spec/draft/conclusions.md#失败零写恢复与验证) |
| `adopt` | closed | [D-LIFECYCLE](../../../plugins/draft/docs/spec/draft/lifecycle.md#根生命周期) | candidate→registered、完整 Check 前置与不改写页面的提交由领域 owner 定义；公共结果由 R-ERRORS 定义。 | [D-LIFECYCLE 验证](../../../plugins/draft/docs/spec/draft/lifecycle.md#验证) |
| `trash` | closed | [D-TRASH-RESTORE](../../../plugins/draft/docs/spec/draft/trash-and-restore.md#行为合同) | package、active membership、领域期限与冲突边界由领域 owner 定义；handoff 和耐久恢复由 runtime owner 定义。 | [D-TRASH-RESTORE 验证](../../../plugins/draft/docs/spec/draft/trash-and-restore.md#验证) |
| `restore` | closed | [D-TRASH-RESTORE](../../../plugins/draft/docs/spec/draft/trash-and-restore.md#行为合同) | Restore 时间、package 认证、冲突、消费顺序与零写由领域 owner 定义；pending 收敛由 R-RECOVERY 定义。 | [D-TRASH-RESTORE 验证](../../../plugins/draft/docs/spec/draft/trash-and-restore.md#验证) |
| `read` | closed | [D-QUERY](../../../plugins/draft/docs/spec/draft/query-check-and-output.md#查询行为合同) | 查询来源范围、items/blockers、cursor、排序和 Check 门禁由查询 owner 定义；公开 envelope 由 R-ERRORS 定义。 | [D-QUERY 验证](../../../plugins/draft/docs/spec/draft/query-check-and-output.md#验证) |
| `notification` | closed | [R-ERRORS](../../../plugins/draft/docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性) | 唯一 `MaintenanceOutcome`、错误 envelope 和零额外字段由公共结果 owner 定义；准入和维护边界由 R-ADMISSION 定义。 | [R-ERRORS owner](../../../plugins/draft/docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性) |
| `recover` | closed | [R-RECOVERY](../../../plugins/draft/docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) | 证据优先分类、forward/rollback/cleanup、`RecoveryOutcome` 及调用方动作由 recovery owner 与 R-ERRORS 共同闭合。 | [R-RECOVERY 可证伪验证](../../../plugins/draft/docs/spec/runtime/recovery.md#可证伪验证) |

## 闭合结论

- 公开 request、operation identity、结果联合、错误 envelope、cursor、排序和字段可选性由当前 runtime interface、query 与 errors owner 共同定义；内部 record、摘要、phase、mapping 与 worker 保持私有实现边界。
- 各领域入口的计划成员、原子提交、失败关闭和显式恢复边界由对应 Draft owner 与 runtime owner 共同定义。没有入口需要执行者从 tracker、实现、测试或历史规格选择行为。
- 本 issue 的关闭只说明过程审计已应用；它不改变任何 owner，也不授权派遣。后续行为变更必须先修改正式 owner，再按治理合同重新核验影响范围。

## 后置事项

仓库级规格检查程序和 CI 调用不属于当前规格收敛的完成条件。Draft CLI 达到可用版本后，再单独定义检查入口、触发范围和失败协议；当前阶段不得要求尚未完成的 Draft CLI 先证明自身规格完整性，也不得仅因 `.github/workflows/` 不存在而阻断本轮规格收敛。
