# Draft 生命周期

## 根生命周期

`D-LIFECYCLE` 定义 Draft 根的领域状态和登记边界；marker 的存储键/编码、runtime 的耐久
算法和内部记录不是公共合同。初始化、登记和错误的公共结果投影由 runtime 公共结果 owner
统一定义。

### 输入与输出

- 输入分为经[运行时公共接口](../runtime/interface.md#公开入口与-root)校验的生命周期请求和
  root-bound Runtime 取得的事实。初始化的领域目标是 `candidate` 或 `registered`；可认证的
  Draft marker、descriptor、完整 Check、成员摘要、物理目录和 recovery overlay 均由 Runtime
  读取或推导。
- 初始化成功后产生一个带请求目标状态的可认证 marker；目标状态表示 Draft 根处于
  `candidate` 或 `registered`。marker 的精确键、编码和版本演进属于私有实现，不是调用方协议；
  公开成功结果由 runtime 公共结果 owner 统一投影。
- `adopt` 成功后仅把同一根从 `candidate` 推进为 `registered`。它不改写页面字节，
  不改变 `conclusions.md` 的所有权或语义快照。
- 失败不会产生“半登记成功”的领域输出；调用者只能得到拒绝、可诊断失败，或由
  runtime 指出的 `recovery_required`，而不能把空输出解释为成功。

### 调用方请求与运行时事实

生命周期入口的公共请求形状、根路径解析、CLI 映射、mode 与校验统一由[运行时公共接口](../runtime/interface.md#公开入口与-root)
定义；本页不复制这些规则，只说明通过校验后的领域意图和 Runtime 事实。初始化请求的领域
目标是 `candidate` 或 `registered`；`adopt` 的领域 after-state 是把同一根从 `candidate`
推进为 `registered`，不改写页面字节、结论所有权或语义快照。初始化与 `adopt` 的公共结果、
错误 envelope、退出码和输出通道由[公共结果、错误与可观测性](../runtime/errors-and-observability.md#公共结果错误与可观测性)
定义。

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

### 初始化结果消费

初始化现场、分类优先级、四类运行时失败及其安全详情映射，唯一由[运行时初始化与平台的初始化状态表](../runtime/initialization-and-platform.md#初始化状态表)
和同页的[初始化失败映射](../runtime/initialization-and-platform.md#初始化失败映射)定义。R-INIT
选择成功或失败结果；成功保留其最终根状态，失败不发生生命周期转换，所有副作用与现场保留
按 R-INIT。

### 转换、提交与副作用

| 请求目标 / 根领域状态 | Check / Runtime 事实 | recovery overlay | 请求 | 领域结果与提交点 |
| --- | --- | --- | --- | --- |
| 初始化 | 由 R-INIT 认证并选择结果 | 无 | 初始化 | R-INIT 选择结果；成功保留其最终根状态，失败不发生生命周期转换，所有副作用与现场保留按 R-INIT。 |
| 任一可认证根 | 任意 | 已存在未收敛 recovery overlay | 非初始化登记或业务写 | `runtime.recovery_required`；不发生生命周期转换，不做新的领域写。 |
| 不可认证 marker 或根 | 任意 | 任意 | 初始化以外的动作 | 拒绝；零领域写，不把未知状态当作不存在。 |
| `candidate` / 可认证 candidate marker | 完整且无 error | 无 | `adopt` | 在完整登记计划的原子提交点把 marker 从 `candidate` 改为 `registered`；不改页面字节、结论所有权或快照语义。 |
| `candidate` / 可认证 candidate marker | 完整但有 error、不完整或不可认证 | 无 | `adopt` | 拒绝；零写，不改变 marker、页面或快照所有权。 |
| `registered` / 可认证 registered marker | 任意可认证 Check | 无 | `adopt` | 选择领域 `no_change`；领域 after-state 无变化；保持 `registered`，不写 marker、页面或快照，不产生新 operation，不选择 candidate。公共结果按 R-ERRORS 投影。 |

`adopt` 的完整计划必须在提交前固定 marker 变更和其声明的领域 after-state。页面正文、
页面头部信息、关系和必要导航是否属于某个其他入口的同一逻辑提交，由各入口 owner 定义；
本合同不复制 runtime 的 `manifest`、`progress`、fsync 或恢复记录算法。

### 失败、零写与恢复

- marker、Check 或 recovery 事实不能认证时，在写 marker 前失败，保持零领域写；初始化失败按 R-INIT 选择结果。
- 初始化或 adopt 的 marker/记录/目录耐久失败时，不返回初始化/登记成功；runtime 必须
  保留可诊断现场并以 recovery overlay 阻止后续业务写。本页不把该情形折叠为
  `candidate`、`registered` 或普通失败。
- 重试只能在重新认证根状态、Check 和 overlay 后进行；attempt、摘要、记录字段、比较编码
  和耐久顺序属于 runtime 私有实现，不是本页公共合同。

### 验证

- 验证初始化只消费 R-INIT 的唯一选择结果，不在生命周期重新分类现场、错误详情或清理动作。
- 分别构造 candidate、registered、Check 门禁和 recovery overlay，验证表中每一行都有唯一结果。
- 验证 registered 根上的 `adopt` 选择领域 `no_change`、after-state 无变化且零写，不产生新 operation，并按 R-ERRORS 投影；
  验证 candidate 根上的 `adopt` 仍只在完整无 error Check 后推进到 registered。
- 证明 `adopt` 成功前后页面字节与结论所有权不变；任一拒绝分区不写 marker 或页面。
- 模拟提交后耐久不确定，验证普通入口被阻止且只有显式 recovery 被允许收敛。
- 验证初始化的失败、重试和恢复现场均由 R-INIT 选择并保留；生命周期不调用恢复路径，也不返回
  恢复动作的细分结果。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/migration-evidence.md#迁移单元`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md#52-状态转换`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md#6-初始化`
