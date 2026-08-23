# 分诊标签

工程 skills 使用五个标准分诊角色。本地 Markdown issue 通过 `Status:` 字段保存对应字符串。

| Skill 中的角色 | 本仓库的 `Status:` 值 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待报告者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 规格已经完整，可由 Agent 实施 |
| `ready-for-human` | `ready-for-human` | 需要人类实施或决策 |
| `wontfix` | `wontfix` | 当前不会处理 |

当 skill 使用标准角色或要求应用某个分诊标签时，把对应字符串写入 issue 的 `Status:` 字段。
