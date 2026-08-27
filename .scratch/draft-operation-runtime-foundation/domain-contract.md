# Draft v1 领域行为合同

Status: proposed

## 1. 范围与权威边界

本文定义 Draft v1 的非 runtime 领域行为：根状态、页面命名空间、页面状态、正式关系、正文资源身份，以及 create、move、promote、collapse、status、relation、generate-conclusions、adopt、Trash、Restore 和 read 的语义。

`.scratch/draft-operation-runtime-foundation/spec.md` 单独定义持久化、并发、durability、自动生命周期、handoff、索引 authority 与恢复。公共 request/result 类型、CLI 词法、JSON 字段和退出码可以进一步精化本文的语义分区，但不得合并本文区分的状态或改变本文的不变量。

本文不定义内部文件布局、record schema、锁算法、索引算法、worker 协议、故障注入 seam 或崩溃恢复步骤。实现不能从这些内部机制反推或改变领域行为。

## 2. 领域对象与状态

### 2.1 根状态

Draft 根有两个稳定领域状态：

- `candidate`：受管理但尚未登记，允许渐进整理并暂时保留检查错误。
- `registered`：已经完成登记，业务写入只从完整、可认证的领域事实开始。

恢复未决叠加在稳定根状态之上，不构成第三种根状态。根状态只允许以下转换：

```text
新根 -- initialize(candidate)  --> candidate
新根 -- initialize(registered) --> registered
candidate -- adopt             --> registered
```

不存在从 `registered` 返回 `candidate` 的领域转换。

### 2.2 结论所有权状态

结论所有权与根状态正交：

- `not_generated`：生成器尚未拥有 `conclusions.md`。
- `generated`：生成器拥有 `conclusions.md` 及根导航中的结论链接。

所有权只允许 `not_generated -> generated`，不回退。`adopt` 保留当前结论所有权状态。

### 2.3 页面命名空间

受管理页面由规范根相对路径决定：

- 根页面：`index.md`；
- 结论页面：`conclusions.md`；
- 普通页面：`pages/**/*.md`。

页面资格不依赖文件内容是否完整。进入上述路径的普通 Markdown 文件是受管理页面，即使其标题或机器区域尚不合法；位于 Draft 根内但不属于上述路径的普通 Markdown 是未受管理 Markdown。

`index.md` 与 `conclusions.md` 是受保护页面，不能作为 move、promote、collapse 或 Trash 的来源。`.draft/` 是 runtime 内部区域，不属于页面或正文资源操作数域。

普通页面有两种结构角色：

- 叶页面：`pages/<name>.md`；
- 枢纽页面：`pages/<name>/index.md`，拥有零个或多个直接子页面。

根页面是页面树的唯一根，不使用普通枢纽的升降级命令。

### 2.4 页面状态

机器状态只有以下五种：

| 稳定值 | Markdown 标签 | 含义 |
| --- | --- | --- |
| `pending` | `待讨论` | 问题已记录，尚无当前方案 |
| `proposed` | `当前提案` | 当前方案仍在评审 |
| `confirmed` | `已确认` | 当前有效结论 |
| `superseded` | `已取代` | 已由其他页面正式取代 |
| `closed` | `已关闭` | 讨论结束且没有当前有效结论 |

状态后的说明是自由文本，不属于机器状态。页面状态不是固定单向流程；只要最终不变量成立，页面可以在 `pending`、`proposed`、`confirmed` 与 `closed` 间转换。

`superseded` 与 `superseded_by` 必须双向成立：页面状态为 `superseded` 当且仅当它至少声明一条 `superseded_by`。状态修改不能自行猜测或增删正式关系。

### 2.5 正式关系

正式关系的稳定值为：

| 稳定值 | Markdown 标签 | 写入方向 |
| --- | --- | --- |
| `parent` | `父主题` | 由结构命令维护 |
| `depends_on` | `依赖` | 主动方向 |
| `supersedes` | `取代` | 主动方向 |
| `superseded_by` | `被取代` | `supersedes` 的反向声明 |
| `challenges` | `质疑` | 主动方向 |
| `challenged_by` | `被质疑` | `challenges` 的反向声明 |

关系修改只接受 `depends_on`、`supersedes` 与 `challenges`。调用方通过交换 source/target 表达反向操作；不能直接写反向关系或 `parent`。

关系必须指向同一 Draft 的受管理页面。每个关系行只有一个目标；同一关系不能重复指向同一规范目标。自引用、`depends_on` 环和 `supersedes` 环非法。`supersedes` 与 `challenges` 的正反声明必须配对；`depends_on` 的反向事实由查询派生，不落盘。

