# 11: 把索引收敛接入通用业务 operation 协调器

**What to build:** 让所有改变根内 Markdown 或索引覆盖目录事实的业务 operation，在业务结果验证后、原成功终态前，通过共同协调边界耐久收敛链接索引结果。

**Blocked by:** 09: 实现链接索引 phase 的恢复解释器.

Status-Schema: triage
Status: needs-triage
Tracker-State: closed
Resolution: superseded
Promoted-to: plugins/draft/docs/spec/runtime/authority-and-projections.md
Base-Spec: 1fb633320e1e692ac6e706d826d88e4ad49e3a68
Affects:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
Parent: .scratch/draft-durable-link-index-maintenance/spec.md
Scope: child-issue-only
Current-Owner: plugins/draft/docs/spec/runtime/authority-and-projections.md
Dispatch: blocked
Dispatch-Diagnostic: this business-operation index settlement brief is retained history; the signed Q32 authority/projections owner supersedes the parent feature and this child cannot be dispatched.

- [ ] 是否进入索引阶段由 journal 中实际 Markdown 内容/路径 mutation 和目录成员/节点类型变化决定，不维护一份按命令名称判断的完整白名单。
- [ ] create、move、promote、collapse、status set、relation add/remove 和 generate-conclusions 的实际 mutation 都经过共同收敛边界；没有相关 mutation 的 operation 不产生索引 revision。
- [ ] 计划内空目录在业务验证后、索引候选准备前按深到浅非递归收敛，并按实际结果建立目录快照；索引发布后不再清理目录。
- [ ] 业务 operation 已持有的锁和 journal 直接交给前向提交内核，不释放锁后改用独立 maintenance operation。
- [ ] `move` 证明 backlink 闭包时只消费当前链接事实接口；索引不能严格证明完整时自动使用全根事实。
- [ ] 索引构建或发布失败保留已经验证的业务结果，并在 rebuild-required 已耐久记录后以退出码 `0` 完成原 operation，同时返回稳定降级 warning。
- [ ] journal 耐久失败返回退出码 `3` 和 `operation.durability_failed`，保留业务 after-state、锁及恢复现场；索引内部矛盾停止并保留证据。
- [ ] dry-run 和实际空操作不创建 journal、索引 revision、staging 或 flag。
- [ ] 单元测试用一个未列入当前命令集合但产生相同 mutation 事实的 kind 证明判定依赖事实而不是入口白名单。
