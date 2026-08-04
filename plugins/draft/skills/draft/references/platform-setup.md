# 平台搭建与故障恢复参考

本参考覆盖 draft 的稳定入口、两种运行模式、插件上下文桥接和故障恢复。所有行为围绕当前 skill 自己的 `scripts/draft.py` 展开；Windows 与 POSIX 使用同一条命令，没有 PowerShell 专用入口。

## 前置条件

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 在 `PATH` 上；入口只检查并调用它，绝不自动下载 uv 本身；
- 不需要系统级 Python：`scripts/draft.py` 头部的 PEP 723 元数据和 runtime 的 `requires-python` 由 uv 自动选定兼容解释器；
- 命令不传 `--python`、`--project`、`--no-project`，不读取 `.env`。

## 唯一入口与 setup

所有命令都通过同一入口执行：

```text
uv run <skill_root>/scripts/draft.py setup
uv run <skill_root>/scripts/draft.py init ROOT
uv run <skill_root>/scripts/draft.py check ROOT
uv run <skill_root>/scripts/draft.py bind ROOT --session SESSION_ID
uv run <skill_root>/scripts/draft.py unbind --session SESSION_ID
uv run <skill_root>/scripts/draft.py plugin-context reset
```

`<skill_root>` 是宿主加载 skill 时注入的当前 skill 根；`draft.py` 按自身所在的 `scripts/` 位置推导 skill 布局，不搜索其他副本。每次调用都执行同一套部署边界：解析插件上下文桥接、选定版本环境目标、按锁文件精确同步、然后分派领域命令。`setup` 映射为 `draft --version`——它只是可选预热，用于把环境构建提前到讨论开始之前。

环境键就是 runtime 的 `project.version`：环境位于 `{data_dir}/envs/<draft_version>`。runtime 与锁定依赖以非可编辑方式安装进目标环境（`--no-editable`），绝不通过 `PYTHONPATH` 执行源码树；同一版本共享环境，不同版本隔离。每次 `uv run` 都按 `uv.lock` 同步目标环境。注意：同版本重打包（改了源码或依赖但没提升版本号）不会被环境键区分——发布任何内容变化前必须提升并统一 runtime 与两个插件清单的版本号。

## 两种运行模式

入口按当前用户运行目录中的桥接定位器选择模式：

- **独立模式**：桥接不存在。数据目录位于当前用户运行目录下（POSIX 优先 `$XDG_RUNTIME_DIR/draft`，否则 `/tmp/draft-runtime-<uid>/draft`；Windows 使用系统临时目录下的对应路径），环境在其 `envs/<draft_version>`。全部领域命令可用；`bind`/`unbind` 以 `binding.plugin_context_unavailable` 失败（退出 4）。
- **插件增强模式**：宿主 SessionStart 钩子通过同一入口发布了桥接。数据目录是桥接声明的宿主数据目录，环境位于其 `envs/<draft_version>`，`bind`/`unbind` 与重新绑定信封可用。普通 Agent 工具进程没有宿主数据变量，通过桥接取得同一数据目录。

运行目录被系统清理后入口合法退回独立模式，下一次 SessionStart 重新发布桥接；入口绝不在两个模式的环境位置之间移动或复制虚拟环境。

## 插件上下文桥接

桥接是当前用户运行目录 `draft/bridges/` 下、以安装键（`scripts/draft.py` 规范路径的 SHA-256）命名的 JSON 文件，严格只有两个字段：`schema_version` 与 `data_dir`。任何未知字段、版本不符、`data_dir` 不是可写绝对目录、权限或文件类型异常都视为 `plugin.context_invalid`。

- 发布只发生在 SessionStart 钩子路径：取得排他锁、验证数据目录、临时文件加原子替换；桥接目录与文件仅当前用户可访问。
- 桥接是安装实例定位器，不是租约或凭证：插件被禁用或钩子不再受信任后不会刷新或撤销它，已存在且仍合法的桥接可被当前进程继续使用；新会话不再取得该插件 skill。
- 同一安装键收到另一个不同的数据目录时形成粘滞冲突：写入冲突哨兵并报 `plugin.context_conflict`，任何一方都不自动删除哨兵。只有先停止重复加载（只保留一个插件安装来源），才能执行 `uv run <skill_root>/scripts/draft.py plugin-context reset`——它在同一把锁内只删除该安装键的 JSON 与哨兵（锁文件保留），下一次 SessionStart 重新发布。它是唯一允许在普通桥接解析失败前分派的维护动作。
- 桥接文件、冲突哨兵和锁是工具内部状态，绝不手工编辑。

## 宿主钩子

两个宿主各自声明 SessionStart 钩子，命令形状完全相同，只直接调用 `uv run` 与当前 skill 的 `scripts/draft.py`：

