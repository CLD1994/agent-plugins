# 平台搭建与故障恢复参考

本参考覆盖 draft 的稳定入口、两种运行模式、插件上下文桥接和故障恢复。所有行为围绕当前 skill 自己的 `scripts/draft`（PowerShell 用 `scripts/draft.ps1`）展开，不依赖任何宿主路径或宿主变量。

## 前置条件

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 在 `PATH` 上；入口只检查并调用它，绝不自动下载；
- Python 3.14 由 uv 按 `scripts/runtime/pyproject.toml` 的约束自动管理，不需要系统级安装。

## 唯一入口与 setup

所有命令都通过稳定入口执行：

```text
scripts/draft setup
scripts/draft init ROOT
scripts/draft check ROOT
scripts/draft bind ROOT --session SESSION_ID
scripts/draft unbind --session SESSION_ID
scripts/draft plugin-context reset
```

每次调用（不止 `setup`）都执行同一套部署边界：解析插件上下文桥接、选定环境目标、按锁文件同步、用当前源码树做 `--version` 自检，然后才分派领域命令。`setup` 在同步与自检成功后直接返回，不执行任何领域命令——它只是可选预热，用于把环境构建提前到讨论开始之前。

环境只包含 Python 和锁定的第三方依赖，始终从当前 `scripts/runtime/src/` 执行，不把 Draft 项目安装进环境。环境是可重建缓存：环境键由 `pyproject.toml` 与 `uv.lock` 的原始字节决定，清单不变则复用，清单变化则落到新目标重建。

## 两种运行模式

入口按当前用户运行目录中的桥接定位器选择模式：

- **独立模式**：桥接不存在。环境位于当前用户运行目录下（POSIX 优先 `$XDG_RUNTIME_DIR/draft/envs/<环境键>`，否则 `/tmp/draft-runtime-<uid>/draft/envs/<环境键>`；Windows 使用系统临时目录下的对应路径）。全部领域命令可用；`bind`/`unbind` 以 `binding.plugin_context_unavailable` 失败（退出 4）。
- **插件增强模式**：宿主 SessionStart 钩子通过同一入口发布了桥接。环境位于桥接声明的宿主数据目录下 `envs/<环境键>`，`bind`/`unbind` 与重新绑定信封可用。

运行目录被系统清理后入口合法退回独立模式，下一次 SessionStart 重新发布桥接；入口绝不在两个模式的环境位置之间移动或复制虚拟环境。

## 插件上下文桥接

桥接是当前用户运行目录 `draft/bridges/` 下、以安装键（入口规范路径的 SHA-256）命名的 JSON 文件，严格只有两个字段：`schema_version` 与 `data_dir`。任何未知字段、版本不符、`data_dir` 不是可写绝对目录、权限或文件类型异常都视为 `plugin.context_invalid`。

- 发布只发生在 SessionStart 钩子路径：取得排他锁、验证数据目录、临时文件加原子替换；桥接目录与文件仅当前用户可访问。
- 桥接是安装实例定位器，不是租约或凭证：插件被禁用或钩子不再受信任后不会刷新或撤销它，已存在且仍合法的桥接可被当前进程继续使用；新会话不再取得该插件 skill。
- 同一安装键收到另一个不同的数据目录时形成粘滞冲突：写入冲突哨兵并报 `plugin.context_conflict`，任何一方都不自动删除哨兵。只有先停止重复加载（只保留一个插件安装来源），才能执行 `scripts/draft plugin-context reset`——它在同一把锁内只删除该安装键的 JSON 与哨兵（锁文件保留），下一次 SessionStart 重新发布。它是唯一允许在普通桥接解析失败前分派的维护动作。
- 桥接文件、冲突哨兵和锁是工具内部状态，绝不手工编辑。

## 宿主钩子

两个宿主各自声明 SessionStart 钩子，命令只引用宿主提供的插件根变量指向当前 skill 的 `scripts/draft`：

