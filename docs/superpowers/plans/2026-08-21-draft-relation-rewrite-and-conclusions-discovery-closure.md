# Draft 结构关系重写与结论发现分类闭合执行计划（2026-08-21）

依据：

- 评审记录：`docs/review-notes/2026-08-21-draft-candidate-root-incremental-migration-round-18.md`
- 用户方向决定（2026-08-21）：H1 采用 **B1**（结构命令重写非父正式关系）；M1 采用 **A1**
  （`not_generated` 时根级遗留 `conclusions.md` 按未受管内容对待）。

本计划区分三类内容：**已确认行为契约**（硬性要求，来自批准规格、评审确认行与用户决定）；
**决策表**（对多条件行为的完整分类，是契约的展开）；**实现预估**（文件、函数与步骤安排，
执行者可依据实际数据流调整，但不得改变契约与决策表）。

所有步骤使用 checkbox 跟踪；偏差必须停止并报告，不得自行选择语义。

## 一、已确认行为契约

### H1（B1）：结构命令重写非父正式关系

1. move/promote/collapse 成功后，全部受影响的正文链接、父关系、父导航**与非父正式关系
   链接目标**一致；规格 §9.1 move 行不变量"全部可受影响链接一致"对关系链接成立。
2. 只重写可证明解析的关系目标。无法解析的关系链接目标保持原样，与正文断链的既有处理一致：
   candidate 允许其继续作为既有错误存在；registered 的写前全局门禁本来就拒绝含此类错误的根。
3. 重写只改变链接目标字符串。关系配对（正反方向）、自引用规则、依赖/取代无环、取代目标
   状态同步等既有不变量全部不受影响；幂等关系操作语义不变。
4. 机器结论列表"允许过时、结构命令不重写"（规格 §12.5、round-15 M2 确认行）保持不变。
5. registered 根的可观察行为发生变化：被入站非父关系指向的页面从"移动必然回滚
   （`operation.validation_failed`）"变为"可移动且移动后全根一致"。这是本次修复的预期
   结果，修复记录必须如实声明该行为变化，不得表述为无行为变更。

**关系目标重写决策表**（纯模型；对每个页面声明的每条非父关系分别求值）：

| 声明页 | 关系目标页 | 要求的新目标字符串 |
|---|---|---|
| 在被移动集合内 | 在被移动集合内 | 从 final(声明页) 到 final(目标页) 的规范相对路径 |
| 在被移动集合内 | 不在被移动集合内 | 从 final(声明页) 到目标页的规范相对路径 |
| 不在被移动集合内 | 在被移动集合内 | 从声明页到 final(目标页) 的规范相对路径 |
| 均不在被移动集合内 | | 现值已一致时不产生 mutation；不一致时按上述规则修正 |

- 关系代码维度：`depends_on`、`supersedes`、`superseded_by`、`challenges`、
  `challenged_by` 全部经同一路径重写；反向声明是其所在页面的独立关系声明，按同一表逐条
  处理，不得依赖"正向重写顺带覆盖反向"。
- 目标字符串携带锚点时保留锚点（预估：协议内关系声明未见锚点用例；实现前先从解析器事实
  确认。若发现锚点语义不明，停止并报告，不得自行猜测）。
- 深度变化（跨目录移动）与同深度改名都必须覆盖；父关系的既有重写分支保持原样，不由本项
  重写路径重复处理。

### M1（A1）：遗留 conclusions.md 的发现分类

**根级 `conclusions.md` 分类决策表**（发现阶段的唯一权威）：

| marker 事实 | conclusions_state | 磁盘事实 | 发现分类 | check 输出 | 结构命令 |
|---|---|---|---|---|---|
| 合法 | generated | 存在 | 受管页面 | 既有结论产物形状检查 | 图内页面；机器产物正文链接不重写（既有） |
| 合法 | generated | 缺失 | 受管页面缺失 | 既有缺失错误 | 不适用（check 已报错） |
| 合法 | not_generated | 存在 | **未受管内容**（本计划变更行） | 恰好一条 `conclusions.unexpected_file`（既有过滤） | 不阻断；工具对其字节零触碰 |
| 合法 | not_generated | 缺失 | 不适用 | 无此诊断 | 正常 |
| 缺失/非法/未知版本 | 任意 | 存在 | 维持现状（受管，既有行为） | 既有行为不变 | 既有行为不变（此类根的结构命令本就在更早的 marker 错误处失败） |

硬性要求：

