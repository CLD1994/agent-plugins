# Draft 单文件 Python 启动入口纠正规格

## 状态与优先级

状态：已按 `docs/superpowers/plans/2026-08-04-draft-single-python-launcher.md` 实施，容器内
全部门禁通过（pytest、pyright strict、ruff check/format、复制产物烟雾测试）；真实
Windows 宿主复验（计划 Task 6 Step 6）仍待执行，完成前 P1 不关闭。

本规格只修正 Draft 自包含运行时的部署入口、环境身份、runtime 安装和
`SessionStart` 启动链。它取代以下文档中与这些主题冲突的结论：

- `docs/design.md` 的稳定入口、Python/uv 环境和宿主钩子相关段落；
- `docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md`
  中的包装脚本、清单摘要环境键、不安装 Draft runtime 和按当前源码树执行等结论；
- `docs/superpowers/plans/2026-07-31-draft-self-contained-skill-runtime.md` 中已经完成的
  对应实现步骤。

两字段插件上下文桥接、无 `platform` 绑定、原始 `session_id` 语义、草稿格式、结构操作、
恢复协议和退出码中与本规格无关的结论继续有效。实施计划另见
`docs/superpowers/plans/2026-08-04-draft-single-python-launcher.md`。

## 问题

现有公开入口由 POSIX `scripts/draft`、PowerShell `scripts/draft.ps1` 和 runtime 内的
`draft.entry` 三层组成。Codex 与 Claude Code 的 `SessionStart` 钩子统一调用 POSIX 文件，
因此 Windows 不能可靠启动。普通 Agent 工具进程又不继承 `PLUGIN_DATA` 或
`CLAUDE_PLUGIN_DATA`，不能在调用 uv 前直接构造持久环境路径。

现有环境只同步第三方依赖、通过 `PYTHONPATH` 执行当前源码，并用 `pyproject.toml` 与
`uv.lock` 的字节摘要作为环境键。这使入口、源码选择、依赖缓存和宿主桥接相互缠绕，
也产生两个平台包装器和一个 Python 入口的重复控制流。

## 行为契约

### 输入

- Agent 从已加载 skill 的协议上下文知道当前 `skill_root`，据此定位
  `<skill_root>/scripts/draft.py`；普通工具进程不需要获得 `skill_root`、`PLUGIN_DATA` 或
  `CLAUDE_PLUGIN_DATA` 环境变量。
- `draft.py` 位于 skill 协议规定的 `scripts/` 目录。它以自身规范路径为锚点推导
  `skill_root`、`SKILL.md` 和 `scripts/runtime/`，不接受重复的路径参数。
- Codex `SessionStart` 只读取 `PLUGIN_ROOT`、`PLUGIN_DATA` 和标准输入；Claude Code
  `SessionStart` 只读取 `CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA` 和标准输入。
- runtime 版本只取自 `scripts/runtime/pyproject.toml` 的 `project.version`。

### 输出

- 唯一公开调用形态为：

  ```text
  uv run <skill_root>/scripts/draft.py <draft arguments>
  ```

- `draft.py` 以相同退出码返回 runtime 命令结果；引导、桥接、布局或环境选择失败使用
  既有稳定错误码并退出 `4`。
- runtime 在选定环境中以非可编辑安装形式提供 `[project.scripts]` 的 `draft` 命令。

### 不变量

1. 安装产物只有一个公开部署入口：`scripts/draft.py`。不得保留 `scripts/draft`、
   `scripts/draft.ps1` 或 runtime 内的 `draft.entry`。
2. `draft.py` 在 runtime 环境存在前可运行，只依赖 Python 标准库；它不得在引导阶段导入
   `draft` 包或第三方模块。
3. 插件模式环境恰好位于 `<PLUGIN_DATA>/envs/<draft_version>` 或
   `<CLAUDE_PLUGIN_DATA>/envs/<draft_version>`；独立模式环境恰好位于当前用户安全临时
   `data_dir` 的 `envs/<draft_version>`。
4. 同一 `data_dir` 中，相同 Draft 版本的所有会话共享同一个环境；不同 Draft 版本必须
   使用不同环境。
5. 环境键只由 Draft 版本构成，不加入源码摘要、入口摘要、锁文件摘要、平台或会话身份。
6. 同版本发布产物不可变。依赖、runtime 源码或入口发生发布级变化时必须按版本策略提升
   Draft 版本；本地开发不保证同版本并行副本安全。
7. runtime 自身和锁定运行依赖都安装进目标环境；runtime 必须以非可编辑方式安装，不能
   通过 `PYTHONPATH` 引用 skill 源码。
8. `plugin-context.json` 仍严格只有 `schema_version` 和 `data_dir`；环境版本和环境路径不
   进入桥接文件。
9. bridge 不存在是合法独立模式；bridge 存在但无效或冲突必须停止，不能降级。
10. `.env` 不属于启动协议，不写入 skill 安装目录，也不作为 bridge 或环境选择器。

