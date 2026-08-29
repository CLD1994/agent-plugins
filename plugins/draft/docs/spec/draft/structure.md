# 结构操作

## move

`D-MOVE` 定义 `move SOURCE... TARGET` 的来源解释、页面纳管、mapping 和原子领域结果。
它只定义领域 after-state，不复制 runtime 的 manifest 编码、耐久顺序或恢复算法。

### 输入、输出与不变量

- 输入是一个或多个规范相对 `SOURCE`、一个 `TARGET`、根状态、recovery overlay、来源
  形状、目标形状、全根可判定 Markdown 闭包和可认证的 before-tree。
- 以 `.md` 结尾的 `TARGET` 是单文件精确目标；其他 `TARGET` 是前缀目标。单文件目标
  不能接收多个 source 或目录 source。
- 成功输出使用与 `promote` 相同的领域 `Outcome` 边界：operation identity、
  `changed_paths`、lifecycle result、warnings 和 errors 属于同一个结果。字段名、字段
  顺序、operation identity 的生成/复用规则是 runtime ABI，仍为 `unresolved`。
- 成功前必须形成唯一的、规范化且去重的有序 `before -> after` mapping。路径、parent、
  导航、可证明链接改写和自动 collapse 都只能从该 mapping 派生。

### 来源决策矩阵

