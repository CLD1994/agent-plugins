# D6：维护与检查

> 状态：`已确认`
>
> 产出：[结论汇总](conclusions.md)
>
> 父主题：[递归讨论草稿方法论与 Skill 设计](../2026-07-18-recursive-discussion-notebook-skill.tmp.md)

## 子主题

- [D6.1 Agent 与工具的职责边界](06-1-agent-tool-boundary.md)
- [D6.2 维护触发与压缩前子 Agent](06-2-maintenance-triggers-and-hooks.md)
- [D6.3 自动修改边界](06-3-write-boundary.md)

## 当前结论摘要

- 工具检查可以机械证明的文件事实；Agent 检查讨论含义；用户掌握语义确认权。
- 重要变化立即局部维护；结构变化触发机械检查；压缩前钩子优先唤起专用维护子 Agent；关键节点才进行整体健康检查。
- 检查工具保持只读；维护 Agent 只依据明确事实修改临时草稿。有歧义就留下审查提醒，不猜测语义。

## 下一步

D6 已关闭。具体钩子接口、检查脚本命令和平台适配属于后续 skill 形态与实现设计，不在本页提前决定。
