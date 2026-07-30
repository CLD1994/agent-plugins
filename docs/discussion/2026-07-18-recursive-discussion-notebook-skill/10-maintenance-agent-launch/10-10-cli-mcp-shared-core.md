# D10.10：CLI 与 MCP 的共同核心和失败接续

> 状态：`已确认，第一版 CLI-only`
>
> 产出：[结论汇总](../conclusions.md)
>
> 父主题：[D10 维护 Agent 的启动方式](index.md)
>
> 依赖：[D10.3 主会话等待与失败放行](10-3-wait-and-release.md)、[D10.6 多文件结构操作的崩溃恢复](10-6-structural-operation-recovery.md)、[D10.9 Agent 文件能力与内部目录隔离](10-9-file-capability-isolation.md)
>
> 第一版修正：[D10.20](10-20-mcp-and-maintenance-agent-scope.md) 已确认只交付共同 Python 核心和 CLI。MCP 适配层与耐久维护任务不进入第一版。

## 历史完整目标

本地 MCP 的底层工具同时提供 CLI。维护 Agent 通过 MCP 整理草稿，平常的主 Agent 通过 CLI 执行同样操作。压缩前维护 Agent 未完成时，主 Agent 能读取同一个待处理任务和操作日志，使用同一份 skill 与同一套校验规则继续完成，而不是换一套流程重新猜测。

这个目标成立，但“底层是 CLI”有两种不同实现，需要区分。

## 历史候选路线

### A：CLI 与 MCP 分别实现业务逻辑

两端命令名称可以相同，但路径检查、操作日志或错误处理会逐渐漂移。它不能真正保证安全接续。

### B：MCP 每次调用都启动一次 CLI 子进程

业务规则只存在于 CLI，MCP 把结构化参数转换为命令行，再解析标准输出。

这确实避免两份业务逻辑，却增加了进程启动、参数转义、文本输出解析、取消和错误映射。Windows、WSL 与普通 Unix 的命令行差异还会进入每一次工具调用。MCP 本来已经是 Python 进程，没有必要反复绕回子进程。

### C：同一个可执行包，两层薄入口，共享同一 Python 核心

插件安装一个 CLI 可执行程序，暂称 `discussion-notebook`。它同时提供普通子命令和 MCP 服务入口：

```text
discussion-notebook page list
discussion-notebook page read ...
discussion-notebook page apply ...
discussion-notebook page move ...
discussion-notebook page trash ...
discussion-notebook page restore ...
discussion-notebook task pending
discussion-notebook task show <task-id>
discussion-notebook check
discussion-notebook mcp serve
```

CLI 适配层和 MCP 适配层都直接调用同一组 Python 函数，不通过子进程互相调用：

```text
主 Agent ── CLI 入口 ──┐
                       ├── 共同核心 ── 草稿、操作日志、锁、检查器
维护 Agent ─ MCP 入口 ─┘
```

共同核心是唯一保存业务规则的位置。CLI 负责把命令参数转换为结构化调用并输出人类可读结果或 JSON；MCP 负责把工具参数转换为同一调用并返回结构化结果。

## 未来自动维护若恢复，需要共享什么

只共享代码还不够。两个入口还必须共享：

- 同一个草稿网络标识；
- 同一个耐久任务编号；
- 同一份操作日志和步骤状态；
- 同一个单写者锁；
- 同一套内容摘要前置条件；
- 同一份机械检查结果和完成凭据。

例如维护 Agent 处理任务 `T17`，完成两个操作后崩溃。任务仍是待处理状态，操作日志能证明前两步已经完成。主 Agent 在压缩后通过 CLI 查看 `T17`，按照同一 skill 继续剩余语义判断；再次提交已完成步骤时，共同核心返回已有结果，不重复改文件。

CLI 不负责替代 Agent 进行语义推理。它只提供安全的文件操作、任务读取和机械检查；主 Agent 与维护 Agent 使用同一份 skill 判断页面是否应该拆分、改名或删除。

## 已确认结论，经 D10.20 修正

第一版采用“共同 Python 核心 + CLI 薄入口”。所有路径校验、结构操作、操作日志、回收站和检查规则只在核心中实现，CLI 负责参数转换和输出。

第一版不创建 MCP 服务、MCP 工具名、耐久维护任务、完成凭据或任务接管状态，也不建立为未来适配准备的接口类。没有调用者的扩展点不进入代码。

未来若出现权限受限的独立维护 Agent，MCP 作为薄适配层直接调用已有核心函数，不得逐次启动 CLI 子进程或重新实现业务规则。D10.9 的 MCP-only 文件能力同时成为该执行者的强制安全边界。

第一版只有用户当前主 Agent 这一类主动写者，不需要 D10.11 的后台任务接管协议。多文件操作自身仍由 D10.6 的锁、内容摘要和操作日志保护。
