# Draft 无锁恢复身份闭合实施计划

**目标：** 让两个公开无锁恢复入口共享 restore 关联决策，并在 operation 目录从枚举到
读取、写入、清理和删除的整个生命周期内保持同一节点身份。

**架构：** 恢复扫描打开 `.draft/operations` 的 no-follow directory FD，并为每个候选
operation 保存已验证的 `(st_dev, st_ino)` 和 directory FD。后续 pending 收敛、journal
读取与耐久写入、恢复副本清理、空目录删除都只经该 FD 链执行，并在名字副作用前复核
operation ID 仍绑定同一 inode。无锁 restore 分类移入 operations 模块的共享边界，两个
公开入口只消费相同的分类结果。

**技术栈：** Python 3.12、标准库 `os`/`stat`、pytest、uv、Ruff、Pyright。

**规格：** `.scratch/draft-durable-link-index-maintenance/spec.md` §11、§12、§14；
`docs/design.md` §13.3、§13.5；
`docs/review-notes/2026-08-25-durable-link-index-docs-round-5.md`。

## 当前目标契约

### 无锁 Operation 目录身份

| 观察事实 | 允许动作 | 结果 |
| --- | --- | --- |
| `.draft/operations` 缺失 | 不产生候选 | 两入口成功返回空结果 |
| operations 节点、operation 子项或 ID 形状不合法 | 不继续打开或解析 | `operation.inconsistent_state`，保留现场 |
| operation 子项是符号链接 | 不跟随目标 | `path.symlink_escape`，root 外内容不读不写 |
| 已打开目录的名字改绑、消失或变为普通目录 | 不在旧 FD 上继续提交副作用 | `operation.inconsistent_state`，保留两个现场 |
| 已打开目录仍为同一 inode | 只通过该 directory FD 访问 journal、pending、恢复副本和空目录 | 按 phase 继续分类 |
| 合法空目录 | 经同一 FD 证明为空并复核名字身份后删除 | 只删除该已验证目录 |
| 无 journal 的非空目录、journal/pending 类型或协议错误 | 停止 | 既有 `operation.inconsistent_state` 或 journal 错误，保留现场 |

目录替换的错误码只描述 operation 目录身份：符号链接固定为
`path.symlink_escape`，其他缺失、类型变化或 inode 不一致固定为
`operation.inconsistent_state`。journal 或 pending 叶自身的格式与状态错误继续使用现有
journal 错误语义。

### 无锁 Restore 关联

| R 的事实 | 动作 | 可观察结果 |
| --- | --- | --- |
| `completed` 或 `rolled_back` | 保留终态审计 | 无副作用 |
| `allocating`、无 mutation、没有 `restores_trash_operation`，且所有 T 均不反向关联 R | 先精确清理 R 的授权恢复副本，再耐久写 `rolled_back` | 成功后重复恢复无副作用 |
| `allocating` 且已有前向认领、任一 T 反向关联 R、T 缺失、T 损坏或双向 ID 错配 | 不清副本、不写终态 | `operation.inconsistent_state` 或更精确读取错误；R/T/权威包保持 |
| 其他 restore phase | 不在无锁路径收敛 | `operation.inconsistent_state`，保留现场 |

任何清理预检、恢复副本摘要验证、journal 写入或节点身份复核失败都不把 R 解释为已
`rolled_back`；允许修复存储条件后再次恢复。

## 修改边界

- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- 修改：`plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- 测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- 修改：`docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md`

不改变 journal v4、合法 phase transition、链接索引 revision、CLI 错误或退出码。不得
创建兼容 wrapper、递归删除、glob、字符串拼接 operation 路径，或在不安全状态
catch-and-continue。

## 任务 1：以公开入口锁定恢复分类与替换矩阵

**接口：** `operations.recover(root)` 与
`trash_lifecycle.recover_interrupted_operations(root)`。

- [ ] 在 `test_operations.py` 构造无锁双向关联的 `T=trash_retained` 和
  `R=restore/allocating`，直接调用 `operations.recover`。断言
  `operation.inconsistent_state`、R 仍为 `allocating`、T 的
  `restore_operation_id`、权威包和两个 journal bytes 均不变。
- [ ] 在两个测试文件中分别覆盖未关联 R、前向引用但 T 缺失、T 损坏和反向 ID 错配。
  只有未关联分区可清副本并写 `rolled_back`；其他分区必须保留现场。对未关联分区补
  清理失败与终态 journal 写入失败后的重试断言。
- [ ] 对两个公开入口分别构造 allocating operation。以 monkeypatch 包装实际的
  `os.open` 或 directory-FD journal 读取边界：operation FD 已成功打开后替换同名目录，
  覆盖 root 外符号链接与同名普通目录。断言外部 sentinel 从未被读取或修改，原目录及
  journal 不被清理，错误码分别为 `path.symlink_escape` 和
  `operation.inconsistent_state`。
- [ ] 在 journal 已读取而 allocating 清理、pending 发布或终态 journal 写入尚未开始时
  注入同一矩阵。验证每条副作用前都会拒绝身份变化；合法空目录的 `rmdir` 也必须有一条
  替换反例。
- [ ] 运行两个测试文件，确认每个行为分区均由真实文件系统测试而非私有函数调用图
  证伪。

## 任务 2：建立 operation directory FD 所有权边界

**接口：** 在 `operations.py` 定义只供恢复使用的目录句柄和上下文管理器，形状为：

```python
@contextmanager
def open_recovery_operation_directories(
    root: Path,
) -> Iterator[tuple[RecoveryOperationDirectory, ...]]: ...

