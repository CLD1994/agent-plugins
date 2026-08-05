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
draft bind ROOT --session SESSION_ID [--last-page-hint PAGE]
draft unbind --session SESSION_ID
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

## 检查诊断输出

`draft check ROOT` 在普通模式按确定性顺序遍历同一份 `CheckReport`，每条诊断渲染为单行编译器式格式：

```text
<path>[:<line>]: <级别> <code>：<客观事实及关键对象>
```

- `path` 是草稿根内规范 POSIX 相对来源路径；真正根级事实（`path=None`）显示为 `<ROOT>`，不输出空字符串。
- `line` 是与编辑器一致的一基行号；存在具体结构行时显示为 `path:line`，不存在具体来源行时只显示 `path`，绝不伪造 `0` 或 `1`。
- 四档中文级别固定为 `错误`、`警告`、`信息`、`提醒`，分别对应 `error`、`warning`、`info`、`review`。
- 参数中的换行符和其他控制字符必须转义，不能破坏单行边界。
- 不按文件折叠、不合并来源不同的诊断、不输出颜色、对齐空格或源码摘录。

全部诊断之后输出四档计数摘要：

```text
检查完成：2 个错误，1 个警告，0 个信息，0 个提醒。
```

四档全部为空时输出全绿句子：

```text
检查通过：未发现客观错误。
```

`--json` 保留 `ok`、`result_type=check` 和 `errors`、`warnings`、`infos`、`reviews` 四个分类数组。每个条目统一包含五字段：`code`、`severity`（与外层分类一致）、`path`（根级为 `null`）、`line`（无来源行为 `null`）、`details`（错误码判别的稳定原始参数数组）。普通输出和 JSON 使用同一份排序后的 `CheckReport`，条目数量、顺序和事实一致；两种模式都只读，不修改草稿任何字节，退出码不变（只有 `errors` 使 `check` 返回 `1`）。

### 诊断契约表

位置模式：`line` 要求 `path` 和正整数 `line`；`file` 要求 `path` 且 `line=None`；`line|file` 允许二者；`root` 要求 `path=None` 和 `line=None`。`details` 只保存错误码判别的稳定字符串参数，不保存预渲染中文整句；环路只在渲染时用 ` -> ` 连接，`details` 保存有序闭合路径（首尾重复）。

