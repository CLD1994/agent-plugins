# Runtime 模块与测试拆分执行计划

## 0. 目标、基线与证据

本计划只定义无行为改变的机械重构。固定基线为当前 `HEAD` `5c6b28d7801df498f1e3a8ef1417ac978516d1fd`；工作树有 20 个已修改 runtime 路径（生产 9、测试 11），不得把它们视为已审阅的单一功能提交。`operations.py` 7406 行、`checker.py` 1483 行、`trash.py` 1477 行、`trash_lifecycle.py` 1100 行、`trash_package.py` 1421 行，另有 `link_index_maintenance.py` 1019 行。

已确认契约来自 `CONTEXT.md`、协调恢复 `spec.md`、Issue 01/03/05、participant-preflight 与 round-3 计划，以及 2026-08-26 round 10/11/12 评审记录：

- 未持锁 discovery 只返回候选 ID 和稳定分类；所有写、删、清理、handoff、解关联和 release 必须在 matching lock 内由当前全图事实和整组 `RecoveryPreflight` 授权。
- Issue 03 的 R/T relation、restore owner、participant closure、唯一 action 是唯一授权来源；不得从 reverse claim、旧 snapshot、局部 `RestoreLinkState` 或另一个 resolver 重新推断。
- current 与 `.pending` 是独立授权投影；raw/type、identity、kind、transition、成员、摘要和材料闭包必须分别证明，不能取 union 或让 pending 掩盖 current 错误。
- 每个 durable transition、pending 发布、cleanup、解关联、handoff/purge 和 release 后 capability 立即失效，锁内完整重读并重新 preflight。
- journal schema、phase transition、稳定错误码/退出码、link-index revision、FD exactly-once close、主异常优先级和既有公开入口行为均不改变。

项目规则（必须满足）：单元测试只测纯函数契约且不访问真实 FS；真实 FS、锁、durability、handoff、符号链接和 close 故障归入 integration；核心 runtime 与插件适配层测试分开运行；模块依赖按实际 import/调用图验证，不能靠文字搜索；不要删除或放宽测试来获得绿色。

实现预估（可调整）：下列新模块名称、符号落点和提交拆分只有在 import 图与测试证明闭合后才固定。任何无法证明责任边界的符号先列为阻塞审计，不移动。

## 1. 目标分层与依赖图

建议在 `src/draft/` 内建立以下包，保持现有 `draft.*` 顶层路径作为 facade：

```text
fs_ports (标准库 os/path FD/no-follow 端口)
  -> journal_model + journal_codec + journal_transition
  -> relation_model (R/T 全图分类、owner/closure/action)
  -> participant_preflight (RecoveryPreflight、材料授权投影)
  -> trash_package_model (package/evidence 纯解析与授权)
  -> recovery_orchestrator (discovery、matching lock、resolver、刷新)
  -> lifecycle_orchestrator (到期推进/handoff，调用 recovery capability)
checker_adapters / cli_adapters -> 上述纯 reader 与公开结果
link_index_authority + link_index_publish -> journal_transition/fs_ports
```

依赖只能向下：`fs_ports` 不导入业务模块；纯模型不调用写入/清理；preflight 不调用 resolver；orchestrator 才能调用 side-effect port；checker/CLI 只能消费公开 reader/result。禁止 `relation_model -> orchestrator`、`participant_preflight -> lifecycle`、`trash_package_model -> checker`、核心 `draft -> plugins/draft/scripts` 等反向依赖。若实际代码显示某一方向无法消除，停止并记录阻塞及调用链。

模块职责和接口（接口名为建议，行为契约固定）：

