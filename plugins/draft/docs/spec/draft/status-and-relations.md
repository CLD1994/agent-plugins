# 状态与关系

## 页面状态与关系

`D-STATUS` 定义页面状态和正式关系的领域不变量。机器区的具体 parser、字段拼写和
遗留 `产出` 形状的处理不由本合同擅自补定。

### 输入与输出

- 输入是受管理页面、其状态事实、正式关系事实、关系端点和根状态。
- 输出是经认证的状态/关系 after-state，或可诊断拒绝。状态与关系编辑的完整请求计划
  和 DryRun/Execute 边界见 [页面编辑](page-edits.md#创建状态与关系编辑)。
- `confirmed` 是结论成员资格的唯一来源；`challenged_by` 不会自动改变该页面的状态或
  有效性。

### 状态、关系与不变量

| 类别 | 合法值 / 规则 |
| --- | --- |
| 页面状态 | `pending`、`proposed`、`confirmed`、`superseded`、`closed`。每个受管理页面有且仅有一个状态。 |
| 关系事实 | `parent`、`depends_on`、`supersedes`、`superseded_by`、`challenges`、`challenged_by`；其中 `parent` 是结构 mapping 的事实，不是 relation 命令的可变参数。 |
| relation 命令参数 | 只允许主动方向 `depends_on`、`supersedes`、`challenges`；`parent`、`superseded_by` 和 `challenged_by` 由各自 owner 或反向计划维护，不能作为 `relation.add/remove` 的 `CODE`。 |
| 显式反向对 | `supersedes` 与 `superseded_by`、`challenges` 与 `challenged_by` 必须同时存在且端点相反。 |
| 图约束 | 不允许重复、自引用或关系环；`parent` 还必须服从页面/物理父/导航的一致性。 |
| 结论制品 | `conclusions.md` 只允许固定 `parent`，不参与可变正式关系。 |

`confirmed + challenged_by` 是合法组合：页面仍属于当前有效结论，同时在结论快照中暴露
未解决质疑。链接观察不产生正式关系，也不替代上述成对声明。

`parent` 只能由 create/structure/move/promote/collapse 的完整 mapping 同时维护路径、物理
父和导航；relation 命令不得单独创建、更新或删除它。`superseded_by` 与 `challenged_by` 仍
是关系事实和查询所需的稳定反向代码，但调用者只能通过主动方向请求它们的成对计划。

### 决策表、转换与提交

| 请求 / 当前事实 | 认证结果 | 允许的领域结果 | 提交与副作用 |
| --- | --- | --- | --- |
| 设置到五个合法状态之一 | 页面、根和目标状态可认证，且最终关系不变量成立 | 形成新的单一页面状态 | 由编辑合同与其必要事实同一提交；不刷新结论快照。 |
| 设置未知状态或状态事实缺失 | 不成立 | 拒绝 | 零写。 |
| 添加 `depends_on` | 两端存在、不同、无重复且无依赖环；关系参数是主动方向 | 添加 source 的依赖关系 | 只写该主动行；不写反向行、状态或结论快照。 |
| 添加 `supersedes` | 两端存在、不同、无取代环；主动行、反向行可认证为缺失、完整或单边 | 添加/补齐 `supersedes` 与 `superseded_by` 成对关系，并将 target 状态设为 `superseded` | 两端关系和 target 状态同一提交；不刷新结论快照。 |
| 添加 `challenges` | 两端存在、不同、无重复/自引用/环；反向行可认证并可纳入同一计划 | 添加成对关系 | 两端机器区同一提交；不改变任一状态或结论快照。 |
| 添加 `parent` 或任一反向代码 | relation 参数不属于主动方向集合 | 拒绝 | 写入前失败，零写；parent 只能由结构 mapping owner 处理。 |
| 添加可变关系且任一端为 `conclusions.md` | 不成立 | 拒绝 | 零写。 |
| 删除 `depends_on` | 被点名的 source 行可认证且存在 | 仅删除该主动行 | 同一提交；不重写状态或结论快照。 |
| 删除不存在的 `depends_on` 或两边均不存在的成对关系 | 两端页面和相关机器区可认证，且被点名关系确实不存在 | 幂等空操作 | 零写；不能把页面/关系不可认证当作不存在。 |
| 删除 `challenges` | 主动行、反向行可认证为完整或单边残留 | 删除被点名的主动行和/或其已存在的反向残留 | 同一提交；不改变任一状态或结论快照。 |
| 删除非最后一条 `supersedes` | 主动行、反向行可认证为完整或单边残留，target 仍有其他取代者 | 删除被点名的主动行和/或其已存在的反向残留，target 保持 `superseded` | 同一提交；不刷新结论快照。 |
| 删除最后一条 `supersedes` | 主动行、反向行可认证为完整或单边残留，调用方提供 `pending`、`proposed`、`confirmed` 或 `closed` 之一 | 删除被点名的主动行和/或其已存在的反向残留，并把 target 状态设为提供的合法状态 | 关系和状态同一提交；不刷新结论快照。 |
| 删除最后一条 `supersedes` 且缺少或非法目标状态 | 不能认证唯一 after-state | 拒绝 | 写入前失败，零写；不得猜测恢复为 `proposed` 或其他状态。 |
| 删除请求的页面、机器区或关系事实无法认证 | 不成立 | 拒绝 | 零写；不得把未知事实解释为不存在。 |

添加 `supersedes` 或 `challenges` 时，before-state 缺少主动行、反向行或两者均缺失，都由
同一完整计划补入；已存在的行若重复、指向错误端点或无法解析，则在任何写入前失败。删除
这两类关系时，单边残留由完整计划清理，两边都缺失才是幂等零写。`depends_on` 没有显式
反向行。完整请求、DryRun/Execute 以及恢复覆盖层的共同门禁见
[页面编辑](page-edits.md#创建状态与关系编辑)。

`relation.remove` 可以清理涉及 `conclusions.md` 的已认证非法遗留关系，但只能清理被精确
定位的行。candidate/registered、遗留关系组合与生成器 survivor gate 的完整分区尚无一致
签署事实：该分区为 `unresolved`。在该分区中不得自动清理、覆盖生成制品或把拒绝解释为
关系已不存在。

### 失败、零写与恢复

- 重复、缺端点、冲突/不可解析的反向行、自引用、环或根处于 recovery pending 时，编辑前
  失败并保持零写；添加成对关系时，认证的缺失行可作为同一计划的待写 after-state，删除
  成对关系时，认证的单边残留可作为同一计划的待删 after-state。
- relation 参数不是 `depends_on`、`supersedes` 或 `challenges`，包括 `parent`、
  `superseded_by` 和 `challenged_by` 时，编辑前失败并保持零写。
- 任何提交后耐久不确定都由 runtime 标记 recovery pending；本页不把它降级成关系成功或
  自动回滚成功。只有显式 recovery 可以收敛现场。
- 重试重新读取两端与反向关系。未认证的缺失不构成幂等成功。

### 验证

- 为每个状态值、`confirmed + challenged_by`、每类反向对、重复、自引用和环分别做
  反例验证；分别覆盖三个主动方向和三个禁止的 relation 参数。
- 验证 `supersedes` 添加的 target `superseded` after-state、删除非最后一条时的保持，
  以及删除最后一条时合法目标状态、缺失/非法目标状态和缺少反向行的零写分区。
- 验证 `conclusions.md` 不能作为 `relation.add` 的 source 或 target，而精确删除已认证
  遗留行不影响无关关系。
- 验证状态/关系改动不改变结论快照成员、排序、标签或质疑来源。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md#11-修改正式关系`
