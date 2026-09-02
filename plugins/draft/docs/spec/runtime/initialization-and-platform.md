# 运行时初始化与平台

## 初始化与平台前置

本合同规定 `Lifecycle.initialize` 如何把一个显式 root 认证为当前 Draft，并规定 marker
发布前后的安全边界。它不定义页面、关系或 marker 的领域内容；那些事实由 Draft owner
拥有。

初始化输入是 `AbsoluteRoot` 与请求目标 `root_state: candidate | registered`。CLI 的
`init ROOT --candidate` 传入 `candidate`，不带该选项则传入 `registered`。成功或失败均按
[公共结果、错误与可观测性](errors-and-observability.md#公共结果错误与可观测性) 投影；本页只
决定初始化状态事实、失败选择和副作用。

在任何 target write 前，平台必须证明当前本地卷支持逐节点 no-follow、create-only/no-replace
原子发布、live OS lock、文件和目录 durability，以及可终止隔离调用的 hard timeout。缺少
任一证明时返回 `runtime.platform_unsupported`，零写。POSIX、
Windows 和 macOS 可以用不同实现满足这些相同的可观察性质。

### 权威现场与私有实现边界

`InitDescriptor` 是 runtime 内唯一完整的初始化 authority。它认证 marker 预期字节、初始
内容、v1 布局、允许节点类型和精确 cleanup artifacts；marker 不重复 descriptor，也不含
初始化身份。descriptor 的成员名、编码、摘要算法、文件名和 cleanup 清单布局均为 Draft
私有实现，不是公共字段。

当前 v1 runtime 的持久节点包括 Draft marker、descriptor、operation 记录、lifecycle
authority、index authority 和 Trash 记录。immutable record 使用 canonical encoding、
create-only 发布、file fsync 与父目录 fsync。`active.json` 与 `index/head.json` 是替换型
authority；schedule 和 cache 只可从 authority 重建，绝不能授权业务结果。

`replace_exact_regular(expected_target, staged_source)` 只接受同目录、已认证的 canonical
regular staged source，和 identity/digest 都等于 `expected_target` 的 canonical regular
target。它逐节点 no-follow、原子可见。缺失、符号链接、目录、非规范 target、identity/digest
变化或不确定结果都不是成功：stage 后变化是 `runtime.external_change`，稳定类型/编码非法是
`runtime.corrupt`，二者均零替换。预期不存在的派生文件必须使用认证过的 create-only 发布。

### 初始化状态表

初始化先取得目标 root 外的临时 OS init gate，并持有到 marker durable。同一 target 的
初始化由该 gate 串行；不同 descriptor 互相冲突。它只接受严格空 target、空 `.draft` 或
仅空 `.draft/runtime` 作为新的空形态。gate 内按 descriptor 创建全部 v1 节点和初始内容，
逐项 durable，最后才发布 marker。

下表按从上至下优先级分类；每次调用只命中一行。所有标为零写的行都不迁移、不双读、不清理，
并保留既有现场。

| root / marker / runtime 现场 | descriptor 与请求事实 | 唯一结果 | 副作用与后续 |
| --- | --- | --- | --- |
| 尚未识别可认证当前 marker 或当前初始化现场，且目标是否为空、marker 或初始化现场所需事实无法完整稳定取得 | 尚无更具体的已认证现场 | `runtime.closure_incomplete` | 零写；保留现场。 |
| 严格允许的空形态，当前 marker 不存在 | 为请求 `candidate` 或 `registered` 建立的新 descriptor | `initialization/created`，返回请求根状态 | 持 gate 完整创建、durable 发布 marker，随后完成必需 cleanup。 |
| 无 marker 的当前初始化现场 | 完成认证前，安全判断所需事实因权限、I/O 或不稳定而暂时无法完整取得 | `runtime.closure_incomplete` | 零写；保留现场。 |
| 无 marker 的当前初始化现场 | supported v1、完整可认证、与请求目标和所有预期成员完全相同 | `initialization/resumed`，返回请求根状态 | 仅续建缺失的精确成员，发布 marker，完成 cleanup。 |
| 无 marker 的当前初始化现场 | supported v1、完整可认证，但请求目标、请求语义或预期成员不同 | `runtime.init_conflict` | 零写。 |
| 无 marker 的当前初始化现场 | schema 标识可认证但为旧版或未知版，其余已读节点无矛盾 | `runtime.schema_unsupported` | 零写、零迁移、零双读、零 cleanup。 |
| 无 marker 的当前初始化现场 | v1 私有节点稳定观察为类型、schema、摘要、内部关系、成员集合或 no-follow 规则不合法 | `runtime.corrupt` | 零写并保留现场。 |
| 无 marker 的当前初始化现场 | 同一次调用中先成功认证的事实在重复读取、stage 后校验或最终重验时发生变化 | `runtime.external_change` | 零写；保留现场。 |
| 无 marker 的当前初始化现场 | 已决定的保存操作失败或结果不确定 | `runtime.durability_failed` | 不宣告成功；保留可恢复现场。 |
| 可认证当前 marker，但其余初始化安全事实尚未完成认证 | 完成认证前，安全判断所需事实因权限、I/O 或不稳定而暂时无法完整取得；recovery overlay 未知 | `runtime.closure_incomplete` | 零写；保留现场。 |
| 可认证当前 marker，schema 标识为旧版或未知版、其余已读节点无矛盾 | recovery overlay 未判断或不适用 | `runtime.schema_unsupported` | 零写、零迁移、零双读、零 cleanup。 |
| 可认证当前 marker，当前 v1 runtime 混有未知成员，或 marker/runtime/descriptor 节点稳定观察为非法或矛盾 | recovery overlay 不得解释为合法 pending | `runtime.corrupt` | 零写并保留现场。 |
| 可认证当前 marker | 同一次调用中先成功认证的事实在重复读取、stage 后校验或最终重验时发生变化；recovery overlay 不适用 | `runtime.external_change` | 零写；保留现场。 |
| 可认证当前 marker | 已决定的保存操作失败或结果不确定；recovery overlay 不适用 | `runtime.durability_failed` | 不宣告成功；保留可恢复现场。 |
| 可认证当前 marker，current v1 事实完整可认证 | recovery overlay 为合法 `pending`（存在 descriptor cleanup artifact 或有效 operation/cleanup pending） | `runtime.recovery_required` | 零写；保持原有根状态，初始化不进入恢复动作。 |
| 可认证当前 marker，current v1 事实完整可认证，状态与请求相同，runtime/descriptor 匹配且 cleanup 已收敛 | recovery overlay 为 `none` | `initialization/already_initialized`，返回 marker 根状态 | 零写。 |
| 可认证当前 marker，current v1 事实完整可认证，状态与请求不同 | recovery overlay 为 `none` | `runtime.init_conflict` | 零写；初始化不转换为 adopt。 |
| 只有旧身份的目录 | 显式 `--root` 选择该目录 | `runtime.root_invalid` | 零写、零接管并保留全部文件。 |
| 无可认证当前 marker 或当前无 marker 初始化现场，target 稳定非空（包括普通文件、普通目录或仅旧身份） | `init` 直接指向该 target | `runtime.init_target_not_empty` | 零写、零接管并保留全部内容。 |

旧身份只指没有可认证当前 Draft marker/runtime 的旧登记文件或旧内部目录。它不同于可认证
当前 root 上的旧/未知 runtime schema：前者在显式 `--root` 选择时返回 `runtime.root_invalid`，
直接 `init` 时落入稳定非空 target 行；后者按表返回 `runtime.schema_unsupported`。当前 v1
现场出现未知成员一律是 `runtime.corrupt`，不得降级为旧身份或可安全忽略的文件。自动祖先发现
跳过只有旧身份的目录并继续向上寻找可认证的当前 Draft 根。无法稳定读取到足以区分空、当前
初始化现场和稳定非空 target 的事实时，命中表首行的 `runtime.closure_incomplete`，不能猜成
空或非空。

### 初始化失败映射

下表只定义初始化认证事实到既有 machine code 的选择，并按认证阶段、稳定损坏、认证后变化、
耐久保存失败的顺序优先求值。公共 error envelope、退出码、details、定位和建议只由[公共结果、
错误与可观测性](errors-and-observability.md#公共结果错误与可观测性)登记；其中初始化的公共
`details` 精确映射见[初始化认证事实的公共 details 映射](errors-and-observability.md#初始化认证事实的公共-details-映射)，
本页不复制这些公共字段。表中先排除 runtime 布局未登记成员、descriptor 文件自身和 cleanup
material 三类特例，再对其余已有组件应用通则。

| 互斥事实 | 唯一结果 | 初始化认证事实 |
| --- | --- | --- |
| 完成认证前，安全判断所需事实因权限、I/O 或不稳定而暂时无法完整取得 | `runtime.closure_incomplete` | 安全判断所需事实不完整。 |
| 当前 v1 runtime 布局出现未登记成员（包括 descriptor 未列举的额外 cleanup member） | `runtime.corrupt` | runtime 布局含未登记成员。 |
| descriptor 文件自身出现未知字段 | `runtime.corrupt` | descriptor 自身含未知字段。 |
| descriptor 文件自身 schema 非法 | `runtime.corrupt` | descriptor 自身 schema 非法。 |
| descriptor 文件自身摘要不符 | `runtime.corrupt` | descriptor 自身摘要不符。 |
| descriptor 文件自身类型非法 | `runtime.corrupt` | descriptor 自身类型非法。 |
| descriptor 文件自身内部关联不匹配 | `runtime.corrupt` | descriptor 自身内部关联不匹配。 |
| descriptor 文件自身 no-follow 失败 | `runtime.corrupt` | descriptor 自身 no-follow 失败。 |
| 除上述 runtime 未登记成员与 descriptor 文件自身外，其他已有组件的非 cleanup 节点类型非法 | `runtime.corrupt` | 实际所属组件的非 cleanup 节点类型非法。 |
| 除上述 runtime 未登记成员与 descriptor 文件自身外，其他已有组件的非 cleanup 节点 schema 非法 | `runtime.corrupt` | 实际所属组件的非 cleanup 节点 schema 非法。 |
| 除上述 runtime 未登记成员与 descriptor 文件自身外，其他已有组件的非 cleanup 节点摘要不符 | `runtime.corrupt` | 实际所属组件的非 cleanup 节点摘要不符。 |
| 除上述 runtime 未登记成员与 descriptor 文件自身外，其他已有组件的非 cleanup 节点内部关联不匹配 | `runtime.corrupt` | 实际所属组件的非 cleanup 节点内部关联不匹配。 |
| 除上述 runtime 未登记成员与 descriptor 文件自身外，其他已有组件的非 cleanup 节点出现未知字段 | `runtime.corrupt` | 实际所属组件的非 cleanup 节点含未知字段。 |
| 除上述 runtime 未登记成员与 descriptor 文件自身外，其他已有组件的非 cleanup 节点 no-follow 失败 | `runtime.corrupt` | 实际所属组件的非 cleanup 节点 no-follow 失败。 |
| descriptor 精确列举、但没有更具体已登记组件名的 cleanup material 稳定类型不匹配（包括符号链接） | `runtime.corrupt` | 无独立组件名的 cleanup material 类型不匹配。 |
| descriptor 精确列举、但没有更具体已登记组件名的 cleanup material 稳定摘要不匹配 | `runtime.corrupt` | 无独立组件名的 cleanup material 摘要不匹配。 |
| descriptor 精确列举、但没有更具体已登记组件名的 cleanup material 稳定 no-follow 失败 | `runtime.corrupt` | 无独立组件名的 cleanup material no-follow 失败。 |
| cleanup material 本身是已登记的 `authority`、`record`、`catalog`、`head` 或 `ledger`，且稳定类型不匹配（包括符号链接） | `runtime.corrupt` | 已登记组件的 cleanup material 类型不匹配。 |
| cleanup material 本身是已登记的 `authority`、`record`、`catalog`、`head` 或 `ledger`，且稳定摘要不匹配 | `runtime.corrupt` | 已登记组件的 cleanup material 摘要不匹配。 |
| cleanup material 本身是已登记的 `authority`、`record`、`catalog`、`head` 或 `ledger`，且稳定 no-follow 失败 | `runtime.corrupt` | 已登记组件的 cleanup material no-follow 失败。 |
| 本次调用中已经成功认证的事实在重复读取、stage 后校验或最终重验时发生变化 | `runtime.external_change` | 已认证事实在后续校验中发生变化。 |
| 已决定的保存操作失败或结果不确定 | `runtime.durability_failed` | 已决定的保存操作失败或结果不确定。 |

没有产品清除接口，也没有首次 Execute 创建 runtime 的转换。普通 `recover()` 不处理无 marker
的初始化现场；它只处理 marker 已发布后由 descriptor 指定的 cleanup remnant 或运行期 pending。

### marker 发布、cleanup 与恢复

marker durable 是初始化逻辑提交点：在此之前，runtime v1、初始内容和 descriptor 必须都
已完整 durable。marker 发布后，初始化进入与其他 runtime 现场相同的 cleanup 路径：取得
stable OS root lock，durable 发布 recovery record，严格按 `InitDescriptor` 清理，再 unlink
record、fsync `.draft/` 并释放锁。只有这些步骤完成才返回 `created` 或 `resumed`。

cleanup 只可处理 descriptor 精确列举的 material。已缺失成员可以幂等跳过；仍存在成员必须
逐节点 no-follow，且路径、regular 类型和 digest 都匹配。稳定发现的未知成员、符号链接、错误
类型、摘要不匹配或 no-follow 失败，按上表选择 `runtime.corrupt`；只有已认证事实在重复读取、
stage 后校验或最终重验时发生变化才选择 `runtime.external_change`；保存失败或结果不确定时
选择 `runtime.durability_failed`。三者都不得删除不匹配的 material。中断后保留现场；普通操作、读、
DryRun 和 notification 都不得隐式清理。

### 可证伪验证

- 在三类平台上分别验证每项必须 capability 的成功与写前失败；失败只返回
  `runtime.platform_unsupported` 且目录字节不变。
- 覆盖表中每种 root 状态、稳定非空 target、旧/未知 schema、当前 v1 未知成员、匹配/
  冲突 descriptor、cleanup remnant 和 recovery pending，验证唯一命中与零写边界；按映射表核对
  runtime 未登记成员、descriptor 自身、其他已有组件和 cleanup material 的稳定损坏均选择
  `runtime.corrupt`；只有已认证后变化选择 `runtime.external_change`，保存失败或不确定选择
  `runtime.durability_failed`。
- 在 marker 发布、recovery record 发布、每个 cleanup member、file fsync、unlink 与最终
  目录 fsync 注入失败，验证现场保留且只有显式 recover 可继续。
- 验证 `created`、`resumed`、`already_initialized` 的三种输出和最终根状态可区分；成功结果
  按 R-ERRORS 投影且不泄露内部 descriptor 或 cleanup。

## 来源

- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#3-平台根与持久布局`
- `ce64ac30c52ca910fe39fcedd055dd28f0cfbda4:.scratch/draft-operation-runtime-foundation/spec.md#4-init-状态机`