- Codex：`startup|resume|clear|compact`；
- Claude Code：`startup|resume|clear|compact|fork`。

钩子行为：

- 宿主必须信任并启用该插件的钩子；安装插件或改动钩子文件后，重新加载窗口或重启会话，宿主才会登记新钩子。钩子不受信任时不发布桥接、不刷新绑定，已存在的合法桥接不受影响。
- 钩子校验宿主声明的插件根与入口自身的安装位置一致，不一致时报 `hook.plugin_root_mismatch` 并拒绝，绝不搜索其他目录。
- 未绑定的会话：钩子把 `Draft 宿主 session_id：…` 一行注入模型可见的 SessionStart 上下文，其余完全静默。会话标识是不透明字符串，把它原样复制进 `bind --session` / `unbind --session`，不要加工、截断或规范化。
- 已绑定的会话：钩子注入短重新绑定信封（活动草稿根、必须使用的 skill 及摘要、触发原因、可选的最后页面提示）；绑定根缺失、绑定损坏或摘要不符时改为注入诊断，要求重新选择草稿并重新 bind，绝不静默继续旧绑定。
- 绑定写入数据目录 `bindings/<sha256(session_id)>.json`，载荷保留原始会话字符串，会话标识本身不进入文件名。

## 故障恢复

环境与桥接（退出 4）：

- `runtime.uv_unavailable`：安装 uv 并确认在 `PATH` 上；入口不自动下载。
- `runtime.environment_invalid`：环境目标被替换成符号链接/非目录，或环境内 python 缺失。环境是可重建缓存，删除该损坏环境目录后重跑任意命令即按清单重建。
- `plugin.context_invalid`：桥接 JSON 损坏、字段不符或权限/所有权/文件类型异常。先确认没有手工改动桥接目录；无法解释时停止并询问用户。确认要重来时执行 `scripts/draft plugin-context reset`，下一次 SessionStart 重新发布。
- `plugin.context_conflict`：同一安装键被两个不同数据目录争夺（重复加载）。先停止重复加载，再 `scripts/draft plugin-context reset`。
- `binding.plugin_context_unavailable`：当前是独立模式，`bind`/`unbind` 不可用；需要宿主钩子发布桥接后的插件增强模式。
- `binding.session_mismatch`：绑定载荷与查找键不一致，绑定文件被改动过。先 `scripts/draft unbind --session SESSION_ID` 移除它，再重新 bind。
- `binding.skill_mismatch`：SKILL.md 摘要与绑定时不一致（插件更新或文件被改）。重新完整阅读 SKILL.md，再重新 bind。

草稿结构（领域与操作退出码）：

- 结构命令提示 `operation.recovery_required` → `scripts/draft recover ROOT`。恢复只读取草稿内的锁与操作日志，不依赖运行模式。
- `trash` 把页面落地为 7 天保留态（`trash_retained`），不立即移交系统回收站；7 天内可 `scripts/draft restore ROOT OPERATION_ID` 恢复。到期后的系统回收站移交和最终清理由 `recover ROOT` 或普通结构写命令惰性推进（24 小时节流，30 天后最终清理），Agent 不需要维护计时器。
- 普通投递明确失败只产生警告并继续原命令；状态损坏（`operation.inconsistent_state` 等）才停止写入。`restore` 回滚退出 3、`ok=false`，权威包仍保留，不要报告“已恢复”。
- `recover` 报告外部修改 → 有人在操作期间编辑了受管理文件；工具保留现场，由你人工决定取舍，不要删除锁或日志目录。
- 钩子未注入信封 → 确认宿主已信任并登记该插件钩子（安装后重载/重启过会话）、当前会话已 bind 过；未绑定会话只会看到 session_id 一行。
- 想完全移除 → 卸载插件；桥接随运行目录清理自然失效，数据目录中的环境与绑定可随数据目录一并删除。