## 安装产物布局

目标布局为：

```text
plugins/draft/
├── hooks/
│   ├── codex-hooks.json
│   └── claude-hooks.json
└── skills/
    └── draft/
        ├── SKILL.md
        ├── references/
        └── scripts/
            ├── draft.py
            └── runtime/
                ├── pyproject.toml
                ├── uv.lock
                ├── src/draft/
                └── tests/
```

`scripts/draft.py` 同时承担布局定位、bridge 发布/消费/reset、模式选择、环境目标计算、
子进程环境构造和内部 runtime 分派。为了保持首次启动可用，这些职责不能放进尚未安装的
runtime 包。领域 CLI、绑定和 `SessionStart` 载荷处理仍属于已安装的 `draft` 包。

## Python 引导契约

`draft.py` 使用 PEP 723 内联脚本元数据声明与 runtime 相同的 Python 范围和空依赖：

```python
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///
```

因此 Agent 和钩子都不传 `--python`。uv 根据脚本约束发现兼容解释器，并在默认策略允许时
下载缺失的托管 Python。内联元数据使调用者当前项目不参与脚本环境，因此公开命令也不需要
`--project` 或 `--no-project`。

引导脚本环境不是 Draft 应用 runtime；它没有第三方依赖，只用于在目标环境创建前运行
标准库定位逻辑。Draft 应用 runtime 仍只存在于本规格规定的 `<data_dir>/envs/` 下。

## 路径推导

`draft.py` 按以下固定结构推导布局：

```text
entry_path  = realpath(__file__)
scripts_dir = parent(entry_path)
skill_root  = parent(scripts_dir)
skill_path  = skill_root / "SKILL.md"
runtime     = scripts_dir / "runtime"
```

入口必须验证 `scripts_dir.name == "scripts"`、`SKILL.md` 是普通文件、runtime 是真实目录，
且 `pyproject.toml` 与 `uv.lock` 都是普通文件。失败报告 `entry.layout_invalid`，不得搜索父目录、
插件缓存或其他 skill 副本。

钩子仍通过宿主 `PLUGIN_ROOT` 或 `CLAUDE_PLUGIN_ROOT` 定位入口。入口内部不靠宿主根推导
skill 路径，但必须把规范化后的宿主根与 `skill_root.parent.parent` 比较；不一致时报告
`hook.plugin_root_mismatch`。

bridge 的 `installation_key` 改为规范 `scripts/draft.py` 路径的 SHA-256。其余目录安全、锁、
原子发布、冲突哨兵和 reset 语义不变。

## 环境身份与执行

入口使用标准库 TOML 解析器读取 `project.version`。版本必须是非空字符串，且不能包含路径
分隔符、NUL，或等于 `.` / `..`；无效时报告 `runtime.version_invalid`。

```text
environment_key  = draft_version
environment_path = data_dir / "envs" / environment_key
```

不再计算清单摘要。`pyproject.toml`、`uv.lock` 和源码只要属于同一个合法发布版本，就必须
视为不可变的一组发布内容。该约束由发布版本策略保证，不用运行时代码为错误重打包增加摘要
兜底。

每个普通命令和每次 `SessionStart` 都通过等价命令执行：

```text
uv run \
  --project <skill_root>/scripts/runtime \
  --locked \
  --exact \
  --no-dev \
  --no-editable \
  --no-env-file \
  draft <arguments>
```

入口为子进程显式设置：

```text
UV_PROJECT_ENVIRONMENT=<data_dir>/envs/<draft_version>
DRAFT_SKILL_PATH=<skill_root>/SKILL.md
DRAFT_DATA=<plugin data_dir>    # 仅插件增强模式
```

入口必须覆盖继承的 `UV_PROJECT_ENVIRONMENT`，移除继承的 `DRAFT_DATA`、`DRAFT_SKILL_PATH`、
`PYTHONPATH`、`VIRTUAL_ENV`、`UV_NO_SYNC` 和 `UV_NO_INSTALL_PROJECT`。`--no-env-file` 保证
外部 `UV_ENV_FILE` 不能改变本次执行；索引、代理、离线和 Python 下载策略仍允许由用户或
宿主配置。

`--locked` 禁止隐式改写锁文件，`--exact` 清除环境中的多余包，`--no-dev` 排除开发组，
`--no-editable` 把当前 Draft runtime 和任何可编辑本地依赖安装为非可编辑形式。`uv run`
负责在执行 `draft` 前同步环境，不增加 Draft 自有 ready 文件或环境锁。

公开 `setup` 仍是可选预热。入口把它转换为内部 `draft --version`，因此只完成同一套同步和
可执行性检查，不执行领域操作。

## 运行模式与状态转换

