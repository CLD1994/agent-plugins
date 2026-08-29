# 结论快照

## 结论快照行为合同

`D-CONCLUSIONS` 定义 `conclusions.md` 的语义成员资格、显式生成与结构 mapping 的窄
维护边界。它不把快照变成页面状态或正式关系的权威来源。

### 输入、输出与不变量

- 输入是受管理页面的 `confirmed` 状态、可认证的 `challenged_by` 关系、现有
  `conclusions.md` 制品、事件类型，以及结构操作已经认证的 before-to-after mapping。
- 输出是允许暂时 stale 的语义快照、对已有链接目标的窄改写，或可诊断的零写失败。
- `confirmed` 是唯一成员来源。带 `challenged_by` 的 confirmed 页面仍属于“已确认结论”，
  并且同时出现在“受质疑结论”投影；质疑不会自动取消成员资格。
- 文件固定保留 `## 已确认结论` 与 `## 受质疑结论` 两个 section，零条目时也保留二者。
  受质疑顶层顺序跟随确认集合顺序，每个顶层的质疑来源按规范路径排序。
- 生成制品只允许固定 `parent`，不能参与可变正式关系；链接观察不产生关系，也不阻止
  Trash。

### 事件、状态与提交决策表

| 事件 | 所需认证事实 | 允许的结果与副作用 |
| --- | --- | --- |
| 显式 `generate-conclusions` | 所有成员状态、成对关系、端点存在、被质疑目标已确认且无重复 | 整体重建两个 section 的语义快照；这是唯一改变成员、分组、顺序、标签和质疑来源集合的事件。 |
| `move`、`promote`、`collapse` | 现有制品可安全定位既有顶层/缩进链接，且 old-to-new mapping 可证明 | 在同一结构提交中只改写已有条目链接目标；不增删、不重排、不改标签、质疑来源或语义内容。 |
| `move`、`promote`、`collapse` | mapping 不可证明或生成制品不能安全解析 | 整个结构写在任何写前失败；不得按文本相似度猜链接。 |
| `create`、`status set`、`relation add/remove`、标题或正文变化 | 任意 | 不修改快照；快照可因此 stale。 |
| Trash / Restore | 任意 | 不阻止相应操作，且不删除、重建或改写快照。 |
| 显式生成的关系事实不完整、重复、端点缺失或状态不满足 | 不成立 | 拒绝生成，零写；遗留关系只能由精确 `relation.remove` 清理。 |

结构命令的窄链接改写消费其已认证 mapping，不另建 operation 或独立提交。显式生成是完整
语义重建，当前输出只采用固定双 section，不提供其他格式的兼容路径。

### 未决 parser 边界

以下细节尚无唯一签署依据，必须保持 `unresolved`：

| 未决项 | 缺失的唯一依据 | 当前强制边界 |
| --- | --- | --- |
| 生成制品的 source span、challenge parser，以及零/一/多个质疑的精确文本形状 | 已签语义确定双 section 与排序，但没有统一的 parser/渲染合同 | 结构命令只有在能安全认证已有条目链接时才可窄改写；否则零写。 |
| `产出` 形状在机器区中的精确 parser 结果 | 当前语义没有 `output/产出` 成员关系，但未裁决该遗留形状应被拒绝、忽略还是如何诊断 | 不把 `产出` 作为允许机器标签、成员条件或迁移格式；需要该判断时停止并报告 `unresolved`。 |
| 公开生成结果、错误字段和顺序 | 没有已批准的公共 ABI | 不从页面文字或 CLI 现状发明 `Outcome` 字段。 |

### 失败、零写、恢复与验证

- 生成前任一关系、端点、状态或重复事实不可认证时零写。快照 stale 本身不是结构阻塞，
  但对结构命令而言不可安全解析的生成制品是阻塞。
- 领域提交后耐久结果不确定时由 runtime 保留 recovery 现场；普通结构或生成重试不能
  自行覆盖制品。显式 recovery 收敛后必须重新认证成员事实和 mapping。
- 验证 confirmed-only 成员资格、`confirmed + challenged_by` 双投影、双空 section、排序、
  stale 事件、Trash/Restore 非修改，以及 mapping 仅改旧链接目标的反例。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
