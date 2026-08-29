# 运行时权威与派生投影

## Authority 提交与派生投影

本合同定义 operation record、content、catalog/head authority 与派生 projection 的
耐久提交关系。它不定义页面、关系或路径 mapping 的领域含义；只规定这些已认证事实
如何被记录、提交、恢复和投影。

### 输入与公开输出

输入是已通过准入的逻辑 operation：稳定 `operation_id`、immutable request、before/
after closure digest、当前 authority 的 generation/digest、matching intent、staged
regular file、immutable transition 或 epoch、progress 与 projection。输出是下列可观察
状态之一：

- 仍为完整的变更前 authority；
- 完整、耐久且 matching committed progress 已记录的下一 authority；
- `runtime.durability_failed`、`runtime.corrupt` 或待显式恢复的未完成现场；
- 已认证 authority 对应的 schedule/cache projection，或已分类为可重建/可降级的
  projection 结果。

`manifest`、progress、epoch 与 `lifecycle/transitions/<generation>.json` 都是
immutable、create-only、hash-linked record。manifest 在它拥有的 artifact 前发布，
保存 immutable request、before/after closure digest、精确 owned artifacts 与 cleanup
list。一个 `operation_id` 跨 manifest、progress、content、catalog/head 和 `Outcome`
关联；同一意图/authority snapshot 的 retry 新增 attempt 但复用此 id。意图或 snapshot
改变时创建新操作并关联旧操作；比较字段、关联字段和 `attempt_id` 的公开 ABI 仍为
`unresolved`。

### 状态维度与不变量

| 维度 | 互斥状态 | 不变量 |
| --- | --- | --- |
| owner/intent | matching owner、before/after、staged digest；缺失/伪造/重复/不匹配 | 只有 matching intent 可授权下一 authority；不得扫描 terminal record 猜测。 |
| authority | 完整变更前；完整下一版本；缺失、符号链接、目录、identity/digest/generation 矛盾 | `active.json` 与 `index/head.json` 是唯一替换型 authority；第三种状态为 `runtime.corrupt`。 |
| 物理耐久阶段 | intent、immutable record、staged、replace、父目录 fsync、committed progress | 下一阶段以前一阶段已认证且耐久为前提；内存、mtime、目录枚举不能推断 generation。 |
| projection | 缺失/旧 generation 或 digest；staged；同代 durable；失败 | `schedule.json` 与 `cache.json` 绝不成为 authority，也不改变 authority。 |
| 内容 settled proof | matching committed head 指向 published/invalidated epoch；未 settled | cache 不是 settled proof；内容提交后不得因派生失败回滚。 |

`active.json` 保存 `generation`、`predecessor_generation`、`predecessor_digest`、
transition digest 与按 operation id 排序的完整 active entries。每个 entry 带 operation
id、kind、progress digest、due/retry deadline、R -> T 引用或阻塞原因。`index/head.json`
同样保存 generation、predecessor 与 immutable epoch digest。二者只能使用同目录、
已 fsync 的 exact staged regular file，经 `replace_exact_regular` 原子替换并 fsync
父目录。

`catalog-transition-intent` 与 `catalog-transition-committed` 绑定同一 transition id；
`index-transition-intent` 与 `index-transition-committed` 绑定 epoch head。结构操作
使用的 manifest `before -> after` mapping 必须去重、规范化并有序；公开
`changed_paths` 也去重并按规范化路径字典序排序。自动 collapse 若由该操作触发，属于
同一 `operation_id` 与逻辑提交，只记录其子阶段 mapping；本页不扩张 mapping 的领域
准入条件。

### Authority 提交与恢复决策表

下表适用于 `active.json` 与 `head.json`。变更前 authority 版本是开始时已持久的
generation/digest；下一 authority 版本是 matching intent 唯一授权的 generation/digest。
每行都要求 owner、变更前版本、下一版本、staged digest 与 intent 精确匹配；按从上到
下的优先级命中一行，且每行隐含前面各行均不满足，因此分区互斥。

| 可认证事实与物理阶段 | authority 观察 | 唯一动作 | 公开结果、release 与重试 |
| --- | --- | --- | --- |
| owner/intent/transition/epoch/staged 缺失、伪造、重复或任一 predecessor/摘要/版本矛盾 | 任意 | 停止 | `runtime.corrupt`，零 authority/projection 写并保留现场；不得猜测或扫描 terminal audit。 |
| matching intent 已 durable，immutable transition/epoch 尚缺，authority 仍为变更前版本 | 完整变更前 | 创建与 intent 精确绑定的 immutable record | 继续 staging；不得产生第二 authority。 |
| matching intent 与 immutable record 已 durable | 完整变更前 | 写并 fsync exact staged candidate，重验 target 后 replace | 仅此 intent 可授权下一版本。 |
| replace 返回错误 | 完整变更前 | 保留 intent/staged | `runtime.durability_failed`；不 commit、不 release、不参与 AdvanceDue；后续显式 recover 才可重新尝试。 |
| replace 返回错误 | 完整下一版本 | 重新认证 intent、owner、before/after 与 target；成功 fsync 父目录后才可继续 | 父目录 fsync 再失败为 `runtime.durability_failed`；此前不 commit。 |
| replace 返回成功、父目录 fsync 失败或重启后耐久性未确认 | 完整变更前 | 保留 intent/staged | `runtime.durability_failed`；不得将旧 progress 当 committed。 |
| replace 返回成功、父目录 fsync 失败或重启后耐久性未确认 | 完整下一版本 | 重新认证所有匹配事实并成功 fsync 父目录 | 仅随后才可记录 matching committed progress；fsync 再失败为 `runtime.durability_failed`。 |
| authority 父目录 fsync 已完成，matching committed progress 已 durable | 完整下一版本 | 消费该 authority；再处理 projection | catalog membership 才可影响 AdvanceDue；head 才可证明 content settled 并 release。 |
| authority 已 durable，但 matching committed progress 缺失 | 完整下一版本 | accepted Q16 的高层 disposition 是 `forward`，不得建立第二 authority 或回滚完整 after authority | 精确的 progress 补记授权、记录字段与公众结果仍为 `unresolved`；在该 ABI 签署前不得把现场投影为 settled 成功。 |
| target 为缺失、符号链接、目录、其他 identity/digest/generation | 任意 | 停止 | `runtime.corrupt`，保留全部现场、零 commit、零 release、零 AdvanceDue。 |

