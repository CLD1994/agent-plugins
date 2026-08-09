# Draft Agent CLI 能力计划 — 实施终审报告

对应计划：`docs/superpowers/plans/2026-08-10-draft-agent-cli-capabilities.md`
对应规格：`docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md`
实施分支：`draft-agent-cli-capabilities`（已合并回 `main`）
报告日期：2026-08-10

---

## 1. 提交清单（按可独立审查行为组织）

从基线 `main@758ac11` 起，全部 15 个提交按行为分组：

**任务 1：稳定状态/关系词汇**（`9fca61c` refactor(draft): stabilize status and relation codes）
- `vocabulary.py` 成为 `StatusCode`（pending/proposed/confirmed/superseded/closed）、
  `RelationCode`（parent/depends_on/supersedes/superseded_by/challenges/challenged_by）、
  `EditableRelationCode`、`ConclusionsState` 与中文标签映射的唯一权威来源。

**任务 2：正文资源目标建模**（`19279ca` feat(draft): model body resource targets）
- `resources.py`：`ResourceKind`/`ResourceScope`/`TargetState`/`HostOS`/`ResourceIdentity`/
  `ResolvedResource`/`ResourceReference`，词法命名空间分类（`index.md`/`conclusions.md`/
  `pages/**/*.md` = page 命名空间），缺失目标统一为 `target_state: missing`，
  可注入目标探针。

**任务 3：移动保留资源目标**（`a8bee17` feat(draft): preserve resource targets across moves）
- `rebase_body_destination` 与结构操作数据流，使 `move` 不依赖目标存在性重写正文目标。

**任务 4：集中静默生命周期前置推进**（`0da797e` refactor(draft): centralize silent lifecycle preflight）
- 单次捕获 `now`，一次传给前置推进、过滤与 `as_of` 查询；`_registered_root_for` 只读
  资格判定，无效标记不推进。

**任务 5：紧凑查询命令**（`4a2f98d` feat(draft): add compact query commands）
- `query page/links/backlinks/relations/status/recoverable` + 统一分页与 `--count-only`；
  `query.py` 与 `cli_results.py`（`ResultType` 22 值、分页信封）成为权威来源。

**任务 6：语义 PlanEffect 与预演**（`2a55827` feat(draft): expose semantic dry-run plans）
- `plan_effects.py`（`PlanAction`/`PlanEffect`/`public_changed_paths`）；
  为 `move/promote/collapse/trash` 增加 `--dry-run`/`--limit`/`--offset`/`--count-only`。

**任务 7：结论所有权标记与惰性初始化**（`edaffad` feat(draft): conclusions ownership marker and lazy init）
- 标记扩展为 `{"format_version":1,"conclusions_state":"not_generated"}`；`init` 不再创建
  `conclusions.md`；`checker` 按所有权决定 `conclusions.md` 是否必需。

**任务 8：按需生成结论**（`7e5f152` feat(draft): generate conclusions on demand）
- `generate-conclusions ROOT [--dry-run]`；`conclusions.py` 固定机器形状；
  每次实际生成写入 `proposed`，绝不自动确认。

**任务 9：结构操作允许过时结论列表**（`5813874` feat(draft): allow stale conclusions list in structure ops）
- `_conclusions_owned_generated` + `skip_conclusions_links`，生成态下结论页链接不阻塞
  结构写命令；检查器对生成态结论页过滤链接问题。

**任务 10：原子叶子页创建**（`f6ec576` feat(draft): add atomic page creation）
- `page_create.py`；`create` 命令与 `insert_navigation_before_conclusions`。

**任务 11：校验状态变更与幂等关系变更**（`75b4d60` + `955e8f4`）
- `page_metadata.py`：`status set` 校验最终图不变量；`relation add/remove` 幂等补齐/
  清理半对关系，只接受 `EditableRelationCode`。

**写命令正式 JSON 精简**（`9ca8017` fix(draft): 写命令正式 JSON 只暴露公开事实）
- 任务 11 门禁期间发现任务 6 步骤 4 的载荷精简未完整落地，作为独立修复提交完成：
  成功 JSON 只保留 `result_type`/业务事实/`changed_paths`，移除 `phase`、普通
  `operation_id`、`warnings` 与隐式 `ok: true`；`trash` 保留 `operation_id` 与
  `restore_expires_at`；`restore` 保留本次恢复操作的 `operation_id`。

**文档发布**（`06b523d`、`8772c5f` docs(draft): publish agent cli workflow）
- 修订后的计划与批准规格，与最终行为一致。

### 偏离建议任务边界的说明

- 任务 1–11 的模块划分、函数签名与提交拆分属于计划标注的"建议"，实施时按真实数据流
  调整（如 `query.py`、`resources.py` 边界与计划预估略有出入），行为契约未变。
- 结论页结构校验采用"作为普通受管理页面经正常解析验证"的路径，没有为结论页形状
  编写独立谓词；这是现有解析架构的自然落点，见第 11 节假设。