| 模块 | 唯一职责与公开接口 | 允许依赖/禁止事项 |
| --- | --- | --- |
| `fs_ports.py` | `open_dir_fd`、`open_leaf_no_follow`、`read_fd_bytes`、`fstat_identity`、`close_owned_fd`、`fsync_dir`；封装 no-follow、身份和 close 语义 | 仅 stdlib、`DraftError`/诊断码；禁止 journal/phase/业务决策 |
| `journal_model.py`/`journal_codec.py` | `Journal`、`JournalMutation`、常量、`read_journal`、`read_optional_journal`、`write_journal` 序列化字节 | 复用 `models`；schema 与字节顺序唯一来源，禁止 FS 清理和 R/T 推断 |
| `journal_transition.py` | `validate_journal_kind_phase`、`validate_journal_transition`、`with_phase`、`with_metadata` 等纯转换 | 只读模型；禁止 acquire/release lock、link-index 发布 |
| `relation_model.py` | `RestoreTrashRelation/Action/Ownership`、`classify_restore_trash_relations`、`validate_restore_trash_relations`、`read_restore_trash_relations`、`RestoreTrashRelationFacts` | 只消费 journal reader；全图优先级固定；禁止 pending 发布、局部 resolver |
| `participant_preflight.py` | `RecoveryPreflight`、`build_recovery_preflight`、材料/transition/lock/closure 完整证明 | 只读 `fs_ports`、journal、relation、package authorization；禁止任何写删/handoff/release |
| `trash_package_model.py` | `PackageAuthorization`、`VerifiedTrashPackage`、`CleanupEvidence*`、package/evidence 解析与授权分类 | 纯解析与受控读取；清理函数通过下层 side-effect port，不能成为 preflight 旁路 |
| `recovery_orchestrator.py` | `recover`、`recover_unlocked_operations`、`recover_locked_operation`、空 residue 与 mutation resolver | 唯一 matching-lock 编排和 capability 消费点；禁止把 discovery snapshot 传入写路径 |
| `lifecycle_orchestrator.py` | `advance_trash_lifecycle`、`recover_interrupted_operations`、`_advance_one_locked`、handoff 状态机 | 只能消费 relation/preflight/package；预读错误转换为 `LifecycleAdvanceReport`，禁止直接泄漏 `DraftError` |
| `link_index_authority.py`/`link_index_publish.py` | authority/baseline、scan、stage/publish/rebuild、revision 恢复 | journal transition/fs_ports；禁止持有 R/T owner 或反向调用 recovery |
| `checker_adapters.py` | operation index、residue/R/T/link-index 投影，`check_draft` 组合 | 只读 reader/result；不得执行 recovery 或修复现场 |
| `cli_adapters.py` | CLI 参数、结果 envelope、`LifecycleAdvanceError` 映射 | 只依赖公开 orchestrator；禁止导入 core 私有 helper |

旧模块 `operations.py`、`trash.py`、`trash_lifecycle.py`、`trash_package.py`、`checker.py`、`link_index_maintenance.py` 在整个重构期间保留 facade/re-export，旧 import 路径和 `__all__` 不变。facade 只转发，不复制实现或第二套枚举/错误码。

## 2. 符号迁移清单与证据门

迁移前逐项运行 AST/import/call-site 审计，记录定义行、所有生产调用者、unit/integration 测试引用。下列为当前候选；标记 `F` 的符号必须留在旧 facade 导出，标记 `B` 的符号在新增公开测试前不得移动。

### 2.1 `operations.py`

