# Draft 可行动检查诊断设计

## 状态与范围

状态：已实施（runtime `0.2.0`）。

本规格只修正 `draft check ROOT` 的诊断事实保存、普通文本渲染和 JSON 条目协议。Markdown
格式、检查规则、问题排序、严重级别、退出码、结构写入和恢复语义继续保持不变。

## 问题

当前检查器会收集全部客观错误，但普通输出只显示严重级别、错误码和来源文件。例如：

```text
错误 link.missing conclusions.md
```

`CheckIssue` 已经可以保存 `line` 和 `details`，普通渲染却丢弃两者。`link.missing` 的检测点
又进一步丢弃了当时已知的链接字面目标和规范化目标。因此，同一文件存在多处死链时，普通
输出可能产生多行无法区分的相同文本；即使改用 `--json`，调用者也只能得到来源行号，仍需
读取该行才能知道缺失目标。

这使检查器只完成“发现错误”，没有完成“交付可定位的诊断”。人和 Agent 都被迫重新搜索或
阅读全文，违背 CLI 负责机械结构检查、Agent 负责语义判断的职责边界。

## 目标

1. 普通输出直接服务人和 Agent：常见客观错误无需阅读全文即可定位和区分。
2. 每条诊断独立包含来源位置、稳定错误码、简短客观事实和必要相关对象。
3. 复杂事实通过 `--json` 提供完整结构化参数；普通输出明确说明何时需要 `--json`。
4. 检测层、普通渲染和 JSON 使用同一份诊断事实，不能各自重新推导。
5. 保持现有检查规则、确定性顺序、严重级别、退出码和只读语义。

## 非目标

- 不提供自动修复或猜测正确链接目标。
- 不打印源码摘录、插入符或 ANSI 颜色。
- 不引入多语言框架、新依赖或无关重构。
- 不让渲染层重新读取 Markdown、解析链接或计算图关系。
- 不改变 Markdown 机器区域、关系协议或错误严重度。

## 行为契约

### 输入

输入仍是 `check_draft` 产生、按现有规则排序的 `CheckReport`。每个 `CheckIssue` 继续使用：

```text
code, severity, path, line, details
```

`path` 是应当打开或修改的来源文件，不是缺失目标或关联页面。`line` 使用与编辑器一致的一基
行号。

### 输出

普通诊断固定为单行编译器式格式：

```text
<来源位置>: <级别> <错误码>：<客观事实及关键对象>
```

例如：

```text
conclusions.md:12: 错误 link.missing：链接目标不存在：pages/missing.md
conclusions.md:27: 错误 link.missing：链接目标不存在：pages/removed.md
pages/topic.md: 错误 parent.missing：缺少“父主题”声明
pages/a.md:18: 错误 challenge.pair_missing：pages/b.md 缺少对应的“被质疑”关系
```

全部诊断之后输出计数摘要：

```text
检查完成：2 个错误，1 个警告，0 个信息，0 个提醒。
```

完全无诊断时继续输出：

```text
检查通过：未发现客观错误。
```

JSON 保留 `ok`、`result_type=check` 和 `errors`、`warnings`、`infos`、`reviews` 四个分类
数组。每个条目统一包含：

```json
{
  "code": "link.missing",
  "severity": "error",
  "path": "conclusions.md",
  "line": 12,
  "details": ["pages/missing.md", "pages/missing.md"]
}
```

示例中的两个 `details` 参数分别是链接字面目标和草稿根内规范化目标；两者相同时普通输出
只显示一次。

### 不变量

1. 每个被违反的客观事实产生一条独立诊断；扫描不在首个错误处停止。
2. 同一文件、同一错误码的多条诊断必须能由行号或相关对象区分。
3. 具体 Markdown 结构触发的错误必须带来源行号。只有结构缺失、文件级或图级事实可以没有
   行号。
4. `path=None` 只允许用于错误码目录明确声明的根级事实，并渲染为 `<ROOT>`；文件级错误
   不能用空路径掩盖生产者缺陷。
