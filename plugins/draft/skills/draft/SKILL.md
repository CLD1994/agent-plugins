---
name: draft
description: Use when a design or protocol discussion is becoming long, recursive, branched, vulnerable to context compact, or needs a durable Markdown memory graph shared with a human reader.
---

# Draft

把长期、递归、分叉的设计讨论维护成一个自包含的 Markdown 草稿。

Draft 是 [LLM-WIKI](references/llm-wiki.md) 的一种具体实现。首次使用或不熟悉其页面组织时，先读该 reference。
需要格式、状态、关系、资源或结论规则时，按需读取 [draft-format.md](references/draft-format.md)；需要迁入既有内容时，先读取 [migration.md](references/migration.md)。这些 reference 提供建议流程或主题导航；行为仍以其链接的正式 owner 为准。

## 前置条件与命令

需要当前环境提供 `uv`。通过当前 skill 的 runtime 执行命令：

```bash
uvx --isolated --from "<skill_root>/scripts/runtime" draft COMMAND ...
```

`<skill_root>` 由 skill 加载器提供；无法取得时报告环境错误。

可用入口：

- `init` 建立 candidate 或 registered 根；`adopt` 将已通过完整检查的 candidate 登记。
- `create` 创建叶页面；`status set` 修改状态；`relation add/remove` 修改正式关系。
- `move`、`promote`、`collapse` 执行结构变化。
- `check` 检查页面、关系、导航、资源和适用 runtime 事实。
- `query page|links|backlinks|relations|status|recoverable|check` 读取已认证结果。
- `generate-conclusions` 按需生成结论快照；`trash`、`restore` 管理回收与恢复。
- `notify files-changed` 接收外部批量编辑稳定后的变更通知；`recover` 显式收敛 pending 现场。

