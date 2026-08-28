# 权威规格迁移证据包

Source-Commit: 80508340458f393435ae67669656141ec411ba1b
Working-Commit: fc0ba84
Packet-Stage: A (migration-evidence)
Authority: none; 本文件仅保存 provenance（来源追溯）与分类证据，不是行为 authority。

## 目的

本文件是正式迁移合同 M1 阶段的 tracked 产物。它固定来源快照、为每个 tracked 路径分类，并在创建 staged authority 页面前登记迁移单元。删除旧来源后，本文件也不得作为行为规格 fallback。

## 基线核对

- 产品/来源快照按已签署迁移输入固定为 `80508340458f393435ae67669656141ec411ba1b`。
- 当前 HEAD `fc0ba84` 只增加了 `AGENTS.md` 和 `docs/agents/yuna.md`；它们属于 consumer/process（消费者/流程）变更，不作为产品行为 authority，但必须在消费者闭合阶段核对。
- 创建本证据包时工作树没有其他未提交修改。

## 来源资格规则

- 以下内容排除在 authority 验证之外：`docs/design.md`、`docs/discussion/**`、review notes、plan 以及 `/tmp/agents/**`。
- 已签署决定 Q1–Q35 与 S1 仅是本次迁移输入；它们可以授权有明确范围的覆盖，但自身不会成为 authority。
- runtime foundation `.scratch/draft-operation-runtime-foundation/spec.md` 是已签署的 runtime v1 目标合同。
- durable-link-index 与 coordinated-recovery 两份规格依据 Q32 标记为 superseded evidence（已被取代的证据）。
- 日期、目录位置、实现数量和 tracker 文案都不能建立 precedence（优先级）。

## 路径清单

来源快照中共有 `303` 个 tracked 路径；每个路径按 primary role（主要角色）恰好分类一次。

### excluded-evidence (59)

- `.scratch/implementation-plan.md`
- `docs/design.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill.tmp.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/00-background-and-evidence.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/01-scope-and-authority.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/02-page-model.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/03-recursive-splitting.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/04-relationship-types.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/05-links-and-backlinks.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/06-1-agent-tool-boundary.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/06-2-maintenance-triggers-and-hooks.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/06-3-write-boundary.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/06-maintenance.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/07-validation.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/08-project-and-platform-structure.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/09-python-uv-and-bootstrap.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-1-reusable-maintenance-session.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-10-cli-mcp-shared-core.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-11-task-claim-and-takeover.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-12-compaction-maintenance-scheduling.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-13-main-agent-after-compaction.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-14-deterministic-precompact-capture.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-15-postcompact-context-cost.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-16-skill-reactivation.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-17-new-session-binding.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-18-semantic-maintenance-checkpoint.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-19-precompact-necessity.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-2-codex-explicit-compaction.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-20-mcp-and-maintenance-agent-scope.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-3-wait-and-release.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-4-permissions-and-notebook-root.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-5-structural-operations.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-6-structural-operation-recovery.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-7-trash-authority.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-8-trash-handoff-failure.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/10-9-file-capability-isolation.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/10-maintenance-agent-launch/index.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/11-1-capacity-threshold.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/11-checker-scope.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-1-required-fields.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-10-empty-hub-collapse.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-2-1-checker-workflow-by-label.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-2-canonical-relation-direction.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-3-multiple-relation-targets.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-4-unknown-labels.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-5-link-target-scope.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-6-output-page-cardinality.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-7-dependency-cycles.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-8-parent-rooted-tree.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-9-recursive-hub-layout.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/12-minimal-structure-syntax.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/13-managed-notebook-root/13-1-format-version-compatibility.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/13-managed-notebook-root/13-2-init-adopt-migrate-boundaries.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/13-managed-notebook-root/13-3-user-approval-plan-binding.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/13-managed-notebook-root/index.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/conclusions.md`
- `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/discussion-log.md`
- `docs/review-notes/draft-v1-domain-contract-spec/2026-08-28-round-1.md`
- `docs/review-notes/draft-v1-domain-contract-standards/2026-08-28-round-1.md`

