# 运行时初始化与平台

## 初始化与平台前置

本合同规定 `Lifecycle.initialize` 的输入、可认证初始现场、平台能力与 marker
发布前后的唯一状态转换。持久 init artifact 只能位于目标 root 内；它不定义 Draft
页面、关系或 marker 的领域语义。

### 输入与公开输出

输入为显式 `AbsoluteRoot` 与 `RootState`，以及该 root 上可认证的 target tree、
`.draft`、runtime 现场、marker 与 `InitDescriptor`。输出为 `InitOutcome` 或机器
错误；成功不暴露内部路径或中间初始化状态。

在任何 target write 前，平台必须证明当前本地卷支持逐节点 no-follow、create-only/
no-replace 原子发布、live OS lock、文件和目录 durability，以及可终止隔离调用的
hard timeout。无法证明任一性质时，返回 `runtime.platform_unsupported`，零写。
POSIX 实现细节不是跨平台协议；Windows、macOS、Linux 分别由 `PlatformAdapter`
满足同一可观察性质。

### 权威状态与不变量

`InitDescriptor` 是 runtime 内唯一完整的初始化 authority。它的规范编码涵盖 marker
预期字节、初始内容、v1 布局、允许节点类型和精确 cleanup artifacts；marker 不含
`init_id`，也不重复 descriptor。未知字段/tag/member、非 regular 节点、符号链接、
摘要或 hash 链错误均为 `runtime.corrupt`。

```text
.draft/draft.json
.draft/runtime/descriptor.json
.draft/runtime/operations/<id>/{manifest.json,progress/<sequence>.json,artifacts/...}
.draft/runtime/lifecycle/{transitions/<generation>.json,active.json,schedule.json}
.draft/runtime/index/{epochs/<sequence>.json,head.json,cache.json}
.draft/runtime/trash/<id>/...
```

immutable record 必须 canonical encoding、create-only 发布、file fsync 与父目录
fsync。`active.json` 和 `index/head.json` 是可原子替换的 authority；
`schedule.json` 与 `cache.json` 是可重建 projection，绝不成为 authority。

`replace_exact_regular(expected_target, staged_source)` 只接受同目录、已认证的
canonical regular staged source，和 identity/digest 均等于 `expected_target` 的
canonical regular target。它逐节点 no-follow、原子可见；错误或不确定时重读 target，
并耐久 staged file 和 authority 父目录。缺失、符号链接、目录、非规范 target 或
identity/digest 变化不属于成功变体：stage 后变化为 `runtime.external_change`，稳定
类型/编码不合法为 `runtime.corrupt`，均零替换。预期缺失的 projection 必须使用
已认证的 create-only publish，而非该 replace 操作。

### 初始化状态机

初始化先取得目标 root 外的临时 OS init gate，并持有到 marker durable。它只接受
严格空 target、空 `.draft` 或仅空 `.draft/runtime`；其他初始树失败关闭。gate 内按
descriptor 创建全部 runtime v1、初始内容和 marker，逐项耐久，最后才发布 marker。
`initialize` 的 `RootState` 是请求的目标状态，只允许 `candidate` 或 `registered`；它
不会从目标内容、已有 marker 或初始化现场推断另一状态。下表按请求目标、marker、现场和
descriptor 的完整组合分类；每次调用命中且只命中一行。

