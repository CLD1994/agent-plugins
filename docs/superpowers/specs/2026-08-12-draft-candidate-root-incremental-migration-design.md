# Draft 候选根与增量迁移设计

## 状态

状态：已批准。

本版已经纳入对抗性评审修订。项目尚未正式发布，本次直接重定义当前
`format_version=1`，不提供旧 marker、旧命令形态或开发数据的兼容读取与升级路径。

## 0. 与既有规格的关系

本规格是
[`2026-08-10-draft-agent-cli-capabilities-design.md`](./2026-08-10-draft-agent-cli-capabilities-design.md)
的后续修订。发生冲突时，以本规格为准；未被下列事项改变的查询、页面、关系、回收、
结论和公开输出语义继续沿用前一份规格：

- 各命令的 `ROOT` 位置参数由第 7 节的统一根定位取代；
- 前一份规格第 3 节“不支持候选模式结构修改”的非目标被第 8、9 节取代；
- 第 8.1、8.3、8.4、8.5 节“全局扫描失败则整体失败”的规则，只在 `registered`
  根中继续成立；候选根按本规格第 11 节返回明确的不完整结果或拒绝不可靠的单页摘要；
- 第 12.2 节的正式 marker、provisional marker、候选结论限制，由本规格第 5、12 节
  整体取代；正式 marker 也增加 `root_state`，不是只删除 provisional marker；
- 第 15 节的 registered-only 生命周期推进资格，由本规格第 8.1 节的受管根资格取代；
  同时 `check` 改为严格只读，不再参加静默推进；
- 前一份规格中的 `check --candidate` 由本规格第 10 节取代。

## 1. 目标

当前迁移把全部工作交给 Agent：程序只在最后执行候选检查和 `adopt`。结果是复制
页面、建立目标目录、升格枢纽、维护机器块、移动页面、重算链接和根据诊断修复等
确定性工作也需要 Agent 逐页手工完成。

本次目标不是让程序理解任意来源布局，而是让 Agent 在一个固定目标根中渐进整理
内容时，能够复用普通 Draft CLI 完成已经确定的机械操作：

- Agent 读取并解释任意来源；
- Agent 决定哪些内容成为页面、页面语义和目标结构；
- CLI 对 Agent 已经确定的结构动作执行可恢复的机械变换；
- Agent 自主选择检查点，按诊断类别批量修复；
- 完整检查通过后，`adopt` 把同一个目标根推进为正式 Draft。

## 2. 行为契约

### 2.1 输入

- 一个由 Agent 选择并保持不变的来源；来源可以采用任意目录布局或文本格式；
- 一个由 Agent 显式指定的固定目标根；目标根通过 `draft init --candidate ROOT`
  初始化；
- Agent 对页面内容、状态、关系、路径和层级作出的逐步决定；
- Agent 在候选根中实际写入的 Markdown，以及随后调用的普通 Draft 命令。

程序不接受“来源格式”“来源页面映射”或“完整迁移计划”作为协议输入。

### 2.2 输出

- 同一个目标根内渐进形成的 Draft 页面网络；
- 每条 CLI 命令产生的确定性文件影响和必要诊断；
- Agent 显式调用 `check` 时产生的完整、可过滤、可分页检查结果；
- `adopt` 成功后，目标根 marker 从 `candidate` 原子推进为 `registered`。

### 2.3 不变量

- 来源始终保持不变；Draft CLI 不读取、扫描或修改来源；
- 迁移期间不存在第二个工作根、发布根或临时候选副本；
- Markdown 是标题、正文和正文链接的当前事实；不存在持久化完整迁移计划或正文副本；
- CLI 对路径、机器块、正式关系和父导航执行确定性机械修改；
- 候选根可以暂时不满足完整 Draft 协议；
- 候选命令只验证当前操作所需事实和它直接拥有的不变量，不隐式运行完整 `check`；
- 单条写命令在 §2.5 故障模型内要么完整提交，要么能够根据耐久日志安全继续或回滚；
- `adopt` 是唯一把 `candidate` 推进为 `registered` 的操作，并且必须先执行完整、
  无分页、无过滤的候选检查；
- 未知、无效、未完成、候选和正式状态不得折叠为同一个空值或默认行为。

### 2.4 失败语义

- 程序缺少当前操作所需事实时拒绝本次操作，并只报告直接阻碍；
- 与当前操作无关的候选诊断不阻止操作；
- 候选集合查询不能证明结果完整时按第 11 节返回 `complete=false`；`query page` 的全根
  计数不能证明时明确拒绝；两者都不得把未知解释为空集合或精确计数；
- 不能证明结构写操作已找到全部相关页面或引用时拒绝写入；
- 外部修改使事务无法安全继续或回滚时保留锁、日志和诊断现场；
- marker、日志、锁或版本非法时停止写入，不根据目录形状猜测状态。

### 2.5 故障模型与安全边界

本节是恢复与一致性保证的权威边界。其他章节出现"原子""并发""检测修改""完全提交"
等措辞时，都以本节的支持范围为准，不得解释出超过本节的保证。

| 类别 | 结论 |
|---|---|
| 支持 | 本地文件系统；一个受根锁协调的 CLI 写者；普通 I/O 失败；进程在任意已定义耐久步骤之后中断，可由公开 `recover`/trash 恢复入口安全收敛或保留现场 |
| 调用方前提 | 命令执行期间用户或 Agent 不直接修改目标根；程序发现不符合该前提的变化仍保守停止并保留现场 |
| 延期（不保证） | 非协作进程在相邻系统调用之间抢占名字；NFS 等非本地语义；机器掉电后的全目录链耐久；恶意篡改 `.draft/` 内部状态 |
| 不可放宽（高风险硬约束） | 来源零写入；路径不逃逸；未知内容不递归删除；既有节点不静默覆盖；锁与日志关联；歧义状态停机并保留现场 |

