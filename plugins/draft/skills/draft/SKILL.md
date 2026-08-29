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
- move 移动页面或未受管理文件：`TARGET` 以 `.md` 结尾时是文件目标，只允许单个文件 `SOURCE`；否则是前缀目标，来源按基名或目录名落入其下；目录 `SOURCE` 只移动其中的 `.md` 普通文件，其余内容原地留下；未受管理来源提升后由 `check` 报告协议欠账
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

1. 按 [结论快照](../../docs/spec/draft/conclusions.md) 的当前格式显式运行 `draft generate-conclusions ROOT`；
2. 将快照视为可暂时过时的派生制品。页面的 `confirmed` 状态才是成员资格来源，生成事件之外的状态、关系、正文、Trash 和 Restore 操作不刷新其语义集合；
3. 生成前无法认证关系、端点、状态或当前格式时零写并保留诊断，不通过另一个格式或兼容分支继续。

查询选择：用 `draft query page ROOT PAGE` 做路由，并遵循 [读操作与查询](../../docs/spec/draft/query-check-and-output.md#读操作与查询) 的 source-domain 判定。candidate/registered 根中 managed/unmanaged source 对 `links`、`backlinks` 和 `backlink_count` 的组合仍逐项 `unresolved`，不得选择宽域、窄域或 managed-only lower-bound；成功 read、fatal/error、warning 和 notification 的存在性按 [公共结果与错误投影](../../docs/spec/runtime/errors-and-observability.md#公共结果与错误投影) 投影。具体字段、token、顺序和编码仍按 owner 的字段级 `unresolved` 表处理。用 `rg` 搜索正文，不要要求代理在普通编辑过程中维护结论页。

## 拆分时机

- 语义触发为主：子问题获得独立状态生命周期（需要单独确认/否定/关闭）就拆成子页面；
- 容量红线为辅：页面约 600 行或 60 KB 时检查器发出审查提醒--提醒只是提醒，由你判断是否拆；
- 讨论页派生出多个子主题时升格为枢纽（`promote`）。仅当 `move` 或 `promote` 的
  after-tree 可证明出现空的非根 hub 时，自动 `collapse` 才作为同一结构操作的子阶段
  执行；候选集合、处理顺序或 after-tree 无法认证时整项零写（见
  [结构操作](../../docs/spec/draft/structure.md#collapse)）。`trash`/`restore` 涉及的
  reverse-collapse 不是已签署的自动降级保证：只有记录的 mapping 与 after-state 可证明
  时才可继续，算法和公共 ABI 仍为 `unresolved`；无法证明时按
  [Trash 与 Restore](../../docs/spec/draft/trash-and-restore.md#trash-与-restore) 的冲突/损坏边界停止并保留现场，不猜测恢复路径。

## 回收与恢复

- `trash` 按 [Trash 与 Restore](../../docs/spec/draft/trash-and-restore.md#trash-与-restore) 认证页面、删除包和生命周期；普通链接观察以及 `conclusions.md` 入站链接不阻止 Trash，快照条目也不因此删除或重建。
- `trash` 后 7 天内可 `draft restore ROOT OPERATION_ID` 恢复页面；到期后的系统回收站移交和最终清理由允许的已登记命令前置推进，Agent 不需要维护计时器。`draft recover ROOT` 只按 journal 证据收敛 recovery pending operation，不推进 retained trash 的到期、移交或清理。普通 stale 或已耐久降级的链接索引由可选 notification、`check`、`query page` 或 `query backlinks` 根据当前根事实机会式维护，不属于 `recover`；其公开维护阶段、重试和缓存字段不由本入口补造。最终清理（`trash_purge_committed`）只在到达清理期限后最后一次投递明确失败时发生，是永久删除授权。
- `restore` 未完成、冲突、证据不足或运行期现场不安全时按 [Trash 与 Restore](../../docs/spec/draft/trash-and-restore.md#trash-与-restore) 和 runtime owner 返回可诊断错误；不要把错误输出宣称为已恢复，也不要凭未签的成功布尔字段或固定 `result_type` 推断成功。Restore 的领域 code、退出码和公共 envelope 字段仍由对应 owner/ABI 决定，未签字段保持 `unresolved`。
- 交接、索引、耐久和恢复结果只能按 runtime owner 投影：可安全分类的派生 cache 降级可以保留成功/允许 delivered，authority 或已决定结果的耐久失败必须 error 且不含 `delivered`；notification adapter 不读取投影、不调用自动 lifecycle 或 recovery，也不自行安排重试。结构命令遇到 recovery pending 时先运行 `draft recover ROOT`；不要删除锁、日志或删除包，恢复若无法认证则保留现场并停止。
- `.draft/` 下的操作日志、权威删除包和锁是工具内部状态，绝不手工编辑。

## 硬停止条件

遇到以下情况立即停止并询问用户，不要在代码或文本里猜测：

- 状态、关系或拆分涉及语义判断而非机械修正；
- `check` 报告你无法解释的客观错误；
- `recover` 无法完成机械恢复，或其输出为 runtime owner 定义的 corruption/inconsistent/external-change 错误；结构命令首次提示 recovery pending 时先按上一节运行 `recover` 做机械恢复，只有恢复本身失败或结果无法解释才到这里停止；
- `trash` 无法认证页面、删除包或生命周期事实；
- uvx 执行失败，且按 uv 自身的诊断处理后仍无法解决。

## 检查器严重级别

检查器输出四档：

- `errors`客观结构损坏
- `warnings` 合法但需注意的运行状态，只能来自本次 runtime owner 明确允许的 cache/索引派生降级；它们不改变 authority，也不替代错误或恢复阻塞。`adopt` 的完整预检查中，安全分类的派生性能降级不阻止登记，但页面错误、内部制品矛盾、耐久失败或 recovery pending 仍阻止登记。具体 warning code 和字段按 owner 处理，未签 ABI 保持 `unresolved`。
- `infos`合法未结束的生命周期事实，如 `trash_retained`
- `reviews`待人类审查提醒。

## 退出码

| 码  | 含义                                                       |
| --- | ---------------------------------------------------------- |
| 0   | 成功；`check` 无客观错误；回收站移交可以处于待移交并带警告 |
| 1   | `check` 完成并发现客观结构错误                             |
| 2   | 参数、根、路径、版本或领域前置条件无效                     |
| 3   | 需要恢复、锁/日志损坏、外部修改或不安全恢复冲突            |
| 4   | 运行时 OSError（领域操作期间的文件系统错误）               |