5. 普通输出只陈述客观事实，不给出可能涉及语义选择的修复建议。
6. 普通输出和 JSON 使用同一个排序后的 `CheckReport`，条目数量、顺序和事实一致。
7. 输出保持一条诊断一行；参数中的换行符和其他控制字符必须转义，不能破坏行边界。
8. 普通输出和 JSON 都写入标准输出；退出码保持现状，只有 `errors` 使 `check` 返回 `1`。

## 诊断数据协议

### 检测层拥有事实

诊断必须在发现错误的最窄责任边界完整构造。检测层负责保留：

- 来源文件和来源行；
- 链接的字面目标和规范化目标；
- 关系边的来源行、对端页面和缺少的反向关系名；
- 环路的有序闭合路径；
- 状态、阶段、容量计数和操作标识等原始参数。

渲染层不得通过重新读取文件来补足这些事实。

当前数据流至少需要纠正以下损失点：

- `graph.py` 在构造 `link.missing` 和 `link.escape` 时丢弃链接目标；
- 关系边最初包含来源行，但部分一致性检查把边压缩成 `source -> target` 集合后丢弃行号；
- 环路、容量和若干状态错误提前把多个参数拼成自然语言字符串。

### `details` 是由错误码判别的参数协议

`CheckIssue` 不保存预渲染中文。`details` 继续是字符串元组，对外继续序列化为 JSON 数组，
但其内容改为错误码规定的稳定原始参数。错误码目录必须声明参数数量、顺序和含义。

代表性协议如下：

| 错误码 | `details` | 说明 |
| --- | --- | --- |
| `link.missing` | `literal_destination, normalized_target` | 字面目标与草稿根内规范目标 |
| `link.escape` | `branch, operand` | `destination` 分支保存字面目标；`managed_path` 分支保存越界符号链接路径 |
| `challenge.pair_missing` | `target, expected_counterpart` | 对端页面与缺少的反向标签 |
| `replacement.pair_missing` | `target, expected_counterpart` | 对端页面与缺少的反向标签 |
| `dependency.cycle` | `path_1, ..., path_1` | 有序闭合路径，不预先连接 |
| `parent.cycle` | `path_1, ..., path_1` | 有序闭合路径，不预先连接 |
| `page.capacity` | `line_count, byte_count` | 十进制原始计数 |
| `page.parse_error` | `reason, ...operands` | 稳定原因字面量及该原因需要的原始参数 |

同一错误码存在多个原因分支时，首个参数使用稳定分支字面量，后续参数由该分支规定。例如
`output.wrong_target` 需要区分重复产出和目标不是根级 `conclusions.md`，但不因此改变既有
错误码。

现有 `details` 中用于人类显示的中文整句必须迁移到诊断目录模板；JSON 不再把自然语言整句
当作结构化参数。

## 诊断目录与渲染职责

集中诊断目录按错误码定义：

- 允许的位置类型：具体行、文件或草稿根；
- `details` 的参数协议；
- 普通中文事实模板；
- 普通输出是完整事实还是只输出摘要并提示 `--json`。

渲染器是纯函数：

```text
CheckIssue -> 一行普通诊断
CheckIssue -> JSON issue
```

它不访问草稿根。常见结构错误完整显示必要参数；无法可靠压成一行的复杂状态由目录显式
标记为摘要输出，例如：

```text
.draft/operations/abc/journal.json: 错误 operation.inconsistent_state：操作状态不一致；完整上下文请使用 --json
```

是否摘要不能由字符串长度等启发式规则决定。

未知错误码或参数数量不符合目录协议时，JSON 仍输出原始条目；普通输出退回到包含来源位置、
错误码和全部转义参数的通用单行，不能崩溃或吞掉已发现的问题。测试门禁必须保证所有发布
时可产生的错误码都有目录项和正确参数协议，因此回退只承担运行时保真，不把生产者缺陷解释
为正常状态。

## 普通输出细则

- `line` 非空时来源位置为 `path:line`；为空时只显示 `path`。
- 真正根级的诊断显示 `<ROOT>`，不输出空字符串。
- 路径显示为草稿根内的规范 POSIX 相对路径。
- 四档中文级别保持 `错误`、`警告`、`信息`、`提醒`。
- 字面链接与规范目标相同时只显示规范目标；不同时同时说明解析结果。
- 不按文件折叠，不合并来源不同的诊断。
- 完整列出四档诊断后输出四项计数；只有四档全部为空时使用既有全绿句子。
- 不输出颜色、对齐空格、源码摘录或依赖终端宽度的内容。