"检测命令读取之后、提交之前发生的修改"保证在合作模型内成立：它覆盖由本命令读取后
在同一 CLI 写者协调下可检测到的根内变化。命名动作本身依赖的系统调用间隙（例如两个
命名系统调用之间的改名窗口）属于延期项，被检测到时仍然保守停止，但不承诺在该窗口内
与任意非协作进程竞争名字。

## 3. 不在范围内

- `migrate`、`import`、`apply-plan` 或其他迁移专用命令；
- 任意来源格式适配器、来源目录扫描器或来源页面自动识别；
- 一次性提交完整迁移计划；
- `.draft/migration/`、待决项数据库、命令重放历史或页面身份映射；
- 从工作根重新发布或复制出另一个候选根；
- 每条候选写命令之后隐式执行完整检查；
- 对尚未发布的旧 marker、旧 CLI 或开发数据提供兼容、升级或双读分支。

## 4. Agent 与程序的责任边界

### 4.1 Agent 负责

- 阅读任意来源并判断哪些内容构成一个 Draft 页面；
- 选择目标页面、标题、正文、状态、父子关系和正式关系；
- 通过普通文件编辑或 `create` 建立候选页面；
- 在命令之间直接编辑页面标题、正文和正文链接；
- 必要时先把相关页面整理到当前命令能够解析的最低形状；
- 自主选择何时运行 `check`，并按同类诊断批量修复；
- 完整检查通过后显式执行 `adopt`；
- 在迁移完成前后通过来源摘要、版本控制状态或等价手段确认来源未变。

### 4.2 Draft CLI 负责

- 初始化候选或正式根；
- 根据 marker 识别根状态；
- 创建规范页面；
- 移动页面、升降级枢纽并维护父导航；
- 设置状态和增删正式关系；
- 重算当前结构操作影响范围内的机器链接和正文内部链接；
- 对多文件变换提供锁、日志、摘要核对和崩溃恢复；
- 提供完整检查、紧凑查询、回收、恢复和结论生成；
- 在完整候选检查通过后完成根状态转换。

### 4.3 手工编辑边界

skill 要求 Agent 通过 CLI 修改路径、机器块、正式关系和父导航；标题、普通正文和
正文链接可以直接编辑。

程序不保存每页历史摘要来证明某段合法文本由谁写入。因此，已经完成且结构合法的
手工机器块编辑在后续命令看来只是当前文件事实；程序只验证其语法与行为，不追踪
编辑来源。程序必须检测命令读取之后、提交之前发生的根内修改，不能把这种修改覆盖；
该检测保证受 §2.5 故障模型约束（命名动作的系统调用间隙属于延期项，不承诺与任意
非协作进程在窗口内竞争名字）。

## 5. 根状态与 marker

### 5.1 唯一 marker

`.draft/draft.json` 是根身份和稳定根状态的唯一权威来源。当前
`format_version=1` 只接受以下精确键集合：

```json
{
  "format_version": 1,
  "root_state": "candidate",
  "conclusions_state": "not_generated"
}
```

```json
{
  "format_version": 1,
  "root_state": "registered",
  "conclusions_state": "not_generated"
}
```

`root_state` 只允许：

- `candidate`：受管理、未登记、允许渐进整理；
- `registered`：已经正式登记，普通写操作采用严格健康门禁。

`conclusions_state` 继续只允许 `not_generated` 或 `generated`，并与 `root_state`
正交。候选根也可以生成结论页；`adopt` 保留当前结论所有权状态。

缺少 `root_state`、存在未知字段、字段类型错误或值非法时统一报告 `format.invalid`。
`format_version` 不是整数（布尔值也不算整数）时同样报告 `format.invalid`；它是整数但
不等于 `1` 时保留并报告 `format.unsupported`，不得把未知版本按当前格式解析。

不再使用 `draft.provisional.json`，也不定义 `root_state="adopting"`。旧候选加载器专用的
`candidate.already_registered` 和 `candidate.internal_state` 从稳定检查码中删除：根身份由
marker 直接表达，内部状态按第 5.4 节的受管根共同协议检查。

### 5.2 状态转换

```text
不存在或空目标 ── init --candidate ──> candidate
不存在或空目标 ── init             ──> registered
candidate       ── adopt 事务提交   ──> registered
```

未完成操作不是第三种根身份。它由 `.draft/operation.lock` 和
`.draft/operations/<operation-id>/journal.json` 表达，并叠加在当前稳定根状态上。

### 5.3 根状态决策表

根定位失败和已选根的 marker 缺失是不同事实，不能合并：

| 根定位与 marker 事实 | 根身份 | 普通业务写命令 | `check` | `adopt` | `recover` |
|---|---|---|---|---|---|
| 找不到所选目录，或 `pwd` 祖先链没有根 | 未定位 | `root.not_found` | `root.not_found` | `root.not_found` | `root.not_found` |
| 所选路径存在但不是合法目录 | 无法定位 | `root.invalid` | `root.invalid` | `root.invalid` | `root.invalid` |
| 已选目录中 marker 缺失 | 未受管理 | `format.missing` | `format.missing` | `format.missing` | `format.missing` |
| marker JSON、键、字段类型或字段值非法 | 无法安全解释 | `format.invalid` | `format.invalid` | `format.invalid` | `format.invalid` |
| `format_version` 是未知整数 | 无法安全解释 | `format.unsupported` | `format.unsupported` | `format.unsupported` | `format.unsupported` |
| 合法 `candidate`，无未完成操作 | 候选 | 采用候选操作级门禁 | 完整扫描 | 允许 | 无操作成功 |
| 合法 `registered`，无未完成操作 | 正式 | 采用严格健康门禁 | 完整扫描 | `adopt.already_registered` | 无操作成功 |
| 任一合法根，有未完成操作 | 恢复未决 | 拒绝并要求恢复 | 完整扫描并包含恢复事实 | 拒绝并要求恢复 | 按日志恢复 |