### 2.6 页面机器区域与导航

每个页面恰好有一个一级标题。一级标题之后、普通正文之前是唯一连续机器引用块：

- 每页恰好一个状态；
- 除根页面外，每页恰好一个父主题；
- 根页面不声明父主题；
- 正式关系按第 2.5 节表达；
- 未知机器标签、重复单值字段或非法关系目标使页面无效。

父主题与路径树必须一致。父页面的 `## 子主题` 导航恰好一次链接每个直接子页面。生成器拥有结论页时，根导航中的结论链接唯一且始终位于子主题列表末尾。

## 3. 正文资源与链接闭包

### 3.1 资源事实

正文链接与正式关系是不同事实。正文资源由 `ResourceIdentity` 标识，至少区分：

- `kind`：`page`、`local`、`external`；
- `scope`：`draft`、`outside`、`unknown`；
- `target_state`：`present`、`missing`、`unavailable`、`unchecked`、`invalid`。

资源身份由词法位置决定，不因文件出现或消失在 `page` 与 `local` 之间切换。锚点不参与资源身份；同一页面中指向同一资源的多个链接按首次出现去重。外部 URI 不联网检查。

### 3.2 根内 Markdown 链接闭包

会改变页面路径身份或页面可达性的 move、promote、collapse 与 Trash 共享同一闭包：

1. 枚举 `.draft/` 外全部普通 Markdown，包括受管理页面与未受管理 Markdown；
2. 使用同一 CommonMark 提取器识别普通链接、图片和引用式链接，忽略代码围栏；
3. 把每个本地链接解析为 `ResourceIdentity`，或证明它属于不受本次映射影响的类别；
4. 无法安全解码、提取、对齐源码、分类或证明无关的文件进入阻碍集合；
5. 任一阻碍存在时，操作零写失败，并返回稳定排序的有界阻碍事实。

missing 目标仍可具有可证明的词法身份，不自动构成闭包失败。未受管理 Markdown 中看似机器标签的文字只作为普通正文链接；只有受管理页面的机器区域产生正式关系和父导航事实。

生成器拥有的 `conclusions.md` 是闭包的唯一领域例外。结构命令不重写其中的生成列表，也不让其中的过时链接阻止操作；显式 Links 读取仍返回该文件的当前资源事实，下一次 generate-conclusions 负责整体收敛。

### 3.3 路径映射时的链接规则

给定完整的 before-path 到 after-path 映射：

- 被移动文件中的本地相对链接重新基址，使其继续指向移动前的同一资源；
- 指向移动集合的入站本地链接改指对应 after-path；
- 同批成员之间的交叉链接直接映射到各自 after-path；
- 锚点保留，纯页内锚点与外部 URI 不变；
- 正式关系目标、父关系和父导航使用同一映射，但按各自机器结构更新；
- 仅文本相似、文件名相同或搜索命中不能作为重写依据。

## 4. 通用命令规则

### 4.1 根状态与事实门禁

| 根状态 | 请求类别 | 开始业务动作所需领域事实 |
| --- | --- | --- |
| `candidate` | create、status.set、relation.add/remove | 本请求拥有的可证明局部闭包 |
| `candidate` | generate-conclusions | 完整受管理页面、状态和导航闭包 |
| `candidate` | move、promote、collapse、Trash、Restore | 完整根内 Markdown 链接闭包及请求专属事实 |
| `candidate` | adopt | 无过滤、无分页完整 Check 无 error，且 runtime 允许提交 |
| `registered` | 任一业务写 | 完整、有效且可认证的领域事实，以及请求专属闭包 |

未受管理来源只在 `candidate` 根中可用。`registered` 根中的未受管理 Markdown 先作为根不健康事实阻止业务写，不为 move 增加修复特例。

候选命令成功后可以保留与本命令无关的检查错误，但不能违反本命令直接拥有的不变量。无法证明“不存在”“无引用”“无环”或“无冲突”时，不得把未知当作否定事实。

### 4.2 计划、执行与结果

支持 DryRun 的命令与 Execute 使用同一个领域计划。DryRun 返回当前稳定事实下的语义效果，但不改变页面、关系、根状态、Trash 或结论所有权；它不授权以后执行同一计划。

公开效果只有 create、modify、move 与 remove 四种语义。正式成功结果的 changed paths 从这些公开效果派生，不从 runtime artifact 过滤得出。幂等成功可以产生空效果。

任何需要完整集合的命令要么返回完整结果，要么整体失败；不能返回部分写计划或在错误结果中声称业务成功。

## 5. 页面与结构命令

### 5.1 create

