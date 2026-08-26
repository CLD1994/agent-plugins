# Draft 实施总计划

本文件是供人工审阅的执行总计划，不修改任何 issue 的 `Status` 或 checklist，也不把计划、工作树改动或局部测试记录当作完成证据。

## 1. 依据与标记

本计划已完整核对以下材料：

- `AGENTS.md`、`CONTEXT.md`、`docs/agents/issue-tracker.md`；
- `.scratch/draft-coordinated-recovery-ownership/spec.md` 及其 Issue01-05；
- `.scratch/draft-durable-link-index-maintenance/spec.md` 及其 Issue01-18；
- `docs/review-notes/draft-recovery/2026-08-26-round-1.md` 至 `round-4.md`。

文中的“确认要求”是规格、Issue 或顾问复审已经固定的外部行为；“实现预估”是为了安排工作而提出的模块边界、测试组织或顺序建议，实施者可以在不改变确认要求的前提下调整。若实际代码出现规格未定义的状态、机器协议或锁语义，必须暂停该分支并提交证据，不能用默认值或局部补丁自行选定含义。

## 2. 已确认行为契约

### 2.1 协调恢复的共同不变量

以下要求同时适用于 `recover(root)`、`recover_interrupted_operations(root)` 和 `advance_trash_lifecycle(root, ...)`：

- Draft 根内的权威业务事实是当前目录、journal、`.pending`、恢复材料和 R/T 关系；链接索引只是派生缓存。受协调 writer 通过同一 Draft 根的 matching `operation.lock` 串行化，绕过 lock 的本地进程不在原子防护承诺内。
- 未持锁 discovery 只能读取和分类。它不得发布 `.pending`，写入或删除 journal，清理 copies、package 或 operation 目录，创建硬链接，调用 handoff，或改变受管内容。
- 任何会改变现场的候选都必须取得对应 operation ID 的 matching lock；取得后丢弃初扫快照，重新以当前目录成员、当前 journal、`.pending`、身份和关系事实分类。竞争失败只返回 `operation.recovery_required`，不得使用旧快照继续处理。
- 损坏、非法、未知、符号链接、身份不一致、关系无法解释或无法证明所有权时必须失败关闭，保留 journal、pending、copies、package、operation 目录、R/T 关系和已有 lock。不得用跳过、默认空值、递归删除、回滚或“无关目录”折叠错误。
- 副作用顺序由当前事实授权：先 no-follow/类型/身份/transition/关系验证，再写 phase、发布 pending、清理材料、handoff 或解锁。恢复错误不表示业务内容可以回滚；索引故障也不回滚已经验证的业务 mutation。
- 空 allocation residue 只在 matching lock 内重新确认仍为空后删除，并对 operations 父目录 `fsync`，然后才释放 lock。非空无 journal、未知成员或 pending 不是空 residue。

### 2.2 顾问裁定：R/T 错误优先级和分类

下表是互斥事实分区的确认结果；同时满足多个尚未由规格定义的异常时不得自行推导优先级，应停下报告现场。路径节点的 no-follow 约束贯穿所有读取和写入。

| 优先级 | 当前事实 | 稳定结果或允许动作 |
| --- | --- | --- |
| 1 | raw JSON、v4 shape/type 错误；任一 `metadata` key/value 不是 string，包括 `null`、number、list | `operation.journal_corrupt`；失败关闭，保留现场 |
| 1（路径边界） | operation、journal、pending、恢复材料、索引目标或受保护祖先出现符号链接逃逸 | `path.symlink_escape`；不得跟随或改写目标 |
| 2 | R 存在但缺少 `metadata.restores_trash_operation`，或值不是规范的 32 位小写 hex operation ID | `operation.plan_invalid`；不得由 T 的反向 claim 补写 R -> T |
| 3 | R forward 合法后，T 缺失、T kind 错、T claim 非法、ID 错配、T-only/R-only 不可解释、全局 MULTIPLE、未批准单向关系 | `operation.inconsistent_state`；不得回滚、清理、handoff 或解锁来掩盖关系错误 |
| 4 | 图关系合法，但 R/T phase、terminal/waiting 组合不属于该关系允许集合 | `operation.inconsistent_state`；保留全部证据 |
| 合法窗口 | R=`restore/allocating`，无 observations/mutations/remove dirs，除 forward 外 metadata 精确为空，且 T 只被 R 前向认领 | `PRECLAIM`；只由 R restore owner 持锁处理 |
| 合法窗口 | R=`rolled_back`、T=`trash_retained`，T claim 已耐久删除，R 仍保留授权清理关系 | `POST_UNCLAIM_CLEANUP`；只执行 R 授权清理 |
| 合法双向 | 双方 claim 相互匹配且 phase/transition 与业务语义相容 | 只由 R restore owner 锁内前向完成或安全回滚 |
| 竞争 | lock 被另一受协调 owner 持有，或合法候选已经被其他 owner 持有 | `operation.recovery_required`；它不能表示 journal、计划、图或 phase 损坏 |

R 是 restore owner。不得从 `T.metadata.restore_operation_id` 反推或补写 `R.metadata.restores_trash_operation`，不得把缺失 T、T-only、MULTIPLE、terminal dangling 或 phase 矛盾当作普通 allocating 回滚。

### 2.3 current/pending 的唯一只读事实

第 4 轮复审增加并确认了 checker、recovery、lifecycle 必须共享的 effective journal index：

