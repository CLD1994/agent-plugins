# 冻结 HandoffAdapter 与 isolated worker 协议

Type: prototype

Blocked by: 01 形成 Draft v1 领域行为合同；08 综合 PlatformAdapter 可观察合同；09 冻结时间、ID 与确定性排序；10 冻结 canonical encoding 与共享标量

## Question

唯一 external seam `HandoffAdapter` 的方法、输入包、返回值和异常分类是什么？isolated worker 怎样启动、在 30 秒边界终止并证明实际停止，怎样区分 accepted、explicit failed、timeout、crash 与 outcome unknown；root lock 从 authorization 到 worker 停止、outcome/catalog durability 和 cleanup 应覆盖哪些步骤，且怎样保留允许重复投递同一 Trash operation id 的 at-least-once 语义？
