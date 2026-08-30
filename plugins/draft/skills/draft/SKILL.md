---
name: draft
description: Use when a design or protocol discussion is becoming long, recursive, branched, vulnerable to context compact, or needs a durable Markdown memory graph shared with a human reader.
---

# Draft

把长期、递归、分叉的设计讨论维护成一个自包含的 Markdown 草稿。

Draft 是 [LLM-WIKI](references/llm-wiki.md) 的一种具体实现, 如果上下文中没有该文章内容的记忆, 则必须进行阅读

如果上下文中没有关于 Draft 目录布局、机器区域语法、状态与关系基数的认知，则必须阅读 [draft-format.md](references/draft-format.md)。

如果要把旧布局讨论迁移为 draft 格式，则必须阅读 [migration.md](references/migration.md)，它描述迁移流程、状态映射、枢纽升格和旧构造改写规则。

## 前置条件

要求当前环境需要安装有 [uv](https://docs.astral.sh/uv/)。

如果没有安装 `uv`, 这执行下列命令进行安装:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 核心命令入口

始终通过 `uvx` 从当前 skill 的 runtime 包源执行核心 `draft` 命令。命令如下:

```bash
uvx --isolated --from "<skill_root>/scripts/runtime" draft COMMAND ...
```

`<skill_root>` 会在加载本技能时自动注入到上下文，不需要进行猜测或搜索。 如果没有，直接报错即可。

支持的 `COMMAND` 如下:

- init 初始化新草稿
- adopt 接管格式完整的未登记目录
- check 检查草稿一致性
- notify files-changed 通知根外部批量文件编辑已经稳定
- move 移动页面或未受管理文件：`TARGET` 以 `.md` 结尾时是文件目标，只允许单个文件 `SOURCE`；否则是前缀目标。先按 [来源决策矩阵](../../docs/spec/draft/structure.md#来源决策矩阵) 确认 source/target 准入；目录 `SOURCE` 再按 [目录来源](../../docs/spec/draft/structure.md#目录来源) 对可移动 Markdown 成员分类，目录不是 ownership 对象。`mixed`、`unknown`、`unverifiable` 在写前拒绝、零写并保留成员级诊断；`no-movable` 走 owner no-op。可移动成员、retained 边界、mapping 和目录物理清理均由上述 owner 证明；未受管理来源提升后由 `check` 报告协议欠账
- promote 升格页面为枢纽
- collapse 降级枢纽为普通页面
- trash 回收页面为保留态
- restore 恢复被回收的页面
- recover 按持久 journal 证据收敛崩溃或中断留下的 recovery pending operation
- create 创建叶子页面
- status set 修改页面状态
- relation add / relation remove 修改正式关系
- generate-conclusions 按需生成结论页
- query page / links / backlinks / relations / status / recoverable 紧凑结构查询

使用 `uvx --isolated --from "<skill_root>/scripts/runtime" draft COMMAND --help` 来获得更多信息

### CLI 注意事项

- 除 `init` 外，需要草稿根的命令统一使用可选 `--root ROOT`，缺省回退到绝对 `DRAFT_ROOT`，再回退到物理 `pwd` 祖先发现；`init` 只接受位置 `ROOT`。
- `draft init ROOT` 请求 `registered` 根，`draft init --candidate ROOT` 请求 `candidate` 根；两者的根状态、空现场、冲突、耐久失败和 recovery overlay 均按 [根生命周期](../../docs/spec/draft/lifecycle.md#根生命周期) 与 [运行时初始化与平台](../../docs/spec/runtime/initialization-and-platform.md#初始化与平台前置) 判定，不从目标内容或 marker 缺失猜测另一状态。
- `adopt` 只接受可认证的 `candidate` 根，并在完整无 error Check 后将其推进为 `registered`；已是 `registered` 的根返回既有已登记领域结果并保持零写，不退回 `candidate`。发生冲突、耐久失败或 recovery pending 时按 owner 阻塞并保留现场。
- `SOURCE`、`TARGET`、`PAGE`、`CHILD`、`HUB` 是草稿根内规范相对页面路径（如 `pages/a.md`、`pages/hub/index.md`），不接受目录别名。
- `check` 对 `candidate` 与 `registered` 使用同一检查协议并始终完整扫描；CLI 可以机会式维护内部 link-index journal、制品和 flag，但不修改 Markdown、marker 或回收生命周期。底层纯 checker 的 `maintain_index=False` 只表示内部只读边界，不是 CLI 契约。
- `notify files-changed --root ROOT` 适用于 candidate 与 registered 根，是外部批量编辑稳定后的可选后置通知，不是索引进入 `fresh` 的必要前置，也不是故障恢复命令；它不接收路径、变更类型、rename map 或 stdin 清单，也不支持 `--dry-run`。通知只调用 `maintain(ExternalFilesChanged)`；按 [公共结果与错误投影](../../docs/spec/runtime/errors-and-observability.md#公共结果与错误投影) 的决策表，允许的 delivered 行返回最小 `{"result_type": "files_changed_notification", "delivered": true}` 并退出 `0`，错误行按 owner 的机器 code/退出码返回且不含 `delivered`。live busy 与安全 scan-token 行可 delivered；recovery、corrupt、闭包、耐久或不安全外部变化不得被报告为 delivered。
- 结构写命令的 `--dry-run` 使用相同事实和计划边界，但不创建 journal、revision、索引制品或 flag。

## 第一性边界

- **你负责语义：** 讨论意味着什么、问题是否已确认、质疑是否成立、页面该不该拆分。
- **CLI 负责结构：** 链接完整性、父主题树、状态前缀、关系配对、安全移动/升格/降级/回收、崩溃恢复。
- 不要用 shell、编辑器批量命令直接移动、改名或删除受管理页面；结构变化一律通过 CLI。
- 检查器全绿只证明没有机械损坏，不证明讨论语义正确。

## 何时建立草稿

出现以下任一情况时建立一个草稿：

- 讨论明显变长、出现分支或递归子问题
- 用户明确表达希望将讨论过程记录下来

入口选择：

- 全新讨论 -> `draft init <ROOT>`
- 任意旧布局 -> `draft init --candidate <ROOT>` 建立固定候选根，Agent 按当前格式和语义重写并逐步调用普通命令，完整候选检查 `errors` 为空后执行 `draft adopt`；具体规则见 [migration.md](references/migration.md)

## 即时维护循环

每次用户确认、否定、提出关键反例或打开新分支时，在语义仍然新鲜时执行：

1. 读根索引和当前相关页面，判断受影响的最小页面集合；
2. 只改普通内容 -> 直接编辑 Markdown（标题、正文、例子都自由）；一次外部批量编辑稳定后可选运行 `draft notify files-changed --root ROOT`，逐文件普通编辑不要求逐次通知；
3. 改名、移动、升格、降级、回收 -> 调用对应 CLI 命令，一次命令完成，不拆成手工步骤；
4. 更新受影响的正式关系（`depends_on`、`supersedes`、`challenges`）和页面状态；`parent` 由结构 mapping owner 随路径、物理父和导航共同维护，不由 relation 命令写入；取代关系的状态 after-state 和零写边界见 [页面状态与关系](../../docs/spec/draft/status-and-relations.md#页面状态与关系)；
5. 运行 `draft check`；有客观错误立即修复，有语义歧义立即询问用户。后续 `check` 或链接查询会严格比较当前根事实，不依赖是否发送过 notification。

`relation add/remove` 的关系参数只允许 `depends_on`、`supersedes`、`challenges`；反向代码和 `parent` 均不作为修改参数。`supersedes` 添加会在同一计划中维护反向行并将目标状态设为 `superseded`；删除非最后一条取代关系保持该状态，删除最后一条必须携带 `pending`、`proposed`、`confirmed` 或 `closed` 中的合法目标状态，缺失、非法或反向事实不可认证时零写。

`conclusions.md` 是讨论结束时按需生成的机器产物，不实时维护。日常讨论中不手动写入
或逐条改写结论列表；已确认页面由 `confirmed` 状态标记。只有讨论结束且与用户确认后
才运行 `draft generate-conclusions` 生成。

## 最终工作流

讨论结束并与用户确认后：

1. 按 [结论快照](../../docs/spec/draft/conclusions.md) 的当前格式显式运行 `draft generate-conclusions --root ROOT`；
2. 将快照视为可暂时过时的派生制品。页面的 `confirmed` 状态才是成员资格来源，生成事件之外的状态、关系、正文、Trash 和 Restore 操作不刷新其语义集合；
3. 生成前无法认证关系、端点、状态或当前格式时零写并保留诊断，不通过另一个格式或兼容分支继续。

查询选择：用 `draft query page --root ROOT PAGE` 做路由，并遵循 [读操作与查询](../../docs/spec/draft/query-check-and-output.md#读操作与查询) 的 source-domain 判定。candidate/registered 根中 managed/unmanaged source 对 `links`、`backlinks` 和 `backlink_count` 的组合仍逐项 `unresolved`，不得选择宽域、窄域或 managed-only lower-bound；成功 read、fatal/error、warning 和 notification 的存在性按 [公共结果与错误投影](../../docs/spec/runtime/errors-and-observability.md#公共结果与错误投影) 投影。具体字段、token、顺序和编码仍按 owner 的字段级 `unresolved` 表处理。用 `rg` 搜索正文，不要要求代理在普通编辑过程中维护结论页。

## 拆分时机

- 语义触发为主：子问题获得独立状态生命周期（需要单独确认/否定/关闭）就拆成子页面；
- 容量红线为辅：页面约 600 行或 60 KB 时检查器发出审查提醒--提醒只是提醒，由你判断是否拆；
- 多个子主题只是拆分线索，不是 `promote` 的命令触发条件。只有 owner preflight 已证明待升格项为 leaf、所有选定 sibling 与其同一 parent 且不重复，并认证完整保名 mapping、closure、相对 navigation 和 path roles 时，才可建议或执行 `promote`；跨父、重复、非 leaf、未指定页面、recovery pending 或任一事实不可认证时拒绝并零写，跨父先显式 `move`。
- 仅当 `move` 或 `promote` 的 after-tree 可证明出现空的非根 hub 时，自动 `collapse` 才作为同一结构操作的子阶段执行；候选集合、处理顺序或 after-tree 无法认证时整项零写（见 [结构操作](../../docs/spec/draft/structure.md#collapse)）。`trash`/`restore` 涉及的 reverse-collapse 不是已签署的自动降级保证：只有记录的 mapping 与 after-state 可证明时才可继续，算法和公共 ABI 仍为 `unresolved`；无法证明时按 [Trash 与 Restore](../../docs/spec/draft/trash-and-restore.md#trash-与-restore-行为合同) 的冲突/损坏边界停止并保留现场，不猜测恢复路径。

## 回收与恢复

`trash` 和 `restore` 遵循 [Trash 与 Restore 行为合同](../../docs/spec/draft/trash-and-restore.md#trash-与-restore-行为合同)。Trash 只认证受管理 Markdown 页面/子树、回收包和生命周期；普通链接观察、stale 的 `conclusions.md` 或其入站链接不阻止 Trash，也不删除、重建或改写快照。

### 入口边界与显式 recover

- `draft recover --root ROOT` 是唯一收敛 recovery-pending 的入口。它只在 root-bound stable lock 下重读已持久化的 runtime record、marker/descriptor、manifest/progress、owner/intent、before/after authority、epoch、staging、R/T 或 handoff 证据；不定义新的业务操作。
- 普通写、普通 read（包括 `page`、`links`、`backlinks`、`relations`、`status`、`recoverable`）、结构命令、`read(Check)`、`initialize`、`adopt`、所有 `DryRun` 和 notification 都不隐式调用 `recover`，不枚举 terminal progress，也不删除 recovery record。遇到 pending 时保持业务零写、保留诊断，并指向显式 `draft recover --root ROOT`；收敛后必须重新认证本次操作的完整事实。
- `recover` 不推进 due，不创建 handoff，不调用 `HandoffAdapter`，也不因外部“可能已接受”或目录/mtime/terminal projection 猜测结果。有效 record 有 live owner 时保持现场并返回 `runtime.busy`，不得抢占、删除 record 或启动第二次 recovery。
- 普通 stale 或已耐久降级的链接索引仍可由 notification、`check`、`query page` 或 `query backlinks` 按当前根事实机会式维护；这不属于 `recover`，其公开维护阶段、重试和缓存字段未签时保持 `unresolved`。

### Recovery disposition 与 D-MOVE 门禁

- 本节只提供 owner 导航：[显式恢复与 RecoveryOutcome](../../docs/spec/runtime/recovery.md#显式恢复与-recoveryoutcome)、[D-MOVE snapshot 重认证门禁](../../docs/spec/runtime/recovery.md#d-move-snapshot-重认证门禁与-q6-b-漂移分类)、[证据优先恢复决策表](../../docs/spec/runtime/recovery.md#证据优先恢复决策表) 和 [公共结果与错误投影](../../docs/spec/runtime/errors-and-observability.md#公共结果与错误投影) 分别拥有恢复入口、D-MOVE 门禁、generic disposition 优先级和公开投影；本 skill 不复制这些规则。
- 显式 `recover` 按 recovery owner 执行。pending `D-MOVE` 在 stable lock 内先走 `S` 门禁：`S` 缺失、不完整或资格未证明沿 owner 的 `unresolved` 停止；摘要/关联 epoch/digest 不匹配或当前事实明确漂移/矛盾沿 owner 的 `runtime.corrupt` 停止。通过门禁后才继续 owner 证据求值。
- 其他 pending 不要求 D-MOVE `S`，直接走 owner 的 [证据优先恢复决策表](../../docs/spec/runtime/recovery.md#证据优先恢复决策表)。disposition 分类、优先级、cleanup 授权、record/manifest schema、逻辑提交和 `RecoveryOutcome` ABI 都由 owner 决定；公开结果继续不提供路径列表。

### Restore 前置、提交与失败分区

`restore` 使用不透明 `TrashIdentity`，仅在同一可认证观察中同时验证同一根、当前 authority/active membership、记录的 after-state、保留 package、目标路径、共享观察时刻和 7 天有效窗口。identity 编码、窗口起算时刻/时区、命令字段、结果 ABI 以及 reverse-collapse mapping 算法均为 `unresolved`；占位示例为 `draft restore --root ROOT TRASH_IDENTITY`。

| Restore 事实 | 结果与副作用 |
| --- | --- |
| lexical invalid | 拒绝；不查找、不写入。 |
| missing | 拒绝，零写并保留诊断。 |
| expired | 不恢复；不得解释为 consumed，清理仍受 durable authorization 约束。 |
| consumed | 拒绝重复恢复，零写。 |
| conflict（当前 root/after-state 不精确匹配） | 拒绝；不覆盖、不猜测合并，保留 package。 |
| corrupt（package、identity、摘要、关系/导航或 authority 矛盾/不可认证） | 拒绝并保留现场；不得 cleanup 或重建 package。 |
| recovery-required、未完成或耐久结果未收敛 | 不做新的 Restore 写；只有显式 `recover` 可收敛。 |
| valid：package、after-state、时间、目标路径和完整 after-plan 均可认证 | 只做一次完整领域提交；提交完成后才重新加入 active membership，成功前不得消费 package。 |

package 消费、目录清理或交给后续系统的 cleanup 只有在最终 authority、catalog/head 和 matching progress/receipt 均 durable 且获精确授权后才可发生；unknown、failed、冲突或证据不足时保留 package 与现场。Restore 的具体 identity/字段/退出码 ABI 继续按 owner 的 `unresolved` 边界处理。

Trash/Restore 需要 reverse-collapse 时，必须由已记录 mapping 与 after-state 证明，不能从当前目录形状、页面名或缺失文件猜测；无法证明时进入 conflict/corrupt 边界。Trash 仍不受 stale conclusions 快照或链接观察阻止，也不改写快照；Restore 也不以快照替代其 exact after-state 认证。

### Runtime 输出、warning 与停止

- `failed`、`unknown`、`deferred`、自动阶段的 `busy` 只有在本 invocation 新观察且可行动时才能作为 warning；分别遵循 owner 的 `lifecycle.handoff_failed`、`lifecycle.handoff_unknown`、`lifecycle.deferred` 和 `runtime.busy` 语义。历史 record、历史 busy、仅到达 deadline 或 schedule rebuild 不重播 warning。
- `runtime.schema_unsupported`、`runtime.corrupt`、`runtime.recovery_required`、`runtime.durability_failed` 与未分类 worker/runtime failure 是独立结果；错误 envelope 不得带伪造成功、partial success 或 `delivered`。未分类 worker/runtime failure 返回 owner 的 precise error，不折叠为 delivered；`runtime.corrupt`/durability failure 保留现场，普通入口的有效 pending 明确投影 `runtime.recovery_required`，只阻止并要求显式 recover。自动阶段在稳定 read 上观察到 busy 可作为本次 `runtime.busy` warning；直接写入竞争是 error。Restore 领域错误保持各自独立 machine code，不折叠为 warning 或 delivered。
- 只有 owner 明确允许的安全 cache/索引降级且 authority/head 已 durable 时，成功 projection 或 notification 才可保留；fatal/error 或最终事实不稳定时无 projection。notification adapter 不读取 projection、不调用 lifecycle/recover，也不自行安排重试。
- `.draft/` 下的操作日志、runtime record、权威删除包和锁不可手工编辑或删除。在 recovery-pending 现场，只有显式 `recover` 在上表精确授权且逻辑提交条件成立时，才可按清单 cleanup、unlink record 和 fsync；正常 lifecycle 的 owner-authorized cleanup 仍须遵循其自身 durable 授权，consumer 不得绕过 owner 手工清理。

## 硬停止条件

遇到以下情况立即停止并询问用户，不要在代码或文本里猜测：

- 状态、关系或拆分涉及语义判断而非机械修正；
- `check` 报告你无法解释的客观错误；
- `recover` 返回 `runtime.corrupt`、`runtime.durability_failed`、`runtime.external_change` 或其他未能解释的错误，或其事实只能落入 `unresolved`：保留现场、停止当前流程，不猜测下一步；结构/普通入口遇到 recovery pending 时只指向显式 recover，不在入口内代调用；
- `trash` 或 `restore` 无法认证页面、package、identity、after-state、生命周期事实或完整 after-plan；
- uvx 执行失败，且按 uv 自身的诊断处理后仍无法解决。

## 检查器严重级别

检查器输出四档：

- `errors`客观结构损坏
- `warnings` 只投影本 invocation 新产生且可行动的 `failed`、`unknown`、`deferred`、`busy` 或 owner 明确允许的安全 cache/索引降级；它们不改变 authority，也不替代错误或恢复阻塞。`adopt` 的完整预检查中，安全分类的派生性能降级不阻止登记，但页面错误、内部制品矛盾、耐久失败或 recovery pending 仍阻止登记。具体 warning code、字段和顺序按 owner 处理，未签 ABI 保持 `unresolved`。
- `infos`合法未结束的生命周期事实，如 `trash_retained`
- `reviews`待人类审查提醒。

## 退出码

| 码  | 含义                                                       |
| --- | ---------------------------------------------------------- |
| 0   | 逻辑提交成功或 owner 明确允许的 `delivered`；`check` 无客观错误 |
| 1   | `check` 完成并发现客观结构错误                             |
| 2   | 参数、根、路径、版本或领域前置条件无效                     |
| 3   | runtime/lifecycle/durability/external state 错误（包括需要显式恢复、schema 不支持、busy、corrupt 或不安全冲突；具体 machine code 按 owner） |
| 4   | 运行时 OSError（领域操作期间的文件系统错误）               |
