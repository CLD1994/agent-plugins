# 权威与成员资格

## 来源资格与唯一权威

**输入**：候选规格树、固定 Git locator、来源角色、逐合同的精确取代范围、消费者与正式链接核验、独立评审结果，以及 signer 批准。

**输出**：已进入权威分支的当前行为唯一 owner，或阻止候选合入的可复核 `unresolved` 诊断。

**状态维度**：页面是否在当前规格树；来源是否具备资格；取代范围是否精确；消费者与链接是否闭合；独立评审是否通过；候选是否获批准并已进入权威分支。

**不变量**：

- 当前读取路径只能是 `CONTEXT-MAP.md` → `plugins/draft/CONTEXT.md` → `plugins/draft/docs/spec/index.md` → owner 页面或 ADR。
- 页面正文是行为唯一 authority；ADR 只说明取舍，不能补写、覆盖或裁决行为。
- 资格按行为合同而非日期、目录、实现、测试、issue 状态或工具输出判定。显式 `supersedes` 只覆盖被点名的 section 和行为范围。
- Git locator 一律为 `<完整 commit>:<仓库相对路径>#<真实 heading>`。历史签署转录固定指向 `#已签署决定迁移摘要`，并以 `signed-Qx` 或 `signed-S1` 旁注说明，不以 `#Qx` 代替 heading。
- 临时工作文件、缓存、索引、旧 dated spec、实现、测试、issue、计划和 Agent Brief 都不是读取当前行为的 fallback。
- 候选即使内容完整，也只是在待核验的来源树中；在获批准并作为一个不可拆分的内容变更进入权威分支前，它不是 current authority。

### 来源资格判定

| 来源事实 | 资格 | 处理 |
| --- | --- | --- |
| 当前规格树中由入口可达的 owner 页面 | 当前行为 authority | 读取该页的精确 Target。 |
| 已签署转录、固定历史规格且 scope 已获批准 | provenance / 控制证据 | 只支持被明确列出的合同和范围。 |
| `.scratch` feature spec、issue、map、Agent Brief | feature delta、tracker 或工作流证据 | 不产生当前行为；只能按 Feature 生命周期判定是否可派遣。 |
| 实现、测试、CLI help、参考资料 | 消费者或一致性证据 | 可报告漂移，不能反向定义规格。 |
| provenance、资格、scope、消费者、链接或两个 eligible 来源的冲突无法判定 | `unresolved` | 记录 locator、缺失核验和影响范围；不批准权威分支合入。 |

### 候选验证、批准与合入

候选依次经历分类、逐合同提取、重叠裁决、来源与 consumer/link 核验、独立评审、批准和权威分支合入。每一步都针对同一完整候选树；页面移动、删除或 owner 改写必须在候选内同时修复入站正式链接与已知消费者。

批准的是该候选的完整内容，不是某一种 Git 提交图形。可按本次选择使用 merge、线性搬运或最终单提交进入权威分支；它们都不得拆开已批准的内容变更。选择最终单提交时，该提交必须表示固定比较点之后的完整已审文件树，不能只包含修复片段或与旧版本拼接比较。

评审后只要正文、关系、来源、消费者、链接或退役集合发生变化，就必须重新核验受影响范围并重新取得批准；不得把先前评审结果套用到已改变的候选。

**失败语义**：未知 locator、重复或断裂 Target、未批准 scope、来源冲突、断链、未闭合消费者或未通过独立评审都不能降级为“当前”“不存在”或“实现已覆盖”。保留诊断并关闭候选合入；实现与测试只能形成 conformance gap。

**验证**：在只按当前根路由读取行为的 checkout 中，不把临时工作文件、旧规格、实现或会话作为 current authority；从根路由定位一个 Contract-ID 的唯一 Target，对每个 locator 解析 commit、路径和唯一 heading，并对每个 supersession 核对精确范围；核对候选的消费者、正式链接、独立评审和批准对应同一完整文件树。

## 相关规格

- [迁移与原子切换](migration.md#迁移与原子切换)
- [Feature spec 生命周期](feature-specs.md#feature-spec-生命周期)
- [行为合同登记](contract-register.md)

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`（signed-Q1、signed-Q2、signed-Q8、signed-Q9、signed-Q10、signed-Q15、signed-Q18、signed-Q19、signed-Q20、signed-Q23、signed-Q24、signed-Q28）
