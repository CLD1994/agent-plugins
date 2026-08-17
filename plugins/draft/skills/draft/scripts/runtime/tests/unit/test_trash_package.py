from __future__ import annotations

from draft.trash_package import (
    CleanupEvidenceAction,
    CleanupEvidenceIntent,
    EvidenceNodeState,
    cleanup_evidence_actions,
)

MISS, VALID, INV, NON = (
    EvidenceNodeState.MISSING,
    EvidenceNodeState.VALID,
    EvidenceNodeState.INVALID,
    EvidenceNodeState.NON_FILE,
)


def _act(
    intent: CleanupEvidenceIntent,
    final: EvidenceNodeState,
    pending: EvidenceNodeState,
    quarantine: EvidenceNodeState,
    *,
    same_pending: bool = False,
    same_quarantine: bool = False,
) -> tuple[CleanupEvidenceAction, ...]:
    return cleanup_evidence_actions(
        intent=intent,
        final=final,
        pending=pending,
        quarantine=quarantine,
        final_pending_same_node=same_pending,
        final_quarantine_same_node=same_quarantine,
    )


def test_cleanup_evidence_decision_keep_rows() -> None:
    """KEEP（包存在/部分存在，证据必须保留）的合法组合（第十轮 H1）。"""
    assert _act(CleanupEvidenceIntent.KEEP, MISS, MISS, MISS) == (CleanupEvidenceAction.READY,)
    assert _act(CleanupEvidenceIntent.KEEP, VALID, MISS, MISS) == (CleanupEvidenceAction.READY,)
    assert _act(CleanupEvidenceIntent.KEEP, MISS, VALID, MISS) == (
        CleanupEvidenceAction.PUBLISH_PENDING,
        CleanupEvidenceAction.READY,
    )
    assert _act(CleanupEvidenceIntent.KEEP, VALID, VALID, MISS, same_pending=True) == (
        CleanupEvidenceAction.CONSUME_PENDING,
        CleanupEvidenceAction.READY,
    )
    assert _act(CleanupEvidenceIntent.KEEP, MISS, MISS, VALID) == (
        CleanupEvidenceAction.RESTORE_QUARANTINE,
        CleanupEvidenceAction.READY,
    )
    assert _act(CleanupEvidenceIntent.KEEP, VALID, MISS, VALID, same_quarantine=True) == (
        CleanupEvidenceAction.CONSUME_QUARANTINE,
        CleanupEvidenceAction.READY,
    )


def test_cleanup_evidence_decision_remove_rows() -> None:
    """REMOVE（包已删除，证据必须消失）的合法组合（第十轮 H1）。"""
    assert _act(CleanupEvidenceIntent.REMOVE, MISS, MISS, MISS) == (CleanupEvidenceAction.DONE,)
    assert _act(CleanupEvidenceIntent.REMOVE, VALID, MISS, MISS) == (
        CleanupEvidenceAction.CONSUME_FINAL,
        CleanupEvidenceAction.DONE,
    )
    assert _act(CleanupEvidenceIntent.REMOVE, MISS, VALID, MISS) == (
        CleanupEvidenceAction.CONSUME_PENDING,
        CleanupEvidenceAction.DONE,
    )
    assert _act(CleanupEvidenceIntent.REMOVE, MISS, MISS, VALID) == (
        CleanupEvidenceAction.CONSUME_QUARANTINE,
        CleanupEvidenceAction.DONE,
    )
    assert _act(CleanupEvidenceIntent.REMOVE, VALID, VALID, MISS, same_pending=True) == (
        CleanupEvidenceAction.CONSUME_PENDING,
        CleanupEvidenceAction.CONSUME_FINAL,
        CleanupEvidenceAction.DONE,
    )
    assert _act(CleanupEvidenceIntent.REMOVE, VALID, MISS, VALID, same_quarantine=True) == (
        CleanupEvidenceAction.CONSUME_FINAL,
        CleanupEvidenceAction.CONSUME_QUARANTINE,
        CleanupEvidenceAction.DONE,
    )


def test_cleanup_evidence_decision_rejects_invalid_or_non_file() -> None:
    """任一 INVALID/NON_FILE 名都报 CORRUPT（字节/节点损坏可见，不删除不发布）。"""
    for intent in (CleanupEvidenceIntent.KEEP, CleanupEvidenceIntent.REMOVE):
        assert _act(intent, INV, MISS, MISS) == (CleanupEvidenceAction.CORRUPT,)
        assert _act(intent, MISS, INV, MISS) == (CleanupEvidenceAction.CORRUPT,)
        assert _act(intent, MISS, MISS, INV) == (CleanupEvidenceAction.CORRUPT,)
        assert _act(intent, NON, MISS, MISS) == (CleanupEvidenceAction.CORRUPT,)
        assert _act(intent, MISS, NON, MISS) == (CleanupEvidenceAction.CORRUPT,)
        assert _act(intent, MISS, MISS, NON) == (CleanupEvidenceAction.CORRUPT,)
        assert _act(intent, INV, VALID, VALID, same_pending=True) == (
            CleanupEvidenceAction.CORRUPT,
        )


def test_cleanup_evidence_decision_requires_same_node_proof() -> None:
    """两名并存必须证明是同一次发布/隔离；否则按 corrupt 保留，不得逐个删到干净。"""
    for intent in (CleanupEvidenceIntent.KEEP, CleanupEvidenceIntent.REMOVE):
        assert _act(intent, VALID, VALID, MISS, same_pending=False) == (
            CleanupEvidenceAction.CORRUPT,
        )
        assert _act(intent, VALID, MISS, VALID, same_quarantine=False) == (
            CleanupEvidenceAction.CORRUPT,
        )


def test_cleanup_evidence_decision_rejects_three_names_and_unlisted_rows() -> None:
    """三名并存与未列出组合无法由单个命名步骤产生：按 corrupt 保留。"""
    for intent in (CleanupEvidenceIntent.KEEP, CleanupEvidenceIntent.REMOVE):
        assert _act(intent, VALID, VALID, VALID, same_pending=True, same_quarantine=True) == (
            CleanupEvidenceAction.CORRUPT,
        )
        # KEEP 不允许 valid+pending 不同节点或 valid+quarantine 不同节点。
        assert _act(CleanupEvidenceIntent.KEEP, VALID, VALID, MISS, same_pending=True) != (
            CleanupEvidenceAction.CORRUPT,
        )
        assert _act(CleanupEvidenceIntent.KEEP, VALID, VALID, MISS) == (
            CleanupEvidenceAction.CORRUPT,
        )
        # REMOVE 不允许 final+quarantine 但 pending 也出现的组合。
        assert _act(CleanupEvidenceIntent.REMOVE, VALID, VALID, VALID, same_quarantine=True) == (
            CleanupEvidenceAction.CORRUPT,
        )
