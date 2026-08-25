# Draft Recover 当前契约

本文记录 `draft recover` 当前有效的恢复边界、状态决策和验证入口。规范依据为
`.scratch/draft-durable-link-index-maintenance/spec.md` §11、§12、§14，以及
`docs/design.md` §13.3、§13.5。本文不记录实施过程、历史行为或提交步骤。

## 入口边界

`recover_interrupted_operations(root)` 是恢复入口，负责锁内崩溃现场和未持锁候选扫描。
扫描只发现和分类；任何清理、journal 写入、phase transition 或目录删除都必须先取得
匹配 operation lock，并在锁内重读当前事实。它只收敛已经存在的恢复现场，不因当前时间推进 `trash_retained`，
也不发起系统回收站移交或最终清理。

`advance_trash_lifecycle(root, now=None, handoff=None)` 是到期生命周期入口。普通受管
命令在业务逻辑前置阶段调用它，按 operation_id 顺序推进到期的 `trash_retained` 和
已经进入 `trash_handoff_pending` 的项目；首个损坏项目停止，此前成功结果保留在报告
中。两个入口不互相组合，也不通过伪造时间改变责任边界。

所有入口保持 journal schema、合法 phase transition、错误码、退出码、revision、
链接索引协议和失败保留语义不变。

## 持锁 Trash

持锁 Trash 由 `_recover_locked_trash` 按 journal phase 分派。`recover` 只能继续已开始
的恢复或完成已提交清理；到期推进仍归生命周期入口。

| phase | 动作 | 允许的副作用 | 结果 |
| --- | --- | --- | --- |
| `allocating`、`prepared`、`applying`、`validating`、`rolling_back` | 委托通用锁内 operation 恢复 | 按原 mutation journal 前向完成或回滚 | 原 operation 的合法终态 |
| `link_index_updating`、`link_index_published`、`link_index_rebuild_required` | 委托通用锁内 operation 恢复 | 只收敛同一 revision 的索引恢复 | `trash_retained` |
| `trash_retained` | 释放该 operation 的崩溃残留锁 | 不读取 handoff 副本，不调用 handoff，不写 phase，不删除权威包 | 仍为 `trash_retained`，恢复结果为空 |
| `trash_handoff_pending` | 继续持锁生命周期恢复 | 可继续已耐久记录的 handoff、失败重试或 purge | pending 或合法清理终态 |
| `trash_handoff_succeeded` | 完成已提交清理 | 按已提交 journal 清理授权权威包和 attempt，不再次调用 handoff | 写入 `completed`，释放锁 |
| `trash_purge_committed` | 完成已提交清理 | 按已提交 journal 清理授权权威包和 attempt，不再次调用 handoff | 写入 `completed`，释放锁 |
| `completed`、`rolled_back` | 委托通用锁内终态校验与清理 | 只处理当前 operation 材料并释放锁 | 保持原终态 |

kind、phase、锁 operation_id、journal 内容或节点类型不一致时，返回对应稳定
`DraftError` 并保留锁、journal、权威包和恢复材料；不得解释为成功或 `trash_retained`。
重复执行只处理仍存在且已被协议授权的现场，不重复调用已提交的外部适配器。

## 未持锁候选扫描

`operations.recover` 与 `recover_interrupted_operations` 共享纯 discovery 和 restore 关联
分类器。discovery 不收敛 `.pending`，不清副本、不写 journal、不删除目录；它只产生
保留、失败或“取得指定 operation lock 后锁内重读”的动作。锁竞争失败时丢弃本轮快照，
返回稳定 recovery error，不得继续清理。

| kind/phase | 动作 | 结果与失败语义 |
| --- | --- | --- |
| 任意 kind，目录不存在 | 不产生候选 | 返回空结果 |
| 空目录，无 journal 或 `.pending` | 取得该 ID 的 lock 后锁内再次确认 | 删除 benign allocation residue 并同步父目录；不得复用 |
| 非 restore kind，`allocating` | 取得该 ID 的 lock 后锁内重读 | 耐久写 `rolled_back`，清理当前 journal 授权的材料，释放锁 |
| restore，`allocating`，预认领或有效 R/T 关联 | 只由 R 取得 lock 后锁内重读关系 | 按 restore 状态机前向完成或安全回滚 |
| `prepared`、`applying`、`validating`、`rolling_back` 或索引 phase | 无锁发现即失败关闭 | `operation.inconsistent_state`，不修改现场 |
| `trash_handoff_succeeded`、`trash_purge_committed` 或 terminal 的授权残留 | 取得对应 lock 后锁内重读 | 只完成已授权清理；不得再次 handoff |
| 无关联 `trash_retained` | 保留 | 无副作用；不推进生命周期 |
| 有完整失败记录的 `trash_handoff_pending` | 保留 | 无副作用；仅生命周期入口按时间推进 |
| 已完整解关联且无授权残留的 `completed`、`rolled_back` | 保留审计 | 无副作用 |
| 未知 phase、损坏 journal、kind 不合法 | 失败关闭 | 保留可诊断现场，不降级为缺失、成功或无关目录 |

