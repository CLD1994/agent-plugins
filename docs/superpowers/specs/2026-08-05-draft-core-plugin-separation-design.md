# Draft 核心与插件适配层同仓库拆分设计

## 状态与优先级

状态：已实施（plugin 0.3.0 / runtime 0.3.0）。

本规格重划 Draft 核心能力与 Codex/Claude 插件增强能力的物理目录、命令入口、状态所有权和
失败边界。实施完成后，它取代以下文档中与 bridge、binding、`SessionStart`、插件数据目录、
独立模式和入口职责冲突的结论：

- `docs/design.md`；
- `docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md`；
- `docs/superpowers/specs/2026-08-04-draft-single-python-launcher-design.md`。

草稿格式、结构操作、恢复协议和检查诊断中与本规格无关的结论继续有效。实施切换前，现有
入口和文档仍描述当前可执行行为；不得提前发布尚未实现的新命令。

## 背景与决策

当前 `plugins/draft/skills/draft/` 同时拥有两类职责：

1. Draft 核心：格式、检查、结构操作、恢复、独立 CLI 和 runtime 环境；
2. 插件增强：宿主 Hook、bridge、会话 binding、宿主 `data_dir`、重新绑定信封和插件错误码。

这使本应能独立运行的 skill 知道 Codex、Claude、插件安装根和插件数据目录，也使插件状态与
领域运行时使用同一个入口和包。

曾评估把 `skills/draft` 提取为独立 Git 仓库，并以 submodule 挂载到
`plugins/draft/skills/draft/`。该方案不在本次实施：远程 Git marketplace 没有跨宿主保证
递归初始化 submodule，而插件安装产物必须直接包含 `skills/draft/SKILL.md` 及其脚本。为
submodule 额外维护实体化发布分支或独立发布仓库，在当前阶段不值得。

因此采用以下决策：

- `plugins/draft/skills/draft/` 继续是本仓库中的普通目录；
- 在同一仓库内完成严格职责拆分，使该子树可独立运行且不反向依赖插件；
- 远程 marketplace 继续直接安装 `plugins/draft/`，不需要 submodule、生成式发布树或安装后
  下载；
- 核心 runtime 直接作为本地包源交给 `uvx`，环境解析、隔离和缓存归 uv 所有；这不要求发布
  Python 包，也不改变 marketplace 安装的源码来源；
- 等宿主出现可靠的插件依赖/递归源码能力，或 Draft 具备独立包发布条件后，再评估仓库提取
  与短命令 `uvx draft`。

## 目标

1. `skills/draft` 只包含 Draft 领域能力和独立运行所需资源。
2. 所有宿主相关增强都位于 `plugins/draft/scripts/`、`hooks/` 或插件级文档中。
3. 插件状态继续严格归属宿主分配的 `data_dir`，不迁移到用户级 Draft 目录。
4. 独立 Draft 命令不需要 bridge，也不伪造插件上下文。
5. 插件适配层只依赖 Python 标准库，不启动或导入 Draft 核心。
6. 远程 Git marketplace 安装后无需额外步骤即可发现 skill、执行 Hook 和使用绑定。
7. 目录和依赖边界使 `skills/draft` 将来可以低成本提取为独立仓库。

## 非目标

- 不创建 Git submodule、`.gitmodules`、`dist` 分支或第二个发布仓库。
- 不发布 Python 包，不承诺短命令 `uvx draft`，也不让执行时从远程地址下载 Draft 自身源码。
- 不要求最终用户执行时消费 `uv.lock`，不承诺依赖的补丁版本在不同首次解析时间点完全相同。
- 不新增 `inspect` CLI 或仅为 binding 服务的核心公共 API。
- 不让插件适配层验证目标是否为已登记 Draft。
- 不迁移或合并不同宿主的 binding、日志和 bridge 状态。
- 不改变 Markdown 格式、检查规则、结构写入或恢复语义。
- 不顺手重构与职责拆分无关的 Draft 领域代码。

## 目标目录

