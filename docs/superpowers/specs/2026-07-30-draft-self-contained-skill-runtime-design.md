# Draft 自包含技能运行时与插件增强桥接设计

> **2026-08-04 评审后修正：** 本规格记录的是已经实现的基线。包装脚本、`draft.entry`、
> 清单摘要环境键、不安装 runtime 和按源码树执行的结论，已由
> [单文件 Python 启动入口纠正规格](2026-08-04-draft-single-python-launcher-design.md)取代；
> 纠正实现已完成。两字段 bridge、绑定和领域协议中未被新规格修改的部分继续有效。

## 状态与优先级

状态：已完成用户评审并同步进 `docs/design.md`，已实现并验证。实现依据本规格、主设计和 `docs/superpowers/plans/2026-07-31-draft-self-contained-skill-runtime.md`。

宿主机复验仍待执行：Codex 校验器、真实宿主钩子信任与端到端、沙箱 `data_dir` 写权限、宿主官方文档复核。pwsh 包装脚本复验已被 2026-08-04 纠正设计取消。

本规格定义 Draft 下一阶段的运行时归属、唯一执行入口、插件上下文桥接、Python/uv 环境和会话绑定协议。

术语约定：Agent（智能体）是宿主执行技能指令的主体；skill（技能）是宿主扩展单元，路径和 frontmatter 中保留该协议字面量；runtime（运行时）特指技能携带的确定性 Python 程序；plugin context（插件上下文）是数据目录定位协议；bridge（桥接定位器）是该协议在临时文件系统中的载体；binding（会话绑定）是会话到活动 Draft 的持久记录。后文代码标识和宿主协议名保持原文。

本规格取代[旧路径契约规格](2026-07-30-draft-plugin-manifests-and-path-contract-design.md)中的以下设计结论：

- `plugins/draft/runtime/` 是插件根级组件；
- setup 在 `PLUGIN_DATA/bin/` 生成 Agent 必须调用的动态启动器；
- 普通 Agent 需要取得 `DRAFT_DATA`、`DRAFT_SKILL_PATH` 或动态启动命令；
- 绑定记录以 `platform` 作为路径命名空间。

旧规格中与本规格无关的插件身份、marketplace、Draft 文件格式和无旧产品兼容等结论继续有效。当前规范与旧规格冲突时，以本规格为准。

## 目标

1. `skills/draft/` 自己包含执行 Draft 工作流所需的说明、参考资料和确定性 runtime；单独复制这个 skill 后，核心草稿操作仍可运行。
2. Agent 始终只认识并调用一个稳定入口：`skills/draft/scripts/draft`（PowerShell 使用同职责入口）。
3. 插件体系只增强持久会话绑定、生命周期钩子、持久环境和日志；不改变 Agent 的调用入口。
4. 普通 Agent 不读取、推导或搜索 `PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA`，也不依赖宿主把钩子环境变量传播到普通工具进程。
5. Python/uv 虚拟环境可从锁定清单重建。临时环境和持久环境之间不搬迁目录。
6. 删除没有独立语义的 `platform` 绑定维度；原始 `session_id` 是会话的唯一逻辑身份，SHA-256 只用于生成安全、定长的物理文件名。

## 非目标

- 不让 Agent 从插件缓存目录、用户主目录或 `/tmp` 搜索安装位置或数据目录；
- 不根据 Codex 或 Claude 当前会话 ID 的外形推断文件名安全性；
- 不让钩子修改 shell profile、Codex 全局配置或用户级 `PATH`；
- 不依赖 Claude 专有的 `CLAUDE_ENV_FILE` 保证跨宿主行为；
- 不通过 `PreToolUse.updatedInput` 改写并自动批准 Agent 的命令；
- 不迁移 Python 虚拟环境目录；
- 不为尚未发布的旧 runtime 布局或旧绑定路径提供兼容读取；
- 不在本规格中改变 Draft 的 Markdown 格式、结构操作和恢复协议。

## 责任边界

### 技能

