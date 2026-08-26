# 01: 在锁内收敛通用未持锁恢复候选

**What to build:** 将未持锁候选扫描限定为只读发现和分类，并以 Issue 03 的全图关系事实为输入，为整个 participant closure 建立 matching operation lock 内的重读、材料 preflight 和副作用门。

**Blocked by:** 03: 将 R/T 分类和 Trash 恢复接入锁内所有权。

**Status:** ready-for-agent

- [x] 未持锁阶段不发布 `.pending`、不写入或删除 journal、不清理恢复副本或目录、不执行 handoff，也不改变活动写者状态；输出仅为保留、失败或待锁内重读的分类。
- [x] 所有 pending 发布、journal 写删、copies 清理、operation 目录删除、package 清理和交接终态动作，均在取得 matching operation lock 后重读当前目录成员、journal、pending 与授权关系，再按当前事实决定动作。
- [x] 普通 allocating 在锁内先耐久转为 rolled_back，再只清理当前 journal 授权材料；已提交终态只执行当前 journal 明确授权的幂等清理，不重复外部 handoff。
- [x] 锁竞争时丢弃整个未持锁扫描快照，返回稳定的 `operation.recovery_required` 恢复未决错误，并证明活动 lock、writer journal、恢复材料、目录及关系不变。
- [ ] 锁内只消费 Issue 03 当前全图分类产生的 owner、participant closure 和 action；所有 participant 的 current/pending、材料授权投影、精确成员、节点类型、no-follow 身份与摘要在任一副作用前通过同一只读 preflight。
- [ ] `originals`、`staged`、mutation/link-index staging、Trash `attempt`、cleanup evidence 与 authoritative package 均纳入 action 对应的材料闭包；current/pending 不取授权 union，任一 participant 不安全时整组失败关闭。
- [ ] preflight 失败时保留所有 participant 的 journal/pending、材料、package、关系与 matching lock；不发布 pending、不写 journal、不清理、不 handoff、不解关联、不删除目录或释放 lock，并返回 Issue 03 分类或材料验证对应的稳定错误。
- [x] 缺少或含非法 `restores_trash_operation` 的 `restore` journal 在无锁 discovery、已有 matching lock 与锁内重读均返回 `operation.plan_invalid`；不得降格为通用 `allocating`、空 residue 或从 T→R 反推关系，且必须保留 lock（若已有）、journal、copies 与目录。
- [x] 通过三个公开恢复入口的集成测试证明扫描后发生 lock 竞争、pending 出现、普通 allocating、终态残留和重复恢复时均无越权副作用；重复执行不扩大清理或 handoff。
- [x] 锁内 `allocating` 在写入 `rolled_back` 前以 no-follow 重新验证 operation 成员、`originals`/`staged` 类型、登记成员及摘要授权；symlink、未知或未授权成员失败时保留 journal、lock、材料和外部目标。
