# 平台搭建与故障恢复参考

本参考覆盖 Draft 插件安装、宿主变量、插件上下文桥接（bridge）、会话绑定（binding）、Hook 诊断和故障恢复。这些能力属于插件适配层，不属于 Draft 核心；核心 skill 不引用本文档。

## 架构概览

Draft 的核心命令和插件命令使用不同入口：

- **核心命令**：Agent 从已加载 skill 取得 `<skill_root>`，执行 `uvx --isolated --from "<skill_root>/scripts/runtime" draft ...`。核心命令不读取 bridge、不区分运行模式、不依赖宿主变量。详见核心 SKILL.md。
- **插件命令**：插件入口位于 `plugins/draft/scripts/draft.py`，只依赖 Python 标准库和 `adapter/` 包，不导入或调用 Draft 核心。它提供 `bind`、`unbind`、`plugin-context reset` 和 `hook session-start HOST`。

## 前置条件

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 在 `PATH` 上；插件入口和核心 runtime 都通过 uv 调用；
- 插件入口（`scripts/draft.py`）使用 PEP 723 元数据声明兼容 Python 和空依赖，只要求 uv，不需要插件自己的虚拟环境；
- 核心 runtime 的直接依赖和构建依赖由 uv 在首次执行时从配置的包索引解析；后续运行复用 uv 缓存。

## 宿主变量

两个宿主各自声明 SessionStart 钩子，命令形状完全相同，只直接调用 `uv run` 与插件入口：

- Codex：`uv run "${PLUGIN_ROOT}/scripts/draft.py" hook session-start codex`（`startup|resume|clear|compact`）；
- Claude Code：`uv run "${CLAUDE_PLUGIN_ROOT}/scripts/draft.py" hook session-start claude`（`startup|resume|clear|compact|fork`）。

`HOST` 只接受下表字面量：

| `HOST` | 插件根变量 | 插件数据目录变量 |
| --- | --- | --- |
| `codex` | `PLUGIN_ROOT` | `PLUGIN_DATA` |
| `claude` | `CLAUDE_PLUGIN_ROOT` | `CLAUDE_PLUGIN_DATA` |

未知宿主、缺失变量、非绝对数据目录或规范插件根与当前入口布局不一致都属于 Hook 上下文错误，不得尝试另一组宿主变量。

宿主必须信任并启用该插件的钩子；安装插件或改动钩子文件后，重新加载窗口或重启会话，宿主才会登记新钩子。钩子不受信任时不发布 bridge、不刷新绑定，已存在的合法 bridge 不受影响。

## 插件上下文桥接（bridge）

只有 SessionStart Hook 能可靠取得宿主 `data_dir`；普通 Agent 工具进程不继承该变量。因此 bridge 是插件适配层的必要协议。

bridge 保持严格两字段 JSON：

```json
{
  "schema_version": 1,
  "data_dir": "/absolute/host/plugin/data"
}
```

安装键是 `plugins/draft/scripts/draft.py` 规范路径的 SHA-256。bridge 位于当前用户安全运行目录中的 `draft/bridges/`：

```text
installation_key = sha256(UTF-8(realpath(scripts/draft.py)))
bridge_path = <safe-runtime-dir>/draft/bridges/<installation_key>.json
conflict_path = <safe-runtime-dir>/draft/bridges/<installation_key>.conflict
bridge_lock_path = <safe-runtime-dir>/draft/bridges/<installation_key>.lock
```

`<safe-runtime-dir>` 在 POSIX 上优先使用所有权和权限合法（`0o700`、当前用户拥有）的 `XDG_RUNTIME_DIR`，否则在系统临时目录下创建当前用户独占子目录；Windows 使用当前用户临时目录。

bridge 状态严格区分：

| 状态 | 含义 | 普通插件命令行为 |
| --- | --- | --- |
| `missing` | 本安装尚未由 Hook 发布上下文，或临时目录被清理 | `plugin.context_unavailable`，退出 4 |
| `valid` | 唯一、安全的宿主 `data_dir` 已发布 | 继续执行 bind/unbind |
| `invalid` | JSON、字段、路径、权限或文件类型无效 | `plugin.context_invalid`，退出 4 |
| `conflict` | 同一安装键观察到不同宿主数据目录 | `plugin.context_conflict`，退出 4 |

不得把 `missing`、`invalid` 或 `conflict` 降级到全局 Draft 目录，也不得搜索其他插件安装。

bridge 是数据目录定位器，不是租约。插件被禁用或钩子不再受信任后不会刷新或撤销它，已存在且仍合法的 bridge 可被当前进程继续使用；新会话不再取得该插件 skill。

