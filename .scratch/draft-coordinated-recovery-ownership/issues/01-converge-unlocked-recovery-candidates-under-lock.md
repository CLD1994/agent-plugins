# 01: 在锁内收敛通用未持锁恢复候选

**What to build:** 将未持锁候选扫描限定为只读发现和分类，并为所有会改变恢复现场的通用恢复动作建立 matching operation lock 内的重读、重分类和收敛边界。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [x] 未持锁阶段不发布 `.pending`、不写入或删除 journal、不清理恢复副本或目录、不执行 handoff，也不改变活动写者状态；输出仅为保留、失败或待锁内重读的分类。
- [x] 所有 pending 发布、journal 写删、copies 清理、operation 目录删除、package 清理和交接终态动作，均在取得 matching operation lock 后重读当前目录成员、journal、pending 与授权关系，再按当前事实决定动作。
- [x] 普通 allocating 在锁内先耐久转为 rolled_back，再只清理当前 journal 授权材料；已提交终态只执行当前 journal 明确授权的幂等清理，不重复外部 handoff。
- [x] 锁竞争时丢弃整个未持锁扫描快照，返回稳定的 `operation.recovery_required` 恢复未决错误，并证明活动 lock、writer journal、恢复材料、目录及关系不变。
- [ ] 锁内重读遇到未知、损坏、非法节点、身份不一致或无法解释关系时保留全部现场并返回稳定错误，不以空值、跳过或递归清理代替分类。
- [ ] 分类严格按协调恢复 spec 的优先级执行：raw JSON/v4 字段类型或任一 metadata key/value 非 string（含 null/number/list）为 `operation.journal_corrupt`，符号链接为 `path.symlink_escape`；不要让后续关系检查覆盖这些错误。
- [ ] `operation.recovery_required` 只用于锁竞争或合法候选已由其他 owner 持有；journal、计划、图关系或 phase 损坏必须返回对应稳定错误并失败关闭。
- [x] 缺少或含非法 `restores_trash_operation` 的 `restore` journal 在无锁 discovery、已有 matching lock 与锁内重读均返回 `operation.plan_invalid`；不得降格为通用 `allocating`、空 residue 或从 T→R 反推关系，且必须保留 lock（若已有）、journal、copies 与目录。
- [x] 通过三个公开恢复入口的集成测试证明扫描后发生 lock 竞争、pending 出现、普通 allocating、终态残留和重复恢复时均无越权副作用；重复执行不扩大清理或 handoff。
- [x] 锁内 `allocating` 在写入 `rolled_back` 前以 no-follow 重新验证 operation 成员、`originals`/`staged` 类型、登记成员及摘要授权；symlink、未知或未授权成员失败时保留 journal、lock、材料和外部目标。
