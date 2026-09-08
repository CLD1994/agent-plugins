# 12: 跨入口整合与故障验收

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/draft/structure.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
  - plugins/draft/docs/spec/draft/conclusions.md
  - plugins/draft/docs/spec/draft/trash-and-restore.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 把已通过评审的请求、读取、锁、恢复、YAML、初始化和首条普通操作切片组合成一条可审计的端到端链路，并证明不同入口没有第二套状态、错误或清理协议。

下列各前置票据的交付与相应评审均通过后，开始最终整合：

Blocked by:
  - [01: 公共请求在任何副作用前被拒绝](01-request-validation.md)
  - [02: 写入和恢复共享根目录排他锁](02-root-lock-admission.md)
  - [03: 恢复与 Trash 清理只处理已证明归属的现场](03-recovery-cleanup-ownership.md)
  - [04: 只读、预演和通知在并发写入下保持安全](04-read-dryrun-maintenance.md)
  - [05: 页面读取正确区分 YAML 头部和正文](05-yaml-page-read.md)
  - [06: 导航、查询和结论读取使用同一页面事实](06-yaml-navigation-and-conclusions.md)
  - [07: 维护过程使用一致的根目录状态快照](07-marker-consistent-maintenance.md)
  - [08: YAML 元数据变更先形成可验证预演](08-yaml-metadata-plan-dryrun.md)
  - [09: 首个经确认的 YAML 写入操作](09-first-yaml-write.md)
  - [10: 初始化及初始化中断恢复闭环](10-initialization-recovery.md)
  - [11: 首条普通操作的提交与恢复闭环](11-first-operation-closure.md)

- [ ] 沿“请求 → 根目录/标记认证 → 页面事实 → 纯计划 → 锁内重验 → 持久提交 → 派生结果/授权清理 → 释放 → 公开结果”逐段追踪正常、错误、重试和恢复。
- [ ] 核心与适配层分别通过单元、集成、静态和分发检查；真实文件系统、进程竞争、进程终止、耐久失败和重复恢复均有证据。
- [ ] 程序错误码、退出状态、结果字段和人类输出均回到唯一的设计规格页面；不泄露内部路径、记录或临时实现细节。
- [ ] 两个历史归档只留下可追溯的窄片段来源和重新评审结果；没有整支归档或混乱提交进入最终候选。
- [ ] 独立 Spec/Standards 评审通过后，才提出合入当前 `main` 的建议；本票通过不等于未覆盖的其他业务入口自动完成。

**停止条件:** 任一跨票据接缝缺少行为负责人、失败分类或恢复消费者时停止整合并报告。
