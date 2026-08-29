# 运行时公共接口

## 运行时接口

本合同定义调用方可见的 runtime 边界。`Lifecycle` 与 root-bound
`OperationRuntime` 是行为接口；`RuntimeFactory` 仅是 CLI 取得后者的受限构造
seam，不构成第三个业务接口，也不返回或承诺 `Lifecycle`。

### 输入与公开输出

```text
Lifecycle.initialize(root: AbsoluteRoot, root_state: RootState) -> InitOutcome

RuntimeFactory.for_root(root: AbsoluteRoot) -> root-bound OperationRuntime

OperationRuntime
  .perform(request: OrdinaryRequest, mode: Execute | DryRun) -> Outcome
  .trash(request: TrashRequest, mode: Execute | DryRun) -> Outcome
  .restore(request: RestoreRequest) -> Outcome
  .read(request: ReadRequest) -> ReadOutcome
  .maintain(ExternalFilesChanged) -> MaintenanceOutcome
  .recover() -> RecoveryOutcome
```

`Lifecycle.initialize` 接收显式的规范绝对 root 及其 `RootState`，直接执行；CLI
的 `init` 只规范化该显式 root 后调用它，不经 factory、不读取 cwd 或环境来选择
root，也不要求既有 marker。

除 `init` 外，CLI 以 `--root > DRAFT_ROOT > physical cwd ancestor` 的优先级选择
root。最高优先级候选无效即失败，不得回退到较低优先级。CLI 将已封闭的 request
交给 `RuntimeFactory.for_root` 返回的实例；它不得传入、读取或替换
`RuntimeDeps`、`PlatformAdapter`、`HandoffAdapter`、worker、Clock、ID source、
planner、运行期锁、recovery 或 index 协作者。

