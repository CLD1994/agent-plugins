"""load_operation_facts 的 marker snapshot 同源边界（round-15 H1）。

root_state、marker 原始 bytes 与观察摘要必须来自同一次读取；snapshot 后 live marker
被并发切换时，不得把新状态重新采信为观察基线。真实文件系统协作者在集成层验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import draft.operation_facts as facts_module
from draft.graph import MARKER_PATH, MarkerSnapshot
from draft.models import RootState
from draft.operation_facts import load_operation_facts
from draft.operations import sha256_bytes
from tests.integration.support import build_valid_candidate, managed_marker_text


def test_load_operation_facts_snapshot_survives_marker_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """snapshot 后 live marker 切成 registered：facts 的 root state 与观察摘要仍同源。

    load_operation_facts 完成第一次 snapshot 后、观察构造前切换 live marker：返回的
    root_state 与 marker observation 必须同时对应 candidate 快照，registered 新文件
    不被重新采信为基线（round-15 H1）。
    """
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    candidate_bytes = (root / ".draft/draft.json").read_bytes()
    registered_bytes = managed_marker_text(root_state=RootState.REGISTERED).encode("utf-8")

    real_snapshot = facts_module.read_marker_snapshot
    fired = False

    def snapshot_then_switch(path: Path) -> MarkerSnapshot:
        nonlocal fired
        snapshot = real_snapshot(path)
        if not fired:
            fired = True
            (path / MARKER_PATH).write_bytes(registered_bytes)
        return snapshot

    monkeypatch.setattr(facts_module, "read_marker_snapshot", snapshot_then_switch)

    facts = load_operation_facts(root)

    marker_obs = facts.observations.for_path(MARKER_PATH)
    assert facts.root_state is RootState.CANDIDATE
    assert marker_obs is not None
    assert marker_obs.sha256 == sha256_bytes(candidate_bytes)
    assert facts.marker.raw_bytes == candidate_bytes
    assert facts.marker.sha256 == sha256_bytes(candidate_bytes)
    assert facts.marker.state.root_state is RootState.CANDIDATE
    assert (root / ".draft/draft.json").read_bytes() == registered_bytes
