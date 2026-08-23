# Draft CLI 入口 marker 身份分类单一权威闭合实施计划

> **供 agentic workers：** REQUIRED SUB-SKILL：使用 `superpowers:subagent-driven-development`
>（推荐）或 `superpowers:executing-plans`，逐任务实施本计划。所有步骤使用 checkbox（`- [x]`）跟踪，
> 不得跳过红灯、阶段门禁或横切审计。

**目标：** 让 CLI 入口（`select_managed_root`/`FilesystemProbe.managed_at`/pwd 祖先发现）与
snapshot 读取器对 `.draft` 身份使用同一分类权威，消除“同一事实双错误码”与“stat 失败折叠为
missing”，并勘误第十五轮修复记录的准确性问题（round-16 L1–L3）。

**架构：** `graph.py` 的父分类器是 `.draft` 身份的唯一权威；`paths.py` 的生产探针复用它而不是
维护第二套 `Path.is_*` 分类。pwd 祖先发现对异常祖先采用已确认的立即失败语义（错误原样传播，
不跳过、不上溯）。

**技术栈：** Python 3.12、pytest、Ruff、Pyright strict、uv。

**规格：**
`docs/review-notes/2026-08-20-draft-candidate-root-incremental-migration-round-16.md`（M1 及其
决策表；L1–L3 勘误依据）

## 全局约束

- 基线为 `450814e`（分支 `draft/candidate-root-incremental-migration`）。保留第十五轮已正确的
  `read_marker_snapshot` 分类语义、`_classify_marker_leaf` 与全部写入口 snapshot 传递；本计划
  只改 CLI 入口前置分类，不得回退或重写 snapshot 读取器。
- 保障边界不变：本地协作式单写者、普通 I/O 失败与进程中断；不扩展到 NFS、掉电、恶意篡改或
  不可消除的 POSIX 系统调用间隙。
- **不新增错误码**。`path.symlink_escape`、`format.invalid`、`format.missing`、`root.*` 均为既有
  登记码。
- **禁止捷径：** `managed_at` 不得用统一的 `except DraftError` 折叠错误；尤其不得把
  `format.missing`（`.draft` 存在但 marker 缺失）折叠为 `None`——`RootProbe.managed_at` 协议
  保持“`None` 当且仅当 `.draft` 不存在”。unit 不访问真实文件系统；真实目录、I/O 故障注入和
  `main()` 完整入口放 integration。
- `resolve_draft_root`、`_discover_explicit` 对**根目录本身**（不是 `.draft`）的 `is_symlink/
  is_dir/exists` 检查，以及 pages/index/conclusions 形状守卫，继续按第十五轮边界延期，本计划
  不动。
- 每个行为修复先写能在基线失败的最窄测试，再实现。不得删除测试、放宽配置、吞掉异常。
- 行为表和错误码是硬约束；内部函数名（如 `classify_marker_parent` 的命名与返回形式）是建议，
  可按代码事实调整，但必须保留“单一分类权威”和“`None` 仅表示无 `.draft`”两个不变量。
- 不提交 `docs/review-notes/`、`docs/superpowers/plans/` 或用户已有的 `AGENTS.md`；生产提交只
  包含当前任务的代码与测试。

---

## 行为契约（硬约束）

### 表一：`FilesystemProbe.managed_at` 与 `select_managed_root` 的 `.draft` 分类

| `.draft` 事实 | `managed_at` 行为 | `select_managed_root` 最终错误 | 现状 |
|---|---|---|---|
| symlink | 抛 `path.symlink_escape` @ `<root>/.draft` | 同左（原样传播） | 现抛 `format.invalid` @ marker（**红**） |
| lstat EACCES/EIO | 抛 `format.invalid` @ `<root>/.draft/draft.json` | 同左 | 现折叠为 `None` -> `format.missing`（**红**） |
| ENOENT（不存在） | 返回 `None` | `format.missing` @ `<root>/.draft/draft.json` | 一致（保持） |
| 普通文件/其他非目录 | 抛 `format.invalid` @ `<root>/.draft/draft.json` | 同左 | 一致（保持） |

marker 叶（`.draft/draft.json` 自身）的分类完全由既有 snapshot 读取器决定，本表不改变。

### 表二：pwd 祖先发现（`_discover_from_pwd`）异常祖先语义（已确认 2026-08-20）

