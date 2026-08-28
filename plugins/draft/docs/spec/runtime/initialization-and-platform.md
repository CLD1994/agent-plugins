# 初始化与平台

## 行为合同

初始化认证平台 capability、根路径和持久布局，按 foundation Init 状态机创建 marker 与必要目录；目标写入前必须证明能力和路径安全。三平台适配器各自实现原子发布、no-follow 与 durability 保障。

## 失败与恢复

能力不足、路径异常或耐久写失败时在业务状态生效前失败并保留可诊断现场；marker 可见前使用独立 temporary init gate。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:.scratch/draft-operation-runtime-foundation/spec.md#3. 初始化与平台`