表中的“普通业务写命令”不包括 `recover`。`check` 在恢复未决时仍扫描能够安全读取的完整
根并报告页面、关系、链接和内部恢复事实；它不把“有锁”解释为只报告一条错误后停止扫描。

### 5.4 候选根内部状态规则

`candidate` 和 `registered` 使用同一套 `.draft/` 内部命名空间和完整性规则，只有
`draft.json.root_state` 与普通业务写门禁不同。允许的内部条目是：

- 必须存在的 `.draft/draft.json`；
- 仅在活动操作期间存在的 `.draft/operation.lock`；
- 按需存在的 `.draft/operations/`，其中保存活动操作的日志、暂存内容、恢复副本，或
  已完成、已回滚、保留回收等协议允许的终态审计记录；
- 按需存在的 `.draft/trash/outgoing/`，其中保存仍由 Draft 持有的权威删除包。

`draft.provisional.json` 和其他未登记的 `.draft/` 顶层条目不合法，统一报告新的稳定检查码
`internal.unexpected_entry`；已知目录内部的非法形状继续使用最具体的
`operation.journal_corrupt`、`operation.inconsistent_state`、`trash.package_corrupt`、
`path.symlink_escape` 等现有代码。候选检查必须与正式检查一样读取操作日志、锁、删除包和
`conclusions_state`，不能再通过“候选根拒绝一切内部状态”的旧分支短路。

## 6. 初始化

```text
draft init ROOT
draft init --candidate ROOT
```

- `init ROOT` 创建 `root_state=registered` 的全新 Draft；
- `init --candidate ROOT` 创建 `root_state=candidate` 的全新候选根；
- 两者只接受不存在或空目录；
- 两者都创建当前格式的最小骨架和精确 marker；
- 两者不根据目标内容自动切换模式；
- 两者不读取 `DRAFT_ROOT`，`ROOT` 始终显式给出。

初始化必须采用耐久发布方式，保证成功时得到完整目标，确定性失败时不留下一个看似
成功的半初始化根。具体采用同父目录暂存后发布，还是等价的日志化创建协议，属于实现
建议；硬性行为是 marker 不得把半成品伪装成已完成初始化。

## 7. 根定位与 CLI 语法

### 7.1 非初始化命令

除 `init` 外，需要草稿根的命令不再使用 `ROOT` 位置参数。它们统一接受可选
`--root ROOT`；`--version`、帮助等无根命令不触发根解析：

```text
draft check
draft check --root /work/drafts/topic
draft move pages/a.md pages/topic/a.md
draft move --root /work/drafts/topic pages/a.md pages/topic/a.md
```

这样 `SOURCE`、`TARGET`、`PAGE` 等页面操作数不会与可选的首个 `ROOT` 位置参数产生
歧义。

### 7.2 解析优先级

根解析只在一个共同边界完成，优先级固定为：

1. 命令行 `--root ROOT`；
2. 环境变量 `DRAFT_ROOT`；
3. 从物理 `pwd` 沿祖先目录发现根。

高优先级来源存在但无效时直接失败，不回退到低优先级来源。

`--root` 可以是绝对路径，也可以是相对于物理 `pwd` 的路径；解析后必须直接得到
草稿根，不对它执行祖先发现。

### 7.3 `DRAFT_ROOT`

- 必须是非空绝对路径；
- 必须直接指向根，不允许指向根内页面目录后再向上搜索；
- `candidate` 和 `registered` 都是合法定位结果；
- marker 非法时报告 marker 错误；
- 临时覆盖其他根时，`--root` 优先于 `DRAFT_ROOT`；
- `init` 明确忽略 `DRAFT_ROOT`。

### 7.4 `pwd` 祖先发现

省略 `--root` 且不存在 `DRAFT_ROOT` 时：

1. 从物理当前目录逐级向上检查 `.draft/draft.json`；
2. 只检查祖先链，不扫描兄弟目录、项目或文件系统；
3. marker 必须是非符号链接的普通文件；
4. 祖先链没有根时报告 `root.not_found`；
5. 祖先链出现多个合法 Draft 根时报告 `root.ambiguous`，要求显式 `--root`；
6. 最近的 `.draft/` 存在但 marker 缺失、是符号链接、是其他非普通文件/内容损坏，或版本
   不支持时，分别报告 `format.missing`、`path.symlink_escape`、`format.invalid` 或
   `format.unsupported`，不越过它选择外层根。

## 8. 候选根复用普通命令

候选根和正式根共享同一套普通命令，不增加 `migration move`、`migration relation`
等平行入口：

```text
create
move
promote
collapse
status set
relation add
relation remove
query
trash
restore
recover
generate-conclusions
check
```

命令的业务语义与根状态无关。例如 `move` 在两种根上都表示移动同一页面集合、维护
父导航并重算能够证明相关的链接。

