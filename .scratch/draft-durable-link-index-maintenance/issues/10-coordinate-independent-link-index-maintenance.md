# 10: 实现独立 `link-index.update` maintenance 协调器

**What to build:** 组合候选准备、写锁、authority 复核、operation 分配、前向提交和恢复能力，为 notification、`check`、query 与 adopt 预检查提供独立于业务 mutation 的索引维护用例。

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
Dispatch-Diagnostic: this independent maintenance-coordinator brief is retained history; the signed Q32 authority/projections owner supersedes the parent feature and this child cannot be dispatched.

- [ ] 严格扫描和候选准备发生在 operation 分配之前；根事实无变化时不取得写锁、不创建 journal、不产生 revision。
- [ ] 已有 operation 持锁时，独立维护不读取正在变化的根、不创建 maintenance operation，并向调用方返回明确的未发布结果。
- [ ] 需要提交时取得 Draft 写锁，重读最新 authority，并与准备阶段观察到的 absent/revision 令牌比较。
- [ ] authority 已推进时放弃旧候选，不发布更高 revision，也不记录 rebuild-required；不得用旧扫描覆盖另一个受协调 operation 的结果。
- [ ] authority 未变化时创建 `link-index.update` operation，写入 `allocating` 后调用前向提交内核，再进入 `completed` 并清理本 operation staging 和锁。
- [ ] 扫描期间根再次变化时不无限重试；能够耐久记录时形成 rebuild-required，当前读取调用方仍可按自己的完整事实协议继续。
- [ ] maintenance 结果明确提供当前可用索引事实、公开 warning 和是否发布，不携带触发入口或入口专用输出。
- [ ] 集成测试覆盖首次发布、无变化、单文件增量、rebuild-required 后全根重建、已有锁、revision 竞争和各 maintenance phase 中断后的恢复。
