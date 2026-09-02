# 页面头部使用 YAML frontmatter

Draft 受管理页面采用 YAML frontmatter 保存页面元数据，并把它与 Markdown 正文保持清晰边界；Draft 按自身页面语义解释这些元数据，不把通用 YAML 或其他头部字段直接当作页面协议。

这样既便于人和 Agent 工具识别，又保留 Draft 对页面结构的严格控制。具体语法和字段规则见[页面与导航](../spec/draft/pages-and-navigation.md#页面头部与正文边界)及[状态与关系](../spec/draft/status-and-relations.md#yaml-页面头部结构)。
