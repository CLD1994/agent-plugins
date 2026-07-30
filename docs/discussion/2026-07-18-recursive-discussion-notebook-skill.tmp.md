# 递归讨论草稿方法论与 Skill 设计

> 状态：`初版已实现；2026-07-21 对抗性复评补充与增量整改计划已确认，等待实施`
>
> 这是临时元讨论索引，只讨论如何维护递归讨论草稿以及怎样沉淀为 skill。它不继续 Tavern Connector 协议评审，也不是已批准的正式设计。

## 当前状态

D1-D9 已逐项确认。元讨论草稿已经按这些规则完成首次递归拆分。

skill 将作为独立 Git 项目交付，至少兼容 Codex 和 Claude Code。项目采用一个 marketplace 仓库、一个实际插件子目录、两份平台清单、一份共同核心的结构。

运行时采用 Python 3.14.x 和 uv；skill 必须指导环境检查与搭建。Codex 与 Claude Code 分别在自己的持久插件数据目录维护独立虚拟环境。环境不可用时，自动入口只给出诊断和 setup 指引，不保存压缩前输入。依赖原则是合理复用成熟项目，而不是把“尽量只用标准库”当成目标。D10 已确认正常路径是在讨论发生时即时维护，同一会话压缩或恢复后通过耐久绑定和短重新绑定信封恢复 skill；第一版不注册 `PreCompact`，不复制转录，不维护逐轮可靠标记。新建或正式接管的草稿网络必须放在一个自包含目录内。D11 已确认第一版检查器只检查客观结构事实与审查提醒，不判断自然语言语义。

## 当前结果

D1-D13 的第一版设计边界已经逐项收口，没有仍待讨论的阻塞项。D13.3 已经关闭：第一版由 Agent 根据 skill 调用 CLI，CLI 负责机械安全和崩溃恢复；失败、歧义或需要人类判断时通过正常对话反馈，不建立额外批准协议。

唯一扁平的 [结论汇总](2026-07-18-recursive-discussion-notebook-skill/conclusions.md) 已生成。用户已经明确授权迁移，并在 Tavern 的 `.claude/skills/discussion-notebook/` 孵化目录生成 [Discussion Notebook 正式设计](../../.claude/skills/discussion-notebook/docs/design.md)。初版实现和旧实现计划已经完成；真实迁移试用暴露出旧布局适配器把 Agent 可以处理的语义决策错误地下沉给程序，以及即时系统回收站投递让 `restore` 只在失败窗口偶然可用。2026-07-20 已就这两组问题完成修订设计讨论；随后五轮代码复评又暴露出候选登记线性化、日志身份、权威删除包证据链和恢复终态证明不足。2026-07-21 已确认这些补充不变量；旧实现计划不再作为实施依据，由新的增量整改计划取代。

## 2026-07-20 真实试用后的修订结论

### Agent 主导迁移

- 放弃 `superpowers-sidecar-v0` 这类按旧格式枚举状态、标签和页面角色的迁移适配器，也移除第一版公开 `migrate` 命令。迁移不是程序对旧语义的猜测，而是 Agent 读取来源、按当前规范重写候选草稿本的工作流。
- 程序只负责确定性边界：`check ROOT --candidate` 检查尚未登记的候选根是否已经完整满足当前格式；`adopt ROOT` 在同一候选检查通过后只写入 `notebook.json`；普通 `check ROOT` 只接受已经登记的草稿本。
- 候选检查不降低结构要求。它只把“必须已有登记文件”替换为“必须尚未登记且没有操作状态”，其余页面树、关系、路径和 Markdown 协议与正式检查相同。
- 纯链接列表由 Agent 无须询问地逐行展开成每行单目标。若旧行还含额外语义，或展开后出现同一标签重复指向同一规范化目标，Agent 结合上下文决定如何改写，无法确定时再询问用户。
- 表外旧标签、外部结构链接和不能唯一映射的状态同样交给 Agent 判断。程序不认识旧标签，只验证候选结果使用当前标签、每行单目标、无重复规范化关系且所有结构关系留在草稿根内。
- 迁移始终复制到新的空候选目录，来源保持不变。Agent 先消解全部语义和结构问题，再执行候选检查与接管；程序失败只表示结果尚未满足当前协议，不代替 Agent 作迁移决策。