| 请求目标状态 | marker | target/runtime 现场 | descriptor 认证 | `initialize` 的唯一动作与公开结果 | 恢复入口 |
| --- | --- | --- | --- | --- | --- |
| `candidate` 或 `registered` | 不存在 | 严格允许的空形态 | 为本次请求目标的新 descriptor | 持 gate 完整创建、耐久并发布请求目标状态的 marker；随后进入本页 cleanup 路径 | 不处理无 marker 的空现场 |
| `candidate` 或 `registered` | 不存在 | 有初始化现场 | 与请求完全相同，且全部已有成员、字节、摘要匹配 | 持 gate 仅续建缺失的精确成员，发布请求目标状态的 marker 并进入 cleanup | 不处理无 marker 的初始化现场 |
| `candidate` 或 `registered` | 不存在 | 有初始化现场 | 不同、未知、损坏或外部变化 | `runtime.init_conflict` 或 `runtime.corrupt`，零写 | 不处理 |
| `candidate` | 存在 candidate marker | marker/runtime/descriptor 全匹配且 cleanup artifacts 均按清单收敛 | 任意 | 返回 candidate 已初始化结果，零写 | 正常运行期恢复仅处理其他现场 |
| `registered` | 存在 registered marker | marker/runtime/descriptor 全匹配且 cleanup artifacts 均按清单收敛 | 任意 | 返回 registered 已初始化结果，零写 | 正常运行期恢复仅处理其他现场 |
| `candidate` 或 `registered` | 存在相同状态 marker | marker/runtime/descriptor 全匹配，但仍有 descriptor cleanup artifacts | 任意 | `runtime.recovery_required`，零写 | 显式 `recover()` 取得稳定 root lock 后按清单幂等 cleanup |
| `candidate` 或 `registered` | 存在相反状态 marker | marker/runtime/descriptor 可认证但状态与请求目标不同 | 任意 | `runtime.init_conflict`，零写；不把初始化转换为 adopt | 不处理 |
| `candidate` 或 `registered` | 存在 marker | runtime schema 标识可认证但为旧版或未知版；其余节点没有认证矛盾 | 任意 | `runtime.schema_unsupported`，零写、零迁移、零双读、零 cleanup | 不处理；需要兼容/迁移合同另行授权 |
| `candidate` 或 `registered` | 存在 marker | 任一不匹配或节点异常 | 任意 | `runtime.corrupt`，保留现场 | `runtime.corrupt`，保留现场 |
| `candidate` 或 `registered` | 任意可认证 marker | marker/record/cleanup 现场处于 recovery pending | 任意 | `runtime.recovery_required` 或对应 recovery 错误，零写；保持 overlay 之前的根状态 | 只有显式 `recover()` |

同一 target 的 init 由 gate 串行；不同 descriptor 冲突。没有产品清除接口，也没有
首次 `Execute` 创建 runtime 的转换。marker 的目标状态只由 `RootState` 请求决定；普通
`recover()` 不处理无 marker 的初始化现场。公开 `InitOutcome` 的字段和编码仍为
`unresolved`，但 candidate/registered 的领域分支不再留空。

### 逻辑提交、副作用与恢复

marker 是初始化的逻辑提交点：发布前 runtime 必须完整且逐项耐久；marker durable
后，初始化必须进入运行期同一 cleanup 路径。该路径取得 stable OS root lock，durable
发布 `holder_kind=recovery` 记录，严格按 `InitDescriptor` 清理，再依次 unlink
记录、fsync `.draft/`、释放 OS lock。只有这些完成后才可报告首次初始化成功。

marker durable 后，descriptor 列出的 cleanup artifacts 只能在运行期 root lock 下
按精确清单清理。已缺失成员可幂等跳过；仍存在成员必须逐节点 no-follow，且路径、
regular 类型与摘要完全匹配。unknown member、符号链接、类型/digest mismatch 或
外部变化一律 `runtime.corrupt`，不得删除。cleanup 中断保留现场，后续仅由显式
`recover()` 收敛；普通写、read 或 `DryRun` 不得隐式清理。

目标形状、descriptor 或平台能力在 target write 前失败时零写。发布 marker 或
cleanup 中的 record/fsync/unlink 耐久性失败不返回成功，保留可诊断现场；下一步是
显式 recover，而不是重建另一个 descriptor、自动删除或回读旧 schema。

### 可证伪验证

- 在三平台合格本地卷验证 capability 成功与每种无法证明的能力均在写前返回
  `runtime.platform_unsupported`。
- 为上表各请求目标和 marker/现场分区构造反例，验证每行唯一命中、可读但旧/未知
  schema 的 `schema_unsupported` 不与 `init_conflict`/`corrupt` 互相降级，且零写行
  目录字节不变。
- 在 marker 发布、recovery record 发布、每个 cleanup member、record file fsync、
  unlink 与最终目录 fsync 注入失败，验证可认证现场被保留且只有 recover 可继续。
- 验证 `replace_exact_regular` 的 missing、symlink、directory、identity change、
  replace error 与父目录 fsync error 均不被报告为替换成功。

### `unresolved` 的公开 ABI

| 项目 | 状态 | 阻断原因 |
| --- | --- | --- |
| `InitOutcome` 的精确字段名、顺序和已初始化结果编码 | `unresolved` | 已接受或已签来源规定可观察语义，未给出公开结果 ABI。 |
| `InitDescriptor` 的成员字段名、编码字节和 cleanup 清单字段布局 | `unresolved` | 合同固定其权威范围与精确匹配要求，未签署字段级 schema。 |
| 平台适配器的 FD/inode/handle 实现算法 | `unresolved` | 合同只固定可观察的 lock、no-follow 与 durability 性质。 |

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#3-平台根与持久布局`
- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#4-init-状态机`
