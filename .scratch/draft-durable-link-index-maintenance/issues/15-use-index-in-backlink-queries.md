# 15: 接入 backlink 查询的索引消费与机会式维护

**What to build:** 让 `query backlinks` 和 `query page` 通过当前链接事实边界复用严格验证的 backlink，并在需要时调用独立 maintenance 协调器，而不改变其他查询的副作用范围。

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
  - plugins/draft/docs/spec/draft/query-check-and-output.md
Dispatch: blocked
Dispatch-Diagnostic: this backlink-query index brief is retained history; source-domain and projection scope are unresolved under the current owners, so it cannot be dispatched.

- [ ] `query backlinks` 在 fresh、absent、stale 和 rebuild-required 状态下都返回与当前根完整事实相同的 backlink 结果。
- [ ] 只有通过 authority、schema、revision、制品摘要、反向表、全部 Markdown SHA-256 和目录事实验证后，查询才能用索引证明不存在其他 backlink。
- [ ] 索引不可用或维护没有发布时，查询使用本次完整扫描事实；不得返回旧 backlink、近似结果或要求调用方执行 notification。
- [ ] `query page` 的 backlink count 与 `query backlinks` 消费同一链接事实，在所有索引状态下保持一致。
- [ ] 维护成功时正常返回且不产生 stale warning；耐久 rebuild-required 或 flag 降级时返回一次相应 warning；journal 耐久失败使普通 query 非零退出。
- [ ] `query links`、relations、status 和 recoverable 查询不触发全根索引维护，运行前后 operation、索引和 flag 保持不变。
- [ ] 保留既有 candidate/registered 限制、分页、count-only 和机器结果信封语义。
- [ ] 单元测试验证真实维护用例边界的调用路由，不能只通过搜索源码字符串判断依赖关系。
