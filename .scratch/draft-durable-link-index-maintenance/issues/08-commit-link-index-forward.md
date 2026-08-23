# 08: 实现已持有 operation 的索引前向提交内核

**What to build:** 为已经持有写锁和 operation journal 的调用方实现链接索引前向提交，将准备结果耐久收敛为 `published` 或 `rebuild_required`，但不决定调用方的原业务终态或锁生命周期。

**Blocked by:** 07: 实现无副作用的索引候选准备服务.

**Status:** ready-for-agent

- [ ] 在产生 staging、目标替换或 flag 副作用前，先耐久写入 `link_index_updating` 和本 operation 使用的下一 revision。
- [ ] 可发布候选严格按 staging、目标发布、journal `link_index_published`、尽力删除 rebuild flag 的顺序提交，journal 摘要与最终制品 bytes 一致。
- [ ] 要求重建的准备结果或发布结果未知时耐久写入 `link_index_rebuild_required`，再尽力创建 rebuild flag；不得猜测发布成功或重复增量发布。
- [ ] 索引构建或发布失败不会把已经验证的业务 mutation 转入 rollback；只要 rebuild-required 已耐久记录，调用方可以继续其原有成功终态。
- [ ] journal 结果无法耐久写入时返回 `operation.durability_failed` 所需的分类结果，并保留锁、journal、staging 和诊断现场。
- [ ] 普通降级 warning 只使用 `link_index.rebuild_required` 和 `link_index.rebuild_flag_update_failed`，每个代码最多出现一次，不携带 operation ID、内部路径或底层异常文本。
- [ ] 提交内核不释放调用方写锁、不清理不属于本 operation 的材料，也不自行写入 `completed`、`trash_retained` 或其他原业务终态。
- [ ] 测试覆盖 publish、rebuild-required、flag 创建或删除失败、journal 耐久失败、异常内部节点以及 updating 后禁止 rollback。