### signed-override (1)

- `.scratch/draft-operation-runtime-foundation/spec.md`

### authority-candidate (9)

- `.scratch/draft-operation-runtime-foundation/domain-contract.md`
- `docs/superpowers/specs/2026-07-30-draft-plugin-manifests-and-path-contract-design.md`
- `docs/superpowers/specs/2026-07-30-draft-self-contained-skill-runtime-design.md`
- `docs/superpowers/specs/2026-08-04-draft-single-python-launcher-design.md`
- `docs/superpowers/specs/2026-08-05-draft-actionable-check-diagnostics-design.md`
- `docs/superpowers/specs/2026-08-05-draft-core-plugin-separation-design.md`
- `docs/superpowers/specs/2026-08-10-draft-agent-cli-capabilities-design.md`
- `docs/superpowers/specs/2026-08-12-draft-candidate-root-incremental-migration-design.md`
- `docs/superpowers/specs/2026-08-23-draft-move-unmanaged-promotion-design.md`

### feature-delta (2)

- `.scratch/draft-coordinated-recovery-ownership/spec.md`
- `.scratch/draft-durable-link-index-maintenance/spec.md`

### consumer (102)

- `AGENTS.md`
- `docs/agents/domain.md`
- `docs/agents/issue-tracker.md`
- `docs/agents/triage-labels.md`
- `docs/agents/yuna.md`
- `plugins/draft/AGENTS.md`
- `plugins/draft/references/platform-setup.md`
- `plugins/draft/skills/draft/SKILL.md`
- `plugins/draft/skills/draft/references/draft-format.md`
- `plugins/draft/skills/draft/references/llm-wiki.md`
- `plugins/draft/skills/draft/references/migration.md`
- `plugins/draft/skills/draft/scripts/runtime/benchmarks/link_index_maintenance.py`
- `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/__init__.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/__main__.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/checker.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/cli_results.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/conclusions.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostic_codes.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/diagnostics.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/errors.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/graph.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/lifecycle.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/link_index_maintenance.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/markdown.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/move_closure.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/move_operands.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/observations.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/operation_facts.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/operations.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/page_create.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/page_metadata.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/paths.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/plan_effects.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/query.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/resources.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/structure.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_lifecycle.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/trash_package.py`
- `plugins/draft/skills/draft/scripts/runtime/src/draft/vocabulary.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/__init__.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/constraints/minimum-build.txt`
- `plugins/draft/skills/draft/scripts/runtime/tests/constraints/minimum-runtime.txt`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/__init__.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/conftest.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/support.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cache_isolation.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_candidate_migration.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_checker.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_cli.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_conclusions.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_dependency_bounds.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_contract.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_distribution_smoke.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_identity.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_lifecycle.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_link_index_maintenance.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_marker_state_threading.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_move_promotion.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_observations.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operation_facts.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_operations.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_page_commands.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_paths.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_queries.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_structure.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_lifecycle.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/integration/test_trash_package.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/support/__init__.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/support/paths.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/__init__.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_checker.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_cli.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_conclusions.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_diagnostics.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_graph.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_link_index_maintenance.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_markdown.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_closure.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_operands.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_move_planning.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_observations.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_operations.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_create.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_page_metadata.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_paths.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_plan_effects.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_query.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_relation_edit.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_resources.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_structure.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_trash_package.py`
- `plugins/draft/skills/draft/scripts/runtime/tests/unit/test_vocabulary.py`
- `plugins/draft/skills/draft/scripts/runtime/uv.lock`

### implementation-evidence (79)

- `.agents/plugins/marketplace.json`
- `.agents/skills/i-am-yuna/SKILL.md`
- `.agents/skills/i-am-yuna/assets/yuna-template.md`
- `.agents/skills/i-am-yuna/references/codex-subagent.md`
- `.claude-plugin/marketplace.json`
- `.claude/skills/i-am-yuna`
- `.devcontainer/devcontainer-lock.json`
- `.devcontainer/devcontainer.json`
- `.devcontainer/post_create.sh`
- `.gitignore`
- `.prettierrc`
- `CLAUDE.md`
- `CONTEXT.md`
- `docs/adr/0001-coordinated-recovery-writers.md`
- `docs/adr/0002-active-lifecycle-discovery.md`
- `docs/references/git-locking-and-recovery.md`
- `docs/superpowers/plans/2026-07-30-draft-plugin-manifests-and-path-contract.md`
- `docs/superpowers/plans/2026-07-31-draft-self-contained-skill-runtime.md`
- `docs/superpowers/plans/2026-08-04-draft-single-python-launcher.md`
- `docs/superpowers/plans/2026-08-05-draft-actionable-check-diagnostics.md`
- `docs/superpowers/plans/2026-08-05-draft-core-plugin-separation.md`
- `docs/superpowers/plans/2026-08-06-draft-test-suite-restructuring.md`
- `docs/superpowers/plans/2026-08-09-draft-human-output-test-decoupling.md`
- `docs/superpowers/plans/2026-08-09-draft-typed-diagnostic-identifiers.md`
- `docs/superpowers/plans/2026-08-10-draft-agent-cli-capabilities.md`
- `docs/superpowers/plans/2026-08-12-draft-candidate-root-incremental-migration.md`
- `docs/superpowers/plans/2026-08-12-remove-uv-version-gate.md`
- `docs/superpowers/plans/2026-08-18-draft-recovery-protocol-closure.md`
- `docs/superpowers/plans/2026-08-18-draft-restore-abandon-checker-and-io-closure.md`
- `docs/superpowers/plans/2026-08-18-draft-restore-preclaim-and-checker-fact-closure.md`
- `docs/superpowers/plans/2026-08-18-draft-restore-recovery-and-checker-closure.md`
- `docs/superpowers/plans/2026-08-19-draft-checker-role-and-marker-io-closure.md`
- `docs/superpowers/plans/2026-08-19-draft-operation-entry-shape-and-marker-snapshot-closure.md`
- `docs/superpowers/plans/2026-08-20-draft-cli-entry-marker-classification-closure.md`
- `docs/superpowers/plans/2026-08-21-draft-relation-rewrite-and-conclusions-discovery-closure.md`
- `docs/superpowers/plans/2026-08-22-draft-facts-loading-redundancy-closure.md`
- `docs/superpowers/plans/2026-08-23-draft-move-unmanaged-promotion-index-freshness.md`
- `docs/superpowers/plans/2026-08-23-draft-move-unmanaged-promotion.md`
- `docs/superpowers/plans/2026-08-24-draft-durable-link-index-maintenance.md`
- `docs/superpowers/plans/2026-08-25-draft-recover-lifecycle-boundary-fix.md`
- `docs/superpowers/plans/2026-08-25-draft-unlocked-recovery-identity-closure-round-2.md`
- `docs/superpowers/plans/2026-08-25-draft-unlocked-recovery-identity-closure.md`
- `docs/superpowers/plans/2026-08-26-draft-coordinated-recovery-ownership.md`
- `package.json`
- `plugins/draft/.claude-plugin/plugin.json`
- `plugins/draft/.codex-plugin/plugin.json`
- `plugins/draft/CLAUDE.md`
- `plugins/draft/hooks/claude-hooks.json`
- `plugins/draft/hooks/codex-hooks.json`
- `plugins/draft/scripts/adapter/__init__.py`
- `plugins/draft/scripts/adapter/bindings.py`
- `plugins/draft/scripts/adapter/bridge.py`
- `plugins/draft/scripts/adapter/errors.py`
- `plugins/draft/scripts/adapter/session_hook.py`
- `plugins/draft/scripts/check-adapters.sh`
- `plugins/draft/scripts/draft.py`
- `plugins/draft/scripts/pyrightconfig.json`
- `plugins/draft/scripts/pytest.ini`
- `plugins/draft/scripts/ruff.toml`
- `plugins/draft/scripts/tests/__init__.py`
- `plugins/draft/scripts/tests/integration/__init__.py`
- `plugins/draft/scripts/tests/integration/conftest.py`
- `plugins/draft/scripts/tests/integration/test_bindings.py`
- `plugins/draft/scripts/tests/integration/test_bridge.py`
- `plugins/draft/scripts/tests/integration/test_cli.py`
- `plugins/draft/scripts/tests/integration/test_distribution_contract.py`
- `plugins/draft/scripts/tests/integration/test_distribution_smoke.py`
- `plugins/draft/scripts/tests/integration/test_gate_entry.py`
- `plugins/draft/scripts/tests/integration/test_session_hook.py`
- `plugins/draft/scripts/tests/support/__init__.py`
- `plugins/draft/scripts/tests/support/entry.py`
- `plugins/draft/scripts/tests/support/paths.py`
- `plugins/draft/scripts/tests/unit/__init__.py`
- `plugins/draft/scripts/tests/unit/test_bindings.py`
- `plugins/draft/scripts/tests/unit/test_bridge.py`
- `plugins/draft/scripts/tests/unit/test_cli.py`
- `plugins/draft/scripts/tests/unit/test_errors.py`
- `plugins/draft/scripts/tests/unit/test_session_hook.py`
- `pnpm-lock.yaml`

### workflow-state (51)

- `.scratch/draft-coordinated-recovery-ownership/issues/01-converge-unlocked-recovery-candidates-under-lock.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/01-participant-preflight-plan.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/02-close-allocation-residue-and-checker-semantics.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/03-lock-owned-restore-trash-classification.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/03-round-2-plan.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/03-round-3-plan.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/04-reacquire-lock-after-trash-lifecycle-release.md`
- `.scratch/draft-coordinated-recovery-ownership/issues/05-verify-recovery-refresh-and-fd-semantics.md`
- `.scratch/draft-durable-link-index-maintenance/issues/01-define-link-index-domain-ports.md`
- `.scratch/draft-durable-link-index-maintenance/issues/02-establish-journal-v4-state-model.md`
- `.scratch/draft-durable-link-index-maintenance/issues/03-build-pure-link-index-engine.md`
- `.scratch/draft-durable-link-index-maintenance/issues/04-implement-strict-root-fact-source.md`
- `.scratch/draft-durable-link-index-maintenance/issues/05-implement-durable-index-storage.md`
- `.scratch/draft-durable-link-index-maintenance/issues/06-implement-link-index-authority-repository.md`
- `.scratch/draft-durable-link-index-maintenance/issues/07-prepare-link-index-update-candidates.md`
- `.scratch/draft-durable-link-index-maintenance/issues/08-commit-link-index-forward.md`
- `.scratch/draft-durable-link-index-maintenance/issues/09-recover-link-index-phases.md`
- `.scratch/draft-durable-link-index-maintenance/issues/10-coordinate-independent-link-index-maintenance.md`
- `.scratch/draft-durable-link-index-maintenance/issues/11-settle-index-in-business-operations.md`
- `.scratch/draft-durable-link-index-maintenance/issues/12-close-trash-restore-index-lifecycle.md`
- `.scratch/draft-durable-link-index-maintenance/issues/13-add-files-changed-notification.md`
- `.scratch/draft-durable-link-index-maintenance/issues/14-maintain-index-during-check-and-adopt.md`
- `.scratch/draft-durable-link-index-maintenance/issues/15-use-index-in-backlink-queries.md`
- `.scratch/draft-durable-link-index-maintenance/issues/16-verify-entry-and-namespace-contracts.md`
- `.scratch/draft-durable-link-index-maintenance/issues/17-verify-durability-concurrency-freshness.md`
- `.scratch/draft-durable-link-index-maintenance/issues/18-deliver-benchmarks-docs-and-gates.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/01-form-draft-v1-domain-contract.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/02-define-v1-isolation-and-cutover-boundary.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/03-model-durable-authority-and-recovery-ownership.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/04-research-linux-platform-capabilities.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/05-research-macos-platform-capabilities.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/06-research-windows-platform-capabilities.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/07-freeze-public-request-result-and-cli-protocol.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/08-synthesize-platform-adapter-contract.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/09-freeze-time-identifiers-and-ordering.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/10-freeze-canonical-encoding-and-scalars.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/11-freeze-core-runtime-records.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/12-freeze-catalog-and-index-records.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/13-freeze-handoff-adapter-and-worker-protocol.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/14-freeze-trash-restore-and-handoff-records.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/15-freeze-composition-and-fault-injection-seam.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/issues/16-audit-end-to-end-contract-closure.md`
- `.scratch/draft-operation-runtime-execution-wayfinding/map.md`
- `.scratch/draft-operation-runtime-foundation/issues/01-root-lock-bootstrap-and-runtime-records.md`
- `.scratch/draft-operation-runtime-foundation/issues/02-shared-request-gate-runtime-interface-and-dry-run.md`
- `.scratch/draft-operation-runtime-foundation/issues/03-ordinary-content-commit-and-capability-recovery.md`
- `.scratch/draft-operation-runtime-foundation/issues/04-index-maintenance-operations-and-epoch-ledger.md`
- `.scratch/draft-operation-runtime-foundation/issues/05-trash-restore-at-least-once-handoff.md`
- `.scratch/draft-operation-runtime-foundation/issues/06-locked-recovery-convergence.md`
- `.scratch/draft-operation-runtime-foundation/issues/07-lifecycle-inspection-and-cli-adapter.md`
- `.scratch/draft-operation-runtime-foundation/issues/08-remove-superseded-runtime-and-authority-surface.md`

## 迁移单元

下表每行目前只是骨架。在 Commit B 之前，每行都必须补齐 Inputs、Outputs、State-Dimensions、Invariants、Transitions、Side-Effects、Failure、Provenance、Supersedes、Consumers 和 Verification。

| ID | Target | Current contract scope | Control input | Status |
| --- | --- | --- | --- | --- |
| `G-ROUTING` | `CONTEXT-MAP.md` + `plugins/draft/docs/spec/index.md` | 根路由、Draft 插件规格网络唯一入口、作用域边界和无工具读取路径 | Q10、Q19、Q22 | `discovered` |
| `G-AUTH` | `governance/authority.md` | 当前树成员资格、signer、来源资格、局部取代、ADR 边界、工具独立读取 | Q1、Q2、Q8–Q10、Q15、Q18–Q20、Q23、Q24、Q28 | `discovered` |
| `G-MIGRATE` | `governance/migration.md` | Draft confirmed 到正式 Wiki 的 packet、签署与原子切换，以及 `docs/design.md` 的退役处理 | Q3、Q4、Q11、Q13、Q14、Q16、Q21、Q22 | `discovered` |
| `G-FEATURE` | `governance/feature-specs.md` | feature delta、Base-Spec/Affects、tracker close/resolution、Agent Brief 层级 | Q25–Q27、Q32 | `discovered` |
| `D-LIFECYCLE` | `draft/lifecycle.md` | candidate/registered 根、marker、初始化、adopt 与 Draft/正式规范边界 | 08-12、Q4、Q17 | `discovered` |
| `D-PAGES` | `draft/pages-and-navigation.md` | 页面命名空间、根/叶/hub、父关系、导航、唯一空子主题、非法 pages/index.md | 08-10、08-12、Q7、Q35 | `discovered` |
| `D-STATUS` | `draft/status-and-relations.md` | Draft 页面状态、正式关系、正反声明、环和状态不变量 | 08-10、Q5、Q6、S1 | `discovered` |
| `D-RESOURCES` | `draft/resources-and-links.md` | 资源身份、链接闭包和结构移动后的链接事实 | 08-10、08-12、Q31-C | `discovered` |
| `D-RESOURCE-OWNERSHIP` | `draft/resources-and-links.md#所有权` | 页面子树仅拥有受管理 Markdown，资源原地保留 | 08-12、Q33 | `discovered` |
| `D-QUERY` | `draft/query-check-and-output.md` | 读取、分页、排序、check、adopt、backlinks 与诊断 | 08-10、08-12、08-05 | `discovered` |
| `D-CONCLUSIONS` | `draft/conclusions.md` | confirmed 成员、双 section、challenge 投影、生成、路径维护和 clean break | Q5、Q6、Q12、Q13、Q29–Q31、S1 | `discovered` |
| `D-CREATE-EDIT` | `draft/page-edits.md` | create、status.set、relation.add/remove、DryRun 与零写 | 08-10、08-12、S1 | `discovered` |
| `D-MOVE` | `draft/structure.md#move` | 多源/目录 move、页面纳管、链接闭包和原子成败 | Q31-A/B/C | `discovered` |
| `D-MOVE-OUTPUT` | `draft/structure.md#目录来源` | 只移动 Markdown，非 Markdown 原地保留，窄输出 | Q33 | `discovered` |
| `D-PROMOTE` | `draft/structure.md#promote` | 同父 sibling、原导航顺序、保名子树映射 | Q34 | `discovered` |
| `D-COLLAPSE` | `draft/structure.md#collapse` | 空非根 hub collapse、路径和链接原子维护 | Q31-C | `discovered` |
| `D-TRASH-RESTORE` | `draft/trash-and-restore.md` | 回收、7 天窗口、恢复冲突、子树边界和 snapshot 非阻塞 | 08-10、08-12、Q31-C | `discovered` |
| `P-IDENTITY` | `delivery/plugin-and-entrypoints.md#身份` | draft 插件/skill/CLI/marketplace 身份和 clean break | 07-30、Q30 | `discovered` |
| `P-BOUNDARY` | `delivery/plugin-and-entrypoints.md#责任边界` | 核心/适配层边界、入口、bridge/binding、失败投影 | 07-30、08-04、08-05 | `discovered` |
| `P-DIAGNOSTICS` | `draft/query-check-and-output.md#诊断` | 检测层事实、普通/JSON 投影、稳定机器协议 | 08-05 | `discovered` |
| `R-INTERFACE` | `runtime/interface.md` | Lifecycle.initialize 与 root-bound OperationRuntime 接口 | runtime foundation、Q32 | `discovered` |
| `R-INIT` | `runtime/initialization-and-platform.md` | 平台 capability、根/持久布局、Init 状态机、失败前零写 | runtime foundation、Q32 | `discovered` |
| `R-ADMISSION` | `runtime/admission-and-effects.md` | root lock、请求分类、DryRun/Execute、副作用授权 | runtime foundation、Q32 | `discovered` |
| `R-AUTHORITY` | `runtime/authority-and-projections.md` | record、content/catalog/head authority 与 projection 来源 | runtime foundation、Q32 | `discovered` |
| `R-LIFECYCLE` | `runtime/lifecycle-and-handoff.md` | 生命周期、结果语义、Trash/Restore/Handoff | runtime foundation、Q32 | `discovered` |
| `R-RECOVERY` | `runtime/recovery.md` | 崩溃窗口、阻塞、recover、前向/回滚/清理 | runtime foundation、Q32 | `discovered` |
| `R-ERRORS` | `runtime/errors-and-observability.md` | 稳定错误、notification、公开结果和恢复要求 | runtime foundation、Q32 | `discovered` |

