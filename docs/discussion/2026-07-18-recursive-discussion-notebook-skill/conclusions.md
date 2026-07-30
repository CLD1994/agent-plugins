# 递归讨论草稿维护 Skill：结论汇总

> 状态：`已迁移为正式设计，等待评审`
>
> 父主题：[递归讨论草稿方法论与 Skill 设计](../2026-07-18-recursive-discussion-notebook-skill.tmp.md)

本页是当前草稿网络唯一、固定、扁平的产出页。它汇总已经确认且当前未被取代的结论，供后续生成正式设计文档或实现计划时渐进读取；它本身仍是临时草稿，不会越过用户批准直接成为正式规范。

用户已经明确授权迁移，当前正式产物为 [Discussion Notebook 正式设计](../../../.claude/skills/discussion-notebook/docs/design.md)。正式设计尚待用户评审，尚未进入实现计划。

## 1. 目标与职责边界

- 长讨论受上下文容量限制，真实案例已经证明“总索引 + 不再递归拆分的长子页”仍会膨胀。这个 skill 的目标是把讨论维护成可导航、可追溯、可继续拆分的外部记忆网络。[问题背景与外部启发](00-background-and-evidence.md)
- skill 只维护临时讨论记忆、关系和可迁移结论，不自行修改正式规范。正式迁移必须由用户明确批准。[D1：Skill 的职责终点](01-scope-and-authority.md)

## 2. 页面、拆分与关系

- 使用轻量枢纽页组织导航，单问题页保存一个可独立评审的问题。页面继续产生独立子问题时，它可以递归升级为次级枢纽。[D2：页面的最小职责](02-page-model.md)
- 语义分叉必须拆分；约 600 行或 60 KB 只触发拆分审查，不代替语义判断。固定的 `conclusions.md` 是扁平索引，不受容量拆分提醒约束。[D3：递归拆分触发条件](03-recursive-splitting.md)
- 正式关系只保留父子、依赖、取代、质疑和产出五组；其他关联使用普通 Markdown 链接。[D4：记忆图的关系类型](04-relationship-types.md)
- 文档只使用标准 Markdown。`被依赖` 和 `来源于` 由工具推导；会直接影响页面当前理解的 `被取代` 和 `被质疑` 必须显式写回目标页。[D5：链接格式与反向查找](05-links-and-backlinks.md)

## 3. Agent 与工具怎样协作

- Agent 理解对话含义并决定该记录、拆分、改名或回收什么；工具只验证和执行可机械证明的文件操作，不替代语义判断。[D6：维护与检查](06-maintenance.md)、[D6.1：Agent 与工具的职责边界](06-1-agent-tool-boundary.md)
- 用户确认、否定、提出关键反例或打开新分支时，Agent 应立即局部维护草稿。压缩或恢复后只需重新绑定 skill；整体健康检查和异常恢复显式触发。[D6.2：维护触发与生命周期边界](06-2-maintenance-triggers-and-hooks.md)
- 明确事实允许 Agent 直接更新页面及关系；有歧义时保留提醒并询问用户。工具负责路径边界、链接修复、冲突检查和可恢复落盘。[D6.3：自动修改边界](06-3-write-boundary.md)
- 不为 skill 的自然语言建立固定措辞、固定标题或全文快照测试。通过人工阅读和真实长讨论试用判断效果；检查程序只测试自己的机械行为。[D7：Skill 的验证方式](07-validation.md)

## 4. 独立项目与运行环境

- skill 建成独立 Git 项目，同时兼容 Codex 和 Claude Code。仓库提供两平台所需的清单和入口，但共同使用一份 skill 与 Python 运行核心，运行时必需内容都放在实际插件目录内。Tavern 孵化期 `.claude/skills/` 只负责发现 skill；Claude 自动重新绑定需由 setup 输出项目级钩子配置并经用户明确接线。[D8：独立项目与跨平台插件结构](08-project-and-platform-structure.md)
- 运行时采用 Python 3.14.x 与 uv。skill 负责通过 `bootstrap/setup.sh` 和 `bootstrap/setup.ps1` 检查并指导安装环境；Codex 与 Claude Code 在各自持久数据目录维护独立虚拟环境。自动入口不静默安装软件；依赖按真实价值选择，不把“只用标准库”当成目标。[D9：Python、uv 与首次环境搭建](09-python-uv-and-bootstrap.md)

## 5. 第一版运行范围