| 命令类别 | `candidate` | `registered` |
|---|---|---|
| 结构与元数据写命令 | 操作级门禁 | 严格健康门禁 |
| `query` | 允许完整或显式不完整结果 | 应完整；不能完整时报告损坏 |
| `trash` / `restore` / `recover` | 允许并复用相同生命周期 | 允许 |
| `generate-conclusions` | 所需事实完整时允许 | 允许 |
| `check` | 候选完整检查 | 正式完整检查 |
| `adopt` | 完整检查通过后允许 | 报告已经登记 |

候选阶段执行 `trash` 仍表示最终删除授权，并使用相同的保留窗口、权威删除包、恢复
和系统回收站移交协议。候选被登记后，已有删除包和期限继续有效。

候选阶段执行 `generate-conclusions` 时，程序必须能够证明所有受管理页面已经发现、
状态可解析且导航顺序足以确定。缺少这些事实时只拒绝生成，不隐式输出完整检查。

### 8.1 受管根的生命周期前置推进

回收到期、系统回收站移交和清理是受管根协议，不是 registered 专属能力。命令在进入
自身业务逻辑前是否推进生命周期，按下表决定：

| 根与命令 | 是否推进 | 原因 |
|---|---|---|
| 合法 `candidate` 或 `registered`；`query`、业务写命令及其 `--dry-run`、`restore`、`recover`、`generate-conclusions` | 是，恰好一次 | 两种受管根共享回收期限和权威删除包协议 |
| `check` | 否 | 检查必须严格只读，并报告推进前的实际持久化事实 |
| `adopt` | 否 | 登记事务只验证和转换根身份，不夹带生命周期副作用 |
| `init`、帮助、版本 | 否 | 没有可推进的已选受管根 |
| 根未找到，或 marker 缺失、非法、不支持 | 否 | 不根据残留内部目录猜测所有权 |

推进资格只取决于精确有效的受管根 marker，不能仅凭 `.draft/`、操作目录或命令名称推测。
生命周期损坏时继续沿用前一份规格的硬失败语义。实现中的 registered-only 名称、注释和
资格判断必须泛化为“受管根”；不能让新 marker 因为“可读”而偶然放行，却仍把行为描述为
只适用于正式根。

## 9. 候选操作级门禁

候选根允许积累全局协议错误。单条命令只加载完成本操作所需的最小事实闭包，并保证
本操作直接拥有的不变量。

“最小”描述责任边界，不意味着必然只读取少量文件。例如 `move` 为证明已经重写所有
入站链接，可能必须扫描整个候选根；但它不应因此运行完整检查器或输出无关诊断。

| 根状态 | 恢复状态 | 本操作所需事实 | 本操作最终不变量 | 结果 |
|---|---|---|---|---|
| `candidate` | 无未完成操作 | 完整 | 可证明 | 执行事务 |
| `candidate` | 无未完成操作 | 完整 | 会被违反 | 拒绝并报告直接原因 |
| `candidate` | 无未完成操作 | 缺失或未知 | 无法证明 | 拒绝并列出阻碍页面 |
| `candidate` | 有未完成操作 | 任意 | 任意 | 拒绝，要求 `recover` |
| `registered` | 无未完成操作 | 全局检查通过 | 可证明 | 执行事务 |
| `registered` | 无未完成操作 | 全局存在错误 | 任意 | 拒绝并要求修复损坏 |

### 9.1 各写命令的事实闭包与自有不变量

下表是候选门禁的规范性逻辑闭包。实现可以为减少重复 I/O 读取更多文件，但不得把额外
读到的无关诊断变成执行门禁；`--dry-run` 与正式执行使用同一闭包和同一最终不变量。

| 命令 | 必须能够证明的事实闭包 | 本命令直接拥有的最终不变量 |
|---|---|---|
| `create` | 目标规范路径与不存在事实；由路径推导的父页存在且其机器块、标题和子主题导航可编辑；结论导航若存在则可识别 | 新页模板、父关系和父导航相互一致；不覆盖文件；结论链接仍唯一且位于末尾 |
| `move` | 被移动页或子树完整；来源与目标父页可解析；目标均不存在；全根中每个可能包含指向被移动集合的正文链接、正式关系或父导航的页面都可判定 | 路径、父关系、父导航和全部可受影响链接一致；没有目标冲突、路径逃逸或移动后空枢纽 |
| `promote` | 目标叶页、指定子页、其父页和新枢纽路径完整；全根中所有可能指向受影响页面身份的页面都可判定 | 叶页到枢纽的确定映射、子页归属、父导航、父关系和全部受影响链接一致；不遗漏或吸收未指定子页 |
| `collapse` | 枢纽、唯一可折叠内容及父页完整；不存在阻止降级的其他子页；全根中所有可能指向受影响页面身份的页面都可判定 | 枢纽到叶页的确定映射、父导航、父关系和全部受影响链接一致；不丢失子页或留下空枢纽 |
| `status set` | 目标页机器块和当前状态可解析；目标页自己的 `superseded_by` 声明可判定；若目标是结论页，marker 所有权状态可判定 | 写入合法状态与说明；目标页满足“`superseded` 当且仅当至少声明一个 `superseded_by`”；不自动猜测或改写关系 |
| `relation add/remove` | 来源、目标及待修改关系行可解析；双向关系的两端可解析；依赖或取代操作还必须完整发现相应关系图以判环；删除最后一条取代关系时可判定目标剩余声明与显式恢复状态 | 触及的正反关系配对、自引用规则、依赖/取代无环，以及取代目标的状态同步全部成立；幂等调用不制造重复关系 |
| `trash` | 目标页或子树、父页与导航完整；除允许过时的机器结论列表外，全根所有可能入站引用均可判定；操作日志、期限和删除包目标可安全创建 | 没有未授权入站引用；页面、父导航和必要结构修改原子完成；权威删除包、期限和日志互相锚定 |
| `restore` | 指定回收日志、期限、双向恢复关联和权威删除包完整；全部待恢复路径及受影响当前文件可按记录摘要分类；目标未被占用 | 只恢复由该删除包授权的内容；日志关联、页面、父导航和包所有权保持一致；失败不删除唯一权威包 |
| `recover` | 活动锁、对应日志、日志阶段、全部 before/after 摘要、恢复副本及关联包可判定；无锁残留按既有孤儿规则分类 | 只继续或回滚日志已经授权的变更；无法证明时保留现场；恢复完成后的根满足该日志记录的操作级验证契约，而不是当前全根必须无错 |
| `generate-conclusions` | marker 所有权；全部受管理页面的路径、标题和状态；完整且唯一的父导航顺序；当前结论产物和根导航可判定 | 结论页、根导航和 `conclusions_state` 作为一个事务更新；输出覆盖全部 confirmed 页面且顺序确定；无 confirmed 页时遵守既有 no-op 规则 |

