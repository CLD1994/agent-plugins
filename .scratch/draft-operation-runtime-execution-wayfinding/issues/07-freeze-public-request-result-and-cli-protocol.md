# 冻结公共 request/result 与 CLI 机器协议

Type: prototype

Blocked by: 01 形成 Draft v1 领域行为合同；02 确定 v1 隔离开发与一次性切换边界

## Question

`AbsoluteRoot`、`RootState`、所有封闭 request union、`Lifecycle.initialize`、`OperationRuntime` 六方法、DryRun plan/outcome、Restore 独立错误、warning、notification、CLI lexical input、JSON envelope 与 exit code 的精确类型代数是什么？每个 tag 有哪些必需字段、禁止字段、未知值规则、排序和序列化边界，且怎样保持 CLI 只作 root 选择、request 构造和结果投影？
