# 插件与入口

## 行为合同

插件身份固定为 `draft`；skill、CLI 与 marketplace manifest 使用同一身份和 clean break 规则。核心与插件适配层保持单向依赖，入口、bridge、binding、SessionStart 和失败投影各自有明确责任边界。

## 失败与恢复

入口不能绕过核心协议或把适配层错误伪装为领域成功；依赖方向违规和机器结果字段不一致时拒绝发布。

## 来源

- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-07-30-draft-plugin-manifests-and-path-contract-design.md#插件身份`
- `80508340458f393435ae67669656141ec411ba1b:docs/superpowers/specs/2026-08-05-draft-core-plugin-separation-design.md#核心与插件边界`
