# 草稿格式参考

本参考描述 draft 管理的草稿目录、机器区域语法、状态与关系规则、CLI 命令和退出码。只在需要准确格式时阅读；语义判断规则见 SKILL.md。

## 目录布局

一个受管理草稿是自包含目录：

```text
topic/
├── index.md            # 唯一根索引
├── conclusions.md      # 唯一、固定、扁平的结论汇总
├── pages/              # 普通讨论页和递归枢纽
│   ├── 01-question.md
│   └── 10-hub/
│       ├── index.md    # 枢纽页：目录名与页面主干名一致
│       └── 10-1-child.md
└── .draft/
    ├── draft.json   # { "format_version": 1 }，最终登记标记，接管/迁移时最后写入
    ├── draft.provisional.json  # 接管过程中的临时登记态，仅 adopt 未决期间存在
    ├── operation.lock  # 仅结构操作期间存在
    ├── operations/     # 未完成操作的日志、恢复副本和暂存内容
    └── trash/outgoing/ # 尚未成功移交系统回收站的删除包
```

- 只管理根 `index.md`、根 `conclusions.md`、`pages/**/*.md`；根下其他 Markdown 属于未管理路径错误。
- `.draft/` 是工具内部区域：普通页面不得链接或写入其中，也不要手工编辑。
- 叶子页面是 `pages/.../name.md`；枢纽是 `pages/.../name/index.md`。非根空枢纽是客观错误。

## 机器区域

每个页面在唯一一级标题之后、正文之前提供一个连续的引用块：

```markdown
# 页面标题

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[另一页面](other.md)
>
> 产出：[结论汇总](../conclusions.md)
```

- 程序只解析这个顶部引用块；正文标题、章节和例子不是机器协议。
- 允许的标签只有 `状态`、`父主题`、`依赖`、`取代`、`被取代`、`质疑`、`被质疑`、`产出`。
- 形如“标签：值”的行必须使用已知标签和全角冒号 `：`；未知标签和错误冒号是解析错误。
- 普通引用句（不带“标签：值”形状）允许存在并被忽略。
- 一个关系标签行恰好一个 Markdown 链接（不能是图片）；同一标签可以重复多行。

## 状态

`状态` 值是单个反引号包裹的前缀，前缀后可用全角冒号加非空说明：

```markdown
> 状态：`已关闭：历史候选，第一版不采用`
```

五个前缀：`待讨论`、`当前提案`、`已确认`、`已取代`、`已关闭`。其他值是解析错误。

## 关系语义与基数

- `父主题`：除根 `index.md` 外每页恰好一个，指向物理父页（`pages/x.md` → 根索引；`pages/hub/index.md` → 其上级索引；`pages/hub/child.md` → `pages/hub/index.md`）。父页正文必须含一个返回子页的可见普通链接。所有页面形成以根索引为唯一根的一棵树。
- `依赖`：禁止自依赖和依赖环。
- `取代` / `被取代`：必须双向成对；带 `被取代` 的页面必须是 `已取代` 状态，`已取代` 页面必须至少一个 `被取代`；取代链禁止成环。
- `质疑` / `被质疑`：必须双向成对；`已确认 + 被质疑` 合法。
- `产出`：除 `conclusions.md` 自身外，每个 `已确认` 页面必须恰好一次指向根级 `conclusions.md`；其他状态不得声明产出；`conclusions.md` 正文必须链接回每个产出来源页。

## 容量

普通页超过约 600 行或 60 KB 时检查器发出审查提醒（不阻止、不报错）；`conclusions.md` 不受容量提醒约束，已迁移的长条目压缩为紧凑条目（结论 + 草稿来源链接 + 正式文档链接）。

## CLI 命令与退出码

```text
draft init ROOT
draft adopt ROOT
draft check ROOT [--candidate] [--json]
draft move ROOT SOURCE TARGET
draft promote ROOT PAGE CHILD [CHILD ...]
draft collapse ROOT HUB
draft trash ROOT PAGE
draft restore ROOT OPERATION_ID
draft recover ROOT
draft bind ROOT --platform claude|codex --session SESSION_ID [--last-page-hint PAGE]
draft unbind --platform claude|codex --session SESSION_ID
```

