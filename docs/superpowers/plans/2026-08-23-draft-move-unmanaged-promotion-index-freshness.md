# draft move 闭包修复与增量反向索引实施计划

> 本计划针对已完成的 `2026-08-23-draft-move-unmanaged-promotion` 实施结果，执行
> round-2 至 round-6 评审结论。目标是先闭合严格语义，再以可失效、可回退的持久化
> 增量索引减少重复正文读取。计划中的模块划分是实现建议；以下行为契约、失败语义
> 和验收条件不可自行放宽。

## 1. 行为契约

### 1.1 严格 move 语义

对候选根执行 `move` 时，所有根内可判定 Markdown（受管理和未受管理）中指向移动前
`ResourceIdentity` 的 CommonMark 链接都必须改写。索引只负责定位候选链接者，不改变
这个闭包。索引不完整、不新鲜、损坏或无法证明事件连续时，操作必须失效索引并回退
全根闭包扫描；不得静默只处理受管理页面。

### 1.2 读集和提交

计划阶段读取的正文、索引记录和目录成员事实必须进入可复核读集。首次业务写入前，
所有未解释变化返回 `operation.external_change`，不产生部分业务写入。操作自身 mutation
导致的文件和目录变化由 explained mutation 规则解释。提交成功后原子更新索引；索引
更新失败必须标记索引无效，不能继续使用旧索引。

### 1.3 状态分类

索引状态至少区分：

| 状态 | 含义 | move 行为 |
| --- | --- | --- |
| `fresh` | 根/格式/覆盖完整，目录成员和文件指纹一致，无丢失事件 | 使用索引定位链接者 |
| `stale` | 已知文件或目录发生变化 | 增量更新受影响记录后重新检查 |
| `unknown` | watcher 丢事件、检查不完整、外部边界不可信 | 全量重建，不使用旧反向表 |
| `corrupt` | 索引 JSON/版本/身份/反向表不一致 | 保留诊断并全量重建；重建失败则拒绝 |

`fresh` 是某个检查时刻的事实，不保证下一时刻不变；提交前必须再次复核。

## 2. 不采用的捷径

- 不以移动文件自身的出站链接推断全部入站链接。
- 不以 `rg`、关键词、原始链接字符串或 `managed-only` 作为默认正确性边界。
- 不用 `mtime + size` 单独宣称内容绝对未变；元数据异常或事件丢失时全量扫描。
- 不通过省略未受管理观察、减少恢复证据或吞掉 I/O 异常来规避 journal 写放大。
- 不让重叠来源依赖参数顺序、字典覆盖或静默去重。

## 3. 实施阶段

### 阶段 A：先补失败测试并固定现有缺陷语义

先在核心 runtime 的 unit/integration 测试中补齐以下红测，再修改生产代码：

1. 单个受管理来源移动时，受管理和未受管理链接者都被改写；dry-run 与正式执行一致。
2. 计划后修改静止未受管理 Markdown、向来源目录新增 `.md`、删除成员或改变成员类型，
   `execute_plan` 在首次业务写入前返回 `operation.external_change`。
3. 来源文件/目录、祖先/后代目录和重复来源的重叠输入，无论参数顺序都产生同一稳定错误。
4. 文件目标配目录来源、损坏 marker、不可解码页面和缺失内部目录时，先返回参数形态错误，
   不读取根事实、不创建日志。
5. 注入 `FileNotFoundError`、`PermissionError` 和普通 `OSError`，闭包扫描均返回稳定
   `DraftError`，携带根内路径。

阶段门禁：新增测试必须先失败；既有核心 unit 全部保持通过。

### 阶段 B：闭合 move 专用观察读集

建议修改 `move_closure.py`、`observations.py`、`operation_facts.py`、`structure.py`，
必要时调整 `models.py` 的传递类型：

1. 闭包读取原始 bytes 时直接计算 SHA-256，并在 `UnmanagedFile`/等价事实中保留摘要；
   链接解析和摘要必须来自同一 bytes，不二次读取。
2. 同一遍目录遍历记录每个闭包目录的排序成员集合，覆盖目录来源、祖先目录和会影响
   根内 Markdown 覆盖证明的目录；路径和成员形状复用 `Observation` 协议。
3. 提供有明确冲突行为的 manifest 合并函数：同一路径相同观察可去重；同一路径不同
   kind/摘要/成员必须抛计划错误，不能后写覆盖。
4. `plan_move_request` 的单页委托只能复用受管理子计划，之后仍合并未受管理闭包和
   `extra_moved_targets`。
5. `execute_plan`、回滚、恢复和日志解析继续使用同一观察清单及 explained mutation；
   新增目录观察不能把外部新成员吸收为本操作产物。

阶段门禁：H1/H2 集成反例、正常自身 mutation、异常中断恢复全部通过；用日志大小和
   mutation 数记录 H2 写放大，不改变正确性契约。

