# 根锁后续：公共恢复结果与历史验证证据

Status: ready-for-human
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
Parent: .scratch/draft-operation-runtime-foundation/issues/04-root-lock-and-recovery.md

## 已确认的检查方式

用户已批准由 Main 对固定提交逐条核验锁取得、传递、检查、副作用和释放；AST 工具只枚举位置和检查漏列，不提供自动正确性证明。既有运行时验收矩阵和不变量保留。

## 公共恢复结果：当前实现与采用边界

候选已实现 completed、rolled_back、cleaned_up、already_settled 的逐项批量结果；候选规格同步字段、排序、关联归属与失败规则。候选尚未进入 main，不能将它声称为已采用的正式规格。

operations.recover 与 CLI 使用的 trash_lifecycle.recover_interrupted_operations 共用批量结果生成。实际恢复执行者标记本次动作；空目录清理显式形成 cleaned_up，已认证无动作才形成 already_settled。CLI 不读取日志或根据内部 phase 推测结果。

一次调用处理本次已认证的待恢复现场，成功信息保留各项真实结果。关联 R/T 由 Restore 所有者执行并形成一项结果，不按日志数量计数。CLI 文本按信息成本原则合并同类结果；JSON 保留有用途的逐项字段，不公开内部路径、phase 或维护编号。

| 已认证现场与执行情况 | 已批准的行为方向 |
| --- | --- |
| 没有待恢复现场，且本次未执行清理 | 无需恢复 already_settled；不产生恢复写入。 |
| 单一恢复单元完整成功 | 返回实际 completed、rolled_back 或 cleaned_up，不统一成 completed。 |
| 多个恢复单元全部成功 | 逐项表达各自实际结果，包括不同结果的组合。 |
| 发现损坏或资格不足，尚未开始执行 | 返回既有对应错误，保留现场，不返回成功。 |
| 第一项执行失败 | 返回既有对应错误，不继续后续恢复单元。 |
| 前面已有耐久成功，后面执行失败 | 返回错误，不返回部分成功作为整体成功；保留已完成的耐久变化，不为凑全有或全无而回滚；重试重新认证剩余现场。 |
| live owner 阻止取得锁 | 保持既有 busy 和零业务写规则。 |

用户已批准按破坏性重构实施结果模型与 CLI，同步更新受影响测试；不以修复所有基线失败为前置条件。候选已完成实现者的源码与回归核验，整体根锁任务仍待独立验收。

## CLI 信息成本原则

人类与 Agent 阅读信息都有成本。输出的每项信息应有明确用途：帮助调用者判断实际结果、识别需要关注的对象、理解失败，或决定下一步动作；没有这些用途的信息应省略。信息可获得不等于需要展示。

- 普通 CLI 文本只保留必要结论和确有必要的行动提示。相同结果可以合并表达，不机械展开每个成功条目；有意义的完成/回退区别和必要的操作定位仍须保留。
- 不默认展示内部 phase、调用步骤、材料路径或重复说明。没有调用方动作时，不添加泛泛建议。
- --json 主要面向 Agent，可适当保留更完整的逐项结构化结果及有用途的标识，支持后续程序判断；不将“更宽松”解释为输出全部内部状态，也不复制可直接推导的重复摘要。
- 两种输出可以有不同详略，但对实际结果和必需动作不得相互矛盾；错误不能因压缩而被隐藏为成功。

具体字段和文本分组规则在规格中按场景确定，不以展示现有内部对象为设计起点。

## 历史验证证据：已批准的验收依据

1. 保留“历史失败测试原始输入与输出缺失”的事实；旧执行者声明不计为已验证证据，不补造历史。
2. 用户已确认：根据当前已提交代码，重新完成源码核验和回归测试，再判断候选是否合格；它不自动代表候选通过。
3. 对已知缺陷逐项核对回归测试是否覆盖被违反的不变量。
4. 当前测试绿不能证明旧测试确实失败，也不能替代仍未完成的源码核验、公共结果契约或独立评审。

## 当前检查项

- [x] 完成公共批量恢复结果、简洁 CLI 和正式编号实现。
- [x] 完成实现者的源码核验及单元、集成回归。
- [x] 将候选基于 main 组合并重新提交。
- [ ] 指定并完成独立评审，判断剩余集成失败对采用的影响。
- [ ] 完成采用及合并。

## 验证结果

被测生产与测试版本为 `95bd9a2`，位于候选分支 `task/draft-lock-run-20260909`。该版本已组合主线的 YAML 基础交付，尚未采用。以下为 2026-09-10 的执行结果，本任务后续文档整理不改变被测代码。

核心命令在 `plugins/draft/skills/draft/scripts/runtime` 中执行：

| 检查 | 命令或范围 | 结果 |
| --- | --- | --- |
| 核心单元 | `uv run --locked pytest -q tests/unit` | 1340 通过 |
| 核心非网络集成 | `uv run --locked pytest -q tests/integration -m 'not network'` | 1026 通过、218 失败、8 未选中 |
| 核心专项集成 | `tests/integration/` 下的 test_root_lock.py、test_io_cleanup.py、test_recovery_results.py、test_cli.py、test_frontmatter_diagnostics.py | 176 通过 |
| 核心静态检查 | `uv run --locked ruff check .` 与 `uv run --locked pyright` | 通过 |
| 核心格式 | `uv run --locked ruff format --check .` | 未改的 test_distribution_contract.py 有格式差异 |

适配层在仓库根使用同一 runtime 项目分别运行：`uv run --project plugins/draft/skills/draft/scripts/runtime --locked pytest -q plugins/draft/scripts/tests/unit` 为 191 通过；将测试路径换为 `plugins/draft/scripts/tests/integration` 并加 `-m 'not network'` 为 318 通过、1 未选中。`bash plugins/draft/scripts/check-adapters.sh` 通过。

与组合前 `bb0d58d` 的完整非网络集成比较，218 个失败节点相同，没有新增或消失。比较只证明失败节点集合一致，不证明失败原因相同或全部与本任务无关。已知待核对范围包括旧持久锁及返回后仍持锁的断言、缺少 `lock` 参数的测试替身，以及其他消费者的恢复协议测试。独立评审需要判断哪些属于本次必须完成的验收，不能仅凭“无新增失败”放行。

## Comments

2026-09-10：确认以当前已提交代码的源码核验与回归作为验收依据，历史缺失的失败测试输入不补造。确认公共结果表达本次实际动作，并按调用者的信息用途压缩 CLI 输出；公开编号输入与输出统一为正式 UUID v4。完整结果合同随候选提交 `510bdca` 保存。

实现者已核对根锁取得、传递、检查、副作用与释放的调用链，但这不构成独立评审。当前需要用户指定独立评审执行者与调度方式。
