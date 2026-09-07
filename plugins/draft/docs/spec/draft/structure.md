# 结构操作

## move

`D-MOVE` 定义 `move SOURCE... TARGET` 的来源解释、页面纳管、mapping 和原子领域结果。
它只定义领域 after-state，不复制 runtime 的 manifest 编码、耐久顺序或恢复算法。
领域计划必须保留唯一的完整 mapping，供路径、父关系、导航、链接闭包、自动 collapse、manifest
和 recovery 共同消费。

### 输入、输出与不变量

- 输入分为已经通过 [R-INTERFACE 普通操作 request](../runtime/interface.md#普通操作-request)
  公共校验的调用方字段，以及 root-bound Runtime 取得的事实。本页只规定结构领域计划需要
  读取或推导的根状态、recovery overlay、来源/目标形状、managedness、目录成员、全根 Markdown
  闭包、before-tree、parent、导航、链接和结论制品。
- `file`/`prefix` target 分类和 `move` 的静态字段组合均由 R-INTERFACE 的公共 request
  阶段完成；D-MOVE 只消费已经通过公共校验的分类，不在本页按目标字符串重复判定。已认证
  为 `directory` 的 source 配 `file` target 仍须读取当前文件系统事实确认 source 角色，再由
  D-MOVE 在领域门禁中拒绝。
- 结构操作的公共投影由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
  统一定义；本页只规定领域 after-state、mapping 和错误事实，不定义公共结果或错误结构。
- 成功前必须形成唯一、规范化、去重且确定的 `before -> after` mapping。mapping 以规范化
  before path 为键，逻辑顺序由规范化键唯一确定；路径、parent、导航、可证明链接改写、
  自动 collapse、manifest 和 recovery 都只能从该 mapping 派生。mapping 的具体保存编码
  和遍历实现是 Draft 私有细节。
- mapping 只包括实际移动的受管理或获准纳管 Markdown。retained 节点、附件/其他非
  Markdown、`.draft`、目录清理和内部日志不生成 mapping 项，也不取得页面所有权。

### 调用方请求与运行时事实

结构操作使用 `move`、`promote` 和 `collapse` 三个 `OrdinaryRequest` 成员。字段、类型、
基数、枚举、CLI 映射、路径词法、mode、通用参数、禁止组合以及单错误 request-invalid 校验
顺序，均以 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)、
[普通操作 request](../runtime/interface.md#普通操作-request)和[mode、逻辑身份与私有边界](../runtime/interface.md#mode逻辑身份与私有边界)
为唯一来源；本页不复制字段表。
公共校验通过后，调用方不提供 managedness、目录成员、parent、导航、链接闭包、before/after
tree、mapping、结论制品或 Check 结果；这些事实由 root-bound Runtime 读取、认证和推导。

### 来源决策矩阵

先按 source 集合的形状和节点可认证性分类：`single-file` 表示恰好一个、类型和内容均可
认证为 Markdown 的文件 source，`file-set` 表示两个或更多、每一个都可认证为 Markdown 的
文件 source，`directory` 表示单个、类型可认证的目录 source。文件/目录混合、多个目录、
重复 source 或无法确定 source 形状的集合不进入下表。

文件 source 的 `managed`/`unmanaged` 是 source 页面命名空间资格，必须对本次 source 集合
统一可认证。目录不是 ownership 对象：先从目录中筛出可移动 Markdown 集合 `M`，再对每个
成员按其页面命名空间路径判定 `managed(m)` 或 `unmanaged(m)`；非 Markdown、附件和其他
retained 节点不进入 `M` 或该判定。`M` 非空且所有成员为 managed 时，目录键为
`uniform-managed`；`M` 非空且所有成员为 unmanaged 时，目录键为 `uniform-unmanaged`，两者
分别复用下表既有 managed/unmanaged admission 规则。`M` 为空、成员分类为 mixed/unknown/
unverifiable 的目录，转入 [目录来源](#目录来源) 的互斥分区和矩阵外门禁。`candidate` 与
`registered` 是根状态；`file`/`prefix` 是由 R-INTERFACE 已认证的 target 形状，本页不重新
定义其词法。公共 request 阶段已经排除的静态组合不进入下表；
`directory` source 配 `file` target 仍进入下表，因为 source 角色必须读取并认证当前文件系统
事实后才能由 D-MOVE 拒绝。因此每行都是四个维度的互斥组合；所有允许行还必须满足无 recovery pending、路径/闭包/
before-tree/mapping 可认证，以及目录节点分类和边界可认证。`registered` 的允许行还必须对
完整计划 after-state 通过[Check 门禁与副作用](query-check-and-output.md#check-门禁与副作用)：结果
完整、无 blocker 且 error 数为零；`candidate` 不因闭包外的现有 error 被阻止。

| source 形状 | 管理状态 | 根状态 | target 形状 | 结果（满足共同前置事实时） |
| --- | --- | --- | --- | --- |
| single-file | managed | candidate | file | 允许进入完整计划。 |
| single-file | managed | candidate | prefix | 允许进入完整计划。 |
| single-file | managed | registered | file | 允许进入完整计划。 |
| single-file | managed | registered | prefix | 允许进入完整计划。 |
| single-file | unmanaged | candidate | file | 允许页面纳管并进入完整计划；目标必须是页面命名空间中的合法单文件目标。 |
| single-file | unmanaged | candidate | prefix | 允许页面纳管并进入完整计划；目标必须是页面命名空间中的合法前缀目标。 |
| single-file | unmanaged | registered | file | 拒绝，零写；不得把 candidate 的纳管权限扩张到 registered。 |
| single-file | unmanaged | registered | prefix | 拒绝，零写；不得把 candidate 的纳管权限扩张到 registered。 |
| file-set | managed | candidate | prefix | 允许进入完整计划。 |
| file-set | managed | registered | prefix | 允许进入完整计划。 |
| file-set | unmanaged | candidate | prefix | 允许页面纳管并进入完整计划；目标必须是页面命名空间中的合法前缀目标。 |
| file-set | unmanaged | registered | prefix | 拒绝，零写；不得把 candidate 的纳管权限扩张到 registered。 |
| directory | uniform-managed | candidate | file | 拒绝，零写；见[结构错误投影](#结构错误投影)。 |
| directory | uniform-managed | candidate | prefix | 按 [目录来源](#目录来源) 形成 mapping，进入同一次完整计划。 |
| directory | uniform-managed | registered | file | 拒绝，零写；见[结构错误投影](#结构错误投影)。 |
| directory | uniform-managed | registered | prefix | 按 [目录来源](#目录来源) 形成 mapping，进入同一次完整计划。 |
| directory | uniform-unmanaged | candidate | file | 拒绝，零写；见[结构错误投影](#结构错误投影)。 |
| directory | uniform-unmanaged | candidate | prefix | 仅纳管其中可移动 Markdown 并按 [目录来源](#目录来源) 形成 mapping；非 Markdown 和可认证的 retained 节点原地保留，不进入领域成功事实。 |
| directory | uniform-unmanaged | registered | file | 拒绝，零写；见[结构错误投影](#结构错误投影)。 |
| directory | uniform-unmanaged | registered | prefix | 拒绝，零写；不得创建或改写页面、导航、catalog/head，也不得产生日志副作用。 |

### 矩阵外门禁（统一结果）

下列事实不属于上述 20 个合法键。无论 target 是 `file` 还是 `prefix`，也无论 source
形状或管理状态如何，均必须在任何结构写前拒绝、保持零写并保留可诊断事实；不得以“没有
匹配到矩阵行”代替这些结果。

| 矩阵外事实 | 覆盖范围 | 唯一结果与边界 |
| --- | --- | --- |
| 文件 source 含非 Markdown、附件、`unknown`、无法分类或无法认证的节点 | `single-file`、`file-set` 的任意 managed/root/target 组合 | 拒绝，零写；节点不得取得页面所有权或进入 mapping，保留诊断。 |
| source 集合为文件/目录混合、多个目录、重复 source 或 source 形状无法认证 | 任意 managed/root/target | 拒绝，零写；不猜测 source 形状或 owner。 |
| root state 为 `unknown`、`mixed` 或不可认证（unverifiable） | 任意已认证 source 形状、managedness、target 形状 | 拒绝，零写；不得把未知根解释为 `candidate` 或 `registered`。 |
| 文件 source 的 managedness 为 mixed、unknown 或不可认证，或目录成员分类为 mixed、unknown 或 unverifiable | 任意已认证 source 形状、root、target | 拒绝，零写；不得部分纳管或部分移动 source 集合。目录成员按 [目录来源](#目录来源) 保留成员级诊断。 |
| target 形状、规范路径、`.draft` 保留边界、路径角色或碰撞无法认证 | 任意 source/managedness/root | 拒绝，零写；不得用 basename、目录枚举或文本相似度补全目标。 |
| recovery pending，或 recovery/authority snapshot 不可认证 | 任意 source/managedness/root/target | 阻止本次结构写，零写并保留 recovery 诊断；待显式 recovery 收敛后重新认证。 |
| 任一 source、闭包节点、before/after tree、mapping 或链接/导航事实不可认证 | 任意合法键或目录内容分区 | 拒绝，零写；不从 lower-bound 或缓存事实继续。 |
| 目录边界、节点分类或物理空性无法认证 | `directory` source 的任意 managed/root/target | 拒绝，零写；不得先移动 Markdown 再决定是否保留/清理目录，保留目录及诊断。 |

candidate 纳管仅适用于已认证的 Markdown source；目录 candidate 纳管还必须满足 [目录来源](#目录来源)
的可移动 Markdown mapping 和 retained 节点边界。任何矩阵外事实都不能通过辅助章节的页面
所有权、parent/nav 或链接规则反向放宽。

### 结构错误投影

结构操作的公共投影统一由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)拥有；本表只规定
领域事实、错误阶段选择、已登记机器码和零写边界。

| 事实 | 领域 code 选择与零写边界 |
| --- | --- |
| D-MOVE 已认证的 directory source 配 `file` target | 选择 `move.target_incompatible`；在 source 实际类型认证后、可写计划形成前拒绝，Execute 与 DryRun 均零写；所有 directory + `file` 矩阵分区使用同一结果。 |
| mapping 必须不含 after path 等于自身 before path 的 self-mapping | 由 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)在领域读取前使用规范化 source/target 字符串静态拒绝并零写；D-MOVE 不把它重新分类为 mapping 阶段领域错误。 |
| source 集合包含非 Markdown、混合文件/目录、多个目录、混合 managedness 或没有可移动 Markdown | 选择 `move.sources_not_movable`；各来源事实对应的 reason 由 [R-ERRORS 命令特有领域 code 登记](../runtime/errors-and-observability.md#错误登记)投影；拒绝且零写。 |
| source 已存在但未受管理，且命令要求受管理页面 | 选择 `operation.path_not_managed`；拒绝且零写；candidate 仅在矩阵允许的纳管分支进入计划。 |
| 必需 source/target/page 不存在 | 选择 `operation.path_not_found`；拒绝且零写。 |
| 目标路径被其他对象占用，或 promote 将吸收未选择页面 | 选择 `operation.path_conflict`；拒绝且零写。 |
| promote 的页面不是叶，或 sibling 不属于共同 parent | 选择 `promote.candidate_not_leaf` 或 `promote.parent_mismatch`；拒绝且零写。 |
| collapse 目标是根/非 hub，或 hub 仍有受管理子页面 | 选择 `collapse.target_not_collapsible` 或 `collapse.hub_not_empty`；root/not_hub 的事实分区按 [R-ERRORS 命令特有领域 code 登记](../runtime/errors-and-observability.md#错误登记)投影；拒绝且零写。 |
| 已认证内容违反页面、parent、导航、关系规则，或违反结论制品窄改写所需的固定形状与原文位置约束 | 复用已认证事实在 [Check 诊断 code 登记](query-check-and-output.md#诊断-code-登记) 中的 Check code；结论制品按[既有制品的检查与使用](conclusions.md#既有制品的检查与使用)认证，不重算快照语义；正式根完整 after-state 仍有 error 时选择 `operation.check_failed`；拒绝且零写。 |
| 闭包、mapping、span、目录物理边界或 after-tree 无法完整稳定取得 | 选择 `runtime.closure_incomplete`；拒绝且零写，不得从 lower-bound 或缓存事实继续。 |
| recovery pending、snapshot/authority 不可认证、耐久结果不确定或私有权威损坏 | 复用 `runtime.recovery_required`、`runtime.external_change`、`runtime.durability_failed` 或 `runtime.corrupt`；拒绝且零写，不产生结构成功提交。 |

错误阶段按本表从前到后求值；先命中的已认证领域事实选择相应机器码并保持零写。页面内容
错误不另造结构同义码。

### mapping、提交与副作用

完整计划先固定唯一的规范化 mapping，再从它派生所有变化。mapping 必须覆盖每个实际移动
的 Markdown，按规范化 before path 去重，并且不含 self-mapping；R-INTERFACE 已在领域读取前
用规范化 request 字符串排除 after path 等于自身 before path 的请求（见[共用值与输入校验](../runtime/interface.md#共用值与输入校验)）。其余任一 source 重复、after path
重复、未移动节点或保留路径冲突，均在写前拒绝。source 参数的排列只改变输入顺序，不得改变
mapping、parent、导航或链接闭包。目录残留、retained 事实和内部日志不进入领域结果。

对 prefix target，mapping 的 after path 唯一按以下函数计算（所有路径先规范化为根内相对
路径，再做碰撞检查）：

| source 形状 | 每个可移动 Markdown 的 after path |
| --- | --- |
| 单文件 | `TARGET/<source basename>` |
| 目录 | `TARGET/<source directory name>/<member relative path>` |
| file-set | 对每个文件分别取 `TARGET/<source basename>` |

目录项的 `member relative path` 相对于 source 目录，保留其已认证的子目录层级；retained、
附件/其他非 Markdown、`.draft` 和目录清理不生成 mapping 项。对已由 R-INTERFACE 分类为
`file` 的 target，单文件 source 可以把该 source 映射到精确 `TARGET`；directory source 的
`file` target 仍在 D-MOVE 认证 source 角色后写前拒绝。规范化后的绝对路径、路径逃逸、保留 `pages/index.md`、别名重复或任一
after collision 都是零写失败。具体序列化字段、遍历和持久化顺序属于 runtime 私有实现，
不是公共合同；mapping 的数学内容与 source 顺序无关。

### D-MOVE snapshot S 语义载荷

每个 D-MOVE 领域计划都携带同一份完整 snapshot `S`。`S` 的领域内容必须可独立复核，至少
包括：

- source shape 与完整 member paths；
- root state；
- 每个可移动 Markdown member 的 managedness 分类集合（目录不作为 ownership 值）；
- target kind（`file` 或 `prefix`）与规范 target path；
- 完整规范化、去重、确定的 before-to-after mapping；
- 全根链接/页面 closure 及其 digest；
- retained 节点集合与目录边界/移动后物理状态证明；
- parent、navigation、link 和 `conclusions` 的窄改写事实；
- authority epoch 与 authority digest。

上述是领域事实集合，不规定 runtime 的持久化键、编码、manifest/progress/receipt 结构或
序列化顺序；这些 ABI 由 runtime owner 另行定义，不是公共合同。

恢复资格、分类和后续路由仅由
[D-MOVE snapshot S 恢复门禁与 Q6-B 漂移分类](../runtime/recovery.md#d-move-snapshot-s-恢复门禁与-q6-b-漂移分类)
拥有；本页不选择任何恢复处理结果。

若 mapping 后的树可证明产生空的非根 hub，自动 collapse 必须作为触发 `move`/`promote` 的同一
内部逻辑 operation 的子阶段，与其共用同一领域提交、同一 mapping 和恢复记录；不能另开 operation、
另起领域提交或先报告 move 成功。精确
的候选遍历顺序、子阶段字段和 reverse proof 属于 Draft 私有证明资料，不是公共合同。不
能认证 after-tree 或 collapse 顺序时，停止并零写，而不是选择一个目录扫描策略。

提交点是页面路径、parent、必要导航、可证明链接 span 和附带 collapse after-state 同时
可见的时刻。`conclusions.md` 仅在 mapping 可证明时改写已有条目的链接目标，且不改成员、
分组、顺序、标签、质疑来源或语义；这些语义由 `generate-conclusions` 单独重算。

### 失败、零写、重试与恢复

- 碰撞、路径逃逸、重复、闭包不完整、链接或导航不可认证、目录边界不成立均在写前失败。
- 提交后无法认证耐久结果时不返回成功；runtime 保留 recovery 现场，普通 `move` 重试
  被阻止，直到显式 recovery 收敛。
- 收敛后重试重新证明整个 mapping。相同 request/authority snapshot 的复用、attempt 和旧记录
  关联由 runtime 决定；本页不以路径相同推断幂等成功。

### 验证

- 逐行覆盖上述 20 个 source 形状、管理状态、根状态和 target 形状分区，并额外覆盖混合
  source、碰撞、路径逃逸和 `pages/index.md` 门禁；公共静态组合校验按 R-INTERFACE 验证，
  directory + `file` target 在 D-MOVE 认证 source 角色后拒绝。
- 验证 candidate 的 uniform-unmanaged directory + prefix 只纳管可移动 Markdown，非 Markdown 和
  已识别的 retained 节点原地保留且不泄露留置事实；registered 的 uniform-unmanaged directory 在
  两种 target 形状下均拒绝、零写且没有页面、导航、catalog/head 或日志副作用。
- 验证 candidate 纳管与 registered 拒绝的分区，及所有路径/parent/导航/链接只由一个
  mapping 派生；registered 另验证计划 after-state 的完整零 error Check，candidate 验证
  无关 error 不阻止已经完整认证的请求闭包。
- 验证自动 collapse 属于触发结构命令的同一内部逻辑 operation、同一领域提交、同一 mapping
  和恢复记录的子阶段，不产生第二个公共结果或部分成功；mapping 不可认证和耐久不确定均不产生可见成功。

## 目录来源

`D-MOVE-OUTPUT` 只定义目录 source 的资源边界和窄输出；它不重新定义 `move` 的 target
解释或结构操作的公共投影。

### 输入、输出与不变量

- 输入是一个已经通过 `D-MOVE` source/target 门槛、目录边界均可认证的目录，以及每个
  成员的类型、页面命名空间 managedness、可移动 Markdown mapping 和移动后物理状态证明。
  目录不是 ownership 对象。先定义可移动 Markdown 集合 `M`；对 `M` 中每个 `m`，若其规范
  路径属于页面命名空间则 `managed(m)`，否则 `unmanaged(m)`。非 Markdown、附件和其他
  可认证 retained 节点不进入 `M` 或 managedness 分类。
- 目录分类按以下优先级求值以保持互斥：先检查 `unverifiable`（任一节点类型、路径、边界或
  事实本身无法认证；可认证存在但类型未知的节点可作为 retained）；若所有所需事实均可认证，再检查 `unknown`（至少一个可移动 Markdown
  成员的 managedness 明确无法判定）；随后检查 `no-movable`（`M` 为空）；最后在 `M` 非空
  时区分 `mixed`（同时存在 managed 与 unmanaged）、`uniform-managed`（每个成员 managed）
  和 `uniform-unmanaged`（每个成员 unmanaged）。`mixed`、`unknown`、`unverifiable` 均在
  写前拒绝、零写并保留成员级诊断；`no-movable` 进入既有 no-op 分区。分类集合记录每个
  成员的事实，不伪造目录 ownership。
- 目录 source 只移动可认证的 Markdown。retained 非 Markdown、附件、以及可认证存在但类型
  未知而被保留的节点，均原位保留，不取得页面所有权或进入 mapping。目录清理是独立的物理空性与 owner 证明，
  不是页面 mapping 的推论。
- 领域成功只含当前命令必要的业务事实；不得枚举、计数、摘要 retained 内容，也不得加入
  retained facts、截断标志或续读协议。

下表按“成员分类 -> retained 边界 -> mapping/物理状态”判定；`no-movable`、
`uniform-managed`、`uniform-unmanaged`、`mixed`、`unknown` 和 `unverifiable` 这些成员
分类互斥。证明失败行优先于任何可分类行，因而不会出现“先移动再决定”的第二结果。

| 成员分类与 retained 边界（互斥分区） | mapping / 物理状态 | 唯一领域结果与输出边界 |
| --- | --- | --- |
| `uniform-managed`，且无 retained 节点、目录边界与所有事实可认证 | mapping 完整；写前证明移动后目录物理为空 | 只移动 Markdown；仅在独立 owner 清理规则也获证明时移除目录；输出只含必要事实。 |
| `uniform-managed`，且含 retained 非 Markdown/附件/类型未知节点 | mapping 完整；写前证明移动后目录物理非空 | 只移动 Markdown；retained 节点原位保留，不进入 ownership、mapping 或成功输出。 |
| `uniform-unmanaged`，且无 retained 节点、目录边界与所有事实可认证 | mapping 完整；candidate/registered admission 由来源矩阵决定 | candidate 只纳管可移动 Markdown；registered 按矩阵拒绝；输出不增加 retained 事实。 |
| `uniform-unmanaged`，且含 retained 非 Markdown/附件/类型未知节点 | mapping 完整；写前证明移动后目录物理非空 | candidate 只纳管可移动 Markdown；registered 按矩阵拒绝；retained 节点原位保留，不进入 ownership 或 mapping。 |
| `no-movable`，且目录为空或仅含事实可认证的 retained 非 Markdown/附件/可认证存在但类型未知的节点 | 空 mapping；目录物理状态已在写前认证 | 不发生结构写；选择 `move.sources_not_movable`，其 no-movable reason 按 [R-ERRORS 命令特有领域 code 登记](../runtime/errors-and-observability.md#错误登记)投影；拒绝且零写，不产生可见 move 成功或 retained 摘要。 |
| `mixed`、`unknown` 或 `unverifiable` | 分类集合或边界不可用于 admission | 写前拒绝、零写并保留成员级诊断；不得部分移动、部分纳管或把目录当 ownership。 |
| 上述任一可分类分区，但 Markdown mapping 不完整 | mapping 不可认证 | 写前拒绝、零写并保留诊断；不移动任何节点，不进行目录清理。 |
| 上述任一可分类分区，但移动后目录空性或非空性无法认证 | 物理状态不可认证 | 写前拒绝、零写并保留目录及诊断；不得先移动 Markdown 再决定保留或清理。 |

目录 source 的 before-to-after mapping 参与同一个 `move` 提交和任何自动 collapse；retained
节点不生成 mapping 项。恢复或重试也不得扩张领域输出边界：即使保留现场，也不能把目录残留变成
新的领域事实。目录清理只有在独立物理空性和 owner 证明同时成立时才可发生。

### 失败、恢复与验证

任一节点类型、路径、mapping、闭包或目录物理状态不可信时，写前失败并保留现场；耐久不确定
时由 runtime 的 explicit recovery 处理。验证纯 Markdown、Markdown+非 Markdown、
Markdown+可认证 retained 节点、仅 retained/空目录（事实可认证）、mixed/unknown/unverifiable
节点和空性不可认证反例，确认非 Markdown/retained 从不移动、不取得所有权，领域成功从不泄露
retained facts；并验证 registered uniform-unmanaged directory 的 `file` 与 `prefix` target
均在矩阵行中拒绝零写。

## promote

`D-PROMOTE` 把一个叶页面及其已选择的同父 sibling 组织为新 hub，保持相对导航顺序和
保名子树 mapping。

### 输入、输出与不变量

- 输入是已经通过 R-INTERFACE 公共校验的 `promote` request，以及 Runtime 读取的叶角色、
  共同 parent、当前导航、根状态、闭包和 before-tree。调用方不提供 parent、导航、mapping
  或 Check 事实。
- 领域投影与 `move` 共用[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)；
  本页只规定保名 mapping 与领域拒绝。
- 叶页面、hub root 和完整后代都必须按保名子树 mapping 进入新 hub；不得平铺、吸收未
  指定页面或按 operand 顺序重排。

#### Promote 错误决策表

下表只在公共 request、root/runtime admission 和 recovery 门禁均已通过后求值；请求校验、
根错误和 recovery 错误不在本表重复。按阶段从上至下求值，命中一行立即停止，不继续读取
后续事实或拼接多个错误。`page` 先于 sibling 集合；sibling 按规范路径排序，同一集合中
的 parent 也按其所属页面的规范路径首次需要时认证。所有“直接内容”均指 before-state 已
读取并认证的页面、parent 或导航事实；不把本操作正要形成的 after-state 当作 before-state
违规。

| 阶段与前置事实 | 唯一结果（零写） | 反例：即使后续事实也成立，仍只命中本行 |
| --- | --- | --- |
| 1. 必需路径：`page`、按规范路径排序的每个 `sibling`，或认证它们所需的既有 parent 路径不存在 | 选择 `operation.path_not_found`；同一无序集合按规范路径选择第一个缺失路径，零写 | `page` 已存在但已经认证为 hub，同时最小 sibling 路径不存在；不选择 `promote.candidate_not_leaf` |
| 2. 管理状态：路径已存在，但命令要求受管理的 `page`、sibling 或必需 parent 未纳管 | 选择 `operation.path_not_managed`；candidate 和 registered 都只接受受管理的 promote 操作数，零写 | `page` 与其余 sibling 都存在且已纳管，但规范顺序第一的 sibling 存在且未纳管、其 after-path 还会碰撞；只选择 `operation.path_not_managed` |
| 3. 直接内容认证：已读取的页面、parent 或导航明确违反规则 | 复用同一事实的 Check code；零写，不新增 `operation.content_invalid` | `page.frontmatter_invalid` 已认证，而链接闭包暂时不可取得；只选择 `page.frontmatter_invalid`，不选择 `runtime.closure_incomplete` |
| 4. 命令资格：先检查 `page` 是否为叶；若不是，选择 `promote.candidate_not_leaf`。仅当它是叶时，才按 sibling 规范路径顺序检查共同 parent；首个不匹配选择 `promote.parent_mismatch` | 资格错误零写；不因 operand 输入排列改变选择顺序 | `page` 已认证为 hub，且新 hub 的一个目标路径已被占用；只选择 `promote.candidate_not_leaf`，不选择 `operation.path_conflict` |
| 5. 计划事实：形成保名 mapping、parent/导航、链接或 after-state 所需事实在首次需要时无法完整稳定取得 | 选择 `runtime.closure_incomplete`；零写，mapping 不新增未知范围 | registered after-state 预计会有 Check error，但链接闭包无法稳定取得；只选择 `runtime.closure_incomplete`，不运行最终 Check |
| 6. 碰撞或已认证的最终路径状态：未选择页面将被吸收、目标路径或 mapping after-path 被其他对象占用 | 选择 `operation.path_conflict`；零写 | mapping 已完整，某 after-path 被占用且 registered after-state 另有导航 error；只选择 `operation.path_conflict`，不提前选择 `operation.check_failed` |
| 7. registered 完整 after-state Check：此前计划、mapping 和碰撞均已通过，且无过滤完整 Check 仍有 error | 选择 `operation.check_failed`；warning 不阻止，candidate 不因闭包外既有 error 阻止，零写 | registered mapping 已形成、无碰撞，完整 after-state Check 只认证出一个 `navigation.invalid`；只选择 `operation.check_failed` |

重复 sibling 已在 [R-INTERFACE 共用值与输入校验](../runtime/interface.md#共用值与输入校验)阶段拒绝；它不进入
本表。叶页面升格为 hub 是合法 before-state，故不会因未来 hub 结构被本表第 3 阶段提前拒绝。
若 1–7 阶段均未命中，才允许形成一次完整 promote 计划；registered 还必须在第 7 阶段的
完整 after-state Check 无 error 后提交，candidate 则不因闭包外的既有 error 被阻止。

### 提交、副作用、失败与恢复

先固定完整保名 mapping，再在同一领域提交中更新路径、parent、必要导航和可证明链接。
若 after-tree 产生可证明的空非根 hub，collapse 必须并入同一 mapping，不能另开成功。
`conclusions.md` 的既有条目只可按已证明 mapping 改写链接目标，其余语义不变。

碰撞、路径逃逸、重复、跨父、不可认证闭包或耐久不确定均不返回成功；前者写前零写，
后者保留 recovery 现场。恢复后重试重新认证同父、顺序和完整 mapping，不能只比较
operand 列表。

### 验证

- 覆盖同父顺序保持、跨父拒绝、重复 sibling、叶/hub 角色错误、未指定页面与链接闭包失败。
- 证明前后 mapping 保名且不平铺，并且自动 collapse 属于触发 `promote` 的同一内部逻辑
  operation、同一领域提交、同一 mapping 和恢复记录的子阶段，不产生第二个公共结果。

## collapse

`D-COLLAPSE` 处理空的非根 hub。它可以是显式请求，也可以作为 `move` 或 `promote` 的
after-tree 子阶段；自动来源沿用触发操作的同一内部逻辑 operation、同一领域提交、同一 mapping
和恢复记录，两种来源都不得改变结果和恢复边界。

### 输入、输出与不变量

- 显式请求先通过 R-INTERFACE 公共校验；触发方式、after-tree、目录物理状态、链接闭包和
  根状态由 Runtime 读取或推导。自动子阶段使用触发操作已经认证的事实，不接收额外调用方
  字段。
- 内部形成可证明的 hub-to-leaf（或等价页面角色）mapping、必要导航和链接更新，或选择领域拒绝；
  成功投影消费[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)。
- 根 hub 不能 collapse；成功 after-tree 不得留下任何由本操作产生且可证明为空的非根 hub。

#### Collapse 错误决策表

下表同样只在公共 request、root/runtime admission 和 recovery 门禁均已通过后求值；请求、
根和 recovery 错误由公共准入 owner 处理。显式 `collapse` 按阶段从上至下求值并在首个命中
处停止。`move`/`promote` 的自动子阶段沿用同一计划和同一错误优先级：它从触发操作已经
认证的 before-state 进入，候选 after-tree 或处理顺序在首次需要时不可认证就命中第 5 阶段，
错误归属于触发操作的同一内部逻辑 operation，不产生第二个公共结果。

| 阶段与前置事实 | 唯一结果（零写） | 反例：即使后续事实也成立，仍只命中本行 |
| --- | --- | --- |
| 1. 必需路径：`hub`，以及形成既有 parent/导航事实首次需要的必需路径不存在 | 选择 `operation.path_not_found`；按规范路径选择第一个缺失路径，零写；未取得的角色或碰撞事实不参与选择 | `hub` 已认证为 hub 且仍有受管理子页面，但其必需 parent 路径缺失；不选择 `collapse.hub_not_empty` |
| 2. 管理状态：路径已存在，但命令要求受管理的 `hub` 或必需 parent 未纳管 | 选择 `operation.path_not_managed`；零写 | `hub` 已存在但未纳管，同时其 collapse after-path 已被占用；只选择 `operation.path_not_managed` |
| 3. 直接内容认证：已读取的目标、parent 或导航明确违反规则 | 复用同一事实的 Check code；零写；目标自身 `page.role_invalid`/`empty_non_root_hub` 是本操作要修复的合法 before-state，不在此阶段拒绝 | 目标导航已认证为 `navigation.invalid`，而链接闭包无法取得；只选择 `navigation.invalid`，不选择 `runtime.closure_incomplete` |
| 4. 命令资格：先拒绝根或非 hub：`collapse.target_not_collapsible`；root/not_hub 的事实分区按 [R-ERRORS 命令特有领域 code 登记](../runtime/errors-and-observability.md#错误登记)投影。仅当目标是非根 hub 时，才拒绝仍有受管理子页面的 `collapse.hub_not_empty` | 资格错误零写；非 Markdown、附件和其他 retained 节点不计为受管理子页面 | 目标是根页面且 after-path 已被占用；只选择 `collapse.target_not_collapsible`，不选择 `operation.path_conflict` |
| 5. 计划事实：形成 hub-to-leaf mapping、parent/导航、链接、目录物理状态或 after-state 所需事实在首次需要时无法完整稳定取得 | 选择 `runtime.closure_incomplete`；零写。自动子阶段的 after-tree、候选集合或顺序不可认证时，整个触发操作同样选择此错误 | after-tree 候选集合不可认证，且若完整 after-state Check 可运行会有 `navigation.invalid`；只选择 `runtime.closure_incomplete`，不运行最终 Check |
| 6. 碰撞或已认证的最终路径状态：collapse 派生 after-path 被其他对象或 retained 路径占用 | 选择 `operation.path_conflict`；零写 | 目标是可证明为空的非根 hub，`hub.md` 已被占用且 registered after-state 另有 error；只选择 `operation.path_conflict`，不选择 `operation.check_failed` |
| 7. registered 完整 after-state Check：此前 mapping、目录边界和碰撞均已通过，且无过滤完整 Check 仍有 error | 选择 `operation.check_failed`；warning 不阻止，candidate 不因闭包外既有 error 阻止，零写 | registered 的空非根 hub 已完成 collapse mapping 且无碰撞，完整 after-state Check 认证出一个 `page.parent_invalid`；只选择 `operation.check_failed` |

显式目标的路径检查在第 1 阶段完成；自动子阶段不得重新解释调用方 operand，也不得以候选
遍历顺序改变错误选择。空的非根 hub 是 collapse 的合法 before-state，不能以
`page.role_invalid`/`empty_non_root_hub` 提前拒绝。
若 1–7 阶段均未命中，显式请求才形成一次 collapse 计划；自动子阶段把可证明的 collapse
mapping 作为触发 `move`/`promote` 的同一内部逻辑 operation、同一领域提交、同一 mapping
和恢复记录的子阶段继续提交，不产生第二个公共结果。

#### Promote/collapse 组合反例

以下夹具专门验证阶段顺序；每个组合都含有一个更晚才可能命中的事实，但结果只有最早依赖
项。它们不改变 `move` 的 20 个 source/managedness/root/target 分区。

| 组合 | 反例夹具 | 唯一结果 |
| --- | --- | --- |
| 缺失 + 角色错误 | promote 的 sibling 路径不存在，同时 `page` 已认证为 hub | `operation.path_not_found` |
| 内容违规 + 闭包不可用 | collapse 目标导航已认证为 `navigation.invalid`，同时链接闭包无法稳定读取 | `navigation.invalid` |
| 角色错误 + 碰撞 | promote 的 `page` 已认证为 hub，同时新 hub after-path 已被占用 | `promote.candidate_not_leaf` |
| 闭包不可用 + 最终 Check error | collapse 的 after-tree 无法认证；若闭包完整，registered after-state 会认证 `navigation.invalid` | `runtime.closure_incomplete` |

### 提交、副作用、失败与恢复

自动 collapse 反复依据每轮已证明的 after-tree 收敛，直到不再存在可证明为空的非根 hub；
它属于触发 `move`/`promote` 的同一内部逻辑 operation、同一领域提交、同一 mapping 和恢复记录的子阶段，
不产生第二个公共结果。固定的 collapse after-path 是 `pages/topic/index.md ->
pages/topic.md`；父导航、入站/出站链接和碰撞检查均从触发操作的同一 mapping 派生。每轮
候选的精确排序和 reverse-collapse proof 属于 Draft 私有证明资料，不是公共合同，所以不能
以遍历顺序猜结果。可删除目录必须额外证明物理为空，非 Markdown 资源或未知节点使目录保留。

mapping 可证明时，同一提交更新路径、parent、导航、链接和已有结论条目链接目标。失败前
零写；耐久不确定保留 recovery 现场，恢复后从认证后的 authority 重新计算 after-tree，不
从临时目录状态推断已完成。

### 验证

- 覆盖根、非 hub、非空 hub、空 hub、自动触发、多轮收敛、混合资源目录与不可认证
  after-tree。
- 验证自动 collapse 是触发结构命令的同一内部逻辑 operation、同一领域提交、同一 mapping
  和恢复记录的子阶段，不能产生第二个公共结果或部分成功；目录非空或未知时始终保留。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