- Codex：`uv run "${PLUGIN_ROOT}/skills/draft/scripts/draft.py" _session-start codex`（`startup|resume|clear|compact`）；
- Claude Code：`uv run "${CLAUDE_PLUGIN_ROOT}/skills/draft/scripts/draft.py" _session-start claude`（`startup|resume|clear|compact|fork`）。

钩子行为：

- 宿主必须信任并启用该插件的钩子；安装插件或改动钩子文件后，重新加载窗口或重启会话，宿主才会登记新钩子。钩子不受信任时不发布桥接、不刷新绑定，已存在的合法桥接不受影响。
- 钩子校验宿主声明的插件根与入口自身的安装位置一致，不一致时报 `hook.plugin_root_mismatch` 并拒绝，绝不搜索其他目录。
- 每次 SessionStart 都先发布/确认桥接并无条件同步版本环境，再解析钩子载荷——无绑定、空载荷或损坏载荷也不例外。
- 未绑定的会话：钩子把 `Draft 宿主 session_id：…` 一行注入模型可见的 SessionStart 上下文，其余完全静默。会话标识是不透明字符串，把它原样复制进 `bind --session` / `unbind --session`，不要加工、截断或规范化。
- 已绑定的会话：钩子注入短重新绑定信封（活动草稿根、必须使用的 skill 及摘要、触发原因、可选的最后页面提示）；绑定根缺失、绑定损坏或摘要不符时改为注入诊断，要求重新选择草稿并重新 bind，绝不静默继续旧绑定。
- 绑定写入数据目录 `bindings/<sha256(session_id)>.json`，载荷保留原始会话字符串，会话标识本身不进入文件名。

## 故障恢复

环境与桥接（退出 4）：

- `runtime.uv_unavailable`：安装 uv 并确认在 `PATH` 上；入口不自动下载 uv。
- `runtime.environment_invalid`：环境目标被替换成符号链接/非目录，或所有权、权限不允许当前用户使用。环境是可重建缓存，删除该损坏环境目录后重跑任意命令即按锁文件重建。
- 内层 `uv run` 自身失败：退出码原样传播，桥接已发布并保留；按 uv 的诊断处理（锁文件、网络、解释器策略）后重试。
- `plugin.context_invalid`：桥接 JSON 损坏、字段不符或权限/所有权/文件类型异常。先确认没有手工改动桥接目录；无法解释时停止并询问用户。确认要重来时执行 `uv run <skill_root>/scripts/draft.py plugin-context reset`，下一次 SessionStart 重新发布。
- `plugin.context_conflict`：同一安装键被两个不同数据目录争夺（重复加载）。先停止重复加载，再执行同一条 reset 命令。
- `hook.environment_missing`：钩子路径缺少宿主注入的插件根或数据目录变量；确认宿主版本支持该钩子协议。
- `binding.plugin_context_unavailable`：当前是独立模式，`bind`/`unbind` 不可用；需要宿主钩子发布桥接后的插件增强模式。
- `binding.session_mismatch`：绑定载荷与查找键不一致，绑定文件被改动过。先 `uv run <skill_root>/scripts/draft.py unbind --session SESSION_ID` 移除它，再重新 bind。
- `binding.skill_mismatch`：SKILL.md 摘要与绑定时不一致（插件更新或文件被改）。重新完整阅读 SKILL.md，再重新 bind。

草稿结构（领域与操作退出码）：

- 结构命令提示 `operation.recovery_required` → `uv run <skill_root>/scripts/draft.py recover ROOT`。恢复只读取草稿内的锁与操作日志，不依赖运行模式。
- `trash` 把页面落地为 7 天保留态（`trash_retained`），不立即移交系统回收站；7 天内可 `uv run <skill_root>/scripts/draft.py restore ROOT OPERATION_ID` 恢复。到期后的系统回收站移交和最终清理由 `recover ROOT` 或普通结构写命令惰性推进（24 小时节流，30 天后最终清理），Agent 不需要维护计时器。
- 普通投递明确失败只产生警告并继续原命令；状态损坏（`operation.inconsistent_state` 等）才停止写入。`restore` 回滚退出 3、`ok=false`，权威包仍保留，不要报告“已恢复”。
- `recover` 报告外部修改 → 有人在操作期间编辑了受管理文件；工具保留现场，由你人工决定取舍，不要删除锁或日志目录。
- 钩子未注入信封 → 确认宿主已信任并登记该插件钩子（安装后重载/重启过会话）、当前会话已 bind 过；未绑定会话只会看到 session_id 一行。
- 想完全移除 → 卸载插件；桥接随运行目录清理自然失效，数据目录中的环境与绑定可随数据目录一并删除。
