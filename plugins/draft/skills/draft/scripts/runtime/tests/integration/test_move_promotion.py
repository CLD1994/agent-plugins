from __future__ import annotations

import json
from pathlib import Path

import pytest

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import RootState
from draft.structure import move_entries, move_page
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
    assert "[站内](a.md)" in promoted  # 出站：新基址下 pages 内相对
    assert "[本地](../sibling.md)" in promoted  # 出站：指向留下的未受管理文件
    page_a = (root / "pages/a.md").read_text(encoding="utf-8")
    assert "[旧稿](00-background.md)" in page_a  # 受管理链接者
    sibling = (root / "sibling.md").read_text(encoding="utf-8")
    assert "[旧稿](pages/00-background.md)" in sibling  # 未受管理链接者


def test_move_managed_page_rewrites_unmanaged_linkers(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": _page("页面 A"),
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "legacy.md").write_text("旧稿 [A](pages/a.md)。\n", encoding="utf-8")

    move_entries(root, ["pages/a.md"], "pages/b.md")

    assert "[A](pages/b.md)" in (root / "legacy.md").read_text(encoding="utf-8")


def test_fresh_index_path_does_not_read_unmatched_markdown(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": _page("页面 A"),
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "legacy.md").write_text("[A](pages/a.md)\n", encoding="utf-8")
    unrelated = root / "unrelated.md"
    unrelated.write_text("无关正文。\n", encoding="utf-8")
    move_entries(root, ["pages/a.md"], "pages/b.md")
    original = Path.read_bytes

    def fail_unrelated(path: Path) -> bytes:
        if path == unrelated:
            raise AssertionError("fresh index read an unmatched file")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_unrelated)
    move_entries(root, ["pages/b.md"], "pages/c.md")


def test_programmatic_move_page_rewrites_unmanaged_linkers(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": _page("页面 A"),
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "legacy.md").write_text("旧稿 [A](pages/a.md)。\n", encoding="utf-8")

    move_page(root, Path("pages/a.md"), Path("pages/b.md"))

    assert "[A](pages/b.md)" in (root / "legacy.md").read_text(encoding="utf-8")


def test_programmatic_move_page_refreshes_index_from_merged_plan(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": _page("页面 A")},
        root_state=RootState.CANDIDATE,
    )
    (root / "legacy.md").write_text("旧稿 [A](pages/a.md)。\n", encoding="utf-8")
    captured: list[object] = []
    from draft.models import OperationPhase, OperationPlan, OperationResult

    def capture_plan(plan: OperationPlan) -> OperationResult:
        captured.append(plan)
        return OperationResult("id", "move", OperationPhase.COMPLETED, ())

    monkeypatch.setattr(
        "draft.structure.execute_plan",
        capture_plan,
    )
    move_page(root, Path("pages/a.md"), Path("pages/b.md"))
    assert captured
    plan = captured[0]
    assert isinstance(plan, OperationPlan)
    assert any(mutation.path == Path("legacy.md") for mutation in plan.mutations)


def test_move_plan_rejects_unmanaged_file_change_before_first_write(
    draft_factory: DraftFactory,
) -> None:
    from draft.operation_facts import load_operation_facts
    from draft.structure import plan_move_from_arguments

    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")
    (root / "quiet.md").write_text("原本没有入站链接。\n", encoding="utf-8")
    facts = load_operation_facts(root)
    plan = plan_move_from_arguments(root, facts, ["stray.md"], "pages/stray.md")
    (root / "quiet.md").write_text("后来 [stray](stray.md)。\n", encoding="utf-8")

    from draft.operations import execute_plan

    with pytest.raises(DraftError) as caught:
        execute_plan(plan)

    assert caught.value.code == "operation.external_change"
    assert (root / "stray.md").exists()
    assert not (root / "pages/stray.md").exists()