- 载荷精简中发现并修复了计划步骤 4 未完全闭合的 `ok: true` 残留，作为独立 commit 交付。

## 2. 最终 `git status` 与基线比较

基线（实施前）：`main@758ac11`，工作区干净。

最终：`main@8772c5f`，工作区干净（`git status --short` 无输出），`git diff --check` 退出 0。
新增 8 个生产模块（`cli_results.py`、`conclusions.py`、`page_create.py`、
`page_metadata.py`、`plan_effects.py`、`query.py`、`resources.py`、`vocabulary.py`）
与 9 个测试模块，总变更 +6122/−1038 行。

## 3. 任务 11 门禁结果（合并后于 `main` 复跑）

| 门禁 | 结果 |
| --- | --- |
| 核心单元测试 | 384 passed |
| 核心集成测试（非联网） | 309 passed, 8 deselected |
| 核心集成测试（联网） | 8 passed |
| 适配层单元测试 | 191 passed |
| 适配层集成测试（非联网） | 316 passed, 1 deselected |
| 适配层集成测试（联网） | 1 passed |
| 核心 Pyright（strict） | 0 errors, 0 warnings |
| 适配层 Pyright（strict） | 0 errors, 0 warnings |
| Ruff check（核心 + 适配层） | All checks passed |
| Ruff format --check（核心 + 适配层） | 91 files already formatted |
| `uv lock --check` | Resolved 18 packages |

核心与适配层测试树分开收集，无混淆。

## 4. 新增查询与写命令的命令/结果矩阵

人类输出（`--json` 缺省时的简短中文）与 JSON 信封（`result_type` + 业务事实）：

| 命令 | 人类输出 | JSON `result_type` | JSON 关键业务字段 |
| --- | --- | --- | --- |
| `query page` | `路径 标题 状态码` | `query.page` | `path/title/status/parent/child_count/link_count/backlink_count/outgoing_relation_count/incoming_relation_count` |
| `query links` | `链接 N 项` | `query.links` | `items[]/total/offset/limit/has_more/next_offset`（`target/kind/scope/target_state`） |
| `query backlinks` | `反向链接 N 项` | `query.backlinks` | 同上 |
| `query relations` | `关系 N 项` | `query.relations` | 同上 |
| `query status` | `状态 N 项` | `query.status` | 同上 |
| `query recoverable` | `可恢复 N 项` | `query.recoverable` | `as_of` + 分页信封（`operation_id/page/restore_expires_at`） |
| `create` | `已创建 <page>` | `create` | `changed_paths` |
| `status set` | `已将 <page> 状态设为 <code>` | `status.set` | `status` + `changed_paths` |
| `relation add/remove` | `已添加/移除 <code> <source> → <target>` | `relation.add`/`relation.remove` | `relation/source/target` + `changed_paths` |
| `generate-conclusions` | `结论生成：已生成/无已确认页面` | `generate-conclusions` | `generated`/`reason?` + `changed_paths` |
| `move/promote/collapse` | `已移动/升格/降级 …` | `move/promote/collapse` | `changed_paths`（无 `ok`） |
| `trash` | `已回收 <page>（<phase>）` | `trash` | `operation_id/restore_expires_at` + `changed_paths`（无 `phase/package/purge_eligible_at/warnings/ok`） |
| `restore` | `已恢复操作 <id> 的页面` | `restore` | `operation_id` + `changed_paths`（成功无 `ok`；回滚 `ok=false`、退出码 3） |
| `recover` | `恢复完成：N 项结果` | `recover` | 批量信封 + `changed_paths`（成功无 `ok`） |
| `check` | 检查报告 | `check` | 保留 `ok`（规格 §14 例外，退出码 1 含有效报告） |
| 错误信封 | — | `error` | `ok=false` + `error{code,severity,paths,details}` |

## 5. 预演效果与正式执行公开变化路径一致

- `tests/integration/test_cli.py::test_dry_run_move_does_not_write_and_matches_formal`：
  记录快照 → 预演 `move` → 断言业务文件/日志/回收包零变化 → 正式执行 →
  断言 `changed_paths` 与完整未分页预演推导路径一致。
- 同一文件 `test_dry_run_trash_paths_never_include_dot_draft` 覆盖 `trash` 正式/预演
  双向 `changed_paths` 均不含 `.draft/`。
- `test_conclusions.py`、`test_page_commands.py` 覆盖 `generate-conclusions`/`create`/
  `status`/`relation` 的预演与正式一致性。
- 单元层 `test_plan_effects.py::test_public_changed_paths_deduplicates_in_order` 验证
  `public_changed_paths` 保持效果顺序并排除内部变更。

## 6. 内部 mutation 从不进入公开 `changed_paths`

- 正式结果边界接收计划的公开效果：`OperationResult`/`TrashResult`/结论生成的
  `changed_paths` 均来自 `public_changed_paths(plan.effects)` 或等价公开结果模型，
  而非 journal mutation。