“完整发现相应关系图”不等于所有页面必须全协议健康。例如依赖判环要求每个可能声明
依赖边的机器块可判定，但某个无关页面的普通正文断链不阻止关系操作。反过来，只要一个
不可解析页面可能隐藏相关边或入站链接，就不能把它当成“不存在”。

典型行为：

- A 页状态非法，不妨碍为无关的 B 页执行 `status set`；
- 某页机器块无法解析，不妨碍创建另一个根级页面；
- 移动 B 时，若程序无法确定某个不可解析页面是否引用 B，则拒绝移动；
- 添加依赖会形成环时立即拒绝，不能把本命令直接产生的错误留给后续 `check`；
- 升格枢纽时目标冲突或无法证明全部子页归属，则拒绝升格；
- 命令成功后允许候选根仍然存在与本命令无关的检查错误。

候选写命令提交前必须验证计划中全部输入摘要仍与读取时一致、本命令创建或改写的
页面仍可被对应解析器读取，并且本命令直接拥有的路径、链接、关系、导航或生命周期
不变量成立。它不调用完整 `check`，也不要求整个候选根全绿。

## 10. 显式完整检查

```text
draft check
draft check --root ROOT
```

删除 `--candidate`。`check` 从 `.draft/draft.json` 的 `root_state` 选择根前置条件和
检查语义。两种状态使用相同的页面、关系、链接和路径协议；候选检查不会因“仍在迁移”
而降低错误规则，只允许 Agent 决定何时运行它。

`check` 始终只读，不推进回收生命周期、不恢复操作、不清理日志，也不修改 marker。
普通候选命令不隐式调用完整 `check`。Agent 根据文件数量、结构变化范围和当前
不确定性选择检查点。`adopt` 不信任 Agent 是否刚刚检查过，仍执行完整候选检查。

一次 `check` 始终扫描完整根，但输出支持：

- 稳定排序；
- `--code CODE` 按稳定错误码过滤，可重复；
- `--path-prefix PREFIX` 按根内路径前缀过滤，可重复；
- `--limit`、`--offset` 分页；
- `--count-only` 只返回计数和分页事实。

过滤组合固定为：重复的 `--code` 取并集，重复的 `--path-prefix` 取并集，代码集合与路径
集合之间取交集。`--path-prefix` 必须是规范的根内 POSIX 相对路径，按路径段匹配：前缀
`pages/a` 匹配 `pages/a` 及其后代，不匹配 `pages/ab.md`；没有 `path` 的根级诊断不匹配
任何路径前缀。重复值不改变结果。

每个 `--code` 都必须是错误码权威来源中已登记的检查码；未知值是参数错误并在扫描根前
以退出码 2 拒绝，不能把拼写错误静默解释为“过滤后没有结果”。

`--limit` 与现有集合查询统一：默认 20，合法范围为 1 到 100；`--offset` 为非负整数。
检查器先形成唯一的规范诊断流，再过滤、分页，最后把当前页按严重级别放回 `errors`、
`warnings`、`infos`、`reviews` 分组。不能分别分页四个分组，否则同一 `offset` 会有四种
含义。`--count-only` 保留与普通请求相同的全部计数和分页事实，但四个条目数组为空。

分页只限制公开结果，不限制实际扫描。结构化结果同时包含：未过滤完整扫描的 `ok`、各
严重级别和错误码计数；过滤后的 `filtered_total`；当前 `offset`、`limit`、
`returned_count`、`has_more` 和 `next_offset`。退出码和 `ok` 只根据未过滤完整扫描决定，
不能因为过滤后或当前页没有错误而声称根已通过。

## 11. 查询完整性

所有成功查询都显式返回 `complete`；集合查询还返回 `blocking_paths`：

```json
{
  "complete": false,
  "items": [],
  "total": 0,
  "blocking_paths": ["pages/broken.md"],
  "blocking_total": 1,
  "blocking_has_more": false,
  "blocking_next_offset": null
}
```

- `complete=true` 表示程序能够证明结果集合完整；
- `complete=false` 是候选期合法的只读结果，不等于空集合；
- `complete=false` 仍是成功结果并使用退出码 0；
- `items` 中的每一项都是已经证明存在的准确事实，但 `complete=false` 时不能据此证明
  未返回的事实不存在；
- 写命令不得使用 `complete=false` 的查询结果证明没有入站引用、没有环或没有冲突；
- 正式根无法产生完整查询时，应报告正式根损坏。

各查询的完整性语义固定如下：

