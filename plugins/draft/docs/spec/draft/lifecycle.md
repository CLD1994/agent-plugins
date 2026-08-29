# Draft 生命周期

## 根生命周期

`D-LIFECYCLE` 定义 Draft 根的领域状态和登记边界；它不定义 marker 的
字段 ABI、runtime 的耐久算法或公开错误 envelope。

### 输入与输出

- 输入是根路径、可认证的 Draft marker、请求的动作（初始化或 `adopt`）、完整
  Check 的结果，以及 runtime 报告的 recovery overlay。
- 初始化成功后产生一个带 `candidate` 根状态的可认证 marker。marker 的精确键、
  编码和版本演进尚未获批，属于 `unresolved`，不得由本页推断。
- `adopt` 成功后仅把同一根从 `candidate` 推进为 `registered`。它不改写页面字节，
  不改变 `conclusions.md` 的所有权或语义快照。
- 失败不会产生“半登记成功”的领域输出；调用者只能得到拒绝、可诊断失败，或由
  runtime 指出的 `recovery_required`，而不能把空输出解释为成功。

### 状态维度与不变量

根状态只有 `candidate` 和 `registered`。`recovery pending` 是覆盖层，不是第三种根
状态；覆盖层存在时登记和业务写入均被阻止，只有显式 `recover()` 可以收敛它。

| 维度 | 合法值 | 不变量 |
| --- | --- | --- |
| marker | 不存在、可认证 candidate、可认证 registered、不可认证 | 只有可认证 marker 才能授权根状态判断。 |
| 根状态 | `candidate`、`registered` | `adopt` 不得把 `registered` 退回 `candidate`。 |
| Check | 完整且无 error、完整但有 error、不完整或不可认证 | 只有第一类可满足 `adopt` 的 Check 前置条件。 |
| recovery overlay | 无、pending | pending 不改变根状态值，但阻止登记和业务写。 |

Draft 是讨论空间；即使页面成为 `confirmed`、根成为 `registered`，都不把讨论内容
提升为插件正式规格。正式规格的成员资格和批准另由治理合同处理。

### 转换、提交与副作用

| marker / 根状态 | Check | recovery overlay | 请求 | 结果与提交点 |
| --- | --- | --- | --- | --- |
| 不存在 | 不适用 | 无 | 初始化 | 在 marker 可认证写入的提交点创建 `candidate` 根；失败前不留下可用根。 |
| 不可认证 | 任意 | 任意 | 初始化以外的动作 | 拒绝；零领域写。 |
| 可认证 `candidate` | 完整且无 error | 无 | `adopt` | 先形成完整登记计划；在原子领域提交点把 marker 状态改为 `registered`。 |
| 可认证 `candidate` | 完整但有 error | 无 | `adopt` | 拒绝；不改 marker、页面或快照所有权。 |
| 可认证 `candidate` | 不完整或不可认证 | 无 | `adopt` | 拒绝；零写。 |
| 可认证 `candidate` | 任意 | pending | `adopt` 或业务写 | 返回恢复阻塞；不做新的领域写。 |
| 可认证 `registered` | 任意 | 无 | `adopt` | 不是允许的状态转换；幂等公开结果尚未定义，故返回语义为 `unresolved`，不得把它当作成功。 |
| 可认证 `registered` | 任意 | pending | 任意业务写 | 返回恢复阻塞；不做新的领域写。 |

`adopt` 的完整计划必须在提交前固定 marker 变更和其声明的领域 after-state。页面、
机器区、关系和必要导航是否属于某个其他入口的同一逻辑提交，由各入口 owner 定义；
本合同不复制 runtime 的 `manifest`、`progress`、fsync 或恢复记录算法。

### 失败、零写与恢复

- marker、Check 或 recovery 事实不能认证时，在写 marker 前失败，保持零领域写。
- 提交后无法证明耐久结果时，不返回登记成功；runtime 必须保留可诊断现场并以
  recovery overlay 阻止后续业务写。本页不把该情形折叠为 `candidate`、`registered`
  或普通失败。
- 重试只能在重新认证根状态、Check 和 overlay 后进行。相同请求的 operation identity
  与 attempt 规则属于 runtime；未获批的字段和比较方法为 `unresolved`。

### 验证

- 分别构造无 marker、不可认证 marker、candidate、registered 与 recovery pending 根，
  验证表中每一行都有唯一结果。
- 证明 `adopt` 成功前后页面字节与结论所有权不变；任一拒绝分区不写 marker 或页面。
- 模拟提交后耐久不确定，验证普通入口被阻止且只有显式 recovery 被允许收敛。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