`OrdinaryRequest` 是 `create`、`move`、`promote`、`collapse`、`status.set`、
`relation.add`、`relation.remove`、`generate-conclusions` 与 `adopt` 的封闭值联合。
调用方不能把函数、计划、snapshot 或 capability 作为 request 输入。公开输出只给
调用方判断或继续操作所需的结果；其字段边界见
[公共结果与错误投影](errors-and-observability.md#公共结果与错误投影)。

`relation.add/remove` 的 `CODE` 只允许主动方向 `depends_on`、`supersedes`、`challenges`。
`parent` 由结构 mapping owner 随路径、物理父和导航维护；`superseded_by` 与
`challenged_by` 由对应主动方向的完整计划维护，均不是关系命令参数。`supersedes` 的
状态 after-state、最后一条关系的恢复状态和反向行缺失分区由 [页面状态与关系](../draft/status-and-relations.md#页面状态与关系) 与 [页面编辑](../draft/page-edits.md#relation-addremove-的关系与状态分区) 定义。

### 接口状态维度与模式表

| 调用维度 | 合法值或事实 | 对结果的影响 |
| --- | --- | --- |
| 入口 | `initialize`、六个 `OperationRuntime` 方法 | 决定输入类型、返回类型与是否可能产生 root 副作用。 |
| root 绑定 | 显式 init root；或按 CLI 优先级取得的受管理 root | root 无效时失败，不得切换到其他 root。 |
| request 形状 | 封闭联合中的合法成员；或非法/未知成员 | 非法成员为 `runtime.request_invalid`，不进入计划或副作用。 |
| mode | `Execute`、`DryRun`、或该入口不支持的 mode | 只有下表列出的入口可用 `DryRun`；不支持的组合不被解释为 `Execute`。 |
| runtime schema | 已认证的 v1；旧、未知或无法认证 | 后三者失败关闭，不迁移、不双读、不自动清理。 |

| 入口与请求 | `Execute` | `DryRun` | 公开结果 |
| --- | --- | --- | --- |
| `Lifecycle.initialize` | 允许 | 不支持 | `InitOutcome` |
| `perform(create|move|promote|collapse|generate-conclusions|status.set|relation.add|relation.remove)` | 允许 | 允许 | `Outcome` |
| `perform(adopt)` | 允许 | 不支持 | `Outcome` |
| `trash` | 允许 | 允许 | `Outcome` |
| `restore` | 允许 | 不支持 | `Outcome` |
| `read` | 不适用 | 不适用 | `ReadOutcome` |
| `maintain(ExternalFilesChanged)` | 不适用 | 不适用 | `MaintenanceOutcome` |
| `recover()` | 不适用 | 不适用 | `RecoveryOutcome` |

### 不变量、转换与逻辑提交

- `OperationRuntime` 是单一 root 的实例。构造本身不做 I/O，也不缓存跨调用的
  snapshot 或 capability 结论；每次调用按当时已认证事实重新准入。
- `RuntimeFactory` 的内部装配与 `Lifecycle.initialize` 的内部装配可共享私有
  composer，但不得把装配对象扩张为公开接口。`HandoffAdapter` 是唯一外部
  seam。
- 一个逻辑操作使用一个公开且稳定的 `operation_id`，关联它的 `manifest`、
  `progress`、content、catalog/head 与 `Outcome`。同一请求意图和 authority
  snapshot 的重试复用该 `operation_id` 并新增 attempt；任一者改变则建立新操作，
  并显式关联旧操作。`attempt_id` 与 phase 可以留在诊断或耐久记录中，不能成为
  第二个公开逻辑身份。
- 写入口的逻辑提交不是某一次物理写。只有该入口所需的完整 authority 已按批准
  顺序耐久、matching progress 已记录、不可延期 cleanup 已完成，且 projection
  已耐久或安全分类后，才可把操作投影为成功。具体物理顺序由
  [Authority 提交与派生投影](authority-and-projections.md#authority-提交与派生投影)
  定义。

### 副作用、失败、重试与恢复

`DryRun` 与 `Execute` 使用同一 request 形状和事实准入；无已发现运行期记录且
preflight 成功时，`DryRun` 只返回该稳定时点的计划/结果，不取锁、不发布记录、
不推进自动生命周期、不维护索引、不创建 operation、epoch 或 artifact。它不授权
后续 `Execute`。已发现记录时只可执行一次 non-blocking probe；其结果仍由准入
合同分类，不能借 probe 产生持久写。

调用中的 schema、root、请求或 authority 事实无法认证时，接口返回相应机器错误，
不把未知状态降级为空结果或成功。普通入口不会隐式调用 `recover()`；有效但未完成
的现场必须保留并按 `runtime.recovery_required` 阻塞，只有显式 `recover()` 可收敛。
notification 只能调用 `maintain(ExternalFilesChanged)`，不得转调 `read`、自动
lifecycle 或 `recover()`。

### 可证伪验证

- 对每个公开签名分别验证接受的 request/mode 组合与拒绝组合，特别是 `adopt`、
  `restore`、`recover` 和 notification 不支持 `DryRun`。
- 验证 `init` 直达 `Lifecycle.initialize`，其余受管理命令只经
  `RuntimeFactory.for_root`；CLI/export graph 不暴露内部 deps 或 `Lifecycle` factory
  承诺。
- 在相同 intent/authority snapshot 与变化后的任一事实各执行一次重试，核对稳定
  `operation_id` 的复用/新建语义，而不从目录名或时间戳推断 identity。
- 验证构造无 I/O、跨调用不复用 snapshot，及旧/未知 schema 在产生副作用前失败。

### `unresolved` 的公开 ABI

下列字段或比较规则没有来自已接受或已签来源的精确名称、编码或顺序，不能由实现、测试或
本页自行补造：

| 项目 | 状态 | 阻断原因 |
| --- | --- | --- |
| `operation_id` 的生成来源与编码 | `unresolved` | 已接受行为只要求稳定公开 identity，未给出生成器或编码 ABI。 |
| “intent 与 authority snapshot 未变”的比较字段 | `unresolved` | 已接受重试规则未列出完整比较集合。 |
| 新操作关联旧操作的公开字段名与顺序 | `unresolved` | 已接受行为要求显式关联，未指定字段 ABI。 |
| `InitOutcome`、`Outcome`、`ReadOutcome`、`MaintenanceOutcome` 的完整字段顺序 | `unresolved` | 只有部分结果语义已接受或已签；统一 envelope ABI 尚未签署。 |

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#2-对外接口与所有权`