```text
plugins/draft/
├── .codex-plugin/
│   └── plugin.json
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── codex-hooks.json
│   └── claude-hooks.json
├── references/
│   └── platform-setup.md
├── scripts/
│   ├── ruff.toml
│   ├── draft.py
│   ├── adapter/
│   │   ├── __init__.py
│   │   ├── bindings.py
│   │   ├── bridge.py
│   │   ├── errors.py
│   │   └── session_hook.py
│   └── tests/
└── skills/
    └── draft/
        ├── SKILL.md
        ├── references/
        │   └── draft-format.md
        └── scripts/
            └── runtime/
                ├── pyproject.toml
                ├── uv.lock
                ├── src/draft/
                └── tests/
```

`plugins/draft/scripts/draft.py` 是插件适配 CLI 的稳定外部入口，同时负责参数解析和命令
分派；不再增加只有它消费的 `adapter/cli.py`。`adapter/` 保存可独立测试的 bridge、binding
和 Hook 协议实现。

Draft 核心不再保留外层 Python 启动器。`plugins/draft/skills/draft/scripts/runtime` 是本地包
源，其中 `draft-runtime` 发行包提供 `draft` 控制台命令。Agent 必须使用当前 skill 的准确
`<skill_root>` 构造 `uvx --from` 路径，不得搜索、猜测其他副本或降级到插件适配入口。

## 责任边界

### Draft 核心

`skills/draft` 拥有：

- `SKILL.md`、Draft 格式和领域工作流；
- `init`、`adopt`、`check`、`move`、`promote`、`collapse`、`trash`、`restore`、`recover`
  及版本输出；`setup` 是 SKILL 中通过版本输出触发 uvx 环境准备的工作流，不是核心 CLI
  子命令；
- Markdown 解析、Draft 图、检查、结构操作、回收和恢复；
- 独立 runtime 的版本和有界依赖声明；
- 与上述领域能力直接相关的错误码、JSON 和普通文本输出。

`skills/draft` 不得包含或引用：

- `PLUGIN_ROOT`、`PLUGIN_DATA`、`CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA`；
- bridge、binding、SessionStart Hook 或宿主名称；
- 插件安装根、插件 manifest、marketplace 或插件日志；
- `DRAFT_DATA`、`DRAFT_SKILL_PATH` 等旧插件注入变量；
- `bind`、`unbind`、`hook session-start` 或 `plugin.*` / `binding.*` 插件错误码。

### 插件适配层

`plugins/draft/scripts` 拥有：

- Codex 与 Claude 宿主变量归一化；
- bridge 发布、加载、冲突检测、reset 和安全文件操作；
- `bind`、`unbind` 及 binding 文件协议；
- `hook session-start HOST` 的载荷解析、日志和模型可见信封；
- 插件入口路径、skill 路径和宿主 `data_dir` 的严格布局校验；
- 插件错误码、普通输出和 JSON 输出。

适配层只依赖 Python 标准库。它不得导入 `draft` 包、调用核心 CLI、读取 Draft 内部状态或
复制 Draft 格式判断。

## 两套命令入口

### 核心命令

Agent 从已加载 skill 得到 `<skill_root>`，并执行：

```text
uvx --isolated --from "<skill_root>/scripts/runtime" draft <core-command> ...
```

`setup` 工作流执行：

```text
uvx --isolated --from "<skill_root>/scripts/runtime" draft --version
```

`--isolated` 只保证忽略通过 `uv tool install` 安装的同名工具，仍复用 uv 的工具环境缓存；
不得增加 `--no-cache`。不使用 `--no-env-file`，uv 的用户级配置和已有进程环境按 uv 标准语义
生效。

独立运行和插件安装后的普通 Draft 操作使用同一本地包源和同一命令。命令不依赖当前工作
目录中的项目环境，也不从包索引查找名为 `draft` 的发行包。

### 插件命令

SessionStart 信封提供 `<plugin_entry>` 的规范绝对路径。Agent 执行：

```text
uv run <plugin_entry> bind ROOT --session SESSION_ID [--last-page-hint PAGE]
uv run <plugin_entry> unbind --session SESSION_ID
uv run <plugin_entry> plugin-context reset
```

宿主 Hook 执行：

```text
uv run "${PLUGIN_ROOT}/scripts/draft.py" hook session-start codex
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/draft.py" hook session-start claude
```

`HOST` 只接受下表字面量：