1. `check_draft` 输出在全部状态下逐字节不变：`not_generated` 下该文件的
   `page.unmanaged_path` 继续被 `checker.py:1307-1310` 既有过滤抑制，最终恰好一条
   `conclusions.unexpected_file`；generated 与 marker 非法状态完全不变。
2. `generate-conclusions` 的拒绝语义不变：它在 `not_generated` 下检查**物理文件存在**
   （`conclusions.py:381-390`），不只依赖图成员；遗留文件存在时仍拒绝生成。
3. adopt 观察清单语义不变：规格 §12.1 本就只在 generated 时纳入 `conclusions.md`。
4. `not_generated` 下工具对遗留文件零写入、零移动、零删除；结构命令、trash、recover 均不得
   触碰它。
5. 阻断不再依赖遗留内容是否碰巧可解析：可解析与不可解析两种内容下，结构命令行为一致。
6. 该文件在 `not_generated` 下作为查询/命令操作数时统一按未受管路径语义处理（如
   `page.not_found`/`structure.unknown_page`），不再出现"可解析时一种码、不可解析时另一种
   码"的分裂。具体码由既有操作数校验路径决定，执行者不得新增错误码。
7. marker 读取遵守 round-15 H1：发现所需的 `conclusions_state` 必须来自调用链已有的同一
   marker snapshot，不得为发现新增第二次 marker 读取。

## 二、任务与步骤

### Task 1：H1 红灯测试（先失败测试）

预估测试位置：`tests/unit/test_structure.py`、`tests/integration/test_structure.py`。

- [ ] Step 1：unit——关系目标重写纯决策表。独立构造图与移动映射，预期值从上表推导，不复制
      生产算法；覆盖声明页/目标页的四种移动组合 × 五个关系代码 × 同深度与跨深度。当前无
      生产实现，用例应红。
- [ ] Step 2：integration——candidate 根红灯基线（round-18 实验一）：A 正文链接 + A
      depends_on B，`move pages/b.md pages/b2.md`；断言移动后 A 的关系目标已重写且
      `check_draft(root).errors == ()`。当前实现应红（关系目标停留旧路径，check 报
      `link.missing`）。
- [ ] Step 3：integration——registered 根红灯基线（round-18 实验二）：同布置执行同 move；
      断言操作成功、无回滚、移动后 check 零错误。当前实现应红
      （`operation.validation_failed`）。
- [ ] Step 4：integration——promote 与 collapse 各至少一个关系穿越用例；另覆盖"关系由被移动
      页自己声明且跨深度移动"（出站方向）与"双向配对两端均被移动"。
- [ ] Step 5：运行并记录红灯形态；红灯失败形态必须与 round-18 记录一致，否则停止并报告。

### Task 2：H1 实现

预估修改位置：`src/draft/structure.py::_render_pages`（或其抽出的纯函数）。

- [ ] Step 1：按决策表实现关系目标重写：逐页枚举非父关系声明，重算目标字符串，仅在与现值
      不同时产生编辑；锚点按契约保留。不得为某个关系代码写特判分支。
- [ ] Step 2：确认 registered 应用后完整检查（`operations.py:2391-2394`）与写前门禁无需
      改动——修复后它们自然转绿；若实现中发现需要改动，停止并报告。
- [ ] Step 3：运行 Task 1 全部用例转绿；再运行 structure 既有全部 unit/integration，确认
      既有不变量（父关系、导航、结论列表不重写、空枢纽降级等）无回归。

### Task 3：M1 红灯测试（先失败测试）

预估测试位置：`tests/unit/test_graph.py`、`tests/integration/test_structure.py`、
`tests/integration/test_checker.py`、`tests/integration/test_query.py`（以实际测试树为准）。

- [ ] Step 1：integration——candidate + not_generated + **不可解析**遗留 conclusions.md
      （round-18 实验四为红灯基线）：move/promote/collapse 成功；`check_draft` 恰好一条
      `conclusions.unexpected_file`；容错视图 `blocking_paths` 不含该文件；遗留文件字节不变。
- [ ] Step 2：integration——同根换用**可完整解析**的遗留内容（标题、合法状态、合法父关系），
      证明结构命令行为不再依赖偶然可解析性；断言与 Step 1 相同。
- [ ] Step 3：integration——registered + 遗留文件：写命令仍报
      `conclusions.unexpected_file`（不得回归为 `structure.dirty`）。
- [ ] Step 4：integration——generated 根（candidate 与 registered 各一）：conclusions.md 仍是
      受管页；结构命令不重写其正文链接；check 行为不变。优先复用 round-15 M2 既有交叉矩阵
      用例，不足处补充。
