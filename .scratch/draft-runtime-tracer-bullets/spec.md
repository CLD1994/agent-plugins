# Draft runtime 与 YAML 垂直交付票

Status-Schema: triage
Status: ready-for-agent
Tracker-State: open
Base-Spec: 4cf3b96b7d30e4c3f9d2374b88852e160d35dfa2
Affects:
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
Parent: .scratch/draft-operation-runtime-foundation/spec.md
Scope: feature-delta
Current-Owner:
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
Dispatch: blocked
Dispatch-Diagnostic: 本目录只把已批准路线整理为可独立验收的垂直交付票；行为仍以 Current-Owner 为准，不能把本目录整体作为单一派遣任务。

## 用途

本目录把现有运行时（runtime）与 YAML 路线拆成一组能从公开入口一路验证到持久结果的交付票。每张票都必须同时说明用户可观察的行为、失败结果、持久副作用和测试证据；不能只交付一个内部模块或一组水平重构。

编号按依赖顺序排列。`Blocked by` 是开始该票前必须满足的真实条件，不代表被阻塞的票已经完成。现有父任务和规格入口保持不变，本目录不关闭或改写它们。

## 与现有任务的关系

这些票据是现有任务的可执行拆分，不是第二套规格，也不是要求重新创建重复实现：

| 本目录票据 | 现有任务中的对应范围 |
| --- | --- |
| 01 | 公共请求校验与拒绝 |
| 02–04 | 根目录排他锁、恢复和维护边界 |
| 05–08 | 页面 YAML 读取、消费者和纯计划 |
| 09、11 | 首条普通操作的页面写入与提交/恢复闭环 |
| 10 | 初始化及初始化中断恢复边界 |
| 12 | 所有已通过切片的最终整合与故障验收 |

执行第 02–04 票时，先核对现有根锁任务的工作目录和未提交候选，继续或收敛那一项工作；不得平行创建第二套根锁实现。执行第 05–08 票时，历史 YAML 和 Markdown 归档只能提供待核对片段，不能替代当前规格或当前候选。原有任务仍是状态维护入口，完成票据后再按项目规则更新对应任务状态。

## 面向新读者的术语

- **根目录排他锁**：由操作系统保证同一根目录同一时间只有一个需要写入或清理的进程占用的锁。锁文件是否存在、进程号或修改时间都不能代替这项证明。
- **待恢复现场**：一次写入中留下的操作记录、临时材料或未完成替换。普通入口不能擅自清理，必须由明确的恢复规则处理。
- **预演（DryRun）**：只读取事实并计算将要发生的变化，不写页面、记录、索引或临时材料。
- **唯一事实来源**：真正保存业务状态的记录或页面内容；索引、查询结果和诊断都是从它生成的结果，不能反过来修改事实。
- **状态快照**：在一次读取或维护过程中固定的一组根目录状态和摘要，避免扫描前后混用不同版本的事实。
- **规格负责人页面（owner）**：当前规格中真正定义某项行为、错误和边界的页面；本目录只引用它，不复制或改写它的规则。
- **隔离工作目录（worktree）**：同一 Git 仓库中供一项任务独立修改和测试的工作目录。
- **命令行接口（CLI）**：用户通过终端调用的那一层入口。

## 基线与历史归档

新票据开始执行时，应先核对当时的 `main` 是否干净，再从该时点建立隔离工作目录（worktree）。根锁票据继续使用已经建立的专用工作目录；开始工作前记录该目录当时的分支和提交，不把本文件中某次检查得到的提交号当作永久基线。完成后再根据当时的整合检查结果合入 `main`。

各票据头部的 `Base-Spec` 是已批准行为规格的版本定位，用来确认票据依据哪一版规则；它不是生产代码的工作基线，只有行为规格发生批准变更时才更新。

`archive/2026-09-07-yaml-dirty-main` 和 `archive/2026-09-07-markdown-shared-dirty` 是为保留 2026-09-07 当时未整理的工作区改动而创建的历史归档副本，不是可合并分支。它们的作用是保留原始改动，供后续逐项核对来源和行为；只能在对应垂直票中抽取经过核对的窄片段，补失败测试并重新评审。不得整体 merge、cherry-pick 或在归档分支上继续开发。历史 Markdown 共享层必须先与当前页面事实边界对齐，历史 YAML 写入代码必须等根锁和读取契约闭合后重建。

## 当前可派遣前沿

目前第 01、05 票没有票据级阻塞，可以从当前干净基线开始；第 02 票没有票据级阻塞，但应继续核对现有根锁专用工作目录，不得另起重复实现。第 09、10 票还需要人类确认既定的产品选择；其余票据应等列出的阻塞条件满足后再派遣。

## 行为权威与停止条件

行为、错误、状态和平台保证只从 `CONTEXT-MAP.md` 指向的 Current-Owner（规格负责人）页面读取。本目录不登记第二套字段或错误清单。遇到规格负责人未定义的状态、需要改变公开协议、无法证明临时材料归属，或归档代码与当前事实冲突时，停止相关票据并报告，不用实现便利自行补语义。
