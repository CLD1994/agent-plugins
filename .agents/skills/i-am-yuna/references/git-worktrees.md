# Git worktree 隔离与清理

多个写入者会接触同一份可变状态，或写入任务需要独立起始基线时阅读。只读任务和单一写入者不因使用 subagent 自动增加 worktree。

## 建立隔离工作区

优先使用用户或项目已经规定的位置；没有约定时，把 linked worktree 建在 Git 主工作区的 `.worktrees/<任务名>/`。当前目录本身是 linked worktree 时，用 `git worktree list --porcelain` 找到主工作区，不根据路径外观猜测。所有 worktree 都是主工作区 `.worktrees/` 的直接子目录，不在 linked worktree 内继续嵌套。

每个 worktree 在自己的 `tmp/agents/` 中保存任务说明、可选消息、checkpoint 和报告，使任务记录与实际输入及产物归属同一工作区。文件引用遵循[调度记录与恢复](dispatch-records.md)中的路径规则。

创建前，用 `git check-ignore` 确认候选 `.worktrees/<任务名>/` 和主工作区 `tmp/agents/<会话名>/` 会被项目规则忽略；创建后，再确认目标 worktree 的 `tmp/agents/<会话名>/` 被忽略。没有等价规则时，向用户申请加入：

```gitignore
/.worktrees/
/tmp/
```

用户批准后，先让忽略规则进入本次固定基线，再创建 worktree；用户不批准时，请用户指定已经被忽略的位置。发起调度的 agent 先在目标工作区写好任务说明，再联系 subagent。

需要隔离写入但没有 Git 时，先说明采用 Git 会带来的安装或初始化、忽略规则和首个提交变化，并请用户决定。用户拒绝 Git 后，列出当前环境实际可用的隔离方案及影响，由用户选择；没有获准且可靠的隔离方式时，不并行安排会写入同一状态的任务。

## 生成清理指引

Git worktree、对应分支和 `tmp/agents/<会话名>/` 默认由人类决定是否删除。agent 只检查状态，并为每个命名资源给出：路径或分支、一句状态概述、查看详情的命令，以及确认安全时可执行的普通清理命令。

常用检查和清理命令如下，实际指引必须替换为已经核对过的具体路径与分支：

```bash
git worktree list
git -C <工作区路径> status --short --branch
git worktree remove <工作区路径>
git branch -d <任务分支>
git worktree prune
rm -r <主工作区/tmp/agents/具体会话名>
```

存在未提交改动、未采用的独有提交或状态不明时，直接警告并保留资源，不默认给出强制删除命令。删除 worktree 也会删除其中未跟踪的任务记录，指引应提醒这一点。

用户明确要求 agent 代为清理命名 worktree、对应分支或会话记录时，以该要求为准。执行前解析并核对准确目标；只有用户明确确认丢弃已经指出的具体内容后，才使用 `--force`、`-D` 或等价操作。优先使用 `git worktree remove` 和 Git 的分支命令，不用递归删除代替 Git 的工作区管理。