- 基础协议与时间/摘要：`DurabilityStep`、`DurabilityObserver`、`durability_observer_scope`、`durability_checkpoint`、`validate_link_index_revision`、`MutationDirection`、`MutationRole`、`ArtifactState`、`MutationRecoveryAction`、`_mutation_role`、`sha256_bytes`、`normalize_utc`、`format_utc`、`_parse_utc_field`、`TrashDeadlines`、`has_handoff_failure`、`handoff_failure_metadata`、`sha256_file`。候选 `journal_model`/`fs_ports`；`F` 为外部测试直接 import 的公开名称，`B` 直到 unit 契约测试补齐。
- FS/pending 端口：`replace_file`、`fsync_directory`、`_fsync_dir_fd`、`_pending_name`、`_quarantine_name`、`_publish_pending`、`_create_only_publish`、`_replace_publish`、`_open_parent_dir`、`ensure_directory_chain`、`_pending_decision`、`recover_digest_pending`、`_quarantine_decision`、`recover_quarantine`、`reject_symlink_components`、`classify_node_or_error`、`_name_node`、`_same_name_node`、`_read_name_bytes`、`durable_write`、`acquire_lock`、`read_lock`、`release_lock`。候选 `fs_ports`/`journal_codec`；所有 close/fstat 相关符号 `B`，须先有 integration exactly-once 矩阵。
- FD/context：`_RecoveryContext`、`RecoveryOperationDirectory`、`_RecoveryLeaf`、`_AnchoredLeaf`、`_MissingLeaf`、`anchored_leaf`、`_open_recovery_operations_fd`、`open_recovery_operation_directories`、`_recovery_leaf`、`_read_optional_journal_fd`、`_UnlockedRecoveryDiscovery`、`_discover_unlocked_operation_fd`、`read_unlocked_operation_journal_fd`。候选 `fs_ports`；旧类/函数保留 `F`，直到所有 recovery/lifecycle caller 改用新 owner API。
- Journal/domain：`JournalMutation`、`Journal`、`_journal_to_json`、`_decode_persistent_json`、`_parse_journal_bytes`、`_parse_journal`、`read_journal`、`read_optional_journal`、`operation_journal_path`、`validate_journal_kind_phase`、`validate_journal_transition`、`with_phase`、`with_link_index_state`、`with_observations`、`with_metadata`、`without_metadata`、`_with_applied`、`write_journal`、`resolve_journal_pending`。迁移至 `journal_model/codec/transition`；`Journal`、读写和常量必须 `F`，字节序列化测试未补齐前 `B`。
- R/T 模型：`RestoreTrashRelation`、`RestoreTrashAction`、`restore_trash_action_for_relation`、`RestoreTrashOwnership`、`_restore_forward_id`、`_trash_reverse_id`、`_is_restore_id`、`_is_restore_preclaim_pair`、`_is_restore_post_unclaim_pair`、`_matched_restore_phase_pair`、`classify_restore_trash_relations`、`validate_restore_trash_relations`、`classify_restore_trash_ownership`、`RestoreTrashRelationFacts`、`is_empty_allocation_residue`。迁移至 `relation_model`；枚举和稳定分类码唯一来源，所有现有 import `F`。
- 材料/副作用 resolver：`_validate_plan_paths`、`_check`、`_quarantine_before_then_publish`、`_apply_write`、`_apply_delete`、`_execute_apply_phase`、`_clean_recovery_copies`、`clean_recovery_copies`、`finalize_operation`、`_rollback`、`_finish_forward`、`_apply_one`、`settle_planned_empty_directories`、`settle_link_index_for_operation`、`_finish_operation_published`、`_finish_operation_rebuild_required`、`_validate_and_complete`、`_validate_recovery_material_fd`、`_validate_allocating_recovery_material`、`_validate_locked_journal_node`、`_validate_locked_recovery_material`、`build_recovery_preflight`、`_validate_retained_package_preflight`、`_consume_recovery_preflight`、`_recover_terminal`、`recover_link_index_phase`、`_recover_mutation_artifacts`、`_execute_mutation_artifact_actions`、`_clean_owned_temp_artifacts`、`_recover_locked`、`recover_locked_operation`、`verify_locked_terminal_operation`。迁移至 `participant_preflight` + `recovery_orchestrator` + `link_index_publish`；所有 side-effect caller `B`，必须先建立 capability 测试，禁止先移动后补测试。
- Recovery discovery/public：`_classify_recovery_name`、`_open_recovery_directory`、`_registered_recovery_material`、`_validate_authorized_recovery_tree_fd`、`_UnlockedRecoveryAction`、`_UnlockedRecoveryCandidate`、`_read_recovery_operation_members`、`_verify_residue_member_nodes`、`_raise_nonempty_residue`、`_confirm_empty_operation_directory`、`_try_cleanup_matching_empty_allocation_residue`、`_confirm_empty_allocation_residue`、`_recover_empty_allocation_residue_locked`、`_discover_empty_allocation_residues`、`recover_empty_allocation_residues`、`read_locked_recovery_snapshot`、`read_locked_recovery_journals`、`_effective_recovery_journals`、`_read_effective_recovery_journals`、`_restore_trash_relation_facts`、`read_restore_trash_relation`、`read_restore_trash_relations`、`_classify_unlocked_recovery_candidates`、`_discover_unlocked_recovery_candidates`、`_recover_unlocked_candidate_locked`、`_refresh_unlocked_journals`、`recover_unlocked_operations`、`recover`。迁移至 `recovery_orchestrator`/`relation_model`；三个公开入口 `F`，旧未持锁分类 helper `B` 直到 discovery 只读和竞争回归通过。
- Plan/execute 相关不得随意移动：`preallocate_journal`、`validate_restore_link`、`validate_preallocated_journal`、`execute_plan`、`execute_preallocated_plan`、`_execute_allocated_plan`、`_verify_plan_observations`、`require_plan_operation_id`、`operation_changes_link_index`、`artifact_plan_time_absent`、`mutation_artifact_actions`、`_forward_artifact_actions`、`_rollback_artifact_actions`、`_allocating_has_index_artifacts`、`_plan_time_missing_index`、`adjusted_observation_manifest`、`explained_directory_deltas`、`_classify_observation`、`_write_recovery_decision`、`_verify_observations_for_recovery`、`_recover_adopt`。这些是计划执行与恢复共享边界，先做调用图审计；若无法证明拆分不改变观察/摘要或 `validate_restore_link` 仅非恢复调用，则标 `B`。

