# Draft 无锁恢复身份闭合 Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让两个无锁恢复入口只读取和消费仍绑定到本轮认证 inode 的恢复节点；身份一旦变化即保留现场并失败关闭。

**Architecture:** 在现有 recovery FD 链上保留并验证 root/.draft/operations 的父子身份，且为每个可消费的 journal、pending、恢复副本目录和文件保留打开 FD 与 `(st_dev, st_ino)`。读取始终来自该 FD；发布、link、unlink 与 rename 前再验证当前名字仍绑定同一节点。由于 POSIX `rmdir(name, dir_fd=...)` 没有“仅当 name 仍是指定 inode”的原子原语，无锁恢复不删除 operation 目录。

**Tech Stack:** Python 3.12、标准库 `os`/`stat`/`errno`、pytest、uv、Ruff、Pyright。

**Spec:** `.scratch/draft-durable-link-index-maintenance/spec.md` §11、§12、§14；`docs/design.md` §13.3、§13.5；`docs/review-notes/2026-08-25-durable-link-index-unlocked-recovery-round-6.md`。

## Global Constraints

- 只改变无锁 recovery 的内部 I/O 边界；不改变 journal v4、phase transition、索引 revision、CLI 错误码或退出码。
- `operations.recover(root)` 与 `trash_lifecycle.recover_interrupted_operations(root)` 必须使用同一无锁扫描和 restore 分类器。
- operation、journal、pending、恢复副本目录或叶的身份无法证明时，停止并保留两个现场；不得把失败解释为缺失、已完成或可重试成功。
- `path.symlink_escape` 仅表示认证名字变为符号链接；缺失、类型变化或 inode 改绑为 `operation.inconsistent_state`；journal 读取/协议 I/O 维持 `operation.journal_corrupt`；耐久写失败维持 `operation.durability_failed`。
- 无锁路径不得使用 `Journal.directory`、`Path.iterdir()`、glob、递归删除或字符串拼接来重建 operation I/O 路径。
- 无锁路径不得删除 operation 目录；空目录保留为无副作用审计现场，重复恢复仍成功返回。
- 所有 `os.link` 使用 `follow_symlinks=False`；每个调用、`os.unlink`、`os.replace` 和 `os.rmdir` 的前置 identity gate 都必须紧邻实际系统调用。
- 旧 Path scanner/reader/remover/dispatcher 不得保留公开兼容层；仓库生产调用方必须迁移或删除。

## 已确认的状态契约

| R=restore/allocating 的当前认证事实 | 允许动作 | 结果 |
| --- | --- | --- |
| 有 mutation | 无锁路径不收敛 | `operation.inconsistent_state`，保留现场 |
| 有 `restores_trash_operation` 前向键 | 无锁路径不收敛 | `operation.inconsistent_state`，保留现场 |
| 任一当前认证的 T 含 `restore_operation_id=R` | 无锁路径不收敛 | `operation.inconsistent_state`，保留 R/T |
| T 缺失、journal/pending 损坏、关系 ID 错配或身份无法重新认证 | 无锁路径不收敛 | 原有更精确读取错误或 `operation.inconsistent_state` |
| 无 mutation、无前向键、无反向关联，并且副作用前再次验证仍成立 | 清理 R 的授权恢复副本，再写 `rolled_back` | 返回 R 的 `OperationResult` |
| `completed` 或 `rolled_back` | 无副作用 | 保留终态审计 |

## 修改边界

- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- Modify: `docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md`

### Task 1: 先以两个公开入口锁定身份变化与失败映射

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**Interfaces:**
- Consumes: `recover(root)`；`recover_interrupted_operations(root)`；现有 journal 构造 helper。
- Produces: 两个入口共用的真实文件系统替换矩阵和稳定错误验收。

- [ ] **Step 1: 写出 operations 与 operation 目录身份的失败测试。**

  对每个公开入口参数化 `replace_with in (symlink, missing, regular_file, new_directory)`：
  通过包装 `draft.operations.os.open`，在 `.draft/operations` 或 `<operation_id>` 成功
  打开后立刻替换原名字。外部目录放置 `journal.json` sentinel；断言入口抛
  `path.symlink_escape`（symlink）或 `operation.inconsistent_state`（其余），外部
  sentinel、旧 journal bytes 和替换节点均保持不变。

- [ ] **Step 2: 运行新增测试确认当前实现失败。**

  Run: `uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py -q -k 'recovery and identity'`

  Expected: 至少一个同类型替换、operations 目录替换或 operation 目录删除场景未拒绝，或现有空目录测试与“禁止 rmdir”契约冲突。