def read_unlocked_operation_journal(
    entry: RecoveryOperationDirectory,
) -> Journal | None: ...

def remove_empty_recovery_operation_directory(
    entry: RecoveryOperationDirectory,
) -> None: ...
```

- [ ] 从 root 开始逐组件以 `dir_fd`、`O_DIRECTORY` 和 `O_NOFOLLOW` 打开
  `.draft/operations`；目录缺失返回空候选，其他类型和 I/O 失败映射当前稳定错误。
- [ ] 从 operations FD 枚举名字，先 `lstat` 分类、验证 32 位小写十六进制 ID，再用
  `O_DIRECTORY | O_NOFOLLOW` 打开子目录；把打开 FD 的 `fstat` 与枚举时的
  `(st_dev, st_ino)` 比较。符号链接报 `path.symlink_escape`；普通文件、消失或 inode
  不同报 `operation.inconsistent_state`。
- [ ] 在 `RecoveryOperationDirectory` 上提供“名字仍指向原 inode”的复核。所有读取、
  pending 发布、journal 写入、恢复副本清理和 `rmdir` 在副作用前调用它；所有实际
  `stat`、`open`、`replace`、`unlink`、`rmdir` 都使用已经打开的 parent 或 operation FD。
  上下文退出时关闭每一个 FD，不泄漏描述符。
- [ ] 将 `_read_optional_journal`、`resolve_journal_pending` 和 `write_journal` 中可复用
  的解析与转换验证保留为单一权威；新增 directory-FD 版本只替换 I/O 载体，不复制
  journal schema、transition 或 pending 决策。`Journal.directory` 在该路径只用于错误
  显示，不能再作为无锁恢复的 I/O 根。
- [ ] 抽取可从既有 operation FD 打开祖先的锚定叶工具，并让无锁恢复副本清理复用它。
  保留现有锁内清理的公开行为；无锁清理不得回退到 `journal.directory` 的 `Path` 访问。

## 任务 3：让两个入口消费同一无锁分派

**接口：** 在 `operations.py` 提供共享的“单个已锚定无锁 operation”分派函数；其输入
包含当前 `RecoveryOperationDirectory`、已解析 `Journal` 和本轮已锚定候选集合，输出为
`OperationResult | None` 或稳定 `DraftError`。

- [ ] 将 R/T 反向关联扫描移入该共享分派。扫描只读取本轮候选的已锚定目录；T 指向 R
  时同时验证 T 的 kind、R 的 kind、两侧 ID 和 R 的前向引用。没有前向引用但存在反向
  引用也属于错配，不得放弃 R。
- [ ] 将一般 allocating 的授权材料清理、restore 孤儿的“先清后写终态”、中间 phase
  拒绝、终态/等待态无副作用和未知 phase 失败关闭集中在同一分派。任何目录身份变化都
  先于清理和 journal 写入失败关闭。
- [ ] 把 `operations.recover` 改为在目录上下文内逐项消费共享分派；删除
  `restores_trash_operation` 键存在即终态化的专用旁路。
- [ ] 把 `recover_interrupted_operations` 改为消费同一分派，并仅保留它拥有的锁内
  lifecycle 结果包装。删除 `_handle_no_lock_dir` 与 `_restore_linked_back` 中重复的
  phase 或关联判断，避免两个公开入口再次漂移。
- [ ] 运行任务 1 两个 integration 文件、Ruff、format check、strict Pyright 与
  `git diff --check`；任何失败先定位根因，不能删除或放宽测试。

## 任务 4：收敛当前契约并完成验收

- [ ] 将 `draft-recover-lifecycle-boundary-fix.md` 的规格引用更正为 `.scratch` §11、§12、
  §14 与 `docs/design.md` §13.3、§13.5。
- [ ] 将“每次经 `operation_journal_path` 重建 Path”改为当前的 FD/inode 身份契约，明确
  哪些错误是符号链接、哪些是名称改绑或类型变化；保留全部 locked/unlocked phase 表。
- [ ] 检索该当前契约，确认没有不存在的段落号、实现过程、已否决机制或旧 `Path` 安全
  承诺。
- [ ] 分别运行 runtime unit、adapter unit、runtime 非网络 integration、adapter 非网络
  integration、runtime Ruff、format、strict Pyright、adapter gate 与 `git diff --check`。
  记录测试计数；network integration 和 Windows 只记录为未运行。
- [ ] 在新建 Round 6 review note 后，使用 Terra 审 Standards、Sol 审 Spec。两个轴无
  P1/P2 后，才更新 progress ledger；最终工作树只能保留用户的 `M AGENTS.md`。