- 对每个 operation 明确区分 current、pending-only、current+pending 和无 journal/残留目录；“没有 current”不得静默解释为不存在。
- 以 no-follow 方式验证 current/pending 的节点类型、operation identity、kind、JSON/v4 shape/type、metadata 类型和 current -> pending 合法 transition。current+pending 通过验证后，以 pending 作为后续关系/phase 的 effective 版本，但仍保留 pending 存在事实供 checker 检查目录残留；pending-only 先验证 bootstrap transition，再进入同一 R/T 分类。
- 该边界只读，不发布、替换、删除或清理 pending。checker 与 recovery 必须消费同一 effective index；非法 forward 在两者中都必须是权威 `operation.plan_invalid`。
- relation reader 的任一 journal parse、identity/fstat、leaf read、operation directory/context read 或 close 错误都是授权证明失败。不得使用 `tolerate_unrelated_errors`、`continue`、默认空关系或“不是当前 T”来跳过。
- lifecycle 在取得 T lock 前关系读取失败，不得取得 lock 或产生任何 pending/journal/package/handoff 副作用；已经持有 T lock 后读取失败，必须保留 lock 和现场，在任何 phase 写入、发布、handoff、package 清理或安全解锁前停止。

### 2.4 FD 和异常优先级

- 每个成功取得的 descriptor 只有一个 owner，owner 在 close 前转移或清空所有权；每个 descriptor 最多尝试关闭一次。`open` 后 `fstat` 失败、read 失败和 close 失败都必须经过稳定错误边界。
- 无主体异常时，第一个稳定映射的 fstat/read/close 错误必须成为可观察的 `DraftError`；有主体 `DraftError` 时仍关闭其余资源，但 close 错误不得覆盖主体 code、现场保留语义或锁语义。
- 不得使用 `suppress(OSError)`、`suppress(DraftError)` 或宽泛 catch-and-continue 吞掉关系/FD 事实。`operation.plan_invalid` 必须由 `draft.diagnostic_codes` 的唯一常量登记，production、checker、lifecycle、recovery 和测试均从该来源读取；测试精确断言 `exc.code`，不以未锚定 regex 或人类文案代替机器协议断言。

### 2.5 链接索引权威和耐久契约

- 根内 Markdown 与目录事实是唯一业务权威；`.draft/link-index.json` 是可重建派生制品，`.draft/link-index-rebuild-required.flag` 只是零字节存在性快速阻断，journal 才是链接索引结果的唯一权威。
- journal 只支持 v4，不迁移、不双读、不填默认值；索引制品只支持 schema v2。`link_index_revision` 从字符串 `"1"` 开始，以 `[1-9][0-9]*` 规范十进制持久化，内存按任意精度整数比较；禁止前导零、符号、小数、指数、空白和 JSON number，也不按字符串字典序比较。
- phase 字段矩阵固定：`link_index_updating` 必须有 revision、不得有 result/sha；`link_index_published` 必须有 revision、`published`、制品 sha；`link_index_rebuild_required` 必须有 revision、`rebuild_required`、不得有 sha；禁止字段必须缺失而非 `null`。进入 updating 后不得 rollback；索引 phase 之后只能前向进入原业务成功终态。
- 发布顺序固定为：耐久写 updating -> operation 专属 staging -> staging/file sync -> 原子替换目标 -> 目标和 operation 目录同步 -> 耐久写 published，或在无法证明发布结果时耐久写 rebuild_required -> 尽力更新 flag -> 原有成功终态。journal 摘要必须对应最终制品 bytes。
- `link_index_updating`、`published` 制品不一致或 journal 结果无法耐久写入时按各自稳定错误处理。只要 `rebuild_required` 已耐久记录，已验证的业务结果保留，业务 operation 可成功退出并返回一次 `link_index.rebuild_required` warning；flag 普通 I/O 失败只增加一次 `link_index.rebuild_flag_update_failed` warning。journal durability failure 为 `operation.durability_failed`、退出码 3、保留 lock 和现场。
- fresh 证明必须读取全部覆盖 Markdown 并比较 SHA-256，同时验证目录成员/节点类型、schema、revision、制品摘要和 files/backlinks 双向完整性；inode、size、mtime 只能作单次进程内变化探针，不能证明新鲜。符号链接不跟随，不可读、非 UTF-8 或 CommonMark 提取失败不得解释为“没有链接”。路径和摘要都未变的文件可以复用解析事实，但相同 bytes 移到新路径必须按新来源重新解释相对资源身份。
- 实际 mutation（普通 Markdown 内容/路径或索引覆盖目录成员/节点类型）决定是否进入索引阶段；no-op、只改变 `.draft/` 内部状态、`init`、dry-run 和不消费索引的查询不得产生无意义 revision 或 maintenance journal。

### 2.6 入口、warning 与恢复

- 强制进入索引阶段的业务 operation 包括 create、move、promote、collapse、status set、relation add/remove、generate-conclusions、trash、restore，以及未来产生同类 mutation 的 kind；判定必须依赖实际 mutation，不维护命令白名单。
- 独立 `link-index.update` 由 notification、`check`、`query backlinks`、`query page` 和 adopt 完整预检查机会式触发；`query links`、relations/status/recoverable、dry-run、init 不触发全根索引维护。维护候选准备在锁外只读，提交前复核 authority revision/absent token；authority 已推进就丢弃旧候选，不覆盖新结果、不记录 rebuild-required。
- `draft notify files-changed --root ROOT` 只声明外部变化，不接收路径、类型、重命名映射、stdin 清单或 `--dry-run`；有效 candidate/registered 根收到通知固定退出码 0，JSON 只含 `result_type: files_changed_notification`，不投影 warning、revision、operation ID、路径或变化数量。通知遗漏不破坏正确性；参数/根选择/marker 错误和未预期程序缺陷仍按真实错误报告。
- `check`、query、adopt 对缓存降级保持页面事实透明：完整事实可用时不能被旧索引替代；性能降级、absent 和 flag 普通 I/O 降级不阻止 adopt，journal/制品矛盾、损坏或 recovery pending 阻止 adopt。notification 不吸收断言、类型错误或未知异常。
- `recover` 不创建独立 revision 或 maintenance operation；`link_index_updating` 恢复为同一 revision 的 `rebuild_required`，published 必须验制品 revision/sha 后补原终态，rebuild-required 尽力补 flag 后补原终态，任何恢复仍遵守上面的 R/T owner 和 matching lock 规则。

## 3. ASCII 依赖图与推荐顺序