def test_move_plan_rejects_index_generation_change_before_write(
    draft_factory: DraftFactory,
) -> None:
    from draft.operation_facts import load_operation_facts
    from draft.operations import execute_plan
    from draft.structure import plan_move_from_arguments

    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": _page("页面 A")},
        root_state=RootState.CANDIDATE,
    )
    move_entries(root, ["pages/a.md"], "pages/b.md")
    facts = load_operation_facts(root)
    plan = plan_move_from_arguments(root, facts, ["pages/b.md"], "pages/c.md")
    # 篡改已发布的 v2 索引制品字节：规划读集观察覆盖制品，执行前必须拒绝。
    index_path = root / ".draft" / "link-index.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["revision"] = str(int(payload["revision"]) + 1)
    index_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DraftError) as caught:
        execute_plan(plan)
    assert caught.value.code == "operation.external_change"
    assert (root / "pages/b.md").exists()
    assert not (root / "pages/c.md").exists()
    journals = list((root / ".draft" / "operations").glob("*/journal.json"))
    phases = {json.loads(path.read_text(encoding="utf-8"))["phase"] for path in journals}
    assert phases == {"completed", "rolled_back"}
    assert not (root / ".draft" / "operation.lock").exists()


def test_move_rejects_overlapping_sources_independent_of_argument_order(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "legacy" / "topic").mkdir(parents=True)
    (root / "legacy" / "index.md").write_text("旧根。\n", encoding="utf-8")
    (root / "legacy" / "topic" / "a.md").write_text("旧页。\n", encoding="utf-8")

    errors: list[tuple[str, tuple[str, ...]]] = []
    for sources in (("legacy", "legacy/topic"), ("legacy/topic", "legacy")):
        with pytest.raises(DraftError) as caught:
            move_entries(root, list(sources), "pages")
        errors.append((caught.value.code, caught.value.details))

    assert errors[0] == errors[1]
    assert errors[0][0] == "structure.conflict"


def test_move_shape_error_precedes_damaged_marker(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "legacy").mkdir()
    (root / "legacy" / "a.md").write_text("旧稿。\n", encoding="utf-8")
    (root / ".draft" / "draft.json").write_text("not json\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["legacy"], "pages/a.md")

    assert caught.value.code == "cli.move_operands_invalid"


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("gone"), PermissionError("denied"), OSError("io")]
)
def test_move_closure_io_failures_are_stable_draft_errors(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
) -> None:
    from draft.operation_facts import load_operation_facts
    from draft.structure import plan_move_from_arguments

    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")
    facts = load_operation_facts(root)
    original = Path.read_bytes

    def fail_closure_read(path: Path) -> bytes:
        if path == root / "stray.md":
            raise failure
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", fail_closure_read)
    with pytest.raises(DraftError) as caught:
        plan_move_from_arguments(root, facts, ["stray.md"], "pages/stray.md")

    assert isinstance(caught.value, DraftError)
    assert caught.value.paths == (Path("stray.md"),)
    expected_reason = (
        "not_found"
        if isinstance(failure, FileNotFoundError)
        else "permission_denied"
        if isinstance(failure, PermissionError)
        else "io_error"
    )
    assert caught.value.details == (expected_reason,)


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
    assert (root / "stray.md").exists()  # 拒绝后零写入
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

    def _interrupted_apply_delete(root: Path, target: Path, *, expected_digest: str) -> None:
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
    assert "[枢纽](index.md)" in (root / "pages/legacy-notes/topic/a.md").read_text(
        encoding="utf-8"
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
    # round-1 L8：钉住具体协议欠账码，而非只断言非空。
    assert CheckCode.PAGE_MISSING_TITLE.value in legacy_codes
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
    page_a = "# 页面 A\n\n> 状态：`已确认：说明`\n>\n> 父主题：[主题枢纽](index.md)\n\n正文。\n"
    hub_archive = (
        "# 归档枢纽\n\n> 状态：`已确认：枢纽`\n>\n> 父主题：[根索引](../../index.md)\n\n正文。\n"
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
    assert (root / "conclusions.md").exists()  # 拒绝后零写入
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
    assert not (root / "pages/a.md").exists()  # 批量中任一来源命中即拒绝整批


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


# ---------------------------------------------------------------------------
# H3：分类决策表 §2.2 未覆盖行（顺序 2/3/7/8）
# ---------------------------------------------------------------------------


def test_move_source_in_internal_territory_is_rejected(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)

    with pytest.raises(DraftError) as caught:
        move_entries(root, [".draft/ops.md"], "pages/x.md")

    assert caught.value.code == CheckCode.PATH_INTERNAL.value


def test_move_nested_draft_file_operand_is_ordinary_content(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "legacy" / ".draft").mkdir(parents=True)
    (root / "legacy" / ".draft" / "notes.md").write_text("旧稿。\n", encoding="utf-8")

    move_entries(root, ["legacy/.draft/notes.md"], "pages/notes.md")

    assert (root / "pages/notes.md").is_file()
    assert not (root / "legacy" / ".draft" / "notes.md").exists()


def test_move_symlink_source_is_rejected(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "real.md").write_text("x", encoding="utf-8")
    (root / "link.md").symlink_to(root / "real.md")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["link.md"], "pages/link.md")

    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value


def test_move_root_level_symlink_blocks_via_check_escape(
    draft_factory: DraftFactory,
) -> None:
    """根级符号链接由 check/事实加载的 link.escape 进入既有 blocking，move 被拒。

    round-1 L5 实证：load_graph_tolerant 对根级符号链接产生 link.escape issue，
    进入 blocking_paths；move 经 merged_blocking 一并判断而被 structure.dirty 拒绝。
    """
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "real.md").write_text("x", encoding="utf-8")
    (root / "link.md").symlink_to(root / "real.md")
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["stray.md"], "pages/stray.md")

    assert caught.value.code == "structure.dirty"
    assert "link.md" in caught.value.details