## JSON 兼容性

现有四个分类数组、`code`、`path`、`line` 和 `details` 保持。每个条目增加与外层分类一致的
`severity`；这是加法兼容，也使实现符合 `draft-format.md` 已声称的严重级别字段。

`line` 没有具体来源时为 `null`，不能用 `0`、`1` 或缺省路径伪造位置。`details` 保持数组
形状，但参数协议从无约束人类字符串收紧为错误码判别的稳定原始值。需要按复杂状态消费 JSON
的调用者以 `code` 选择对应参数协议。

## 影响范围与发布

实现只允许修改以下诊断链路和发布元数据：

- `scripts/runtime/src/draft/graph.py`：在链接与关系解析边界保存诊断事实；
- `scripts/runtime/src/draft/checker.py`：保留行号和原始图、状态、容量参数；
- `scripts/runtime/src/draft/markdown.py`：把会进入 `CheckIssue` 的解析错误详情改为稳定原因
  字面量和原始参数，不改变接受或拒绝的 Markdown 集合；
- 新建 `scripts/runtime/src/draft/diagnostics.py`：集中保存错误码协议和普通模板；
- `scripts/runtime/src/draft/cli.py`：单行渲染、计数摘要和 JSON `severity`；
- `scripts/runtime/tests/test_checker.py`、`test_cli.py`、`test_markdown.py` 和新增的诊断目录
  测试；
- `skills/draft/references/draft-format.md`：正式记录本规格的输出和参数协议。

`CheckIssue` 的 `code`、`severity`、`path`、`line`、`details` 五字段公开形状保持不变；集中
目录所需的内部辅助类型全部放入 `diagnostics.py`，不借机调整领域模型。

runtime 内容变化属于发布级变化。实现时必须同步提升：

- `scripts/runtime/pyproject.toml` 的 `project.version`；
- `.codex-plugin/plugin.json` 与 `.claude-plugin/plugin.json` 的版本；
- `scripts/runtime/uv.lock` 中受项目版本影响的内容。

`SKILL.md` 已要求 Agent 运行普通 `check ROOT`；普通输出变得可行动后不需要改成强制
`--json`。

## 测试契约

### 检测层

测试从行为事实手写预期，不复用诊断目录或渲染计算：

- 死链保留准确来源文件、一基行号、字面目标和规范目标；
- 同一文件不同位置的多条死链形成多条可区分诊断；
- 同一行指向不同缺失目标时仍能由目标区分；
- 缺少 `父主题`、`产出` 等不存在来源行的错误保持 `line=None`；
- 关系配对缺失保留原始关系行、对端页面和预期反向标签；
- 环路 `details` 是有序闭合路径，不是预拼接字符串；
- 容量、状态、阶段和操作标识分别保存，不折叠成说明句。

### 普通渲染

- 精确断言 `path:line: 级别 code：事实`；
- 覆盖四档严重级别、无行号文件位置和根级位置；
- 覆盖同一文件多条错误、同一行不同目标和最终计数摘要；
- 覆盖复杂诊断的 `--json` 提示；
- 覆盖未知错误码、错误参数数量和控制字符的保真单行回退；
- 断言没有 ANSI 颜色、源码摘录或尾随空白；
- 全绿输出保持既有句子。

### JSON 与回归

- 每条诊断包含 `code`、`severity`、`path`、`line`、`details`；
- 四个分类数组的条目顺序和数量与 `CheckReport` 一致；
- 多错误不去重、不覆盖；
- `details` 不含普通输出中的预渲染中文整句；
- 普通模式与 `--json` 退出码一致；
- 普通和 JSON 模式都不修改草稿任何字节。

实施完成前运行：

```text
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pytest -q
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime pyright
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff check .
uv run --isolated --project plugins/draft/skills/draft/scripts/runtime ruff format --check .
```

还必须运行复制 skill 后只通过公开 `scripts/draft.py` 入口的烟雾测试，证明版本化 runtime
没有复用旧缓存。