下图中的箭头是前置契约，不是固定文件拆分。`R02` 的已验证结果是前置门，不应重新实现成另一套空目录语义。

```text
协调恢复轨：

[R01 Issue01: pending/material no-follow preflight]
       |------------------------------+
       v                              v
[R03 Issue03: effective index +     [R02 Issue02: 空 residue]
              全图 R/T + owner]      (已实施，round-3 通过，门禁复用)
       |              |
       v              v
[R04 Issue04: lifecycle 释放后重取]  [R05 Issue05: 三入口/FD/刷新验收]
       \              /
        +------------+

链接索引轨：

                 +--> [L02 Issue02: journal v4/state model] --+
[L01 Issue01: ports/domain]                                  |
                 +--> [L03 Issue03: pure engine] -------------+--> [L05 Issue05: durable storage]
                                      |                         |          |
                                      v                         +----------+--> [L06 Issue06: authority repository]
                               [L04 Issue04: strict root facts] --+                  |
                                                                  v                  v
                                      [L07 Issue07: no-side-effect candidate] --> [L08 Issue08: forward commit]
                                                                                         |
                                      +--------------------------------------------------v
                              [L09 Issue09: index phase recovery; 依赖协调恢复 owner 已闭合]
                                      |
                         +------------+-------------+
                         v                          v
                [L10 Issue10: independent maintenance] [L11 Issue11: business operation integration]
                         |                          |
              +----------+----------+               v
              v          v         v       [L12 Issue12: trash/restore lifecycle]
       [L13 Issue13] [L14 Issue14] [L15 Issue15]       |
              \          |         /                 |
               +---------+---------+-----------------+
                         v
                [L16 Issue16: entries/namespace]
                         v
                [L17 Issue17: durability/concurrency/freshness]
                         v
                [L18 Issue18: benchmarks/docs/final gates]

跨轨约束：R01/R03 --> L09；R03/R04 --> L12；R05 与 L16/L17 共同承担三入口端到端证据。
```

推荐顺序：

1. 阶段 0：先建立失败测试、现场快照、R/T 决策表、effective journal index 的行为测试和 DiagnosticCodes 断言；不先改入口分支。
2. 阶段 1：完成 R01 的 pending/material preflight 与当前事实边界；确认 R02 的 round-3 结果作为既有门禁；随后完成 R03 的全图 owner、checker 同码和 direct recover 停止语义。
3. 阶段 2：完成 R04 的 lifecycle release/reacquire；以 R05 的 FD owner × 主异常矩阵和三个公开 seam 做恢复轨收口。R02 不改 status/checklist，不因测试深度备注重新宣称未完成。
4. 阶段 3：按 L01 -> L02/L03 -> L04/L05 -> L06 -> L07 -> L08 建立领域、schema、严格扫描、存储、authority 和候选/提交基础；共享生产模块时采用串行实现和阶段门禁。
5. 阶段 4：在恢复 owner 已可用后完成 L09；随后 L10、L11，再接入 L12、L13、L14、L15。每一步先写最窄红测，经过 unit/integration 门禁后再扩入口。
6. 阶段 5：L16 汇总所有入口和 namespace，L17 运行耐久/中断/并发/新鲜度全矩阵，L18 交付基准、文档和最终门禁报告。

## 4. 逐 Issue 执行说明

每项的“前置”是确认的依赖加上必要的实施门；“实现预估”只约束验收边界，不固定 helper、文件名或模块拆分。

### 4.1 协调恢复所有权

#### R01 / draft-coordinated-recovery-ownership Issue01：锁内收敛未持锁候选

- **目标（确认）**：未持锁扫描完全只读；所有 pending 发布、journal 写删、材料/目录清理和 handoff 都在 matching lock 内以当前事实重新验证。普通 allocating 必须先耐久写 `rolled_back`，再按当前 journal 授权清理；恢复材料在任何 journal 写入前完成 no-follow、类型、成员集合、身份和摘要验证。
- **前置（确认/预估）**：Issue 文件声明无前置；实施上先落地 effective current/pending 读取和失败测试，才能给 R03 与所有入口提供同一事实边界。
- **先写失败测试（预估执行顺序）**：三个公开入口分别覆盖 current+pending 的错误节点类型/inode/operation ID/kind/transition、pending JSON/v4/metadata 错误、缺失/非法 R forward；覆盖 originals/staged 符号链接、目录、未知成员、身份变化和外部 sentinel；覆盖 discovery 后 writer 改变 journal/material、lock 竞争、`rolled_back` 写入前零副作用。
- **边界与验收（确认）**：非法 pending 在第一项发布、写入、handoff、清理或解锁前失败，current/pending/copies/package/目录/关系/lock bytes 和节点集合不变；`operation.recovery_required` 只用于竞争；合法 transition 保留既有 durability；三个入口重复执行不扩大副作用。
- **绝不可自行决定**：不得把 pending 当不存在、从 T reverse claim 补 R forward、把 restore 当普通 allocating、以旧快照授权删除、改变错误优先级或发明新的退出码/错误码；已持有 lock 的失败是否保留必须遵守确认现场，不得为“清理锁”而报告成功。

#### R02 / Issue02：空 allocation residue（已实施，复审通过）

- **目标与前置（确认）**：合法 operation-id 空目录仅在 matching lock 内、no-follow 身份认证后两次重读仍为空才 `rmdir`，父目录 `fsync` 后释放 lock；非空无 journal、未知成员、pending、符号链接、竞争和身份变化必须保留现场。前置原为 R01。
- **先写失败测试（已有证据）**：round-3 记录三个入口的 14 项窄集成测试 `14 passed`，覆盖无锁/已有 lock、成员新增、pending、未知成员/符号链接、身份变化、竞争、父目录同步、lock release、重复恢复和 checker 一致性。后续若补深度，建议只增加独立 `fsync`/`release` 故障注入，不把它改写成另一项行为或以此否定已通过复审。
- **验收证据（确认）**：round-3 结论为通过；共享 resolver 已丢弃 discovery 快照，只有两次空重读才删除，成员变化/删除失败/父目录同步失败均失败关闭。Issue status/checklist 保持原样，不在本计划中更新。
- **绝不可自行决定**：不能把空目录永久 `RETAIN`，不能把非空无 journal 递归当 residue，不能用旧快照删除新 writer 材料，也不能将本项的复审通过扩展为 Issue01/03 已通过。

