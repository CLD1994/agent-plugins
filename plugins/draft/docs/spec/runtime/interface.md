# 运行时接口

## 行为合同

`Lifecycle.initialize` 直接初始化显式 root；`RuntimeFactory.for_root` 只构造绑定到 root 的 `OperationRuntime`。运行时提供 `perform`、`trash`、`restore`、`read`、`maintain` 与 `recover`，构造不执行 I/O，也不缓存跨调用快照。

## 失败与恢复

入口必须遵守 root 绑定和统一 admission；旧或未知 runtime schema 失败关闭，不迁移、不双读。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#2. 对外接口与所有权`
