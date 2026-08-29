# 结构操作

## move

`D-MOVE` 定义 `move SOURCE... TARGET` 的来源解释、页面纳管、mapping 和原子领域结果。
它只定义领域 after-state，不复制 runtime 的 manifest 编码、耐久顺序或恢复算法。
公开结果不提供 source/destination 路径列表；内部 operation 必须保留唯一的完整 mapping，
供路径、父关系、导航、链接闭包、自动 collapse、manifest 和 recovery 共同消费。

### 输入、输出与不变量

- 输入是一个或多个规范相对 `SOURCE`、一个 `TARGET`、根状态、recovery overlay、来源
  形状、目标形状、全根可判定 Markdown 闭包和可认证的 before-tree。
- 以 `.md` 结尾的 `TARGET` 是单文件精确目标；其他 `TARGET` 是前缀目标。单文件目标
  不能接收多个 source 或目录 source。
- 成功输出使用与 `promote` 相同的领域 `Outcome` 边界：operation identity、lifecycle
  result、warnings 和 errors 属于同一个结果；公开结果不暴露路径列表。字段名、字段顺序、
  operation identity 的生成/复用规则是 runtime ABI，仍为 `unresolved`。
- 成功前必须形成唯一、规范化、去重且确定的 `before -> after` mapping。mapping 以规范化
  before path 为键，逻辑顺序由规范化键唯一确定；具体持久化/序列化遍历顺序仍属 runtime
  `unresolved`。路径、parent、导航、可证明链接改写、自动 collapse、manifest 和 recovery
  都只能从该 mapping 派生。
