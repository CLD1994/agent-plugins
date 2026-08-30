# 页面与导航

## 页面角色与导航

`D-PAGES` 定义受管理页面的路径角色、物理父关系和可见导航。它消费结构操作已经
认证的 before-to-after mapping，不在此页重新定义 mapping 或 runtime 提交算法。公开结果
不提供 source/destination 路径列表；本页只定义页面角色、parent 和导航的一致性。

### 输入与输出

- 输入是根状态、规范相对路径、页面当前树、创建或页面纳管请求，以及受影响父页面的
  导航事实；`move` 的页面纳管还必须先通过 `structure.md#来源决策矩阵` 的
  source/managedness/target 分类、目录成员级分类和完整链接闭包门禁。
- 输出是路径角色、唯一物理父页、相应导航 after-state，或可诊断的拒绝；公开结果不提供
  路径列表，字段名和诊断码尚未获批。
- 结构操作成功时，路径、父关系和导航必须从同一已证明 mapping 派生，不能分别按
  basename、文本相似度或目录枚举猜测。目标碰撞由该 mapping 在写前统一判定；本页不
  创建第二套 collision 或路径推导规则。

### 路径角色与不变量

| 路径形状 | 角色 | 允许性与不变量 |
| --- | --- | --- |
| `index.md` | 唯一根页 | 根没有 `parent` 页面；它是唯一根级 index。 |
| `pages/<...>.md`（不含保留 `pages/index.md`） | 叶页面 | 不拥有子页面命名空间。 |
| `pages/<topic>/index.md` | 枢纽页面 | 拥有该目录的直接子页面命名空间。 |
| `pages/index.md` | 保留且非法 | create、页面纳管、move 的目标 mapping 均不得产生它；既存节点由 Check 报告路径角色错误。 |
| 根内其他 Markdown | 未受管理 Markdown 或非页面制品 | 不是受管理页面；是否可在 candidate 中经 `move` 纳管由 [结构操作](structure.md#move) 定义。 |

每个非根受管理页面恰有一个物理父页；其 `parent` 事实、物理路径和父页中可见的直接
子页面导航必须一致。直接子页面在父导航中恰出现一次，不得重复、错指或以叶/枢纽两种
路径同时表示。根没有子页面时，仍恰好保留一个空的 `## 子主题`；这不是可省略的格式
分支。

### 决策表、转换与提交

| 请求后的路径角色 | 物理父与导航事实 | 根状态 / recovery | 结果 |
| --- | --- | --- | --- |
| 合法根、叶或 hub | 可认证且三者一致 | 可写根且无 pending | 可纳入完整计划；在同一领域提交中更新路径、`parent` 和必要导航。 |
| 合法根、叶或 hub | 缺失、重复、错指或双表示 | 任意 | 拒绝；零写。 |
| `pages/index.md` | 任意 | 任意 | 拒绝、零写，并由 Check 报告路径角色错误。 |
| 未受管理 Markdown 进入页面命名空间 | mapping、父关系、导航与完整闭包均可证明，且满足 move 的 candidate 门槛 | 由 move 合同认证 | 只允许按 `D-MOVE` 的页面纳管分支提交；不凭路径自动补齐标题、机器区、状态或关系。 |
| registered 根中的未受管理 source（包括 directory 的 `file`/`prefix` target） | 任意 | 已认证根但不满足 candidate 纳管 | 拒绝，零写；不得借页面角色或 parent/nav 规则创建所有权。 |
| 非 Markdown、`.draft` 内节点或路径逃逸 | 任意 | 任意 | 不是页面输入；拒绝，零写。 |
| 文件 source 含附件/非 Markdown/无法分类节点，或目录 retained 节点未能认证 | 任意 | 任意 | 不是页面输入；按 `D-MOVE` 矩阵外门禁拒绝，零写。 |
| root state 为 unknown、mixed 或不可认证，或 mapping/链接闭包不可认证 | 任意 | 任意 | 写前拒绝，零写并保留诊断；不得把未知根、不完整闭包或 lower-bound mapping 解释为合法页面树。 |
| 任一需要恢复的根 | 任意 | pending | 阻止转换，保留现场，等待显式 recovery。 |

提交点在所有受影响页面、关系和必要导航都已进入同一个完整计划之后；路径、parent、
导航、入站/出站链接和自动 collapse（固定 after-path 为 `pages/topic/index.md ->
pages/topic.md`）均消费同一 mapping，不能拆成多个可见成功。candidate 的
Check 或协议欠账只表示可观察的现有事实，不反向拒绝已经满足 `D-MOVE` candidate
纳管门槛的 Markdown；若欠账使 mapping、parent、nav 或闭包无法证明，则按上表写前失败。
具体导航元素的
机器区/正文边界、序列化格式和 runtime durable record 为 `unresolved`；实现不得借此
省略三者一致性。

对 pending `D-MOVE`，恢复或 partial-after 继续前，页面 owner 只能消费结构 owner 在锁内重
认证通过的完整 snapshot `S`。pending `D-MOVE` 的 S 缺失或 Q20/Q21 尚无资格证明时保持
`unresolved` 并零写；
出现矛盾、摘要不匹配或 parent/navigation 事实不一致时报告 `runtime.corrupt` 并保留现场，
不能 forward。非 `D-MOVE` operation 的 recovery/partial-after 不适用该 S；页面 owner 直接
消费 runtime recovery owner 的
[证据优先恢复决策表](../runtime/recovery.md#证据优先恢复决策表)，不额外要求 S。

### 失败、零写与恢复

- 导航无法解析、父页不存在、角色冲突、目标碰撞或 mapping 不可证明时，不写任何受
  管理页面、关系或导航。
- runtime 报告耐久不确定时不返回结构成功；保留 recovery 现场。重试前按 operation 类型
  重新认证：pending `D-MOVE` 必须重新认证整棵受影响树和 S，不能复用过期导航事实；非
  `D-MOVE` operation 的 recovery/partial-after 按 runtime 的
  [证据优先恢复决策表](../runtime/recovery.md#证据优先恢复决策表) 重新认证，不额外引入 S。
- 外部手工移动没有可证明 mapping 时，不能由本合同猜测 parent 或导航；必须失败并由
  Check 诊断。

### 验证

- 覆盖根、叶、hub、空根、`pages/index.md`、重复导航、错指导航与叶/hub 双表示。
- 对一个多路径结构变更，证明路径、parent 和导航从同一 mapping 同时变化；任一事实
  缺失时验证零写。
- 验证 candidate 页面纳管后仅获得路径角色，协议欠账仍可由 Check 观察；registered
  unmanaged source、目录成员 `mixed`/`unknown`/`unverifiable`、unknown/mixed/unverifiable
  root、非 Markdown/附件 source 和不完整闭包均验证写前拒绝零写。验证路径、parent、导航
  和 collapse 均从一个 mapping 派生。对 pending `D-MOVE`，验证 S 缺失与 S 矛盾分别落入
  `unresolved` 与 `runtime.corrupt`；对非 `D-MOVE` recovery/partial-after，验证完整 generic
  evidence 且无 S 时页面层不额外阻断，结果由 runtime 的 generic authority/evidence table
  决定。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
