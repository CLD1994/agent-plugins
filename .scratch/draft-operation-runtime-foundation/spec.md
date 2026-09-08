# Draft operation runtime 基础实施任务

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md

## 1. 任务定位与边界

本文组织 runtime v1 的实现范围与验收路线，实际工作通过独立 child issue 执行，不将整票作为单一实施任务。
当前行为由 `Design-Refs` 指向的七份现行设计规格定义；本文提供读取顺序、实现责任、建议的代码与测试位置、
停止条件和完成条件。实现者不得从本文推导新的字段、错误、状态、时限、调用顺序或其他行为。

实现范围覆盖七份 owner 共同负责的 runtime 核心和 CLI/适配层接缝。领域词汇仅按
[Draft 领域词汇](../../plugins/draft/CONTEXT.md)理解；tracker 的字段只用于派遣和导航，
不成为行为来源。需要改变行为时，先停止实施并通过正式规格流程确认、更新相应 owner，
再按 owner 更新实现和测试；本条目本身不得承载该行为变化。

代码和测试的默认位置如下；具体文件由执行者依据现有数据流确定：

- 核心实现：`plugins/draft/skills/draft/scripts/runtime/src/draft/`
- 核心单元测试：`plugins/draft/skills/draft/scripts/runtime/tests/unit/`
- 核心集成测试：`plugins/draft/skills/draft/scripts/runtime/tests/integration/`
- CLI/适配层实现：`plugins/draft/scripts/`（含 `adapter/`）
- CLI/适配层单元测试：`plugins/draft/scripts/tests/unit/`
- CLI/适配层集成测试：`plugins/draft/scripts/tests/integration/`

测试分类、替身边界和命令遵循
[plugins/draft/AGENTS.md](../../plugins/draft/AGENTS.md)；tracker 操作遵循
[Issue tracker：本地 Markdown](../../docs/agents/issue-tracker.md)。

## 2. 开始前的共同步骤

1. 先逐页读取 `Design-Refs` 指向的七份设计规格，确认要实现的行为、验证章节和引用的 Draft owner；再
   检查对应现有代码、测试及其真实调用关系。
2. 为每项 owner 合同确定最窄的生产责任边界和相应测试边界。先建立能区分合同分支的
   测试，再实现；测试替身必须服从真实协作者接口，跨文件系统、进程、平台或分发边界的
   行为放在集成测试。
3. 按第 3 节的建议顺序推进。顺序是实施建议，不改变 owner 之间已确认的行为依赖；如
   数据流显示需要调整顺序，仍须保持每个阶段的停止和完成条件。
4. 任一实现或测试遇到 owner 未定义的状态、互相冲突的规定、需要新增公开协议、或需要
   让 tracker 替 owner 做决定时，立即停止相关工作，记录具体 owner 文件、标题和触发
   场景并报告。不得自行补语义、向 tracker 添加协议副本、修改 owner 规避冲突或删除/放宽
   测试。

## 3. Owner 对应的实施路线