### 2.2 `trash.py`

`_read_managed_text`、`_rebase_links`、`_retarget_links`、`_is_hub`、`_blockers`、`trash_plan`、`build_restore_plan`、`_build_restore_plan_impl`、`_restore_observations`、`verify_restore_after_state`、`_verify_restore_after_state_impl`、`_without_navigation`、`_drop_blank_own_subtopic_section`、`_parent_restore_candidates`、`_prove_parent_restore`、`trash_page`、`require_retained_restore_window`、`abandon_unlinked_restore`、`require_no_existing_restore_claim` 属于计划/页面域，可迁移到 `trash_commands.py`，但公开 `trash_plan/build_restore_plan/trash_page` 必须 `F`。

恢复模型与 resolver：`RestoreLinkState`、`RestoreRecoveryAction`、`is_restore_preclaim_bootstrap`、`restore_recovery_decision`、`RestoreRecoveryFacts`、`load_restore_recovery_facts`、`_preflight_restore_recovery`、`restore_recovery_decision_from_facts`、`_restore_recovery_action`、`resolve_restore_execution`、`restore_retained_trash`、`_commit_restore_cleanup`。模型/纯决策迁移 `relation_model`/`recovery_orchestrator`；`resolve_restore_execution`、`restore_retained_trash` 保留 `F`。不得在恢复中重新引入 `validate_restore_link` 或第二套 action 派生；新增 capability/phase 错配单元测试前上述 resolver 标 `B`。

### 2.3 `trash_package.py`

`PackageAuthorization`、`VerifiedTrashPackage`、`parse_package_authorization`、`require_journal_write`、`_require_journal_delete`、`_relative_path`、`_managed_markdown_path`、`_expect_str`、`_reject_symlink`、`_require_regular_file`、`_reject_symlink_components`、`_reject_package_extra_entries`、`_verified_package_observations`、`_parse_manifest`、`load_verified_package`、`_load_verified_package`、`_package_directories`、`_precheck_package_tree`、`_remove_verified_leaf`、`_remove_verified_dir` 迁移至 `trash_package_model`（解析/验证）与 `trash_package_effects`（清理）。`load_verified_package`、`parse_package_authorization` 必须 `F`；清理函数无新 preflight 集成测试前 `B`。