| `HOST` | 插件根 | 插件数据目录 |
| --- | --- | --- |
| `codex` | `PLUGIN_ROOT` | `PLUGIN_DATA` |
| `claude` | `CLAUDE_PLUGIN_ROOT` | `CLAUDE_PLUGIN_DATA` |

未知宿主、缺失变量、非绝对数据目录或规范插件根与当前入口布局不一致都属于 Hook 上下文错误，
不得尝试另一组宿主变量。

`hook session-start` 是宿主 Hook 协议命名空间，不使用 `_session-start` 私有式拼写。它不是
权限边界；严格参数和宿主环境校验才是协议边界。

`plugin-context reset` 保留现有恢复职责：只删除以当前插件入口为安装键的临时 bridge JSON
和冲突哨兵，不读取或删除任何宿主 `data_dir`、binding 或日志。锁文件永久保留，作为所有
进程共同使用的同步锚点，reset 不删除它。下一次合法 SessionStart 会重新发布 bridge。
该命令不属于 Draft 核心。

插件入口继续使用 PEP 723 元数据声明兼容 Python 和空依赖，因此只要求 `uv`，不需要插件
自己的虚拟环境、runtime 项目或第三方包。

## 核心运行环境与依赖范围

核心 runtime 由 `uvx --isolated --from <local-runtime-path> draft ...` 构建并执行。工具环境
位于 uv 自己的平台缓存目录，属于可丢弃、可自动重建的 uv 缓存；Draft 不选择环境路径、
不创建 `envs/<runtime_version>`，也不定义 `runtime.cache_unavailable`。不同来源、解析结果和
Python 版本的环境隔离由 uv 的工具缓存语义负责。

runtime 的直接运行依赖使用基于当前已验证版本的补丁窗口：

```toml
dependencies = [
    "markdown-it-py>=4.2,<4.3",
    "Send2Trash>=2.1,<2.2",
]
```

本地源码首次构建还会解析构建后端，因此 `[build-system].requires` 同样使用有界补丁窗口：

```toml
requires = ["hatchling>=1.31,<1.32"]
```

直接依赖的新补丁版本可以在范围内被首次解析采用；新次版本必须先通过兼容性门禁，再显式
扩展范围。Draft 不直接约束未直接使用的传递依赖；其可接受范围由直接依赖的包元数据决定。

`runtime/uv.lock` 保留，但只用于仓库开发、测试和静态门禁的可重复环境。最终用户的 `uvx`
命令不读取该锁文件，锁文件内容也不是最终用户执行协议。首次执行可能从配置的包索引下载
第三方运行依赖和构建依赖；缓存命中后复用已有工具环境。该设计不承诺无网络首次执行，但
不得联网取得 Draft 自身源码。

核心不读取 bridge，不区分“独立模式”和“插件增强模式”，也不拥有任何业务状态目录。binding、
宿主日志和其他插件业务状态仍只能写入宿主 `data_dir`，不得写入 uv 缓存。

## bridge 协议

只有 SessionStart Hook 能可靠取得宿主 `data_dir`；普通 Agent 工具进程不继承该变量。
因此 bridge 仍是插件适配层的必要协议。

bridge 保持严格两字段 JSON：

```json
{
  "schema_version": 1,
  "data_dir": "/absolute/host/plugin/data"
}
```

安装键改为 `plugins/draft/scripts/draft.py` 规范路径的 SHA-256。bridge 位于当前用户安全运行
目录中的 `draft/bridges/`，保留既有所有者、权限、普通文件、锁、原子写入和冲突哨兵规则。

状态严格区分：

| 状态 | 含义 | 普通插件命令行为 |
| --- | --- | --- |
| `missing` | 本安装尚未由 Hook 发布上下文，或临时目录被清理 | `plugin.context_unavailable`，退出 4 |
| `valid` | 唯一、安全的宿主 `data_dir` 已发布 | 继续执行 bind/unbind |
| `invalid` | JSON、字段、路径、权限或文件类型无效 | `plugin.context_invalid`，退出 4 |
| `conflict` | 同一安装键观察到不同宿主数据目录 | `plugin.context_conflict`，退出 4 |

不得把 `missing`、`invalid` 或 `conflict` 降级到全局 Draft 目录，也不得搜索其他插件安装。

## binding 行为契约

binding 表示“宿主会话与工作目录的关联”，不证明目录是有效或已登记 Draft。

### 输入