下一 authority 只有在父目录 fsync 与 matching committed progress 都完成后才是逻辑提交点。
此前只能观察到完整旧 authority、完整但尚未公开 settled 的 after authority，或
recovery-pending；不得把中间 projection 当作成功。

### Projection 决策表与 snapshot token

| projection 现场 | `schedule.json` 的唯一动作 | `cache.json` 的唯一动作 | 对 authority/公开结果的影响 |
| --- | --- | --- | --- |
| authority/head 尚未 durable，只有 projection staging | 仅清理 intent 精确列举的 staging，或以当前 active generation 覆盖 | 仅清理 intent 精确列举的 staging；不得当作新 cache | 不改变 authority。 |
| staging durable、replace 前 | 从 active authority 重建并 replace 同 generation projection | 从 committed head/current content 重建；head 未提交则丢弃 staging | 不改变 authority。 |
| replace 已返回、父目录 fsync 未完成 | 重读 authority；generation/digest 匹配才保留，否则重建 | 重读 head/current content；head generation、content digest、cache digest 全匹配才保留，否则重建 | 不改变 authority。 |
| replace 与父目录 fsync 均完成 | 接受同代 projection；过期时静默重建 | 接受同代 cache；不能匹配时分类降级并重建 | schedule/cache 失败不得回滚 content 或 authority。 |
| 未知 staging、无法验证 predecessor 或 replace target 异常 | 停止 | 停止 | `runtime.corrupt`，不得以历史扫描修复。 |

预期缺失的 projection 使用 create-only publish；预期存在时只能经
`replace_exact_regular`。`schedule.json` 从 active catalog 以 O(A) 重建；`cache.json`
只有与同代 head、content 和完整 scan 匹配时可用。稳定 catalog/head 损坏为 fatal。

每次 read 的统一 snapshot token 在语义上覆盖 root、content、active members、head
与相关 projection generation；任一相关 authority 改变即失效。token 的编码、字段名
和 `ReadOutcome` 中的确切位置不是本合同可补造的 ABI。

### 副作用、失败、重试与恢复

authority 写入的顺序为：intent progress -> immutable transition/epoch -> staged exact
authority -> replace + authority 父目录 fsync -> committed progress -> schedule/cache。
head durable 失败是 fatal；cache publish 失败可安全分类为降级。普通
AdvanceDue、Check 和 normal read 只能从 active/head authority 发现候选，不枚举
terminal progress。显式 `recover()` 才能在已认证 pending 现场上进行只读 discovery；
其 Q20/Q21 级别的 owner/staging/progress 细分见本页 `unresolved`，不得由这一规则
反推。

### 可证伪验证

- 在 intent-only、intent+immutable record、变更前 authority、下一 authority、replace
  前后、父目录 fsync 前后及 commit 缺失的每一行注入失败，验证表中唯一结果。
- 验证 matching owner/intent/predecessor/digest 之外的 record 产生 `runtime.corrupt`，
  不创建第二 transition/epoch，也不从 terminal audit 恢复。
- 验证 schedule/cache 在所有崩溃窗口只能重建或降级，绝不成为 authority；cache 失败
  不回滚已提交内容。
- 验证 read token 覆盖事实一旦变化即失效，且公开 token ABI 未被测试夹具反向定义。

### `unresolved` 的跨页 ABI 与未签分区

| 项目 | 状态 | 阻断原因 |
| --- | --- | --- |
| manifest/progress/epoch 的精确字段、record 编码与 hash 链字段名 | `unresolved` | 已接受或已签来源固定 immutable/认证性质，未给出字段级 schema。 |
| `operation_id` 生成、intent/snapshot 比较集合、旧操作关联字段、`attempt_id` 公开性 | `unresolved` | 已接受重试语义未定义这些 ABI。 |
| snapshot token 的编码、字段名、失效后的 `ReadOutcome` 字段顺序 | `unresolved` | 已接受覆盖/失效语义，未有统一公共协议来源。 |
| authority durable 但 progress 未补记时“补记”的精确条件、写入与输出 | `unresolved` | 该 Q21 细分没有同等级接受或 signer locator；它阻断把该现场宣布为 settled。 |
| orphan/unknown staging 的精确资格谓词、cleanup 清单和 progress 交叉认证 | `unresolved` | 该 Q20 细分没有同等级接受或 signer locator；不得以目录内容自行分类。 |

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#7. Record、authority 与派生投影`