create 的输入语义是目标页面路径、非空单行标题和初始状态。初始状态只允许 `pending` 或 `proposed`。

目标必须是 `pages/` 下不存在的叶页面；父页面必须已经存在。create 不创建缺失枢纽，不覆盖节点，也不接受根页面、结论页面或 `.draft/` 内路径。

成功后：

- 新页面具有规范标题、状态和父主题；
- 父导航恰好增加该页面一次；
- 若父页面是根且已有结论链接，新页面链接位于结论链接之前；
- 页面与父导航属于同一个领域变更。

### 5.2 move 与页面纳管

move 接受一个或多个 source 和一个 target。source 可以是：

- 受管理叶页面；
- 受管理枢纽及其完整子树；
- `candidate` 根中的未受管理 Markdown；
- 包含可移动 Markdown 的目录。

`index.md`、`conclusions.md`、根级 `pages/`、`.draft/`、符号链接、非 Markdown 文件和空枚举目录不能作为 source。

target 采用确定性词法分类：

- `.md` 结尾的文件 target 只接受一个文件 source；
- 其他 target 是前缀 target；文件映射为 `target/<source basename>`，目录映射为 `target/<source directory name>/<member relative path>`；
- 每个 after-path 必须位于 `pages/` 页面命名空间；
- after-path 的父页面必须已经存在，或由同批成员的 `index.md` 占据。

以下任一事实使整批零写失败：重复 source、source 祖先/后代重叠、成员被重复枚举、目标碰撞、目标已存在、目标位于任一 source 内、目标父页面无人占据、路径逃逸、未知节点或闭包不完整。source 顺序不能改变接受或拒绝结果。

受管理 source 保持页面协议：更新路径、父主题、旧/新父导航和全部受影响链接。未受管理 source 执行页面纳管：移动 Markdown 原有字节并按第 3 节改写链接，但不推断标题、状态、正式关系或父导航；after-path 因进入页面命名空间而成为受管理页面，机器协议欠账由 Check 逐项报告。

目录 source 只移动 Markdown。其他普通内容保留在原位置；移动后的链接继续指向其原资源身份。结果必须提供有界 retained facts，使调用方能够发现仍留在 source 树中的内容。

不同类型 source 可以出现在同一批次；每个成员按自身类型维护，整个映射作为一个领域变更提交。

### 5.3 promote

promote 执行枢纽升格。输入是一个现有叶页面和至少一个明确指定的受管理子页面或子枢纽根。

所有选定 child 必须能够完整解析、互不重叠，并可无碰撞地改挂到新枢纽下；相同 basename、目标占用或闭包不完整均拒绝。未选定页面保持原位。

成功后：

- `pages/x.md` 变为 `pages/x/index.md`，正文和页面状态保持；
- 选定 child/subtree 移至 `pages/x/` 下并改指新父页面；
- 新枢纽的子导航只包含实际改挂的直接子页面；
- 原父导航以新枢纽路径替换叶页面路径，并移除已改挂 child 的旧导航；
- 正文链接、正式关系和父导航按统一路径映射重写；
- 新枢纽至少有一个直接子页面。

### 5.4 collapse

collapse 只接受非根、没有直接子页面的枢纽。成功时把 `pages/x/index.md` 变为 `pages/x.md`，保持正文和页面状态，更新父关系、父导航及全部受影响链接，并移除不再承载内容的枢纽目录。

根页面或非空枢纽拒绝显式 collapse。move 或 Trash 若使受影响的非根枢纽失去最后一个直接子页面，必须在同一个领域计划中使用相同规则自动 collapse，不能留下空枢纽等待另一条命令。

## 6. 状态与关系命令

### 6.1 status.set

status.set 接受一个现有受管理页面、目标状态，以及以下互斥说明动作之一：保留现有说明、替换说明、清除说明。

命令只改变目标页面的状态和说明，不推断关系。若最终状态与 `superseded_by` 不变量冲突，操作拒绝。生成器拥有的结论页面可以由 Agent 显式设为 `confirmed`；下一次 generate-conclusions 仍会把它重置为 `proposed`。

### 6.2 relation.add/remove

relation.add/remove 接受现有受管理 source、主动关系代码和现有受管理 target。

- add 已完整存在的关系是幂等成功；双向关系只存在一侧时，add 补齐。
- remove 已不存在的关系是幂等成功；双向关系有残留时，remove 清除两侧。
- 添加 `supersedes` 同时把 target 设为 `superseded`。
- 删除 target 的最后一条 `superseded_by` 时，调用方必须显式提供 target 的新状态；可选值为 `pending`、`proposed`、`confirmed` 或 `closed`。
- 任一结果若形成非法配对、自引用、依赖环、取代环或状态矛盾，整个操作拒绝。

