# 运行时权威与派生投影

## Authority 提交与派生投影

本合同定义 operation record、content、catalog/head authority 与派生资料之间的耐久提交关系。
它不定义页面、关系、路径 mapping 或 Trash package 的领域含义；只规定已认证事实如何被记录、
提交、恢复和投影。

operation、manifest、progress、generation、receipt、cleanup、recovery record、比较摘要和锁算法
只供 Draft 读取和写入，不构成外部兼容接口。外部工具只能调用公开 runtime 入口，不能依赖
它们的字段、编码、哈希、文件名、物理布局或目录位置，更不得绕过恢复规则改写它们。

### 输入、操作身份与公共输出

输入是已通过准入的逻辑 operation：规范化 request、计划实际消费的已认证事实闭包、matching
intent、变更前/后 authority、staged regular file、进度与派生资料。公开只投影
`OperationOutcome`、`QueryOutcome`、`RecoveryOutcome` 或 error envelope，完整字段见
[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)。

一个逻辑 operation 的公开身份是小写 UUID v4 `operation_id`。runtime 先比较规范化 request，
再比较本次计划实际消费且任一变化都会改变计划或公开结果的所有认证事实；两组语义值都相同
的重试复用 id，任一值变化则建立新 id。仅当 runtime 已识别直接前项时，成功的 `applied`
结果可附带同为 UUID v4 的 `previous_operation_id`；不公开 attempt、比较编码或完整历史。