- `session_id` 是宿主提供的不透明字符串，原样参与摘要和载荷，不截断、规范化或解释；必须能以严格 UTF-8 编码（孤立代理码位非法），空字符串合法，不同码点序列（即使视觉相同）映射为不同会话；
- `ROOT` 展开用户目录后必须存在、是目录且自身不是符号链接；保存前规范化为绝对真实路径；
- `last_page_hint` 可省略；若提供，必须是根内非空相对 Markdown 路径，两平台语义下都不是
  绝对路径、不含 `..`，路径段不得经过符号链接，目标当前必须是普通文件。

适配层不检查 `.draft/`、`index.md`、`conclusions.md`、Draft 图或 `draft check` 结果，也不要求
页面提示属于 Draft 图。

### 持久格式

binding 文件位于：

```text
<host_data_dir>/bindings/<sha256(session_id)>.json
```

载荷严格只有：

```json
{
  "session_id": "opaque host value",
  "root": "/canonical/absolute/root",
  "last_page_hint": "pages/topic.md"
}
```

`last_page_hint` 缺失时值为 `null`。不再保存 `skill_name` 或 `skill_digest`；skill 内容变化不
改变会话与目录的关联，重新执行 bind 也不能证明 Agent 已阅读新指令。

写入使用同目录临时文件、文件同步、原子替换和目录同步。现有旧载荷包含额外字段时按严格
协议报告 `binding.invalid`；重新 bind 会原子覆盖为新格式。`unbind` 按 session 键直接安全
删除目标，因此即使旧载荷无法解析也能解除，且目标不存在时成功返回 `removed=false`。

### CLI 输出

bind/unbind 保留普通文本和 `--json` 两种输出；`--json` 在子命令前后都可接受。成功 JSON
分别为：

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

普通文本保持一行结果或一行稳定错误，不输出 traceback。`plugin-context reset` 成功时保持静默
并退出 0；失败使用普通插件错误格式和退出 4。

## SessionStart 数据流

`hook session-start HOST` 按以下顺序执行：

1. 根据 `HOST` 只读取对应宿主的 root/data 变量；不混用两个宿主的变量；
2. 验证插件根、入口和 `skills/draft/SKILL.md` 均位于预期布局；
3. 发布或验证 bridge；
4. 最多读取 65,536 字节标准输入（按字节而非字符计长，超限检查先于 UTF-8 解码），严格 UTF-8 解码并解析验证 `SessionStart` 载荷；
5. 提取原始 `session_id` 和启动 `source`；
6. 从宿主 `data_dir` 加载 binding；
7. 记录尽力而为的宿主 Hook 日志；
8. 输出会话事实、重新绑定信封或诊断。

输出分支：

| 状态 | 模型可见输出 |
| --- | --- |
| 无 binding | 原始 session ID、规范插件入口，以及 bind/unbind 命令格式 |
| binding 有效且根存在 | 根、仍存在的可选页面提示、当前 skill 规范路径、插件入口；要求重新完整阅读 skill，并在修改前运行核心 `check` |
| 根消失 | 明确诊断，要求重新选择目录并 bind；不静默沿用旧根 |
| 页面提示后来消失或不再是普通文件 | 省略提示，保留根 binding |
| binding 损坏或 session 载荷不一致 | 明确诊断，要求重新 bind；不解释为未绑定 |
| bridge、插件布局或宿主上下文失败 | 明确插件诊断；不伪造 `data_dir` |

每次恢复都要求读取当前 `SKILL.md`，不使用摘要推断 Agent 是否已经阅读。

信封中的文件和入口路径使用规范绝对路径；展示命令时对路径加引号。`session_id` 仍作为单独的
不透明参数展示，Agent 必须原样传回，不把它拼接成路径、摘要或另一种身份。

Hook 的协议错误不得阻止宿主会话启动：脚本可执行时，`hook session-start` 对载荷、bridge、
binding、布局和日志错误都输出或记录诊断并返回 0。`uv` 无法启动脚本属于宿主执行层失败，
不在脚本能够转换的范围内。

