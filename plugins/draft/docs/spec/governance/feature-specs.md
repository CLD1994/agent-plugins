# Feature spec 与 tracker

## 行为合同

`.scratch/<feature>/spec.md` 是相对 `Base-Spec` 的 feature delta，不是当前行为。可派遣条件为：`Status` 映射到 `ready-for-agent`、`Tracker-State: open`、`Base-Spec` 仍有效且 `Affects` 非空可解析。Agent Brief 只能缩小父规格范围，不能改变父规格行为。

完成时保留 feature 目录，使用 `Tracker-State: closed`、`Resolution: applied|rejected|superseded` 和必要的 `Promoted-to`；`applied` 不是 `Status` 的第六种值。

## 失败与恢复

基线过期、影响范围未解析或状态非法时不得派遣，改为 `needs-triage` 并保留诊断。与当前 Wiki 冲突时返回 `needs-triage`，不能自行选语义。

## 来源

- `33aeaec60a34833ce8b686bfdd7e50b8dc9932d0:.scratch/draft-authority-spec-migration/signed-decisions-transcript.md#已签署决定迁移摘要`