- [ ] Step 5：unit——发现分类纯决策表：marker 状态 × 磁盘事实五行全覆盖，含"marker 非法时
      维持现状"行；预期独立于生产算法。
- [ ] Step 6：integration——查询语义：candidate 根含遗留文件时集合查询 `complete` 与
      `blocking_paths` 不含该文件；registered 查询门禁行为不变。
- [ ] Step 7：运行并记录红灯形态（Step 1/2 当前应为 `structure.dirty`）。

### Task 4：M1 实现

预估修改位置：`src/draft/graph.py::_discover/_load` 及其调用链。

- [ ] Step 1：把 `conclusions_state` 传入发现阶段。来源必须是调用链已持有的同一 marker
      snapshot/状态（round-15 H1）；`load_graph` 与 `load_graph_tolerant` 两个入口都覆盖。
      禁止在发现阶段新增 marker 文件读取。
- [ ] Step 2：按决策表实现分类：合法 marker + not_generated 时根级 `conclusions.md` 不进入
      受管列表（随后按未受管路径处理，其 `page.unmanaged_path` 由 checker 既有过滤抑制）；
      marker 缺失/非法/未知版本时维持现状分类。
- [ ] Step 3：消费者逐个审计并记录（按实际导入/调用关系，不做纯文本搜索）：
      `_render_pages` 的 `CONCLUSIONS_PAGE` 分支、`_validate_operand`/`_validate_target`、
      trash 的受保护页条款、status/relation/query 操作数路径、`conclusions.plan_generation`。
      对每个消费者写明 not_generated 下该文件的新行为与错误码；发现与决策表冲突时停止并报告。
- [ ] Step 4：运行 Task 3 全部用例转绿；再运行 graph/checker/query/structure 既有全部
      unit/integration 确认无回归。

### Task 5：横切闭合与全量门禁

- [ ] Step 1：端到端数据流复核：marker snapshot → 发现分类 → 容错视图 blocking →
      结构门禁 → 重写器 → 应用后检查 → check 输出 → adopt 观察清单。逐项写明事实来源唯一，
      不存在第二套 marker/conclusions_state 读取或第二套受管分类。
- [ ] Step 2：全量离线门禁（命令见 `plugins/draft/CLAUDE.md`）：核心 unit、核心
      integration（非网络）、适配层 unit、适配层 integration（非网络）、核心 ruff/pyright、
      `plugins/draft/scripts/check-adapters.sh`。网络集成测试若未运行，如实记录为未运行。
- [ ] Step 3：提交划分：H1 与 M1 分开提交；每个提交只含本任务的生产文件与测试；不夹带无关
      改动（用户工作区已有的 AGENTS.md 改动与未跟踪 docs 保持原样）。
- [ ] Step 4：编写修复记录 `docs/review-notes/2026-08-21-draft-candidate-root-incremental-migration-round-18-fix-record.md`：
      逐条对应 round-18 的 H1/M1，包含红灯形态、实现说明、绿灯证据、registered 可移动行为
      变化声明、消费者审计清单、门禁证据与未运行项。

## 三、禁止的捷径

- 不得只为 `depends_on` 特判；五个非父关系代码必须经同一路径。
- 不得为 M1 在发现阶段新增 marker 读取，或捕获/折叠 marker 错误来改变分类。
- 不得通过删除、放宽既有测试或放宽检查器规则让红灯变绿。
- 不得在 `not_generated` 下以任何方式写入遗留文件。
- 不得顺带重写 generated 下机器结论列表的正文链接。
- 不得新增错误码；本计划不授权任何诊断协议变更。
- 不得顺带拆分 `operations.py` 或做无关重构。

## 四、验收条件

1. Task 1/3 全部红灯用例在实现后转绿，且红灯形态与 round-18 记录一致。
2. round-18 实验一/二/三/四的现场在修复后全部表现为契约要求的行为（可复跑验证）。
3. 既有全部离线门禁通过；网络门禁如实记录。
4. generated 状态与 marker 非法状态下行为逐字节不变（测试证明）。
5. 修复记录完整，包含行为变化声明与消费者审计清单。

## 五、范围外与后置事项

- 未受管遗留文件的工具内导入入口（用户已知悉，独立功能话题，不属本计划）。
- 遗留 `conclusions.md` 内链接在受管页移动后悬空：按未受管内容既有语义接受，修复记录中
  显式记载该后果。
- 批准规格文档本计划不修改：B1 与 §9.1 原文一致；A1 与 §12.1 的所有权条件化表述及既有
  check 语义相容。细化契约记录于修复记录与评审记录。