`CleanupEvidenceLeaf`、`CleanupEvidence`、`CleanupEvidenceIntent`、`EvidenceNodeState`、`_CleanupEvidenceRole`、`CleanupEvidenceAction`、`cleanup_evidence_actions`、`cleanup_evidence_protocol_corrupt`、`_keep_evidence_actions`、`_remove_evidence_actions`、`CleanupAuthorization`、`cleanup_authorization`、`_consume_cleanup_preflight`、`_cleanup_evidence_path`、`parse_cleanup_evidence`、`_write_cleanup_evidence`、`classify_cleanup_evidence_artifacts`、`_consume_cleanup_evidence_name`、`_publish_cleanup_evidence_name`、`converge_cleanup_evidence`、`_restore_missing_leaves_from_evidence`、`clean_authoritative_package` 按同一原则拆分；枚举/授权模型 `F`，每个 unlink/publish 函数 `B`。

### 2.4 `trash_lifecycle.py`

生命周期数据与纯转换：`RecoverableItem`、`LifecycleFailure`、`LifecycleAdvanceReport`、`LifecycleAdvanceError`、`with_disposition`、`with_handoff_failure`、`_to_failure`、`_iter_trash_journals`、`_root_from_package`、`_attempt_authorized`、`_attempt_authorized_from_journal`、`reread_matching_trash_journal`。迁移至 `lifecycle_model.py`，公开类型和异常 `F`；`LifecycleAdvanceError` 的机器码映射测试前 `B`。

副作用与编排：`_clean_attempt_tree`、`make_attempt_copy`、`remove_attempt_copy`、`require_no_restore_claim_for_expiry`、`_attempt_handoff_locked`、`_advance_one`、`_advance_one_locked`、`advance_trash_lifecycle`、`_recover_locked_trash`、`_recover_locked_restore`、`prepare_managed_command`、`list_recoverable`、`recover_interrupted_operations`。迁移到 `lifecycle_orchestrator`，公开入口 `F`；必须消费 `RecoveryPreflight`，不能保留旧 `restore_journal` 快照旁路。round-12 已知全图预读 `DraftError` 到 `LifecycleAdvanceReport` 的转换属于 P1，先修复并测试后再移动。

### 2.5 `checker.py` 与 `link_index_maintenance.py`

`checker.py` 的图检查纯函数 `_error`、`_warning`、`_info`、`_review`、`_sort_key`、`_load_issues_in`、`_canonical_cycle`、`_find_cycles`、`_multi_edges`、`_relation_lines`、`_cycle_details`、`_check_relation_duplicates`、`_body_links_to`、`_check_parents`、`_check_dependencies`、`_check_replacements`、`_check_challenges`、`_check_capacity`、`check_graph` 可迁移 `checker_graph.py`。operation/index 投影 `operation_entry_kind`、`_operation_dir_relative`、`InternalDirectoryState`、`InternalDirectoryListing`、`OperationCheckIndex`、`PackageOwnership`、`OperationDirectoryRole`、`package_ownership_decision`、`operation_directory_role`、`_list_internal_directory`、`_stable_child_kind`、`_check_operation_directory_entries`、`_is_valid_operation_id_name`、`_build_operation_index`、`_check_operation_state`、`_trash_package_issue`、`_check_trash_tree`、`_check_trash_retained`、`_check_trash_handoff_pending`、`_deadline_issue` 迁移 `checker_operation_adapter.py`；`check_draft`、`check_link_index_maintenance`、`check_graph` 必须 `F`。所有检查路径必须继续只读。