未持锁扫描不触碰受管内容，不递归删除，不使用 glob，不把不安全状态解释为成功。锁内
重读、清理或写入失败均停止当前入口并保留证据。

## Recovery 目录身份

恢复扫描从 root 经 `.draft/operations` 逐组件以 `O_DIRECTORY | O_NOFOLLOW` 打开目录
FD，再枚举其直接子项。每个子项先记录 `(st_dev, st_ino)`，随后以同一 parent FD 和
`O_DIRECTORY | O_NOFOLLOW` 打开；打开后的 `fstat` 必须与枚举身份一致。枚举不授权后续
沿可重新解析的旧路径读取或删除。

| 目录状态 | 决策 |
| --- | --- |
| `.draft/operations` 不存在 | 无候选，返回空结果 |
| `.draft/operations` 不是目录 | `operation.inconsistent_state`，保留节点 |
| 子项是符号链接 | `path.symlink_escape`，不读取链接目标 |
| 子项是普通文件或其他非目录 | `operation.inconsistent_state` |
| 子项目录名不是合法 operation ID | `operation.inconsistent_state` |
| 合法 ID 的空目录且无 journal | 标记 benign allocation residue；取得匹配 lock 后再次确认为空才删除 |
| 合法 ID 的非空目录且无 journal | `operation.inconsistent_state`，保留全部内容 |
| `.pending` 存在 | discovery 标记为需锁内收敛；不得在未持锁时发布 |
| 合法 journal | 按 kind/phase 决策表交给对应恢复责任边界；journal、pending、`originals`/`staged` 子目录和 copy leaf 均须持有 no-follow FD 与 inode 认证 |

discovery 保存已打开的 operation directory FD，并在读取前以 no-follow 认证其身份；
锁内 I/O 仍拒绝符号链接、非法类型和协议损坏。FD/inode 认证用于输入安全和可观察诊断，
不作为无锁命名副作用的 compare-and-swap。`Journal.directory` 在此边界只作为错误显示
路径，不能作为 I/O 根；所有 FD 在恢复上下文退出时关闭。

因此，在任一次后续读取或副作用开始前，若 `operations/<合法 ID>` 已被替换为指向 root
外部的符号链接，返回精确的 `CheckCode.PATH_SYMLINK_ESCAPE.value`；若缺失、类型变化或
inode 改绑，返回 `CheckCode.OPERATION_INCONSISTENT_STATE.value`。不得读取或修改 root
外内容，也不得在名字改绑后继续使用旧目录 FD 提交写入或清理。

## 职责边界

- operations 模块的纯 discovery 负责完整扫描、R/T 双向关系分类和失败保留；两个公开
  入口只消费这一分类，不各自维护 phase 白名单。
- 锁内 resolver 重新读取 journal、schema、transition、目录成员和关联，并只按当前
  journal 授权清理材料、写入终态或删除空 allocation residue。
- 清理不得递归删除、glob 或以 `Journal.directory` 重新解析路径。
- `_recover_locked_trash` 负责持锁 Trash 的 phase 路由；链接索引和 mutation 的具体
  恢复继续使用 operations 模块权威原语。

这些边界不得通过复制 phase 清单、递归删除、glob、字符串拼接路径或 catch-and-continue
来绕过。无法证明目录身份、journal 权威性、摘要或锁所有权时，必须停止并保留可诊断
现场。

## 验证入口

核心 runtime 与适配层测试分开运行；network 测试标记为集成测试属性，而不是第三类
测试。当前契约可用下列命令复现验证：

```bash
RUNTIME=plugins/draft/skills/draft/scripts/runtime

uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/unit" -q
uv run --project "$RUNTIME" --locked pytest "$RUNTIME/tests/integration" -q -m 'not network'
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/unit -q
uv run --project "$RUNTIME" --locked pytest plugins/draft/scripts/tests/integration -q -m 'not network'
uv run --project "$RUNTIME" --locked ruff check \
  "$RUNTIME/src/draft/operations.py" "$RUNTIME/src/draft/trash_lifecycle.py"
uv run --project "$RUNTIME" --locked ruff format --check "$RUNTIME/src" "$RUNTIME/tests"
(cd "$RUNTIME" && uv run --locked pyright)
bash plugins/draft/scripts/check-adapters.sh
git diff --check
```

network integration 和 Windows 验证不在本轮验证范围内；它们不能被上述结果代替或
表述为已经验证。
