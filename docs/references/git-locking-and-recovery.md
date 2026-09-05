# Git 锁与恢复参考

## 源码可用性

本参考必须以可用的 Git 源码为依据。开始探索前，先在本机查找 Git 源码树；本轮已确认
`/home/zsm1695/git` 可用，revision 为 `2c3adbb`。该路径属于当前环境，不是仓库依赖，
后续工作不得假定它一定存在。

找不到源码时，请用户提供 Git 源码，不自行克隆，也不以未经核验的记忆替代源码结论。
Git 源码只供查证，不是本仓库的子模块或 Draft 运行时依赖。

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

本参考保留 Git 源码观察，供比较本地锁与原子发布机制时查证。Draft 的当前行为由
[运行时准入规格](../../plugins/draft/docs/spec/runtime/admission-and-effects.md) 和
[运行时恢复规格](../../plugins/draft/docs/spec/runtime/recovery.md) 定义；架构取舍见
[受协调 writer 的显式恢复边界](../../plugins/draft/docs/adr/0001-coordinated-recovery-writers.md)。
