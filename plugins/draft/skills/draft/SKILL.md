---
name: draft
description: Use when a design or protocol discussion is becoming long, recursive, branched, vulnerable to context compact, or needs a durable Markdown memory graph shared with a human reader.
---

# Draft

把长期、递归、分叉的设计讨论维护成一个自包含的 Markdown 草稿。

## 核心命令入口

始终通过 `uvx` 从当前 skill 的 runtime 包源执行核心 `draft` 命令。`<skill_root>` 使用宿主在加载本 skill 时注入的当前 skill 根，不搜索或猜测其他副本。

```text
uvx --isolated --from "<skill_root>/scripts/runtime" draft --version
uvx --isolated --from "<skill_root>/scripts/runtime" draft init ROOT
uvx --isolated --from "<skill_root>/scripts/runtime" draft check ROOT
uvx --isolated --from "<skill_root>/scripts/runtime" draft move ROOT SOURCE TARGET
uvx --isolated --from "<skill_root>/scripts/runtime" draft promote ROOT PAGE CHILD [CHILD ...]
uvx --isolated --from "<skill_root>/scripts/runtime" draft collapse ROOT HUB
uvx --isolated --from "<skill_root>/scripts/runtime" draft trash ROOT PAGE
uvx --isolated --from "<skill_root>/scripts/runtime" draft restore ROOT OPERATION_ID
uvx --isolated --from "<skill_root>/scripts/runtime" draft recover ROOT
```

`--version`（即 `setup` 工作流）只验证 uvx 能从当前 skill 的 runtime 包源构建并运行 `draft`，不是其他命令的前置条件。每个普通命令都会在执行前完成同一套 uv 工具环境解析，因此不先跑 `setup` 也能工作。

uv 启动失败（Python 发现、包索引访问、依赖解析、构建或缓存错误）直接保留 uv 的标准错误输出和退出码；外层没有启动器将其转换为 Draft 错误码。调用方不得把 uvx 启动失败解释为 Draft 检查失败、目标不存在或命令成功。首次依赖解析可能从配置的包索引下载第三方运行依赖和构建依赖；后续运行复用 uv 缓存中的工具环境。

## 第一性边界

- **你负责语义：** 讨论意味着什么、问题是否已确认、质疑是否成立、页面该不该拆分。
- **CLI 负责结构：** 链接完整性、父主题树、状态前缀、关系配对、安全移动/升格/降级/回收、崩溃恢复。
- 不要用 shell、编辑器批量命令直接移动、改名或删除受管理页面；结构变化一律通过 CLI。
- 检查器全绿只证明没有机械损坏，不证明讨论语义正确。

## 何时建立草稿

出现以下任一情况时建立或继续一个草稿：

- 讨论明显变长、出现分支或递归子问题；
- 上下文即将被压缩，或会话可能在多轮之间延续；
- 需要让人类用 Obsidian 等工具直接阅读讨论记忆。

入口选择：

- 全新讨论 -> `draft init ROOT`；
- 已经完全符合当前格式的未登记目录 -> `draft check ROOT --candidate`，然后 `draft adopt ROOT`；
- 任意旧布局 -> 复制到新的空候选根，由 Agent 按当前格式和语义重写；程序不提供来源格式迁移器。
- 三个入口绝不互相自动降级；失败就停止并询问用户，不要改试另一个入口。
- 最终登记草稿只用普通 `check ROOT`；`check ROOT --candidate` 对最终登记根只读返回 `candidate.already_registered`（退出 1），对临时登记态返回 `adopt.recovery_required`（退出 3）。

## 即时维护循环

每次用户确认、否定、提出关键反例或打开新分支时，在语义仍然新鲜时执行：

1. 读根索引和当前相关页面，判断受影响的最小页面集合；
2. 只改普通内容 -> 直接编辑 Markdown（标题、正文、例子都自由）；
3. 改名、移动、升格、降级、回收 -> 调用对应 CLI 命令，一次命令完成，不拆成手工步骤；
4. 更新受影响的正式关系（父主题、依赖、取代/被取代、质疑/被质疑、产出）和 `conclusions.md`；
5. 运行 `draft check ROOT`；有客观错误立即修复，有语义歧义立即询问用户。