普通核心命令只有在 `draft` 控制台命令启动后才进入 Draft 的错误协议。`uvx` 自身的 Python
发现、包索引访问、依赖解析、构建或缓存错误直接保留 uv 的标准错误输出和退出码；外层没有
启动器将其转换为 Draft 错误码或 `--json` 载荷。调用方不得把 uvx 启动失败解释为 Draft
检查失败、目标不存在或命令成功。

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

OSError 必须在最窄文件操作边界转换为稳定错误码并保留异常链，不向 Agent 泄漏 traceback。
已有主错误时，清理失败以稳定明细码 `cleanup.unlock_failed`、`cleanup.close_failed`、
`cleanup.unlink_failed` 追加到主错误 `details`（顺序与执行顺序一致），普通文本与 JSON 均
可见，主错误码、路径、退出码与异常链不变；Hook 只显示净化后的诊断码。持久 JSON 文件的
无效 UTF-8 按损坏处理（`load_context` 报 `plugin.context_invalid`、`load_binding` 报
`binding.invalid`、`publish_context` 原子重写）；含孤立代理码位的 `data_dir`、binding 字段
或插件入口路径按对应持久载荷或上下文无效处理。Hook 标准输入超过 65,536 字节报
`hook.stdin_too_large`、非严格 UTF-8 或载荷非法报 `hook.stdin_invalid`，均返回 0。Hook 日志
对 `logs/` 与 `hook.jsonl` 做打开前 `lstat` 类型检查，异常类型不打开并报
`hook.log_unavailable`。目录 fsync 是明确的尽力而为操作；binding/bridge 的创建、写入、
替换、删除和锁操作是强制步骤。

## 文档边界

`skills/draft/SKILL.md` 和 `references/draft-format.md` 只记录核心命令与 Draft 领域协议，删除：

- bind/unbind 命令；
- SessionStart、bridge、宿主 `data_dir`；
- Codex/Claude Hook 接线；
- 插件增强模式与相关错误码。

插件安装、Hook、bridge、binding 和故障恢复文档迁移到
`plugins/draft/references/platform-setup.md`。模型日常不需要主动读取该文档；SessionStart
信封直接提供完成 bind/unbind 所需的准确路径和命令。

## 版本关系

插件版本与核心 runtime 版本不再要求数值相同：

- 插件 manifest 版本描述整个安装包；适配层或内嵌核心任何发布级变化都必须提升插件版本；
- Codex 与 Claude 的 plugin manifest 必须声明相同插件版本；
- runtime `project.version` 只描述 Draft 核心可执行实现；runtime 源码、直接依赖范围、构建
  依赖范围或控制台入口发生发布级变化时必须提升 runtime 版本；
- 只修改适配层时，runtime 版本保持不变；
- 相同 runtime 版本的源码和依赖范围必须不可变，使 uv 的本地包缓存身份与发布语义一致；
- 只更新开发依赖或 `uv.lock` 中不影响最终用户解析的条目时，不要求提升 runtime 版本。

由于适配层不调用核心 API，不需要运行时兼容握手。远程 marketplace 始终安装同一插件提交
中的适配层与核心普通文件。

## 迁移策略

迁移采用一次发布内的行为切换，不维持两个长期入口协议：

1. 先在插件根建立标准库适配模块及独立测试，复制现有 bridge、binding 和 SessionStart
   行为并按本规格调整；
2. 将 Hook 改到插件入口，并验证新入口能独立完成 bridge、bind、unbind 和 SessionStart；
3. 从核心 CLI、模型和错误码中删除插件分支，删除核心外层 launcher，改由 SKILL 通过本地
   runtime 路径执行 `uvx --isolated --from`；
4. 从核心 SKILL/格式参考移除插件说明，新增插件级平台文档；
5. 删除核心中的 `bindings.py`、`session_hook.py` 及不再使用的测试；
6. 为运行依赖和构建依赖增加补丁版本窗口，更新主设计、manifest 版本、runtime 版本和开发
   锁文件；
7. 从复制后的完整 `plugins/draft/` 使用本地 runtime 包源执行端到端烟雾测试。

旧 binding 不进行静默迁移。首次 SessionStart 将其报告为无效；用户显式重新 bind 后写入新
格式。bridge 两字段协议保持版本 1，不需要迁移。

## 测试与质量门禁

### 核心门禁