对 pending `D-MOVE`，authority owner 消费同一个 immutable eligibility snapshot `S`。其
领域载荷由 [D-MOVE snapshot S 语义载荷](../draft/structure.md#d-move-snapshot-s-语义载荷)
定义，恢复资格由 [D-MOVE snapshot S 恢复门禁与 Q6-B 漂移分类](recovery.md#d-move-snapshot-s-恢复门禁与-q6-b-漂移分类)
定义。`S` 的字段、编码、哈希和保存位置是 Draft 私有实现；本页不从它推导新的公开字段。

### 权威不变量

| 维度 | 互斥状态 | 必须保持的事实 |
| --- | --- | --- |
| owner/intent | matching；缺失、伪造、重复或不匹配 | 只有 matching intent 可授权下一 authority；不得扫描终态记录猜测。 |
| authority | 完整变更前；完整下一版本；类型/identity/digest/generation 矛盾 | `active.json` 与 `index/head.json` 是唯一替换型 authority；矛盾为 `runtime.corrupt`。 |
| 物理阶段 | intent、immutable record、staged、replace、父目录 fsync、committed progress | 下一阶段以前一阶段认证且 durable 为前提；内存、mtime、目录枚举不能推断 generation。 |
| projection | 缺失/旧 generation 或 digest；staged；同代 durable；失败 | schedule/cache 永不成为 authority，也不改变 authority。 |
| settled proof | matching committed head 指向 published/invalidated generation；未 settled | cache 不能证明 content 已提交。 |

`active.json` 与 `index/head.json` 只可由同目录、已 fsync 的 exact staged regular file 经
`replace_exact_regular` 原子替换，随后 fsync 父目录。内部 active entries、predecessor、
transition/epoch digest 和顺序必须能认证，但不对调用方公开。结构操作的 `before -> after`
mapping 必须完整、去重、规范化且确定；它是 manifest、路径、parent、navigation、链接闭包、
collapse 与恢复的内部单一来源，不产生公开路径列表。

### Authority 提交与恢复表

下表适用于 active/head authority。每行都要求 owner、intent、变更前/后版本、staged digest
精确匹配；按从上至下优先级只命中一行。pending `D-MOVE` 仅在 recovery owner 已完成通用
先验门禁与 `S` gate 后使用此表，不重新分类 schema、lock 或 `S`。

| 已认证持久事实与物理阶段 | authority 观察 | 唯一内部动作 | 对公开结果与恢复的约束 |
| --- | --- | --- | --- |
| owner/intent/transition/staged 缺失、伪造、重复，或 predecessor/摘要/版本矛盾 | 任意 | 停止 | `runtime.corrupt`，零 authority/projection 写，保留现场。 |
| matching intent durable，immutable transition/epoch 尚缺，authority 为变更前 | 完整变更前 | 创建精确绑定 immutable record | 继续 staging；不得建立第二 authority。 |
| matching intent 与 immutable record durable | 完整变更前 | 写并 fsync exact staged candidate，重验 target 后 replace | 只有该 intent 可授权下一版本。 |
| replace 返回错误 | 完整变更前 | 保留 intent/staged | `runtime.durability_failed`；不提交、不 release，显式 recover 才可继续。 |
| replace 返回错误 | 完整下一版本 | 重认证所有匹配事实并 fsync 父目录 | 父目录 fsync 失败仍为 `runtime.durability_failed`；此前不记录 committed progress。 |
| replace 已成功但父目录 fsync 未完成或重启后不确定 | 完整变更前 | 保留 intent/staged | `runtime.durability_failed`，不得把旧 progress 当 committed。 |
| replace 已成功但父目录 fsync 未完成或重启后不确定 | 完整下一版本 | 重认证后成功 fsync 父目录 | 随后才可记录 matching committed progress。 |
| authority 父目录 fsync 完成且 matching committed progress durable | 完整下一版本 | 消费 authority，再处理派生资料 | authority 才可影响自动工作；head 才可证明 content settled。 |
| 完整下一 authority durable，其他 pending 事实都匹配，唯一缺口是 committed progress | 完整下一版本 | 补写精确 matching progress | progress durable 后继续投影和 settled 检查；不得建立第二 authority 或回滚。 |
| target 缺失、符号链接、目录或其他 identity/digest/generation | 任意 | 停止 | `runtime.corrupt`，零 commit、零 release、零自动推进。 |

下一 authority 的逻辑提交点是父目录 fsync 与 matching committed progress 都完成之后。在此之前，
调用方只能收到机器错误；不得把 memory、projection 或可见的 after 文件投影为成功。

### 派生资料、清理与读 continuation

schedule 从 active authority 重建，cache 从 committed head/current content 重建。二者可能缺失、
过期、staged、同代 durable 或发布失败；任何一种都不改变 authority。预期不存在的派生文件
使用 create-only publish，预期存在时只能 `replace_exact_regular`。cache 的安全降级不回滚
已提交 content/head；稳定 catalog/head 损坏则是 `runtime.corrupt`。

| projection 现场 | schedule 的唯一动作 | cache 的唯一动作 | authority/公开影响 |
| --- | --- | --- | --- |
| authority/head 尚未 durable，只有 staging | 只清理 intent 精确拥有的 staging，或从当前 active generation 覆盖 | 只清理 intent 精确拥有的 staging | 不改变 authority。 |
| staging durable、replace 前 | 从 active authority 重建同代文件 | 从 committed head/current content 重建；head 未提交则丢弃 staging | 不改变 authority。 |
| replace 返回、父目录 fsync 未完成 | 重读 authority，匹配才保留，否则重建 | 重读 head/content，三者匹配才保留，否则重建 | 不改变 authority。 |
| replace 与父目录 fsync 完成 | 接受同代文件，过期时重建 | 匹配时接受，失败时安全降级并重建 | 不回滚 content 或 authority。 |
| 所有当前 authority、有效 pending operation、manifest、intent、Trash/Restore/Handoff package 与 cleanup 授权完整认证，某临时 material 无任何引用 | 精确删除并 fsync 所在目录 | 同左 | 只在认证证明无主时 cleanup。 |
| 任一所有权来源缺失、不完整、不可认证或矛盾 | 停止并保留 material | 同左 | 不足证据为 `runtime.recovery_blocked`；不可认证/矛盾为 `runtime.corrupt`。 |

查询的公开 continuation 由错误与可观测性合同定义：每个不透明 cursor 自身绑定同一语义快照
和集合，相关 authority 改变后续页即失效。cursor 之外没有单独的读取版本字段。它的编码、
完整性保护和内部最后排序位置是私有实现；公共 entry 排序、`total` 和 continuation 行为由
query 合同定义。

### 失败、重试与恢复

authority 写入顺序固定为：intent progress -> immutable transition/epoch -> staged exact
authority -> replace + authority 父目录 fsync -> committed progress -> schedule/cache。head durable
失败是 fatal；cache 发布失败只可安全降级。普通自动工作、Check 和 read 只从 active/head
authority 发现候选，不能枚举 terminal progress。

显式 `recover()` 仅在已认证 pending 现场上做只读发现和授权操作。完整下一 authority 且唯一
缺 matching progress 时，补记该 progress；完整所有权闭包证明临时 material 无主时，精确
清理该 material。其他现场不得通过目录存在、修改时间、终态审计或内部 phase 猜测。恢复的
公开 `outcome`、可选操作编号和调用方动作由 [显式恢复](recovery.md#显式恢复与-recoveryoutcome)
定义，永不公开 authority version、内部动作名称或路径。

### 可证伪验证

- 在 intent-only、immutable record、replace 前后、父目录 fsync 前后与 committed progress 缺失
  的每一行注入失败，验证表中唯一结果。
- 验证 owner/intent/predecessor/digest 之外的 record 为 `runtime.corrupt`，不创建第二
  transition/epoch，也不扫描终态记录。
- 验证 schedule/cache 在所有崩溃窗口只能重建或安全降级，永不成为 authority；cache 失败
  不回滚已提交内容。
- 构造“完整下一 authority 唯一缺 progress”、“临时 material 明确无主”和“任一所有权来源
  不完整”三个现场，验证补记、精确清理、保留报错的边界。
- 验证任何公开 operation/recovery/query 结果都不含 manifest、progress、epoch、内部 mapping、
  路径列表、内部 phase 或独立读取版本字段。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#7-recordauthority-与派生投影`
