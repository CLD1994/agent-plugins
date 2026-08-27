# 08 替换旧 runtime、测试与权威表面

Status: ready-for-agent

Blocked by: 01, 02, 03, 04, 05, 06, 07

## 确认合同

实施 spec 第 1--2、11 节的 replace-don't-layer 规则。完成后跨模块仅允许公开行为 Interface `Lifecycle`、root-bound `OperationRuntime`，唯一 external seam `HandoffAdapter`，受限 CLI 构造 seam `RuntimeFactory.for_root(AbsoluteRoot)` 与 CLI adapter composition/投影边界；CLI init 只可直接调用公开 `Lifecycle.initialize`，其他受管理根命令只可通过该 factory 获取 `OperationRuntime`。runtime Implementation 可依赖抽象 `HandoffAdapter` 并在内部装配本地 concrete Adapter；CLI、插件适配层生产代码及插件适配层 unit/integration tests 对抽象或 concrete Adapter 的导入均为零，插件适配层不得导入核心 draft 包；核心 runtime unit/integration tests 仅可引用抽象 `HandoffAdapter` Interface，不得导入 concrete local Adapter、`RuntimeDeps`、`PlatformAdapter` concrete implementations、worker、Clock/ID concrete sources、lock、recovery、index-settled 或其他内部协作者。`RuntimeFactory` 不是额外行为 Interface，不返回或承诺 Lifecycle，不得暴露 `RuntimeDeps`、`PlatformAdapter`、HandoffAdapter、worker、Clock、ID source、planner、lock、recovery、index-settled 或其他内部协作者。旧 runtime schema、导入、转发、双读、旧调用图测试与并行可实施规格均不得保留。终态审计不在此任务删除。

## 建议实现与顺序

1. 先写旧 schema fail-closed、public boundary、CLI import 与终态 audit 保留的失败测试/静态审计。完成条件：测试证明旧 schema 不可读写、终态 audit 仍保留；公开边界测试覆盖 CLI init 直达 `Lifecycle.initialize` 及其余六个 OperationRuntime 方法经 `RuntimeFactory.for_root(AbsoluteRoot)` 返回实例调用；AST/import/export 检查只允许 spec 第 2 节的 Lifecycle/OperationRuntime 公开行为 Interface、HandoffAdapter 唯一 external seam、`RuntimeFactory.for_root(AbsoluteRoot)` 受限 OperationRuntime 构造 seam 与 CLI adapter composition/投影边界；CLI、插件适配层生产代码及插件适配层 unit/integration tests 对抽象或 concrete HandoffAdapter 的导入为零，插件适配层不得导入核心 draft 包；核心 runtime tests 仅可引用抽象 HandoffAdapter Interface，使用受真实 Interface 约束的 mock/fake，且不得导入 concrete local Adapter 或其他内部协作者；factory 返回类型不含、不承诺 Lifecycle，且不暴露 deps 或内部协作者；证据为测试与审计输出，并与 Issue 02、Issue 07 的同一边界一致。
2. 删除被替代生产与测试表面，用新公开接口行为测试取代旧 shallow helper 调用图测试。完成条件：旧 helper 的生产导入和专属调用图测试均不存在，新行为测试覆盖相同合同；证据为删除清单、`rg` 与测试映射。
3. 处置下列本 feature 被替代 authority：删除 `.scratch/draft-coordinated-recovery-ownership/spec.md`、`.scratch/draft-durable-link-index-maintenance/spec.md` 与 `.scratch/runtime-file-split-plan.md`。`docs/discussion/**` 仅作演进证据，保留不删。完成条件：三个路径均满足 `test ! -e <path>`，历史讨论仍存在；证据为文件存在性检查和文档索引审计，不以 `git status` 作为条件。
4. 独立运行核心/adapter unit 与 integration、三平台门禁、100,000 个 terminal operation/10,000 个 active catalog entry、10,000 个 handoff entry benchmark、import graph、`git diff --check` 与 authority 审计。完成条件：三个被替代路径均 `test ! -e`，且 `rg -n --glob 'spec.md' '^# Draft operation runtime 基础规格$' .scratch/draft-operation-runtime-foundation` 恰有一项；import/export graph 证明 CLI init 仅调用公开 `Lifecycle.initialize`，其他命令仅调用 `RuntimeFactory.for_root(AbsoluteRoot)` 获取 OperationRuntime，factory 不返回或承诺 Lifecycle；CLI、插件适配层生产代码及插件适配层 unit/integration tests 对抽象或 concrete HandoffAdapter 的导入为零，插件适配层不得导入核心 draft 包；核心 runtime unit/integration tests 仅可引用抽象 HandoffAdapter Interface，三平台 integration 通过公开行为 Interface 使用内部装配的真实本地 concrete Adapter，并不得导入 concrete Adapter 或其他内部协作者；历史讨论排除在 authority 搜索外。证据为这些命令输出和文档索引审计。

## 禁止捷径与完成标准

不得以保留 facade/re-export、格式兼容、吞错误、删除行为测试或放宽检查完成替换。完成时所有入口遵守 spec，旧 runtime 不可读写且未知 schema 失败关闭；性能证明 normal path 不随 terminal N 增长。