`link_index_maintenance.py` 的 `LinkIndexAuthority`、`LinkIndexBaseline`、`select_link_index_authority`、`read_link_index_authority`、`load_link_index_baseline`、`next_link_index_revision`、`scan_link_index`、`_list_directory_members`、`_scan_walk_error`、`read_file_no_follow`、`_read_no_follow`、`_verify_root_stable`、`_reject_ancestor_symlinks`、`_ensure_dot_draft`、`_read_artifact_bytes`、`_regular_flag_identity` 迁移 `link_index_authority`；`stage_link_index`、`publish_staged_link_index`、`ensure_rebuild_flag`、`remove_rebuild_flag`、`_unlink_protocol_file`、`cleanup_link_index_staging`、`verify_published_link_index`、`PreparedLinkIndexUpdate`、`LinkIndexMaintenanceResult`、`prepare_link_index_update`、`commit_link_index_update`、`maintain_link_index`、`_finish_rebuild_required`、`_finish_published`、`_in_process_index` 迁移 `link_index_publish`。所有公开名称 `F`；其来源需单独核验，因为当前改动同时触及 recovery phase，不能先拆提交。

## 3. 测试目录拆分与不变量

不得按当前文件行号切割。先为每个新生产模块建立一项独立契约测试，再移动实现；旧测试路径保留兼容导入，最终才改为新目录。建议目录：

```text
tests/unit/recovery/{test_journal_model,test_relation_model,test_participant_preflight,test_fd_ports,test_orchestrator}.py
tests/unit/trash/{test_package_model,test_lifecycle_model}.py
tests/unit/link_index/{test_authority,test_publish}.py
tests/unit/checker/{test_graph,test_operation_adapter}.py
tests/integration/recovery/{test_recover,test_restore_trash,test_lifecycle,test_fd_failures}.py
tests/integration/link_index/{test_maintenance,test_recovery_phase}.py
tests/integration/adapters/{test_checker,test_cli,test_distribution_contract}.py
```

单元测试按公开生产函数一个行为分支验证返回值、异常、状态变化；替身必须实现真实接口（特别是 `os.scandir` 的 context-manager/iterator）。集成测试按真实 FS/lock/journal durability/package/handoff/FD 边界和三个公开 seam：`recover(root)`、`recover_interrupted_operations(root)`、`advance_trash_lifecycle(root, ...)`。保留现有测试中的所有错误码、状态、字节、lock、handoff 计数和重复执行断言；不断言私有调用图。

测试状态矩阵至少包括：raw/type、R forward、graph/phase、多重/错配、current-only/current+pending/bootstrap、普通 allocating、空/非空 residue、lock 竞争和锁后新增成员、pending 出现、package/attempt/evidence 未授权成员、已提交 handoff/purge、第三方 claim、symlink/identity 变化、close/fstat 主异常优先级、重复恢复。任何新增测试缺失时，相关符号标 `B`，不可移动。

## 4. 无行为改变的执行顺序与阶段门

1. **冻结与审计**：记录 `git status --short`、完整 diff、当前测试基线；用 AST/import/call graph 给每个候选符号标注定义、调用者、测试和来源。`link_index_maintenance.py`、`trash_package.py` 的变更来源单独写入审计记录。若来源/责任不能证明，停止该符号移动。
2. **建立空包与 facade**：新增包、`__init__.py`、类型导出和兼容 re-export，不改调用者。运行全 runtime import smoke、现有 unit、ruff、pyright；提交边界只允许新模块空实现和 facade，不能包含行为 hunk。
3. **先搬纯模型**：迁移 journal codec/transition、R/T relation、lifecycle/package dataclass 与纯分类函数。每组先新增单元测试（红→绿），再切换内部 import；旧路径只 re-export。门禁：unit 全量、`git diff --check`、无循环依赖。
4. **抽取 FS ports/FD owner**：迁移 `_RecoveryContext`/directory/leaf 和 pending/no-follow helper；建立 exactly-once close、fstat/主异常 integration 测试。任何 close 语义不明即阻塞，不能 catch-and-continue。
5. **participant preflight**：在 `participant_preflight` 实现唯一 `RecoveryPreflight`，接入所有副作用前门；先写反例测试证明非法 participant 零写零删零 release，再迁移 resolver。每步 durable transition 后强制 reader→preflight 刷新。
6. **恢复编排**：迁移 discovery、matching lock、普通 allocating、R/T restore、terminal cleanup；保持 `recover*` facade。先修复 lifecycle 全图预读错误报告 P1，再运行三个 seam 的竞争/刷新/重复集成测试。
7. **Trash package/evidence 与 lifecycle**：将 package/evidence 纯授权与副作用分离，接入 preflight；迁移 handoff/purge 与 `LifecycleAdvanceReport`。不得重复 handoff 或让 `POST_UNCLAIM_CLEANUP` 绕过 T。
8. **link-index**：在 recovery capability 稳定后拆 authority/publish；保持 revision、staging、degraded/rebuild 语义。link-index 阶段与 recovery 共享 journal transition，但不得互相反向导入。
9. **checker/CLI adapters**：迁移 operation index、R/T/residue 投影和 CLI envelope；核心与适配层分开测试，确保 checker 只读并共享同一 reader。
10. **删除内部实现（最后）**：仅在所有旧调用点、公开 import 和测试路径通过后删除 facade 内重复私有实现；若有外部使用风险则永久保留薄 facade。