已确认的结论必须当日或当轮写入 `conclusions.md`；长条目在正式迁移到设计文档后可以压缩成紧凑条目（结论本身 + 草稿来源链接 + 正式文档链接），结论被正式取代时才移除。

## 状态决策表

每个页面恰好一个状态前缀，前缀后可加全角冒号和非空说明：

| 前缀 | 何时使用 |
| --- | --- |
| `待讨论` | 问题已记录，尚无当前方案 |
| `当前提案` | 方案正在评审，尚未确认 |
| `已确认` | 已接受，属于当前有效结论；必须声明 `产出` 指向根级 `conclusions.md` |
| `已取代` | 已被另一页面正式取代，只保留历史；必须声明 `被取代` |
| `已关闭` | 讨论结束但没有当前有效结论；纯导航的根索引和枢纽页也用它（`已关闭：纯导航页，不表达独立结论`） |

- 状态描述页面自身，不汇总后代；不要因为子页已确认就把导航页标为已确认。
- `已确认` 与 `被质疑` 可以共存；若质疑使结论失效，把页面改回 `当前提案` 并移除 `产出`。

## 拆分时机

- 语义触发为主：子问题获得独立状态生命周期（需要单独确认/否定/关闭）就拆成子页面；
- 容量红线为辅：页面约 600 行或 60 KB 时检查器发出审查提醒--提醒只是提醒，由你判断是否拆；
- 讨论页派生出多个子主题时升格为枢纽（`promote`）；枢纽失去最后子页时移动/回收命令会自动降级，不留空枢纽。

## 回收与恢复

- `trash` 把页面和权威删除包落地为 7 天保留态（`trash_retained`），不立即移交系统回收站；有其他入站引用时命令拒绝并列出阻碍，先人工解除或改指再回收。
- `trash` 后 7 天内可 `draft restore ROOT OPERATION_ID` 恢复页面；到期后的系统回收站移交和最终清理由 `draft recover ROOT` 或普通结构写命令惰性推进，Agent 不需要维护计时器。最终清理（`trash_purge_committed`）只在到达清理期限后最后一次投递明确失败时发生，是永久删除授权。
- `restore` 回滚不是成功：恢复未完成时退出 3、JSON `ok=false`、`phase=rolled_back`，权威删除包仍保留；遇到 exit 3 时不要报告"已恢复"。
- 移交明确失败只产生警告并继续原命令；状态损坏（`operation.inconsistent_state` 等）才停止写入。结构命令提示需要恢复时，先运行 `draft recover ROOT` 做机械恢复，不要删除锁或日志目录；恢复结果无法解释或需要取舍时再停止并询问用户（见硬停止条件）。
- `.draft/` 下的操作日志、权威删除包和锁是工具内部状态，绝不手工编辑。

## 硬停止条件

遇到以下情况立即停止并询问用户，不要在代码或文本里猜测：

- 状态、关系或拆分涉及语义判断而非机械修正；
- `check` 报告你无法解释的客观错误；
- `recover` 无法完成机械恢复，或其输出提示 `operation.journal_corrupt`、`operation.inconsistent_state`、`operation.external_change`；注意：结构命令首次提示 `operation.recovery_required` 时先按上一节运行 `recover` 做机械恢复，只有恢复本身失败或结果无法解释才到这里停止；
- `trash` 列出你不确定能否解除的入站阻碍；
- uvx 启动失败（uv 的 Python 发现、包索引访问、依赖解析、构建或缓存错误），且按 uv 自身的诊断处理后仍无法解决。

## 参考资料

- 需要准确的目录布局、机器区域语法、状态与关系基数、CLI 命令和退出码时，读 [references/draft-format.md](references/draft-format.md)。