def test_move_empty_or_dot_source_reports_protected(draft_factory: DraftFactory) -> None:
    """round-1 L9：根本身作为来源显式以 structure.protected 拒绝，零写入。"""
    root = draft_factory(
        {"index.md": PAGE_INDEX, "pages/a.md": _page("页面 A")},
        root_state=RootState.CANDIDATE,
    )
    (root / "stray.md").write_text("正文。\n", encoding="utf-8")

    for operand in ("", "."):
        with pytest.raises(DraftError) as caught:
            move_entries(root, [operand], "pages/")
        assert caught.value.code == "structure.protected"

    assert not (root / "pages/stray.md").exists()  # 零写入
    assert (root / "stray.md").exists()


def test_move_non_markdown_file_source_is_rejected(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "img.png").write_bytes(b"\x89PNG")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["img.png"], "pages/img.png")

    assert caught.value.code == "path.not_markdown"


def test_move_nonexistent_source_reports_unknown_page(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["missing.md"], "pages/missing.md")

    assert caught.value.code == "structure.unknown_page"


# ---------------------------------------------------------------------------
# H3：目录枚举契约 §2.3（符号链接条目拒绝整批 / 空枚举 / pages 保护）
# ---------------------------------------------------------------------------


def test_move_directory_source_with_symlink_entry_rejects_batch(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    legacy = root / "legacy"
    (legacy / "topic").mkdir(parents=True)
    (legacy / "index.md").write_text("旧根。\n", encoding="utf-8")
    (root / "real.md").write_text("x", encoding="utf-8")
    (legacy / "topic" / "link.md").symlink_to(root / "real.md")

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["legacy"], "pages")

    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value
    assert (legacy / "index.md").exists()  # 整批拒绝，零写入


def test_move_empty_directory_source_reports_unknown_page(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "legacy" / "topic").mkdir(parents=True)  # 无任何 .md

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["legacy"], "pages")

    assert caught.value.code == "structure.unknown_page"


def test_move_pages_directory_source_is_protected(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["pages"], "pages/archive")

    assert caught.value.code == "structure.protected"