先按 source 集合分类：`single-file` 表示恰好一个文件 source，`file-set` 表示两个或更多
文件 source，`directory` 表示单个目录 source；文件/目录混合、多个目录或无法确定 source
形状的集合不进入下表。`managed`
表示 source 的页面所有权已经存在，`unmanaged` 表示尚未取得页面所有权；管理状态必须对本次
source 集合可认证，混合或未知状态按矩阵外门禁处理。`candidate` 与 `registered` 是根状态，
`file` target 当且仅当以 `.md` 结尾，其他 target 均为 `prefix`。因此每行都是四个维度的一个
互斥组合；所有允许行还必须满足无 recovery pending、路径/闭包/before-tree/mapping 可认证，
以及目录节点分类和边界可认证。

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
| directory | managed | candidate | file | 拒绝，零写；目录 source 不能接收单文件 target。 |
| directory | managed | candidate | prefix | 按 [目录来源](#目录来源) 形成 mapping，进入同一次完整计划。 |
| directory | managed | registered | file | 拒绝，零写；目录 source 不能接收单文件 target。 |
| directory | managed | registered | prefix | 按 [目录来源](#目录来源) 形成 mapping，进入同一次完整计划。 |
| directory | unmanaged | candidate | file | 拒绝，零写；目录 source 不能接收单文件 target。 |
| directory | unmanaged | candidate | prefix | 仅纳管其中可移动 Markdown 并按 [目录来源](#目录来源) 形成 mapping；非 Markdown 和可认证的未知节点原地保留，输出不得泄露留置事实。 |
| directory | unmanaged | registered | file | 拒绝，零写；不得创建或改写页面、导航、catalog/head，也不得产生日志副作用。 |
| directory | unmanaged | registered | prefix | 拒绝，零写；不得创建或改写页面、导航、catalog/head，也不得产生日志副作用。 |

下列门禁不属于上述 24 个组合，统一在写入前拒绝并保持零写：文件/目录混合 source、
多个目录 source、管理状态混合或未知、`.draft` 或非规范路径、路径逃逸、重复 source、
目标碰撞、保留 `pages/index.md`、任一 source/闭包节点/mapping 不可认证，以及 recovery
pending。目录 candidate 纳管只有在可移动 Markdown 的 mapping 完整时成立；无 Markdown、
无法分类的节点或目录边界/空性无法认证时，按 [目录来源](#目录来源) 的 fail-closed 规则处理，
不得伪造成功或扩张输出。

### mapping、提交与副作用

完整计划先固定去重的规范化 mapping，再从它派生所有变化。`changed_paths` 是 mapping 的
公开路径投影：去重后按规范化路径的字典序输出。它不能通过枚举目录残留、基名猜测或旧
导航文本补充。

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
- 验证 candidate 的 unmanaged directory + prefix 只纳管可移动 Markdown，非 Markdown 和
  已识别的 unknown 节点原地保留且不泄露留置事实；registered 的 unmanaged directory 在
  两种 target 形状下均拒绝、零写且没有页面、导航、catalog/head 或日志副作用。
- 验证 candidate 纳管与 registered 拒绝的分区，及所有路径/parent/导航/链接只由一个
  mapping 派生。
- 验证自动 collapse 不产生第二 operation，mapping 不可认证和耐久不确定均不产生可见成功。

## 目录来源

`D-MOVE-OUTPUT` 只定义目录 source 的资源边界和窄输出；它不重新定义 `move` 的 target
解释或公共 Outcome ABI。

### 输入、输出与不变量

- 输入是一个已经通过 `D-MOVE` source/target 门槛的目录、其中的节点类型、可移动
  Markdown mapping 和目录的物理空性证明。
- 目录 source 只移动 Markdown。非 Markdown 内容、附件、未知节点和它们所在的非空目录
  原地保留，不取得页面所有权。
- 成功输出只含当前命令必要的业务事实和公开 `changed_paths`。它不得枚举、计数、摘要
  留置内容，也不得加入 retained facts、截断标志或续读协议。

| 目录内容与证明 | mapping / 物理状态 | 领域结果与输出边界 |
| --- | --- | --- |
| 仅有可移动 Markdown | mapping 完整，移动后目录可证明为空 | 移动 Markdown；目录可按结构清理规则移除；输出仍只含必要事实与 `changed_paths`。 |
| Markdown 与非 Markdown 混合 | Markdown mapping 完整，目录非空 | 只移动 Markdown；所有非 Markdown 原位保留；输出不描述留置集合。 |
| 无 Markdown，或非 Markdown / 未知节点无法分类 | 无可认证 mapping | 拒绝或无可执行结构变化，具体 no-op ABI 为 `unresolved`；不得伪造成功输出。 |
| 目录空性无法认证 | 任意 | 保留物理目录；不以页面 mapping 推断可删除。 |

目录 source 的 before-to-after mapping 参与同一个 `move` 提交和任何自动 collapse。恢复或
重试也不得扩张输出边界：即使保留现场，也不能把目录残留变成新的结果字段。

### 失败、恢复与验证

任一节点类型、路径、mapping 或目录空性不可信时，写前失败并保留现场；耐久不确定时由
runtime 的 explicit recovery 处理。验证纯 Markdown、混合目录、仅非 Markdown、未知节点
和空目录，确认非 Markdown 从不移动且成功输出从不泄露 retained facts。

## promote

`D-PROMOTE` 把一个叶页面及其已选择的同父 sibling 组织为新 hub，保持相对导航顺序和
保名子树 mapping。

### 输入、输出与不变量

- 输入是待升格叶页面、被选择的 sibling、共同父页面、当前导航、根状态、闭包和
  before-tree。
- 输出与 `move` 共用同一 `Outcome` 领域边界和 `changed_paths` 规则；精确 ABI 仍为
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
- 证明前后 mapping 保名且不平铺，`changed_paths` 去重排序，并且自动 collapse 没有第二
  operation。

## collapse

`D-COLLAPSE` 处理空的非根 hub。它可以是显式请求，也可以作为 `move` 或 `promote` 的
after-tree 子阶段；两种来源都不得改变 mapping、结果和恢复边界。

### 输入、输出与不变量

- 输入是 collapse 触发方式、候选 hub、after-tree、目录物理状态、链接闭包和根状态。
- 输出是可证明的 hub-to-leaf（或等价页面角色）mapping、必要导航和链接更新，或拒绝；
  精确路径算法与公开字段仍为 `unresolved`。
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
它不生成第二 operation。每轮候选的精确排序和 reverse-collapse proof 尚未有唯一签署合同，
为 `unresolved`，所以不能以遍历顺序猜结果。可删除目录必须额外证明物理为空，非 Markdown
资源或未知节点使目录保留。

mapping 可证明时，同一提交更新路径、parent、导航、链接和已有结论条目链接目标。失败前
零写；耐久不确定保留 recovery 现场，恢复后从认证后的 authority 重新计算 after-tree，不
从临时目录状态推断已完成。

### 验证

- 覆盖根、非 hub、非空 hub、空 hub、自动触发、多轮收敛、混合资源目录与不可认证
  after-tree。
- 验证自动 collapse 是触发结构命令的子阶段，不能产生独立 `changed_paths`、operation
  或部分成功；目录非空或未知时始终保留。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
