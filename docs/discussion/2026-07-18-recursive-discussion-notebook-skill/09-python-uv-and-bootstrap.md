# D9：Python、uv 与首次环境搭建

> 状态：`已确认`
>
> 产出：[结论汇总](conclusions.md)
>
> 父主题：[递归讨论草稿方法论与 Skill 设计](../2026-07-18-recursive-discussion-notebook-skill.tmp.md)
>
> 依赖：[D8 独立项目与跨平台插件结构](08-project-and-platform-structure.md)

## 已确认部分

- 插件可以要求用户环境提供额外运行时。
- 优先使用 Python 技术栈，因为它常见、容易理解，也适合建立隔离的虚拟环境。
- 使用 uv 管理 Python、依赖和虚拟环境。
- skill 必须负责指导环境检查和搭建，包括安装 uv、准备 Python 和建立运行环境。
- Codex 与 Claude Code 分别在自己的持久插件数据目录维护独立虚拟环境，不跨平台共享。
- 环境未准备好或损坏时，生命周期入口只给出明确诊断和 setup 指引；第一版没有必须在压缩前保存的插件输入，也不注册 `PreCompact`。
- 插件运行时固定 Python 3.14 主次版本，补丁版本随 uv 采用当前最新的 3.14.x。
- 依赖原则是合理利用已有成熟项目，避免重复造轮子；不把“尽量只用标准库”当作目标。

“负责指导”不等于生命周期钩子可以在后台静默安装软件。安装外部程序、下载 Python 和修改用户环境应发生在明确的 setup 流程中，并让用户能够看见将要执行的操作。

## 第一性原理约束

首次安装时可能同时缺少 Python 和 uv。因此，不能用 Python 脚本承担“发现 Python 不存在并指导安装 Python”的最外层启动工作，否则形成循环依赖。

setup 和生命周期入口的最外层只应使用平台本身能够启动的极薄预检入口：

- macOS、Linux 和 WSL 使用命令钩子可用的 shell；
- Windows 使用平台提供的 Windows 命令入口；
- 插件内的固定入口分别是 `bootstrap/setup.sh` 和 `bootstrap/setup.ps1`；
- 预检只确认 `uv` 是否可用、环境是否已经准备好；
- 未准备好时给出明确诊断并引导用户调用 setup skill，不在自动入口里临时联网安装。

uv 自身不依赖 Python。安装 uv 后，uv 可以发现已有 Python，也可以下载并管理缺少的 Python。因此推荐顺序是：

1. 检查 uv；
2. 没有 uv 时，按照 uv 官方方式安装；
3. 由 uv 准备插件要求的 Python；
4. 由 uv 创建或同步插件运行环境；
5. 运行插件健康检查；
6. 只有健康检查通过，压缩后重新绑定等自动入口才调用 Python 运行核心。

这仍然满足“安装 Python”，但不要求用户先单独寻找系统 Python 安装器。Python 可以通过 `uv python install` 或项目同步时的自动下载得到。

## 已确认：虚拟环境放入插件数据目录

独立插件发布后，不要把虚拟环境放在以下位置：

- 用户正在工作的项目 `.venv`：会污染用户项目，并可能与其 Python 版本冲突；
- 已安装插件目录：插件升级会更换该目录，且安装缓存不应被当成持久可写状态；
- Git 仓库根目录：用户安装 marketplace 后实际运行的是缓存副本，不一定保留原始仓库路径。

使用两个平台各自提供的持久插件数据目录：

- Codex：`PLUGIN_DATA`；
- Claude Code：`CLAUDE_PLUGIN_DATA`。

环境概念上类似：

```text
plugin-data/
├── venv/
├── installed-runtime-manifest
├── setup-status.json
└── logs/
```

插件发布包内保存 `pyproject.toml` 和 `uv.lock`，插件数据目录保存实际虚拟环境和已同步版本标记。插件升级后比较发布包中的运行清单与数据目录中的版本标记；只有依赖发生变化时才重新同步。

