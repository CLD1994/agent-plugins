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
| 关系 | `parent`、`depends_on`、`supersedes`、`superseded_by`、`challenges`、`challenged_by`。 |
| 显式反向对 | `supersedes` 与 `superseded_by`、`challenges` 与 `challenged_by` 必须同时存在且端点相反。 |
| 图约束 | 不允许重复、自引用或关系环；`parent` 还必须服从页面/物理父/导航的一致性。 |
| 结论制品 | `conclusions.md` 只允许固定 `parent`，不参与可变正式关系。 |

`confirmed + challenged_by` 是合法组合：页面仍属于当前有效结论，同时在结论快照中暴露
未解决质疑。链接观察不产生正式关系，也不替代上述成对声明。

### 决策表、转换与提交

| 请求 / 当前事实 | 认证结果 | 允许的领域结果 | 提交与副作用 |
| --- | --- | --- | --- |
| 设置到五个合法状态之一 | 页面、根和目标状态可认证，且最终关系不变量成立 | 形成新的单一页面状态 | 由编辑合同与其必要事实同一提交；不刷新结论快照。 |
| 设置未知状态或状态事实缺失 | 不成立 | 拒绝 | 零写。 |
| 添加 `supersedes` / `challenges` | 两端存在、不同、无环、反向行可同时计划 | 添加成对关系 | 两端机器区同一提交；不刷新结论快照。 |
| 添加 `parent` / `depends_on` | 页面、路径和相应局部不变量成立 | 添加或更新该关系 | 只能与其 required 页面/导航事实同一提交。 |
| 添加可变关系且任一端为 `conclusions.md` | 不成立 | 拒绝 | 零写。 |
| 精确删除已认证的关系 | 目标行与反向行（若适用）均可定位 | 仅删除被点名的关系事实 | 同一提交；不得顺带重写其他关系、状态或快照。 |
| 删除请求无法认证为既存关系 | 不成立 | 拒绝 | 零写。 |

`relation.remove` 可以清理涉及 `conclusions.md` 的已认证非法遗留关系，但只能清理被精确
定位的行。candidate/registered、遗留关系组合与生成器 survivor gate 的完整分区尚无一致
签署事实：该分区为 `unresolved`。在该分区中不得自动清理、覆盖生成制品或把拒绝解释为
关系已不存在。

### 失败、零写与恢复

- 重复、缺端点、缺反向行、自引用、环或根处于 recovery pending 时，编辑前失败并保持
  零写。
- 任何提交后耐久不确定都由 runtime 标记 recovery pending；本页不把它降级成关系成功或
  自动回滚成功。只有显式 recovery 可以收敛现场。
- 重试重新读取两端与反向关系。未认证的缺失不构成幂等成功。

### 验证

- 为每个状态值、`confirmed + challenged_by`、每类反向对、重复、自引用和环分别做
  反例验证。
- 验证 `conclusions.md` 不能作为 `relation.add` 的 source 或 target，而精确删除已认证
  遗留行不影响无关关系。
- 验证状态/关系改动不改变结论快照成员、排序、标签或质疑来源。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
