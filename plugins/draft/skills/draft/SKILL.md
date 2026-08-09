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
- move 移动页面
- promote 升格页面为枢纽
- collapse 降级枢纽为普通页面
- trash 回收页面为保留态
- restore 恢复被回收的页面
- recover 推进回收生命周期
- create 创建叶子页面
- status set 修改页面状态
- relation add / relation remove 修改正式关系
- generate-conclusions 按需生成结论页
- query page / links / backlinks / relations / status / recoverable 紧凑结构查询

使用 `uvx --isolated --from "<skill_root>/scripts/runtime" draft COMMAND --help` 来获得更多信息

### CLI 注意事项

- `ROOT` 永远显式给出；`SOURCE`、`TARGET`、`PAGE`、`CHILD`、`HUB` 是草稿根内规范相对页面路径（如 `pages/a.md`、`pages/hub/index.md`），不接受目录别名。
- `check ROOT --candidate` 只读检查未登记候选布局，要求不存在任何 `.draft/` 内部状态。程序不提供来源格式迁移器，旧布局由 Agent 复制到新空候选根后重写。

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
- 已经完全符合当前格式的未登记目录 -> `draft check <ROOT> --candidate`，然后 `draft adopt ROOT`
- 任意旧布局 -> 复制到新的空候选根，由 Agent 按当前格式和语义重写；具体规则见 [migration.md](references/migration.md)

## 即时维护循环

每次用户确认、否定、提出关键反例或打开新分支时，在语义仍然新鲜时执行：

1. 读根索引和当前相关页面，判断受影响的最小页面集合；
2. 只改普通内容 -> 直接编辑 Markdown（标题、正文、例子都自由）；
3. 改名、移动、升格、降级、回收 -> 调用对应 CLI 命令，一次命令完成，不拆成手工步骤；
4. 更新受影响的正式关系（父主题、依赖、取代/被取代、质疑/被质疑）和页面状态；
5. 运行 `draft check ROOT`；有客观错误立即修复，有语义歧义立即询问用户。

`conclusions.md` 是讨论结束时按需生成的机器产物，不实时维护。日常讨论中不手动写入
或逐条改写结论列表；已确认页面由 `confirmed` 状态标记。只有讨论结束且与用户确认后
才运行 `draft generate-conclusions ROOT` 生成。

## 最终工作流

讨论结束并与用户确认后：

1. 运行 `draft generate-conclusions ROOT`，生成 `proposed` 状态的结论汇总页；
2. 根据这份最新结论页产出设计文档；
3. 产出完成后运行 `draft status set ROOT conclusions.md confirmed`，表示已据最新结论完成产出。

查询选择：用 `draft query page ROOT PAGE` 做路由，用窄集合命令
（`query links`/`backlinks`/`relations`/`status`/`recoverable`）获取事实，用
`rg` 搜索正文。不要要求代理在普通编辑过程中维护结论页。

## 拆分时机

- 语义触发为主：子问题获得独立状态生命周期（需要单独确认/否定/关闭）就拆成子页面；
- 容量红线为辅：页面约 600 行或 60 KB 时检查器发出审查提醒--提醒只是提醒，由你判断是否拆；
- 讨论页派生出多个子主题时升格为枢纽（`promote`）；枢纽失去最后子页时移动/回收命令会自动降级，不留空枢纽。

## 回收与恢复

- `trash` 把页面和权威删除包落地为 7 天保留态（`trash_retained`），不立即移交系统回收站；有其他入站引用时命令拒绝并列出阻碍，先人工解除或改指再回收。
- `trash` 后 7 天内可 `draft restore ROOT OPERATION_ID` 恢复页面；到期后的系统回收站移交和最终清理由 `draft recover ROOT` 或普通结构写命令惰性推进，Agent 不需要维护计时器。最终清理（`trash_purge_committed`）只在到达清理期限后最后一次投递明确失败时发生，是永久删除授权。
- `restore` 回滚不是成功：恢复未完成时 `exit 3`、JSON `ok=false`、`phase=rolled_back`，权威删除包仍保留；遇到 `exit 3` 时不要报告"已恢复"。
- 移交明确失败只产生警告并继续原命令；状态损坏（`operation.inconsistent_state` 等）才停止写入。结构命令提示需要恢复时，先运行 `draft recover ROOT` 做机械恢复，不要删除锁或日志目录；恢复结果无法解释或需要取舍时再停止并询问用户（见硬停止条件）。
- `.draft/` 下的操作日志、权威删除包和锁是工具内部状态，绝不手工编辑。

## 硬停止条件

遇到以下情况立即停止并询问用户，不要在代码或文本里猜测：

- 状态、关系或拆分涉及语义判断而非机械修正；
- `check` 报告你无法解释的客观错误；
- `recover` 无法完成机械恢复，或其输出提示 `operation.journal_corrupt`、`operation.inconsistent_state`、`operation.external_change`；注意：结构命令首次提示 `operation.recovery_required` 时先按上一节运行 `recover` 做机械恢复，只有恢复本身失败或结果无法解释才到这里停止；
- `trash` 列出你不确定能否解除的入站阻碍；
- uvx 执行失败，且按 uv 自身的诊断处理后仍无法解决。

## 检查器严重级别

检查器输出四档：

- `errors`客观结构损坏
- `warnings`合法但需注意的运行状态，如投递失败
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
