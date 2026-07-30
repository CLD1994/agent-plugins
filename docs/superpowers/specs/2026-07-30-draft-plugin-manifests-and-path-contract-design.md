# Draft 插件清单、完整改名与路径契约设计

## 目标

把已经完成孵化并重组为安装产物的项目统一命名为 `draft`，补齐 Codex 与 Claude Code 的插件清单和 marketplace 清单，并修复迁移后依赖固定目录层级的路径解析。

本次是尚未发布前的干净断代。新实现只识别 `draft` 命名空间，不读取、检测或迁移旧的 `discussion-notebook` 命名空间。

## 范围

### 公开交付身份

以下标识统一为 `draft`：

- 插件目录：`plugins/draft/`；
- Codex 与 Claude Code 插件清单中的插件名；
- 两份 marketplace 的 marketplace 名和插件条目名；
- skill 目录、`SKILL.md` frontmatter 名称和绑定记录中的 `skill_name`；
- 用户调用的 CLI 名称；
- Claude 生命周期钩子入口名称。

### 内部实现与持久化身份

以下内部标识也统一改名：

| 旧标识 | 新标识 |
| --- | --- |
| `discussion_notebook` | `draft` |
| `discussion-notebook-runtime` | `draft-runtime` |
| `discussion-notebook` CLI | `draft` CLI |
| `discussion-notebook-hook-claude` | `draft-hook-claude` |
| `DISCUSSION_NOTEBOOK_DATA` | `DRAFT_DATA` |
| `DISCUSSION_NOTEBOOK_SKILL_PATH` | `DRAFT_SKILL_PATH` |
| `.discussion-notebook/` | `.draft/` |
| `notebook.json` | `draft.json` |
| `notebook.provisional.json` | `draft.provisional.json` |
| `NotebookError` | `DraftError` |
| `NotebookGraph` | `DraftGraph` |
| `check_notebook` | `check_draft` |
| `recover_notebook` | `recover_draft` |
| `resolve_notebook_root` | `resolve_draft_root` |
| `notebook_manifest` | `draft_manifest` |
| `NotebookFactory` | `DraftFactory` |
| `notebook_id` | `draft_id` |
| `references/notebook-format.md` | `references/draft-format.md` |

`draft` 同时是产品身份和根领域对象。活跃代码、协议及文档不再使用 `notebook`、`Notebook` 或“草稿本”表达这个根对象。页面（page）、关系（relation）、检查报告和操作日志（journal）表达 Draft 内部不同职责，继续保留。

## 不在范围内

- 不识别或迁移 `.discussion-notebook/`；
- 不识别或迁移旧的 `notebook.json` 与 `notebook.provisional.json`；
- 不保留旧 Python 包、CLI、环境变量或钩子入口的别名；
- 不提供弃用期或双写；
- 不实现尚未存在的 Codex 生命周期钩子；
- 不重写 `docs/discussion/` 中作为推导历史保存的旧讨论记录；
- 不在本次改动中重命名工作区外层目录或远端 Git 仓库。

## 插件与 marketplace 清单

新增四份清单：

```text
.agents/plugins/marketplace.json
.claude-plugin/marketplace.json
plugins/draft/.codex-plugin/plugin.json
plugins/draft/.claude-plugin/plugin.json
```

共同约束：

- 插件名为 `draft`；
- 插件版本为 `0.1.0`，与 Python runtime 版本一致；
- 作者和 marketplace owner 使用 `CLD`；
- marketplace 插件来源为 `./plugins/draft`；
- 只声明安装产物中真实存在的组件；
- 不虚构 repository、homepage、license、隐私政策或图像资产；
- Codex 清单提供校验器要求的最小界面元数据；
- Claude Code 清单遵循其插件清单模式；
- 当前没有 Codex 钩子文件，因此 Codex 清单不声明钩子；
- Claude 钩子只有在其正式安装入口满足路径契约后才由 Claude 清单声明。

## 路径契约

### 原则

Python runtime 不从 `__file__` 的固定父层级猜测插件资源。安装位置属于部署上下文，必须由知道实际安装位置的 bootstrap 和平台入口显式提供。

因此删除 `default_skill_path()`。共同核心只消费一个已经规范化的准确文件路径，不负责寻找插件根或扫描父目录。

### Bootstrap 输入

`bootstrap/setup.sh` 和 `bootstrap/setup.ps1`：

1. 从脚本自身位置取得插件根；
2. POSIX 入口固定为 `bootstrap/setup.sh codex|claude`，PowerShell 入口固定为 `bootstrap/setup.ps1 -Platform codex|claude`；
3. Codex 只读取宿主提供的 `PLUGIN_DATA`；
4. Claude Code 只读取宿主提供的 `CLAUDE_PLUGIN_DATA`；
5. 即使另一平台变量同时存在也忽略；
6. 目标数据目录缺失、不是目录或不可写时失败；
7. 不读取用户项目位置，不回退到当前工作目录，也不生成 Tavern 项目级设置片段。