### 页面回收、保留与恢复

- 页面确认、关闭、被取代、已经迁移到正式设计或仅仅存在很久，都不是回收条件。普通历史页面永不按年龄自动离开草稿树。
- Agent 只有在页面是误建空壳、完整重复页，或错误拆分且全部独有内容已经并回其他页面时，才可以不询问直接执行 `trash`。只要可能丢失独有历史或页面归属不清，就先询问用户。
- `trash` 本身就是最终删除授权。成功后页面先进入内部权威删除包，日志阶段为无锁的 `trash_retained`，而不是立即投递系统回收站；保留期只是自动维护的保险窗口，不需要人类照看。
- 权威删除包先保留 7 天，期间 `restore` 可以撤销删除；达到 7 天后进入系统回收站尽力投递阶段，每 24 小时至多重试一次；再经过 30 天仍未成功时进行最后一次投递，明确失败后先耐久提交永久清理，再删除内部权威包。惰性推进只会让实际保留时间更长，不会更短。
- `recover ROOT` 显式推进全部到期包；其他结构写命令在开始前惰性推进。普通投递失败只产生机器可读警告，不阻止原结构操作；权威包损坏、日志关联矛盾或锁状态无法解释时必须阻止写入。`check` 始终只读，不推进生命周期。
- `restore` 只处理指定包且只在 `now < restore_expires_at` 时允许。它不顺带推进或清理该包，也不维护其他包，避免“尝试找回”触发意外删除。恢复开始前先耐久创建关联恢复日志，再取得写锁、复核期限和冲突、建立双向关联，之后才允许改动受管理文件。
- 恢复在期限前完成耐久认领后，即使执行期间越过期限仍可完成；到期推进先把原日志耐久转为 `trash_handoff_pending` 时则由到期推进获胜。路径已存在、父级变化、结构无效或摘要不符时返回恢复冲突，不覆盖、不改路径、不合并，权威包继续保留但期限照常流逝。
- 永久清理必须先耐久写入 `trash_purge_committed`。在该提交之前，任何崩溃或失败后，页面内容必须仍位于受管理树或有效权威删除包之一；提交之后，缺失或部分残留的权威包属于授权清理的恢复状态。
- `notebook.json` 的 Markdown 结构版本仍为 `format_version: 1`；操作日志字段和阶段发生不兼容变化，因此提升 `journal_version`。第一版不猜测旧日志缺失的创建时间和期限，遇到旧日志明确报告不兼容。

### 2026-07-21 对抗性复评补充的不变量

