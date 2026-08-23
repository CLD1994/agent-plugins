# 12: 闭合 `trash` 和 `restore` 的链接索引生命周期

**What to build:** 将通用索引收敛和恢复协议接入 trash/restore 的特殊终态及双 journal 协调，使索引阶段不改变页面回收生命周期和锁所有权。

**Blocked by:** 11: 把索引收敛接入通用业务 operation 协调器.

**Status:** ready-for-agent

- [ ] trash 的业务 mutation 验证后先耐久形成索引结果，再进入 `trash_retained`；索引发布后不再发生未记录的根目录变化。
- [ ] restore 的业务 mutation 验证后先耐久形成索引结果，再进入 restore `completed`，随后才执行与 trash journal 的既有解关联和清理。
- [ ] 双 journal 协调调用恢复解释器时可以要求终态后继续持有锁，避免释放后重新取得锁造成所有权窗口。
- [ ] trash/restore 在 updating、published 和 rebuild-required 中断后都恢复原 operation，不创建独立 revision 或 maintenance operation。
- [ ] 链接索引 warning 与既有回收生命周期 warning 分开保存和渲染，不混入 handoff operation ID 或内部恢复细节。
- [ ] 重复 recovery 保持业务文件、回收包、两个 journal、revision、索引、flag、锁和 staging 一致。
- [ ] 集成测试覆盖 trash 保留态、restore 正常完成、restore 双 journal 清理中断以及索引降级后的既有回收语义。
