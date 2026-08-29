# 迁移与原子切换

## 迁移与冻结基线

**输入**：固定基线、来源分类、逐合同 packet、精确 Git diff、消费者核验结果和 signer 批准。

**输出**：一个不可拆分的 A/B/C authority migration series，或阻断切换的 `unresolved` 诊断。

**状态维度**：迁移单元资格、冲突与未决状态、A/B/C commit 角色、series 审核状态、消费者闭合状态、冻结基线。

**不变量**：

- A 只保存可复核的迁移证据；B 写最终字节形状的规格网络；C 只执行 authority cut-over、消费者与 tracker 同步、历史 fallback 退役。A 或 B 单独合入均不产生 authority。
- 只有 signer 批准确切 A/B/C series 且 C 随完整 series 合入权威分支后，新的 current spec tree 才生效。批准后的正文、关系、来源、消费者或删除集合一旦变化，必须重新验证并重新签署。
- `docs/design.md` 由本合同处理：它在 C 中整份退役、从当前树删除且不保留 fallback；历史只通过 Git locator 读取。该责任不属于其他 Contract-ID。
- 任何 `unresolved`、冲突、断链、未闭合消费者或未通过的复审都会阻止 cut-over。

### 迁移状态决策表

| 当前事实 | 下一状态或结果 | 可否写入 authority |
| --- | --- | --- |
| 来源已发现，角色和 scope 可判定为 eligible / scoped eligible | `classified → extracted → reconciled` | 否，继续形成 packet。 |
| 来源是 issue、plan、实现、测试、review、map 或不具资格的 Draft | `classified → evidence-only` | 否，只保留证据用途。 |
| 来源角色、locator、取代范围或重叠冲突未定义 | `unresolved`，记录具体缺口 | 否，阻止签署和 C。 |
| 全部单元可重放、Target/消费者/locator 已核验 | `drafted → verified → signed` | 仅可形成待签 series。 |
| A、B、C 均为同一已批准 series，且 C 合入权威分支 | `cut-over` | 是。 |
| series 被拆分、squash、rebase，或 B 内容在批准后变更 | `unresolved` | 否，必须从冻结基线重新生成。 |

### 审计修复与新冻结基线

`fc11168a30bbc52645cc3b2c4738fd95f26433ba` 与 `c26ff6df90f5a28d1353e9ffaae5cb20bb264fac` 位于 A/B/C authority migration series 之外。它们只有在**独立复审通过**后才可先合并，并据此形成新的冻结基线；随后必须从该基线重新生成并整体签署新的 A/B/C series。新 series 不得拆分、squash 或 rebase。

当前复审结论仍为 `FAIL / cut-over blocked`，因此不能先行形成可切换基线，也不能把旧 C 或上述审计修复解释为新 series 的 C。此规则来自 `accepted-Q23`（本轮过程输入），与 signed-Q23（首次迁移验证范围）是不同编号命名空间。

**副作用**：C 同步根路由、插件入口、已知消费者和 tracker，并完成 `docs/design.md` 的退役；A/B/C 之外的审计修复不改变 authority，直到满足前述独立复审与重生条件。

**失败与恢复**：不能证明 source eligibility、packet、series、消费者、Target 或冻结基线时保留 `unresolved` 和诊断，不以旧 C、历史规格、实现或测试作为恢复路径。重新生成并重新签署是唯一继续路径。

**验证**：逐项检查状态表分区；验证 A/B/C 的 parent 和内容未被改写；核对 `docs/design.md` 只由本合同退役；确认所有 locator 可由 Git 解析，且当前结论仍为 `FAIL / cut-over blocked` 时 C 不可发生。

## 相关规格

- [来源资格与唯一权威](authority.md#来源资格与唯一权威)
- [Feature spec 生命周期](feature-specs.md#feature-spec-生命周期)
- [行为合同登记](contract-register.md)

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`（signed-Q3、signed-Q4、signed-Q11、signed-Q13、signed-Q14、signed-Q16、signed-Q21、signed-Q22）
- `80508340458f393435ae67669656141ec411ba1b:docs/design.md#Draft 正式设计`（signed-Q11、signed-Q14 的精确退役对象）
