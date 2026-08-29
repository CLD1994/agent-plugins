# 14: 接入 `check` 和 `adopt` 的机会式索引维护

**What to build:** 让 `check` 和 adopt 完整预检查复用已经取得的页面事实，并通过独立 maintenance 协调器机会式维护索引，同时保持页面合法性与缓存健康状态的责任边界。

**Blocked by:** 10: 实现独立 `link-index.update` maintenance 协调器.

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
Current-Owner:
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/draft/lifecycle.md
Dispatch: blocked
Dispatch-Diagnostic: this check/adopt maintenance brief contains a legacy independent-operation contract; the signed Q32 owner supersedes it and the conflict cannot be dispatched.

- [ ] `check` 对 absent、fresh、stale、rebuild-required、flag 降级、制品不一致、journal 损坏和 recovery pending 使用规格中的完整状态决策表。
- [ ] `check` 已经解析的 Markdown 只有在严格扫描重新读取正文并验证 SHA-256 后才能复用链接提取结果。
- [ ] absent、stale 或 rebuild-required 维护成功时不产生索引 issue；维护后仍降级时投影稳定 warning，且完整页面检查事实仍然返回。
- [ ] journal 耐久失败时保留已经计算的检查事实并报告 operation error；制品矛盾、journal 损坏或 recovery pending 作为内部/operation error 报告。
- [ ] 索引性能降级、从未建立或 flag 普通 I/O 降级不阻止 `adopt`；无法解释的 journal、制品矛盾或 recovery pending 阻止登记。
- [ ] adopt 在开始 marker operation 前完成独立 maintenance operation，两个 operation 不共享含糊的锁或 journal 所有权。
- [ ] `init` 不发布首次链接索引；第一次维护由业务写入、notification 或允许机会式维护的入口触发。
- [ ] 测试同时断言 `check` issue、adopt 决策、journal、revision、索引、flag 和锁，不以索引写入成功替代完整检查结果。