## 7. generate-conclusions

generate-conclusions 读取全部受管理页面的标题、状态和唯一导航树，并按根导航的深度优先顺序形成 confirmed 页面扁平列表。结论页自身不进入列表。

| 所有权 before | confirmed 页面 | 当前文件事实 | 结果 |
| --- | --- | --- | --- |
| `not_generated` | 无 | `conclusions.md` 不存在 | 幂等成功；不创建文件、不改变所有权 |
| `not_generated` | 任意 | `conclusions.md` 意外存在 | 冲突；不覆盖、不取得所有权 |
| `not_generated` | 有 | 文件不存在 | 创建结论页、增加根导航末尾链接、所有权变为 `generated` |
| `generated` | 有或无 | 生成物可认证或缺失 | 重建确定内容并修复根导航；所有权保持 `generated` |
| `generated` | 任意 | 无法认证必要页面或导航 | 整体失败；不返回近似列表 |

每次实际生成都把结论页面状态设为 `proposed`。条目只含页面标题和链接，不复制正文或生成摘要。`generated` 下没有 confirmed 页面时仍保留空列表页面和根导航。

生成物不是实时索引。create、move、promote、collapse、status、relation、Trash、Restore 与正文编辑不维护其条目；旧标题、旧路径、已移除页面或遗漏新页面允许暂时存在，并由下一次显式生成覆盖。Check 只验证生成物的基本形状和所有权一致性，不比较条目与当前 confirmed 集合。

## 8. adopt

adopt 只接受 `candidate` 根。它执行无过滤、无分页的完整 Check；只有完整报告没有 error，且 runtime 状态允许提交时，才把同一个根转换为 `registered`。

adopt 不修改 Markdown、不重新生成结论、不推进 Trash 生命周期、不解释来源，也不改变结论所有权。登记生效后根状态只能前向收敛到 `registered`；持久提交与恢复语义由 runtime foundation 定义。

对 `registered` 根调用 adopt 返回“已经登记”的独立领域失败；无效根、检查 error 与恢复未决不得折叠成该结果。

## 9. Trash 与 Restore

### 9.1 Trash

Trash 是 Agent 对页面或子树作出的最终删除授权，不是页面状态。页面不会因年龄、`confirmed`、`closed`、`superseded` 或已进入正式文档而自动成为 Trash。

Trash 只接受受管理普通页面或枢纽子树；根页面和结论页面受保护。请求必须能够证明目标集合、父导航和完整根内 Markdown 链接闭包。

入站引用按以下规则分类：

- 目标集合内部的引用随集合一起保留，不阻塞；
- 目标父页面到直接子页面的规范导航由 Trash 自动移除；
- `conclusions.md` 的生成列表按第 3.2 节例外处理；
- 其他来自集合外的正文链接、正式关系或导航全部阻止 Trash，程序不猜替代目标、不删除语义引用。

成功后目标集合离开受管理页面树，父导航更新，必要的空枢纽自动 collapse，并形成一个具有稳定 operation identity、原路径、精确内容和绝对期限的 retained Trash。恢复资格持续 7 天；`now < restore_expires_at` 才可开始 Restore，边界相等时已过期。之后的 handoff、重试和最终清理由 runtime foundation 定义。

成功结果至少提供后续 Restore 所需的 operation identity 与恢复期限；不能暴露 runtime 私有 artifact 路径。

### 9.2 Restore

Restore 只接受一个 Trash operation identity，不支持 DryRun。新 Restore 只有在以下事实同时成立时才可建立：

- identity 词法有效且对应 retained Trash；
- 当前时间仍在恢复窗口内；
- 没有已生效或正在收敛的 Restore 消耗该 Trash；
- 保留内容、原路径和关系证据可认证；
- 所有恢复目标未被占用，受影响当前页面仍满足记录的前置事实；
- 完整闭包能够证明恢复后的页面、父导航和关系合法。

Restore 成功时只把该 Trash 授权的内容恢复到原路径，恢复原有结构关系并消费该 Trash。它不覆盖现有节点、不另选路径、不猜测 merge。失败不能删除唯一保留内容或把未恢复投影为成功。

Restore 失败至少区分以下语义分区：

| 分区 | 含义 | 保留语义 |
| --- | --- | --- |
| lexical invalid | identity 输入非法 | 零领域变化 |
| missing | 没有对应 Trash | 零领域变化 |
| expired | 未在期限内取得恢复资格 | Trash 按原生命周期保留或推进 |
| consumed | 已被 Restore、accepted handoff 或最终清理消费 | 不重新恢复 |
| conflict | 原路径占用、当前父事实变化或最终结构冲突，但保留证据仍可信 | 不覆盖；Trash 保留 |
| corrupt | 内容、identity、关系或证据无法认证 | 保留现场；不猜测 |
| recovery required | 已有未完成领域变化必须先收敛 | 保留现场；不新建并行恢复 |

