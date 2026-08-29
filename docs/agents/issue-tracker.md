# Issue tracker：本地 Markdown

本仓库的 issue、feature spec、Agent Brief 和 wayfinding map 使用 `.scratch/` 下的本地 Markdown 文件管理。它们是当前 Wiki 的 delta、过程证据或派遣入口，不是行为 authority；当前行为只能从 `CONTEXT-MAP.md`、插件 `CONTEXT.md`、`plugins/draft/docs/spec/index.md` 和其 owner 页面读取。

## 文件约定

- 每个 feature 使用独立目录：`.scratch/<feature-slug>/`
- feature spec 位于 `.scratch/<feature-slug>/spec.md`
- 实施 issue/Agent Brief 分别写入 `.scratch/<feature-slug>/issues/<NN>-<slug>.md`
- issue 从 `01` 开始编号；每个 issue 使用独立文件
- map 位于 `.scratch/<effort>/map.md`，只表达路线和依赖，不成为行为 authority
- 评论和讨论记录追加到文件末尾的 `## Comments` 章节

## Tracker 头部与机器判定

纳入 authority/consumer audit 的 `.scratch` feature/spec/issue/map/Agent Brief 必须在标题附近提供下列正交字段；字段名和值按行解析，列表字段使用后续缩进的 `-` 行：

```text
Status-Schema: triage
Status: <canonical triage role>
Tracker-State: open | closed
Base-Spec: <valid base commit or approved base locator>
Affects:
  - <repo-relative current owner path>
Parent: <repo-relative parent path or none>
Scope: <feature-delta | child-issue-only | wayfinder-map-only>
Current-Owner: <repo-relative current Wiki owner path>
Dispatch: eligible | blocked
Dispatch-Diagnostic: <具体、可复核的阻断或资格说明>
```

`Status` 只能取 `needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。`Tracker-State` 只能取 `open` 或 `closed`；open 条目不得写 `Resolution` 或 `Promoted-to`，closed 条目必须写合法的 `Resolution`，其中 `applied`/`superseded` 必须有可解析的 `Promoted-to` current-spec Target，`rejected` 不得有 `Promoted-to`。`Base-Spec` 和 open 条目的 `Affects` 必须非空且可解析；closed 条目的历史基线若无法重放，必须在 `Dispatch-Diagnostic` 中说明，不得伪造 current owner。

只有同时满足 `Status-Schema: triage`、合法字段、`Tracker-State: open`、`Status: ready-for-agent`、有效 `Base-Spec`、非空可解析 `Affects`、无父子范围冲突，并且 `Dispatch: eligible` 时才能派遣。任何字段缺失、未知值、非法组合、无效链接、父子范围扩张或未决语义都输出 `unresolved` 并保持 `Dispatch: blocked`；不能默认成 `open`、`applied`、`ready-for-agent` 或从标题、`Type`、`Blocked by`、代码、测试和 checklist 推断完成。

`Current-Owner`、`Promoted-to` 和 `Affects` 只提供 tracker 导航，不能定义或覆盖 owner 页面行为。Agent Brief 只能收窄 `Parent` 的范围；与父 spec 或 current Wiki 冲突时，child 逐项保持 `Status: needs-triage`、`Tracker-State: open`、`Dispatch: blocked`，并写出冲突入口、影响和 current owner。已完整取代的条目显式使用 `closed` + `Resolution: superseded`，保留文件和逐项诊断，不通过 parent 状态隐式关闭 child。

Q23 只排除 `docs/design.md`、`docs/discussion/**`、review notes、plan 和明确的过程报告；不能用排除规则批量删除 `.scratch` 历史条目，也不能把 tracker 状态写入正式规格 owner。

## Skill 操作

当 skill 要求“发布到 issue tracker”时，在 `.scratch/<feature-slug>/` 下创建相应文件；目录不存在时一并创建，并先写完整 tracker 头部。

当 skill 要求“读取相关 ticket”时，读取用户提供的文件路径或 issue 编号对应的文件；先解析 tracker 头部，再读取正文。缺字段或冲突条目只能作为不可派遣过程证据，不能作为当前行为输入。

## Wayfinding 操作

`/wayfinder` 使用一个 map 文件和多个独立的 child ticket 文件：

- Map：`.scratch/<effort>/map.md`，保存 Notes、Decisions-so-far 和 Fog；它的 tracker 状态独立于 child。
- Child ticket：`.scratch/<effort>/issues/<NN>-<slug>.md`
- `Type:` 可记录 `research`、`prototype`、`grilling` 或 `task`，但不是 tracker 状态，也不能替代 `Status-Schema`、`Status` 或 `Tracker-State`。
- `Blocked by: NN, NN` 只记录过程依赖；依赖满足不能绕过 `Base-Spec`、父子范围和 current owner 判定。
- Frontier 只从机器判定为 `Dispatch: eligible` 的条目中选择；legacy 缺字段、`claimed`/`resolved` 旧状态或未决冲突均不可派遣。
- Claim/Resolve 只能追加过程说明或更新完整正交 tracker 字段；不得把 `Status` 改成 `claimed`/`resolved`，不得只在 map 或 parent 写 superseded 来关闭 child。