| 顺序 | 设计依据与实现责任 | 建议代码与测试位置 | 停止条件 | 完成条件 |
| --- | --- | --- | --- | --- |
| 1 | [运行时公共接口](../../plugins/draft/docs/spec/runtime/interface.md)：落实公开入口、root 绑定、CLI 路由、输入边界及核心与适配层的隔离；只按 owner 连接内部协作者，不新增公开 seam。 | 核心的入口/装配/CLI 模块位于 `plugins/draft/skills/draft/scripts/runtime/src/draft/`；适配层位于 `plugins/draft/scripts/adapter/` 和 `plugins/draft/scripts/draft.py`。从核心 `tests/unit/`、`tests/integration/` 以及适配层 `tests/unit/`、`tests/integration/` 覆盖入口和依赖边界。 | 入口需要 owner 未定义的输入或输出、root 选择出现第二种解释、或内部协作者必须泄漏到公开边界。 | 入口与适配层行为逐项对应 owner，公开边界测试和核心/适配层依赖限制测试通过，内部实现细节未进入公开结果。 |
| 2 | [运行时初始化与平台](../../plugins/draft/docs/spec/runtime/initialization-and-platform.md)：落实显式初始化、初始化现场认证与续建、平台能力检查、持久布局建立及初始化后清理的实现协作；平台差异只留在平台适配实现内。 | 核心运行时目录中的初始化、路径、平台和持久化模块；适配层的 init 路由。核心单元测试隔离稳定协作者，文件系统、原子发布、锁、耐久性和跨平台行为放在核心/适配层集成测试。 | 无法证明写入前的某项平台能力，初始化现场出现 owner 未覆盖的组合，或实现必须猜测/删除未认证现场。 | owner 验证章节覆盖空现场、已有现场、旧版或未知现场、损坏及保存失败等分区；初始化、平台拒绝、清理和现场保留的测试证据齐全。 |
| 3 | [运行时准入与操作计划](../../plugins/draft/docs/spec/runtime/admission-and-effects.md)：落实请求准入、root/runtime 认证、操作计划与模式隔离、锁边界、自动阶段入口以及最终重读/重验；保证普通入口不替代显式恢复。 | 核心 `operations.py`、`plan_effects.py`、`lifecycle.py`、`paths.py`、`observations.py` 及相关装配代码；核心单元测试覆盖计划和准入，集成测试覆盖锁、竞态、真实持久边界，适配层测试覆盖路由投影。 | 准入顺序或锁持有责任在 owner 之间不一致，某入口需要把暂时未知当作成功/空结果，或自动工作会绕过 owner 规定的恢复入口。 | 每一类入口都在正确阶段停止或继续，计划不会提前产生副作用，最终重验和释放条件可由测试证明；核心与适配层测试分开通过。 |
| 4 | [运行时权威与派生投影](../../plugins/draft/docs/spec/runtime/authority-and-projections.md)：落实记录、内容提交、权威资料、派生资料、原子替换、崩溃窗口和恢复协作；保持权威与投影的单向依赖。 | 核心 `operation_facts.py`、`operations.py`、`link_index.py`、`link_index_maintenance.py`、`trash_package.py` 及其实际协作者；核心 unit 覆盖纯提交/认证逻辑，integration 覆盖写入顺序、崩溃和耐久性，必要时在适配层补分发边界测试。 | 实现从目录存在、修改时间、缓存或终态审计猜测权威，无法证明临时资料的所有权，或投影失败会改变已经提交的权威事实。 | 权威提交、投影重建/降级、暂存资料清理和恢复交接均有 owner 对应测试；权威资料与派生资料的责任边界在代码结构和运行行为中可核验。 |
| 5 | [运行时生命周期与交接](../../plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md)：落实自动生命周期入口、候选处理、Trash/Restore 交接、外部调用隔离、重试和授权清理；只消费已由领域 owner 认证的事实。 | 核心 `lifecycle.py`、`trash_lifecycle.py`、`trash.py`、`trash_package.py` 及 handoff 协作者；核心 unit 覆盖调度与状态转换的局部契约，integration 覆盖真实文件、worker、锁、超时、耐久失败和重复调用。 | 需要从外部可能结果推断成功、在未获授权时清理资料、释放仍需持有的锁，或自动入口职责超出 owner 定义。 | 每个允许的自动入口、交接阶段、失败/未知恢复和清理边界均由测试证明；实现不会向其他公共路径泄漏生命周期内部资料。 |
| 6 | [显式恢复](../../plugins/draft/docs/spec/runtime/recovery.md)：落实 pending 现场发现、证据门禁、授权前向收敛/回退/清理，以及重复恢复的稳定行为；恢复只处理已认证现场。 | 核心恢复协调、记录折叠和持久化模块（优先沿现有 `operations.py`、`lifecycle.py` 及其协作者追踪）；在核心 `tests/unit/` 做证据分类替身测试，在 `tests/integration/` 做崩溃现场、真实恢复和重复调用测试。 | 证据不足却需要猜测动作，恢复路径会接管 live owner、扫描无关终态资料、调用外部交接，或实现无法说明现场为何安全收敛。 | owner 的每类 pending 现场都有唯一可观察结果或明确阻断；恢复后现场、调用方动作和再次恢复的行为通过集成测试验证，且不泄漏内部持久细节。 |
| 7 | [运行时结果、错误与可观测性](../../plugins/draft/docs/spec/runtime/errors-and-observability.md)：落实统一结果投影、错误封装、退出状态、JSON/人类输出和安全建议的适配层；将内部事实收敛到 owner 已登记的公开边界。 | 核心 `cli_results.py`、`errors.py`、`cli.py` 和相关查询投影；适配层 `adapter/errors.py`、`adapter/bridge.py`、`draft.py`。核心与适配层各自的 unit 覆盖投影/序列化，integration 覆盖 CLI、分发和 stdout/stderr 边界。 | 需要在本条目或其他非 owner 文件新增结果字段/错误类别，公开输出泄漏路径或内部记录，或文案与已认证事实不一致。 | 所有入口的成功、失败、人类输出和机器输出均由 owner 约束；输出测试、CLI 集成测试和分发检查通过，未出现第二套协议。 |

## 4. 阶段门禁与交付

每完成一个 owner 阶段，都要核对：改动是否仍落在该 owner 的责任边界；测试是否验证了
输入事实、结果和副作用而不是复制私有调用图；失败、重试、恢复和清理是否有明确语义；
以及必要的行为决定是否已经通过正式规格流程进入 owner。发现跨 owner 接缝未闭合时，按第 2 节停止并报告，
不得在 tracker 中临时规定接缝。

整合前后至少完成以下检查：

- 核心与适配层按 [plugins/draft/AGENTS.md](../../plugins/draft/AGENTS.md) 分开运行 unit、integration 和适用的静态/格式检查。
- 运行 `git diff --check`，检查本条目头部字段、Design-Refs 集合及正文中的所有相对链接。
- 扫描确认本文只组织实施路线：不包含任何可脱离 owner 独立执行的协议细节。
- 核对每项实现和测试都能回到对应 owner；owner 发生必要行为变化时，先停止并由正式规格流程处理。

完成条件是七份 owner 的实现和验证均已通过、跨 owner 数据流和失败恢复路径闭合、没有
未决语义被 tracker 或代码自行补全，并且上述阶段门禁和交付检查都有可复核证据。