## 目标页覆盖核对

签署清单中的 16 个计划目标页均已映射到至少一个迁移单元；其中 `MAP` 与 `INDEX` 共同由 `G-ROUTING` 覆盖，因为它们描述同一条“从仓库根路由到 Draft 插件规格唯一入口”的可验证行为合同，而不是两个独立行为。

| 目标简称 | 计划路径 | 覆盖迁移单元 |
| --- | --- | --- |
| `MAP` | `CONTEXT-MAP.md` | `G-ROUTING` |
| `INDEX` | `plugins/draft/docs/spec/index.md` | `G-ROUTING` |
| `AUTH` | `plugins/draft/docs/spec/governance/authority.md` | `G-AUTH` |
| `MIG` | `plugins/draft/docs/spec/governance/migration.md` | `G-MIGRATE` |
| `FEATURE` | `plugins/draft/docs/spec/governance/feature-specs.md` | `G-FEATURE` |
| `LIFE` | `plugins/draft/docs/spec/draft/lifecycle.md` | `D-LIFECYCLE` |
| `PAGE` | `plugins/draft/docs/spec/draft/pages-and-navigation.md` | `D-PAGES` |
| `REL` | `plugins/draft/docs/spec/draft/status-and-relations.md` | `D-STATUS` |
| `CONC` | `plugins/draft/docs/spec/draft/conclusions.md` | `D-CONCLUSIONS` |
| `RESOURCE` | `plugins/draft/docs/spec/draft/resources-and-links.md` | `D-RESOURCES`、`D-RESOURCE-OWNERSHIP` |
| `EDIT` | `plugins/draft/docs/spec/draft/page-edits.md` | `D-CREATE-EDIT` |
| `STRUCT` | `plugins/draft/docs/spec/draft/structure.md` | `D-MOVE`、`D-MOVE-OUTPUT`、`D-PROMOTE`、`D-COLLAPSE` |
| `TRASH` | `plugins/draft/docs/spec/draft/trash-and-restore.md` | `D-TRASH-RESTORE` |
| `QUERY` | `plugins/draft/docs/spec/draft/query-check-and-output.md` | `D-QUERY`、`P-DIAGNOSTICS` |
| `DELIVERY` | `plugins/draft/docs/spec/delivery/plugin-and-entrypoints.md` | `P-IDENTITY`、`P-BOUNDARY` |
| `RUNTIME` | `plugins/draft/docs/spec/runtime/` | `R-INTERFACE`、`R-INIT`、`R-ADMISSION`、`R-AUTHORITY`、`R-LIFECYCLE`、`R-RECOVERY`、`R-ERRORS` |