| 查询 | 所需扫描 | candidate 遇到无关页面不可解析 | registered 遇到同一事实 |
|---|---|---|---|
| `query page` | 目标页加全根计数 | 不返回含不可靠计数的摘要；以 `query.incomplete` 拒绝并给出有界阻碍事实 | 失败并报告根损坏 |
| `query links` | 仅目标页 | 无关页面不影响，成功为 `complete=true`；目标页本身不可解析则失败 | 相同 |
| `query backlinks` | 全根 | 返回已证明的来源和 `complete=false` | 失败并报告根损坏 |
| `query relations --direction outgoing` | 仅目标页 | 无关页面不影响，成功为 `complete=true`；目标页本身不可解析则失败 | 相同 |
| `query relations --direction incoming` | 全根 | 返回已证明的入站关系和 `complete=false` | 失败并报告根损坏 |
| `query status` | 全根 | 返回已证明的页面和 `complete=false` | 失败并报告根损坏 |
| `query recoverable` | 操作日志、期限和权威删除包 | 页面解析错误无关；相关内部证据损坏时整体失败，不返回部分项；成功为 `complete=true` | 相同 |

`query page` 成功时返回 `complete=true` 和 `blocking_paths=[]`；目标页能解析但全根计数不能
证明时，不得返回猜测值、下界或 `null` 与整数混合的摘要。`query.incomplete` 的阻碍详情
按第 14.1 节保持有界，Agent 需要完整修复清单时运行可过滤、可分页的 `check`。

允许部分结果的三个集合查询使用双集合分页。现有 `offset`、`limit` 分别对稳定排序后的
`items` 和去重后的 `blocking_paths` 应用同一个窗口；原有 `total`、`has_more`、
`next_offset` 只描述 `items`，并新增 `blocking_total`、`blocking_has_more`、
`blocking_next_offset` 描述阻碍路径。这样两个集合都能有界遍历，不会让一个巨大
`blocking_paths` 绕过分页限制。`complete=true` 时后三个阻碍字段分别为 `0`、`false`、
`null`。

## 12. `adopt` 状态转换

`adopt` 只接受 `candidate`：

1. 解析根并拒绝已有未完成操作；
2. 运行无过滤、无分页的完整候选检查；只有 `errors` 为空才继续；
3. 捕获第 12.1 节定义的观察清单；
4. 预分配 `kind="adopt"` 的普通操作日志。日志必须保存 marker 的 before/after 摘要与
   staged bytes、观察清单摘要以及“marker 是提交变更”的事实，然后取得排他锁；
5. 锁内排除本次 adopt 自己新建的日志和锁，重新核对 `root_state`、观察清单的完整路径
   集合、文件类型和摘要；任一差异都在 marker 替换前终止，并只清理本次准备状态；
6. 把日志推进到已经完成全部可失败业务验证、可以提交的阶段；
7. 作为最后一项受管根变更，把 `draft.json` 从 `candidate` 原子替换为 `registered`；
8. 只核对 marker 当前字节/摘要恰好等于日志的 after 状态，然后提交日志、清理恢复材料并
   释放锁。生效点之后不再运行会以“业务校验失败”为理由把 marker 回滚成 candidate 的
   全根检查。

`draft.json` 原子替换为 `registered` 是登记生效点。`adopt` 不修改来源，不重写
Markdown，也不清除已有结论所有权或回收生命周期状态。marker 的 after 语义只能把
`root_state` 从 `candidate` 改为 `registered`，必须保留同一个 `format_version` 和
`conclusions_state`。

步骤 4 在取得锁之前若遇到锁竞争，必须安全清理或终态化本次刚预分配且尚未授权任何
根变更的日志；不得遗留一个无锁中段 adopt 日志。步骤 5 发现差异时同样只放弃并清理本次
准备状态，不回写或覆盖发生变化的业务文件。

### 12.1 观察清单边界

观察清单记录所有会影响锁前完整检查或根内协议状态的根内事实：

- `index.md`、当前受管理的 `pages/**/*.md`，以及所有权为 `generated` 时的
  `conclusions.md`；
- `.draft/draft.json`；
- adopt 开始前已经存在的操作日志、日志恢复材料、权威删除包和其他合法内部协议文件；
- 上述目录的成员集合、每个观察路径的普通文件/目录/符号链接类型，以及普通文件摘要；
- 完整检查实际观察到的根内本地资源目标的存在性、类型和内容摘要。

根外资源不被纳入持久摘要，避免把来源或任意外部目录变成 Draft 的写入/锁定对象；但锁内
复核仍重新读取完整检查所需的根外存在性事实。步骤 5 的集合比较只排除本次 adopt 自己的
操作目录和 `operation.lock`，不能笼统排除整个 `.draft/operations/` 或
`.draft/trash/`，否则并发生命周期变化会被漏掉。

### 12.2 统一恢复中的 adopt 提交点

`recover` 仍是唯一公开恢复入口，也继续复用普通锁、操作日志、staged bytes 和恢复副本；
但统一恢复器必须识别 `journal.kind="adopt"` 及其 marker 提交点。这里允许统一状态机按操作
kind 分派收敛规则；“不建立专用恢复状态机”只表示不增加第二套 marker、日志目录或公开
命令，不能解释为忽略 adopt 的单向根状态转换。