- 操作日志不能只靠内部字段自报身份。`operation.lock.operation_id`、`operations/<operation-id>/` 目录名和 `journal.operation_id` 必须一致；`T -> R` 与 `R -> T` 还必须同时验证日志类型、阶段和两侧标识。任一缺失或错配都保留现场并报告状态不一致。
- 操作目录必须按新 `operation_id` 排他创建；同名目录即使看似空闲也不能复用或覆盖。预分配在初始日志写入前崩溃留下的空目录没有授权，只能作为未关联分配残留清理。
- 权威删除包的清单和负载不能互相自证。所有路径必须在任何文件读取前验证为边界内规范相对路径；页面和父文件留在草稿根内，负载留在对应权威包目录内。包目录名、清单 `operation_id` 与 `T.operation_id` 必须一致，清单字节和负载摘要必须由原 `T` 日志的耐久 mutation 锚定。
- `R=completed` 只是阶段声明，不足以单独授权删除权威包。工具必须在同一写锁下证明 `R` 的完整 mutation 与 `T` 的包和恢复后文件逐段对应，随后耐久写入 `T=rolled_back`、清理权威包和恢复材料，最后才释放锁；不能证明时保留包和现场。
- `R=rolled_back` 表示恢复没有发生。工具在锁内清除或解释 `T` 的失败认领，保留权威包和原期限。CLI 返回退出码 `3`，JSON 使用 `ok=false`、`result_type=restore`、`phase=rolled_back`，人类输出明确说明恢复未完成且权威包仍保留，不能报告“已恢复”。
- 恢复包装层必须同时收口操作引擎的正常返回和异常路径。只有完整证据证明 `R=completed` 才能提交删除撤销；引擎返回或在回滚后抛出异常而耐久状态已证明 `R=rolled_back` 时，应在同一锁内清除 `T` 的认领、保留权威包和期限并释放锁。若 `R` 仍处于中间态、回滚证据不足或外部变化无法解释，则保留认领、锁和权威包，把 `operation.*` 错误交给 `recover`，不能为了整洁猜测终态。
- 恢复冲突与证据损坏必须分开：原路径已存在、父级发生变化、导航无法唯一恢复或最终结构无效属于 `trash.restore_conflict`；权威包、清单、日志 mutation 或摘要互不匹配属于 `trash.package_corrupt` 或 `operation.inconsistent_state`。前者在已证明安全回滚后可以解关联并退出 `2`，后者必须保留现场，不能伪装成普通业务冲突。
- 两个 `restore` 的正常竞争发生在同一写锁的排他取得处。失败方把自己尚未关联的 `R=allocating` 耐久放弃、清理分配材料，并返回既有 `operation.recovery_required`；不引入无法可靠判断进程存活性的 `trash.restore_busy`。若已经取得锁后才看到 `T` 指向另一个 `R`，那是遗留恢复现场或状态矛盾，不是正常并发；只能放弃自己的未关联日志，绝不能覆盖旧认领。
- 恢复 CLI 语义固定为：成功 `exit 0 / ok=true / phase=completed`；安全回滚结果 `exit 3 / ok=false / result_type=restore / phase=rolled_back`；过期和普通恢复冲突使用标准错误信封并退出 `2`；恢复必需或状态不一致等操作错误退出 `3`。过期命令可以清理自己未关联的分配日志，但不得改变 `T`、权威包、生命周期阶段、移交状态或其他包。
- 检查器必须区分“合法”与“无需注意”：恢复窗口内以及到期但尚未被惰性推进的 `trash_retained` 都是 `INFO`；有有效失败记录的无锁 `trash_handoff_pending` 是合法但值得注意的 `WARNING`，尚未到下一次重试时刻也不能降为信息，到点后只是变为可推进；无锁、无失败记录的 pending 无法由协议解释，属于 `ERROR`。
- 批量生命周期推进必须保留部分成功事实。按 `operation_id` 排序推进时，若 `A` 成功而 `B` 损坏，立即停止且不触碰后续 `C`；`recover --json` 和结构写前置推进的失败信封都要返回 `A` 的结果、`B` 的结构化错误和 `stopped_at_operation_id`，结构写本身不得开始。
- `check --candidate` 打在最终登记草稿根上应只读拒绝并返回稳定问题码 `candidate.already_registered`，提示改用普通 `check`；临时登记态则返回 `adopt.recovery_required`，不能混同为普通已登记状态。
- `journal_version=2`、旧版本拒绝、日志身份绑定、排他预分配和双向关联检查属于同一个日志信封升级，应在整改的第一任务一次完成并一次性更新当前日志 fixture，避免后续任务再次改写同一读取边界。
- 惰性推进产生的普通系统回收站失败在 JSON 中必须是带稳定 `code`、相关 `operation_id` 和 `details` 的结构化警告；本地化人类句子不能充当机器协议，也不能把原结构命令改报失败。
- `adopt` 必须登记一份确切经过候选检查的完整文件清单。它先把清单耐久写入 `notebook.json` 的临时登记态，复核同一清单后才原子发布最终登记标记；临时态不能被普通命令当作已接管，只能由再次执行 `adopt` 确定完成或撤销。不匹配时只删除本次临时标记并保留并发 Markdown 修改，最终标记替换才是登记提交点。
- 新整改采用增量替换：复用操作日志、锁、耐久写入、摘要、回滚、权威包和系统回收站适配器；先加固共享证据边界，再替换旧即时移交状态编排，不重新实现已经验证的底层机制。

