# 05: 页面读取正确区分 YAML 头部和正文

Status-Schema: triage
Status: ready-for-agent
Tracker-State: open
Base-Spec: 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2
Affects:
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md
Scope: child-issue-only
Current-Owner:
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Dispatch: eligible
Dispatch-Diagnostic: 页面读取的字段形状、正文边界和诊断规则已有规格负责人页面定义；纯读取可以与根锁和请求校验并行。

**What to build:** 页面读取和检查能够识别合法的 YAML 头部，把头部事实与头部结束后的正文分开，并在不写入任何文件的情况下向调用者提供可验证的页面事实和诊断。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] 合法页面的字段类型、重复键、未知键、文档形状和不支持特性按完整分类报告；未认证字段不被当作有效状态、父级或关系。
- [ ] 头部中的链接文字不会被当成正文链接、资源或导航内容；正文原字节、换行和原始位置保持可追踪。
- [ ] 读取、检查和再次读取的结果一致；纯解析路径不创建记录、临时材料、索引写入或恢复动作。
- [ ] 非 ASCII 正文、多行头部、不同换行形式和头部形似 Markdown 的内容都有回归测试。
- [ ] 历史 YAML 归档只补经过核对的窄差量，不覆盖当前干净解析实现；所有抽取内容重新通过单元和真实页面集成测试。

**停止条件:** 需要决定初始化页面集合、结论成员资格或尚未由规格负责人定义的页面链接对应规则时停止并报告。
