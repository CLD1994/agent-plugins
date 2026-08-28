# 权威与成员资格

## 行为合同

- 当前规格树由仓库根 `CONTEXT-MAP.md` 路由到本插件 `docs/spec/index.md`；位于该树中的页面即当前有效，不使用冗余 `Status: current`。
- 页面正文是行为唯一权威；ADR 只解释取舍，不能补出或覆盖行为。
- 来源按行为合同逐项取得资格。显式 `supersedes` 只覆盖点名范围；日期、目录和实现现状不产生 precedence。
- signer 只通过批准确切 migration packet 和 Git diff 授予效力；合入权威分支后才生效。
- 工具、缓存、索引、`/tmp/agents` 和旧 dated spec 都不是读取当前规格的必要条件。

## 失败与恢复

来源角色、资格、provenance 或取代范围无法确定时，迁移单元保持 `unresolved` 并阻止切换；实现与测试只能报告 conformance gap。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
- `80508340458f393435ae67669656141ec411ba1b:CONTEXT.md`（词汇边界）
