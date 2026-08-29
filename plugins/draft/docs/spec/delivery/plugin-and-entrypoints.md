# 插件与入口

## 身份

**输入**：插件 manifest、skill 名称、CLI 名称、marketplace metadata 和发布产物路径。

**输出**：唯一公开身份 `draft`，以及彼此一致的插件、skill、CLI 与 marketplace 表达。

**状态维度**：身份字符串、入口路径、发布 metadata、是否存在旧身份兼容分支。

**不变量**：公开身份只使用 `draft`；不识别、升级、双读或迁移旧身份。每个发布产物表达相同的身份和入口，不能以某个 manifest 或 help 输出作为第二套协议。

**转换与副作用**：身份或路径变更必须同步所有 manifest、skill、入口和分发 metadata，再进入发布核验；这不是 runtime `Outcome` ABI 的定义或重写。

**失败与验证**：任一身份、路径或 metadata 不一致即拒绝发布并保留诊断。核验 plugin/marketplace manifest、skill 名称、CLI help 与分发 smoke test 的一致性，并确认不存在旧身份兼容分支。

## 责任边界

**输入**：根选择结果、封闭请求、插件 bridge/binding/SessionStart 事实，以及核心公开接口返回的已认证机器结果。

**输出**：插件入口对核心调用的路由和对宿主的结果投影。

**状态维度**：核心与适配层模块归属、入口类型、bridge/binding 合法性、SessionStart 上下文、公开结果是否为成功或错误。

**不变量**：核心不导入插件适配层；适配层不重新定义领域、runtime 或错误 ABI。CLI 只负责选择 root、构造封闭请求、调用公开接口并投影结果；bridge/binding/SessionStart 只提供宿主事实。adapter 不自行安排重试，不隐式调用 `recover()`，也不得把错误伪装为成功。

**转换与副作用**：入口解析并认证宿主事实后调用核心；适配层可发布/消费 bridge、建立 binding 或响应 SessionStart，但不能绕过核心的认证、恢复和错误投影。每次结果投影保留核心给出的机器语义。

**失败与验证**：依赖方向、入口、bridge/binding 或投影无法认证时失败关闭并保留可诊断信息。用 import/AST 边界检查、CLI call-site、bridge/binding 集成检查、SessionStart 分发检查和错误投影测试验证；不以测试文本定义结果 ABI。

## 诊断投影

`P-DIAGNOSTICS` 的 owner 是 [查询规格的诊断段](../draft/query-check-and-output.md#诊断)。本页只是该合同的 delivery 消费者：它投影已认证诊断事实，不复制或重新定义 `Outcome`、`ReadOutcome`、`RecoveryOutcome`、错误码、字段顺序或退出码。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-07-30-draft-plugin-manifests-and-path-contract-design.md#公开交付身份`（P-IDENTITY；signed-Q30）
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md#责任边界`（P-BOUNDARY）
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-05-draft-core-plugin-separation-design.md#责任边界`（P-BOUNDARY）
