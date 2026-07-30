# 平台搭建与故障恢复参考

本参考覆盖 draft 作为正式插件安装后的环境搭建、显式绑定和故障恢复。

## 前置条件

- Python 3.14（由 uv 自动管理，不需要系统级安装）；
- [uv](https://docs.astral.sh/uv/getting-started/installation/)；
- 插件已安装，且宿主在 setup 时提供本平台的数据目录变量。

## 环境搭建

setup 需要一个显式平台参数，并且只读取该平台对应的数据目录变量；另一个平台的变量即使同时存在也被忽略：

- Codex：`PLUGIN_DATA`
- Claude Code：`CLAUDE_PLUGIN_DATA`

POSIX（Linux/macOS/WSL）：

```bash
bootstrap/setup.sh codex
bootstrap/setup.sh claude
```

PowerShell：

```powershell
bootstrap/setup.ps1 -Platform codex
bootstrap/setup.ps1 -Platform claude
```

数据目录缺失、不是目录或不可写时 setup 失败，不会回退到当前目录，也不创建任何状态。

setup 执行并仅执行：

1. 检查 uv（缺失时打印安装地址并退出，不自动下载）；
2. 在平台数据目录建立隔离虚拟环境并按锁文件同步；
3. 在数据目录 `bin/` 下生成 `draft` 与 `draft-hook-claude` 启动器；
4. 写入 `installed-runtime-manifest.json` 与 `setup-status.json`。

setup 不读取用户项目位置，不生成项目级设置片段，也不修改任何设置文件。

## 关键环境变量

生成的启动器把部署上下文规范化为 runtime 消费的准确输入：

- `DRAFT_DATA`：平台持久数据目录的规范化绝对路径。绑定命令要求它存在；缺失时退出码 4，绝不回退到当前目录。
- `DRAFT_SKILL_PATH`：已安装插件中 `skills/draft/SKILL.md` 的规范化绝对路径。缺失、目标不存在或不是普通文件时，bind 与钩子摘要校验报告 `binding.skill_unavailable`，不搜索其他目录。
- `UV_PROJECT_ENVIRONMENT`：平台数据目录下的隔离虚拟环境。
- `CLAUDE_CODE_SESSION_ID`：当前 Claude Code 会话标识，bind 时作为 `--session` 值。

所有 CLI 与钩子入口都通过生成的启动器运行，启动器已导出上述变量。

## 显式绑定与解除

```bash
draft bind ROOT --platform claude --session "$CLAUDE_CODE_SESSION_ID"
draft bind ROOT --platform claude --session "$CLAUDE_CODE_SESSION_ID" --last-page-hint pages/01-topic.md
draft unbind --platform claude --session "$CLAUDE_CODE_SESSION_ID"
```

- `ROOT` 必须是已经登记且检查全绿的草稿；
- 绑定写入 `DRAFT_DATA/bindings/<platform>/sha256(session).json`，会话标识本身不进入文件名；
- 绑定后，Claude Code 会话压缩/恢复/重启时 SessionStart 钩子注入短重新绑定信封；未绑定的会话钩子完全静默。

## 故障恢复

- 结构命令提示 `operation.recovery_required` → `draft recover ROOT`。恢复只读取草稿内的锁与操作日志，不依赖平台数据目录。
- `trash` 把页面落地为 7 天保留态（`trash_retained`），不立即移交系统回收站；7 天内可 `restore ROOT OPERATION_ID` 恢复。到期后的系统回收站移交和最终清理由 `recover ROOT` 或普通结构写命令惰性推进（24 小时节流，30 天后最终清理），Agent 不需要维护计时器。
- 普通投递明确失败只产生警告并继续原命令；状态损坏（`operation.inconsistent_state` 等）才停止写入。`restore` 回滚退出 3、`ok=false`，权威包仍保留，不要报告“已恢复”。
- `recover` 报告外部修改 → 有人在操作期间编辑了受管理文件；工具保留现场，由你人工决定取舍，不要删除锁或日志目录。
- 钩子未注入信封 → 检查钩子启动器是否存在、`DRAFT_DATA` 是否指向存在的数据目录、当前会话是否 bind 过。
- 想完全移除 → 卸载插件并删除对应平台数据目录。
