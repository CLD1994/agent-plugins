# 已签署决定迁移摘要

本文件是 Q1–Q35 与 S1 的 tracked 迁移证据摘要。它只保存已签署决定的当前结论、取代范围和目标页，不是正式行为 authority；正式效力仍需 signer 批准 A/B/C commit series 并合入权威分支。

| 编号 | 当前结论摘要 | 目标迁移单元 |
| --- | --- | --- |
| Q1 | 用户本人拥有 Draft 产品与领域规格的最终签署权。 | `G-AUTH` |
| Q2 | `/tmp/agents` 仅作过程审计，不是长期 authority。 | `G-AUTH`、`G-MIGRATE` |
| Q3 | 采用 D0、D1、P0、I0 和端到端审计阶段边界。 | `G-MIGRATE` |
| Q4 | Draft 永远是临时讨论空间；迁入正式 Wiki 并获签署后才成为规范。 | `G-MIGRATE`、`D-LIFECYCLE` |
| Q5 | `confirmed` 是结论成员资格唯一来源；不存在 `产出` 机器关系。 | `D-STATUS`、`D-CONCLUSIONS` |
| Q6 | `confirmed + challenged_by` 仍属有效结论，质疑必须显式暴露。 | `D-STATUS`、`D-CONCLUSIONS` |
| Q7 | 空根仍保留唯一空 `## 子主题`。 | `D-PAGES` |
| Q8 | 提案与讨论留在 Draft，不进入正式规格网络。 | `G-AUTH`、`D-LIFECYCLE` |
| Q9 | 仅凭 Git Markdown 和普通链接即可读取当前规格。 | `G-AUTH`、`G-ROUTING` |
| Q10 | 规格网络面向整个 Draft 插件，采用根路由加插件作用域。 | `G-ROUTING` |
| Q11 | `docs/design.md` 是退役的第一版规格初稿。 | `G-MIGRATE` |
| Q12 | `conclusions.md` 固定只有已确认和受质疑两个 section。 | `D-CONCLUSIONS` |
| Q13 | `conclusions.md` 是显式生成、可暂时过时的快照。 | `D-CONCLUSIONS`、`G-MIGRATE` |
| Q14 | `docs/design.md` 整份删除，不作 fallback；历史由 Git 保存。 | `G-MIGRATE` |
| Q15 | 正式规格只保存当前有效规范；其他状态留在 Draft。 | `G-AUTH` |
| Q16 | signer 批准具体 packet 和 Git diff，整组合入后才生效。 | `G-MIGRATE` |
| Q17 | 插件规格只保存当前有效行为；未决讨论留在 Draft 或 issue。 | `G-AUTH`、`D-LIFECYCLE` |
| Q18 | 当前树成员资格由权威分支当前树中的页面位置决定。 | `G-AUTH`、`G-ROUTING` |
| Q19 | 根只保留轻量路由，每个插件拥有自己的规格网络。 | `G-ROUTING` |
| Q20 | ADR 只解释理由，不能覆盖规格。 | `G-AUTH` |
| Q21 | 首次迁移不等待生成器，从经核验来源提取 packet。 | `G-MIGRATE`、`G-AUTH` |
| Q22 | 根 `CONTEXT-MAP.md`、插件 `CONTEXT.md`、规格入口和 ADR 固定布局。 | `G-ROUTING` |
| Q23 | 仅排除 design、discussion、review notes、plan；其他来源必须验证。 | `G-MIGRATE` |
| Q24 | 迁移单位是行为合同；显式取代只覆盖点名范围，冲突需 signer 裁决。 | `G-AUTH`、`G-MIGRATE` |
| Q25 | `.scratch` 长期保留；tracker 状态与完成 resolution 正交。 | `G-FEATURE` |
| Q26 | 当前 Wiki → feature delta → child Agent Brief，子层只能缩小范围。 | `G-FEATURE` |
| Q27 | legacy feature 缺字段或基线无效时不得派遣，先归一化并保留诊断。 | `G-FEATURE` |
| Q28 | provenance 使用稳定 commit/path/heading locator，不能引用临时最新文件。 | `G-AUTH`、`G-MIGRATE` |
| Q29 | 结论双 section、排序、关系校验和显式生成规则固定。 | `D-CONCLUSIONS` |
| Q30 | clean break：不识别、升级、双读或迁移旧结论形状。 | `D-CONCLUSIONS`、`P-IDENTITY` |
| Q31-A | `move SOURCE... TARGET` 支持多源和目录来源，映射必须原子确定。 | `D-MOVE` |
| Q31-B | 页面纳管只负责路径与链接一致性，协议欠账由 Check 暴露。 | `D-MOVE`、`D-QUERY` |
| Q31-C | 结构命令维护可证明的链接路径映射；快照集合仍只由生成重算。 | `D-MOVE`、`D-CONCLUSIONS` |
| Q32 | runtime foundation 是 runtime v1 唯一目标合同；旧 runtime spec 完整 superseded。 | `R-*`、`G-FEATURE` |
| Q33 | 目录 move 只移动 Markdown，非 Markdown 原地保留，输出保持窄。 | `D-MOVE`、`D-QUERY` |
| Q34 | promote 只选同父 sibling，保持原导航顺序，跨父先 move。 | `D-PROMOTE` |
| Q35 | `pages/index.md` 保留但非法；根 `index.md` 是唯一根级 index。 | `D-PAGES`、`D-MOVE`、`D-QUERY` |
| S1 | `conclusions.md` 只允许固定 parent；可变关系只能由普通页面承担。 | `D-STATUS`、`D-CONCLUSIONS` |

## 来源

- 原始签署清单：`/tmp/agents/runtime-domain-contract-review/signed-decisions-q1-q35.md`（仅作为创建本证据时的输入）
- 迁移目标与取代范围：`.scratch/draft-authority-spec-migration/migration-evidence.md`