同一安装键收到另一个不同的数据目录时形成粘滞冲突：写入冲突哨兵并报 `plugin.context_conflict`，任何一方都不自动删除哨兵。只有先停止重复加载（只保留一个插件安装来源），才能执行 `plugin-context reset`。

bridge 文件、冲突哨兵和锁是工具内部状态，绝不手工编辑。

## 会话绑定（binding）

binding 表示"宿主会话与工作目录的关联"，不证明目录是有效或已登记 Draft。适配层不检查 `.draft/`、`index.md`、`conclusions.md`、Draft 图或 `draft check` 结果。

### 输入

- `session_id` 是宿主提供的不透明字符串，原样参与载荷，不截断、规范化或解释；
- `ROOT` 展开用户目录后必须存在、是目录且自身不是符号链接；保存前规范化为绝对真实路径；
- `last_page_hint` 可省略；若提供，必须是根内非空相对 Markdown 路径，两平台语义下都不是绝对路径、不含 `..`，路径段不得经过符号链接，目标当前必须是普通文件。

### 持久格式

binding 文件位于：

```text
<host_data_dir>/bindings/<sha256(session_id)>.json
```

载荷严格只有三个字段：

```json
{
  "session_id": "opaque host value",
  "root": "/canonical/absolute/root",
  "last_page_hint": "pages/topic.md"
}
```

`last_page_hint` 缺失时值为 `null`。不再保存 `skill_name` 或 `skill_digest`；skill 内容变化不改变会话与目录的关联，重新执行 bind 也不能证明 Agent 已阅读新指令。

写入使用同目录临时文件、文件同步、原子替换和目录同步。已有旧载荷包含额外字段时按严格协议报告 `binding.invalid`；重新 bind 会原子覆盖为新格式。`unbind` 按 session 键直接安全删除目标，因此即使旧载荷无法解析也能解除，且目标不存在时成功返回 `removed=false`。

### 插件 CLI 命令

SessionStart 信封提供 `<plugin_entry>` 的规范绝对路径。Agent 执行：

```text
uv run <plugin_entry> bind ROOT --session SESSION_ID [--last-page-hint PAGE]
uv run <plugin_entry> unbind --session SESSION_ID
uv run <plugin_entry> plugin-context reset
```

`--json` 可放在子命令紧前、紧后或末尾，输出稳定 JSON 信封。成功 JSON 分别为：

```json
{"ok": true, "result_type": "bind", "root": "/canonical/absolute/root"}
{"ok": true, "result_type": "unbind", "removed": true}
```

失败 JSON 统一为：

```json
{
  "ok": false,
  "result_type": "error",
  "error": {
    "code": "binding.invalid_hint",
    "severity": "error",
    "paths": ["pages/topic.md"],
    "details": []
  }
}
```

普通文本保持一行结果或一行稳定错误，不输出 traceback。

## plugin-context reset

`plugin-context reset` 只删除以当前插件入口为安装键的临时 bridge、冲突哨兵和锁，不读取或删除任何宿主 `data_dir`、binding 或日志。下一次合法 SessionStart 会重新发布 bridge。该命令不属于 Draft 核心。

它是唯一允许在普通 bridge 解析失败前分派的维护动作：先于 bridge 加载执行，不需要合法 bridge 上下文。

## SessionStart 数据流

`hook session-start HOST` 按以下顺序执行：

1. 根据 `HOST` 只读取对应宿主的 root/data 变量；不混用两个宿主的变量；
2. 验证插件根、入口和 `skills/draft/SKILL.md` 均位于预期布局；
3. 发布或验证 bridge；
4. 最多读取 64 KiB 标准输入，解析并验证 `SessionStart` 载荷；
5. 提取原始 `session_id` 和启动 `source`；
6. 从宿主 `data_dir` 加载 binding；
7. 记录尽力而为的宿主 Hook 日志；
8. 输出会话事实、重新绑定信封或诊断。

输出分支：

| 状态 | 模型可见输出 |
| --- | --- |
| 无 binding | 原始 session ID、规范插件入口，以及 bind/unbind 命令格式 |
| binding 有效且根存在 | 根、仍存在的可选页面提示、当前 skill 规范路径、插件入口；要求重新完整阅读 skill，并在修改前运行核心 `draft check` |
| 根消失 | 明确诊断，要求重新选择目录并 bind；不静默沿用旧根 |
| 页面提示后来消失或不再是普通文件 | 省略提示，保留根 binding |
| binding 损坏或 session 载荷不一致 | 明确诊断，要求重新 bind；不解释为未绑定 |
| bridge、插件布局或宿主上下文失败 | 明确插件诊断；不伪造 `data_dir` |

每次恢复都要求读取当前 `SKILL.md`，不使用摘要推断 Agent 是否已经阅读。