#### R03 / Issue03：锁内 R/T 全图分类、restore owner 与 checker 一致性

- **目标（确认）**：按 2.2 的四层优先级读取全体 effective journals；R 作为唯一 restore owner 处理合法 PRECLAIM、POST_UNCLAIM_CLEANUP 和合法双向关系，其他图/phase 状态失败关闭。checker、recovery、lifecycle 共用 current/pending/effective 只读 index，checker 不消费 pending。
- **前置（确认/预估）**：Issue 文件声明依赖 R01；实现上需先有 R01 的 pending preflight，并把 round-4 的 R03-1 至 R03-5 作为额外硬门：checker 不得忽略 pending，不能吞 unrelated `DraftError`，必须统一机器码，测试必须精确断言 `.code`，FD 矩阵必须闭合。
- **先写失败测试**：先写纯分类器决策表（raw/type、symlink、R forward、T 缺失/kind/claim、PRECLAIM、POST_UNCLAIM_CLEANUP、双向、其他单向、错配、MULTIPLE、T-only、terminal dangling、非法 phase）；再写 checker/recovery 同码的 pending-only、双方 pending、current+pending 非法 transition/identity 和非法 forward 集成测试；最后写 direct recover/lifecycle 不走通用 rollback、锁前/锁内 relation read 错误零副作用测试。
- **边界与验收（确认）**：全体 journals 的关系图在 matching lock 内当前重读后分类；非法关系不写 `rolled_back`、不 handoff、不清理、不解锁；合法关系 owner 正确，重复恢复幂等；checker 与 recovery 对同一事实返回同一机器码。第 4 轮列出的五个发现全部有行为证据后才可复审通过。
- **绝不可自行决定**：不能只看 R forward 或当前候选，不能从 T 反推 R，不能把关系未知当无 owner，不能让 `tolerate_unrelated_errors` 继续吞任意 `DraftError`，不能以最终错误文字替代现场和 `.code` 断言，也不能把本 Issue 文件中已有的实现性勾选/评论当作 round-4 复审通过。

#### R04 / Issue04：Trash 生命周期释放旧锁后的重取

- **目标（确认）**：释放遗留 lock 后丢弃旧 journal、operation ID、package、关系和时间快照；无竞争时重新取得 matching lock 并重读，发生新 writer 竞争时返回稳定 recovery-required 分类；已提交 handoff/purge 只按当前 journal 授权清理，绝不重复 handoff。
- **前置（确认）**：R03；实现上先通过 R03 的 owner/effective index，才可把 lifecycle 的释放/重取交给同一 resolver。
- **先写失败测试**：构造释放后无竞争、释放后新 writer 抢锁、重取后 journal/package/关系/时间变化、重复 lifecycle 调用、handoff 重试和已提交 cleanup；spy/快照断言旧材料不被消费。
- **边界与验收（确认）**：任何副作用都由新 matching lock 和当前事实授权；竞争不改变新 writer 的 journal、材料、目录和关系；合法 waiting/terminal/error 保持既有分类，重复调用收敛到同一结果。
- **绝不可自行决定**：不能继续使用旧快照，不能为方便而释放后直接清理或再次 handoff，不能把 waiting/terminal/error 合并，也不能自行改变 lifecycle 的时间语义。

#### R05 / Issue05：三入口刷新竞态、分类和 FD 资源验收

- **目标（确认）**：以三个公开 seam 验收扫描后竞争、锁内重读、完整错误分类、重复恢复和 FD exactly-once；真实 FS/lock/journal durability/handoff/close 故障属于 integration，纯分类属于 unit。
- **前置（确认）**：Issue 文件声明依赖 R01、R03；推荐在 R04 后做最终跨入口验证，但不改变 issue 声明。
- **先写失败测试**：建立 owner × 故障 × 主异常矩阵：recovery leaf 的 fstat/read/close，operation directory 的 fstat/close，context 的 fstat/close，`_read_name_bytes` read/close；每行覆盖无主体异常、已有主体 `DraftError`、多个 close 失败。另测 lock 竞争、pending、普通 allocating、空/非空目录、R/T 全表、waiting/terminal/error、handoff 已提交和三入口重复执行。
- **边界与验收（确认）**：无主体 close 错误成为稳定 `DraftError`，主体错误保持 code；每个 descriptor 最多关闭一次且无 FD leak/double-close；竞争只返回 `operation.recovery_required`；损坏/关系/FD 错误均零副作用、保留现场。
- **绝不可自行决定**：不能吞 close/fstat/read、不能用私有 helper 调用次数替代公开行为、不能让 close 错误覆盖主异常、不能把网络/平台/环境未运行写成通过，也不能新增未登记错误码。

### 4.2 链接索引耐久维护

#### L01 / Issue01：领域状态与依赖端口

- **目标（确认）**：建立显式 authority（absent/published/rebuild-required/inconsistent）、candidate（unchanged/publish/rebuild/unable）和规范 revision 类型；上层只依赖领域端口，基础设施适配器单向实现端口；独立 maintenance、业务 operation、notification、check、query 各有应用边界。
- **前置**：无。先写 revision 合法/非法、任意精度排序、authority/candidate 穷举、预期领域失败与未知异常分界、端口不访问 FS 的 unit 红测。
- **边界与验收**：不以 `None`、空集合、布尔值折叠现实状态；unit 不启动子进程、不读 FS；通过后其余 index issue 只能依赖端口语义。
- **绝不可自行决定**：不能把所有入口塞进带 mode 参数的万能维护函数，不能让领域层导入具体 FS/journal/CLI，也不能擅自新增权威来源或错误码。