- mapping 只包括实际移动的受管理或获准纳管 Markdown。retained 节点、附件/其他非
  Markdown、`.draft`、目录清理和内部日志不生成 mapping 项，也不改变公开结果边界。

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
`registered` 是根状态，`file` target 当且仅当以 `.md` 结尾，其他 target 均为 `prefix`。
因此每行都是四个维度的互斥组合；所有允许行还必须满足无 recovery pending、路径/闭包/
before-tree/mapping 可认证，以及目录节点分类和边界可认证。

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
| file-set | managed | candidate | file | 拒绝，零写；单文件 target 不能接收多个 source。 |
| file-set | managed | candidate | prefix | 允许进入完整计划。 |
| file-set | managed | registered | file | 拒绝，零写；单文件 target 不能接收多个 source。 |
| file-set | managed | registered | prefix | 允许进入完整计划。 |
| file-set | unmanaged | candidate | file | 拒绝，零写；单文件 target 不能接收多个 source。 |
| file-set | unmanaged | candidate | prefix | 允许页面纳管并进入完整计划；目标必须是页面命名空间中的合法前缀目标。 |
| file-set | unmanaged | registered | file | 拒绝，零写；单文件 target 不能接收多个 source，且不得把 candidate 的纳管权限扩张到 registered。 |
| file-set | unmanaged | registered | prefix | 拒绝，零写；不得把 candidate 的纳管权限扩张到 registered。 |
| directory | uniform-managed | candidate | file | 拒绝，零写；目录 source 不能接收单文件 target。 |
| directory | uniform-managed | candidate | prefix | 按 [目录来源](#目录来源) 形成 mapping，进入同一次完整计划。 |
| directory | uniform-managed | registered | file | 拒绝，零写；目录 source 不能接收单文件 target。 |
| directory | uniform-managed | registered | prefix | 按 [目录来源](#目录来源) 形成 mapping，进入同一次完整计划。 |
| directory | uniform-unmanaged | candidate | file | 拒绝，零写；目录 source 不能接收单文件 target。 |
| directory | uniform-unmanaged | candidate | prefix | 仅纳管其中可移动 Markdown 并按 [目录来源](#目录来源) 形成 mapping；非 Markdown 和可认证的 retained 节点原地保留，公开结果不得泄露留置事实。 |
| directory | uniform-unmanaged | registered | file | 拒绝，零写；不得创建或改写页面、导航、catalog/head，也不得产生日志副作用。 |
| directory | uniform-unmanaged | registered | prefix | 拒绝，零写；不得创建或改写页面、导航、catalog/head，也不得产生日志副作用。 |

### 矩阵外门禁（统一结果）

下列事实不属于上述 24 个合法键。无论 target 是 `file` 还是 `prefix`，也无论 source
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
| 任一 source、闭包节点、before/after tree、mapping 或链接/导航事实不可认证 | 任意合法键或目录内容分区 | 拒绝，零写；不返回可见结构成功，不从 lower-bound 或缓存事实继续。 |
| 目录边界、节点分类或物理空性无法认证 | `directory` source 的任意 managed/root/target | 拒绝，零写；不得先移动 Markdown 再决定是否保留/清理目录，保留目录及诊断。 |

candidate 纳管仅适用于已认证的 Markdown source；目录 candidate 纳管还必须满足 [目录来源](#目录来源)
的可移动 Markdown mapping 和 retained 节点边界。任何矩阵外事实都不能通过辅助章节的页面
所有权、parent/nav 或链接规则反向放宽。

### mapping、提交与副作用

完整计划先固定唯一的规范化 mapping，再从它派生所有变化。mapping 必须覆盖每个实际移动
的 Markdown，按规范化 before path 去重；任一 source 重复、after path 重复、after path
与未移动节点/保留路径冲突，均构成碰撞并在写前拒绝。source 参数的排列只改变输入顺序，
不得改变 mapping、parent、导航、链接闭包或结果。公开结果不提供路径列表，也不把目录残留、
retained 事实或内部日志转成结果字段。

对 prefix target，mapping 的 after path 唯一按以下函数计算（所有路径先规范化为根内相对
路径，再做碰撞检查）：

| source 形状 | 每个可移动 Markdown 的 after path |
| --- | --- |
| 单文件 | `TARGET/<source basename>` |
| 目录 | `TARGET/<source directory name>/<member relative path>` |
| file-set | 对每个文件分别取 `TARGET/<source basename>` |

目录项的 `member relative path` 相对于 source 目录，保留其已认证的子目录层级；retained、
附件/其他非 Markdown、`.draft` 和目录清理不生成 mapping 项。对 `.md` file target，只有
单文件 source 可以把该 source 映射到精确 `TARGET`；file-set 和 directory 的 file target
仍在写前拒绝。规范化后的绝对路径、路径逃逸、保留 `pages/index.md`、别名重复或任一
after collision 都是零写失败。具体序列化字段、遍历和持久化顺序仍是 runtime ABI 的
`unresolved`，但 mapping 的数学内容与 source 顺序无关。

### D-MOVE snapshot S 与漂移门禁

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
序列化顺序；这些 ABI 由 runtime owner 另行定义，尚属 `unresolved`。

任何 forward、partial-after 或授权 cleanup 前，都必须在锁内重新认证 `S` 完整且与当前
authority、树、closure、mapping、目录边界和窄改写事实匹配。若出现明确矛盾、摘要不匹配或
事实不一致，结果为 `runtime.corrupt`：零写、保留现场，禁止 forward。单纯缺失证据，或
Q20/Q21 尚未取得资格证明，结果为 `unresolved`：同样零写、保留现场并禁止 forward；不得
把缺失升级成矛盾，也不得以缓存、lower-bound 或临时目录状态补齐 `S`。

若 mapping 后的树可证明产生空的非根 hub，自动 collapse 必须作为同一 logical operation
和同一领域提交的子阶段映射纳入该 mapping；不能另开 operation 或先报告 move 成功。精确
的候选遍历顺序、子阶段字段和 reverse proof 没有统一已批准 ABI，故为 `unresolved`。不
能认证 after-tree 或 collapse 顺序时，停止并零写，而不是选择一个目录扫描策略。

提交点是页面路径、parent、必要导航、可证明链接 span 和附带 collapse after-state 同时
可见的时刻。`conclusions.md` 仅在 mapping 可证明时改写已有条目的链接目标，且不改成员、
分组、顺序、标签、质疑来源或语义；这些语义由 `generate-conclusions` 单独重算。

### 失败、零写、重试与恢复

- 碰撞、路径逃逸、重复、闭包不完整、链接或导航不可认证、目录边界不成立均在写前失败。
- 提交后无法认证耐久结果时不返回成功；runtime 保留 recovery 现场，普通 `move` 重试
  被阻止，直到显式 recovery 收敛。
- 收敛后重试重新证明整个 mapping。相同 request/authority snapshot 的 operation 复用、
  attempt 和旧操作关联由 runtime 决定；本页不以路径相同推断幂等成功。

### 验证

- 逐行覆盖上述 24 个 source 形状、管理状态、根状态和 target 形状分区，并额外覆盖混合
  source、重复 source、碰撞、路径逃逸和 `pages/index.md` 门禁。
- 验证 candidate 的 uniform-unmanaged directory + prefix 只纳管可移动 Markdown，非 Markdown 和
  已识别的 retained 节点原地保留且不泄露留置事实；registered 的 uniform-unmanaged directory 在
  两种 target 形状下均拒绝、零写且没有页面、导航、catalog/head 或日志副作用。
- 验证 candidate 纳管与 registered 拒绝的分区，及所有路径/parent/导航/链接只由一个
  mapping 派生。
- 验证自动 collapse 不产生第二 operation，mapping 不可认证和耐久不确定均不产生可见成功。

## 目录来源

`D-MOVE-OUTPUT` 只定义目录 source 的资源边界和窄输出；它不重新定义 `move` 的 target
解释或公共 Outcome ABI。

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
- 成功输出只含当前命令必要的业务事实；不得枚举、计数、摘要 retained 内容，也不得加入
  retained facts、截断标志或续读协议。公开结果不提供路径列表。

下表按“成员分类 -> retained 边界 -> mapping/物理状态”判定；`no-movable`、
`uniform-managed`、`uniform-unmanaged`、`mixed`、`unknown` 和 `unverifiable` 这些成员
分类互斥。证明失败行优先于任何可分类行，因而不会出现“先移动再决定”的第二结果。

| 成员分类与 retained 边界（互斥分区） | mapping / 物理状态 | 唯一领域结果与输出边界 |
| --- | --- | --- |
| `uniform-managed`，且无 retained 节点、目录边界与所有事实可认证 | mapping 完整；写前证明移动后目录物理为空 | 只移动 Markdown；仅在独立 owner 清理规则也获证明时移除目录；输出只含必要事实。 |
| `uniform-managed`，且含 retained 非 Markdown/附件/类型未知节点 | mapping 完整；写前证明移动后目录物理非空 | 只移动 Markdown；retained 节点原位保留，不进入 ownership、mapping 或成功输出。 |
| `uniform-unmanaged`，且无 retained 节点、目录边界与所有事实可认证 | mapping 完整；candidate/registered admission 由来源矩阵决定 | candidate 只纳管可移动 Markdown；registered 按矩阵拒绝；输出不增加 retained 事实。 |
| `uniform-unmanaged`，且含 retained 非 Markdown/附件/类型未知节点 | mapping 完整；写前证明移动后目录物理非空 | candidate 只纳管可移动 Markdown；registered 按矩阵拒绝；retained 节点原位保留，不进入 ownership 或 mapping。 |
| `no-movable`，且目录为空或仅含事实可认证的 retained 非 Markdown/附件/可认证存在但类型未知的节点 | 空 mapping；目录物理状态已在写前认证 | 不发生结构写；no-op 的公开 ABI 仍为 `unresolved`，不得返回可见 move 成功或 retained 摘要；只保留必要诊断。 |
| `mixed`、`unknown` 或 `unverifiable` | 分类集合或边界不可用于 admission | 写前拒绝、零写并保留成员级诊断；不得部分移动、部分纳管或把目录当 ownership。 |
| 上述任一可分类分区，但 Markdown mapping 不完整 | mapping 不可认证 | 写前拒绝、零写并保留诊断；不移动任何节点，不进行目录清理。 |
| 上述任一可分类分区，但移动后目录空性或非空性无法认证 | 物理状态不可认证 | 写前拒绝、零写并保留目录及诊断；不得先移动 Markdown 再决定保留或清理。 |

目录 source 的 before-to-after mapping 参与同一个 `move` 提交和任何自动 collapse；retained
节点不生成 mapping 项。恢复或重试也不得扩张输出边界：即使保留现场，也不能把目录残留变成
新的结果字段。目录清理只有在独立物理空性和 owner 证明同时成立时才可发生。

### 失败、恢复与验证

任一节点类型、路径、mapping、闭包或目录物理状态不可信时，写前失败并保留现场；耐久不确定
时由 runtime 的 explicit recovery 处理。验证纯 Markdown、Markdown+非 Markdown、
Markdown+可认证 retained 节点、仅 retained/空目录（事实可认证）、mixed/unknown/unverifiable
节点和空性不可认证反例，确认非 Markdown/retained 从不移动、不取得所有权，成功输出从不泄露
retained facts；并验证 registered uniform-unmanaged directory 的 `file` 与 `prefix` target
均在矩阵行中拒绝零写。

## promote

`D-PROMOTE` 把一个叶页面及其已选择的同父 sibling 组织为新 hub，保持相对导航顺序和
保名子树 mapping。

### 输入、输出与不变量

- 输入是待升格叶页面、被选择的 sibling、共同父页面、当前导航、根状态、闭包和
  before-tree。
- 输出与 `move` 共用同一 `Outcome` 领域边界；公开结果不提供路径列表，精确字段 ABI 仍为
  `unresolved`。
- 叶页面、hub root 和完整后代都必须按保名子树 mapping 进入新 hub；不得平铺、吸收未
  指定页面或按 operand 顺序重排。

| 输入事实 | mapping / 导航证明 | 结果 |
| --- | --- | --- |
| 待升格项是叶，所有选定项与它同父且不重复 | 全部保名 after-path、parent、导航相对顺序和链接闭包可认证 | 允许形成一次完整 promote 计划。 |
| 选定项跨父 | 任意 | 拒绝；先显式 `move`，零写。 |
| 待升格项不是叶、选定项重复、父页缺失或包含未指定页面 | 任意 | 拒绝，零写。 |
| mapping、闭包、导航或路径角色不可认证 | 任意 | 拒绝，零写。 |
| 根为 recovery pending | 任意 | 阻止，等待显式 recovery。 |

### 提交、副作用、失败与恢复

先固定完整保名 mapping，再在同一领域提交中更新路径、parent、必要导航和可证明链接。
若 after-tree 产生可证明的空非根 hub，collapse 必须并入同一 mapping，不能另开成功。
`conclusions.md` 的既有条目只可按已证明 mapping 改写链接目标，其余语义不变。

碰撞、路径逃逸、重复、跨父、不可认证闭包或耐久不确定均不返回成功；前者写前零写，
后者保留 recovery 现场。恢复后重试重新认证同父、顺序和完整 mapping，不能只比较
operand 列表。

### 验证

- 覆盖同父顺序保持、跨父拒绝、重复 sibling、叶/hub 角色错误、未指定页面与链接闭包失败。
- 证明前后 mapping 保名且不平铺，公开结果不产生路径列表，并且自动 collapse 没有第二
  operation。

## collapse

`D-COLLAPSE` 处理空的非根 hub。它可以是显式请求，也可以作为 `move` 或 `promote` 的
after-tree 子阶段；两种来源都不得改变 mapping、结果和恢复边界。

### 输入、输出与不变量

- 输入是 collapse 触发方式、候选 hub、after-tree、目录物理状态、链接闭包和根状态。
- 输出是可证明的 hub-to-leaf（或等价页面角色）mapping、必要导航和链接更新，或拒绝；
  精确字段 ABI 仍为 `unresolved`，公开结果不提供路径列表。
- 根 hub 不能 collapse；成功 after-tree 不得留下任何由本操作产生且可证明为空的非根 hub。

| 触发 | 候选与 after-tree 事实 | 结果 |
| --- | --- | --- |
| 显式 | 非根 hub 可证明为空，mapping、导航、闭包和目录状态可认证 | 形成一次 collapse 计划。 |
| 显式 | 根、非 hub、非空 hub 或事实不可认证 | 拒绝，零写。 |
| `move` / `promote` 的自动子阶段 | after-tree 可证明出现空非根 hub | 将 collapse mapping 加入触发操作的同一计划和提交。 |
| `move` / `promote` 的自动子阶段 | after-tree、候选集合或处理顺序不可认证 | 整个触发结构操作零写；不把未完成 collapse 留作成功后的独立修复。 |
| 任一 | recovery pending | 阻止，等待显式 recovery。 |

### 提交、副作用、失败与恢复

自动 collapse 反复依据每轮已证明的 after-tree 收敛，直到不再存在可证明为空的非根 hub；
它不生成第二 operation。固定的 collapse after-path 是 `pages/topic/index.md ->
pages/topic.md`；父导航、入站/出站链接和碰撞检查均从触发操作的同一 mapping 派生。每轮
候选的精确排序和 reverse-collapse proof 尚未有唯一签署合同，为 `unresolved`，所以不能
以遍历顺序猜结果。可删除目录必须额外证明物理为空，非 Markdown 资源或未知节点使目录保留。

mapping 可证明时，同一提交更新路径、parent、导航、链接和已有结论条目链接目标。失败前
零写；耐久不确定保留 recovery 现场，恢复后从认证后的 authority 重新计算 after-tree，不
从临时目录状态推断已完成。

### 验证

- 覆盖根、非 hub、非空 hub、空 hub、自动触发、多轮收敛、混合资源目录与不可认证
  after-tree。
- 验证自动 collapse 是触发结构命令的子阶段，不能产生独立路径列表、operation 或部分
  成功；目录非空或未知时始终保留。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
