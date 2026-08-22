from __future__ import annotations

from pathlib import Path

import pytest

from draft.errors import DraftError
from draft.models import RootState
from draft.structure import move_entries
from tests.integration.support import DraftFactory

PAGE_INDEX = """# 根

> 状态：`已确认：根索引`

正文。
"""


def _page(title: str, body: str = "正文。") -> str:
    return f"# {title}\n\n> 状态：`已确认：说明`\n\n{body}\n"


def test_move_promotes_unmanaged_file_and_rewrites_all_linkers(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": _page("页面 A", "参考 [旧稿](../00-background.md)。"),
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "00-background.md").write_text(
        "旧稿 [站内](pages/a.md) 与 [本地](sibling.md)。\n", encoding="utf-8"
    )
    (root / "sibling.md").write_text("邻居 [旧稿](00-background.md)。\n", encoding="utf-8")

    move_entries(root, ["00-background.md"], "pages/00-background.md")

    assert not (root / "00-background.md").exists()
    promoted = (root / "pages/00-background.md").read_text(encoding="utf-8")
    assert "[站内](a.md)" in promoted          # 出站：新基址下 pages 内相对
    assert "[本地](../sibling.md)" in promoted  # 出站：指向留下的未受管理文件
    page_a = (root / "pages/a.md").read_text(encoding="utf-8")
    assert "[旧稿](00-background.md)" in page_a  # 受管理链接者
    sibling = (root / "sibling.md").read_text(encoding="utf-8")
    assert "[旧稿](pages/00-background.md)" in sibling  # 未受管理链接者


def test_move_rejects_and_names_undecodable_unmanaged_file(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文 [链接](pages/a.md)。\n", encoding="utf-8")
    (root / "gbk-legacy.md").write_bytes("旧稿。".encode("gb18030"))

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    assert caught.value.code == "structure.dirty"
    assert "gbk-legacy.md" in caught.value.details
    assert (root / "stray.md").exists()          # 拒绝后零写入
    assert not (root / "pages/stray.md").exists()


def test_move_blocking_merges_managed_and_unmanaged(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/broken.md": "没有标题与机器块的受管理页。\n",
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")
    (root / "gbk-legacy.md").write_bytes("旧稿。".encode("gb18030"))

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    assert caught.value.code == "structure.dirty"
    assert sorted(caught.value.details) == ["gbk-legacy.md", "pages/broken.md"]


def test_move_promotion_recovers_after_interrupt(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文 [站内](index.md)。\n", encoding="utf-8")

    def _interrupted_apply_delete(
        root: Path, target: Path, *, expected_digest: str
    ) -> None:
        raise OSError("注入中断")

    monkeypatch.setattr("draft.operations._apply_delete", _interrupted_apply_delete)

    with pytest.raises(OSError):
        move_entries(root, ["stray.md"], "pages/stray.md")

    # 注入窗口：WRITE 已应用、DELETE 未应用，锁保留。
    assert (root / "stray.md").exists()
    assert (root / "pages/stray.md").exists()
    assert (root / ".draft" / "operation.lock").exists()

    monkeypatch.undo()

    from draft import operations

    operations.recover(root)

    # 恢复复用现状语义（不改 operations.py）：APPLYING 阶段崩溃前向完成移动，
    # 与 test_recover_finishes_a_partially_applied_plan 一致；出站链接已重写。
    assert not (root / "stray.md").exists()
    promoted = (root / "pages/stray.md").read_text(encoding="utf-8")
    assert "[站内](../index.md)" in promoted
    assert not (root / ".draft" / "operation.lock").exists()
