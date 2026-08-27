# 形成 Draft v1 领域行为合同

Type: prototype

Status: claimed

Blocked by: None

## Question

怎样从仍有效的已批准设计中形成一个只表达当前目标状态的领域行为合同，完整定义页面、状态、关系、create、move、promote、collapse、generate-conclusions、adopt、Trash、Restore 与 read 的输入、输出、不变量、状态转换和失败分类，同时排除旧 marker、journal、index 与恢复协议？哪些术语需要同步进入 `CONTEXT.md`，哪些行为必须留在领域合同？

## Comments

### 已确认的工作决策

- runtime v1 的 `move` 接受未受管理 Markdown 与目录来源，使其通过路径移动进入受管理页面命名空间；领域合同必须闭合该能力的跨命令链接责任、页面协议欠账、目录中非 Markdown 内容和根状态门禁。
- `move`、`promote`、`collapse` 与 `trash` 共享根内 Markdown 链接闭包：受管理页面和 `.draft/` 外的未受管理 Markdown 都参与链接证明；任何必需文件无法安全解码或提取链接时，操作零写失败并报告阻碍集合。
- 未受管理 Markdown 进入页面命名空间称为“页面纳管”；`promote` 的叶页面到枢纽页面转换称为“枢纽升格”。
- 页面纳管只移动原字节并重写链接，不推断标题、状态、正式关系或父导航；纳管后的机器协议欠账由 `check` 报告并交给 Agent 按语义补齐。
- 目录来源只移动 Markdown；其他普通内容留在原位，相关链接重算到原位置，结果向调用方暴露有界的 retained facts。
- 未受管理来源只允许在 `candidate` 根中使用；`registered` 根仍受完整健康门禁约束，不增加修复特例。