`skills/draft/` 是可独立分发的工作流单元，负责：

- 告诉 Agent 何时使用 Draft；
- 暴露唯一入口 `scripts/draft`；
- 携带 runtime 源码、`pyproject.toml` 和 `uv.lock`；
- 在没有插件上下文时提供独立模式；
- 在存在合法插件上下文时透明进入插件增强模式。

### 插件

插件负责宿主才能准确知道的部署与会话事实：

- 插件根；
- 插件持久数据目录；
- 当前宿主会话 ID；
- 生命周期事件；
- 钩子信任和启用状态。

插件通过钩子发布一个最小数据目录定位器，但不向 Agent 注入另一个启动命令。

### Python 运行时

Python runtime 负责 Draft 的确定性结构和状态协议。它只消费入口已经解析好的准确输入，不扫描插件根、父目录、缓存目录或临时目录。

## 安装产物布局

目标布局为：

```text
plugins/draft/
├── .codex-plugin/
│   └── plugin.json
├── .claude-plugin/
│   └── plugin.json
├── hooks/
│   ├── codex-hooks.json
│   └── claude-hooks.json
└── skills/
    └── draft/
        ├── SKILL.md
        ├── references/
        │   ├── draft-format.md
        │   └── platform-setup.md
        └── scripts/
            ├── draft
            ├── draft.ps1
            └── runtime/
                ├── pyproject.toml
                ├── uv.lock
                ├── src/
                │   └── draft/
                └── tests/
```

`runtime/` 是 `draft` skill 的确定性脚本资源，不再是插件根级伴生组件。钩子和 Agent 都通过 `scripts/draft` 使用同一份 runtime。

## 唯一入口契约

### Agent 可见行为

`SKILL.md` 只指示 Agent 调用相对于本 skill 的 `scripts/draft`。Agent 不根据模式切换命令，不调用 `PLUGIN_DATA/bin/draft`，也不自行拼装 uv 命令。

入口负责：

1. 确定自身规范绝对路径；
2. 取得同一 skill 内的准确 `SKILL.md`、runtime 项目和锁文件路径；
3. 解析插件上下文；
4. 选择临时或持久 Python 环境；
5. 始终向 Python runtime 注入准确的 `DRAFT_SKILL_PATH`；只有插件增强模式才注入准确的 `DRAFT_DATA`，独立模式保持它不存在；
6. 执行用户请求的 `draft` 子命令并原样传播退出码。

Python 包内部不得重新实现这些路径发现规则。

同一入口还提供幂等的环境准备动作：

```text
scripts/draft setup
```

`setup` 只确保当前模式选中的 Python 环境可用并完成自检，不创建会话绑定，不要求 `ROOT`。其他 Draft 子命令也必须在执行前完成同一环境检查，因此 `setup` 是可显式执行的预热动作，不是“漏执行后所有命令必然损坏”的隐藏前置条件。

### PowerShell

`draft.ps1` 与 POSIX `draft` 表达相同行为契约。平台差异只存在于入口实现，不进入 binding、plugin context 或 Python 领域模型。无论实际由哪个包装器启动，用于计算安装实例身份的规范入口都固定为同一 skill 内的 `scripts/draft`；`draft.ps1` 不用自己的路径生成另一份 bridge。

## 运行模式

入口必须显式区分以下状态：

| 状态 | plugin context | Python 环境 | 允许行为 |
| --- | --- | --- | --- |
| `standalone` | 不存在 | 可重建的临时环境 | 不依赖插件持久状态的全部核心命令 |
| `plugin_enhanced` | 合法 | `data_dir` 下的持久环境 | 核心命令、bind/unbind、钩子恢复和持久日志 |
| `plugin_context_invalid` | 存在但损坏、冲突或不可用 | 不启动 | 明确失败，不降级为独立模式 |
| `environment_unavailable` | 任一合法模式 | 环境不能创建或校验 | 明确失败并保留可诊断信息 |

