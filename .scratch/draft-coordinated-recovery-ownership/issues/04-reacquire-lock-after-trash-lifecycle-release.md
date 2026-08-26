# 04: 修复释放旧锁后的 Trash 生命周期竞争边界

**What to build:** 修正 Trash 生命周期路径在释放遗留 lock 后继续使用旧快照的问题，使后续副作用必须重新取得匹配 lock、重读当前事实并交给锁内 resolver。

**Blocked by:** 03: 将 R/T 分类和 Trash 恢复接入锁内所有权。

**Status:** ready-for-agent

- [ ] 生命周期路径释放旧 lock 后不得继续使用旧 journal、operation ID、package 或 R/T 快照执行清理、终态写入或 handoff。
- [ ] 释放后若另一受协调 writer 取得 lock，生命周期调用方丢弃旧快照并返回稳定恢复未决分类；新 writer 的 journal、材料、目录与关系保持不变。
- [ ] 释放后重新取得 matching lock 时，必须重读当前 lock、journal、package、关系和时间相关事实，再按当前合法状态决定 waiting、terminal、error 或授权清理。
- [ ] 重新取得 lock 的路径不得重复 handoff；已提交 handoff/purge 仅按当前 journal 授权清理，未知或缺少失败记录的状态保留现场并报告错误。
- [ ] 集成测试覆盖释放后无竞争、释放后新 writer 抢锁、重取锁后事实变化、重复生命周期调用和 handoff 重试，证明旧快照不会产生副作用。