| 锁与日志事实 | marker 当前摘要 | `recover` 结果 |
|---|---|---|
| 匹配锁和合法 adopt 中段日志，观察清单仍匹配 | before（`candidate`） | staged after 完整时优先前向完成并在替换瞬间登记；前向材料不完整但 before 可证明时放弃本次准备并保持候选 |
| 匹配锁和合法 adopt 中段日志，观察清单已变化但 marker 未变 | before（`candidate`） | 不覆盖外部变化；把本次 adopt 终态化为已回滚并清理自己的材料、释放锁，报告外部修改 |
| 匹配锁和合法 adopt 中段或已提交待清理日志 | after（`registered`） | 只完成日志提交、恢复材料清理和解锁；不得回滚根身份；其他业务文件后来变化也不反转已经生效的登记 |
| 匹配锁和合法 adopt 终态日志 | `completed` 对应 after；`rolled_back` 对应 before | 验证后清理和解锁；反向组合报告 `operation.inconsistent_state` |
| marker 既不是日志 before 也不是 after | 任意 | 保留锁和现场，报告 `operation.external_change` |
| 中段 adopt 日志没有匹配锁，或锁指向别的操作 | 任一 | 保留现场，报告 `operation.inconsistent_state` |
| 日志、锁或内部路径形状损坏 | 任一 | 保留现场，报告最具体的损坏代码 |

marker 已是 after 状态时，日志阶段落后只表示“登记已生效、审计提交待完成”，不能因阶段
仍是 `applying` 或 `validating` 而退回 candidate。marker 仍是 before 状态时，恢复可以按
普通操作规则安全前向完成；这会在恢复期间产生新的登记生效点，并不表示崩溃前已经登记。
marker 已是 after 时，staged bytes 或 before 恢复副本已经不再是证明提交所需的权威材料；
它们缺失不能阻止终态清理，但符号链接、日志无法解析或 after 摘要无法从日志证明仍须停止。

## 13. 端到端数据流

```text
任意只读来源
    │ Agent 阅读和作出语义决定
    ▼
固定 candidate 根（同一个目标目录）
    │ Agent 批量写标题/正文，普通 CLI 执行机械变换
    │ 候选允许暂时存在全局诊断
    ▼
Agent 选择检查点并运行 check
    │ 按错误码或路径分组，批量修复
    ▼
完整候选检查通过
    │ adopt：日志、锁、摘要复核、marker 原子替换
    ▼
同一个目录成为 registered 根
    │ 普通 check 验证最终可观察状态
    ▼
迁移完成；来源摘要仍与开始时一致
```

这个流程没有隐式来源识别，没有候选发布副本，也没有“程序先猜布局、Agent 再纠错”
的控制流。

## 14. 错误、重试与恢复闭合

### 14.1 新增、统一登记和退役的稳定代码

本规格新增的代码，以及被统一根选择协议纳入同一分支集合的既有 `root.invalid`，必须登记
在生产代码的单一错误码权威来源中。CLI、渲染器、测试和文档从该来源引用，不在各层维护
平行清单：

| 代码 | `path` | `details` 协议 |
|---|---|---|
| `root.invalid` | `--root` 规范化后的目标、绝对 `DRAFT_ROOT` 目标，或不合法的非绝对 `DRAFT_ROOT` 原始词法路径 | 空 |
| `root.not_found` | `--root`/`DRAFT_ROOT` 解析后的目标，或祖先发现起点的物理 `pwd` | 恰好一个来源代码：`cli`、`env` 或 `pwd` |
| `root.ambiguous` | 祖先发现起点的物理 `pwd` | 两个或更多合法根的规范绝对路径，按离 `pwd` 由近到远排列 |
| `internal.unexpected_entry` | 非法条目的根内 POSIX 相对路径 | 空 |
| `query.incomplete` | `query page` 请求的规范页面路径 | 第一项是十进制阻碍路径总数，后续最多 20 项是按路径排序的根内 POSIX 路径 |

`root.invalid` 表示调用方已经提供了选择值，但它在根目录边界上不合法；例如
`DRAFT_ROOT` 不是绝对路径，或规范化后的目标存在但不是目录。`root.not_found` 只表示目标
目录不存在或祖先发现没有得到根；已选目录存在但 marker 缺失必须使用 `format.missing`。
`query.incomplete` 的详情若总数大于实际携带路径数，即表示输出已截断；完整清单通过
`check` 获取。

旧 `candidate.already_registered`、`candidate.internal_state` 退役且不保留别名。正式根再次
执行 `adopt` 使用既有 `adopt.already_registered`。

- 参数或根定位错误发生在读取页面前，不创建内部状态；
- 候选操作缺少事实时不创建操作日志、不产生部分写入；
- 写计划生成后发生外部修改时，在正式替换前通过摘要复核拒绝；
- 提交中断时由同根日志恢复，不能依赖 Git、平台数据目录或来源副本；
- `recover` 能证明安全时继续，不能继续但能证明未被外部修改时回滚；
- 两者都不能证明时停止并保留现场；
- `check` 只报告事实，不自动恢复；
- `adopt` 失败后若事务已安全回滚，根仍为 `candidate`，Agent 可以继续修复后重试；
- `adopt` 生效点之后的清理失败通过恢复收敛到 `registered`，不能回报为候选；
- 已成功执行的候选命令不因后续 `check` 发现其他问题而回滚。

## 15. 安全与路径边界

- `--root` 和 `DRAFT_ROOT` 必须指向准确根，不能让命令搜索任意目录；
- `pwd` 发现只沿祖先链进行；
- marker、根路径、页面路径和内部状态继续执行现有符号链接与越界检查；
- 候选放宽的是全局健康前置条件，不放宽路径逃逸、锁、原子写、摘要复核或恢复证据；
- Agent 对来源拥有读取权限不表示 Draft CLI 获得来源写权限；CLI 接口中不存在来源参数；
- `trash` 的删除授权和恢复期限不因 candidate 状态而弱化。

## 16. 测试设计

### 16.1 单元测试