| Code | Location | `details` schema | Human fact |
| --- | --- | --- | --- |
| `candidate.already_registered` | `file` | `()` | `目标已经登记，--candidate 仅适用于未登记候选` |
| `candidate.internal_state` | `file` | `()` | `候选目录包含 Draft 内部状态` |
| `format.missing` | `file` | `()` | `登记文件不存在` |
| `format.invalid` | `file` | `()` | `登记文件格式无效` |
| `format.unsupported` | `file` | `()` | `登记文件版本不受支持` |
| `root.missing_file` | `file` | `()` | `必需文件或目录不存在` |
| `page.unmanaged_path` | `file` | `()` | `Markdown 文件不在受管理路径中` |
| `page.missing_title` | `file` | `()` | `缺少一级标题` |
| `page.missing_machine_block` | `file` | `()` | `一级标题后缺少机器引用块` |
| `page.missing_status` | `file` | `()` | `机器引用块缺少“状态”` |
| `page.duplicate_status` | `line` | `()` | `重复声明“状态”` |
| `page.unknown_label` | `line` | `(label,)` | `机器引用块包含未知标签：{label}` |
| `page.invalid_status` | `line` | `(not_single_code_span,)` / `(empty_explanation,)` / `(unknown_prefix, prefix)` | 分别说明状态值不是单一反引号值、说明为空、前缀无效 |
| `page.invalid_relation` | `line` | `(link_cardinality_or_image, label)` / `(extra_content, label)` | 分别说明关系必须恰有一个非图片链接、链接外有额外内容 |
| `page.parse_error` | `line\|file` | `(link_destination_missing,)` / `(image_destination_missing,)` / `(source_alignment_failed, destination)` / `(lazy_continuation,)` / `(machine_block_map_missing,)` | 对应说明解析器无法取得目标、无法对齐源码、出现懒惰延续行或缺少行映射 |
| `link.missing` | `line` | `(literal_destination, normalized_target)` | 相同时 `链接目标不存在：{normalized_target}`；不同时说明 `{literal_destination} 解析到 {normalized_target} 后不存在` |
| `link.escape` | `line\|file` | `(destination, literal_destination)` / `(managed_path, relative_path)` | 分别说明链接越出草稿根、受管理路径是越界符号链接 |
| `path.symlink_escape` | `file` | `()` | `路径包含越出受管理树的符号链接` |
| `relation.duplicate` | `line` | `(label, target)` | `{label} 重复指向 {target}` |
| `parent.root_declared` | `line` | `()` | `根页面不得声明“父主题”` |
| `parent.missing` | `file` | `()` | `缺少“父主题”声明` |
| `parent.multiple` | `line` | `()` | `声明了多个“父主题”` |
| `layout.parent_mismatch` | `line` | `(declared_target, physical_target)` | `父主题声明为 {declared_target}，物理父主题应为 {physical_target}` |
| `parent.cycle` | `file` | `(path_1, ..., path_1)` | `父主题形成环路：path_1 -> ... -> path_1` |
| `parent.unreachable` | `file` | `()` | `父主题链无法到达根页面` |
| `parent.navigation_missing` | `file` | `(child,)` | `正文缺少对子页面 {child} 的导航链接` |
| `hub.empty` | `file` | `()` | `非根枢纽没有子页面` |
| `dependency.self` | `line` | `()` | `页面不能依赖自己` |
| `dependency.cycle` | `file` | `(path_1, ..., path_1)` | `依赖形成环路：path_1 -> ... -> path_1` |
| `replacement.pair_missing` | `line` | `(target, expected_counterpart)` | `{target} 缺少对应的“{expected_counterpart}”关系` |
| `replacement.status_mismatch` | `line\|file` | `(relation_requires_status,)` / `(status_requires_relation,)` | 分别说明“被取代”关系要求已取代状态、已取代状态要求“被取代”关系 |
| `replacement.cycle` | `file` | `(path_1, ..., path_1)` | `取代形成环路：path_1 -> ... -> path_1` |
| `challenge.pair_missing` | `line` | `(target, expected_counterpart)` | `{target} 缺少对应的“{expected_counterpart}”关系` |
| `output.forbidden` | `line` | `()` | `当前页面或状态不允许声明“产出”` |
| `output.missing` | `file` | `()` | `已确认页面缺少“产出”声明` |
| `output.wrong_target` | `line` | `(duplicate,)` / `(not_conclusions, literal_destination)` | 分别说明重复产出、产出必须指向根级 `conclusions.md` 并显示当前目标 |
| `output.backlink_missing` | `file` | `(source_page,)` | `缺少对已确认页面 {source_page} 的正文反向链接` |
| `page.capacity` | `file` | `(line_count, byte_count)` | `页面容量为 {line_count} 行 / {byte_count} 字节，建议审查是否拆分` |
| `operation.journal_corrupt` | `file` | `()` | `操作锁或日志损坏` |
| `operation.recovery_required` | `file` | `(operation_id,)` | `操作 {operation_id} 需要恢复` |
| `operation.orphan_allocating` | `file` | `()` | `发现未持锁的 allocating 操作日志` |
| `operation.inconsistent_state` | `file` | `(phase,)` | `操作处于无锁不一致阶段：{phase}；完整上下文请使用 --json` |
| `trash.deadlines_invalid` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的期限字段无效` |
| `trash.package_missing` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的权威删除包不存在` |
| `trash.package_corrupt` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的权威删除包损坏` |
| `trash.retained` | `file` | `(operation_id,)` | `回收操作 {operation_id} 仍处于保留态` |
| `trash.handoff_pending` | `file` | `(operation_id,)` | `回收操作 {operation_id} 的系统回收站移交等待重试` |
| `trash.pending_unexplained` | `file` | `(operation_id,)` | `回收操作 {operation_id} 处于无有效失败记录的待移交态` |

`operation.inconsistent_state` 是唯一明确要求普通摘要附加 `完整上下文请使用 --json` 的错误码：普通输出只给出阶段事实，完整参数通过 `--json` 的 `details` 查看。未知错误码或参数数量不符合目录协议时，JSON 仍输出原始条目，普通输出退回到包含来源位置、错误码和全部转义参数的通用单行 `未登记诊断参数：[…]`，不崩溃或吞掉已发现的问题。

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
