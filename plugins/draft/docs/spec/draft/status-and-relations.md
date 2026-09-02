# 状态与关系

## 页面状态与关系

`D-STATUS` 定义页面状态和正式关系的领域不变量。页面头部使用 YAML frontmatter，其
外壳与正文边界由[页面头部与正文边界](pages-and-navigation.md#页面头部与正文边界)定义；
字段和严格解析规则由本页定义。禁止的 `产出` 键见
[结论快照](conclusions.md#页面头部键与输出边界)。

### 输入与输出

- 输入分为已经通过 [R-INTERFACE 普通操作 request](../runtime/interface.md#普通操作-request)
  公共校验的调用方字段，以及 root-bound Runtime 取得的事实。本页只规定状态/关系领域计划
  需要读取或推导的当前页面字节、当前状态、正式关系、关系端点、物理 parent、根状态、完整
  链接闭包和 recovery overlay。
- 输出是经认证的状态/关系 after-state，或可诊断拒绝。状态与关系编辑的完整请求计划、
  DryRun/Execute 的领域门禁与提交顺序见[页面编辑](page-edits.md#创建状态与关系编辑)；
  公共结果投影由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
  统一定义。
- `confirmed` 是结论成员资格的唯一来源；`challenged_by` 不会自动改变该页面的状态或
  有效性。

### 调用方请求与运行时事实

状态与关系入口使用 `status_set`、`relation_add` 和 `relation_remove` 三个
`OrdinaryRequest` 成员。字段、类型、基数、枚举、CLI 映射、路径词法、mode、通用参数、
禁止组合以及单错误 request-invalid 校验顺序，均以 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)、
[普通操作 request](../runtime/interface.md#普通操作-request)和[mode、逻辑身份与私有边界](../runtime/interface.md#mode逻辑身份与私有边界)
为唯一来源；本页不复制字段表。
公共校验通过后，调用方不提供当前页面状态、关系闭包、parent、反向关系、Check 结果或页面
字节；这些事实由 Runtime 认证、读取或推导。

### YAML 页面头部结构

页面头部的 YAML 文档必须是一个映射对象。下面是包含全部可选字段的非根页面示例：

```yaml
---
status: proposed
note: "可选说明"
parent: index.md
relations:
  - type: depends_on
    target: pages/background.md
---
```

顶层只允许以下四个区分大小写的键：

| 键 | YAML 类型与值 | 出现规则 | 领域含义 |
| --- | --- | --- | --- |
| `status` | 字符串；恰为 `pending`、`proposed`、`confirmed`、`superseded`、`closed` 之一 | 每个受管理页面恰出现一次 | 页面当前状态。 |
| `note` | 不含换行或控制字符的非空字符串 | 可省略；省略表示没有状态说明 | 只说明当前状态，不参与状态或关系判定。 |
| `parent` | 规范的 Draft 根相对 POSIX 受管理页面路径字符串 | 根页 `index.md` 必须省略；其他受管理页面恰出现一次 | 页面唯一物理父页；不能放入 `relations`。 |
| `relations` | 非空列表；每项是恰含 `type`、`target` 的映射对象 | 可省略；省略表示没有可变正式关系，空列表不合法 | 按列表声明顺序保存可变正式关系。 |

每个 `relations` 成员的 `type` 必须是字符串 `depends_on`、`supersedes`、
`superseded_by`、`challenges` 或 `challenged_by`；`target` 必须是规范的 Draft 根相对 POSIX
受管理页面路径字符串。`parent` 不属于该列表。列表顺序是出站关系查询的声明顺序；添加
关系把新成员追加到相应页面列表末尾，删除关系保持其他成员的相对顺序。

`parent` 和 `target` 是页面身份，不是 Markdown 链接或 URI；它们消费
[R-INTERFACE 的 RootPath 与共用输入校验](../runtime/interface.md#共用值与输入校验)定义的路径
词法。页面角色与现存端点资格仍由[路径角色与不变量](pages-and-navigation.md#路径角色与不变量)
及相应操作认证。

解析器只接受上述数据形状：顶层、关系成员或任何嵌套映射对象的重复键都无效；未知键、
空值、数字、布尔值及其他错误类型都无效。YAML 注释、anchor（锚点）、alias（别名）、
merge key（合并键）和自定义 tag（类型标签）均无效；引号内的 `#`、`&` 或 `*` 只是
字符串字符。无效页面不能提供状态、parent 或关系事实，依赖这些事实的写操作必须在修改
任何文件前失败。未知键 `产出` 的稳定诊断由
[页面头部键与输出边界](conclusions.md#页面头部键与输出边界)定义；其他结构错误的
公开诊断字段仍由诊断协议统一定义。

字段事实按可执行的排他顺序认证：`status` 的缺失、非字符串和值域错误都走状态专门分区；
`note` 的 YAML null 与空字符串先认定为空，其他非字符串认定为外层类型错误，字符串再检查
换行和控制字符；`parent` 先区分非字符串与字符串，只有字符串才继续检查 RootPath；
`relations` 先检查外层是否为列表，再检查空列表和每个成员是否为恰含两键的映射，成员形状
合法后才分别检查 `type` 与 `target`。同一个 YAML 节点只产生一个最具体诊断；成员形状无效时
不推断其嵌套值，不同节点上彼此独立且可单独认证的问题仍分别报告。公开 code/reason 的唯一
映射见[诊断 code 登记](query-check-and-output.md#诊断-code-登记)。

读取时，合法键的排列顺序和 YAML 字符串的引号样式不改变领域含义。CLI 写回整个
frontmatter 时使用稳定顺序 `status`、`note`、`parent`、`relations`；关系成员使用
`type`、`target`。没有说明时省略 `note`，根页省略 `parent`，没有关系时省略
`relations`。写回可以规范化 YAML 的缩进、引号和空白，不保留人工排版；它必须保留
关系列表中未被本次操作修改的成员顺序。只修改状态或关系的命令必须逐字保留 frontmatter
结束行之后的 Markdown 正文。

### 状态、关系与不变量

| 类别 | 合法值 / 规则 |
| --- | --- |
| 页面状态 | `pending`、`proposed`、`confirmed`、`superseded`、`closed`。每个受管理页面有且仅有一个状态。 |
| 关系事实 | `parent`、`depends_on`、`supersedes`、`superseded_by`、`challenges`、`challenged_by`；其中 `parent` 是结构 mapping 的事实，不是 relation 命令的可变参数。 |
| 关系写入方向 | 公共接口允许的主动方向由相应领域计划消费；`parent` 由结构 owner 维护，反向关系由主动方向的同一计划维护。 |
| 显式反向对 | `supersedes` 与 `superseded_by`、`challenges` 与 `challenged_by` 必须同时存在且端点相反。 |
| 图约束 | 不允许重复、自引用或关系环；`parent` 还必须服从页面/物理父/导航的一致性。 |
| 结论制品 | `conclusions.md` 只允许固定 `parent`，不参与可变正式关系。 |

`confirmed + challenged_by` 是合法组合：页面仍属于当前有效结论，同时在结论快照中暴露
未解决质疑。链接观察不产生正式关系，也不替代上述成对声明。

`parent` 只能由 create/structure/move/promote/collapse 的完整 mapping 同时维护路径、物理
父和导航；关系编辑不得单独创建、更新或删除它。`superseded_by` 与 `challenged_by` 仍
是关系事实和查询所需的稳定反向代码，由相应主动方向的成对计划维护。

### 决策表、转换与提交

`status_set` 只有在公共 request、root/recovery、目标页面、关系闭包和本领域最终不变量都已
认证后，才按 R-INTERFACE 的 `note_change` 语义计算最终 `status` 与有效 `note`。candidate
根只需局部最终不变量；registered 根还需完整 after-state Check 无 blocker 且 error 数为零。
相等性比较不得提前于这些门禁，也不得把不可认证事实解释为相等。省略说明变更表示保留既有
说明；合法设置后的同值说明，以及无说明时的合法清除，均属于相等分区，任一状态或说明不同
则属于不等分区。

| 请求 / 当前事实 | 认证结果 | 允许的领域结果 | 提交与副作用 |
| --- | --- | --- | --- |
| `status_set` 的合法最终状态与说明 | 页面、根和目标状态可认证，且最终关系不变量成立 | 按下方四行 after-state 分区 | 由下方四行 after-state 分区各自决定；不刷新结论快照。 |
| 当前状态或必要状态事实无法认证 | 不成立 | 拒绝 | 零写。 |
| 添加 `depends_on` | 两端存在、不同、无重复且无依赖环；关系参数是主动方向 | 添加 source 的依赖关系 | 只写该主动行；不写反向行、状态或结论快照。 |
| 添加 `supersedes` | 两端存在、不同、无取代环；主动行、反向行可认证为缺失、完整或单边 | 添加/补齐 `supersedes` 与 `superseded_by` 成对关系，并将 target 状态设为 `superseded` | 两端关系和 target 状态同一提交；不刷新结论快照。 |
| 添加 `challenges` | 两端存在、不同、无重复/自引用/环；反向行可认证并可纳入同一计划 | 添加成对关系 | 两端页面头部信息同一提交；不改变任一状态或结论快照。 |
| 添加可变关系且任一端为 `conclusions.md` | 不成立 | 拒绝 | 零写。 |
| 删除以 `conclusions.md` 为任一端点的可变关系 | 两端页面头部和被点名主动方向可认证；成对关系可以完整、单边残留或两边均缺失 | 按下列相应关系分区精确删除或幂等零写；删除最后一条 `supersedes` 时仍要求合法目标状态 | 只修改被点名关系、必要反向成员和必要目标状态；不生成或覆盖结论制品。 |
| 删除 `depends_on` | 被点名的 source 行可认证且存在 | 仅删除该主动行 | 同一提交；不重写状态或结论快照。 |
| 删除不存在的 `depends_on` 或两边均不存在的成对关系 | 两端页面和相关页面头部信息可认证，且被点名关系确实不存在 | 幂等空操作 | 零写；不能把页面/关系不可认证当作不存在。 |
| 删除 `challenges` | 主动行、反向行可认证为完整或单边残留 | 删除被点名的主动行和/或其已存在的反向残留 | 同一提交；不改变任一状态或结论快照。 |
| 删除非最后一条 `supersedes` | 主动行、反向行可认证为完整或单边残留，target 仍有其他取代者 | 删除被点名的主动行和/或其已存在的反向残留，target 保持 `superseded` | 同一提交；不刷新结论快照。 |
| 删除最后一条 `supersedes` | 主动行、反向行可认证为完整或单边残留，请求提供了经公共接口校验的 `target_status` | 删除被点名的主动行和/或其已存在的反向残留，并把 target 状态设为该值 | 关系和状态同一提交；不刷新结论快照。 |
| 删除最后一条 `supersedes` 且缺少 `target_status` | 不能认证唯一 after-state | 拒绝 | 写入前失败，零写；不得猜测恢复状态。 |
| 删除请求的页面、页面头部信息或关系事实无法认证 | 不成立 | 拒绝 | 零写；不得把未知事实解释为不存在。 |

`status_set` 的 after-state 分区如下；四行都继承本节前置门禁。四个领域 outcome 的唯一选择
由[页面编辑的请求、根状态、模式与失败决策表](page-edits.md#请求根状态模式与失败决策表)负责，
公共结果外壳由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
投影：

| mode | 最终 `status` 与有效 `note` | 领域 after-state 与副作用 |
| --- | --- | --- |
| Execute | 二者都与当前页面相同 | 无领域写入。 |
| DryRun | 二者都与当前页面相同 | 内部形成计划并确认无变化；零写且不取得写锁。 |
| Execute | 任一与当前页面不同 | 沿既有原子提交路径完成状态或说明写入。 |
| DryRun | 任一与当前页面不同 | 内部形成完整计划并据此验证 after-state；零写。 |

任一前置事实非法、不完整或不可认证时，继续沿既有唯一错误路径处理，绝不能降级为
成功的无变化或预演结果。

添加 `supersedes` 或 `challenges` 时，before-state 缺少主动行、反向行或两者均缺失，都由
同一完整计划补入；已存在的行若重复、指向错误端点或无法解析，则在任何写入前失败。删除
这两类关系时，单边残留由完整计划清理，两边都缺失才是幂等零写。`depends_on` 没有显式
反向行。完整请求、DryRun/Execute 以及恢复覆盖层的共同门禁见
[页面编辑](page-edits.md#创建状态与关系编辑)。

`relation.remove` 可以清理涉及 `conclusions.md` 的已认证非法关系，并消费 R-INTERFACE
已经校验的主动方向。成对关系只有一侧残留时，精确请求
清理该残留；两侧都不存在时只有在两端页面头部均可认证后才是幂等零写。删除最后一条
`supersedes` 的目标状态规则不因关系非法而放宽。candidate 根按请求事实闭包执行；
registered 根只按[Check 门禁与副作用](query-check-and-output.md#check-门禁与副作用)的窄修复例外
执行，必须减少被点名错误且不新增错误。

显式结论生成在写前扫描全部可变正式关系；只要 `conclusions.md` 仍是任一关系端点，就
拒绝并保持零写。生成器不代替 `relation.remove` 删除、补齐或解释关系。

### 领域错误投影

状态与关系事实触发的机器码均复用公共结果 owner 的登记：页面头部、状态、说明和父页面
错误分别使用 `page.frontmatter_invalid`、`page.status_invalid`、`page.note_invalid` 和
`page.parent_invalid`；关系声明、端点、配对、图和状态事实分别使用
`relation.declaration_invalid`、`relation.endpoint_invalid`、`relation.pair_invalid`、
`relation.graph_invalid` 和 `relation.status_mismatch`。端点不存在、存在但未受管理、或任一
可变关系以 `conclusions.md` 为端点时，均使用 `relation.endpoint_invalid`；公开 reason 名称
和逐事实映射只见中央[诊断 code 登记](query-check-and-output.md#诊断-code-登记)。
实际删除最后一条 `supersedes` 而缺少合法 `target_status` 使用
`relation.target_status_required`。

请求字段、类型、枚举或组合错误按 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)
使用 `runtime.request_invalid`；事实无法稳定取得使用
`runtime.closure_incomplete`，私有权威损坏使用 `runtime.corrupt`，恢复覆盖层阻止使用
`runtime.recovery_required`，正式根 after-state 门禁失败使用 `operation.check_failed`。
各机器码的 `details.reason` 值域、可选 `path`/`line`、单错误选择、输出通道和错误外壳由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
以及[诊断 code 登记](query-check-and-output.md#诊断-code-登记)统一拥有；本页只规定已认证事实
及其领域 after-state，不另造错误 envelope。

### 失败、零写与恢复

- 重复、缺端点、冲突/不可解析的反向行、自引用、环或根处于 recovery pending 时，编辑前
  失败并保持零写；添加成对关系时，认证的缺失行可作为同一计划的待写 after-state，删除
  成对关系时，认证的单边残留可作为同一计划的待删 after-state。
- 任何提交后耐久不确定都由 runtime 标记 recovery pending；本页不把它降级成关系成功或
  自动回滚成功。只有显式 recovery 可以收敛现场。
- 重试重新读取两端与反向关系。未认证的缺失不构成幂等成功。

### 验证

- 覆盖四个顶层键的必填、可选、非法空值和错误类型；覆盖根页省略 `parent`、非根页缺失
  `parent`、空 `relations`、关系成员缺键/多键、未知键和各层重复键。
- 验证注释、锚点、别名、合并键、自定义类型标签、数字和布尔值均被拒绝；引号内相同
  字符仍作为普通字符串。验证所有诊断行号都映射到含 frontmatter 的原始文件。
- 验证 `parent` 与关系 `target` 消费 R-INTERFACE 的 RootPath 规则，并覆盖页面角色、端点
  和关系成员的合法/非法领域分区；验证 relation 添加只追加，删除不重排其他成员。
- 对一次状态或关系写回，验证顶层键与关系成员键按固定顺序输出、缺失可选字段被省略，
  并且 frontmatter 结束行之后的字节逐字不变。
- 为每个状态值、`confirmed + challenged_by`、每类反向对、重复、自引用和环分别做
  反例验证；分别覆盖公共接口允许的每个主动方向。
- 验证 `supersedes` 添加的 target `superseded` after-state、删除非最后一条时的保持，
  以及删除最后一条时合法目标状态、缺失/非法目标状态和缺少反向行的零写分区。
- 验证 `conclusions.md` 不能作为 `relation.add` 的 source 或 target，而精确删除已认证
  非法关系不影响无关关系；覆盖完整、单边残留、两边缺失、最后一条 `supersedes` 的合法
  目标状态及缺失状态，以及 candidate/registered 门禁。
- 验证状态/关系改动不改变结论快照成员、排序、标签或质疑来源。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md#11-修改正式关系`