单元测试保持同进程、无真实文件系统、无子进程、无真实环境变量修改，通过纯函数和
受真实接口约束的替身验证：

- marker 对两个 `root_state`、两个 `conclusions_state` 和精确键集合的解析矩阵；
- candidate 与 registered 共享内部条目分类，旧 `candidate.*` 检查码不可达；
- 根选择优先级，以及高优先级非法时不回退；
- `--root`、`DRAFT_ROOT` 和 `pwd` 发现事实的分类；
- `root.invalid`、`root.not_found`、`root.ambiguous`、`internal.unexpected_entry` 和
  `query.incomplete` 的精确 `path`/`details` 协议；
- 生命周期资格对两种受管根成立，同时排除 `check` 和 `adopt`；
- 候选操作门禁决策表；
- 无关诊断不阻止操作，相关事实未知时拒绝；
- 每类写命令声明并验证自己拥有的不变量；
- 候选写命令不调用完整检查协作者；
- 七种查询分支的 `complete`/拒绝分类，以及部分 `items` 与 `blocking_paths` 的独立分页
  事实；
- 检查过滤的并集/交集和路径段语义、稳定排序、统一分页、计数，以及 `ok`/退出码不受
  当前页误导；
- `adopt` 只在完整候选检查通过并且摘要复核一致时生成 marker 状态转换计划；
- marker 状态、日志阶段和文件摘要组合的恢复决策表，特别是 after marker 不可回滚和
  after 状态不依赖已无用途的 staged/before 副本。

交互断言只用于“不得隐式运行完整检查”“必须取得锁”“必须复核摘要”等协议和安全
契约，不复制内部辅助函数调用图。

### 16.2 集成测试

真实文件系统测试单独覆盖：

- `init --candidate` 和普通 `init` 的 marker 与目录结果；
- 根内子目录的 `pwd` 发现、祖先歧义、损坏 marker、显式 `--root` 覆盖；
- 从任意 `pwd` 使用绝对 `DRAFT_ROOT` 操作目标根；
- Agent 连续创建、直接编辑和调用多条普通命令，中间不运行 `check`；
- 一次检查汇总多个错误，按同类批量修复后通过；
- 候选存在无关错误时局部命令成功，缺少相关事实时同一命令拒绝且文件不变；
- 候选 `trash`、`restore`、`generate-conclusions` 和普通结构操作；
- 候选普通操作产生合法日志后，`check` 不报告旧 `candidate.internal_state`，并能报告
  与正式根相同的操作、trash 和结论事实；
- 候选业务命令会推进回收生命周期，`check` 与 `adopt` 保持只读/无夹带推进；
- `query page` 在全局计数不可靠时拒绝，backlinks/incoming relations/status 返回可分页的
  明确不完整结果，recoverable 不受页面解析错误影响；
- `adopt` 把同一目录从 `candidate` 推进为 `registered`；
- 在 marker 替换前后注入故障，覆盖锁丢失、观察清单变化和恢复材料缺失；after marker
  始终收敛到 registered，before marker 只在重新证明安全后前向完成；
- 外部并发修改、锁竞争、日志损坏和恢复副本损坏；
- 来源目录在完整端到端迁移试验前后字节摘要一致。

### 16.3 完整质量门禁

实施完成前运行与变更范围相称的核心单元测试、核心集成测试、适配层测试、严格类型
检查和格式检查。测试全绿之外，还必须逐项核对本规格的命令表面、根状态、检查点
控制权和来源零写入边界。

## 17. 验收流程

一个真实的任意旧布局迁移必须能够按以下形式完成：

```text
draft init --candidate TARGET
export DRAFT_ROOT=/absolute/path/to/TARGET

Agent 读取固定来源并批量建立候选内容
Agent 连续调用普通结构命令
draft check --count-only
draft check --code parent.navigation_missing --limit 100 --offset 0
Agent 按诊断类别批量修复
draft check
draft adopt
draft check
```

验收必须证明：

- 没有来源格式适配器或迁移专用 CLI；
- 中间所有写入发生在同一个目标根；
- Agent 能在多条命令之间直接编辑正文；
- 候选命令没有隐式完整检查；
- 检查输出可以有界消费并支持批量修复；
- 机械命令在候选全局不健康时仍能安全处理局部确定事实；
- `adopt` 之前必须全绿，之后根状态和普通检查均为正式状态；
- 来源完全未变。

## 18. 预计实现影响（非硬性文件清单）

以下是根据当前代码结构作出的实现预估，不限制实施者在保持行为契约的前提下调整
模块边界：

- marker 模型和解析器增加受约束的根状态；
- 删除候选内部状态拒绝分支和两个旧 `candidate.*` 检查码，候选与正式根共享内部检查；
- 生命周期入口重构 `init` 和 `adopt`，删除 provisional marker 协议；
- 生命周期前置资格从 registered 泛化为受管根，同时让 `check` 保持严格只读；
- CLI 统一根解析、`--root`、`DRAFT_ROOT` 和 `pwd` 发现；
- `check` 删除 `--candidate`，增加过滤、分页和计数事实；
- 图加载与命令规划分离“完整健康加载”和“操作所需事实加载”；
- 查询结果增加按查询类型区分的完整性事实和阻碍路径分页；
- 操作日志允许 candidate 根并承载 `adopt` 状态转换；统一恢复器增加 marker 提交点的
  单向收敛规则；
- 新增/退役错误码通过生产代码单一权威来源同步；
- skill、格式参考、迁移参考和正式设计同步新工作流；
- 现有测试夹具直接更新到新的开发期 `format_version=1` marker 形状。