“上下文不存在”是合法独立模式；“上下文存在但无效”不是不存在，不能折叠为独立模式。

## 插件上下文桥接

### 为什么使用桥接文件

Claude Code 的 [`SessionStart`](https://code.claude.com/docs/en/hooks#sessionstart) 可以通过 `CLAUDE_ENV_FILE` 影响后续 Bash 命令；截至本规格日期，Codex 的 [`SessionStart`](https://learn.chatgpt.com/docs/hooks#sessionstart) 文档没有记录等价协议。钩子子进程执行 `export` 不能修改之后由宿主创建的普通工具进程环境。

因此跨宿主共同协议使用一个临时、最小、非权威的 JSON 定位器。它只把宿主钩子知道的 `data_dir` 交给稳定入口，不保存持久状态。

### 桥接键

入口和钩子共同按以下逻辑计算桥接文件名：

```text
installation_key = sha256(UTF-8(realpath(scripts/draft)))
bridge_path = <per-user-runtime-dir>/draft/bridges/<installation_key>.json
conflict_path = <per-user-runtime-dir>/draft/bridges/<installation_key>.conflict
bridge_lock_path = <per-user-runtime-dir>/draft/bridges/<installation_key>.lock
```

`<per-user-runtime-dir>` 是钩子和普通命令都能确定的当前用户临时运行目录。POSIX 优先使用所有权和权限合法的 `XDG_RUNTIME_DIR`，否则在系统临时目录下创建当前用户独占的子目录；Windows 使用当前用户临时目录。不同本地用户不得竞争同一个可写父目录。

`realpath(scripts/draft)` 指同一 skill 内 POSIX 命名的规范入口文件；PowerShell 包装器也解析这个兄弟文件并使用相同键。

`installation_key` 只是安装实例的临时命名键：

- 它不进入 JSON；
- 它不是安全认证或 runtime 内容摘要；
- 它不向 Agent 暴露；
- 它使 Claude、Codex、旧插件缓存版本和新插件缓存版本通常使用不同的桥接文件。

如果两个宿主刻意从同一个物理 skill 路径运行，却提供不同 `data_dir`，发布必须报告 `plugin.context_conflict`，不得采用最后写入者覆盖。由于两字段 JSON 本身无法保存冲突状态，发布者还必须原子创建不含路径信息的 `conflict_path` 哨兵；后续入口先检查该哨兵并停止，不能继续使用冲突前留下的旧 `data_dir`。

### 文件模式

`plugin-context.json` 的完整模式只有：

```json
{
  "schema_version": 1,
  "data_dir": "/absolute/plugin/data/path"
}
```

字段语义：

- `schema_version`：只版本化桥接文件格式；
- `data_dir`：宿主为当前插件安装提供的规范绝对持久数据目录。

不得添加 `platform`、`runtime_version`、`entry_path`、`session_id` 或虚拟环境路径。它们属于其他责任边界，或者可以由入口本身确定。

解析器必须拒绝未知字段，避免生产者误以为某个未被消费的字段已经构成协议。冲突哨兵和锁是 bridge 文件系统状态，不属于 `plugin-context.json`，因此不扩展这个 JSON 模式。

被排除字段的理由固定如下：

| 字段 | 不进入 bridge 的理由 |
| --- | --- |
| `platform` | `data_dir` 已由宿主按插件安装身份隔离，平台不改变 runtime 语义 |
| `runtime_version` | 当前入口可从捆绑清单确定环境身份，不接受钩子重复声明 |
| `entry_path` | 入口从自身位置准确得到 runtime 与 `SKILL.md`，再传一份路径会产生双重真相 |
| `entry_digest` | 它既不能认证同一用户可修改的安装目录，也不负责依赖缓存失效；安装完整性属于宿主安装来源，环境失效由清单键负责 |
| `session_id` | 它是每个 hook 调用的短事实；写进安装级共享文件会让并发会话互相覆盖 |

### 发布

`SessionStart` 插件钩子从宿主环境取得插件根和数据目录，然后直接调用当前插件根内的 `skills/draft/scripts/draft` 内部桥接入口。钩子不得从 `data_dir/bin/` 调用尚未生成的启动器。

内部桥接入口必须能在目标虚拟环境尚不存在时运行。它可以依赖本规格统一要求的 uv/Python 引导能力，但不能依赖待创建的 venv、`data_dir/bin/` 中的生成物或一次更早的 setup；否则首次 `SessionStart` 仍会形成循环依赖。

发布必须：

1. 规范化入口路径和数据目录；
2. 确认数据目录存在且是目录；
3. 创建仅当前用户可访问的 bridge 父目录；
4. 取得当前 `installation_key` 的操作系统级排他锁；锁文件存在本身不表示锁仍被持有，也不得按经过时间猜测陈旧；
5. 拒绝 bridge 目标、哨兵、锁或父目录中的不安全符号链接和非普通文件；
6. 如果已有合法冲突哨兵，保持冲突并停止，不自动清除；
7. 如果已有合法文件指向不同 `data_dir`，原子创建冲突哨兵、保留原文件并报告冲突；
8. 如果已有普通文件只是 JSON 损坏，可以记录诊断后原子重写；符号链接、非普通文件或所有权异常不得自动修复；
9. 使用临时文件、刷新到磁盘并原子替换；
10. 成功后才报告 bridge 已发布。

### 消费

普通 `scripts/draft` 调用按自身规范路径计算同一个 `bridge_path`，并在同一排他锁内解析以下状态；锁只覆盖 bridge 状态解析，不覆盖后续 Draft 命令执行：

- 合法冲突哨兵存在：报告 `plugin.context_conflict`，不读取旧 bridge；哨兵文件类型或所有权异常则报告 `plugin.context_invalid`；
- 文件不存在：进入 `standalone`；
- 文件是合法模式且 `data_dir` 可用：进入 `plugin_enhanced`；
- JSON 无法解析、字段类型错误、版本未知、目标不是绝对目录、权限或文件类型异常：报告 `plugin.context_invalid`；

不得搜索其他 bridge 文件、宿主缓存目录或已知的 Claude/Codex 默认数据路径。

### 安全和生命周期

- bridge 文件不通过 shell `source` 执行，只作为 JSON 解析；
- bridge 目录、JSON、冲突哨兵和锁必须限制为当前用户访问；
- 数据目录路径不是秘密，但定位器完整性必须防止 runtime 被重定向到任意目录；
- 临时目录被系统清理后，入口合法退回独立模式；下一次 `SessionStart` 重新发布；
- 插件卸载后，入口文件通常随插件缓存失效；残留 bridge 不具有独立执行能力；
- bridge 是安装实例定位器，不是“插件此刻启用”的租约或凭证。插件禁用或钩子不受信任后不会发布、刷新或恢复绑定，但已经存在且仍合法的 bridge 可以被当前进程继续使用；新会话不再取得该插件 skill。若要求禁用立即撤销当前进程的增强能力，就必须引入租约或会话字段，这不属于两字段版本 1；
- 发生冲突后，任何一方都不得自动删除哨兵。用户先停止重复加载，再通过同一稳定入口的显式维护动作 `scripts/draft plugin-context reset` 在相同锁内删除这个安装键对应的 JSON 和哨兵；锁文件本身保留。该动作不接受 `data_dir`，并在删除前执行相同的所有权、文件类型和符号链接检查。它是唯一允许在普通 plugin context 解析失败前分派的用户维护动作。下一次 `SessionStart` 再重新发布。

## Python/uv 环境契约

### 共同原则

虚拟环境是可重建执行缓存，不是权威状态。Python 官方明确把[虚拟环境定义为不可移动或复制、应在目标位置重建](https://docs.python.org/3/library/venv.html#how-venvs-work)，因此不能把一个已经创建在临时路径的环境移动到 `data_dir`。

环境目标变化时，必须从 skill 携带的清单在目标路径重新创建。

### 环境清单

入口以以下捆绑输入定义 runtime 环境：

- `scripts/runtime/pyproject.toml`；
- `scripts/runtime/uv.lock`；
- `pyproject.toml` 中的 Python 版本约束。

`runtime_version` 不进入 plugin context。入口基于当前捆绑清单的原始字节判断环境是否匹配：

```text
environment_key = sha256(
  UTF-8("draft-environment-v1\0")
  || sha256(bytes(pyproject.toml))
  || sha256(bytes(uv.lock))
)
```

两个内层 SHA-256 以固定 32 字节二进制摘要连接，因此不存在文本拼接歧义。Python 约束已经包含在 `pyproject.toml` 中，不再作为第三份重复输入。

虚拟环境只安装锁定的 Python 和第三方运行依赖，使用 uv 的 [`--no-install-project`](https://docs.astral.sh/uv/concepts/projects/sync/#partial-installations) 语义，不把 Draft 项目本身安装进 venv。入口始终让该 venv 的解释器从当前 skill 的准确 `scripts/runtime/src/` 执行捆绑源码。这样，源码更新不会在 venv 中遗留旧的可执行入口或旧插件绝对路径；仅源码变化时可以安全复用依赖环境，清单变化时使用新的 `environment_key`。开发依赖不进入交付运行环境。

### 环境路径

独立模式和插件增强模式分别使用：

```text
standalone:      <per-user-runtime-dir>/draft/envs/<environment_key>/
plugin_enhanced: data_dir/envs/<environment_key>/
```

`<environment_key>/` 本身就是虚拟环境根，不再增加 `venv/` 子目录、`environment.lock` 或 `environment.json`。临时环境丢失后按同一清单重建；不得把临时环境目录提升、重命名或复制到插件数据目录。

这遵循 Claude Code 的[持久插件数据目录指导](https://code.claude.com/docs/en/plugins-reference#persistent-data-directory)：语言依赖和 Python 环境可以在会话与插件更新之间复用；捆绑清单变化时必须重新安装，而不能只检查目录存在。

不同安装副本在清单相同时可以安全共享独立环境，因为其中只有 Python 和第三方依赖，不安装 Draft 源码。清单不同时，`environment_key` 自然选择不同目录。

### 创建、校验与并发

入口在每次执行 Draft 前：

1. 计算当前模式的准确环境路径；
2. 若目标不存在，允许 uv 创建；若目标存在但不是当前用户可访问的普通目录，或是符号链接，报告 `runtime.environment_invalid`；
3. 设置准确的 `UV_PROJECT_ENVIRONMENT`，执行等价于以下命令的同步：

   ```text
   uv sync --project <scripts/runtime> --locked --no-install-project --no-dev
   ```

4. 同步成功后，用该环境的解释器从当前 `scripts/runtime/src/` 执行 `draft --version` 自检；自检成功后才执行请求的 Draft 命令。

uv 已经对目标虚拟环境使用文件锁，并明确支持多个 uv 进程并发操作同一环境，因此不增加 `environment.lock`。`environment_key` 已经编码清单身份，Python 版本可从解释器读取，而 uv 同步结果才是环境当前可用的证据，因此不增加不能证明完整性的 `environment.json`。参见 [uv 缓存与并发安全](https://docs.astral.sh/uv/concepts/cache/#cache-safety)和[锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)。

uv 同步或自检失败时，入口非零退出且不执行 Draft 命令。下一次调用重新运行同一同步流程；不把目录存在解释为成功，也不维护第二套 ready 状态机。

版本 1 不主动清理旧 `environment_key`。插件从最后一个安装范围卸载时，宿主默认删除整个 `data_dir`；用户选择 `--keep-data` 时保留。只有实际观察到更新累积造成空间问题后，才设计受保护的旧环境回收协议。

## 持久数据布局

插件增强模式的数据目录为：

```text
data_dir/
├── envs/
│   └── <environment_key>/
├── bindings/
│   └── <binding_key>.json
└── logs/
```

`data_dir` 已经由宿主按插件安装身份隔离，因此不再增加 `platform` 子目录。

## 绑定协议

### 删除 platform

从公开和内部绑定协议删除：

- `--platform` CLI 参数；
- `Binding.platform`；
- binding JSON 中的 `platform`；
- `PLATFORMS` 枚举；
- `bindings/<platform>/` 路径层级。

平台只影响哪个宿主钩子取得当前 `data_dir` 和 `session_id`，不改变绑定的领域语义。

### 会话 ID 与物理键

原始 `session_id` 是宿主提供的唯一逻辑身份。Draft 只把它视为不透明字符串，不验证唯一性，也不根据当前宿主示例推断字符集或外形。

```text
binding_key = sha256(UTF-8(session_id))
binding_path = data_dir/bindings/<binding_key>.json
```

SHA-256 在这里不是第二身份，也不是为了弥补会话 ID 唯一性；它只把外部字符串编码为定长、路径安全的文件名。

绑定 JSON 保留原始 ID：

```json
{
  "session_id": "original-host-session-id",
  "root": "/absolute/draft/root",
  "last_page_hint": null,
  "skill_name": "draft",
  "skill_digest": "..."
}
```

读取后必须验证 payload 中的 `session_id` 与本次请求完全一致。文件名正确但内容中的 ID 不匹配时报告 `binding.session_mismatch`，不得返回其中的 root。

`skill_digest` 是绑定建立时 `SKILL.md` 原始字节的 SHA-256，用于阻止旧绑定在 skill 语义已经变化后静默生成重新绑定指令。它不是被排除的 `entry_digest`：不参与入口发现、插件安装认证或环境缓存失效。摘要不匹配时保留绑定，报告 `binding.skill_mismatch`，并要求 Agent 重新读取当前 skill 后显式覆盖绑定。

哈希和 payload 使用宿主提供的原始字符串，不执行 Unicode 规范化，也不增加 Draft 自己的长度、字符集或格式限制。若未来宿主采用当前 `--session` 命令行参数无法承载的字符串，必须修改传输协议，不能把合法的宿主会话身份判为非法。

### Agent 调用

Agent 始终使用稳定入口：

```text
scripts/draft bind ROOT --session SESSION_ID
scripts/draft unbind --session SESSION_ID
```

插件钩子必须把当前 `session_id` 作为短会话事实注入上下文；这不是动态启动命令，也不改变 Agent 的入口。不得把 `session_id` 写进共享 `plugin-context.json`，因为同一安装可能同时存在多个会话。

独立模式没有宿主数据目录，因此 `bind` 和 `unbind` 稳定失败并报告 `binding.plugin_context_unavailable`；其他核心命令继续可用。

## 钩子行为

### SessionStart

Codex 与 Claude 的插件清单都声明实际存在的 SessionStart 钩子。钩子命令从插件根调用 `skills/draft/scripts/draft` 的内部 hook 入口。

Codex 适配器只把 `PLUGIN_ROOT`、`PLUGIN_DATA` 和 hook stdin 中的会话事实规范化后传给入口；Claude 适配器只使用 `CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA` 和对应 stdin。兼容别名即使同时存在也不参与选择。这个差异停在钩子适配层，不重新引入 binding 的 `platform` 字段。

这些 Codex 变量、hook 信任要求、`session_id` 输入、SessionStart 来源和模型可见输出均以当前 [Codex Hooks 官方文档](https://learn.chatgpt.com/docs/hooks)为准。平台适配实现开始前必须重新核对该文档；若宿主协议改变，先修订本规格，不在适配器里增加未记录的猜测或兼容分支。

每次宿主 `SessionStart`（Codex 当前来源为 `startup`、`resume`、`clear`、`compact`；Claude Code 还可能提供 `fork`）：

1. 从宿主提供的插件环境取得准确插件根和 `data_dir`；
2. 发布或验证两字段 plugin context；
3. 若 `<per-user-runtime-dir>/draft/envs/<environment_key>/` 已经存在且是安全目录，则把它只视为“独立 setup 曾被尝试”的预热信号，在 `data_dir/envs/<environment_key>/` 按同一清单运行 uv 同步；不得读取其包状态来替代同步，也不得移动、复制或重命名临时环境；
4. 从 hook stdin 取得当前 `session_id`；字段缺失或不是字符串时保留已经发布的 bridge 和持久环境，但停止后续绑定分支；
5. 在 `data_dir/bindings/<binding_key>.json` 查找绑定；
6. 没有绑定时只输出当前 `session_id` 的短事实，不输出活动草稿根或重新绑定信封；
7. 有绑定时无论临时环境是否存在，都确保对应 `environment_key` 的持久环境可用，校验 skill 摘要并输出重新绑定信封；
8. bridge、环境或绑定状态损坏时输出可诊断失败，不静默采用其他目录。

第 3 步只检查当前 `environment_key` 的准确路径，不扫描其他临时目录。使用相同清单的 skill 副本可以共享这个只含 Python 与第三方依赖的临时环境；每个插件数据目录仍在自己的目标位置按清单同步。

Codex 安装或启用插件后，用户仍需审查并信任插件钩子。未信任钩子不会创建新的 bridge；若当前安装键已有合法 bridge，则按“安全和生命周期”一节的非租约语义处理。

### 当前会话安装

如果宿主在当前会话中热加载了 skill 但没有补发 SessionStart：

- Agent 仍可通过 `scripts/draft` 使用独立模式；
- 不尝试猜测插件数据目录；
- 新会话或宿主明确触发 SessionStart 后自动进入插件增强模式；
- 版本 1 不使用 Claude 的 `CLAUDE_ENV_FILE` 建立第二条启动路径，两个宿主统一依赖同一 bridge 协议。

## 失败和恢复语义

| 失败 | 结果 | 恢复 |
| --- | --- | --- |
| uv 不可用 | `runtime.uv_unavailable`，不执行 Draft 命令 | 安装 uv 后重试 |
| bridge 不存在 | 合法独立模式 | 下一次受信任 SessionStart 发布 bridge |
| bridge JSON 损坏 | `plugin.context_invalid` | 普通用户文件可由钩子记录诊断后原子重写；不安全文件类型需人工处理 |
| bridge 指向不可用目录 | `plugin.context_invalid` | 修复插件安装/权限后重新触发钩子 |
| 同一 `installation_key` 出现不同 `data_dir` | 创建冲突哨兵并报告 `plugin.context_conflict` | 停止重复加载，显式 reset 后由下一次 SessionStart 发布 |
| 临时环境缺失 | 自动重建 | 无额外恢复 |
| 持久环境缺失 | 在当前 environment key 下创建 | 无额外恢复 |
| 环境同步或自检失败 | 非零退出，不执行 Draft 命令 | 修复 uv、网络、磁盘或环境问题后重试 |
| 环境路径不是安全目录 | `runtime.environment_invalid` | 保留诊断，人工确认后清理或重建 |
| SessionStart 缺少 session ID 或该字段不是字符串 | `binding.session_unavailable`，bridge 仍可发布但不输出会话或绑定上下文 | 修复宿主 hook 输入后重新触发 SessionStart |
| binding 文件内容 ID 不匹配 | `binding.session_mismatch` | 保留现场；人工移除或隔离冲突文件后再 bind，不采用错误 root |
| binding 的 skill 摘要不匹配 | `binding.skill_mismatch`，不输出旧重新绑定信封 | Agent 重新读取当前 skill 后显式覆盖 binding |
| 独立模式调用 bind/unbind | `binding.plugin_context_unavailable` | 触发受信任插件钩子或继续不绑定使用 |

## 文档影响

`docs/design.md` 已同步安装产物树、路径契约、环境生命周期和绑定布局。实施时还需同步更新：

- `skills/draft/SKILL.md`：只公开稳定 `scripts/draft` 入口，说明独立/增强模式；
- `references/platform-setup.md`：删除普通 Agent 手工读取插件数据变量和动态启动器说明；
- 两端 hook 配置和插件清单；
- 旧路径规格中已经被本规格取代的段落应明确标记为历史结论，不继续作为当前规范。

## 验收标准

### 安装产物和独立模式

- runtime 完整位于 `skills/draft/scripts/runtime/`；
- 复制 `skills/draft/` 到与插件无关的目录后，Agent 只通过 `scripts/draft` 可完成至少 `init`、`check` 和一个结构操作；
- `scripts/draft setup` 在独立和增强模式下都可重复执行，并选择各自模式的环境；
- 独立模式不访问 Claude/Codex 插件缓存或默认数据路径；
- 临时环境删除后下一次命令可以重建。

### 桥接

- JSON 只有 `schema_version` 和 `data_dir`；
- Claude 与 Codex 正常 marketplace 安装得到各自 bridge；
- Agent 的命令在独立和增强模式下入口完全相同；
- bridge 不存在与 bridge 损坏产生不同结果；
- 两个不同 `data_dir` 竞争同一 `installation_key` 时稳定报冲突；
- 冲突发生后，普通入口也因哨兵停止，不能继续消费旧 data dir；
- bridge 文件类型、权限、符号链接、显式 reset 和原子写入边界有测试。

### 环境

- 第一次插件增强调用在 `data_dir/envs/<environment_key>/` 创建环境；
- 同一清单复用同一环境，并在每次执行前由 uv 校验和同步；
- `pyproject.toml`、`uv.lock` 或 Python 约束改变后使用新的 environment key；
- venv 不安装 Draft 项目；源码变化但依赖清单不变时，复用环境并执行当前捆绑源码；
- 同步或自检失败时不执行 Draft 命令；
- 同一 environment key 的并发首次启动由 uv 自身串行化，不创建 `environment.lock`；
- 环境目录中不创建 Draft 自有的 `environment.json`；
- 测试证明临时 venv 不被移动到持久目录；
- 清单不同的两个 runtime 版本使用不同环境；清单相同的版本可共享依赖环境，但各自执行自身捆绑源码。

### 绑定

- 绑定 CLI、模型、绑定目录和 JSON 不再包含 `platform`；
- binding 文件名是 `sha256(session_id)`，JSON 保留原始 ID；
- 所有从宿主接口实际收到的 session 字符串都按原值哈希，不能因包含 `/`、`\\`、`..` 或 Unicode 而逃逸 `bindings/`；Draft 不增加长度、字符集或外形限制；
- payload session ID 不匹配时拒绝绑定；
- skill 摘要不匹配时不输出旧重新绑定信封，重新读取当前 skill 并覆盖绑定后恢复；
- 独立模式 bind/unbind 明确失败，核心草稿命令不受影响。

### 完整验证

- 完整 pytest、pyright、ruff check、ruff format check；
- POSIX 与 PowerShell 入口的定向测试；
- 两端插件清单和 hook 配置校验；
- marketplace 复制安装产物 smoke test；
- 新会话 SessionStart、resume、clear、compact，以及 Claude Code `fork` 的 bridge/绑定恢复测试；
- 无既有 binding 的 SessionStart 只注入当前 session ID，不伪造活动草稿或动态启动命令；
- 在 Codex 常见 sandbox 配置下验证普通 Agent 是否能读写宿主 `data_dir`；若宿主不授予该路径写权限，bind/unbind 必须停在明确权限错误，不能假报成功。

## 成功条件

完成后，Draft skill 本身就是可执行的完整核心：Agent 永远只调用 `scripts/draft`。插件钩子以两字段临时定位器提供持久数据目录，使同一入口透明获得持久环境、绑定和恢复能力。部署知识不进入 Python 领域核心；临时环境丢失可重建，插件环境按清单重建且不搬迁；会话身份不依赖平台路径或宿主当前文件名形状。