每个阶段单独提交，提交前运行受影响 unit、对应 integration、ruff、pyright、distribution gate、`git diff --check`；提交只包含该阶段新模块/测试/兼容导出，不混入行为修复或未审阅用户改动。任一门禁失败先定位根因，不能删测试、放宽配置或暂时复制代码。

## 5. 混合未提交变更的 B0 与提交策略

- B0 是上述 `HEAD` 与当前 20 个修改路径的只读快照；不得重置、覆盖或使用 `git add -p` 按行猜来源。
- 先把每个路径按“协调恢复 / durable link-index / 两者共享 / 用户或来源不明”分类；`operations.py`、`checker.py`、`trash.py`、`trash_lifecycle.py` 的共享 hunk 在隔离 worktree 重构并独立复审后才可提交。
- `diagnostic_codes.py`、`observations.py`、`structure.py` 的小改动仍须跟随其唯一 literal-owner gate 和消费者测试，不因行数少而单独提交。
- `link_index_maintenance.py` 与 `trash_package.py` 当前虽是修改路径，来源必须由 `git log`、规格引用、调用图和测试增量分别核验；证据不足则保留未暂存。
- 计划阶段只提交本计划文件。实现阶段先在隔离 worktree 形成可构建、可测试、可回退的提交，再将审阅后的提交 cherry-pick；主工作树已有用户改动不得混提交。只有当共享 hunk 真正解耦且两组可独立构建/回退时才拆成 link-index 与 recovery 两条提交，否则使用如实描述两项协议的整组提交。

## 6. 完成验收与恢复 Issue 入口

完成拆分后必须证明：新旧 import 图无循环；所有原 `draft.operations`、`draft.trash`、`draft.trash_lifecycle`、`draft.trash_package`、`draft.checker`、`draft.link_index_maintenance` 路径仍可导入；runtime unit/integration（含 network 属性按项目规则分开）、插件适配层测试分开通过；`ruff`、`pyright`、distribution contract/smoke、`git diff --check` 全通过。用静态 import 图工具和运行时 smoke 双重验证，不能只搜文本。

拆分完成并独立复审后，才恢复 Issue 01/05：Issue 01 从 `participant_preflight` 入口接入所有公开 recovery/lifecycle side-effect gate；Issue 05 从 `fs_ports` 的 descriptor owner 接口运行完整 refresh/竞态/close 矩阵。验收再次覆盖 `recover`、`recover_interrupted_operations`、`advance_trash_lifecycle` 的 lock 竞争、当前事实重读、错误报告、零副作用、重复收敛和 handoff 计数。任何符号边界、根级 lifecycle operation ID 归因或材料缺失语义未定义，必须在对应 issue/spec 中先补齐后实现。

禁止事项：先改名/移动再补测试；复制代码以减少行数；新造状态枚举/错误码；在拆分期间顺带修复行为缺陷；以旧 snapshot、局部关系 helper、递归删除或 catch-and-continue 绕过 capability；把测试预期复制实现逻辑。
