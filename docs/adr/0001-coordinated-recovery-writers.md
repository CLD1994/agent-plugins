# 以受协调 writer 作为恢复串行化边界

Draft 以同一 Draft 根的 `operation.lock` 串行化受协调 CLI writer；未持锁候选扫描只读取和分类，任何 journal、恢复材料、权威删除包或 operation 目录的变更都必须在取得匹配 lock 后重新读取当前事实再执行。我们不承诺阻止同权限、绕过该协议的本地进程改名或替换 `.draft/` 节点，因为 Python 标准库没有跨平台的条件式命名空间变更原语；对扫描或读取时可观察到的符号链接、非法类型、损坏和身份不一致仍失败关闭。

## Considered Options

- 将 FD/inode 身份复核视为任意本地改绑的原子防护：拒绝；复核与最终按名称的系统调用之间仍存在不可消除的间隙。
- 停用所有未持锁候选的清理：拒绝；受协调 writer 模型下，取得 matching lock 后可以安全收敛已认证的恢复现场。

## Consequences

所有 recovery 入口必须把 discovery、锁取得和锁内重读分开；锁竞争时必须丢弃旧快照，不得继续清理。绕过 lock 的外部变化只能在 Draft 实际观察到时报告，不能表述为被完全阻止。