- 第一版只交付 skill、共同 Python 核心、CLI、压缩后重新绑定入口、结构操作日志、回收站和检查器；不交付 `PreCompact`、MCP、无界面维护 Agent、专用维护会话、后台任务或任务接管协议。[D10：维护 Agent 的启动方式](10-maintenance-agent-launch/index.md)
- 一个草稿网络必须完整位于同一个自包含目录中，这个目录就是唯一可写根。新建、接管和迁移必须显式选择，现有历史草稿不自动迁移。[D10.4：最小权限与草稿目录边界](10-maintenance-agent-launch/10-4-permissions-and-notebook-root.md)
- 改名、移动、枢纽升降级和回收必须通过共同核心提供的 CLI 窄命令完成。改名和移动自动修复等价链接；回收只自动解除父级导航，其他入站语义引用必须先由 Agent 处理。第一版不为这些能力建立 MCP 服务。[D10.5：结构变化如何安全落盘](10-maintenance-agent-launch/10-5-structural-operations.md)
- 多文件操作把跨平台单写者锁、操作日志、恢复副本和暂存内容全部放在草稿网络的 `.discussion-notebook/` 内。`recover ROOT` 只读取这份共同事实；崩溃后只有在能证明安全时才继续或回滚，不能按锁年龄猜测陈旧。Git 可以是额外保险，但不是正确性前提。[D10.6：多文件结构操作的崩溃恢复](10-maintenance-agent-launch/10-6-structural-operation-recovery.md)
- 删除先进入插件自己的临时回收区。整轮检查通过后，权威删除包继续留在插件内，只把完整副本送入系统回收站；适配器成功返回并耐久记录后才清理权威包。CLI 恢复只服务仍处于 `trash_handoff_pending` 的删除包；成功移交后不承诺自动恢复或重新纳入。[D10.7：回收站的权威位置与崩溃语义](10-maintenance-agent-launch/10-7-trash-authority.md)
- 系统回收站移交失败不推翻已经完成的草稿维护：保留权威删除包、明确警告并记录待重试状态，后续只重做文件移交；外部接口没有幂等回执，崩溃窗口允许产生带同一 `operation_id` 的重复回收站条目，但不允许丢失或猜测成功。这段待移交窗口也允许显式恢复。[D10.8：系统回收站移交失败的放行边界](10-maintenance-agent-launch/10-8-trash-handoff-failure.md)
- 路径校验、结构操作、日志、回收站和检查规则只在共同 Python 核心实现；第一版由 CLI 做参数转换和输出，不预先创建没有调用者的接口层。[D10.10：CLI 与 MCP 的共同核心](10-maintenance-agent-launch/10-10-cli-mcp-shared-core.md)
- 第一版不复制或持续镜像平台转录，不保存 `captured_through` 或 `processed_through`，也不自动解析转录寻找语义缺口；原始转录只在已知异常和显式恢复时作为证据。[D10.14：压缩前是否复制对话证据](10-maintenance-agent-launch/10-14-deterministic-precompact-capture.md)
- 压缩后的主 Agent 不自动重读历史。正常路径是在讨论语义仍然新鲜时即时维护；真实缺口才进入独立的显式恢复过程。[D10.15：压缩后语义整理的上下文成本](10-maintenance-agent-launch/10-15-postcompact-context-cost.md)
- 同一会话压缩或恢复后，钩子根据显式的平台和会话绑定注入短重新绑定信封，要求 Agent 重新确认或读取完整 skill；`last_page_hint` 只作可过期的导航提示，不是阅读游标。[D10.16：压缩与会话迁移后的 skill 重新绑定](10-maintenance-agent-launch/10-16-skill-reactivation.md)
- 真正的新会话必须由用户显式选择草稿网络。工作目录只能展示候选，不能静默绑定；一次性会话移交记录暂不进入第一版。[D10.17：全新会话怎样取得草稿绑定](10-maintenance-agent-launch/10-17-new-session-binding.md)
- 第一版不维护每轮 `updated`、`no_change` 或累计已整理位置；只有真实出现无人值守恢复需求后，才重新评估最小累计位置。[D10.18：是否需要每轮可靠标记](10-maintenance-agent-launch/10-18-semantic-maintenance-checkpoint.md)
- 第一版不注册 `PreCompact`，不建立压缩前保存、等待或后台维护流程；压缩生命周期只负责压缩后重新绑定 skill。[D10.19：第一版是否需要压缩前钩子](10-maintenance-agent-launch/10-19-precompact-necessity.md)
- 第一版不交付 MCP 和独立维护 Agent。未来若真实需要权限受限的独立执行者，MCP 才作为共同核心的薄适配层，并成为该执行者唯一的文件能力。[D10.20：第一版 MCP 与独立维护 Agent 范围](10-maintenance-agent-launch/10-20-mcp-and-maintenance-agent-scope.md)

## 6. 检查器边界

- 检查器只报告可机械证明的结构事实和容量等审查提醒，不判断结论是否正确、状态是否应该改变，也不自动修改页面。[D11：第一版检查器范围](11-checker-scope.md)
- 普通页面在约 600 行或 60 KB 时提醒审查；两个阈值任一达到即可提醒。提醒不是错误，`conclusions.md` 例外。[D11.1：容量阈值](11-1-capacity-threshold.md)

## 7. Markdown 结构协议

