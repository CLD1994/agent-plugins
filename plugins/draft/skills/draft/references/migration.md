# 迁移参考

这是一份建议性的迁移顺序，不是 Draft 行为合同。每一步开始前先读取列出的正式 owner；请求形状、前置条件、成功结果和失败边界都以 owner 为准，本文件不固定命令参数或登记门禁。

每一步先由所列正式 owner 根据已认证事实判定是否适用。适用步骤只有在 owner 要求的动作成功且必要复核完成后才能进入下一步；若已认证事实证明无需动作，明确记录为跳过并继续。发生失败、事实不足、恢复现场待决（recovery pending）或用户语义未决时，保存诊断并停止，不自行补答案。迁移的最终完成条件是：所有适用步骤均已完成或明确跳过，且根状态转换由其 owner 成功确认。

## 建议顺序

1. **建立独立的 candidate 根**

   目标：为待保留内容准备一个从空现场开始的 candidate 根，保留来源内容作为输入。

   读取：[Draft 根生命周期](../../../docs/spec/draft/lifecycle.md#根生命周期) 与 [初始化与平台前置](../../../docs/spec/runtime/initialization-and-platform.md#初始化与平台前置)。

   完成标准：candidate 根的建立动作已由链接 owner 成功确认并完成必要复核；已认证事实若证明无需动作，则已明确记录跳过。

2. **建立页面骨架并迁入已确认内容**

   目标：为每个待迁入主题建立当前页面角色、正文和普通链接。

   读取：[页面角色与导航](../../../docs/spec/draft/pages-and-navigation.md#页面角色与导航)、[页面状态与关系](../../../docs/spec/draft/status-and-relations.md#页面状态与关系) 与 [页面编辑](../../../docs/spec/draft/page-edits.md#创建状态与关系编辑)。

   完成标准：页面建立与内容保存动作已由链接 owner 成功确认并完成必要复核；已认证事实证明无需某项动作时，明确记录跳过。

3. **整理状态、关系和结构**

   目标：用当前领域入口表达状态、正式关系、改名、移动、升格或降级，让相关页面事实和导航保持同一变更边界。

   读取：[页面状态与关系](../../../docs/spec/draft/status-and-relations.md#页面状态与关系)、[创建、状态与关系编辑](../../../docs/spec/draft/page-edits.md#创建状态与关系编辑) 与 [结构操作](../../../docs/spec/draft/structure.md#move)。

   完成标准：适用的状态、关系或结构动作已由链接 owner 成功确认并完成必要复核；已认证事实证明无需动作时，明确记录跳过。

4. **检查迁移后的事实**

   目标：在每组内容或结构调整后检查页面、关系、导航、资源及适用 runtime 事实，并按直接负责的 owner 处理已认证问题。

   读取：[查询行为合同](../../../docs/spec/draft/query-check-and-output.md#查询行为合同) 与 [Check 诊断合同](../../../docs/spec/draft/query-check-and-output.md#check-诊断合同)。

   完成标准：Check 结果完整且没有未解决的 blocker，所有适用诊断已由对应 owner 处理并复核；已认证事实证明无需动作的项已明确记录跳过。

5. **按需生成结论制品**

   目标：在讨论内容确认后，由结论 owner 生成或更新结论制品，不手工重建其成员或排序。

   读取：[结论快照行为合同](../../../docs/spec/draft/conclusions.md#结论快照行为合同)。

   完成标准：结论制品的生成或更新已由链接 owner 成功确认并完成必要复核；已认证事实证明无需动作时，明确记录跳过。

6. **处理回收、恢复或运行期现场**

   目标：迁移过程中若需要回收、恢复或收敛待决现场，分别使用对应领域入口，并保留无法证明安全动作的现场。

   读取：[Trash 与 Restore](../../../docs/spec/draft/trash-and-restore.md#行为合同)、[显式恢复与 RecoveryOutcome](../../../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) 与 [运行时结果、错误与可观测性](../../../docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性)。

   完成标准：适用的回收、恢复或现场收敛动作已由对应 owner 成功确认并完成必要复核；已认证事实证明无需动作时，明确记录跳过。动作失败或 recovery pending 时，保存诊断并停止。

7. **复核并完成根状态转换**

   目标：在迁移内容、结构和检查事实都准备好后，按生命周期与准入 owner 处理 candidate 的登记；结果和失败处理以 owner 为准。

   读取：[Draft 根生命周期](../../../docs/spec/draft/lifecycle.md#根生命周期)、[准入与操作计划](../../../docs/spec/runtime/admission-and-effects.md#准入与操作计划) 与 [Check 门禁与副作用](../../../docs/spec/draft/query-check-and-output.md#check-门禁与副作用)。

   完成标准：candidate→registered 的根状态转换已由生命周期与准入 owner 成功确认并完成必要复核；未成功确认时迁移未完成。

只有前六步的所有适用项均已完成或明确跳过，且第七步的根状态转换成功时，迁移才算完成；否则保留诊断并停止。

迁移不搬入 `.draft/` 下的运行期记录、锁、索引或其他私有现场；这些内容由当前 runtime owner 管理。历史页面头部字段按 [页面状态与关系](../../../docs/spec/draft/status-and-relations.md#页面状态与关系) 与 [页面头部键与输出边界](../../../docs/spec/draft/conclusions.md#页面头部键与输出边界) 的正式定义处理，再继续检查。