## 未决门禁

当前表中登记了 `27` 个唯一 Contract-ID，与形式合同第 13 节声称的数量一致。此前缺失的是 `G-ROUTING`：它闭合了签署清单中 `MAP` 与 `INDEX` 两个目标页的根路由行为。目标页覆盖核对未发现未映射的计划目标或孤立的 Contract-ID。

签署决定覆盖核对：Q1–Q35 均至少出现在一个迁移单元的控制输入中（范围记法如 `Q8–Q10`、`Q25–Q27` 按连续编号展开），编号外合同 S1 由 `D-STATUS`、`D-CONCLUSIONS` 和 `D-CREATE-EDIT` 覆盖。Q11 与 Q14 明确归入 `G-MIGRATE`，因为它们描述旧 `docs/design.md` 的 authority 退役和原子删除动作，而不是新的 Wiki 行为。

只要仍有路径角色、来源资格、provenance locator、supersession scope 或消费者映射处于 unresolved，就不得进行 authority cutover。eligible 来源之间确实冲突，或所有 eligible 来源都沉默且不同选择会改变可观察行为时，必须提交 signer 裁决；实现和测试可以报告 conformance gap，但不能替规格选择语义。

## 下一阶段

本证据包通过静态审查后，创建 Commit B，在 `plugins/draft/docs/spec/` 下建立 staged plugin spec network。只有 Commit C 执行 authority cutover、消费者同步、旧来源退役和证据包删除；A、B、C 必须作为不可拆分的 commit series 由 signer 一次批准。