#### L02 / Issue02：journal v4 状态模型

- **目标与前置（确认）**：在 L01 之后由一个权威模型定义 v4、`link_index_*` 字段矩阵、`link_index_updating/published/rebuild_required`、`link-index.update` kind、kind owner、后继边和 rollback 禁止边。
- **先写失败测试**：v3/未知版本、raw/v4 shape/type、metadata 非 string、字段缺失/null、超大 revision、各 kind 合法 phase、非法转换、索引终态字段保留、rollback 携带索引字段。
- **边界与验收**：解析、构造、不可变转换、写入、恢复共享同一模型；禁止字段必须缺失；`link_index_updating` 后不 rollback，published/rebuild-required 只能进入原业务成功终态。
- **绝不可自行决定**：不能迁移/双读旧版本，不能用 null 填禁止字段，不能复制一套 phase/kind 清单或让 link-index phase 覆盖协调恢复的 raw/type 优先级。

#### L03 / Issue03：纯链接索引引擎

- **目标与前置（确认）**：L01 后实现不依赖 FS/journal 的 schema v2、确定序列化、完整 files/outbound/backlinks/directories 和增量计算。
- **先写失败测试**：未知字段、绝对路径、inode/size/mtime、非法 sha/path/span/resource/目录类型、重复项、backlinks 缺失/多余/部分反向边、文件新增/删除/摘要变化、相同 bytes 换路径的相对资源身份、非 Markdown 与目录节点变化、读取/解析统计值。
- **边界与验收**：同一逻辑内容 bytes 完全相同；files 与 backlinks 精确双向一致；路径和 sha 未变才复用；新路径必须重解相对身份；schema 不保存不完整状态或绝对根路径。
- **绝不可自行决定**：不能把摘要相同当作路径身份相同，不能把非 Markdown 正文加入 files，不能用生产引擎生成测试预期。

#### L04 / Issue04：严格 Draft 根事实来源

- **目标与前置（确认）**：L03 后遍历完整根并排除整个 `.draft`，提供目录成员/节点类型、普通 `.md` 正文和 SHA-256；不跟随符号链接。
- **先写失败测试**：managed/unmanaged Markdown、非 Markdown、宽目录、符号链接、不可读/非 UTF-8/链接提取失败、扫描期间文件/目录变化；确认每次扫描仍读所有覆盖 Markdown，只有 path+sha 匹配才复用解析事实。
- **边界与验收**：任一不可判定 Markdown 不产生完整候选；mtime/size/inode 不能作 fresh 证明；扫描不稳定不得发布混合时点索引或无限重试。
- **绝不可自行决定**：不能扫描 `.draft` 内部、跟随 symlink、把不可读文件解释为无链接，不能引入 watcher/无限重试。

#### L05 / Issue05：索引制品与 rebuild flag 耐久存储

- **目标与前置（确认）**：L02、L03 后实现 operation 专属 staging、create-only/幂等续写、文件与目录同步、原子发布和零字节 flag。
- **先写失败测试**：staging write/fsync、staging 父目录 sync、目标 replace、目标父目录 sync、flag create/delete/sync 各故障；staging/目标/flag/祖先是 symlink、目录或异常节点；跨首次发布、rebuild-required 发布、published 替换的目标矩阵。
- **边界与验收**：staging 只属于当前 operation，摘要针对最终 bytes；symlink 为 `path.symlink_escape`，目录/其他异常节点为 `operation.inconsistent_state`；flag 不是 authority，清理不使用 glob。
- **绝不可自行决定**：不能覆盖/跟随/递归删除异常节点，不能把 flag 内容当第二份状态，不能删除其他 operation 或未知文件，不能自行改变耐久顺序。

#### L06 / Issue06：authority repository

- **目标与前置（确认）**：L02、L03、L05 后只读取规范 operation journal，收集合法索引结果或经索引阶段到达的终态，按数值最大 revision 选择 authority 并验证 published 制品。
- **先写失败测试**：absent、published、rebuild-required、超大 revision、重复/冲突 revision、制品缺失/sha/schema/revision/backlinks 不符、残留/缺失 flag 的所有组合。
- **边界与验收**：published 必须普通文件且制品完整一致；rebuild-required 不提供可信增量基线；flag 只快速阻断，不能覆盖 journal；暂时接受 O(J) 扫描。
- **绝不可自行决定**：不能以字符串字典序、flag 或 sidecar 取代 journal authority，不能迁移旧 schema，不能在冲突时选一个“看起来最新”的结果。

#### L07 / Issue07：无副作用候选准备

- **目标与前置（确认）**：L04、L06 后组合 authority、严格事实源和纯引擎，产生带 absent/revision 观察令牌的候选；整个准备阶段无 lock、operation、journal、索引、staging、flag 或删除副作用。
- **先写失败测试**：absent 全根构建、published 严格验证后增量、rebuild-required 全根构建、事实无变化返回 unchanged 且不分配 revision、不可读/不稳定返回 rebuild-required、authority/制品矛盾返回 inconsistent；前后 FS 状态字节不变。
- **边界与验收**：正文仍严格读取并比较 sha；复用只发生在 path+sha 都验证后；候选完整、确定且带提交时复核令牌。
- **绝不可自行决定**：不能在准备阶段取得写锁或清理对象，不能把无法证明的事实当 unchanged，不能把旧索引矛盾降级为 rebuild-required 而掩盖内部状态。

#### L08 / Issue08：已持锁索引前向提交

- **目标与前置（确认）**：L07 后仅为已持 lock/journal 的调用方，将候选按固定耐久顺序收敛为 published 或 rebuild-required；不写原业务终态、不释放调用方 lock。
- **先写失败测试**：updating 前后、staging 写/sync、replace、published journal、flag create/delete、journal durability、异常内部节点、发布结果未知、updating 后 rollback 禁止、warning 去重/不泄露内部信息。
- **边界与验收**：发布 sha 等于最终制品 bytes；失败但 rebuild-required 已耐久记录时业务结果保留；journal 不能耐久时为 `operation.durability_failed` 并保留现场；只清理本 operation。
- **绝不可自行决定**：不能猜测发布成功、重复增量发布、回滚已验证业务 mutation、释放锁或写 completed/trash_retained，也不能新增 warning 文案/operation ID 暴露。

