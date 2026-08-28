# 领域文档

本仓库采用多上下文领域文档布局。工程 skills 在探索代码前按本文件读取领域术语和架构决策。

## 探索前读取

- 根目录 `CONTEXT-MAP.md`：上下文路由
- 目标插件的 `CONTEXT.md`：领域词汇和对象边界
- 目标插件的 `docs/spec/index.md`：当前行为规格唯一入口
- 目标插件的 `docs/adr/`：与当前工作范围有关的架构决策记录

路由或目标文件不存在时停止当前 authority 读取，不把其他上下文或历史文件当 fallback。领域术语或决策实际形成后，由 `/domain-modeling` 按目标插件上下文按需创建或更新。

## 文件布局

```text
/
├── CONTEXT-MAP.md
├── plugins/
│   └── <plugin>/
│       ├── CONTEXT.md
│       └── docs/
│           ├── spec/index.md
│           └── adr/
└── ...
```

## 使用领域词汇

issue 标题、spec、重构建议、测试名和代码说明应使用 `CONTEXT.md` 已定义的领域术语。

需要的概念尚未出现在 glossary 时，先判断它是与现有词汇重复，还是确实存在领域模型缺口。确认存在缺口后，把它交给 `/domain-modeling` 处理。

## 处理 ADR 冲突

输出与现有 ADR 冲突时，必须明确指出冲突及理由，不得静默覆盖已有决策。例如：

> 与 ADR-0007 的既有决策冲突；建议重新讨论，因为……
