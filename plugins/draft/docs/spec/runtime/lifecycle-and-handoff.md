# 生命周期与 handoff

## 行为合同

普通写、Trash、Restore 和除 Check 外的 read 可触发自动 lifecycle；每次 invocation 最多处理一个 candidate、最多一次 handoff。Recover 不推进 due、不新建 handoff。

## 失败与恢复

成功、cleanup、receipt 和 handoff 结果按 durable audit 记录；failed、unknown、deferred、busy 以可行动 warning 投影。自动生命周期 fatal 时停止业务 projection。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#8. 自动生命周期与结果语义`
- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#9. Trash、Restore 与 handoff`