#### L09 / Issue09：链接索引 phase 恢复解释器

- **目标与前置（确认）**：L08 后且 R01/R03 已确定 matching owner/effective 分类后，机械前向恢复 updating/published/rebuild-required；不重新实现 R/T 或 `recovery_required` 分类。
- **先写失败测试**：updating 中断转同 revision rebuild-required；published 目标 revision/sha 一致补原终态、不一致保留；rebuild-required 补 flag；各 phase 一次/重复恢复；staging 精确清理；maintenance allocating 无索引副作用时 rollback。
- **边界与验收**：recover 不创建新 operation/revision；已成功终态的已知 lock/staging 清理幂等；所有错误依协调恢复优先级失败关闭。
- **绝不可自行决定**：不能从 staging/目标外观猜 published，不能用 glob/旧快照清理，不能绕过 matching owner，不能复制 R/T 状态表。

#### L10 / Issue10：独立 `link-index.update` 协调器

- **目标与前置（确认）**：L09 后为 notification、check、query、adopt 预检查提供独立维护用例；先扫描/准备，根无变化不分配 operation/revision。
- **先写失败测试**：首次发布、无变化、单文件增量、rebuild-required 全根重建、已有 operation lock、authority revision 竞争、扫描中根再次变化、各 phase 中断恢复、结果/warning 投影。
- **边界与验收**：取得 lock 后重读 authority token；推进则放弃旧候选、不记 rebuild-required；未推进才创建 `link-index.update` allocating，提交后 completed 并精确清理。
- **绝不可自行决定**：不能让已有 writer 变化时继续读根/创建 operation，不能以入口 mode 参数吞并不同应用边界，不能把维护 warning/operation ID泄露给 notification。

#### L11 / Issue11：业务 operation 接入索引阶段

- **目标与前置（确认）**：L09 后在共同 operation 协调边界中，以实际 Markdown/目录 mutation 判定索引阶段；业务验证后、原成功终态前完成索引结果。
- **先写失败测试**：create/move/promote/collapse/status/relation add/remove/generate-conclusions 及未列举但产生同类事实的 kind；每类 mutation 和 no-op；空目录深到浅清理；索引降级成功、journal durability failure、dry-run。
- **边界与验收**：业务 after-state 不因缓存故障回滚；rebuild-required 耐久后退出码 0 和 warning；journal durability failure 退出码 3/保留 lock；move backlink 不完整时使用完整事实；不释放旧 lock 改用独立 maintenance。
- **绝不可自行决定**：不能维护命令白名单、不能在命令后加专用刷新钩子、不能把 no-op 产生 revision、不能以索引证明替代完整事实闭包。

#### L12 / Issue12：trash/restore 索引生命周期

- **目标与前置（确认）**：L11、R03、R04 后把索引阶段嵌入既有 Trash/restore owner、双 journal 和 lock 生命周期；不改变页面回收终态。
- **先写失败测试**：trash 验证后索引再到 `trash_retained`；restore 验证后索引再到 completed 再做既有解关联；updating/published/rebuild-required 中断恢复；双 journal 清理中断、重复 recovery、索引 warning 与回收 warning 分离、handoff 计数。
- **边界与验收**：索引 phase 恢复原 operation/kind/revision；终态后可按既有要求继续持锁；重复 recovery 中业务文件、包、两个 journal、索引、flag、lock、staging 一致。
- **绝不可自行决定**：不能复制/改写 R/T 分类，不能因缓存失败改变 handoff/restore 语义，不能释放锁后用旧快照继续清理，不能重复外部 handoff。

#### L13 / Issue13：外部文件变更 notification

- **目标与前置（确认）**：L10 后提供 `draft notify files-changed --root ROOT` 薄适配器，candidate/registered 都可用；它声明变化，不是变化清单或事件队列。
- **先写失败测试**：参数多余路径/type/rename/stdin/`--dry-run` 被拒绝；有效根首次/增量/无变化；已有 operation lock；每类已分类消费者失败固定退出 0 且无 warning；重复无变化不写 journal/revision；JSON/普通输出信封。
- **边界与验收**：不推进回收、不自动 recovery、不建事件记录；已分类维护结果被吸收，断言/类型错误/未知异常不吞；输出不含 warning/revision/operation/path/count。
- **绝不可自行决定**：不能接收路径映射、不能把 notification 当索引错误修复命令、不能为成功伪造无效根确认或吞未预期缺陷。

#### L14 / Issue14：check/adopt 机会式维护

- **目标与前置（确认）**：L10 后让 check 复用严格扫描事实尝试维护，adopt 在 marker operation 前完成独立 maintenance；缓存健康与页面合法性保持分离。
- **先写失败测试**：absent/fresh/stale/rebuild-required/flag I/O/制品矛盾/journal 损坏/recovery pending 决策表；check 维护成功/降级仍返回完整页面事实；journal durability error；adopt 对降级放行、对内部矛盾阻止；init 不建首次索引。
- **边界与验收**：正文复用必须经过重新读取 sha；check issue、adopt 决策、journal/revision/index/flag/lock 同时断言；两个 operation 不共享含糊所有权。
- **绝不可自行决定**：不能以索引性能降级阻止 adopt，不能把 checker 写入成功当作完整检查成功，不能让 adopt 与维护共用不清晰 lock/journal 生命周期。

#### L15 / Issue15：backlink 查询消费与机会式维护

