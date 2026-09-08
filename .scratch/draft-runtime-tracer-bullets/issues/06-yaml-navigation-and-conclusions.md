# 06: 导航、查询和结论读取使用同一页面事实

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/conclusions.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
Parent: .scratch/draft-runtime-tracer-bullets/spec.md

**交付目标：** 页面图、查询、检查、链接索引和结论读取都消费同一份已认证页面事实，不各自重新解释头部或正文。生成的结论文件按“独立制品”处理，不被误认为普通页面或物理父页。

Blocked by:
  - [05: 页面读取正确区分 YAML 头部和正文](05-yaml-page-read.md)：提供经过认证且明确区分头部与正文的页面事实。

- [ ] 头部链接字符串不会进入正文资源、反向链接、导航或结论成员集合。
- [ ] 即使页面头部本身无效，仍报告能够独立确认的跨页面导航问题；不能因为一个无效字段而吞掉其他可认证事实。
- [ ] 结论制品的固定父级只用于制品解析，不被当作页面树中的实际父页或普通页面关系。
- [ ] 页面图、查询、检查、链接索引和结论解析对同一输入给出一致的节点身份、目标和定位；纯读取不产生持久副作用。
- [ ] 单元测试验证局部事实，真实页面集成测试验证解析器与各消费者的组合；Markdown 与结论候选按直接生产者、消费者和配套测试成组比较、修补并重新评审，采用范围和来源遵循[复用安排](../spec.md#基线与历史归档)。

**停止条件:** 需要自行决定原始 HTML 的新接受规则、结论成员资格或页面链接在不同位置之间的对应关系时停止并报告。