- [ ] **Step 3: 写出 journal、pending 和 copy 叶同类型替换的失败测试。**

  对 R=未关联 `restore/allocating` 和普通 `allocating`，在下列精确点注入同类型替换：
  journal 打开后读取前、journal 读取后终态 publish 前、`journal.json.pending` 决策后
  publish/unlink 前、`originals`/`staged` 打开后扫描前、copy leaf 读取后 link/unlink 前。
  每个点覆盖 symlink、missing、regular file、new inode；断言不读取或覆盖 external
  sentinel，R/T journal bytes 与 copy bytes 不变。对普通 allocating 的 journal unlink 与
  空 operation 目录处理，断言 journal 不被删除且 operation 目录仍存在。

- [ ] **Step 4: 写出 restore 关联刷新和 I/O 映射测试。**

  初始扫描后，把 T 或 R 的 journal 替换为含新 forward/reverse 认领的同类型文件；R 在
  cleanup/write 前必须报错，不得写 `rolled_back`。将 recovery journal 的 `stat`/`read`
  注入 `EACCES`、将 copy 的 `stat`/`read` 注入 `EIO`，两个入口均断言收到稳定
  `DraftError`（分别为 `operation.journal_corrupt`、`operation.inconsistent_state`）且
  没有副作用。

- [ ] **Step 5: 运行测试确认失败矩阵完整。**

  Run: 同 Step 2 命令，外加 `-k 'unlocked or recovery'`。

  Expected: FAIL，直到 Task 2 与 Task 3 的 FD 身份边界实现完成。

### Task 2: 建立有层次的 recovery 目录和叶身份句柄

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:850-1603`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**Interfaces:**
- Consumes: `_name_node(parent_fd, name, ...)`、`_same_name_node(...)`、
  `_parse_journal_bytes(...)`、`validate_journal_transition(...)`。
- Produces: 建议新增的私有 `_RecoveryDirectory` 与 `_RecoveryLeaf`，以及仅供
  `recover_unlocked_operations` 使用的句柄化 journal reader/writer。

- [ ] **Step 1: 实现目录身份链。**

  将 `_open_recovery_operations_fd` 改为返回一个拥有 `root_fd`、`.draft` FD、
  `.draft/operations` FD 及各自认证 `(st_dev, st_ino)` 的上下文对象。每次打开目录后
  立即 `fstat` 与打开前 no-follow `stat` 比较；`operations` 名在 `.draft` FD 中的
  `verify()` 必须发生在扫描、打开 operation、每个 operation `verify()` 和所有子项
  副作用前。关闭所有权按子→operations→.draft→root 的逆序转移，先清空拥有字段再
  `os.close`，close 失败不得二次关闭。

- [ ] **Step 2: 实现叶身份句柄。**

  建议接口如下，名称可按现有代码风格微调，但不得改变其契约：

  ```python
  @dataclass
  class _RecoveryLeaf:
      parent: _RecoveryDirectory
      name: str
      node: tuple[int, int] | None
      fd: int | None

      def verify(self) -> EntryKind: ...
      def read_verified(self) -> bytes: ...
      def consume_verified(self, action: Literal["unlink", "replace", "link"]) -> None: ...
      def close(self) -> None: ...
  ```

  `FILE` 叶先 no-follow 打开、以 `fstat` 对照 lstat 的 node；`DIRECTORY` 叶以
  `O_DIRECTORY|O_NOFOLLOW` 打开并同样对照。`read_verified()` 必须从 held FD 的开头读取，
  而不是重新按名字打开；任何非 `ENOENT` 的 stat/open/read/close 失败映射该叶拥有的
  `DraftError`。`verify()` 必须先验证父链，再验证 name→node；symbolic link 返回精确
  `path.symlink_escape`，其他变化返回其调用方约定错误。

- [ ] **Step 3: 让 journal 与 pending 使用认证叶。**

  将 `_read_optional_journal_fd`、`_resolve_journal_pending_fd` 和
  `write_unlocked_journal_fd` 改为接收/持有 journal 与 pending 叶。解析读取同一 journal
  FD；`_replace_publish`/`_create_only_publish` 的每个 `open`、`link`、`unlink`、
  `replace` 前调用一个 identity gate，gate 同时复核 operation、operations 和目标/pending
  叶。`os.link(..., follow_symlinks=False)`。目标或 pending 已改绑时抛异常而不发布、
  不消费未知节点。

- [ ] **Step 4: 处理不可原子证明的空目录。**

  删除 `remove_empty_recovery_operation_directory_fd` 的调用与任何无锁
  `os.rmdir(entry.name, dir_fd=...)`。无 journal 的空 operation 目录返回 `None` 且保留；
  已清理普通 allocating 后删除 journal，但也保留空 directory。更新现有“removes empty
  directory”测试的预期为保留空目录并在第二次恢复无副作用。

- [ ] **Step 5: 运行 Task 1 两份 integration 测试。**

  Run: `uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py -q`

  Expected: PASS。

### Task 3: 把副本清理和 restore 分类接到认证句柄

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py:3032-3238, 5350-5440`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py:20-72, 728-762`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- Test: `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`

**Interfaces:**
- Consumes: Task 2 的 `_RecoveryDirectory`/`_RecoveryLeaf`、当前已验证的 `Journal`。
- Produces: 只经认证 handle 的 `_fd_clean_recovery_copies` 和副作用前刷新关联的
  `_recover_unlocked_operation_fd`。

- [ ] **Step 1: 用认证 subdirectory 和 copy leaf 替换 `_AnchoredLeaf`。**

  `_fd_clean_recovery_copies` 打开 `originals`/`staged` 后保存它们的 directory handle，
  扫描与每个 copy leaf 的读取、pending/quarantine 收敛、link/unlink 前均验证 subdirectory
  和 leaf name→inode。不得在 recovery 路径重用会重新按名字打开的 `_AnchoredLeaf`。copy
  输入失败映射 `operation.inconsistent_state`；link/unlink/fsync 耐久失败映射
  `operation.durability_failed`；每个映射用 `raise ... from exc` 保留原因。

- [ ] **Step 2: 在 R 副作用前刷新整轮关联事实。**

  将 `journals_by_name: dict[str, Journal]` 替换为保存 entry、journal leaf 与解析 journal 的
  认证集合。新增私有 refresh：逐项验证 operation 与 journal/pending 身份、从同一 held FD
  重新读取并解析 journal，随后构造本次关联映射。每个 R 的 copy cleanup 和
  `rolled_back` journal write 前调用 refresh 并重跑状态表；只要任一条目变化、读取失败、
  T 缺失/错配或任一 forward/reverse 关联出现，就不得继续该 R。

- [ ] **Step 3: 删除绕过边界的旧 API。**

  删除 `iter_recovery_operation_directories`、`read_unlocked_operation_journal`、
  `remove_empty_recovery_operation_directory` 和旧 Path `recover_unlocked_operation`。
  从 `trash_lifecycle` import 与 `__all__` 删除这四个名字；保留且仅调用
  `recover_unlocked_operations(resolved, skip_operation_id=locked)`。新增测试：
  `trash_lifecycle.__all__` 不含旧名，全仓库生产源码没有旧 API 引用。

- [ ] **Step 4: 运行相关测试和静态门禁。**

  Run:

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration/test_operations.py" "$RUNTIME/tests/integration/test_trash_lifecycle.py" -q
  uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src/draft/operations.py" "$RUNTIME/src/draft/trash_lifecycle.py"
  uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
  (cd "$RUNTIME" && uv run --locked pyright)
  git diff --check
  ```

  Expected: 全部 PASS。