命令的封闭 request、mode、根选择和结果 ABI 见 [运行时公共接口](../../docs/spec/runtime/interface.md#运行时公共接口)。命令帮助只用于确认当前安装的入口拼写，不替代 owner 规则。

## 根与入口边界

- 新讨论使用 `draft init ROOT`；需要整理内容时，使用独立的空目录 `draft init --candidate CANDIDATE_ROOT`。初始化目标必须满足当前 owner 的空现场规则；来源内容不会原地接管为 candidate。
- 除 `init` 外，使用 `--root ROOT` 选择根；缺省选择由 runtime 完成。根选择、candidate/registered 转换和初始化边界见 [Draft 根生命周期](../../docs/spec/draft/lifecycle.md#根生命周期) 与 [初始化与平台前置](../../docs/spec/runtime/initialization-and-platform.md#初始化与平台前置)。
- `adopt` 用于将 candidate 根登记为 registered；登记资格、提交结果和失败处理读取 [准入与操作计划](../../docs/spec/runtime/admission-and-effects.md#准入与操作计划) 与 [Draft 根生命周期](../../docs/spec/draft/lifecycle.md#根生命周期)。
- 写入前读取相应领域 owner，确认所需事实已经认证；根处于待恢复现场或事实无法认证时保留现场，并转到 [显式恢复](../../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) 或对应的错误 owner。

## 日常维护

每次用户确认、否定、提出关键反例或打开新分支时：

每一步只有在链接的正式 owner 要求的动作成功且必要 Check/复核完成后才算完成；未满足时不进入下一步。

1. 读取根索引和相关页面，确定受影响的最小页面集合。
   完成标准：受影响的最小页面集合已确定；若根或页面事实不能认证，保存诊断并按硬停止条件停止。
2. 只改变标题、正文或普通链接时，直接编辑 Markdown；批量编辑稳定后可发送 `notify files-changed`，其输入、成功结果和错误边界见 [运行时结果、错误与可观测性](../../docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性)。
   完成标准：编辑内容已经保存；若发送通知，通知 owner 已确认成功并完成其要求的复核。owner 失败、事实不足或用户语义未决时，保存诊断并停止。
3. 改名、移动、升格、降级时调用 `move`、`promote` 或 `collapse`。路径、parent、导航、链接闭包和自动 collapse 由同一个 mapping 证明，结构入口见 [结构操作](../../docs/spec/draft/structure.md#move)。
   完成标准：结构 owner 要求的动作已成功确认，且其要求的复核已完成；事实不能认证或动作失败时，保存诊断并停止。
4. 修改状态或正式关系时调用 `status set`、`relation add` 或 `relation remove`；页面头部和关系不变量见 [页面状态与关系](../../docs/spec/draft/status-and-relations.md#页面状态与关系)，请求和提交计划见 [创建、状态与关系编辑](../../docs/spec/draft/page-edits.md#创建状态与关系编辑)。
   完成标准：状态或关系 owner 要求的动作已成功确认，且必要复核已完成；需要用户决定或 owner 失败时，保存诊断并停止。
5. 运行 `draft check`。Check 的诊断、结果完整性、筛选、分页、来源范围和正式根门禁见 [查询、检查与输出](../../docs/spec/draft/query-check-and-output.md#check-诊断合同)；按对应 owner 修复已认证的问题，语义选择交给用户。
   完成标准：Check 结果完整且没有 error 或未解决的 blocker；可行动诊断已由对应 owner 处理并复核，warning 已按 owner 或用户意图处理或明确保留。若事实不可认证、恢复现场待决、需要用户决定或任一 owner 返回失败，保存诊断并按硬停止条件报告阻断，本流程未完成。

日常维护只有在受影响内容已经保存、必要 owner 动作均成功、Check 结果完整且没有 error 或未解决的 blocker 时才成功结束；warning 可以按 owner 或用户意图保留。遇到需要用户决定、事实不可认证、恢复现场待决或 owner 返回失败时，保存诊断并报告阻断，不把流程标作成功。

页面、关系、资源和导航的公共字段不要在入口文档中重复维护；分别读取 [页面与导航](../../docs/spec/draft/pages-and-navigation.md#页面角色与导航)、[资源与链接](../../docs/spec/draft/resources-and-links.md#资源身份与链接观察)、以及上列状态/编辑 owner。

## 查询与结论

用 `draft query page --root ROOT PAGE` 做路由；需要 links/backlinks、relations、status、recoverable 或 check 时按 [查询行为合同](../../docs/spec/draft/query-check-and-output.md#查询行为合同) 选择入口。查询的来源范围、结果集合、分页和不完整事实处理统一由该 owner 拥有。

结果投影、JSON 编码、错误 envelope 和 notification 统一由 [公共结果、错误与可观测性](../../docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性) 拥有。

讨论结束并与用户确认后：

1. 按 [结论快照](../../docs/spec/draft/conclusions.md#结论快照行为合同) 运行 `draft generate-conclusions --root ROOT`。
   完成标准：结论 owner 已确认生成动作成功并完成必要复核；所需事实不能认证时，保留零写、保存诊断并停止。
2. 成员资格、排序、制品形状和重算边界读取结论 owner；不要在入口文档中推导或复制这些规则。
   完成标准：制品处理遵循结论 owner 的已认证规则，未自行补充语义；owner 失败或用户语义未决时，保存诊断并停止。
3. 若生成所需事实不能认证，保留零写并按结论 owner 与 runtime owner 处理；不要手工重建结论制品。
   完成标准：生成或更新已由 owner 成功确认并完成必要复核；事实不能认证、恢复现场待决或 owner 返回失败时，保存诊断并按硬停止条件报告阻断。

## 回收、恢复与宿主适配

`trash` 回收受管理页面子树；`restore` 按 [Trash 与 Restore](../../docs/spec/draft/trash-and-restore.md#行为合同) 读取输入、期限、package、冲突和提交边界。

完成标准：适用的回收或恢复动作已由链接 owner 成功确认并完成必要复核；已认证事实证明无需动作时，明确记录跳过。owner 失败、事实不能认证或恢复现场待决时，保存诊断并按硬停止条件报告阻断。

`draft recover --root ROOT` 是显式收敛入口；调用边界、成功结果、阻断条件和私有现场见 [显式恢复与 RecoveryOutcome](../../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome)。

完成标准：恢复 owner 已确认动作成功并完成必要复核；无法证明安全动作或 recovery pending 时保留现场、保存诊断并停止。

不要手工编辑 `.draft/` 下的 marker、record、authority、package、lock 或派生索引。authority、生命周期、清理和宿主适配的实现边界分别见 [Authority 提交与派生投影](../../docs/spec/runtime/authority-and-projections.md#authority-提交与派生投影)、[自动生命周期与交接](../../docs/spec/runtime/lifecycle-and-handoff.md#自动生命周期与交接) 和 [插件与入口](../../docs/spec/delivery/plugin-and-entrypoints.md#责任边界)。

## 何时拆分

语义上需要独立确认、否定或关闭的子问题拆成子页面；容量提醒只作为人工判断线索。需要结构变化时先读取 [结构操作](../../docs/spec/draft/structure.md#move)，由该 owner 定义 `promote` 的角色、parent、mapping、闭包和导航前置条件；`collapse` 的触发和提交边界也由同一 owner 定义。

## 硬停止条件

- 需要用户决定语义、状态含义、关系成立与否或页面拆分边界时，暂停并询问用户。
- 根、页面、关系、导航、资源、package 或 mapping 事实不能稳定认证时，停止写入并保留诊断；读取对应领域 owner 和 [公共结果、错误与可观测性](../../docs/spec/runtime/errors-and-observability.md#公共结果错误与可观测性) 的错误边界。
- 普通入口遇到待恢复现场时只提示显式 `draft recover --root ROOT`；恢复入口遇到无法证明安全动作的现场时保留现场，按 [显式恢复与 RecoveryOutcome](../../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome) 决定后续动作。
- 链接 owner 返回失败时保存其诊断并停止；不得将未完成动作报告为成功。
- `uvx` 失败且按其诊断无法恢复时，停止当前工作并报告环境问题。

页面正文可自由表达讨论语义；CLI 负责已认证的结构、关系、检查、回收和恢复。检查通过只说明机器可验证事实成立，不替代用户对讨论内容的判断。