精确机器 code 由公共协议定义，但不能合并这些分区。

## 10. read 与 Check

read 是以下封闭成员：Page、Links、Backlinks、Relations、Status、Recoverable 与 Check。所有成功结果都明确表达 `complete`；集合读取在不完整时同时返回有界 `blocking_paths` 事实。

### 10.1 查询语义

| 读取 | 领域结果 | 所需范围 |
| --- | --- | --- |
| Page | 路径、标题、状态、父页面及 child/link/backlink/relation 计数 | 目标页加完整全根计数 |
| Links | 目标页面去重后的正文资源事实 | 仅目标页面 |
| Backlinks | 指向目标 `ResourceIdentity` 的来源页面 | 全根 Markdown 链接事实 |
| Relations(outgoing) | 目标页面声明的正式关系 | 仅目标页面 |
| Relations(incoming) | 其他页面指向目标页面的正式关系 | 全部受管理页面 |
| Status | 具有指定页面状态的页面集合 | 全部受管理页面 |
| Recoverable | 当前仍可建立 Restore 的 retained Trash | Trash authority 与同一次 `as_of` |
| Check | 当前根的完整诊断报告 | 完整领域事实与 runtime 提供的可检查事实 |

Links 中 missing、unavailable、unchecked 或 invalid 是资源事实，不自动使读取失败。Backlinks 的目标是宽松资源身份，不要求目标当前存在；其他要求页面操作数的读取必须指向现有受管理页面。

### 10.2 candidate 与 registered 的完整性

| 读取 | candidate 遇到无关页面不可解析 | registered 遇到相同事实 |
| --- | --- | --- |
| Page | 拒绝不可靠计数，并返回有界阻碍事实 | 根损坏失败 |
| Links | 无关页面不影响；目标页可解析则 `complete=true` | 相同 |
| Backlinks | 返回已证明来源与 `complete=false` | 根损坏失败 |
| Relations(outgoing) | 无关页面不影响；目标页可解析则 `complete=true` | 相同 |
| Relations(incoming) | 返回已证明关系与 `complete=false` | 根损坏失败 |
| Status | 返回已证明页面与 `complete=false` | 根损坏失败 |
| Recoverable | 页面解析错误无关；Trash 证据损坏则整体失败 | 相同 |

`complete=false` 是成功但不完整的事实，不等于空集合，也不能用于证明不存在引用、关系、环或冲突。写命令不能把不完整读取当作授权。

### 10.3 排序、分页与 Check

领域排序为：页面路径使用规范路径序；正文资源使用目标首次出现序；Backlinks 和 incoming Relations 先按来源路径、再按声明位置；outgoing Relations 使用声明序；Recoverable 先按到期时间、再按 operation identity。

集合读取和 Check 必须有界分页。count-only 只隐藏当前 items，不改变 total、窗口或 complete 事实。Check 始终完整扫描后再过滤和分页；过滤不能改变完整扫描的 `ok`，空的当前页不能伪装成根已通过。

Check 不改变页面、根状态、结论所有权或 Trash 生命周期，不执行 Restore 或 recovery。runtime 可以按 foundation 规格使用同一次最终完整扫描维护非权威派生投影；该行为不能改变 Check 的领域报告。

未受管理 Markdown 在 candidate 中是检查 error，但不使根身份无效。页面纳管后，该路径不再产生 unmanaged error，而由缺少标题、机器区域、状态、父主题或导航等具体页面 error 表达欠账。adopt 只有在全部 error 清零后才可达。

## 11. 失败分类不变量

领域失败至少保持以下区别：

- request shape 或词法路径非法；
- 根未定位、根路径非法、根身份缺失、根格式无效、版本不支持；
- 页面不存在、source 不可移动、target 已存在、target 冲突或受保护；
- 所需闭包不完整、查询不完整、领域不变量违反；
- 外部变化导致 before facts 不再成立；
- runtime busy、recovery required、corrupt、platform unsupported 或 durability failure；
- Restore 的七个独立分区。

不存在、无效、未知、不完整、冲突、损坏和恢复未决不能用同一个空值、默认成功或“未找到”结果表达。失败发生后若自动 runtime 生命周期已经产生独立 durable 结果，其可观察语义由 foundation 规格定义；领域命令本身仍不得报告为成功。
