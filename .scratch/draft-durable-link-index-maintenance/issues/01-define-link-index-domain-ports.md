# 01: 固定链接索引维护的领域状态与依赖端口

**What to build:** 建立链接索引维护的领域边界，使上层策略依赖稳定的语义接口，而不是依赖文件系统、journal 或 CLI 的具体实现。领域模型必须显式表达 authority、候选准备、提交结果和失败分类，并为后续适配器规定单向依赖关系。

**Blocked by:** None (can start immediately).

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
Dispatch-Diagnostic: this link-index domain-port brief is retained history; the signed Q32 authority/projections owner supersedes the parent feature and this child cannot be dispatched.

- [ ] 链接索引修订号使用规范十进制字符串表示单调正整数，从 `"1"` 开始；解析、比较和递增支持任意精度，并拒绝零、前导零、符号、小数、指数、空白和 JSON 数字。
- [ ] authority 明确区分从未产生结果、已发布、要求重建和内部不一致；候选准备明确区分无变化、可以发布、要求重建和无法继续，不用 `None`、空集合或布尔值折叠这些状态。
- [ ] 高层策略拥有根事实来源、authority repository、索引制品存储、operation journal 存储和写锁的端口；基础设施适配器依赖这些端口，领域与应用层不导入具体文件系统实现。
- [ ] 独立 maintenance、业务 operation 收敛、notification、`check` 和 query 使用各自的应用边界，不通过带入口模式参数的万能维护接口共享生命周期策略。
- [ ] 预期领域失败与断言、类型错误和未知异常具有不同结果边界，后续入口可以只吸收已经分类的维护结果。
- [ ] 纯单元测试穷举领域状态和端口结果，不访问真实文件系统、启动子进程或依赖 CLI。
