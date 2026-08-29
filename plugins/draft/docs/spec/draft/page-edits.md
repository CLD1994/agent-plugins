# 页面编辑

## 创建、状态与关系编辑

`D-CREATE-EDIT` 覆盖 `create`、`status set`、`relation add` 和 `relation.remove` 的
请求认证、计划和原子领域提交。它不定义 runtime 的 operation record、字段 ABI 或
耐久实现。

### 输入、输出与共同不变量

- 输入由请求种类、根状态、DryRun/Execute 模式、目标页面/端点、当前页面字节与机器区、
  页面/导航/关系事实和 recovery overlay 组成。
- 输出是完整计划（DryRun）或该计划的领域 after-state（Execute）；公开 `Outcome` 的字段、
  顺序及 operation identity 由 runtime 统一拥有，尚未获批的 ABI 为 `unresolved`。
- DryRun 与 Execute 使用完全相同的前置事实和计划边界。DryRun 不写页面、机器区、导航、
  关系、marker 或 runtime 状态。
- Execute 只有在全部后置不变量可同时证明时才提交；不能先改页面再补关系、导航或反向行。

| 请求 | 必须进入完整计划的领域事实 | 成功后的不变量 |
| --- | --- | --- |
| `create` | 新路径角色、目标不存在、物理 parent、必要导航、初始机器区事实 | 新页符合页面角色；parent/导航一致；不产生 `pages/index.md`。 |
| `status set` | 目标页、当前/目标状态、受影响关系和结论快照事件边界 | 状态是五个合法值之一；关系不变量仍成立；不改快照。 |
| `relation add` | 两端、关系类别、必要反向行、无重复/自引用/环证明 | 成对关系和状态/页面不变量同时成立；结论制品不成为可变端点。 |
| `relation.remove` | 被点名的既存行及适用反向行 | 仅精确删除已认证关系；不做推测性清理。 |

### 请求、根状态、模式与失败决策表

| 请求 | 根 / recovery | 模式 | 前置事实与计划 | 结果 |
| --- | --- | --- | --- | --- |
| 任一 | marker 不可认证或 recovery pending | DryRun / Execute | 不能建立可信计划 | 拒绝；零写。 |
| `create` | `candidate`、无 pending | DryRun | 新路径及 create 专属事实完整 | 返回完整计划；零写。 |
| `create` | `candidate`、无 pending | Execute | 与 DryRun 相同的计划仍可认证 | 原子提交新页及必要事实。 |
| `create` | `registered`、无 pending | DryRun / Execute | create 专属事实完整，且 registered 业务写所需全局健康可认证 | DryRun 返回计划；Execute 原子提交。 |
| `status set` | `candidate`、无 pending | DryRun / Execute | 目标状态与局部最终不变量可认证 | 按模式返回计划或原子提交。 |
| `status set` | `registered`、无 pending | DryRun / Execute | 上述事实加 registered 全局健康可认证 | 按模式返回计划或原子提交。 |
| `relation add/remove` | `candidate`、无 pending | DryRun / Execute | 端点、关系和精确清理范围可认证 | 按模式返回计划或原子提交。 |
| `relation add/remove` | `registered`、无 pending | DryRun / Execute | 上述事实加 registered 全局健康可认证 | 按模式返回计划或原子提交。 |
| 任一 | 任一 | DryRun / Execute | 参数、span、链接、目标、状态、关系、导航或完整计划任一项不可认证 | 拒绝；不以缺失事实推断幂等成功，零写。 |

“registered 全局健康”的精确 Check 投影、每类入口的机器区字段集合，以及 candidate
遗留关系的 survivor 例外尚未由签署合同唯一给出，均为 `unresolved`。这不授权实现跳过
前置认证；需要这些细节的请求必须停止并保留诊断。

### 提交、副作用、失败与恢复

Execute 的提交点是完整计划中的所有领域事实一起可见的时刻。成功可以改写页面、机器区、
关系和必要导航；`create`、`status set`、`relation add/remove` 不修改 `conclusions.md`
快照。任何校验失败在提交前零写。

若领域提交后耐久状态不能认证，普通调用不得报告成功或继续后续写入；runtime 保留现场并
加上 recovery overlay。重试须重新认证请求意图和 authority 快照；相同请求是否复用
operation identity、不同快照如何关联旧操作，属于 runtime 的 `unresolved` ABI，不能由
本页自行命名字段。

### 验证

- 对四类请求分别覆盖 candidate、registered、pending、DryRun、Execute、合法和各类
  不可认证前置事实。
- 对每个 DryRun/Execute 对证明使用同一计划，且 DryRun 零写。
- 让计划中任一页面、机器区、导航、端点或反向关系失效，验证 Execute 不产生部分写入；
  模拟耐久不确定，验证普通重试被 recovery overlay 阻止。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
