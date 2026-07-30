# D8：独立项目与跨平台插件结构

> 状态：`已确认`
>
> 产出：[结论汇总](conclusions.md)
>
> 父主题：[递归讨论草稿方法论与 Skill 设计](../2026-07-18-recursive-discussion-notebook-skill.tmp.md)

## 当前结论

该 skill 应成为独立 Git 项目，并至少作为 Codex 和 Claude Code 两个平台的插件交付。

采用一个 marketplace 仓库和一个实际插件子目录：

```text
recursive-discussion-notebook/
├── .agents/
│   └── plugins/
│       └── marketplace.json
├── .claude-plugin/
│   └── marketplace.json
├── plugins/
│   └── recursive-discussion-notebook/
│       ├── .codex-plugin/
│       │   └── plugin.json
│       ├── .claude-plugin/
│       │   └── plugin.json
│       ├── skills/
│       ├── hooks/
│       │   ├── codex-hooks.json
│       │   └── claude-hooks.json
│       ├── bootstrap/
│       │   ├── setup.sh
│       │   └── setup.ps1
│       └── runtime/          # 共同 Python 核心和 CLI 薄入口
├── tests/
├── docs/
├── dev/
├── README.md
└── LICENSE
```

仓库根目录的两个 marketplace 都指向 `./plugins/recursive-discussion-notebook`。安装时只交付该插件子目录，不把项目测试、设计资料和发布工具一起复制到用户插件缓存。

## 共同部分与平台差异

两个平台共享：

- skill 正文；
- 草稿页面和关系规则；
- 只读检查器；
- 写入边界和幂等规则；
- 同一个 Python 运行核心；
- 同一可执行包中的 CLI 入口。

两个平台分别维护：

- 插件清单；
- 钩子声明；
- 平台如何在压缩、恢复或适用的启动入口注入短重新绑定信封；
- 平台持久数据目录和会话标识怎样转换为共同绑定状态。

平台适配层只负责生命周期重新绑定和环境入口，共同核心负责安全文件操作与检查。主 Agent 通过 CLI 使用共同核心。第一版不交付 MCP、无界面维护 Agent 启动器或专用维护会话。

平台上下文不能由共同核心根据“哪些环境变量存在”自行猜测。Codex 为兼容插件生态可能同时提供 Codex 与 Claude 风格的变量；同一项目也可能被两个平台先后使用。平台钩子的薄入口必须把 `platform`、宿主提供的 `session_id` 和该平台的数据目录规范化后显式传入绑定命令。Codex 入口只接受 `PLUGIN_DATA`，Claude Code 插件入口只接受 `CLAUDE_PLUGIN_DATA`；另一个变量即使存在也忽略。Tavern 孵化期没有插件数据目录，由 setup 固定使用 `.claude/discussion-notebook-data/`，并在项目级钩子配置中传给入口；该生成目录不进入版本控制。来源不明确或目录不可写时拒绝绑定，不退回当前工作目录。普通检查、改名和移动等草稿结构命令不依赖会话身份。

### Tavern 孵化期的例外

第一阶段只在 Tavern 的 `.claude/skills/discussion-notebook/` 中孵化。这个位置能让 Claude Code 发现 skill，却不是一个带 `.claude-plugin/plugin.json` 的已安装插件，所以放在其中的 `hooks/claude-hooks.json` 不会自动被加载。

孵化期由 `bootstrap/setup.sh` 或 `bootstrap/setup.ps1` 检查环境，并输出一段项目级 Claude `SessionStart` 钩子配置以及移除说明。用户明确确认后，把该配置接入 Tavern 的 `.claude/settings.local.json`；setup 不静默改写用户设置。自动重新绑定的孵化期验收只在这一步接线完成后成立。独立插件发布后再由插件清单和随包钩子接管，不要求用户保留孵化期配置。

## 官方安装模型

Codex 先添加 marketplace，再安装插件：

```bash
codex plugin marketplace add owner/repo
codex plugin add recursive-discussion-notebook@marketplace-name
```

Codex marketplace 使用 `.agents/plugins/marketplace.json`，实际插件使用 `.codex-plugin/plugin.json`。技能、钩子和脚本位于插件根目录，而不是 `.codex-plugin/` 内。

Claude Code 同样先添加 marketplace，再安装插件：

```text
/plugin marketplace add owner/repo
/plugin install recursive-discussion-notebook@marketplace-name
/reload-plugins
```

Claude Code marketplace 使用仓库根目录的 `.claude-plugin/marketplace.json`，实际插件使用自身目录里的 `.claude-plugin/plugin.json`。

Claude Code 会把插件目录复制到版本缓存，安装后的插件不能依赖插件目录之外的 `../shared` 文件。因此，所有运行时必需内容都必须放在实际插件子目录中。

## Ponytail 提供的实证

[DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) 在一个仓库根目录中同时提供：

- `.agents/plugins/marketplace.json`；
- `.claude-plugin/marketplace.json`；
- `.codex-plugin/plugin.json`；
- `.claude-plugin/plugin.json`；
- 共同的 `skills/` 和 `hooks/`。

这证明一个仓库、一份共同 skill 和两份平台清单可以共存。

本项目不完全复制 Ponytail 的“仓库根目录就是插件根目录”做法。Ponytail 的做法最简单，但会让测试、文档和其他开发材料也处于插件目录内。本项目使用单独插件子目录，保留共享核心的同时缩小安装产物。

Ponytail 能共用同一个钩子文件，是因为它使用两个平台都支持的命令钩子。本项目虽然不注册 `PreCompact`，但两个平台的重新绑定事件格式、持久数据目录和插件清单仍不同，因此平台钩子文件先保持分开。

## 未采用的方案

### 仓库根目录就是插件根目录

优点是最简单，缺点是整个开发仓库都可能进入插件缓存。若未来项目始终极小，可以重新评估；当前不采用。

### Codex 与 Claude Code 各自一份完整插件

这会复制 skill、运行代码和规则，并引入长期行为漂移。只有平台格式真正无法共存时才应采用；当前没有这个必要。

## 资料来源

- [Codex：Build plugins](https://learn.chatgpt.com/docs/build-plugins)
- [Codex：Developer commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli#cli-codex-plugin)
- [Claude Code：Create plugins](https://code.claude.com/docs/en/plugins)
- [Claude Code：Discover and install plugins](https://code.claude.com/docs/en/discover-plugins)
- [Claude Code：Plugin marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
- [Claude Code：Plugins reference](https://code.claude.com/docs/en/plugins-reference)
- [Ponytail](https://github.com/DietrichGebert/ponytail)

## 后续关系

[D9](09-python-uv-and-bootstrap.md) 已确认 Python 与 uv 的运行环境边界；[D10.20](10-maintenance-agent-launch/10-20-mcp-and-maintenance-agent-scope.md) 已确认第一版只交付共同 Python 核心和 CLI，MCP 与独立维护 Agent 延后。