日常运行不依赖激活脚本，也不直接调用虚拟环境里的 shebang 入口，而是始终通过 uv 指向明确的项目与环境运行。这可以避免虚拟环境在 Windows、WSL、容器或插件缓存路径变化后留下失效的绝对 shebang。

两个平台的数据目录彼此独立，因此同一台机器同时安装 Codex 与 Claude Code 插件时，默认会拥有两个虚拟环境。第一版接受这点，以换取清晰的所有权和生命周期；不尝试跨平台共享虚拟环境。

Tavern `.claude/skills/` 孵化期还没有 Claude 插件数据目录，因此显式使用项目本地生成目录 `.claude/discussion-notebook-data/`。这是为了真实试用而设置的临时所有权边界，不进入版本控制，也不在提取独立插件时迁移；它不能被推广为正式插件把虚拟环境放进用户项目的常规做法。

## 已确认：首次搭建与自动入口的边界

setup skill 负责：

- 检测操作系统和当前 shell；
- 检查 uv 与可用 Python；
- 展示将执行的安装方式；
- 在得到授权后安装缺失工具；
- 同步锁定依赖；
- 执行健康检查；
- 给出钩子信任或重新加载平台插件的后续步骤。

在 Tavern 孵化期，setup 还负责输出项目级 Claude `SessionStart` 钩子配置和解除配置的步骤。它不能悄悄修改 `.claude/settings.local.json`；只有用户明确接线后，孵化期才验收自动重新绑定。独立插件安装后，这段临时接线由插件自身的钩子声明取代。

自动生命周期入口负责：

- 快速确认环境是否已经准备好；
- 已准备好时执行短重新绑定等当前入口职责；
- 未准备好时给出可操作提示，不声称重新绑定成功；
- 不在自动入口中安装、升级或修复运行环境。

第一版不注册 `PreCompact`，因此插件环境故障不会阻塞上下文压缩，也没有需要保存的压缩前原始输入。极薄入口只负责让失败可见，并提示用户显式调用 skill 或 setup 恢复。

若极薄入口本身也无法运行，插件不能承诺自动重新绑定；用户仍可通过平台原生 skill 调用和 setup 流程恢复。插件不建立第二份会话转录或待处理输入作为退路。

## 已确认：Python 版本与依赖原则

插件使用 Python 3.14.x：

- 固定 `3.14` 主次版本，避免插件在没有发布新版本的情况下跨到未来 Python 3.15；
- 不固定补丁版本，由 uv 获取当前最新的 3.14.x，以便得到错误修复和安全更新；
- 第一版只承诺和验证 Python 3.14，不为没有真实需求的旧版本建立兼容矩阵；
- 若某个必要依赖或目标平台对 3.14 存在经过验证的阻碍，再依据证据调整，而不是预先退回旧版本。

本项目不采用“标准库优先”或“依赖越少越好”的教条。对每个实际问题，应先调查是否已有合适、维护良好的项目；已有项目能够可靠解决问题时，应合理复用，不自行重造同类实现。

这也不等于无条件增加依赖。依赖必须解决真实问题，并由 uv 锁定和同步；是否采用取决于它带来的能力是否值得其安装、升级、安全和兼容成本。

## 后续修正

[D10.19 第一版还需要压缩前钩子吗](10-maintenance-agent-launch/10-19-precompact-necessity.md) 已确认第一版完全移除 `PreCompact`。本页关于 Python、uv、独立虚拟环境和 setup 指导的结论继续成立；旧的无 Python 压缩前应急保存路线不再适用。

## 官方资料

- [uv：安装 uv](https://docs.astral.sh/uv/getting-started/installation/)
- [uv：安装和管理 Python](https://docs.astral.sh/uv/guides/install-python/)
- [Codex：插件钩子的 `PLUGIN_DATA`](https://learn.chatgpt.com/docs/hooks#plugin-bundled-hooks)
- [Claude Code：插件环境变量和持久数据目录](https://code.claude.com/docs/en/plugins-reference#environment-variables)
