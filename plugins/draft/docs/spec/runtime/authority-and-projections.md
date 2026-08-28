# 权威与投影

## 行为合同

record、content、active catalog 和 head 是各自唯一 authority；schedule、cache 与其他查询资料是可重建 projection。只有 authority 变更完成父目录 fsync 并记录 matching commit 后，projection 才可被消费。

## 失败与恢复

authority、generation、digest、transition 或 staging 不匹配时报告 `runtime.corrupt` 或 `runtime.durability_failed`，不以 terminal projection 猜测恢复。安全分类的 projection 可重建或降级，不改变 authority。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#7. Authority 与 projection`
