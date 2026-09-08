# 05: 页面读取正确区分 YAML 头部和正文

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 页面读取和检查能够识别合法的 YAML 头部，把头部事实与头部结束后的正文分开，并在不写入任何文件的情况下向调用者提供可验证的页面事实和诊断。

- [ ] 合法页面的字段类型、重复键、未知键、文档形状和不支持特性按完整分类报告；未认证字段不被当作有效状态、父级或关系。
- [ ] 头部中的链接文字不会被当成正文链接、资源或导航内容；正文原字节、换行和原始位置保持可追踪。
- [ ] 读取、检查和再次读取的结果一致；纯解析路径不创建记录、临时材料、索引写入或恢复动作。
- [ ] 非 ASCII 正文、多行头部、不同换行形式和头部形似 Markdown 的内容都有回归测试。
- [ ] 比较 frontmatter、diagnostics 专题候选与 YAML 归档中的生产代码和配套测试，按已认证页面事实的直接协作边界选择并修补候选；来源和独有测试可追溯，采用内容通过当前基线的单元和真实页面集成测试。来源入口见[复用安排](../spec.md#基线与历史归档)。

## 阶段验收与关闭

头部认证阶段交付 YAML 边界、严格元数据认证、直接诊断和原始位置基础；该阶段尚未提供完整正文观察或跨页面消费者。其重复读取必须零写，并保持未接入公开数据流。

本票完整交付还须提供 06 所需的已认证页面读取事实，包括正文边界、正文观察和直接读取诊断；头部认证阶段不能单独满足 06 的前置。本票按这些读取能力及上述验收关闭，不等待第 12 票的公开组合验收。公开 `OperationRuntime.read`／Check 的请求与错误归 01，运行时效果与并发归 04；最终公开接入沿 [YAML 总任务](../../draft-page-frontmatter/issues/01-yaml-page-io.md#格式整合与关闭)和 12 验证。

**停止条件:** 需要决定初始化页面集合、结论成员资格或尚未由设计规格定义的页面链接对应规则时停止并报告。
