# 确定 v1 隔离开发与一次性切换边界

Type: prototype

Blocked by: None

## Question

v1 在不修改旧生产模块和旧 CLI 路由的阶段应通过什么 namespace、export graph 和测试入口提供目标公开 Interface？`Lifecycle`、`OperationRuntime`、`RuntimeFactory` 与内部 composer 怎样共存，才能让每个新增切片可通过目标边界验证，并在最终 cutover 中一次切换而不保留 facade、re-export、dual-read 或占位成功？
