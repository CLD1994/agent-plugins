# 06: 实现链接索引 authority repository

**What to build:** 实现链接索引状态的权威读取边界，从合法 operation journal 中选择最新结果，并在 `published` 状态下验证与其绑定的完整索引制品。

**Blocked by:** 02: 建立 operation journal v4 状态模型; 03: 建立纯链接索引领域引擎; 05: 实现索引制品和 rebuild flag 的耐久存储.

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
Dispatch-Diagnostic: this index-authority repository brief is retained history; the signed Q32 authority/projections owner supersedes the parent feature and this child cannot be dispatched.

- [ ] repository 只读取规范 operation 目录中的 journal v4，并只收集合法索引结果 phase 或经索引阶段到达的合法终态。
- [ ] revision 按任意精度整数比较，选择数值最大的合法结果；不得使用字符串字典序。
- [ ] 没有任何索引结果时返回 absent；重复 revision、同一 revision 的冲突结果或非法字段组合返回内部状态错误。
- [ ] 最新结果为 `published` 时，索引目标必须是普通文件，并且 schema、revision、制品 SHA-256 和反向表全部与 journal 一致。
- [ ] 最新结果为 `rebuild_required` 时，现有索引制品不具有 authority，不得作为可信增量基线。
- [ ] rebuild flag 只能快速阻断未经 authority 判定的读取；flag 缺失不能覆盖 journal 的 rebuild-required 结果，残留 flag 也不能覆盖合法 published 结果。
- [ ] 当前允许通过线性扫描全部 journal 发现最新 revision，不增加 sidecar 或第二份 revision authority。
- [ ] 单元和集成测试覆盖 absent、published、rebuild-required、超大 revision、重复结果、冲突结果、制品缺失、摘要不符、schema 不符和反向表损坏。