## 子主题索引

- [结论汇总](2026-07-18-recursive-discussion-notebook-skill/conclusions.md) — `初版已迁移；2026-07-21 复评补充已同步到正式设计`
- [问题背景与外部启发](2026-07-18-recursive-discussion-notebook-skill/00-background-and-evidence.md)
- [D1 Skill 的职责终点](2026-07-18-recursive-discussion-notebook-skill/01-scope-and-authority.md) — `已确认`
- [D2 页面的最小职责](2026-07-18-recursive-discussion-notebook-skill/02-page-model.md) — `已确认`
- [D3 递归拆分触发条件](2026-07-18-recursive-discussion-notebook-skill/03-recursive-splitting.md) — `已确认`
- [D4 记忆图的关系类型](2026-07-18-recursive-discussion-notebook-skill/04-relationship-types.md) — `已确认`
- [D5 链接格式与反向查找](2026-07-18-recursive-discussion-notebook-skill/05-links-and-backlinks.md) — `已确认`
- [D6 维护与检查](2026-07-18-recursive-discussion-notebook-skill/06-maintenance.md) — `已确认`
  - [D6.1 Agent 与工具的职责边界](2026-07-18-recursive-discussion-notebook-skill/06-1-agent-tool-boundary.md)
  - [D6.2 维护触发与生命周期边界](2026-07-18-recursive-discussion-notebook-skill/06-2-maintenance-triggers-and-hooks.md)
  - [D6.3 自动修改边界](2026-07-18-recursive-discussion-notebook-skill/06-3-write-boundary.md)
- [D7 Skill 的验证方式](2026-07-18-recursive-discussion-notebook-skill/07-validation.md) — `已确认`
- [D8 独立项目与跨平台插件结构](2026-07-18-recursive-discussion-notebook-skill/08-project-and-platform-structure.md) — `已确认`
- [D9 Python、uv 与首次环境搭建](2026-07-18-recursive-discussion-notebook-skill/09-python-uv-and-bootstrap.md) — `已确认`
- [D10 维护 Agent 的启动方式](2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/index.md) — `已确认，第一版范围已收敛`
- [D11 第一版检查器范围](2026-07-18-recursive-discussion-notebook-skill/11-checker-scope.md) — `已确认`
  - [D11.1 600 行后是否还保留 30 KB 阈值](2026-07-18-recursive-discussion-notebook-skill/11-1-capacity-threshold.md) — `已确认`
- [D12 最小机器可识别结构使用什么 Markdown 语法](2026-07-18-recursive-discussion-notebook-skill/12-minimal-structure-syntax.md) — `已确认`
  - [D12.1 最少必须有哪些结构字段](2026-07-18-recursive-discussion-notebook-skill/12-1-required-fields.md) — `已确认`
  - [D12.2 正式关系由哪一侧声明](2026-07-18-recursive-discussion-notebook-skill/12-2-canonical-relation-direction.md) — `已确认`
    - [D12.2.1 检查器如何逐个处理标签](2026-07-18-recursive-discussion-notebook-skill/12-2-1-checker-workflow-by-label.md) — `已确认，流程说明已收口`
  - [D12.3 同一标签有多个目标时怎样表达](2026-07-18-recursive-discussion-notebook-skill/12-3-multiple-relation-targets.md) — `已确认`
  - [D12.4 看起来像标签的未知行怎样处理](2026-07-18-recursive-discussion-notebook-skill/12-4-unknown-labels.md) — `已确认`
  - [D12.5 不同标签的链接能否指向草稿根外](2026-07-18-recursive-discussion-notebook-skill/12-5-link-target-scope.md) — `已确认`
  - [D12.6 一个草稿网络应该有几个产出页](2026-07-18-recursive-discussion-notebook-skill/12-6-output-page-cardinality.md) — `已确认`
  - [D12.7 依赖关系能否形成环](2026-07-18-recursive-discussion-notebook-skill/12-7-dependency-cycles.md) — `已确认`
  - [D12.8 父主题关系是否必须形成一棵根树](2026-07-18-recursive-discussion-notebook-skill/12-8-parent-rooted-tree.md) — `已确认`
  - [D12.9 次级枢纽怎样映射为目录](2026-07-18-recursive-discussion-notebook-skill/12-9-recursive-hub-layout.md) — `已确认`
  - [D12.10 枢纽失去全部子页后是否自动降级](2026-07-18-recursive-discussion-notebook-skill/12-10-empty-hub-collapse.md) — `已确认`
