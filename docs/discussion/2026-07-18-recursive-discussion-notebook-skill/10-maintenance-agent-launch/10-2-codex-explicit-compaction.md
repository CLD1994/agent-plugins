# D10.2：Codex 维护会话的显式压缩

> 状态：`历史条件方案，第一版不采用`
>
> 父主题：[D10 维护 Agent 的启动方式](index.md)
>
> 依赖：[D10.1 复用专用维护会话](10-1-reusable-maintenance-session.md)
>
> 第一版结论：[D10.19](10-19-precompact-necessity.md) 已取消专用维护会话；第一版不接入维护会话显式压缩。本页只说明未来若重新引入可恢复维护会话时的条件方案。

## 要解决的问题

D10.1 已确认每次维护成功后由协调器请求并验证会话压缩。Claude Agent SDK 对此提供了明确的 `/compact` 控制和完成事件；Codex 的普通 `codex exec resume` 支持恢复会话，但没有公开的显式压缩子命令。

Codex app-server 提供 `thread/resume` 和 `thread/compact/start`。当前需要决定是否为了确定性压缩而引入这一控制接口。

## 已核实事实

- 当前本机 `codex-cli 0.145.0-alpha.18` 提供 `codex app-server`，但命令帮助仍将它标为实验性能力。
- `codex exec resume <会话标识> <提示>` 是正式的非交互恢复入口。
- app-server 的 `thread/compact/start` 可以指定会话并返回压缩进度事件。
- 没有官方依据表明把字符串 `/compact` 当作 `codex exec resume` 的普通提示，就等同于执行 Codex 的会话压缩控制命令，因此不能依赖这种猜测。

## 候选方案

### A：所有维护操作都改用 app-server

协调器启动 app-server，通过 JSON-RPC 完成会话创建或恢复、维护回合、结果事件和压缩。

它提供最完整、统一的生命周期控制，但第一版必须自行处理更多事件、审批、失败状态和协议兼容，而且主要接口目前仍带实验性标记。

### B：维护回合使用 codex exec，压缩时短暂使用 app-server

正常维护继续使用较简单的 `codex exec` 或 `codex exec resume`。维护完成且写入凭据后，协调器启动一个短暂的 app-server 进程，只执行初始化、恢复指定会话、请求压缩和等待完成，然后退出。

它保留成熟的非交互执行路径，同时只为缺失的压缩控制承担最小 app-server 接入面。短暂进程不是常驻维护服务。

### C：完全不显式压缩，依赖 Codex 自动压缩

这是最简单的路线，也不会影响草稿维护正确性，但不能保证每个任务后都收缩维护会话。它适合作为 app-server 不可用或协议不兼容时的退路。

## 当时确认的条件方案

第一版采用 B，把 C 作为兼容退路；不采用 A。

具体顺序是：

1. 使用 `codex exec` 创建或恢复维护会话；
2. 完成草稿更新、检查和完成凭据；
3. 设置维护子进程标记，启动短暂 app-server；
4. 恢复同一会话并请求 `thread/compact/start`；
5. 收到压缩完成事件后退出；
6. app-server 不可用、超时或协议不兼容时，记录“维护已完成、显式压缩未完成”，依赖 Codex 自动压缩，不把维护任务重新执行一遍。

该方案需要声明最低 Codex 版本，并在 setup 健康检查中探测 app-server 能力，不能只根据版本号猜测。

## 官方资料

- [Codex：使用 codex exec 和 resume](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex app-server：thread/resume 与 thread/compact/start](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