- **目标与前置（确认）**：L10 后让 `query backlinks`/`query page` 通过严格验证的统一链接事实消费索引；其他 query 的副作用范围不变。
- **先写失败测试**：fresh/absent/stale/rebuild-required 下 backlink 与完整事实逐项一致；authority/schema/revision/sha/反向表/全 Markdown sha/目录事实任一不满足时走完整扫描；query page count 与 backlinks 一致；maintenance warning/durability failure；query links、relations、status、recoverable 前后内部状态不变。
- **边界与验收**：索引不可用不返回旧/近似结果，不要求调用方 notification；保留 candidate/registered、分页、count-only 和机器信封语义；调用路由以真实边界测试验证，不用源码字符串搜索。
- **绝不可自行决定**：不能以旧索引证明“无 backlink”，不能让窄 `query links` 触发全根维护，不能改变既有 query 限制和退出协议。

#### L16 / Issue16：全部入口与 namespace 验收

- **目标与前置（确认）**：L12-L15 后从系统边界验证十类 mutation、维护入口、不消费索引的命令和 Draft 内部命名空间共同遵守协议。
- **先写失败测试**：十类 mutation 的正常/无 mutation、warning/退出码；notification/write/check/query/adopt 路由；query links/无关 query/dry-run/init/recover 无禁止副作用；marker/lock/operations/trash/index/flag、未知临时文件、错误节点、symlink 的 namespace 矩阵。
- **边界与验收**：所有业务入口只经过共同 operation 收敛；unknown index-like object 报稳定内部错误，symlink 按 `path.symlink_escape`；warning code/severity/order/dedup 一致，notification 不投影；测试入口清单独立于生产 mutation 集合。
- **绝不可自行决定**：不能追加命令专用刷新、放宽内部 namespace、把 symlink 归为普通 inconsistent、用生产集合生成验收预期或改变 warning 机器协议。

#### L17 / Issue17：耐久、并发、freshness 全矩阵

- **目标与前置（确认）**：L16 后在真实 FS 和受协调竞争中闭合 normal/failure/retry/recovery/cleanup/next fresh read 的证据链。
- **先写失败测试**：staging/file/dir/journal/flag 每个耐久点故障；updating 尚未 staging、目标已替换但 published 未写、published 已写但原终态未写；两个维护者 revision race；扫描期间根变化；正文 bytes 变化但 size/inode/mtime 相同；新增/删除/重命名/节点变化/不可读/非 UTF-8/链接提取失败/异常节点。
- **边界与验收**：每项同时记录业务文件摘要、journal phase/fields、revision、制品 sha、flag、staging、lock、退出码/warning 和下一次命令是否接受；一次/重复 recovery 一致；预期 backlink 由独立事实计算。
- **绝不可自行决定**：不能把局部测试绿当耐久闭合，不能设置跨机器绝对 SLA，不能用探针代替 SHA，不得无限重试混合时点快照，不得回滚已验证业务 mutation。

#### L18 / Issue18：基准、文档与最终门禁

- **目标与前置（确认）**：L17 后交付固定种子、100/1000/5000 Markdown 和宽目录基准、当前使用文档及完整质量门禁报告。
- **先写失败测试/基准**：首次构建、fresh backlink/check、无变化 notification、单文件/1% 变化、新增/删除/重命名、目录节点变化、rebuild-required 恢复、check 维护、journal 数量增长；记录读取文件/字节、解析数、wall time、峰值内存或可复现近似、索引大小、journal 写次数/字节。
- **边界与验收**：fresh/无变化仍读全部 Markdown，但未变摘要不重复解析；无变化 notification journal 写入为零；大基准不进入默认门禁，只保留小规模 schema/count smoke；文档不把 notification 写成恢复步骤，不暴露 operation/revision/path。
- **绝不可自行决定**：不能添加 watcher/事件队列、路径 notification、mtime fresh 证明、多 schema 迁移、随机临时索引或跨机器绝对耗时 SLA；网络/平台未运行必须明确列为未验证。

## 5. 当前实际状态、证据与待复审项

### 5.1 协调恢复

- 第 1 轮（3 个 P1、1 个 P2）和第 2 轮（Issue01/R03/R02/R05 仍未闭合）结论均为未通过；第 1 轮记录顺序运行 `test_operations.py` 与 `test_trash_lifecycle.py` 为 `221 passed`，并明确并行 I/O 卡滞不是可靠测试证据，不能据此宣称闭合。
- Issue02 已实施，且第 3 轮针对三个公开入口以 14 项窄集成测试记录 `14 passed`，复审结论为通过。记录的 resolver 已满足 no-follow、FD 身份、两次成员重读、`rmdir`、父目录 `fsync`、再 release lock 和 checker 一致性。Issue02 文件当前仍保持其原 `ready-for-human` 状态；本计划不改 status/checklist。
- Issue01 仍未关闭。第 2 轮明确 pending preflight 仍可能在 current/pending 的节点类型、identity、kind、transition 或 R forward 完整验证前发布 T pending；第 4 轮要求把它与 effective journal index、锁前/锁内 lifecycle 停止语义一起闭合。Issue01 文件仍是 `ready-for-agent`，其部分勾选不能替代这些复审证据。
- Issue03 仍未关闭。Issue03 文件的评论记录了已有实现与 `1997` 个 runtime 主体测试、唯一网络边界用例重试 `1 passed`，并记录 Ruff/Pyright/`git diff --check`；但第 4 轮仍以 R03 未通过为结论，列出 checker 忽略 pending、`tolerate_unrelated_errors` 吞任意错误、`operation.plan_invalid` 无单一来源、unit regex 不精确和 FD 矩阵不完整五项阻塞。应把评论当作历史执行记录，不当作当前闭合证明。Issue03 文件当前仍是 `ready-for-human`，本计划不改 status/checklist。
- Issue04 和 Issue05 的 issue 文件仍是 `ready-for-agent`，未有本计划可据以宣称完成的复审证据。Issue04 的重取锁和旧快照边界、Issue05 的三入口/FD 矩阵仍是后续门禁。

### 5.2 链接索引维护

