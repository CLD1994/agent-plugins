# Issue tracker：本地 Markdown

本仓库的 issue 和 spec 使用 `.scratch/` 下的本地 Markdown 文件管理。

## 文件约定

- 每个 feature 使用独立目录：`.scratch/<feature-slug>/`
- feature spec 位于 `.scratch/<feature-slug>/spec.md`
- 实施 issue 分别写入 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- issue 从 `01` 开始编号；每个 issue 使用独立文件
- issue 顶部附近使用 `Status:` 字段记录分诊状态，合法角色见 `triage-labels.md`
- 评论和讨论记录追加到文件末尾的 `## Comments` 章节

## Skill 操作

当 skill 要求“发布到 issue tracker”时，在 `.scratch/<feature-slug>/` 下创建相应文件；目录不存在时一并创建。

当 skill 要求“读取相关 ticket”时，读取用户提供的文件路径或 issue 编号对应的文件。

## Wayfinding 操作

`/wayfinder` 使用一个 map 文件和多个独立的 child ticket 文件：

- Map：`.scratch/<effort>/map.md`，保存 Notes、Decisions-so-far 和 Fog
- Child ticket：`.scratch/<effort>/issues/<NN>-<slug>.md`
- `Type:` 字段记录 `research`、`prototype`、`grilling` 或 `task`
- `Status:` 字段记录 `claimed` 或 `resolved`
- `Blocked by: NN, NN` 字段记录依赖；所列 ticket 全部为 `resolved` 后才解除阻塞
- Frontier：按编号选择第一个仍开放、未阻塞且未认领的 ticket
- Claim：开始工作前先把 `Status:` 改为 `claimed`
- Resolve：在 `## Answer` 下追加结论，把 `Status:` 改为 `resolved`，再向 `map.md` 的 Decisions-so-far 添加上下文指针
