from __future__ import annotations

from pathlib import Path

import pytest

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
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


def test_move_directory_promotes_tree_and_leaves_non_markdown(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    legacy = root / "legacy-notes"
    (legacy / "topic").mkdir(parents=True)
    (legacy / "index.md").write_text("旧根 [主题](topic/index.md)。\n", encoding="utf-8")
    (legacy / "topic" / "index.md").write_text("枢纽正文 [图](img.png)。\n", encoding="utf-8")
    (legacy / "topic" / "a.md").write_text("子页 [枢纽](index.md)。\n", encoding="utf-8")
    (legacy / "topic" / "img.png").write_bytes(b"\x89PNG")

    move_entries(root, ["legacy-notes"], "pages")

    moved_hub = root / "pages/legacy-notes/topic/index.md"
    assert moved_hub.is_file()
    # 图片原地留下：出站图片链接按新基址重算回留存的图片位置（规格 §11.2）。
    assert "[图](../../../legacy-notes/topic/img.png)" in moved_hub.read_text(encoding="utf-8")
    assert (root / "pages/legacy-notes/topic/a.md").is_file()
    assert (
        "[枢纽](index.md)" in (root / "pages/legacy-notes/topic/a.md").read_text(encoding="utf-8")
    )
    assert (legacy / "topic" / "img.png").is_file()  # 非 .md 原地留下
    assert not (legacy / "topic" / "index.md").exists()


def test_promoted_file_reports_protocol_debts_and_not_unmanaged_path(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": _page("页面 A")},
        root_state=RootState.CANDIDATE,
    )
    (root / "legacy.md").write_text("只有正文，没有任何协议结构。\n", encoding="utf-8")
    (root / "other-stray.md").write_text("另一篇未受管理文件。\n", encoding="utf-8")

    move_entries(root, ["legacy.md"], "pages/legacy.md")

    report = check_draft(root)
    legacy_codes = {i.code for i in report.errors if i.path == Path("pages/legacy.md")}
    assert legacy_codes                       # 协议欠账被报告（如 page.missing_title）
    assert CheckCode.PAGE_UNMANAGED_PATH.value not in legacy_codes
    assert any(
        i.path == Path("other-stray.md") and i.code == CheckCode.PAGE_UNMANAGED_PATH.value
        for i in report.errors
    )


def test_registered_root_rejects_at_health_gate_before_classification(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.REGISTERED)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    # 健康门禁先于操作数分类：错误码是检查错误（page.unmanaged_path），
    # 不是 structure.unknown_page/path.internal。
    assert caught.value.code == CheckCode.PAGE_UNMANAGED_PATH.value


def test_move_managed_directory_reparents_physically(
    draft_factory: DraftFactory,
) -> None:
    # 良构根：根索引列出两个子枢纽，topic 枢纽列出子页 a.md（规格 §11.1/§11.2）。
    root_index = (
        "# 根\n\n> 状态：`已确认：根索引`\n\n正文。\n"
        "\n## 子主题\n\n"
        "- [主题枢纽](pages/topic/index.md)\n"
        "- [归档枢纽](pages/archive/index.md)\n"
    )
    hub_topic = (
        "# 主题枢纽\n\n> 状态：`已确认：枢纽`\n>\n"
        "> 父主题：[根索引](../../index.md)\n\n正文。\n"
        "\n## 子主题\n\n- [页面 A](a.md)\n"
    )
    page_a = (
        "# 页面 A\n\n> 状态：`已确认：说明`\n>\n"
        "> 父主题：[主题枢纽](index.md)\n\n正文。\n"
    )
    hub_archive = (
        "# 归档枢纽\n\n> 状态：`已确认：枢纽`\n>\n"
        "> 父主题：[根索引](../../index.md)\n\n正文。\n"
    )
    root = draft_factory(
        {
            "index.md": root_index,
            "pages/topic/index.md": hub_topic,
            "pages/topic/a.md": page_a,
            "pages/archive/index.md": hub_archive,
        },
        root_state=RootState.CANDIDATE,
    )

    move_entries(root, ["pages/topic"], "pages/archive")

    moved_hub = (root / "pages/archive/topic/index.md").read_text(encoding="utf-8")
    # 枢纽的父声明目标重写到新物理父页 pages/archive/index.md：
    # 原 ../../index.md（指向根）→ 新 ../index.md（指向 archive 枢纽）
    assert "> 父主题：[根索引](../index.md)" in moved_hub
    assert (root / "pages/archive/topic/a.md").is_file()
    assert not list((root / "pages/topic").rglob("*.md"))
    assert check_draft(root).errors == ()


# ---------------------------------------------------------------------------
# H1：根 conclusions.md 来源保护（规格 §2.5）
# ---------------------------------------------------------------------------


def test_move_rejects_conclusions_source_on_candidate(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "conclusions.md").write_text("旧稿结论。\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["conclusions.md"], "pages/conclusions.md")

    assert caught.value.code == "structure.protected"
    assert (root / "conclusions.md").exists()          # 拒绝后零写入
    assert not (root / "pages/conclusions.md").exists()


def test_move_rejects_conclusions_source_in_batch(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "a.md").write_text("甲。\n", encoding="utf-8")
    (root / "conclusions.md").write_text("旧稿结论。\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["a.md", "conclusions.md"], "pages")

    assert caught.value.code == "structure.protected"
    assert not (root / "pages/conclusions.md").exists()
    assert not (root / "pages/a.md").exists()          # 批量中任一来源命中即拒绝整批


# ---------------------------------------------------------------------------
# M1：目录来源枚举嵌套 .draft 内容（规格 §2.2/§2.3：嵌套 .draft 是普通内容）
# ---------------------------------------------------------------------------


def test_move_directory_promotes_nested_draft_content(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    legacy = root / "legacy"
    (legacy / ".draft").mkdir(parents=True)
    (legacy / ".draft" / "index.md").write_text("嵌套枢纽。\n", encoding="utf-8")
    (legacy / ".draft" / "notes.md").write_text("嵌套旧稿。\n", encoding="utf-8")
    (legacy / "index.md").write_text("旧根 [注](.draft/notes.md)。\n", encoding="utf-8")

    move_entries(root, ["legacy"], "pages")

    assert (root / "pages/legacy/.draft/index.md").is_file()
    moved = root / "pages/legacy/.draft/notes.md"
    assert moved.is_file()
    assert moved.read_text(encoding="utf-8") == "嵌套旧稿。\n"
    assert not (legacy / ".draft" / "notes.md").exists()
    # 旧根链接按新基址重算：同深度同批移动，相对形式不变
    hub = (root / "pages/legacy/index.md").read_text(encoding="utf-8")
    assert "[注](.draft/notes.md)" in hub