- 全量领域测试继续通过；
- 从 `skills/draft` 复制出的临时目录能通过路径式 `uvx` 独立执行版本输出、`init` 和 `check`；
- 核心测试不依赖插件 manifest、Hook 或仓库父目录；
- 核心子树不存在宿主环境变量、bridge、binding、SessionStart 和插件错误码引用；
- 删除 launcher 后不存在 Draft 自管缓存路径、环境目标校验或平台缓存选择测试；
- 使用最低边界 `markdown-it-py==4.2.0`、`Send2Trash==2.1.0` 和 `hatchling==1.31.0` 执行构建
  与领域测试，并在独立新缓存中解析各范围内最高可用组合再次执行；
- 同一命令连续运行时复用 uv 缓存；预装同名全局工具时，`--isolated --from <local-path>` 仍
  执行当前 skill 的 runtime；
- `uv.lock` 继续通过开发锁一致性检查，但分发 smoke test 不依赖该锁文件被 uvx 消费。

### 适配层门禁

- bridge 的缺失、有效、无效、冲突、锁、权限、原子写入和 reset 状态矩阵；
- binding 的三字段严格 schema、原始 session ID、路径摘要、根路径和页面提示安全矩阵；
- bind 覆盖未登记普通目录成功，证明适配层没有调用 Draft 核心；
- unbind 对存在、不存在、损坏 binding 的行为；
- Codex/Claude SessionStart 载荷、匹配 source、空/特殊 session ID、超限/无效 JSON；
- 无 binding、有效 binding、根消失、提示消失和损坏 binding 的输出分支；
- Hook 的所有协议失败返回 0，普通 bind/unbind 保持退出 2/4 区分；
- 适配层在未安装 Draft runtime 和第三方依赖的环境中仍可运行。

### 分发门禁

- 两个 manifest 指向 `./skills/` 和各自 Hook 文件；
- Hook 命令严格使用 `scripts/draft.py hook session-start HOST`；
- `plugins/draft/skills/draft/SKILL.md`、runtime 项目和适配入口都是普通文件，核心外层
  `scripts/draft.py` 不存在；
- 仓库不存在 `.gitmodules`，skill 路径不是 gitlink；
- 把 `plugins/draft/` 复制到无 `.git`、无仓库父目录的临时位置后：
  1. 模拟 Codex 和 Claude Hook 发布 bridge；
  2. 使用信封中的插件入口 bind/unbind；
  3. 使用 `uvx --isolated --from <copied-skill>/scripts/runtime draft` 执行核心 smoke test；
  4. 证明 Draft 源码只来自复制后的插件目录，插件业务状态只进入宿主临时 `data_dir`；允许
     可重建工具环境进入该测试指定的独立 uv 缓存；
- 使用空 uv 缓存完成一次允许联网的首次解析，再使用同一缓存和 `--offline` 重复 smoke test，
  证明第三方依赖缓存可复用；不承诺空缓存离线启动；
- 插件适配层从 `plugins/draft/scripts/ruff.toml`、核心从 runtime `pyproject.toml` 的
  `[tool.ruff]` 自动发现配置；两侧规则一致，但 `src` 分别解析为插件 `scripts/` 和核心
  `runtime/src/`，门禁不跨边界强制指定配置；
- pytest、pyright strict、ruff check 和 ruff format 全部门禁通过。

## 验收标准

- 远程 Git marketplace 取得一个普通、自包含的 `plugins/draft/` 目录，不执行 submodule，
  也不在运行时下载 Draft 自身源码；第三方依赖首次解析仍可访问配置的包索引；
- `skills/draft` 从任意父目录复制后仍能运行核心命令；
- 插件适配层不导入或执行 Draft 核心；
- 核心不知道宿主、bridge、binding 或插件数据目录；
- binding 只表达 session 到目录的关联，允许关联尚未 init/adopt 的现存目录；
- SessionStart 每次提供当前 skill 和插件入口的准确路径，不用摘要使 binding 失效；
- 插件状态只写入当前宿主 `data_dir`；uv 缓存只保存可重建的构建产物、依赖和工具环境，
  不包含 Draft 业务状态；
- 普通核心命令精确使用当前 skill 内的本地 runtime 包源，不会被已安装的同名工具替代；
- 现有远程 marketplace 安装路径保持直接可用；
- 将来提取独立仓库时，核心子树无需先清理宿主专用逻辑。