- `test_dry_run_trash_paths_never_include_dot_draft` 显式断言正式 `trash` 结果不含
  `.draft/` 路径（覆盖公开页面、父导航、回收包元数据与 payload 同时变化的场景）。
- `test_conclusions.py` 断言首次生成只报告 `conclusions.md`/`index.md`，`generate-conclusions`
  的 `.draft/draft.json` 写入从不进入结果。
- 结论来自公开效果数据流而非前缀过滤；前缀过滤仅作为防御断言存在。

## 7. `--count-only`、`recoverable` 时间基准、预演写锁边界

- `test_cli.py::test_dry_run_count_only_hides_items_keeps_metadata`：`--count-only` 只清空
  `items`，`total/offset/limit/has_more/next_offset` 与正常预演完全一致。
- `test_queries.py::test_query_recoverable_lists_only_in_window`：`query recoverable`
  输出 `as_of`，窗口过滤正确；单次 `now` 同时传给前置推进、过滤与序列化。
- `test_cli.py::test_dry_run_with_existing_lock_fails_recovery_required`：既有业务写锁
  下预演以 `operation.recovery_required`、退出码 3 整体失败，不返回部分 `effects`，
  不忽略/接管/恢复锁。

## 8. 全预演支持 `--count-only`、正式拒绝分页、无登记标记不推进、严格现存页面 `page.not_found`

- `test_cli.py::test_formal_execution_rejects_pagination_options`：正式执行携带
  `--limit` 时退出码 2、`cli.pagination_requires_dry_run`，且无业务副作用。
- `test_cli.py::test_candidate_check_rejects_internal_state_without_mutation`：
  `--candidate` 下残留 `.draft/`（空/操作日志/回收包三变体）以 `candidate.internal_state`
  拒绝且零修改；`_registered_root_for` 对无有效正式登记标记的根返回 `None`，不推进。
- `test_page_commands.py::test_status_set_missing_page_is_page_not_found` 与
  `test_queries.py::test_query_page_missing_page_is_page_not_found_exit_two`：
  严格现存页面解析（`_page_operand`）统一返回 `page.not_found` → 退出码 2。

## 9. 机器结论允许过时而不阻塞结构写命令，重新生成后收敛

- 机制：`structure.py::_conclusions_owned_generated` 读取所有权状态，
  `_render_pages(skip_conclusions_links=conclusions_generated)` 在生成态跳过结论页
  正文链接；`checker.py::_conclusions_required` 只在 `generated` 时要求
  `conclusions.md` 存在。
- 证据：`test_page_commands.py::test_create_inserts_before_conclusions_link`（生成态
  结论页存在时 `create` 正常完成并保持导航顺序）、
  `test_conclusions.py::test_generate_conclusions_rebuilds_damaged_page`（损坏结论页
  重新生成后 `changed_paths` 含 `conclusions.md`，`check_draft` 零错误，收敛）、
  `test_checker.py::test_not_generated_absent_conclusions_is_not_missing_file` 与
  `test_missing_required_files`（not_generated 缺页不报错、generated 缺页报错）。
- 结构单元测试（`test_structure.py`）验证计划在结论页不变时不产生其 mutation。

## 10. 稳定词汇、结果类型与预演操作代码均从生产类型派生

- `test_distribution_contract.py`（核心 AST 契约）：`_vocabulary_literal_offenders` 扫描
  生产源码，`StatusCode`/`RelationCode` 值只由 `vocabulary.py` 拥有；
  `_cli_result_literal_offenders` 以"第二份完整清单"规则（容器内 ≥3 个不同值）保证
  `ResultType`/`DryRunOperation` 只由 `cli_results.py` 拥有；诊断码与分支判别值
  同理受 AST 契约保护。测试中的精确断言属测试预期，不参与扫描。
- 适配层分发冒烟 `test_distribution_smoke.py` 验证插件分发只读契约。

## 11. 假设、残留项与外部失败

**已确认的偏离/假设：**

1. 结论页固定形状由 `conclusions.py` 作为权威来源，但"形状是否被破坏"的验证走的是
   普通受管理页面解析路径，而非独立形状谓词；与"Markdown 为单一事实来源"的架构一致。
2. 载荷精简（含移除 `ok: true`）是任务 6 步骤 4 契约的补全，独立提交于 `9ca8017`；
   适配层 `bind`/`unbind` 命令属于适配层自有契约，不在本次精简范围。
3. 计划任务的模块划分与函数签名按真实数据流作了必要调整，均未改变行为契约。

**外部失败：**

- 本环境无法访问 `origin`（`git fetch` 报 GnuTLS 网络错误），因此完成方式为本地合并
  （`main` fast-forward 到 `8772c5f`），未推送、未创建 PR。功能分支
  `draft-agent-cli-capabilities` 保留在本地以便审计与回滚。

**门禁状态：** 第 3 节全部门禁于合并后的 `main` 上通过，工作区干净。全部完成。