- [D13 工具如何确认一个目录是受管理草稿网络](2026-07-18-recursive-discussion-notebook-skill/13-managed-notebook-root/index.md) — `已确认`
- [讨论日志](2026-07-18-recursive-discussion-notebook-skill/discussion-log.md)

## 已确认结论摘要

- **D1：** skill 只维护临时讨论记忆和可迁移结论清单；正式迁移必须由用户批准。
- **D2：** 使用轻量枢纽页和单问题讨论页；例子仅在复用或膨胀时独立；任何产生独立子问题的页面都提升为次级枢纽，索引拆分递归应用到每一层。
- **D3：** 语义分叉要求拆分；容量提醒为约 600 行或 60 KB，只触发拆分审查；固定扁平的 `conclusions.md` 不适用该提醒。
- **D4：** 正式关系限定为父子、依赖、取代、质疑和产出五组。
- **D5：** 使用标准 Markdown；`被依赖` 和 `来源于` 由工具推导；`被取代` 和 `被质疑` 必须作为标签写回目标页；产出页正文必须汇总结论并链接每个声明 `产出` 的来源页。
- **D6：** 工具检查文件事实且保持只读；Agent 依据明确事实维护草稿；重要变化立即局部写入；结构变化立即机械检查；压缩和恢复后只重新绑定 skill，整体检查与异常恢复显式触发。
- **D7：** skill 文本通过人工阅读和真实场景试用评价；附带脚本作为程序单独验证，不测试自然语言措辞。
- **D8：** 建立独立 Git 项目；仓库根目录提供 Codex 与 Claude Code marketplace，单一插件子目录内保存两份平台清单、共同 skill 和共同运行核心，平台钩子入口分开。Tavern 孵化期需由 setup 输出项目级 Claude 钩子配置并经用户明确接线。
- **D9：** 使用 Python 3.14.x 与 uv；`bootstrap/setup.sh` 和 `bootstrap/setup.ps1` 负责环境搭建入口；两个平台各自在持久插件数据目录维护独立虚拟环境；自动入口不静默安装环境，环境不可用时只诊断并引导 setup；合理复用成熟依赖，不以标准库优先为教条。
- **D10.15：** 压缩后的主 Agent 不自动重读原始历史；正常路径是讨论过程中即时维护，真实缺口才交给独立维护上下文。
- **D10.16：** 同一会话压缩或恢复后，钩子根据显式平台和会话绑定注入短重新绑定信封；完整 skill 通过平台原生机制按需读取，不依赖压缩摘要保存指令；最后页面只作可过期提示。
- **D10.17：** 真正的新会话必须由用户显式选择草稿网络；工作目录只用于提供候选，一次性移交记录暂不进入第一版。
- **D10.18：** 第一版不维护 `processed_through`、`updated`、`no_change` 或永久逐轮回执；无人值守自动恢复成为真实需求后再考虑累计已整理位置。
- **D10.19：** 第一版不注册 `PreCompact`，不保存压缩前输入，也不建立自动启动、等待、后台任务或接管协议；压缩后只重新绑定 skill。
- **D10.20：** 第一版只交付共同 Python 核心和 CLI；MCP 与独立维护 Agent 延后，MCP-only 文件能力保留为未来权限受限执行者的安全前提。
- **D12：** 使用一级标题后的连续标准 Markdown 引用块保存最小机器可识别结构；正文标题、章节顺序和措辞不属于机器协议。
- **D12.1：** 所有页面的 `状态` 使用五个生命周期前缀并解析值内部的全角冒号；纯导航根页或枢纽使用带说明的 `已关闭`，自身记录问题、方案或独立结论时才使用对应状态，确认时必须产出。
- **D12.2：** 父子关系使用子页面 `父主题` 加父页面导航；`取代 / 被取代`、`质疑 / 被质疑` 成对落盘；`被依赖` 和 `来源于` 由工具推导；不新增 `状态依据`。
- **D12.3：** 每个可重复关系标签行只放一个 Markdown 链接目标；通过重复标签表达多个目标；同标签同目标重复属于错误，行顺序没有协议含义。
- **D12.4：** 顶部引用块中“标签名：值”形状的行必须使用已知标签；未知标签和已知标签格式错误属于客观错误；普通引用句子继续忽略。
- **D12.5：** 除产出页外，每个 `已确认` 页面必须恰有一个指向 `conclusions.md` 的 `产出`；`已确认 + 被质疑` 表示结论仍有效但存在未解决挑战，汇总必须显示，用户撤销效力时才改回提案并移除产出。
- **D12.6：** 唯一 `conclusions.md` 永久扁平；正式迁移后仍保留有效结论，但允许压缩为包含结论、草稿来源和正式文档链接的单行紧凑条目。
- **D12.7：** 页面不能依赖自己，任何长度的依赖环都是客观结构错误；检查器报告至少一条闭合路径，但不自动修复或降级关系。
- **D12.8：** 一个草稿目录只讨论一个话题；所有受管理页面必须形成以根 `index.md` 为唯一根的一棵父主题树，父链无法到根属于客观结构错误，不再另报“疑似孤页”。
- **D12.9：** 次级枢纽是完整页面文件主干名目录中的 `index.md`；直接子项放在同一目录并可继续递归，不缩写目录名，也不逐层重复 `pages/`。
- **D12.10：** 历史页面不会因确认、关闭或被取代而离开草稿树；最终状态禁止非根空枢纽，CLI 用 `promote` 升格、用 `collapse` 修复遗留空枢纽，最后一个直接子页离开时由原操作自动降级；根 `index.md` 永不降级。
- **D13：** 新建或显式接管的草稿根必须包含最小 `.discussion-notebook/notebook.json`；第一版只保存 `format_version`，所有写操作仍要求显式根路径和完整结构检查，不保存永久 `notebook_id`。
- **D13.1：** `format_version` 是从 1 开始递增的结构版本；只有当前版本允许写入，已知旧版本必须显式迁移，未知或更高版本要求先升级工具，字段非法时不猜测。
- **D13.2（已由 2026-07-20 修订取代）：** 第一版不提供旧布局迁移适配器或 `migrate` 命令；Agent 把来源重写到新的空候选根，程序以 `check ROOT --candidate` 验证当前格式，并由 `adopt ROOT` 在验证通过后登记。
- **D13.3：** 第一版不建立公开计划编号、批准凭证或独立授权服务；Agent 按 skill 使用 CLI，CLI 保证机械安全，异常和语义判断通过正常的人类反馈处理。

## 案例与外部材料

- 案例总索引：[Codex Tavern Connector 评审讨论索引与摘要](2026-07-13-codex-tavern-connector-review-discussion.tmp.md)
- 膨胀案例：[05 Runner、Connector 与服务端状态](2026-07-13-codex-tavern-connector-review-discussion/05-runner-server-state.md)
- 案例开放项：[06 尚未关闭的评审发现](2026-07-13-codex-tavern-connector-review-discussion/06-open-review-findings.md)
- 外部启发：[Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)

## 原 Connector 讨论暂停点

原协议讨论暂停在 `S8 并发复验`：当前提案是在最终发布事务中重读真正影响判断的有限事实，并以条件写入、数据库约束或受保护查询保证检查与发布之间不再出现空隙；行版本不作为默认机制，只保留为经测量后采用的优化或特殊连续性证明。该提案尚待用户最终确认。

恢复原讨论时，应先读案例总索引和 `05` 顶部当前状态，不应从本文件推导 Tavern 协议结论。
