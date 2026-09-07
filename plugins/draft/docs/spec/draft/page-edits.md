# 页面编辑

## 创建、状态与关系编辑

`D-CREATE-EDIT` 覆盖 `create`、`status set`、`relation add` 和 `relation.remove` 的
请求认证、计划和原子领域提交。它不定义 runtime 的 operation record、字段 ABI 或
耐久实现。

`relation.add/remove` 的主动方向和 request 映射由运行时的[普通操作 request](../runtime/interface.md#普通操作-request)
唯一规定。本页的关系表继续以这些操作名作为领域决策行标签；`parent` 由结构 mapping
owner 随路径、物理父和导航共同维护；`superseded_by` 与 `challenged_by` 是可查询的反向
事实，由对应主动方向的完整计划维护。

### 输入、输出与共同不变量

- 输入分为已经通过 [R-INTERFACE 普通操作 request](../runtime/interface.md#普通操作-request)
  公共校验的调用方字段，以及 root-bound Runtime 取得的事实。本页只规定四个操作进入领域
  计划后需要读取或推导的根状态、目标页面当前字节、页面头部信息、物理 parent、导航、
  关系闭包、结论事件边界和 recovery overlay。
- 每个操作都在内部形成完整计划；Execute 按该计划提交领域 after-state。DryRun 复用同一计划
  验证 after-state；公共结果结构由运行时的[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
  投影；本页只规定领域 after-state 和哪些事实触发已登记机器码。
- DryRun 与 Execute 使用完全相同的前置事实和计划边界。DryRun 不写页面头部信息、正文、导航、
  关系、marker 或 runtime 状态。
- Execute 只有在全部后置不变量可同时证明时才提交；不能先改页面再补关系、导航或反向行。

### 调用方请求与运行时事实

本页涉及 `create`、`status_set`、`relation_add` 和 `relation_remove` 四个
`OrdinaryRequest` 成员。字段、类型、基数、枚举、CLI 映射、路径词法、mode、通用参数、
禁止组合以及单错误 request-invalid 校验顺序，均以 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)、
[普通操作 request](../runtime/interface.md#普通操作-request)和[mode、逻辑身份与私有边界](../runtime/interface.md#mode逻辑身份与私有边界)
为唯一来源；本页不复制字段表。
公共校验通过后，调用方仍不提供 parent、导航、当前状态、关系反向行、闭包、计划或 Check
结果，这些事实由 Runtime 认证、读取或推导。

`create` 形成的新叶页面使用 UTF-8、无 BOM、LF 换行并以 LF 结束；frontmatter 依次写入
`status`、`parent`，结束分隔行后空一行，再写唯一一级标题，标题后保留一个空行。标题按
纯文本请求值进行必要 Markdown 转义，使解析后的可见标题恰等于请求值；不生成 `note`、
`relations`、`## 子主题`、正文占位符或其他推测内容。

| 请求 | 必须进入完整计划的领域事实 | 成功后的不变量 |
| --- | --- | --- |
| `create` | 新叶路径角色（不含根页和保留路径）、目标不存在、物理 parent、必要导航，以及只有请求状态和 parent 的初始页面头部 | 新页符合页面角色；没有 `note` 或 `relations`；parent/导航一致；不产生 `pages/index.md`。 |
| `status set` | 目标页、当前/目标状态、可选说明变更、受影响关系和结论快照事件边界 | 状态是五个合法值之一；按请求保留、替换或删除 `note`；关系不变量仍成立；不改快照。 |
| `relation add` | 两端、主动方向、必要反向成员和 supersedes 状态 after-state；无重复/自引用/环证明 | 新成员追加到对应 `relations` 列表；成对关系、状态和页面不变量同时成立；结论制品不成为可变端点。 |
| `relation.remove` | 被点名的既存主动成员、适用反向成员，以及删除最后一条 supersedes 时的合法目标状态 | 仅精确删除已认证关系并保持其余列表顺序；不做推测性清理。 |

### relation add/remove 的关系与状态分区

| 请求与当前事实 | 认证与计划 | 成功 after-state | 失败与副作用 |
| --- | --- | --- | --- |
| `relation.add`，`depends_on` | 两端存在、不同、无重复且无依赖环 | 仅添加 source 的主动依赖行 | 任一事实不可认证时零写。 |
| `relation.add`，`challenges` | 两端存在、不同、无重复/自引用/环；主动/反向行可认证为缺失、完整或单边 | 添加或补齐 `challenges` 与 `challenged_by` 成对行；不改变状态 | 已存在的行若冲突、重复或不可解析，零写。 |
| `relation.add`，`supersedes` | 两端存在、不同、无取代环；主动/反向行可认证为缺失、完整或单边 | 添加或补齐 `supersedes` 与 `superseded_by` 成对行，并将 target 状态设为 `superseded` | 状态、端点或既有行冲突时零写。 |
| `relation.remove`，`depends_on` | 被点名主动行存在且可精确定位 | 删除该主动行；不改状态 | 缺失行或 root recovery pending 时零写。 |
| `relation.remove`，不存在的 `depends_on` | 两端页面和页面头部信息可认证，且被点名主动行确实不存在 | 幂等空操作 | 零写；页面或关系事实不可认证时改走拒绝分区。 |
| `relation.remove`，`challenges` | 主动/反向行可认证为完整或单边残留 | 删除被点名主动行和/或已存在的反向残留；不改状态 | 两边都缺失时幂等零写；冲突/不可解析时零写。 |
| `relation.remove`，非最后一条 `supersedes` | 主动/反向行可认证为完整或单边残留，target 仍有其他取代者 | 删除被点名主动行和/或已存在的反向残留；target 保持 `superseded` | 冲突/不可解析或其他取代者事实不完整时零写。 |
| `relation.remove`，最后一条 `supersedes` | 主动/反向行可认证为完整或单边残留，且请求提供了经公共接口校验的 `target_status` | 删除被点名主动行和/或已存在的反向残留，并把 target 状态设为该值 | 缺少该值时零写；不得自动选择状态。 |

添加成对关系时，before-state 缺少主动行、反向行或两者均缺失，都由完整计划补入；已存在
的行若重复、指向错误端点或无法解析，则在任何写入前失败。删除成对关系时，单边残留由
完整计划清理，两边都缺失才是幂等零写。`conclusions.md` 不得成为可变端点，且任何
关系/状态操作不刷新其语义快照。

`status_set` 按经公共接口校验的 `note_change` 计算有效 `note`，领域 after-state 只会保留、
替换或省略该键。关系操作只修改所需列表成员；结构操作只按其 mapping 修改 `parent`。所有入口都消费
[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)，不能把错误的页面头部
规范化成成功。

### 请求、根状态、模式与失败决策表

| 请求 | 根 / recovery | 模式 | 前置事实与计划 | 结果 |
| --- | --- | --- | --- | --- |
| 任一 | marker 不可认证或 recovery pending | DryRun / Execute | 不能建立可信计划 | 拒绝；零写。 |
| `create` | `candidate`、无 pending | DryRun | 新路径及 create 专属事实完整 | 内部形成完整计划；零写。 |
| `create` | `candidate`、无 pending | Execute | 与 DryRun 相同的计划仍可认证 | 原子提交新页及必要事实。 |
| `create` | `registered`、无 pending | DryRun / Execute | create 专属事实完整，且完整计划 after-state 的无过滤 Check 结果完整、无 blocker、error 数为零 | DryRun 内部形成完整计划并零写；Execute 原子提交。 |
| `relation add/remove` | `candidate`、无 pending | DryRun / Execute | 端点、关系和精确清理范围可认证 | DryRun 内部形成完整计划并零写；Execute 原子提交。 |
| `relation add/remove` | `registered`、无 pending | DryRun / Execute | 上述事实加完整计划 after-state 的完整零 error Check；仅精确删除已定位非法关系可走窄修复例外 | DryRun 内部形成完整计划并零写；Execute 原子提交。 |
| 任一 | 任一 | DryRun / Execute | 参数、span、链接、目标、状态、关系、导航或完整计划任一项不可认证 | 拒绝；不以缺失事实推断幂等成功，零写。 |

`status_set` 的领域门禁先按上表认证 request、root/recovery、目标页面内容、关系和计划事实；
candidate 根只需局部最终不变量，registered 根还需完整 after-state Check 无 blocker 且 error
数为零。随后按照已通过
R-INTERFACE 校验的 `note_change` 语义计算最终 `status` 与有效 `note`，再进入下面四行唯一
分区。省略说明变更表示保留既有说明；合法设置后的同值说明，以及无说明时的合法清除，
均属于相等分区，任一状态或说明不同则属于不等分区：

| 请求 | 执行分支 | 最终 `status` 与有效 `note` | 唯一领域选择与副作用 |
| --- | --- | --- | --- |
| `status_set` | Execute | 二者都与当前页面相同 | 选择领域 `no_change`；领域 after-state 与当前页面相同；不发生领域写入。 |
| `status_set` | DryRun | 二者都与当前页面相同 | 选择领域 `would_not_change`；领域 after-state 与当前页面相同；不发生领域写入，且不取得写锁。 |
| `status_set` | Execute | 任一与当前页面不同 | 选择领域 `applied`；沿既有原子提交路径提交领域 after-state；按请求写入状态或说明。 |
| `status_set` | DryRun | 任一与当前页面不同 | 选择领域 `would_apply`；内部形成完整计划；零写。 |

只有全部前置事实通过后才允许比较最终状态；任一事实非法、不完整或不可认证时，继续沿既有
唯一错误路径处理，绝不能把拒绝降级为成功的无变化或预演结果；公共投影按 R-ERRORS 规则处理。

正式根门禁消费
[Check 门禁与副作用](query-check-and-output.md#check-门禁与副作用) 的完整 after-state 结果；候选根只
消费请求事实闭包。精确非法关系删除的窄例外同样由该门禁限定，不能推广为“错误不增加”或
任意局部修复。每类入口写入的页面头部成员由 R-INTERFACE 的 request 约束和
[YAML 页面头部结构](status-and-relations.md#yaml-页面头部结构)共同定义；这不授权实现
跳过状态、parent、关系和导航认证。

### 领域错误选择

普通修改和 DryRun 的公共投影由运行时[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
统一拥有；下表只列本领域的事实选择阶段、机器码和零写边界。

| 事实选择阶段 | 已认证触发事实 | 领域 code 选择与零写边界 |
| --- | --- | --- |
| 根与恢复 | 根路径/选择不可认证 | `runtime.root_invalid`；拒绝且零写，不建立领域计划。 |
| 根与恢复 | recovery pending、运行环境或耐久状态不能安全完成，或私有 marker/authority 已损坏 | 分别复用 `runtime.recovery_required`、`runtime.platform_unsupported`、`runtime.busy`、`runtime.external_change`、`runtime.durability_failed` 或 `runtime.corrupt`；拒绝且零写，不产生领域成功。 |
| 页面路径 | 必需页面不存在，或存在但不属于受管理页面 | 选择 `operation.path_not_found` 或 `operation.path_not_managed`；拒绝且零写。 |
| 页面内容 | 已读取但违反标题、frontmatter、状态、说明、parent 或页面角色规则 | 复用 `page.title_invalid`、`page.frontmatter_invalid`、`page.status_invalid`、`page.note_invalid`、`page.parent_invalid` 或 `page.role_invalid`；拒绝且零写。 |
| 关系事实 | 关系声明、端点、反向配对、图或 supersedes 状态不成立 | 复用 `relation.declaration_invalid`、`relation.endpoint_invalid`、`relation.pair_invalid`、`relation.graph_invalid`、`relation.status_mismatch`；实际删除最后一条取代关系缺少 `target_status` 时使用 `relation.target_status_required`，拒绝且零写。 |
| 计划事实 | 结构/页面闭包、span 或 after-state 无法完整稳定取得 | 选择 `runtime.closure_incomplete`；拒绝且零写，不得把未知或暂时不可取得解释成不存在或成功无变化。 |
| 正式根门禁 | 完整 after-state Check 有 error 且不是操作自身已分类的错误 | 选择 `operation.check_failed`；Check 无法完整运行仍使用 `runtime.closure_incomplete`；拒绝且零写。 |
| 其他 | 没有已登记的安全分类 | 选择 `runtime.internal`；拒绝且零写，不拼接未认证异常原文。 |

请求错误已经由 R-INTERFACE 在进入本表前选定。本领域按表中事实阶段从前到后求值；先命中的
领域事实选择相应机器码并保持零写。

### 提交、副作用、失败与恢复

Execute 的提交点是完整计划中的所有领域事实一起可见的时刻。成功可以改写页面头部信息、正文、
关系和必要导航；`create`、`status set`、`relation add/remove` 不修改 `conclusions.md`
快照。`status set` 与 `relation add/remove` 只拥有 frontmatter 变化，必须逐字保留正文；
`create` 拥有新页面的初始正文，结构命令的正文与导航改写由其各自 owner 定义。任何校验
失败在提交前零写。

若领域提交后耐久状态不能认证，普通调用不得报告成功或继续后续写入；runtime 保留现场并
加上 recovery overlay。重试须重新认证规范化请求和全部会改变计划或结果的 authority 事实。
只有请求与这些已认证事实均未变化时，才按 runtime owner 的规则处理重试；事实变化时重新形成
计划。摘要算法、记录字段、比较编码和耐久顺序属于 runtime 私有实现，不是本页的公共合同。

### 验证

- 对四类请求分别覆盖 candidate、registered、pending、DryRun、Execute 和各类不可认证
  前置事实；关系请求覆盖公共接口允许的每个主动方向及成对行缺失。
- 对每个 DryRun/Execute 对证明使用同一计划，且 DryRun 零写。
- 覆盖 `status_set` 对有效 `note` 的保留、替换和删除；覆盖 create 省略 `note`/
  `relations`，relation 添加追加、删除保持其余顺序，以及所有头部修改逐字保留正文。
- 让计划中任一页面、页面头部信息、导航、端点或反向关系失效，验证 Execute 不产生部分写入；
  模拟 supersedes 的非最后/最后删除、缺失/非法恢复状态和耐久不确定，验证普通重试被
  recovery overlay 阻止。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md#11-修改正式关系`
