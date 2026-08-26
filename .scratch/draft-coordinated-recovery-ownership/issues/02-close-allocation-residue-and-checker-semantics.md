# 02: 闭合恢复目录与 checker 的 allocation residue 语义

**What to build:** 统一恢复与 checker 对合法空 operation-id 目录的 allocation residue 分类，确保无 journal 的合法空目录能够在锁内安全闭合，同时保留非空未知现场的诊断证据。

**Blocked by:** 01: 在锁内收敛通用未持锁恢复候选。

**Status:** ready-for-human

- [x] 合法 operation-id 的空目录且没有 journal 或 `.pending`，在 matching operation lock 内再次确认仍为空后才删除，并同步其父级状态；重读发现任一成员、journal、pending 或关系变化时改按当前事实重新分类。
- [x] 非空但无 journal 的目录、未知成员、非法节点类型、符号链接和目录身份不一致均保留现场并返回稳定诊断，不被解释为可清理 residue。
- [x] checker 与恢复使用同一 allocation residue 判定：合法空 residue 不永久产生 journal 损坏诊断，非空无 journal 仍报告对应失败分类，二者不会因入口不同而得出相反结论。
- [x] residue 清理不会删除其他 operation 的材料、未知内部对象或活动 writer 的现场；锁竞争时不发生目录删除。
- [x] 集成测试覆盖空目录一次确认、确认前新增成员、确认前产生 journal、非空无 journal、重复恢复以及恢复后 checker 重跑，断言状态和诊断稳定。

## Comments

- 2026-08-26：`recover`、`recover_interrupted_operations` 与 `advance_trash_lifecycle` 共用 matching-lock 内空目录二次确认；成功路径通过已认证 operations FD 执行 `rmdir`、父目录 `fsync` 后释放 lock。集成测试、Ruff、Pyright 与 `git diff --check` 已通过。
