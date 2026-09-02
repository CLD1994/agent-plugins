# 迁移与原子切换

**输入**：固定比较点、完整候选规格树、来源分类、逐合同 packet、精确 Git diff、消费者与正式链接核验、独立评审结果、signer 批准，以及选定的集成方式。

**输出**：作为一个不可拆分内容变更进入权威分支的完整规格树，或阻止该变更的 `unresolved` 诊断。

**状态维度**：候选内容是否完整；来源、Target 与 supersedes scope 是否可认证；消费者与链接是否闭合；独立评审和 signer 批准是否覆盖当前树；候选是否已进入权威分支。

**不变量**：

- 候选必须把所有属于本次批准范围的 owner、关系、来源、消费者和历史页退役一起呈现；任何子集单独进入权威分支都不产生新的 authority。
- 只有来源、Target、精确 supersedes scope、消费者、正式链接和独立评审均已核验，且 signer 批准该完整候选后，才可合入权威分支。
- 历史页面只通过 Git locator 提供 provenance。已批准退役的旧页面从当前树移除且不保留 fallback；不能用旧正文、实现或 tracker 补回当前规则。
- Git 的 merge、线性搬运和最终单提交都是可选集成形态；选择应符合本次交付目标，不能把固定提交数量或父子图形当成内容正确性的唯一证明。
- 若选择最终单提交，它的父提交必须是固定比较点，并且该提交的文件树等于固定比较点之后完整已审候选树；暂存文件集合必须等于已批准范围。
- 批准后正文、关系、来源、消费者、链接、退役集合或最终文件树一旦变化，受影响范围必须重新核验并重新批准。

### 迁移状态决策表

| 当前事实 | 下一状态或结果 | 可否写入 authority |
| --- | --- | --- |
| 来源已发现，角色和 scope 可判定为 eligible / scoped eligible | `classified → extracted → reconciled` | 否，继续形成完整候选。 |
| 来源是 issue、plan、实现、测试、review、map 或不具资格的 Draft | `classified → evidence-only` | 否，只保留证据用途。 |
| 来源角色、locator、取代范围、消费者、链接或重叠冲突未定义 | `unresolved`，记录具体缺口 | 否，阻止批准和权威分支合入。 |
| 完整候选的 Target、消费者、locator 与正式链接已核验，且独立评审通过 | `drafted → verified → approved` | 仅可形成待合入的内容变更。 |
| 已批准的完整候选按选定方式进入权威分支，最终文件树未变化 | `authority-integrated` | 是。 |
| 评审后候选树发生变化，或集成时出现冲突、额外文件或最终文件树不等 | `reverify-required` | 否，先定位并重新核验受影响范围。 |

### 集成与失败关闭

集成前先说明希望保留的历史形态，再选择工具。merge 保留已审来源的祖先关系；线性搬运保留逐份来源顺序；最终单提交把固定比较点后的完整已审树作为一个提交。三种方式都必须证明文件范围与已批准范围一致，且不得在集成过程中改写语义、解决未批准冲突或掺入额外文件。

任何 `unresolved`、来源冲突、断裂 Target、断链、未闭合消费者、未通过独立评审、未获批准或最终树不一致都会关闭本次 authority 合入。保留可诊断证据，修复后从受影响的分类、核验和评审步骤继续；不得以旧版本、部分提交、实现或测试作为恢复路径。

**验证**：逐项检查状态表分区；比较固定比较点与完整候选树；核对所有 locator、Target、supersedes scope、相对链接和消费者；确认独立评审与 signer 批准覆盖当前树；按所选集成方式验证祖先/补丁或最终单提交文件树。

## 相关规格

- [来源资格与唯一权威](authority.md#来源资格与唯一权威)
- [Feature spec 生命周期](feature-specs.md#feature-spec-生命周期)
- [行为合同登记](contract-register.md)

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`（signed-Q3、signed-Q4、signed-Q11、signed-Q13、signed-Q14、signed-Q16、signed-Q21、signed-Q22）
- `80508340458f393435ae67669656141ec411ba1b:docs/design.md#draft-正式设计`（已批准退役的历史页面）
