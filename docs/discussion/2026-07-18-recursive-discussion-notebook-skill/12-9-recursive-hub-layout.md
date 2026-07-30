# D12.9：次级枢纽怎样映射为目录

> 状态：`已确认`
>
> 产出：[结论汇总](conclusions.md)
>
> 父主题：[D12 最小机器可识别结构使用什么 Markdown 语法](12-minimal-structure-syntax.md)
>
> 依赖：[D2 页面的最小职责](02-page-model.md)、[D10.5 草稿结构变化如何安全落盘](10-maintenance-agent-launch/10-5-structural-operations.md)、[D12.8 父主题关系是否必须形成一棵根树](12-8-parent-rooted-tree.md)

## 要解决的问题

用户已经确认索引拆分递归应用到每一层。当前 D10 在逻辑上拥有 D10.1 至 D10.20 等直接子页，但物理上仍只是与这些子页并列的 `10-maintenance-agent-launch.md`，还不是真正的次级索引目录。

本页只决定次级枢纽怎样落到文件系统。它不重新讨论是否应该拆分 D10，也不立即执行当前历史草稿的迁移。

## 第一性原理

合适的物理表示需要：

- 人类进入任意子目录时，都能立刻找到该层的 `index.md`；
- 一层索引只直接列出自己的子问题，不让根索引扁平列出全部后代；
- 叶子页产生子问题时，可以通过安全结构操作提升为枢纽；
- 物理层级和 `父主题` 不应互相矛盾；
- 不需要页面类型字段或另一份目录注册表；
- 目录递归不能引入没有实际作用的固定层级。

## 三种路线

### A：枢纽使用子目录中的 `index.md`，子项直接放在该目录内

```text
topic/
├── index.md
├── conclusions.md
└── pages/
    ├── d1.md
    └── 10-maintenance-agent-launch/
        ├── index.md
        ├── 10-1-reusable-maintenance-session.md
        ├── 10-2-codex-explicit-compaction.md
        └── 10-15-postcompact-context-cost/
            ├── index.md
            └── 10-15-1-example.md
```

规则很简单：

- 根的直接讨论项位于根级 `pages/`；
- 叶子问题是 `.md` 文件；
- 拥有子问题的枢纽是一个目录，该目录的 `index.md` 表示枢纽本身；
- 枢纽目录名完整复用原页面文件名去掉 `.md` 后的文件主干名，例如 `10-maintenance-agent-launch.md` 升格为 `10-maintenance-agent-launch/index.md`，不得缩写成 `d10/`；
- 枢纽的直接子项放在同一目录中，可以继续是叶子文件或下一层枢纽目录；
- 子页的 `父主题` 必须链接该目录的 `index.md`，检查器同时验证链接关系和物理归属一致；
- `conclusions.md` 始终只存在于草稿根，不随次级枢纽递归复制。

叶子 `pages/10-maintenance-agent-launch.md` 产生独立子问题时，安全工具把它提升为 `pages/10-maintenance-agent-launch/index.md`，创建或移动直接子项，并一次性修复所有链接。反向降级是否需要自动进行，不在本轮决定。

这是当前推荐路线。它递归保留 `index.md`，但不在每层机械重复一个只有分组作用的 `pages/` 目录。

### B：每个枢纽都重复 `index.md + pages/`

```text
topic/
├── index.md
├── conclusions.md
└── pages/
    └── 10-maintenance-agent-launch/
        ├── index.md
        └── pages/
            ├── 10-1-reusable-maintenance-session.md
            └── 10-15-postcompact-context-cost/
                ├── index.md
                └── pages/
                    └── 10-15-1-example.md
```

它在形式上完全自相似，也明确区分枢纽文件和子项。但每深入一层都多一个没有独立语义的 `pages/` 路径，增加路径长度、链接噪声和移动工作。次级枢纽没有根级 `conclusions.md` 等其他固定文件需要隔离，因此这层重复目录提供的价值有限。

### C：继续保持所有页面物理扁平，只靠 `父主题` 表达递归

```text
pages/
├── d10.md
├── d10-1.md
└── d10-15.md
```

这是当前临时草稿的形式，移动最少，逻辑父子树也能成立。但人类从文件系统或 Obsidian 文件树看不到次级边界；根的同一目录会随着讨论增长持续堆积；“D10 已经成为次级索引”也只存在于标签关系中，没有落实用户要求的递归索引目录。

因此不推荐继续使用。

## 用当前 D10 说明路线 A

确认路线 A 后，当前结构在显式迁移时会从：

```text
10-maintenance-agent-launch.md
10-1-reusable-maintenance-session.md
10-2-codex-explicit-compaction.md
...
10-20-mcp-and-maintenance-agent-scope.md
```

变为类似：

```text
10-maintenance-agent-launch/
├── index.md
├── 10-1-reusable-maintenance-session.md
├── 10-2-codex-explicit-compaction.md
...
└── 10-20-mcp-and-maintenance-agent-scope.md
```

根索引只链接 `d10/index.md`。D10 的索引只保存本主题范围、当前有效路线、直接子页索引和下一步；目前仍留在 D10 主文件里的大段历史论证，需要在迁移评审时归入合适子页或历史页，而不是原样塞进次级索引。

## 对抗性审查

- **目录路径不是已经表达父关系了吗？** 是，但 `父主题` 仍提供页面内可点击的向上导航，也是安全移动后需要校验的明确声明。检查器验证两者一致，避免两套关系悄悄分叉。
- **叶子升级为目录会不会导致大量断链？** 直接手工移动会；因此它必须使用 D10.5 已确认的安全结构操作，先暂存、修复全部入站链接、检查新结构，再完成切换。
- **路径会不会变得太深？** 路径深度对应真实讨论层级。路线 A 已经避免每层额外重复 `pages/`；若层级仍异常深，应审查讨论边界，而不是让文件系统隐藏它。
- **D10 的所有后代都必须由根索引列出吗？** 不必。根只列 D10 次级枢纽；D10 的 `index.md` 列直接子项，渐进阅读逐层展开。
- **每个次级枢纽都需要自己的 `conclusions.md` 吗？** 不需要。D12.6 已确认整个草稿网络只有根级一个固定、扁平的结论汇总页。
- **为什么不能把目录缩写成 `d10/`？** 文件树本身也承担渐进阅读职责。完整文件主干名保留了编号和主题含义，不需要人类先打开 `index.md` 才知道目录讨论什么，也避免维护另一套简称映射。
- **当前草稿现在就移动吗？** 用户已经明确要求当前 D10 转换为次级父节点；目录约定确认后，将本次迁移作为对安全结构操作和检查器规则的真实场景试用。

## 当前建议

采用路线 A：根级 `pages/` 是受管理讨论区；其中叶子是 `.md`，次级枢纽是带 `index.md` 的目录，子项直接放在该枢纽目录内并可继续递归。枢纽目录完整复用原页面文件主干名，物理归属和 `父主题` 必须一致。

## 已确认结论

用户确认路线 A，并明确要求目录保留完整语义名称：`10-maintenance-agent-launch.md` 升格为 `10-maintenance-agent-launch/index.md`，不能简写为 `d10/`。次级枢纽的子项直接放在该目录内，不在每层重复 `pages/`。

当前 D10 文件组已经按此规则完成显式迁移：主页面位于 `10-maintenance-agent-launch/index.md`，20 个直接子页位于同一目录，根索引只保留 D10 枢纽入口。递归链接与父子导航检查通过。下一项转入 [D12.10 枢纽失去全部子页后是否自动降级](12-10-empty-hub-collapse.md)。