### Task 4: 收敛当前契约、提交并完成全量验收

**Files:**
- Modify: `docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md`
- Modify: `.superpowers/sdd/2026-08-24-draft-durable-link-index-maintenance/progress.md`

- [ ] **Step 1: 更新当前契约。**

  将“已关联 restore 交生命周期决策”替换为状态表中的失败关闭规则；将“合法空目录删除”
替换为“无锁路径因无法 atomically identity-gate `rmdir` 而保留空目录”。明确 journal、
pending、subdirectory 和 copy leaf 也必须被认证，且 R 每次副作用前刷新关联事实。

- [ ] **Step 2: 提交实现。**

  ```bash
  git add plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py \
    plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py \
    plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py \
    docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md
  git commit -m "fix(draft): close unlocked recovery leaf identity"
  ```

  不得暂存或提交用户的 `AGENTS.md` 修改、Round review note 或本执行计划。

- [ ] **Step 3: 运行全部门禁。**

  ```bash
  RUNTIME=plugins/draft/skills/draft/scripts/runtime
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
  uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
  uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'
  uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/integration -q -m 'not network'
  uv run --project "$RUNTIME" --locked ruff check "$RUNTIME/src/draft/operations.py" "$RUNTIME/src/draft/trash_lifecycle.py"
  uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
  (cd "$RUNTIME" && uv run --locked pyright)
  bash plugins/draft/scripts/check-adapters.sh
  git diff --check
  ```

  Expected: 全部 PASS；记录四个 pytest 计数。network integration 与 Windows 标为未运行。

- [ ] **Step 4: 更新账本并交接复审。**

  在 progress ledger 记录 commit、门禁计数、未运行范围与 Round 7 双轴评审要求。此时不得
自行宣布完成；主 Agent 必须独立检查提交、差异、测试日志，并使用 Terra 做 Standards、
Sol 做 Spec 评审。

## 自审

- 规格覆盖：Task 1 覆盖两个入口和失败矩阵；Task 2 覆盖目录/journal/pending identity 与
close 所有权；Task 3 覆盖副本、关联刷新与 API 绕过；Task 4 覆盖当前契约、门禁和 ledger。
- 占位符扫描：每个失败分区、错误码和验证命令均已显式列出，没有未定义的后续工作。
- 类型一致性：Task 2 产出的认证目录/叶 handle 是 Task 3 唯一允许的无锁 I/O 输入；
Task 3 的 refresh 只产生当前认证 `Journal` 映射，供 restore 状态表消费。
