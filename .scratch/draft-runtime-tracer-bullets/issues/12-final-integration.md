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

## 前置交付与公开组合

05 提供完整页面读取事实与直接诊断，06 提供跨消费者一致性，08 提供纯元数据计划；三票分别按自身能力或内部集成验收关闭，不以本票公开组合验收为前置。本票消费这些交付和其余前置的运行时、端到端证据，验证唯一公开路径。

公开 `read`／Check／预演使用统一请求与错误边界，并遵守现行运行时效果。预演的无已发现记录、已发现记录两个分区均须有证据：前者不探测根锁，后者最多一次非阻塞探测，两者始终零写。公开格式接入同时验证 [YAML 总任务](../../draft-page-frontmatter/issues/01-yaml-page-io.md#格式整合与关闭)的原子整合，所有仍可达的受管理页面读写及直接消费者采用同一协议，并逐入口验证写后和恢复后重读。

初始化内容与首条普通操作只按各自已确认决定验收；一条操作通过不能代替其他入口的交付。

| 私有计划或准入结果 | 核心公开结果 | 命令行接口验收 |
| --- | --- | --- |
| 合法预演可形成计划 | 既有 `OperationOutcome`，`mode: dry_run`，结果按是否变化为 `would_apply` 或 `would_not_change` | 使用同一结果和已登记成功退出状态；只输出结果合同允许的字段。 |
| 领域或运行时阻止 | 对应既有 `ErrorEnvelope` | `code`、允许的 `details`、输出通道及退出状态一致。 |
| 读取或 Check | 各自既有 `QueryOutcome` 或错误联合 | 按查询与错误设计投影，不借用私有操作计划添加字段。 |

用同一私有计划事实分别驱动核心与命令行适配，验证已登记结果一致、预演零写和字段边界；私有计划、变更前后事实及内部阻止项不直接成为公开协议。

- [ ] 沿“请求 → 根目录/标记认证 → 页面事实 → 纯计划 → 锁内重验 → 持久提交 → 派生结果/授权清理 → 释放 → 公开结果”逐段追踪正常、错误、重试和恢复。
- [ ] 核心与适配层分别通过单元、集成、静态和分发检查；真实文件系统、进程竞争、进程终止、耐久失败和重复恢复均有证据。
- [ ] 程序错误码、退出状态、结果字段和人类输出均回到唯一的设计规格页面；不泄露内部路径、记录或临时实现细节。
- [ ] 每组复用代码和测试都有可追溯的来源、采用范围、具体修补及当前验收和重新评审结果；最终候选只包含各票已采用的交付，符合[复用安排](../spec.md#基线与历史归档)。
- [ ] 独立 Spec/Standards 评审通过后，才提出合入当前 `main` 的建议；本票通过不等于未覆盖的其他业务入口自动完成。

**停止条件:** 任一跨票据接缝缺少行为负责人、失败分类或恢复消费者时停止整合并报告。