- L01-L18 的 issue 文件当前均为 `ready-for-agent`；没有 `draft-durable-link-index-maintenance` 的独立复审记录，因此不能把工作树修改或 checklist 勾选推断为完成。
- 当前工作树已有进行中改动，证据包括 durable spec、L02/L05/L09/L12/L16 issue 文件，以及 runtime 的 `checker.py`、`diagnostic_codes.py`、`observations.py`、`operations.py`、`structure.py`、`trash.py`、`trash_lifecycle.py` 和多组 unit/integration 测试。`git diff --stat` 显示这些是已有用户/执行者改动；它们的存在只证明进行中，不能证明 L01-L18 任一项满足全部行为契约。
- 当前工作树本身是 dirty，另有 `.scratch/draft-coordinated-recovery-ownership/` 未跟踪内容。本计划只新增本文件，不回退、覆盖或整理这些既有改动；最终验收必须区分本次计划文件与其他工作树变化。
- 本次落盘没有运行生产测试、构建或静态检查，也没有伪造测试结果。上文所有数字均明确归属于 review/issue 记录；后续执行必须重新按阶段记录命令、结果、环境限制和未验证前提。

## 6. 最终门禁与完成审计清单

### 6.1 规格和状态门

- [ ] 逐条核对两份 spec、23 个 issue、4 轮复审；确认每个确认要求都有一个可观察验收，不把实现预估写成协议。
- [ ] R/T 四层优先级、PRECLAIM、POST_UNCLAIM_CLEANUP、合法双向、T-only/MULTIPLE/phase、`recovery_required` 分类表有纯 unit 覆盖，未定义组合已记录并暂停决策。
- [ ] Issue02 的 round-3 通过证据保留；Issue01/03 在新的复审通过前仍明确为未关闭；任何 status/checklist 更新由其责任流程完成，不由本计划完成。
- [ ] 未把测试数量、局部静态检查、工作树 diff 或 issue comment 当作跨入口完成证明；未运行的平台、网络或环境受限测试单独列出。

### 6.2 协调恢复门

- [ ] 三个公开 seam 的未持锁 discovery 全部无副作用；matching lock 竞争丢弃旧快照并只返回 `operation.recovery_required`。
- [ ] current/pending/effective journal index 只有一个只读责任边界；checker/recovery/lifecycle 对 pending-only、双向 pending、非法 forward、raw/type 错误返回同一机器码，checker 不发布 pending。
- [ ] 所有 pending/material/journal 写入前完成 no-follow、类型、身份、成员、transition、R/T owner 验证；非法路径 bytes、inode、节点集合、lock、package 和 handoff 计数不变。
- [ ] R 是唯一 restore owner；合法窗口才有授权动作，缺失 T/非法 claim/错配/MULTIPLE/未批准单向/非法 phase 不回滚、不清理、不 handoff、不解锁。
- [ ] Issue02 的空目录只在 matching lock 两次重读为空后删除、父目录同步后释放 lock；非空 residue 不误删。
- [ ] lifecycle 释放 lock 后只使用新 lock/current facts；锁前关系错误不取得锁，锁内关系/FD 错误保留锁和现场，不继续 pending/journal/handoff/package 清理。
- [ ] leaf/opdir/context/读取边界的 owner × fstat/read/close × 主异常矩阵全部通过；无 FD leak/double-close，主体 code 不被 close 错误覆盖，无主体 close 错误不返回成功。

### 6.3 链接索引门

- [ ] L01-L06 的 authority、v4 state、schema v2、strict fact、storage、flag 和 repository 单元/集成契约闭合；没有第二份 revision/flag authority、schema 迁移或 path/inode fresh 捷径。
- [ ] L07/L08/L09/L10 的 candidate token、lock 内 authority 复核、固定耐久顺序、rebuild-required、恢复 phase 和 idempotency 证据闭合；无变化不创建 operation/revision。
- [ ] L11/L12 覆盖所有实际 mutation、trash/restore 双 journal、原业务终态、索引降级和 recovery；缓存故障不回滚业务结果、不重复 handoff。
- [ ] L13/L14/L15 的 notification、check/adopt、backlink/page query 输出、warning、退出码和副作用路由与 spec 一致；窄 query 和无关 query 不触发全根维护。
- [ ] L16 namespace 和入口清单由独立行为预期验收；内部未知节点、symlink、目录和 staging 所有权分类准确。
- [ ] L17 每个 staging/replace/journal/flag/fsync/目录同步故障和三个中断窗口均有业务 before/after、journal、index、flag、lock、staging、退出码、warning 和下一次 fresh 结果快照。
- [ ] L18 基准和文档可复现，fresh 读全文件但只重解析变化项；大规模基准不进入默认门禁，使用说明不泄露内部 operation/revision/path，也不把 notification 当 recovery。

### 6.4 测试、静态和交付门

- [ ] 先运行纯 unit：revision、state model、R/T 分类、effective index、schema/反向表、candidate/authority/warning/routing、FD error aggregator；unit 不访问真实 FS/网络/子进程。
- [ ] 再顺序运行真实 integration：checker、operations、trash、trash lifecycle、三入口 recovery、lock 竞争、pending、journal durability、真实 FS/FD/handoff；不得用并发 I/O 卡滞替代可诊断证据。
- [ ] 分别运行 runtime unit、runtime integration、adapter unit、adapter integration、格式检查、Ruff、Pyright、adapter consistency；网络测试未运行时记录为未验证，不写成通过。
- [ ] 运行 L18 要求的小规模 schema/count smoke 和独立性能基准；记录命令、结果、测试选择、环境限制、未验证前提和提交/工作树清单。
- [ ] 最后执行 `git diff --check`，确认本计划和所有待审改动没有 whitespace error；再核对 issue status/checklist 没有被计划落盘意外改变。

## 7. 本计划的边界

本文件不实施代码、不关闭 issue、不更新 checklist、不声明未复审的 Issue01/03 或 L01-L18 已完成，也不替用户决定规格未定义的状态。它只把当前已确认契约、依赖、红测顺序、证据状态和最终门禁集中记录，供后续执行者逐项落实和复审。