### 阶段 C：实现索引数据模型和持久化边界

新增建议模块 `link_index.py`（实际文件可按代码结构调整），并在 `.draft/` 内增加
版本化索引文件。索引格式至少包含：

```text
root_id / schema_version / generation / completeness / last_event_seq?
files[path] = {
  fingerprint: {file_id, size, mtime_ns},
  sha256,
  parse_status,
  outbound: [{resource_identity, span}]
}
backlinks[resource_identity] = [{source_path, span}]
directories[path] = sorted(member names and node kinds)
```

要求：

- 使用 `ResourceIdentity` 做反向表键；保留链接位置以便增量更新和诊断。
- 索引写入采用临时文件 + 原子替换；格式、根身份或反向表不一致时为 `corrupt`。
- 索引不存在是 `unknown`，首次需要全量构建；不能把空索引解释为“无反向链接”。
- 索引更新失败先保留业务操作结果，但必须原子写入 `invalid` 标记或等价状态，使下次
  操作必然重建；若无法可靠标记，则业务操作也必须失败并进入恢复路径。

单元测试只使用内存 fake，覆盖序列化、版本拒绝、重复来源、反向边增删和原子更新状态。

### 阶段 D：实现新鲜度检查和增量重建

提供独立的 `check_index_freshness`/等价接口，返回显式状态而不是布尔值。保守无 watcher
路径执行：

1. 核对根身份、格式版本、完整性状态。
2. 检查索引目录成员事实，识别新增、删除、重命名和类型改变。
3. 对已登记文件先比较 `file_id/size/mtime_ns`；指纹变化的文件才读取、hash、解码、
   解析并更新出站边及反向边。
4. 目录覆盖或解析状态无法证明时，转换为 `unknown` 并全量扫描。
5. 增量更新完成后重新验证索引完整性，只有满足 `fresh` 条件才允许用于 move。

如果实现 watcher：

- 持久化最后处理的事件序号；先排空事件并增量更新，再读取当前序号；
- watcher 报队列溢出、事件丢失、路径无法分类或处理失败时标记 `unknown`；
- 计划完成到提交前若事件序号变化，必须复核受影响读集并重新计划；不假定 watcher
  本身提供事务锁。

为文件读取加上稳定性检查：打开/读取前后指纹不一致时重试或标记 `unknown`，避免把
  正在写入的半截内容登记为有效索引事实。

### 阶段 E：接入 move、原子索引更新和恢复

在 `structure.py`/CLI 入口编排：

1. 先做 M2 参数形态预检，再加载 root facts。
2. 获取 fresh 索引；索引失效则执行全根闭包扫描并同步重建索引。
3. 用反向表定位链接者，读取移动集合和命中链接者，构造本次精确观察清单；目录移动
   的所有成员按新基址重算出站链接。
4. 在同一 `OperationPlan` 中保存索引代次/完整性观察。提交前复核文件摘要、目录成员
   和索引代次；任何变化返回 `operation.external_change`。
5. mutation 完成后，以实际新路径和新内容更新文件记录、出站边、反向表和目录事实；
   索引更新必须与业务完成状态有明确恢复顺序。崩溃恢复时不能把半更新索引当作 fresh。

不要让 `move_page` 旧程序化入口无记录地获得或失去闭包；在代码与文档中明确它是兼容
入口，或将其收敛到统一 `move_entries`，并为选定契约补测试。

### 阶段 F：性能、日志和全局门禁

补集成基准，至少测量：

- 稳态索引命中时的正文读取文件数、解析文件数和 wall time；
- 单文件修改、新增/删除成员、索引失效时的增量/全量成本；
- 100、1,000、5,000 个 Markdown 及宽目录下的索引内存；
- 观察清单序列化大小和不同 mutation 数下的 journal 总写入量；
- watcher 丢事件、索引损坏和索引更新失败的回退时间与结果。

只有基准显示 journal 写放大成为实际瓶颈时，才另开日志 sidecar/引用式格式任务；该
优化必须先有独立的格式版本、完整性校验、恢复和兼容测试。

最终运行并分别保留结果：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/integration -q -m 'not network'
uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src" "$RUNTIME/tests"
uv run --project "$RUNTIME" --locked pyright -p "$RUNTIME/pyproject.toml"
bash plugins/draft/scripts/check-adapters.sh
```

## 4. 交付和停线规则

- 每阶段先红测、再最小实现、再重构；测试失败先定位根因，不得删除测试或放宽断言。
- 发现代码事实与本计划冲突、出现计划未定义的状态、需要改变错误码/索引格式/旧入口
  契约时，停止相关阶段并报告证据，不自行选择语义。
- 提交不得包含既有工作区变更、原始评审记录或无关计划文件。
- 最终交付必须附：改动文件清单、每阶段测试结果、索引 freshness 状态转移证据、回退
  和恢复测试证据、性能基准摘要以及仍未解决的风险。