| 祖先 `.draft` 事实 | 语义 |
|---|---|
| symlink | **立即失败**，`path.symlink_escape` 原样传播，不跳过该祖先、不继续上溯 |
| lstat EACCES/EIO | **立即失败**，`format.invalid` 原样传播，同上 |
| ENOENT | 返回 `None`，继续上溯（保持） |
| 合法受管根 | 记入候选，继续按既有歧义/最近规则（保持） |

当前代码的异常传播已经实现“立即失败”的**控制流**；表一的修复会自动让 symlink/EIO 祖先以正确
错误码失败。本计划任务三只需用测试钉住该语义，预期不改 `_discover_from_pwd` 的逻辑；若测试揭示
控制流本身不符合表二，修 walk，禁止用折叠错误的方式让它变绿。

---

## 任务一：父分类器公开化（纯结构重构，无行为变化）

`graph.py` 的 `_classify_marker_parent(internal, marker)` 改为公开的
`classify_marker_parent(internal, marker) -> bool`：`.draft` ENOENT 返回 `False`（缺失合法），
其余行为不变（symlink -> `path.symlink_escape`；OSError -> `format.invalid`；非目录 ->
`format.invalid`）。`read_marker_snapshot` 调用处忽略返回值，行为不变（`.draft` 缺失时继续由
leaf 分类给出 `format.missing`）。

- [x] Step 1：实施重命名与返回值；`read_marker_snapshot` 调用处适配。
- [x] Step 2：运行 `tests/unit/test_graph.py` 全部快照矩阵用例，保持全绿（本任务不要求红灯，
  属于为任务二暴露权威函数的结构步骤）。
- [x] 阶段门禁：`uv run pytest tests/unit/test_graph.py -q` 通过；`ruff check` 与
  `pyright` 无新告警。

## 任务二：`FilesystemProbe.managed_at` 改用单一权威（红灯 -> 实现）

- [x] Step 1（红灯，`tests/integration/test_paths.py` 新增用例，走
  `select_managed_root(probe=FilesystemProbe())`）：
  - `.draft` 为 symlink：断言 `DraftError.code == "path.symlink_escape"` 且
    `paths == (<root>/.draft,)`。基线上现为 `format.invalid` @ marker，必须先失败。
  - `.draft` lstat EIO：按既有 integration 的 EIO 注入方式（对 `.draft` 路径 monkeypatch
    `os.lstat` 抛 `OSError(errno.EIO)`，参照 `tests/integration/test_lifecycle.py` 与
    `tests/unit/test_graph.py` 中 snapshot 读取器的 EIO 注入先例），断言
    `code == "format.invalid"` 且 path 为 marker 路径。基线上现为 `format.missing`，必须先失败。
  - 保持用例（基线即绿，防回归）：`.draft` 为普通文件 -> `format.invalid`；无 `.draft` ->
    `format.missing`。
- [x] Step 2：实现 `paths.py` 的
  `FilesystemProbe.managed_at`：调用 `classify_marker_parent(directory / INTERNAL_DIR,
  directory / MARKER_PATH)`，返回 `False` 时直接 `return None`；否则 `return
  read_marker(directory)`。删除 `is_symlink/exists/is_dir` 三连分类。禁止捕获并折叠任何
  `DraftError`。
- [x] Step 3：定向门禁 `uv run pytest tests/integration/test_paths.py tests/unit/test_paths.py -q`
  通过。
- [x] 阶段门禁：核心 unit 全量通过（`tests/unit/`），确认 `RootProbe` 协议文档与
  `tests/unit/test_paths.py` 的 fake 无需语义变更（fake 的 `broken` 语义仍符合协议）。

## 任务三：pwd 祖先发现 fail-fast 语义钉住

- [x] Step 1（unit，`tests/unit/test_paths.py`，用 `FakeProbe`）：新增两个用例钉住表二——
  中间祖先的 `managed_at` 抛 `path.symlink_escape` / `format.invalid` 时，`select_managed_root`
  （pwd 来源）原样传播该错误，不返回更外层合法根，也不折叠。基线上 fake 的传播路径大概率已绿；
  其价值是规格钉住，若已绿在提交说明中注明“钉住已确认语义”而非新行为。
- [x] Step 2（红灯，integration，`tests/integration/test_paths.py`）：
  - 场景 A：`outer` 为合法受管根，`outer/mid/.draft` 为 symlink，cwd 在 `outer/mid/deep`。
    断言 pwd 发现抛 `path.symlink_escape` @ `outer/mid/.draft`。基线上该错误码经由旧分类器
    报 `format.invalid`，必须先失败。
  - 场景 B：同布局但 `outer/mid/.draft` lstat EIO（monkeypatch 注入）。断言抛
    `format.invalid`，**不得**静默跳过该祖先后成功发现 `outer` 或报 `root.not_found`。基线上
    正是被折叠跳过，必须先失败。
