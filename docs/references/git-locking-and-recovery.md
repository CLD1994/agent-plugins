# Git 锁与恢复参考

## 源码可用性

本参考必须以可用的 Git 源码为依据。开始探索前，先在本机查找 Git 源码树；本轮已确认
`/home/zsm1695/git` 可用，revision 为 `2c3adbb`。该路径属于当前环境，不是仓库依赖，
后续工作不得假定它一定存在。

找不到源码时，先将 Git 官方源码浅克隆到独立临时目录后再作判断。克隆失败时，立即向
用户求助，请用户提供或克隆源码；不得以未经核验的记忆替代源码结论。不要把 Git 源码
加入本仓库、提交为子模块或作为 Draft 运行时依赖。

## 已核验的 Git 行为

### 协作锁与原子发布

Git 的普通文件更新使用同目录的 `<target>.lock` 临时文件：

1. `lockfile.c` 的 `lock_file()` 请求创建 lock；底层
   `tempfile.c:repo_create_tempfile_mode()` 使用 `O_CREAT | O_EXCL`。
   已有 lock 时，第二个遵守协议的 Git 进程不能取得它。
2. 取得 lock 的进程把完整新内容写入 lock 文件。
3. `commit_lock_file()` 经 `rename_tempfile()` 将 lock 原子重命名为目标。

这个协议串行化的是遵守 `.lock` 约定的 Git 进程。它不声称阻止同权限、绕过 lock 并在
最后一个 `rename()` 前改名的进程；`rename()` 仍按目标名字发布。

相关源码：`lockfile.c:168-190, 206-252, 352-371`，
`tempfile.c:135-160, 335-373`，`lockfile.h:40-116, 345-373`。

### 锁冲突的诊断

Git 在创建 lock 失败时停止当前更新，不自动删除已有 lock。`unable_to_lock_message()`
报告 lock 路径和系统错误。启用 `core.lockfilePid` 时，Git 还读取旁路 PID 文件，显示
进程可能仍运行或看起来已退出；这是提示，不是所有权证明，因为 PID 可以复用。

相关源码与测试：`lockfile.c:254-308`，
`Documentation/config/core.adoc:351-360`，`t/t0031-lockfile-pid.sh`。

### 高价值状态的语义比较

Git 更新 ref 时，除了取得 ref lock，还可要求 ref 的当前 OID 等于调用方提供的 old OID。
`check_old_oid()` 在锁内比较；不匹配时拒绝更新并报告“当前值”和“预期值”。这是对状态
内容的 compare-and-swap，不是 inode 比较。

相关源码：`refs/files-backend.c:2611-2667, 2675-2729`；reftable 后端在锁住 stack 后
也核对 old OID，见 `refs/reftable-backend.c:1229-1293`。

Git 对某些跨存储层的检查-使用竞争明确选择接受，而不是引入代价更高的全局锁；
`refs/files-backend.c:3064-3077` 直接记录了这一取舍。该注释是保障范围必须明确、不能
用一次检查假装消除竞态的实例。

### 损坏状态的处理

Git 区分可清理的临时 lock 与有语义价值的 ref/object。对损坏 ref，读路径可警告或报错；
维护操作不应静默删除损坏 ref。相关测试包括
`t/t6301-for-each-ref-errors.sh` 与 `t/t5312-prune-corruption.sh`。

Git 的对象库是内容寻址且大部分对象不可变，`git fsck` 可以检查对象、ref 和 reflog 的
连通性与格式。Draft 的 journal 和恢复副本不具备相同模型，不能把 Git 的对象库结论直接
套用到 Draft。

## 对 Draft 的适用边界

Draft 不调用 Git，也不要求 Draft 根位于 Git 仓库。本参考只用于选择本地协议原则。

### 可以借鉴

- 用原子创建的 operation lock 串行化遵守 Draft 协议的 writer；不得以一次
  `read_lock() is None` 作为整轮写入授权。
- 取得 lock 后重新读取 journal；锁前扫描只发现候选，不能决定 cleanup 或 phase
  transition。
- journal phase transition 使用当前状态和目标后继状态做语义验证；当前状态不符合预期时
  停止并报告，而不是覆盖。
- lock 已存在时报告恢复待处理或锁冲突；PID、时间等只可作为诊断提示，不能自动证明
  lock 陈旧。
- 对可验证的结构异常给稳定错误；对已经判定为可丢弃的私有恢复副本，可把“内容被替换”
  作为 warning，而不是把它误判成成功或业务状态。

### 不能照搬

- Git 的 lockfile 默认会解析符号链接，除非调用方指定 `LOCK_NO_DEREF`
  （`lockfile.c:175-180`、`lockfile.h:168-178`）。Draft 的 `.draft/` 属于内部协议边界，
  仍必须拒绝并且不跟随符号链接。
- Git 的 OID compare-and-swap 适用于 ref。Draft 不能把 inode 复核伪装成跨平台、原子化的
  compare-and-swap；Python 标准库的 `unlink`、`replace` 和 `rmdir` 没有“仅当名字仍是
  某 inode”这一条件。
- Git 对绕过 lock 的同权限进程没有绝对保护。Draft 也应明确：它保证协议参与者之间的
  串行化，并在可观察的边界诊断外部修改；不承诺阻止外部进程在最后一个命名系统调用
  间隙改写私有恢复垃圾。

## 建议的 Draft 尽力诊断模型

以下模型适用于选择“受协调 writer 通过 operation lock 串行化”的保障范围时；它是从 Git
获得的设计参考，不是当前 Draft recovery 实现的行为声明。当前有效契约以
`docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md` 为准。

在该模型中，“尽力诊断”只覆盖 Draft 实际观察到的事实，不能声称发现不可观察的最终瞬间
竞态。

| 观察到的事实 | 处理 |
| --- | --- |
| operation lock 不能原子取得 | 停止当前恢复，报告锁冲突或恢复待处理；不自动删除 lock。 |
| 获锁后 journal 与锁前快照不同 | 丢弃锁前判断，以锁内 journal 重新分类；需要时返回 snapshot changed warning。 |
| journal 缺失、格式损坏、非法 phase 或关联不闭合 | 返回 `operation.journal_corrupt` 或 `operation.inconsistent_state`，保留现场。 |
| `.draft` 路径组件、operation 或叶节点是符号链接 | 返回 `path.symlink_escape`，不读取、不跟随、不清理链接目标。 |
| operation 中存在未知名称，或期望普通文件的位置变成目录等异常节点 | 返回 `operation.inconsistent_state`，不递归删除。 |
| 已确认可清理的普通恢复副本内容或 inode 改变 | 作为可观察 warning 记录后，按授权名称删除；该规则不适用于 journal 语义判断。 |
| journal 写入、目录同步或 lock 释放失败 | 返回 `operation.durability_failed`，保留足以重试恢复的持久现场。 |

若采纳该模型，无锁扫描不删除 journal、恢复副本或 operation 目录。任何会改变这些名字
的动作都先取得对应 operation lock，并在 lock 内重读和验证当前 journal。这样可以防止
正常 Draft writer 相互破坏；绕过 lock 的直接 `.draft/` 修改则按上述可观察边界处理。
