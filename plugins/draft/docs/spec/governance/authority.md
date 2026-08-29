# 权威与成员资格

## 来源资格与唯一权威

**输入**：权威分支中的规格页、固定 Git locator、来源角色、逐合同的显式取代范围，以及 signer 批准的确切迁移 series。

**输出**：某项当前行为的唯一 owner，或可复核的 `unresolved` 诊断。

**状态维度**：页面是否在当前规格树；来源是否具备资格；取代范围是否精确；迁移 series 是否得到批准且已合入。

**不变量**：

- 当前读取路径只能是 `CONTEXT-MAP.md` → `plugins/draft/CONTEXT.md` → `plugins/draft/docs/spec/index.md` → owner 页面或 ADR。
- 页面正文是行为唯一 authority；ADR 只说明取舍，不能补写、覆盖或裁决行为。
- 资格按行为合同而非日期、目录、实现、测试、issue 状态或工具输出判定。显式 `supersedes` 仅覆盖被点名的 section 和行为范围。
- Git locator 一律为 `<完整 commit>:<仓库相对路径>#<真实 heading>`。历史签署转录固定指向 `#已签署决定迁移摘要`，并以 `signed-Qx` 或 `signed-S1` 旁注说明，不以 `#Qx` 代替 heading。`accepted-Qx` 只标识本轮过程输入，绝不与 `signed-Qx` 混用。
- `/tmp`、缓存、索引、旧 dated spec、实现、测试、issue、计划和 Agent Brief 都不是读取当前行为的 fallback。

### 来源资格判定

| 来源事实 | 资格 | 处理 |
| --- | --- | --- |
| 当前规格树中由入口可达的 owner 页面 | 当前行为 authority | 读取该页的精确 Target。 |
| 已签署转录、固定历史规格且 scope 已获批准 | provenance / 控制证据 | 只支持被明确列出的合同和范围。 |
| `.scratch` feature spec、issue、map、Agent Brief | feature delta、tracker 或工作流证据 | 不产生当前行为；只能按 Feature 生命周期判定是否可派遣。 |
| 实现、测试、CLI help、参考资料 | 消费者或一致性证据 | 可报告漂移，不能反向定义规格。 |
| provenance、资格、scope 或两个 eligible 来源的冲突无法判定 | `unresolved` | 记录 locator、缺失批准和影响范围；阻止 cut-over。 |

**转换与副作用**：候选来源依次经历分类、逐合同提取、重叠裁决、迁移签署和 authority 合入。任何页面移动、删除或 owner 改写必须在同一批准变更中修复入站正式链接。

**失败语义**：未知 locator、重复或断裂 Target、未批准 scope、来源冲突都不能降级为“当前”“不存在”或“实现已覆盖”。保留诊断并阻止 authority 切换；实现与测试只能形成 conformance gap。

**验证**：在不读取 `/tmp`、旧规格、实现或会话的 checkout 中，从根路由定位一个 Contract-ID 的唯一 Target；对每个 locator 解析 commit、路径和唯一 heading；对每个 supersession 核对精确范围。

## 相关规格

- [迁移与冻结基线](migration.md#迁移与冻结基线)
- [Feature spec 生命周期](feature-specs.md#feature-spec-生命周期)
- [行为合同登记](contract-register.md)

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`（signed-Q1、signed-Q2、signed-Q8、signed-Q9、signed-Q10、signed-Q15、signed-Q18、signed-Q19、signed-Q20、signed-Q23、signed-Q24、signed-Q28）