- [x] Step 3：跑定向 integration；预期任务二的实现已使两场景转绿。若仍有失败，修
  `_discover_from_pwd` 的传播逻辑，禁止以捕获/折叠方式变绿。
- [x] 阶段门禁：`uv run pytest tests/integration/test_paths.py -q` 通过。

## 任务四：CLI 端到端入口断言（`main()`）

- [x] Step 1（红灯，integration，按既有 CLI 错误信封用例的同一 runner 入口执行 `cli.main`）：
  对表一的“symlink / EIO / 普通文件 / ENOENT”四行，经真实 `main()` 入口（非 init 命令，如
  `check` 或 `status`）分别断言：退出失败、错误信封的 code 与 path 逐字段精确，且失败路径
  **前后根树无任何 mutation**（对根目录做前后递归摘要/清单对比）。symlink 与 EIO 两行在基线
  上必须失败（码不一致），另两行基线即绿作防回归。
- [x] Step 2：实现层无新增改动预期（任务二已覆盖）；若 CLI 信封层存在独立的错误改写（折叠码或
  改写 path），在该层修复为原样透传，禁止改信封协议本身。
- [x] 阶段门禁：`uv run pytest tests/integration -q`（核心 integration 全量）通过。

## 任务五：第十五轮修复记录勘误（L1–L3，文档修正，无需红灯）

`docs/review-notes/2026-08-19-draft-candidate-root-incremental-migration-round-15-fix-record.md`
不纳入版本控制，直接修改：

- [x] L1：在“全部实际 marker 消费者”清单补入 `graph.load_graph`、`graph.load_graph_tolerant`、
  `query.load_registered_page`（并注明 `query.load_tolerant_view` 为间接消费者），归类与
  round-16 记录一致（均 state-only，无行为违约）。
- [x] L2：修正 adopt 验证证据表述——parent lstat EIO 目前仅由 unit 矩阵覆盖；若任务二~四的
  集成用例落地后已补齐 CLI 层覆盖，改为如实描述新覆盖来源；不得保留与实际测试不符的声明。
- [x] L3：`c4595e7` 改为 `450814e`，注明该提交经 amend 追加了 marker 观察恰好一项的断言。
- [x] 阶段门禁：逐条对照 round-16 记录的 L1–L3 核验证据复查勘误后表述与代码/测试事实一致。

## 任务六：横切审计与完整门禁

- [x] Step 1：审计第三套分类残留——grep 生产代码中与 `INTERNAL_DIR`/`MARKER_PATH` 组合的
  `Path.is_symlink/exists/is_dir/read_bytes/read_text`，确认除已声明的延期范围（根目录自身、
  pages/index/conclusions 形状守卫）外不存在绕过 `classify_marker_parent`/`read_marker_snapshot`
  的 marker 身份分类。
- [x] Step 2：完整离线门禁（与 round-16 记录同一套）：核心 unit、核心 integration、适配层
  unit、适配层 integration 分别运行；`ruff check`；`pyright`（strict）；`check-adapters.sh`；
  `git diff --check`。网络集成测试保持 deselected，不表述为通过。
- [x] Step 3：汇报契约闭合情况、影响范围、验证证据与未核验事项，交由评审者进行下一轮独立
  复核；不得以测试全绿替代对表一/表二逐行闭合的说明。

---

## 验收条件（最终）

1. 表一四行经 `main()` 真实入口逐行成立（code 与 path 精确断言），失败路径树不变。
2. 表二两个异常祖先场景（symlink、stat 失败）按“立即失败”通过集成用例。
3. 全部既有测试无回归；第 2 节列出的完整离线门禁全部通过。
4. 横切审计确认不存在第三套 marker 身份分类；延期范围表述与代码事实一致。
5. round-15 修复记录的 L1–L3 勘误完成且与事实一致。

## 建议提交拆分（非硬约束）

1. 任务一 + 任务二（权威函数公开化与 `managed_at` 改造及其红灯用例）；
2. 任务三（祖先发现钉住用例）；
3. 任务四（CLI 端到端用例）；
4. 任务五为 `docs/review-notes/` 内勘误，不提交。
