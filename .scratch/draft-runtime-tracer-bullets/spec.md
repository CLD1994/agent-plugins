# Draft runtime 与 YAML 垂直交付票

Status: ready-for-agent
Tracker-State: open
Design-Refs:
  - plugins/draft/docs/spec/runtime/interface.md
  - plugins/draft/docs/spec/runtime/initialization-and-platform.md
  - plugins/draft/docs/spec/runtime/admission-and-effects.md
  - plugins/draft/docs/spec/runtime/authority-and-projections.md
  - plugins/draft/docs/spec/runtime/lifecycle-and-handoff.md
  - plugins/draft/docs/spec/runtime/recovery.md
  - plugins/draft/docs/spec/runtime/errors-and-observability.md
  - plugins/draft/docs/spec/draft/pages-and-navigation.md
  - plugins/draft/docs/spec/draft/status-and-relations.md
  - plugins/draft/docs/spec/draft/page-edits.md
  - plugins/draft/docs/spec/draft/structure.md
  - plugins/draft/docs/spec/draft/resources-and-links.md
  - plugins/draft/docs/spec/draft/conclusions.md
  - plugins/draft/docs/spec/draft/trash-and-restore.md
  - plugins/draft/docs/spec/draft/query-check-and-output.md

## 用途

本目录把现有运行时（runtime）与 YAML 路线拆成一组能从公开入口一路验证到持久结果的交付票。每张票都必须同时说明用户可观察的行为、失败结果、持久副作用和测试证据；不能只交付一个内部模块或一组水平重构。

本页组织路线，实际工作选定独立票据执行，不把整个目录作为单一派遣任务。

编号按依赖顺序排列。`Blocked by` 是开始该票前必须满足的真实条件，不代表被阻塞的票已经完成。现有父任务和规格入口保持不变，本目录不关闭或改写它们。

## 与现有任务的关系

本目录统筹 runtime 与 YAML，各 child ticket 以本页为父任务。与既有任务的关系如下；同一实现沿既有工作记录继续，各票据按自身交付范围验收：

| 本目录票据 | 现有任务中的对应范围 |
| --- | --- |
| [01](issues/01-request-validation.md) | [公共请求校验与拒绝](../draft-operation-runtime-foundation/issues/01-request-validation.md) |
| [02](issues/02-root-lock-admission.md)、[03](issues/03-recovery-cleanup-ownership.md)、[04](issues/04-read-dryrun-maintenance.md) | [根目录排他锁、恢复和维护边界](../draft-operation-runtime-foundation/issues/04-root-lock-and-recovery.md) |
| [05](issues/05-yaml-page-read.md)、[06](issues/06-yaml-navigation-and-conclusions.md)、[07](issues/07-marker-consistent-maintenance.md)、[08](issues/08-yaml-metadata-plan-dryrun.md) | [页面 YAML 读取、消费者和纯计划](../draft-page-frontmatter/issues/01-yaml-page-io.md) |
| [09](issues/09-first-yaml-write.md)、[11](issues/11-first-operation-closure.md) | [首条普通操作的页面写入与提交/恢复决策](../draft-operation-runtime-foundation/issues/03-status-operation-recovery-slices.md) |
| [10](issues/10-initialization-recovery.md) | [初始化及初始化中断恢复的实施边界决策](../draft-operation-runtime-foundation/issues/02-initialization-recovery-slices.md) |
| [12](issues/12-final-integration.md) | 所有已通过切片的最终整合与故障验收 |

执行第 02–04 票时，先核对现有根锁任务的工作目录和未提交候选，继续或收敛那一项工作；不得平行创建第二套根锁实现。执行第 05–08 票时，历史 YAML 和 Markdown 归档只能提供待核对片段，不能替代当前规格或当前候选。单项实现的认领与结果使用同一份工作记录，相关票据链接该记录；各票据按自身完整验收维护状态，不因另一条目完成而自动关闭。

## 面向新读者的术语

- **根目录排他锁**：由操作系统保证同一根目录同一时间只有一个需要写入或清理的进程占用的锁。锁文件是否存在、进程号或修改时间都不能代替这项证明。
- **待恢复现场**：一次写入中留下的操作记录、临时材料或未完成替换。普通入口不能擅自清理，必须由明确的恢复规则处理。
- **预演（DryRun）**：只读取事实并计算将要发生的变化，不写页面、记录、索引或临时材料。
- **唯一事实来源**：真正保存业务状态的记录或页面内容；索引、查询结果和诊断都是从它生成的结果，不能反过来修改事实。
- **状态快照**：在一次读取或维护过程中固定的一组根目录状态和摘要，避免扫描前后混用不同版本的事实。
- **隔离工作目录（worktree）**：同一 Git 仓库中供一项任务独立修改和测试的工作目录。
- **命令行接口（CLI）**：用户通过终端调用的那一层入口。

现行设计规格、变更规格和设计依据的含义见 [Draft 词汇](../../plugins/draft/CONTEXT.md)。任务字段与日常操作见[轻量任务文档](../../docs/agents/issue-tracker.md)。

## 基线与历史归档

新票据开始执行时，应先核对当时的 `main` 是否干净，再从该时点建立隔离工作目录（worktree）。根锁票据继续使用已经建立的专用工作目录；开始工作前记录该目录当时的分支和提交，不把本文件中某次检查得到的提交号当作永久基线。完成后再根据当时的整合检查结果合入 `main`。

每次开始工作时，在本次执行记录中写明实际读取的设计依据版本和生产代码工作基线。设计依据使用 `Design-Refs` 定位，关键内容发生变化后重新核对相关行为及验收。

`archive/2026-09-07-yaml-dirty-main` 和 `archive/2026-09-07-markdown-shared-dirty` 是为保留 2026-09-07 当时未整理的工作区改动而创建的历史归档副本，不是可合并分支。它们的作用是保留原始改动，供后续逐项核对来源和行为；只能在对应垂直票中抽取经过核对的窄片段，补失败测试并重新评审。不得整体 merge、cherry-pick 或在归档分支上继续开发。历史 Markdown 共享层必须先与当前页面事实边界对齐，历史 YAML 写入代码必须等根锁和读取契约闭合后重建。

## 选择执行任务

从上表进入候选票据，读取其状态、前置交付和待决问题，再结合现有工作记录选定执行范围。各票据的依赖满足情况在执行前核对，本页仅维护导航和共同工作安排。

## 行为权威与停止条件

行为、错误、状态和平台保证从 `CONTEXT-MAP.md` 路由下、`Design-Refs` 引用的现行设计规格读取。本目录不登记第二套字段或错误清单。遇到设计规格未定义的状态、需要改变公开协议、无法证明临时材料归属，或归档代码与当前事实冲突时，停止相关票据并报告，不用实现便利自行补语义。
