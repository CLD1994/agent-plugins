# 16: 验收全部入口和 Draft 内部命名空间边界

**What to build:** 从系统边界验证所有当前 mutation、维护入口和不消费索引的命令遵守同一索引协议，并确保 Draft 内部命名空间只接受规格拥有的制品和节点类型。

**Blocked by:** 12: 闭合 `trash` 和 `restore` 的链接索引生命周期; 13: 接入外部文件变更 notification; 14: 接入 `check` 和 `adopt` 的机会式索引维护; 15: 接入 backlink 查询的索引消费与机会式维护.

**Status:** ready-for-agent

- [ ] create、move、promote、collapse、status set、relation add、relation remove、generate-conclusions、trash 和 restore 的实际 mutation 都产生下一 revision、匹配的 terminal journal 摘要和当前根 backlink。
- [ ] 上述十类入口的 no-op 都不产生 revision；发布失败都形成 rebuild-required，同时保留已经验证的业务结果。
- [ ] notification、普通业务写入、`check`、`query backlinks`、`query page` 和 adopt 预检查按规格路由到正确协调器。
- [ ] `query links`、relations/status/recoverable 查询、dry-run、`init` 和 `recover` 不创建规格禁止的独立维护副作用。
- [ ] Draft 内部根只接受 marker、锁、operations、trash、索引目标和 rebuild flag 等已拥有对象；operation 目录只接受该 operation 已有恢复材料和精确 staging。
- [ ] 未知索引形似文件、随机临时文件和错误节点类型报告稳定内部错误；符号链接按协调恢复 spec 的路径边界分类为 `path.symlink_escape`。checker 与 authority/storage 适配器只验证自身 link-index phase 接入，不复制 R/T 恢复分类。
- [ ] 所有业务入口都经过共同 operation 收敛边界，不存在命令完成后追加的专用索引刷新钩子。
- [ ] warning 机器代码、severity、顺序和去重在写命令、query 与 `check` 投影之间一致；notification 不投影这些 warning。
- [ ] 验收测试的入口清单从用户可见行为独立列出，不复制生产代码的 mutation 判定集合生成预期。