- 机器结构只存在于一级标题后的连续 Markdown 引用块；正文标题、章节顺序和措辞不属于协议。[D12：最小机器可识别结构](12-minimal-structure-syntax.md)
- 每页必须有一个 `状态`，解析器读取状态值内部的冒号。状态只描述页面自身：纯导航根页或枢纽使用带说明的 `已关闭`，不随子页状态变化；若自身记录问题、方案或独立确认结论，再分别使用相应状态，确认时同样必须产出。除根 `index.md` 外，每页必须有且只有一个 `父主题`。[D12.1：最少结构字段](12-1-required-fields.md)
- 子页用 `父主题` 声明父子关系，父页正文提供返回导航；`取代 / 被取代` 与 `质疑 / 被质疑` 成对保存，`被依赖` 和 `来源于` 由工具推导。[D12.2：关系声明方向](12-2-canonical-relation-direction.md)
- 检查器先逐页读取顶部结构块，再检查链接、父主题树和各类关系的一致性。它不从正文猜关系，也不自动修复。[D12.2.1：检查器逐标签流程](12-2-1-checker-workflow-by-label.md)
- 同一可重复关系的每一行只放一个 Markdown 链接目标；多个目标通过重复标签表达。同标签同目标重复属于错误，行顺序没有协议含义。[D12.3：多目标关系语法](12-3-multiple-relation-targets.md)
- 顶部结构块中形似“标签名：值”的行必须使用已知标签；未知标签或已知标签的错误格式属于客观错误，普通引用句仍可自由书写。[D12.4：未知标签处理](12-4-unknown-labels.md)
- 所有结构关系都必须指向同一草稿根内的受管理页面；普通正文链接可以引用外部材料。除产出页自身外，每个 `已确认` 页面必须恰有一个指向 `conclusions.md` 的 `产出`。`已确认` 可以同时 `被质疑`：结论仍有效但汇总必须显示挑战；用户撤销当前效力时才改回 `当前提案` 并移除产出。[D12.5：结构链接边界](12-5-link-target-scope.md)
- 每个草稿网络只有一个根级 `conclusions.md`。它永久保持扁平，不建立分卷或子产出页；结论迁入正式文档后仍保留，但可以压缩为包含结论、草稿来源和正式文档链接的单行紧凑条目。[D12.6：唯一产出页](12-6-output-page-cardinality.md)
- 页面不能依赖自己，任何长度的依赖环都是客观结构错误；检查器报告闭合路径，但不替 Agent 修复。[D12.7：禁止依赖环](12-7-dependency-cycles.md)
- 所有受管理页面必须形成以根 `index.md` 为唯一根的一棵父主题树；任一父链无法到根都属于错误。[D12.8：唯一父主题树](12-8-parent-rooted-tree.md)
- 次级枢纽使用完整页面文件主干名作为目录名，枢纽本身为目录内 `index.md`，直接子项放在同一目录，布局可继续递归。[D12.9：递归枢纽目录](12-9-recursive-hub-layout.md)
- 最终状态禁止非根空枢纽。CLI 用 `promote` 显式升格叶子、用 `collapse` 修复遗留空枢纽；最后一个直接子页离开时，普通移动或回收在同一次可恢复操作中自动降级并修复链接。根 `index.md` 永不降级。[D12.10：空枢纽降级](12-10-empty-hub-collapse.md)

## 8. 受管理草稿根

- 新建或显式接管的草稿根必须包含 `.discussion-notebook/notebook.json`。第一版只保存 `format_version`；写操作仍要求显式根路径和完整结构检查，不保存永久 `notebook_id`。[D13：受管理草稿根](13-managed-notebook-root/index.md)
- 只有当前 `format_version` 可以写入。已知旧版本必须显式迁移；未知或更高版本要求先升级工具；非法字段不靠目录形状猜测。[D13.1：格式版本兼容](13-managed-notebook-root/13-1-format-version-compatibility.md)
- `init` 只创建空白结构，`adopt` 只验证并登记已经符合当前格式的目录，`migrate SOURCE --from FORMAT --to ROOT` 才允许改写明确指定的旧布局或旧版本。第一版 `superpowers-sidecar-v0` 让已经符合当前状态语法的值恒等通过，并在新目标全部检查通过后最后写入 v1 登记文件。[D13.2：新建、接管与迁移边界](13-managed-notebook-root/13-2-init-adopt-migrate-boundaries.md)

## 9. 第一版明确不做

- 不建立自然语言快照测试；不提前实现 MCP、独立维护 Agent、专用维护会话、后台任务、逐轮可靠标记或压缩前处理。
- 不建立公开计划编号、批准令牌、计划期限、签名凭证或独立授权服务。Agent 按 skill 使用 CLI，CLI 保证机械安全；失败、歧义和语义判断通过正常对话反馈。[D13.3：关闭专门批准协议](13-managed-notebook-root/13-3-user-approval-plan-binding.md)
- 不让工具替 Agent 判断语义，不用最近修改时间猜测草稿网络，不把 Git 当作结构操作事务，也不自动迁移历史草稿。

## 10. 收口结果

当前没有仍会阻塞第一版的设计问题。本页已经迁移为正式设计；下一阶段是在用户评审通过后生成实现计划。

实现过程中出现的新问题应以真实平台行为、失败案例和试用反馈为依据，回到草稿网络逐项讨论，不提前扩展假想协议。