# ---------------------------------------------------------------------------
# H3：同批交叉链接与锚点保留
# ---------------------------------------------------------------------------


def test_move_batch_remaps_cross_links_within_moved_set(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "a.md").write_text("甲引 [乙](b.md)。\n", encoding="utf-8")
    (root / "b.md").write_text("乙正文。\n", encoding="utf-8")

    move_entries(root, ["a.md", "b.md"], "pages")

    moved_a = (root / "pages/a.md").read_text(encoding="utf-8")
    assert "[乙](b.md)" in moved_a  # 同批同目录移动，相对形式不变且指向新位置
    assert "[乙](pages/b.md)" not in moved_a


def test_move_preserves_anchors_in_rebased_links(draft_factory: DraftFactory) -> None:
    root = draft_factory({"index.md": PAGE_INDEX}, root_state=RootState.CANDIDATE)
    (root / "legacy").mkdir()
    (root / "src.md").write_text("看 [节](legacy/other.md#sec)。\n", encoding="utf-8")
    (root / "legacy" / "other.md").write_text("旧稿。\n", encoding="utf-8")

    move_entries(root, ["src.md"], "pages/src.md")

    text = (root / "pages/src.md").read_text(encoding="utf-8")
    assert "[节](../legacy/other.md#sec)" in text  # 锚点保留，路径按新基址重算


# ---------------------------------------------------------------------------
# H3：受管理目录来源父声明无法重写 → parent_mismatch
# ---------------------------------------------------------------------------


def test_move_managed_directory_source_parent_mismatch(draft_factory: DraftFactory) -> None:
    hub_topic = (
        "# 主题枢纽\n\n> 状态：`已确认：枢纽`\n>\n> 父主题：[根索引](../../index.md)\n\n正文。\n"
    )
    page_a = "# 页面 A\n\n> 状态：`已确认：说明`\n>\n> 父主题：[主题枢纽](index.md)\n\n正文。\n"
    hub_archive = (
        "# 归档枢纽\n\n> 状态：`已确认：枢纽`\n>\n> 父主题：[根索引](../../index.md)\n\n正文。\n"
    )
    # pages/other/x.md 声明父主题指向将被移走的 pages/topic/index.md；x 自身不移动，
    # 其物理父页（pages/other/index.md 位置的页面）不是枢纽移动后的位置 → 无法重写。
    page_x = (
        "# 异页\n\n> 状态：`已确认：说明`\n>\n> 父主题：[主题枢纽](../topic/index.md)\n\n正文。\n"
    )
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/topic/index.md": hub_topic,
            "pages/topic/a.md": page_a,
            "pages/archive/index.md": hub_archive,
            "pages/other/x.md": page_x,
        },
        root_state=RootState.CANDIDATE,
    )

    with pytest.raises(DraftError) as caught:
        move_entries(root, ["pages/topic"], "pages/archive")

    assert caught.value.code == "structure.parent_mismatch"


# ---------------------------------------------------------------------------
# H3：闭包扩展只对 move 生效（promote 不受不可解码未受管理文件影响）
# ---------------------------------------------------------------------------


def test_move_closure_extension_does_not_leak_to_promote(draft_factory: DraftFactory) -> None:
    from draft.structure import promote_page

    index = (
        "# 根\n\n> 状态：`已确认：根索引`\n\n正文。\n"
        "\n## 子主题\n\n"
        "- [主题](pages/topic.md)\n"
        "- [子页](pages/child.md)\n"
    )
    topic = "# 主题\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n正文。\n"
    child = "# 子页\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n正文。\n"
    root = draft_factory(
        {"index.md": index, "pages/topic.md": topic, "pages/child.md": child},
        root_state=RootState.CANDIDATE,
    )
    (root / "gbk-legacy.md").write_bytes("旧稿。".encode("gb18030"))

    promote_page(root, Path("pages/topic.md"), [Path("pages/child.md")])

    assert (root / "pages/topic/index.md").is_file()
    assert (root / "pages/topic/child.md").is_file()