### 规范化启动器

setup 在对应平台数据目录的 `bin/` 下生成启动器。启动器把部署上下文规范化为共同核心使用的准确输入：

- `DRAFT_DATA`：平台持久数据目录的规范绝对路径；
- `DRAFT_SKILL_PATH`：已安装插件中 `skills/draft/SKILL.md` 的规范绝对路径；
- runtime 项目路径：已安装插件中 `runtime/` 的规范绝对路径。

所有 CLI 与生命周期入口都通过该启动器运行。目录布局将来改变时，只修改生成启动器的部署适配层；Python 业务模块不再随父目录层级变化。

### Runtime 消费边界

需要 skill 摘要的入口从 `DRAFT_SKILL_PATH` 取得路径。该值缺失、目标不存在、不是普通文件或不可读时，操作失败并报告 `binding.skill_unavailable`；不得搜索其他目录或回退到打包副本。

`bind` 和 `unbind` 继续只读取规范化后的 `DRAFT_DATA`，平台身份仍由显式 `--platform` 参数决定。共同核心不得根据宿主环境变量推断平台。

## 状态与兼容性语义

新建或收养 Draft 只创建 `.draft/`，最终登记标记为 `.draft/draft.json`，临时登记标记为 `.draft/draft.provisional.json`。检查、结构操作、垃圾箱、恢复和操作日志只读取这一命名空间。

如果目标目录只有旧内部目录或旧登记文件，新实现不把它视为已管理 Draft，也不产生专用兼容诊断。其行为与其他未受管理目录相同。项目当前没有此类目录，因此不提供迁移路径。

`format_version` 和 `journal_version` 不因品牌命名改变而提升：新旧命名空间不会由同一个实现共同读取，版本字段不承担跨命名空间迁移职责。

## 文档同步

更新当前规范和用户操作文档：

- `docs/design.md` 使用 `draft`、`plugins/draft/`、`draft` CLI、`.draft/` 和新的环境变量；
- `docs/design.md` 的状态改为已经结束孵化并进入独立插件阶段，修复迁移后指向仓库外旧位置的链接；
- `skills/draft/SKILL.md` 及其 references 使用 Draft 领域术语和新的公开、内部标识；
- `platform-setup.md` 删除 Tavern 孵化路径，描述正式插件安装后的显式平台 setup；
- `notebook-format.md` 重命名为 `draft-format.md`，并使用 `.draft/draft.json` 与 `.draft/draft.provisional.json`；
- 当前中文规范使用“Draft”或“草稿”表示根对象，不再使用“草稿本”。

`docs/discussion/` 保持为历史推导资料。当前规范与历史资料冲突时，以 `docs/design.md` 和本规格为准。

## 测试与验收

### 清单

- 四份 JSON 均可解析；
- Codex 插件通过 plugin creator 校验器；
- 两份 marketplace 都把 `draft` 指向 `./plugins/draft`；
- 清单不引用不存在的文件或组件。

### 改名完整性

- 活跃安装产物、runtime 源码、测试、skill 和规范性设计中不再出现 `discussion-notebook`、`discussion_notebook`、`DISCUSSION_NOTEBOOK` 或 `.discussion-notebook`；
- 允许这些字符串只出现在本规格的改名映射、明确排除的 `docs/discussion/` 历史资料和工作区外层路径中；
- `uv.lock` 与 `pyproject.toml` 都使用 `draft-runtime`；
- Python 导入、CLI 和钩子入口都使用新名称。
- 活跃安装产物、runtime 源码、测试、skill 和规范性设计中不再出现作为根领域名称的 `notebook`、`Notebook` 或“草稿本”；本规格的改名映射和 `docs/discussion/` 历史资料除外。
- 最终和临时登记标记分别为 `draft.json` 与 `draft.provisional.json`，所有 Python 类型、函数、fixture 和局部变量使用对应的 `draft`/`Draft` 名称。

### 路径行为

- runtime 模块移动到不同父目录层级后，显式提供 `DRAFT_SKILL_PATH` 仍可完成 bind 和钩子摘要校验；
- 未提供 skill 路径时稳定返回 `binding.skill_unavailable`；
- setup 对两个平台只接受各自的数据目录变量；
- 两个平台变量同时存在时，显式平台决定唯一输入；
- 数据目录不可用时不在当前目录创建状态。

### 完整门禁

- 完整 pytest；
- pyright；
- ruff check；
- ruff format check；
- POSIX setup 语法检查；
- 在可用环境中执行 PowerShell 语法检查；
- 从复制后的 `plugins/draft/` 安装产物执行一次 runtime smoke test，证明不依赖仓库外文件。

## 成功条件

完成后，`draft` 是唯一活跃产品和协议身份；两端能从各自 marketplace 发现同一个自包含插件；runtime 资源路径由部署适配层显式注入，不再依赖 Python 文件所在的父目录数量；所有失败都停在明确边界，不通过搜索或默认目录掩盖错误。