- `ROOT` 永远显式给出；`SOURCE`、`TARGET`、`PAGE`、`CHILD`、`HUB` 是草稿根内规范相对页面路径（如 `pages/a.md`、`pages/hub/index.md`），不接受目录别名。
- `check ROOT --candidate` 只读检查未登记候选布局，要求不存在任何 `.draft/` 内部状态；对最终登记根返回 `candidate.already_registered`（退出 1），对临时登记态返回 `adopt.recovery_required`（退出 3）。程序不提供来源格式迁移器，旧布局由 Agent 复制到新空候选根后重写。
- `--json` 可放在程序名后或子命令后，输出稳定信封（`ok`、`result_type`、错误 `code`、路径、严重级别）。
- `.draft/` 下的操作日志、权威删除包、锁和绑定文件是工具内部状态，绝不手工编辑；任何手工改动都会被识别为状态损坏。

## 回收生命周期

`trash` 把页面与权威删除包落地为 `trash_retained`，并耐久写入四个绝对 UTC 期限事实（`created_at`、`restore_expires_at = created_at + 7 天`、`purge_eligible_at = restore_expires_at + 30 天`、`next_handoff_attempt_at`，初值等于 `restore_expires_at`）。这些期限不可变，未来默认值变化不追溯改变已有删除包。

```text
trash
  -> trash_retained
     -> restore（now < restore_expires_at 才能开始认领）
        -> 原 trash 日志 rolled_back
     -> 到期推进
        -> trash_handoff_pending
           -> 系统回收站成功 -> trash_handoff_succeeded -> completed, disposition=system_trash
           -> 达到 purge_eligible_at 后最后一次投递明确失败 -> trash_purge_committed -> completed, disposition=expired_purge
```

- 没有后台计时器；到期推进只在 `recover ROOT` 或普通结构写命令（`move`/`promote`/`collapse`/`trash`）自身操作前惰性触发。`restore` 只处理指定包、不推进其他包；`check` 与 `check --candidate` 始终只读、不推进、不改动任何字节。
- 普通投递失败节流 24 小时（`next_handoff_attempt_at = attempted_at + 24 小时`）；达到 `purge_eligible_at` 后不再等待节流，立即进行最后一次投递。`trash_purge_committed` 是唯一永久清理提交点，在此之前页面内容必须仍位于受管理树或有效权威删除包之一。
- 批量推进按 `operation_id` 排序；前项成功而当前项损坏时，公开信封保留前项结果与 `stopped_at_operation_id`，后续项不读取、不触碰。`recover --json` 用 `ok=false`、`result_type=recover`、`results=[...]`、`error={code,operation_id,paths,details}`、`stopped_at_operation_id` 表达部分完成。结构写前置推进失败用 `ok=false`、`result_type=error`、`lifecycle_results=[...]`、同一 `error` 和停止标识；此时请求的结构写不得开始。

## 恢复退出与错误矩阵

| 情形 | 退出码 | JSON |
| --- | ---: | --- |
| 恢复完成 | `0` | `ok=true`、`result_type=restore`、`phase=completed` |
| 恢复回滚 | `3` | `ok=false`、`result_type=restore`、`phase=rolled_back`（权威包仍保留） |
| `trash.restore_expired` | `2` | 标准错误信封、`result_type=error` |
| `trash.restore_conflict` | `2` | 标准错误信封、`result_type=error` |
| 恢复需要恢复/状态不一致 | `3` | 标准操作错误信封 |

- 非检查命令的 JSON 把结构化生命周期警告渲染为对象 `{"code","operation_id","details"}`（如 `trash.handoff_failed`），警告文本只在人类输出中生成；`trash` 成功为 `phase=trash_retained` 并带 `restore_expires_at`、`purge_eligible_at`。
- 权威删除包、清单、原 `T` mutation、恢复日志或内容摘要互不匹配不是普通恢复冲突，而是 `trash.package_corrupt` 或 `operation.inconsistent_state`，保留现场。

## 检查器严重级别

检查器输出四档：`errors`（客观结构损坏）、`warnings`（合法但需注意的运行状态，如投递失败）、`infos`（合法未结束的生命周期事实，如 `trash_retained`）、`reviews`（容量等审查提醒）。回收生命周期诊断固定为：恢复窗口内或已过期但未推进的 `trash_retained` 是 `INFO`；带有效失败记录的无锁 `trash_handoff_pending` 是 `WARNING`；无有效失败记录的 `trash_handoff_pending` 或包缺失/损坏是 `ERROR`。警告不使 `check` 非零退出，错误才退出 1。

退出码：

| 码 | 含义 |
| --- | --- |
| 0 | 成功；`check` 无客观错误；回收站移交可以处于待移交并带警告 |
| 1 | `check` 完成并发现客观结构错误 |
| 2 | 参数、根、路径、版本或领域前置条件无效 |
| 3 | 需要恢复、锁/日志损坏、外部修改或不安全恢复冲突 |
| 4 | 运行环境、安装或平台适配失败 |