| 状态 | `data_dir` | 行为 |
| --- | --- | --- |
| `standalone` | 当前用户安全临时目录中的 Draft 子目录 | 核心命令可用；bind/unbind 保持 `binding.plugin_context_unavailable` |
| `plugin_enhanced` | bridge 中宿主提供的持久目录 | 核心命令、绑定、日志和钩子恢复可用 |
| `plugin_context_invalid` | 未采用 | 退出 `4`，不启动 uv |
| `plugin_context_conflict` | 未采用 | 退出 `4`，只有 `plugin-context reset` 可在普通加载前分派 |

允许的转换只有：bridge 缺失进入 `standalone`；合法 SessionStart 发布后进入
`plugin_enhanced`；临时 bridge 被系统清理后再次进入 `standalone`。环境绝不在两个
`data_dir` 之间移动、复制或重命名。

## SessionStart

两个宿主钩子都直接执行 `uv run .../scripts/draft.py _session-start <host>`。`draft.py`：

1. 只读取所选宿主的根和数据变量，拒绝缺失、无效或根不一致；
2. 在检查标准输入会话字段之前发布或验证 bridge；
3. 选择 `<data_dir>/envs/<draft_version>`，无条件通过上述内部 `uv run` 同步 runtime；
4. 把原始标准输入传给已安装 runtime 的内部 `_session-start` 分派；
5. 原样返回内部命令退出码。

runtime 才解析最大 64 KiB 的 SessionStart JSON、验证 `hook_event_name`、保留任意字符串
`session_id`（包括空字符串）、读取绑定并输出会话事实、重新绑定信封或诊断。无绑定会话也
必须先完成同步；不再用“绑定文件不存在”绕过 runtime 环境。

## `.env` 的边界

uv 能在环境选择前读取 `--env-file`，但普通 Agent 只有在文件位于 `skill_root` 时才能直接
定位它；这会把宿主可变状态写入安装产物，并要求安装目录可写。进程已有的
`UV_PROJECT_ENVIRONMENT` 还会覆盖 `.env`，`UV_NO_ENV_FILE` 也能禁止加载。

因此本设计不生成或消费 `.env`。`draft.py` 已经位于选择边界上，直接构造子进程环境比再
维护一个磁盘环境文件更少状态、更容易验证。

## 失败语义

| 失败 | 结果 |
| --- | --- |
| uv 不在 `PATH` | `runtime.uv_unavailable`，退出 `4` |
| skill 布局不符合协议 | `entry.layout_invalid`，退出 `4` |
| runtime 版本缺失或不能安全作为目录名 | `runtime.version_invalid`，退出 `4` |
| 环境目标是符号链接或非目录 | `runtime.environment_invalid`，退出 `4` |
| bridge 缺失 | 合法独立模式 |
| bridge 无效、冲突，或 bridge 文件/锁操作发生 OSError | 保留既有 `plugin.context_*` 错误并退出 `4`；OSError 在操作现场转换为 `plugin.context_invalid`，保留异常链，不泄漏原始 traceback，不启动内层 uv；锁内主错误在途时清理失败只附加诊断，不覆盖主错误；目录 fsync 为尽力而为，其余写入、替换、删除与锁操作为强制步骤 |
| hook 宿主变量缺失或根不一致 | 保留既有 `hook.*` 错误并退出 `4` |
| 内部 uv 同步、安装或 runtime 命令失败 | 保留 uv 诊断并传播子进程退出码 |

失败不得创建 `.env`、切换到其他环境键、搜索其他 bridge，或执行领域命令作为降级。

## 文档与发布约束

实施切换时必须在同一变更中更新 `SKILL.md`、`references/platform-setup.md`、两个 hook
清单、主设计和分发测试。实施前这些运行说明继续描述当前可执行入口，不能提前发布不存在的
命令。

发布门禁必须证明 runtime `project.version`、两个插件 manifest 版本和 `draft --version`
一致。环境键不读取 manifest 版本，但版本不一致必须阻止发布。任何已经发布的同版本产物
不得用不同源码或依赖重打包。

## 验收标准

- Windows、POSIX 和复制后的独立 skill 都只需要 `uv` 命令和同一个 `draft.py` 路径；
- 公共命令不包含 `--python`、`--project`、`--no-project`、shell 包装器或 PowerShell 包装器；
- `draft.py` 从自身结构推导并严格验证 skill 布局；
- 普通 Agent 不继承宿主数据变量时仍能通过 bridge 进入插件增强模式；
- bridge 缺失、无效和冲突保持三种不同状态；
- 环境路径只由 `data_dir` 和 Draft 版本决定；相同版本复用、不同版本隔离；
- runtime 在目标环境中存在非可编辑安装，命令不依赖 `PYTHONPATH`；
- 每次 SessionStart 都先同步 runtime，再处理任意合法或非法 hook 载荷；
- 继承的 uv/Draft 选择变量和 `.env` 不能重定向权威环境；
- `plugin-context reset` 仍能在损坏或冲突 bridge 的普通解析之前执行；
- 完整 pytest、pyright strict、ruff check、ruff format check 和复制产物烟雾测试通过；
- 真实 Windows 宿主钩子复验完成后，才关闭原始 P1。