信封中的文件和入口路径使用规范绝对路径；展示命令时对路径加引号。`session_id` 仍作为单独的不透明参数展示，Agent 必须原样传回，不把它拼接成路径、摘要或另一种身份。

## Hook 诊断与返回行为

Hook 的协议错误不得阻止宿主会话启动：脚本可执行时，`hook session-start` 对载荷、bridge、binding、布局和日志错误都输出或记录诊断并返回 0。`uv` 无法启动脚本属于宿主执行层失败，不在脚本能够转换的范围内。

普通核心命令只有在 `draft` 控制台命令启动后才进入 Draft 的错误协议。`uvx` 自身的 Python 发现、包索引访问、依赖解析、构建或缓存错误直接保留 uv 的标准错误输出和退出码；外层没有启动器将其转换为 Draft 错误码或 `--json` 载荷。调用方不得把 uvx 启动失败解释为 Draft 检查失败、目标不存在或命令成功。

## 失败语义与退出码

普通插件命令遵循：用户输入错误退出 2，插件上下文或持久化运行时错误退出 4。

| 失败 | 错误码/行为 |
| --- | --- |
| bridge 缺失 | `plugin.context_unavailable`，退出 4 |
| bridge 损坏、路径不安全或文件操作失败 | `plugin.context_invalid`，退出 4 |
| bridge 冲突 | `plugin.context_conflict`，退出 4 |
| ROOT 缺失、不是目录或是符号链接 | `binding.root_unavailable`，退出 2 |
| 页面提示为空、绝对、越界、非 Markdown、经过符号链接或目标不是普通文件 | `binding.invalid_hint`，退出 2 |
| 宿主数据目录不可安全读写 | `binding.data_dir_unavailable`，退出 4 |
| binding 载荷字段、类型或 JSON 无效 | `binding.invalid`，退出 4 |
| binding 载荷的 session 与查找键不一致 | `binding.session_mismatch`，退出 4 |
| unbind 目标不存在 | 成功，`removed=false` |

OSError 必须在最窄文件操作边界转换为稳定错误码并保留异常链，不向 Agent 泄漏 traceback。已有主错误时，清理失败只能附加诊断，不能覆盖主错误。Hook 日志和目录 fsync 是明确的尽力而为操作；binding/bridge 的创建、写入、替换、删除和锁操作是强制步骤。

## 故障恢复

插件上下文（退出 4）：

- `plugin.context_invalid`：bridge JSON 损坏、字段不符或权限/所有权/文件类型异常。先确认没有手工改动 bridge 目录；无法解释时停止并询问用户。确认要重来时执行 `uv run <plugin_entry> plugin-context reset`，下一次 SessionStart 重新发布。
- `plugin.context_conflict`：同一安装键被两个不同数据目录争夺（重复加载）。先停止重复加载，再执行同一条 reset 命令。
- `plugin.context_unavailable`：当前 bridge 未发布（`missing` 状态），`bind`/`unbind` 不可用；需要宿主 Hook 发布 bridge（进入 `valid` 状态）后才能使用。
- `binding.session_mismatch`：绑定载荷与查找键不一致，绑定文件被改动过。先 `uv run <plugin_entry> unbind --session SESSION_ID` 移除它，再重新 bind。
- `hook.environment_missing`：钩子路径缺少宿主注入的插件根或数据目录变量；确认宿主版本支持该钩子协议。
- `uv run`（插件入口启动）自身失败：退出码原样传播，bridge 已发布并保留；按 uv 的诊断处理（Python、网络、解释器策略）后重试。

草稿结构（领域与操作退出码）：

- 结构命令提示 `operation.recovery_required` -> `uvx --isolated --from "<skill_root>/scripts/runtime" draft recover ROOT`。恢复只读取草稿内的锁与操作日志，不依赖运行模式。
- `trash` 把页面落地为 7 天保留态（`trash_retained`），不立即移交系统回收站；7 天内可 `draft restore ROOT OPERATION_ID` 恢复。到期后的系统回收站移交和最终清理由 `recover ROOT` 或普通结构写命令惰性推进（24 小时节流，30 天后最终清理），Agent 不需要维护计时器。
- 普通投递明确失败只产生警告并继续原命令；状态损坏（`operation.inconsistent_state` 等）才停止写入。`restore` 回滚退出 3、`ok=false`，权威包仍保留，不要报告"已恢复"。
- `recover` 报告外部修改 -> 有人在操作期间编辑了受管理文件；工具保留现场，由你人工决定取舍，不要删除锁或日志目录。
- 钩子未注入信封 -> 确认宿主已信任并登记该插件钩子（安装后重载/重启过会话）、当前会话已 bind 过；未绑定会话只会看到 session_id 一行。
- 想完全移除 -> 卸载插件；bridge 随运行目录清理自然失效，数据目录中的绑定与日志可随数据目录一并删除。
