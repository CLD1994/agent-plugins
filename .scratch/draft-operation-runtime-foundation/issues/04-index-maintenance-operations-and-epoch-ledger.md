# 04 索引 ledger、同次扫描与 ExternalFilesChanged

Status: ready-for-agent

Blocked by: 01, 02, 03

## 确认合同

实施 spec 第 6--7、10 节。ledger/head 是权威、cache 仅物化；每次 `maintain(ExternalFilesChanged)` 是独立 index-maintenance operation。无运行期根锁记录时，`maintain` 和 Page/Backlinks/Check 的机会式 index maintenance 仍只在实际维护子阶段先取得 stable OS root lock，锁内重读确认仍无记录后才可发布自己的记录；记录缺失不能创建第二把锁。Page/Backlinks/Check 的稳定 preflight/final projection 本身不取得锁，机会维护遇 live lock 按既有 busy 规则跳过并带 warning，不改变 projection。每个 index transition 必须认证变更前 head 版本与本次唯一授权的下一 head 版本，完成 authority 父目录 fsync 和 matching committed progress 后才证明 content settled。分类 cache 降级 warning，ledger/authority/durability 矛盾 fatal。运行期根锁记录无 live owner、marker-ready cleanup remnants 或 corrupt 时由 admission 阻止 read/Check projection 与本任务维护，不能自行删除或恢复。

## 建议实现与顺序

1. 先从 spec 第 7 节的 head/cache transition、需要恢复的持久现场 discovery 与 authority 变更恢复表写 fold、base-head、token 和 cache-repair 失败单元测试。完成条件：每个变更前/下一 generation、intent-only、intent+epoch、replace-return、parent-fsync、head 可见 target、cache 分区都有独立期望，cache 不被当作 authority；证据为参数化测试输出。
2. 写 Windows/macOS/Linux integration 与故障矩阵：首次、无变化、单文件/目录改变、intent/epoch/head/cache stage、replace 返回、authority 父目录 fsync、cleanup、运行期根锁记录 publish/unlink/最终目录 fsync 每一中断点及同次扫描结果；覆盖 Page/Backlinks/Check 稳定 projection 无锁、机会维护仅在子阶段 acquire+publish、live busy skip/warning，以及有效记录无 live owner、marker-ready cleanup remnants/corrupt 阻止 read/Check projection。完成条件：仅 explicit `recover()` 能发现 intent-only owner；故障后仅 matching intent 可前向；head 在重新认证、父目录 durability 和 committed progress 前不 settled；head durable 而 cache 缺失只降级；read/Check 不因纯 projection 发布运行期根锁记录；证据为按平台故障矩阵与 ledger/head 摘要。
3. 实现专用 manifest/progress、cache transaction、epoch append 与 content settled release。完成条件：每个 epoch 有 source operation，head generation 只能由认证的变更前 head 版本推进，release 读取 committed head；证据为 record dump 与接口集成。
4. 提供 full scan 读取字节、解析数与无变化 no-op 证据。完成条件：Page/Backlinks/Check 的 projection 与 index input 使用同一最终 scan，notification 只路由 maintain 且无变化不创建 epoch；证据为 scan token 日志和基准报告。

## 禁止捷径与完成标准

禁止全局 rebuild flag、锁外 epoch/cache 发布、以 cache 取代 ledger、以旧 token 覆盖 head、在有效记录但无 live owner 时绕过 recovery-required。完成时每个 epoch 有 source operation，cache 缺失不改变权威结果，notification 的 delivered 不依赖是否产生 epoch；三平台每个 durable fault 都有收敛或 fatal 证据。
