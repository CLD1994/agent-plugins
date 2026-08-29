# Trash 与 Restore

## Trash 与 Restore 行为合同

`D-TRASH-RESTORE` 定义受管理 Markdown 的回收包、7 天恢复窗口、活动成员资格和恢复
冲突边界。它不复制 runtime 的 receipt、handoff、durability record 或清理算法。

### 输入、输出与不变量

- Trash 输入是可认证的受管理 Markdown 页面/子树、根状态、活动成员资格、完整页面字节、
  机器区、身份/摘要和已授权的关系/导航事实。
- Trash package 只保存上述受管理 Markdown 事实；不自动包含非 Markdown、附件、未知节点
  或生成的 `conclusions.md`。不因物理邻接扩大 package，也不输出 retained facts。
- 成功 Trash 的领域 after-state 是目标从活动页面成员移出，保留可恢复 package 和生命周期
  记录。`conclusions.md` 快照不阻止 Trash，且 Trash 不删除快照条目。
- Restore 输入是不透明 `TrashIdentity`、同一根、当前 authority/active membership、记录的
  after-state、共享观察时刻和保留 package。成功后仅在完整提交后才重新加入活动成员。
- 7 天是 Restore 的有效窗口。起算时刻、时区编码和 identity 的公开格式尚未获批，不得从
  实现或测试推断；资格判断必须在同一次可认证观察中完成。

| 事实 | 不变量 |
| --- | --- |
| package 成员 | 仅受管理 Markdown 完整字节、机器区、身份/摘要和已授权关系/导航事实。 |
| 页面所有权 | 非 Markdown、`conclusions.md` 和未授权目录事实不进入 package。 |
| active membership | Trash 完整提交时移出；Restore 完整提交后才加入。 |
| restore 对比 | 当前根与记录的 after-state 必须精确匹配；不覆盖、不猜测合并。 |
| 清理 | 只有最终 authority、catalog/head、matching progress/receipt 均 durable 时，才可按授权清理。unknown、失败或证据不足保留现场。 |

### Trash、Restore 与恢复资格决策表

| 请求 / 身份状态 | 认证与时间事实 | 领域结果、副作用与零写边界 |
| --- | --- | --- |
| Trash 受管理 Markdown | package、授权关系/导航、active membership 和 root authority 均可认证 | 形成完整 Trash 计划；提交时移出 active，保留 package 与生命周期记录。 |
| Trash 非 Markdown、生成的 `conclusions.md`、未管理节点或 package 边界不可认证 | 任意 | 拒绝，零写；不通过目录内容推断 package。 |
| Trash 的结论快照 stale 或有链接观察 | 任意 | 不阻止 Trash，也不修改快照。 |
| Restore：lexical invalid | identity 不能按词法接受 | 拒绝，不查找或写入。 |
| Restore：missing | identity 合法但无对应保留 package | 拒绝，零写，保留可诊断事实。 |
| Restore：expired | 超过同一可认证观察中的 7 天窗口 | 不恢复内容；不把过期解释为 consumed，清理仍受 durable 授权约束。 |
| Restore：consumed | package 已被完整成功恢复并消费 | 拒绝重复恢复，零写。 |
| Restore：conflict | 当前 root / after-state 与记录不精确匹配 | 拒绝，不覆盖、不猜测合并，保留 package。 |
| Restore：corrupt | package、身份、摘要、关系/导航或 authority 证据矛盾/不可认证 | 拒绝并保留现场；不得清理或重建 package。 |
| Restore：recovery-required | runtime 指出 pending 或耐久结果未收敛 | 不做新的 Restore 写；只有显式 `recover()` 可收敛。 |
| Restore：有效 | package、after-state、时间、目标路径和完整 after-plan 可认证 | 完整领域提交后恢复页面并重新加入 active；成功前不得先消费 package。 |

若 Trash 前后的结构变化需要 reverse-collapse，必须从记录的 mapping 与 after-state 证明；不能
从当前目录形状、页面名或缺失文件猜测。reverse mapping 的精确算法/字段尚未获统一批准，
故为 `unresolved`，需要它而无法证明时必须进入 conflict 或 corrupt，而不能尝试局部恢复。

### 提交、耐久清理、失败与恢复

Trash 与 Restore 各自只允许一个完整领域提交。package 消费、目录清理或交给后续系统的
cleanup 不能在 authority、catalog/head、matching progress/receipt 尚未全部 durable 前发生。
这几个 durable fact 的精确字段、授权谓词和清理清单由 runtime owner 定义，当前为
`unresolved`；本页的强制边界是证据不足时保留现场。

提交后耐久结果不确定时，不报告 Trash/Restore 成功，也不改变后续 active membership 的
可观察结果；runtime 保留 recovery pending。重试首先重新认证 identity、after-state、
package 和 durable evidence；只有显式 `recover()` 可以做 forward、rollback 或授权 cleanup。
任何恢复过程都不得为了“释放空间”扩大 package、删除非 Markdown 或生成的结论制品。

### 验证

- 覆盖单页/子树、非 Markdown 邻接、`conclusions.md`、active membership、stale 快照、
  7 天内外和每个 Restore 身份分区。
- 验证 Trash 只包受管理 Markdown，Restore 成功后才加入 active，冲突/损坏/未知耐久证据
  始终保留现场。
- 模拟 authority/catalog/head/progress/receipt 任一项未 durable，验证不会 cleanup 或消费
  package；验证 reverse-collapse 证明缺失时不猜恢复路径。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
