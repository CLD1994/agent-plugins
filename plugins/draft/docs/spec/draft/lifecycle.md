# Draft 生命周期

## 根生命周期

`D-LIFECYCLE` 定义 Draft 根的领域状态和登记边界；它不定义 marker 的
字段 ABI、runtime 的耐久算法或公开错误 envelope。

### 输入与输出

- 输入是根路径、请求的初始化目标状态（`candidate` 或 `registered`）、可认证的
  Draft marker、请求的动作（初始化或 `adopt`）、完整 Check 的结果，以及 runtime
  报告的 recovery overlay。
- 初始化成功后产生一个带请求目标状态的可认证 marker；`init --candidate` 请求
  `candidate`，普通 `init` 请求 `registered`。marker 的精确键、编码和版本演进尚未
  获批，属于 `unresolved`，不得由本页推断。
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
| 根状态 | `candidate`、`registered` | 初始化只发布请求的目标状态；`adopt` 只允许 `candidate -> registered`，不得把 `registered` 退回 `candidate`。 |
| Check | 完整且无 error、完整但有 error、不完整或不可认证 | 只有第一类可满足 `adopt` 的 Check 前置条件。 |
| recovery overlay | 无、pending | pending 不改变根状态值，但阻止登记和业务写。 |

Draft 是讨论空间；即使页面成为 `confirmed`、根成为 `registered`，都不把讨论内容
提升为插件正式规格。正式规格的成员资格和批准另由治理合同处理。

### 转换、提交与副作用

| 请求目标 / marker | Check / 初始化现场 | recovery overlay | 请求 | 领域结果与提交点 |
| --- | --- | --- | --- | --- |
| `candidate` 或 `registered` / 不存在 | 严格允许的空 target、空 `.draft` 或仅空 `.draft/runtime` | 无 | 初始化 | 按请求目标状态完整创建并耐久发布 marker；失败前不留下可用根。 |
| `candidate` 或 `registered` / 不存在 | 有初始化现场，descriptor 与请求完全相同且已有成员摘要匹配 | 无 | 初始化 | 只续建缺失的精确成员，按请求目标状态发布 marker；不把现场当作新根或自动切换状态。 |
| `candidate` 或 `registered` / 不存在 | 初始化现场 descriptor 不同、未知、损坏或发生外部变化 | 无 | 初始化 | `runtime.init_conflict` 或 `runtime.corrupt`；零领域写，保留可诊断现场。 |
| `candidate` 或 `registered` / 可认证 marker | marker 状态与请求目标相同，runtime/descriptor 匹配且 cleanup 已收敛 | 无 | 初始化 | 返回该目标状态的已初始化领域结果，零写；公开结果字段仍按 runtime ABI。 |
| `candidate` 或 `registered` / 可认证 marker | marker 状态与请求目标相同，runtime/descriptor 匹配但 cleanup artifacts 仍存在 | 无 | 初始化 | `runtime.recovery_required`；零写，保持当前根状态；只有显式 `recover()` 按清单收敛。 |
| `candidate` 或 `registered` / 可认证 marker | marker 状态与请求目标不同 | 无 | 初始化 | `runtime.init_conflict`；零写，不把初始化转换为 adopt。 |
| `candidate` 或 `registered` / 可认证 marker | schema 旧/未知、marker/runtime/descriptor 不匹配或节点异常 | 无 | 初始化 | 对应 runtime 错误（schema unsupported 或 corrupt）；零写并保留现场。 |
| 任一可认证根 | 任意 | pending | 初始化或业务写 | `runtime.recovery_required` 或对应 recovery 阻塞；不做新的领域写，根状态保持 overlay 之前的值。 |
| 不可认证 marker 或根 | 任意 | 任意 | 初始化以外的动作 | 拒绝；零领域写，不把未知状态当作不存在。 |
| `candidate` / 可认证 candidate marker | 完整且无 error | 无 | `adopt` | 在完整登记计划的原子提交点把 marker 从 `candidate` 改为 `registered`；不改页面字节、结论所有权或快照语义。 |
| `candidate` / 可认证 candidate marker | 完整但有 error、不完整或不可认证 | 无 | `adopt` | 拒绝；零写，不改变 marker、页面或快照所有权。 |
| `registered` / 可认证 registered marker | 任意可认证 Check | 无 | `adopt` | 返回 `already_registered` 领域结果；保持 `registered`，不写 marker、页面或快照，不选择 candidate。公开 code/字段编码仍按 runtime ABI。 |
| `candidate` 或 `registered` / 可认证 marker | 任意 | pending | `adopt` 或业务写 | 返回 recovery 阻塞；不做新的领域写。 |

`adopt` 的完整计划必须在提交前固定 marker 变更和其声明的领域 after-state。页面、
机器区、关系和必要导航是否属于某个其他入口的同一逻辑提交，由各入口 owner 定义；
本合同不复制 runtime 的 `manifest`、`progress`、fsync 或恢复记录算法。

### 失败、零写与恢复

- marker、Check、初始化现场或 recovery 事实不能认证时，在写 marker 前失败，保持零领域写。
- 初始化或 adopt 的 marker/记录/目录耐久失败时，不返回初始化/登记成功；runtime 必须
  保留可诊断现场并以 recovery overlay 阻止后续业务写。本页不把该情形折叠为
  `candidate`、`registered` 或普通失败。
- 重试只能在重新认证根状态、Check 和 overlay 后进行。相同请求的 operation identity
  与 attempt 规则属于 runtime；未获批的字段和比较方法为 `unresolved`。

### 验证

- 分别构造 candidate/registered 初始化目标与 marker、严格空现场、匹配/冲突初始化现场、
  不可认证 marker、candidate、registered 与 recovery pending 根，验证表中每一行都有唯一结果。
- 验证 registered 根上的 `adopt` 返回既有 registered 领域结果且零写；验证 candidate
  根上的 `adopt` 仍只在完整无 error Check 后推进到 registered。
- 证明 `adopt` 成功前后页面字节与结论所有权不变；任一拒绝分区不写 marker 或页面。
- 模拟提交后耐久不确定，验证普通入口被阻止且只有显式 recovery 被允许收敛。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md#52-状态转换`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md#6-初始化`
