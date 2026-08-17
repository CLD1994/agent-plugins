from datetime import UTC, datetime
from pathlib import Path

import pytest

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import RootState
from draft.operations import recover
from draft.structure import collapse_hub, move_page, promote_page
from draft.trash import trash_page
from tests.integration.support import DraftFactory

INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [旧枢纽](pages/old/index.md)
- [新枢纽](pages/new/index.md)
- [结论汇总](conclusions.md)
"""

CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [A](pages/old/a.md)
"""

OLD_HUB = """# 旧枢纽

> 状态：`已关闭：纯导航页，不表达独立结论`
>
> 父主题：[根索引](../../index.md)

## 子主题

- [A](a.md)
- [留存](keep.md)

另见 [A 的补充](a.md#补充)。
"""

A_PAGE = """# A

> 状态：`已确认`
>
> 父主题：[旧枢纽](../old/index.md)

关联 [留存](keep.md#细节) 与 [外部](https://example.com/a)。

```markdown
[示例](keep.md)
```
"""

KEEP = """# 留存

> 状态：`待讨论`
>
> 父主题：[旧枢纽](../old/index.md)

提到 [A](a.md)。
"""

NEW_HUB = """# 新枢纽

> 状态：`已关闭：纯导航页，不表达独立结论`
>
> 父主题：[根索引](../../index.md)

## 子主题

- [B](b.md)
"""

B_PAGE = """# B

> 状态：`待讨论`
>
> 父主题：[新枢纽](index.md)

B 的正文。
"""


def build_two_hub_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": INDEX,
            "conclusions.md": CONCLUSIONS,
            "pages/old/index.md": OLD_HUB,
            "pages/old/a.md": A_PAGE,
            "pages/old/keep.md": KEEP,
            "pages/new/index.md": NEW_HUB,
            "pages/new/b.md": B_PAGE,
        }
    )


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.md"))
        if ".draft" not in path.parts
    }


EXPECTED_REPARENTED_FILES = {
    "index.md": INDEX,
    # 机器所有权（generated）下结构命令不改写结论列表中的旧链接（规格 §12.5）。
    "conclusions.md": CONCLUSIONS,
    "pages/old/index.md": OLD_HUB.replace("- [A](a.md)\n", "").replace(
        "(a.md#补充)", "(../new/a.md#补充)"
    ),
    "pages/old/keep.md": KEEP.replace("(a.md)", "(../new/a.md)"),
    "pages/new/index.md": NEW_HUB.replace("- [B](b.md)\n", "- [B](b.md)\n- [A](a.md)\n"),
    "pages/new/a.md": A_PAGE.replace("(../old/index.md)", "(index.md)").replace(
        "(keep.md#细节)", "(../old/keep.md#细节)"
    ),
    "pages/new/b.md": B_PAGE,
}


def test_move_reparents_and_repairs_both_directions(
    draft_factory: DraftFactory,
) -> None:
    root = build_two_hub_draft(draft_factory)

    move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))

    assert snapshot(root) == EXPECTED_REPARENTED_FILES
    assert check_draft(root).errors == ()


def test_move_preserves_pure_in_page_anchor(draft_factory: DraftFactory) -> None:
    """页面移入子目录时，`#节` 指向来源页面自身，必须保持原文不被重写。"""
    root = build_two_hub_draft(draft_factory)
    a_page = root / "pages/old/a.md"
    a_page.write_text(
        a_page.read_text(encoding="utf-8") + "\n见 [本页](#节)。\n",
        encoding="utf-8",
    )

    move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))

    moved = (root / "pages/new/a.md").read_text(encoding="utf-8")
    assert "见 [本页](#节)。" in moved
    assert "../old#节" not in moved


def test_move_hub_relocates_complete_subtree(draft_factory: DraftFactory) -> None:
    root = build_two_hub_draft(draft_factory)

    move_page(root, Path("pages/old/index.md"), Path("pages/archive/index.md"))

    files = snapshot(root)
    assert set(files) == {
        "index.md",
        "conclusions.md",
        "pages/archive/index.md",
        "pages/archive/a.md",
        "pages/archive/keep.md",
        "pages/new/index.md",
        "pages/new/b.md",
    }
    assert files["pages/archive/index.md"] == OLD_HUB
    assert files["pages/archive/a.md"] == A_PAGE.replace("(../old/index.md)", "(index.md)")
    assert files["pages/archive/keep.md"] == KEEP.replace("(../old/index.md)", "(index.md)")
    assert "(pages/archive/index.md)" in files["index.md"]
    # 机器所有权下结论列表中的旧链接保持原样，不随结构命令重写（规格 §12.5）。
    assert "(pages/old/a.md)" in files["conclusions.md"]
    assert not (root / "pages/old").exists()
    assert check_draft(root).errors == ()


def test_move_into_own_descendant_fails(draft_factory: DraftFactory) -> None:
    root = build_two_hub_draft(draft_factory)

    with pytest.raises(DraftError, match="structure.descendant"):
        move_page(root, Path("pages/old/index.md"), Path("pages/old/sub/index.md"))


def test_move_overwrite_fails(draft_factory: DraftFactory) -> None:
    root = build_two_hub_draft(draft_factory)

    with pytest.raises(DraftError, match="structure.target_exists"):
        move_page(root, Path("pages/old/a.md"), Path("pages/new/b.md"))


def test_candidate_move_succeeds_with_unrelated_parseable_error(
    draft_factory: DraftFactory,
) -> None:
    """候选根中无关的已可解析全局错误不阻止结构移动（计划 Task 10 Step 1）。"""
    root = draft_factory(
        {
            "index.md": INDEX,
            "conclusions.md": CONCLUSIONS,
            "pages/old/index.md": OLD_HUB,
            "pages/old/a.md": A_PAGE,
            "pages/old/keep.md": KEEP,
            "pages/new/index.md": NEW_HUB,
            "pages/new/b.md": B_PAGE,
        },
        root_state=RootState.CANDIDATE,
    )
    # 无关页面父级合法但未被根导航声明：PARENT_NAVIGATION_MISSING，可解析但全局不健康。
    (root / "pages/broken.md").write_text(
        "# 孤儿\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n正文。\n",
        encoding="utf-8",
    )

    move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))

    assert (root / "pages/new/a.md").is_file()


def test_move_with_dirty_graph_fails(draft_factory: DraftFactory) -> None:
    """结构性脏图（关系目标缺失）阻止移动，并传播最具体的损坏码。"""
    root = build_two_hub_draft(draft_factory)
    (root / "pages/broken.md").write_text(
        "# 孤儿\n\n> 状态：`待讨论`\n>\n> 父主题：[缺失](../missing.md)\n\n正文。\n",
        encoding="utf-8",
    )

    with pytest.raises(DraftError, match=CheckCode.LINK_MISSING.value):
        move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))


def test_registered_move_rejects_missing_body_link(
    draft_factory: DraftFactory,
) -> None:
    """registered 根规划前必须通过完整全局健康门禁：正文断链也阻止写操作。"""
    root = build_two_hub_draft(draft_factory)
    (root / "pages/new/b.md").write_text(
        B_PAGE + "\n见 [断链](missing.md)。\n",
        encoding="utf-8",
    )

    with pytest.raises(DraftError, match=CheckCode.LINK_MISSING.value):
        move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))


def test_candidate_move_tolerates_missing_body_link(
    draft_factory: DraftFactory,
) -> None:
    """candidate 根中正文链接目标缺失是已知资源事实：移动不因它失败。"""
    root = draft_factory(
        {
            "index.md": INDEX,
            "conclusions.md": CONCLUSIONS,
            "pages/old/index.md": OLD_HUB,
            "pages/old/a.md": A_PAGE,
            "pages/old/keep.md": KEEP,
            "pages/new/index.md": NEW_HUB,
            "pages/new/b.md": B_PAGE,
        },
        root_state=RootState.CANDIDATE,
    )
    (root / "pages/new/b.md").write_text(
        B_PAGE + "\n见 [断链](missing.md)。\n",
        encoding="utf-8",
    )

    move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))

    assert (root / "pages/new/a.md").is_file()


def test_move_target_escaping_root_fails(draft_factory: DraftFactory) -> None:
    root = build_two_hub_draft(draft_factory)

    with pytest.raises(DraftError, match="path.escape"):
        move_page(root, Path("pages/old/a.md"), Path("../outside.md"))


def test_interrupted_move_recovers_to_same_final_state(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = build_two_hub_draft(draft_factory)
    calls = 0

    def crash_on_second(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected crash")
        source.replace(target)

    monkeypatch.setattr("draft.operations.replace_file", crash_on_second)
    with pytest.raises(RuntimeError, match="injected crash"):
        move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))
    monkeypatch.undo()

    result = recover(root)

    assert result.phase == "completed"
    assert snapshot(root) == EXPECTED_REPARENTED_FILES
    assert check_draft(root).errors == ()


SOLO_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [单人枢纽](pages/solo/index.md)
- [结论汇总](conclusions.md)
"""

SOLO_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论
"""

SOLO_HUB = """# 单人枢纽

> 状态：`已关闭：纯导航页，不表达独立结论`
>
> 父主题：[根索引](../../index.md)

## 子主题

- [唯一](only.md)
"""

SOLO_ONLY = """# 唯一

> 状态：`待讨论`
>
> 父主题：[单人枢纽](index.md)

唯一的正文。
"""


def build_solo_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": SOLO_INDEX,
            "conclusions.md": SOLO_CONCLUSIONS,
            "pages/solo/index.md": SOLO_HUB,
            "pages/solo/only.md": SOLO_ONLY,
        }
    )


def test_moving_last_child_auto_collapses_parent_without_cascade(
    draft_factory: DraftFactory,
) -> None:
    root = build_solo_draft(draft_factory)

    move_page(root, Path("pages/solo/only.md"), Path("pages/only.md"))

    files = snapshot(root)
    assert set(files) == {"index.md", "conclusions.md", "pages/solo.md", "pages/only.md"}
    assert files["pages/solo.md"] == SOLO_HUB.replace("- [唯一](only.md)\n", "").replace(
        "(../../index.md)", "(../index.md)"
    )
    assert files["pages/only.md"] == SOLO_ONLY.replace("(index.md)", "(../index.md)")
    assert "- [单人枢纽](pages/solo.md)" in files["index.md"]
    assert "- [唯一](pages/only.md)" in files["index.md"]
    assert not (root / "pages/solo").exists()
    assert check_draft(root).errors == ()


def test_explicit_collapse_accepts_empty_non_root_hub(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": SOLO_INDEX,
            "conclusions.md": SOLO_CONCLUSIONS,
            "pages/solo/index.md": SOLO_HUB.replace("## 子主题\n\n- [唯一](only.md)\n", ""),
        }
    )

    collapse_hub(root, Path("pages/solo/index.md"))

    files = snapshot(root)
    assert set(files) == {"index.md", "conclusions.md", "pages/solo.md"}
    assert files["pages/solo.md"] == SOLO_HUB.replace(
        "## 子主题\n\n- [唯一](only.md)\n", ""
    ).replace("(../../index.md)", "(../index.md)")
    assert "- [单人枢纽](pages/solo.md)" in files["index.md"]
    assert check_draft(root).errors == ()


def test_collapse_rejects_root_nonempty_hub_and_non_hub_operand(
    draft_factory: DraftFactory,
) -> None:
    root = build_solo_draft(draft_factory)

    with pytest.raises(DraftError, match="structure.protected"):
        collapse_hub(root, Path("index.md"))
    with pytest.raises(DraftError, match="structure.hub_not_empty"):
        collapse_hub(root, Path("pages/solo/index.md"))
    with pytest.raises(DraftError, match="structure.not_hub"):
        collapse_hub(root, Path("pages/solo/only.md"))


PROMOTE_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [主题](pages/topic.md)
- [甲](pages/child-a.md)
- [乙](pages/child-b.md)
- [结论汇总](conclusions.md)
"""

PROMOTE_TOPIC = """# 主题

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

主题的正文。
"""

PROMOTE_CHILD_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲的正文。
"""

PROMOTE_CHILD_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

乙的正文。
"""


def build_promote_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": PROMOTE_INDEX,
            "conclusions.md": SOLO_CONCLUSIONS,
            "pages/topic.md": PROMOTE_TOPIC,
            "pages/child-a.md": PROMOTE_CHILD_A,
            "pages/child-b.md": PROMOTE_CHILD_B,
        }
    )


def test_move_crlf_page_preserves_unmodified_bytes(
    draft_factory: DraftFactory,
) -> None:
    """CRLF 页面移动后：未修改字节（含 \r\n）保持不变，只重写协议要求的链接跨度。"""
    root = build_two_hub_draft(draft_factory)
    page = root / "pages/old/a.md"
    page.write_bytes(page.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))

    move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))

    moved = (root / "pages/new/a.md").read_bytes()
    assert b"\r\n" in moved
    # 正文"关联 [留存](keep.md#细节)"的未修改部分保持 CRLF。
    assert moved.split(b"\n")[0].endswith(b"\r")
    assert check_draft(root).errors == ()


def test_promote_crash_after_first_new_hub_write_recovers(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """promote 首次创建嵌套目录时，新文件落盘但 applied=true 尚未持久化的崩溃窗口必须可恢复。

    恢复必须解释新建文件对其缺失祖先目录（pages/topic）的成员变化，不能因 applied
    滞后而把自身创建误判为外部修改。
    """
    import draft.operations as ops_module
    from draft.operations import read_lock, recover

    root = build_promote_draft(draft_factory)
    original_apply_write = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]

    def write_then_crash(root_path: Path, target: Path, content: bytes) -> None:
        original_apply_write(root_path, target, content)
        if str(target).startswith("pages/topic/"):
            raise RuntimeError("injected crash after new hub file lands")

    monkeypatch.setattr(ops_module, "_apply_write", write_then_crash)
    with pytest.raises(RuntimeError, match="injected crash after new hub file lands"):
        promote_page(
            root, Path("pages/topic.md"), [Path("pages/child-a.md"), Path("pages/child-b.md")]
        )
    monkeypatch.undo()
    assert read_lock(root) is not None

    result = recover(root)

    assert result.phase == "completed"
    assert (root / "pages/topic/index.md").is_file()
    assert (root / "pages/topic/child-a.md").is_file()
    assert (root / "pages/topic/child-b.md").is_file()
    assert not (root / "pages/topic.md").exists()
    assert read_lock(root) is None
    assert check_draft(root).errors == ()


def _inject_intruder(root: Path, entry_type: str) -> Path:
    """向 pages/topic 注入指定类型的未指定条目，返回其路径。"""
    topic = root / "pages/topic"
    if entry_type == "file":
        path = topic / "intruder.txt"
        path.write_text("plain file\n", encoding="utf-8")
    elif entry_type == "markdown":
        path = topic / "intruder.md"
        # 含指向旧路径的链接：吸收后旧语义会被错误承接。
        path.write_text(
            "# 入侵者\n\n> 状态：`待讨论`\n>\n> 父主题：[主题](../topic.md)\n\n入侵正文。\n",
            encoding="utf-8",
        )
    elif entry_type == "symlink":
        path = topic / "intruder-link"
        external = root / ".draft" / "external-target.md"
        external.write_text("# 外部\n", encoding="utf-8")
        path.symlink_to(external)
    elif entry_type == "tempfile":
        # temp-like 名称但本操作从未登记：所有权过滤不得放行。
        path = topic / f"intruder.tmp-{'b' * 32}"
        path.write_text("# 临时形状入侵者\n", encoding="utf-8")
    else:  # pragma: no cover
        raise AssertionError(entry_type)
    return path


@pytest.mark.parametrize("entry_type", ["file", "markdown", "symlink", "tempfile"])
def test_promote_crash_recovery_rejects_intruder_in_created_directory(
    draft_factory: DraftFactory,
    monkeypatch: pytest.MonkeyPatch,
    entry_type: str,
) -> None:
    """promote 首次创建的新目录内混入普通文件/页面/符号链接时，恢复必须拒绝并保留现场。

    回归 P1：计划期 pages/topic 不存在、没有观察基线；若把"解释祖先目录出现"当成
    证明新目录内部只含本操作条目，恢复会把外部条目静默吸收并完成提交。
    """
    import draft.operations as ops_module
    from draft.operations import operation_journal_path, read_journal, read_lock

    root = build_promote_draft(draft_factory)
    original_apply_write = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]

    def write_then_crash(root_path: Path, target: Path, content: bytes) -> None:
        original_apply_write(root_path, target, content)
        if str(target).startswith("pages/topic/"):
            raise RuntimeError("injected crash after new hub file lands")

    monkeypatch.setattr(ops_module, "_apply_write", write_then_crash)
    with pytest.raises(RuntimeError, match="injected crash after new hub file lands"):
        promote_page(
            root, Path("pages/topic.md"), [Path("pages/child-a.md"), Path("pages/child-b.md")]
        )
    monkeypatch.undo()
    assert read_lock(root) is not None

    # 外部向本操作新建的目录注入未指定条目。
    intruder = _inject_intruder(root, entry_type)

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    # 可恢复现场保留：锁、日志与外部条目都在。
    held_id = read_lock(root)
    assert held_id is not None
    assert intruder.is_symlink() or intruder.is_file()
    journal = read_journal(operation_journal_path(root, held_id))
    assert journal.phase in (
        ops_module.OperationPhase.PREPARED,
        ops_module.OperationPhase.APPLYING,
    )


def test_promote_crash_before_replace_recovers_created_directory(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkdir 后、目标文件替换前崩溃：本操作创建的空目录不得被误判为外部变化。

    回归 H2：_apply_write 先 mkdir(parents=True) 再写临时文件替换目标；若进程在
    父目录已创建、目标尚未替换时退出，目录只含本操作临时文件。恢复若只把 live
    状态为 after 的新文件计入创建，会把该空目录误判为 external_change 并保留锁。
    """
    import draft.operations as ops_module
    from draft.operations import read_lock, recover

    root = build_promote_draft(draft_factory)
    original_replace = ops_module.replace_file  # pyright: ignore[reportPrivateUsage]

    def crash_before_replace(source: Path, target: Path) -> None:
        if "pages/topic/" in str(target):
            raise RuntimeError("injected crash before replace")
        original_replace(source, target)

    monkeypatch.setattr(ops_module, "replace_file", crash_before_replace)
    with pytest.raises(RuntimeError, match="injected crash before replace"):
        promote_page(
            root, Path("pages/topic.md"), [Path("pages/child-a.md"), Path("pages/child-b.md")]
        )
    monkeypatch.undo()
    assert read_lock(root) is not None
    assert (root / "pages/topic").is_dir()

    result = recover(root)

    assert result.phase == "completed"
    assert read_lock(root) is None
    assert (root / "pages/topic/index.md").is_file()
    assert (root / "pages/topic/child-a.md").is_file()
    # 本操作崩溃遗留的同名临时文件被清理，不留下孤儿。
    assert not [path for path in (root / "pages/topic").iterdir() if ".tmp-" in path.name]
    assert check_draft(root).errors == ()


def test_promote_crash_recovery_rejects_intruder_in_nested_created_directory(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """子枢纽（嵌套新目录）恢复：深层目录内的未指定条目必须被拒绝。

    回归 H2：缺失证明必须按深度传递（pages/topic 缺失则 pages/topic/sub 也缺失），
    只传播一层会把深层新目录的内部成员漏检并静默吸收。
    """
    import draft.operations as ops_module
    from draft.operations import read_lock

    root = draft_factory(
        {
            "index.md": PROMOTE_INDEX.replace(
                "- [乙](pages/child-b.md)",
                "- [子枢纽](pages/sub/index.md)",
            ),
            "conclusions.md": SOLO_CONCLUSIONS,
            "pages/topic.md": PROMOTE_TOPIC,
            "pages/child-a.md": PROMOTE_CHILD_A,
            "pages/sub/index.md": (
                "# 子枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n"
                "> 父主题：[根索引](../../index.md)\n\n- [乙](child-b.md)\n"
            ),
            "pages/sub/child-b.md": (
                "# 乙\n\n> 状态：`待讨论`\n>\n> 父主题：[子枢纽](index.md)\n\n乙的正文。\n"
            ),
        }
    )
    original_apply_write = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]

    def write_then_crash(root_path: Path, target: Path, content: bytes) -> None:
        original_apply_write(root_path, target, content)
        if str(target).startswith("pages/topic/sub/"):
            raise RuntimeError("injected crash after nested hub file lands")

    monkeypatch.setattr(ops_module, "_apply_write", write_then_crash)
    with pytest.raises(RuntimeError, match="injected crash after nested hub file lands"):
        promote_page(root, Path("pages/topic.md"), [Path("pages/sub/index.md")])
    monkeypatch.undo()
    assert read_lock(root) is not None
    assert (root / "pages/topic/sub").is_dir()

    (root / "pages/topic/sub/intruder.txt").write_text("入侵正文\n", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    held_id = read_lock(root)
    assert held_id is not None
    assert (root / "pages/topic/sub/intruder.txt").is_file()


def test_promote_becomes_hub_and_reparents_children(
    draft_factory: DraftFactory,
) -> None:
    root = build_promote_draft(draft_factory)

    promote_page(root, Path("pages/topic.md"), [Path("pages/child-a.md"), Path("pages/child-b.md")])

    files = snapshot(root)
    assert set(files) == {
        "index.md",
        "conclusions.md",
        "pages/topic/index.md",
        "pages/topic/child-a.md",
        "pages/topic/child-b.md",
    }
    assert files["pages/topic/index.md"] == PROMOTE_TOPIC.replace(
        "(../index.md)", "(../../index.md)"
    ).replace(
        "主题的正文。\n",
        "主题的正文。\n\n## 子主题\n\n- [甲](child-a.md)\n- [乙](child-b.md)\n",
    )
    assert files["pages/topic/child-a.md"] == PROMOTE_CHILD_A.replace("(../index.md)", "(index.md)")
    assert files["pages/topic/child-b.md"] == PROMOTE_CHILD_B.replace("(../index.md)", "(index.md)")
    assert "- [主题](pages/topic/index.md)" in files["index.md"]
    assert "- [甲]" not in files["index.md"]
    assert "- [乙]" not in files["index.md"]
    assert check_draft(root).errors == ()


def test_promote_requires_at_least_one_child(draft_factory: DraftFactory) -> None:
    root = build_promote_draft(draft_factory)

    with pytest.raises(DraftError, match="structure.missing_child"):
        promote_page(root, Path("pages/topic.md"), [])


def test_promote_rejects_duplicate_basenames(draft_factory: DraftFactory) -> None:
    root = draft_factory(
        {
            "index.md": PROMOTE_INDEX.replace(
                "- [乙](pages/child-b.md)",
                "- [乙](pages/child-b.md)\n- [子枢纽](pages/sub/index.md)",
            ),
            "conclusions.md": SOLO_CONCLUSIONS,
            "pages/topic.md": PROMOTE_TOPIC,
            "pages/child-a.md": PROMOTE_CHILD_A,
            "pages/child-b.md": PROMOTE_CHILD_B,
            "pages/sub/index.md": (
                "# 子枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n"
                "> 父主题：[根索引](../../index.md)\n\n- [甲](child-a.md)\n"
            ),
            "pages/sub/child-a.md": (
                "# 二甲\n\n> 状态：`待讨论`\n>\n> 父主题：[子枢纽](index.md)\n\n二甲的正文。\n"
            ),
        }
    )

    with pytest.raises(DraftError, match="structure.duplicate_basename"):
        promote_page(
            root,
            Path("pages/topic.md"),
            [Path("pages/child-a.md"), Path("pages/sub/child-a.md")],
        )


def test_promote_moves_child_hub_as_complete_subtree(
    draft_factory: DraftFactory,
) -> None:
    root = draft_factory(
        {
            "index.md": PROMOTE_INDEX.replace(
                "- [乙](pages/child-b.md)",
                "- [子枢纽](pages/sub/index.md)",
            ),
            "conclusions.md": SOLO_CONCLUSIONS,
            "pages/topic.md": PROMOTE_TOPIC,
            "pages/child-a.md": PROMOTE_CHILD_A,
            "pages/sub/index.md": (
                "# 子枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n"
                "> 父主题：[根索引](../../index.md)\n\n- [乙](child-b.md)\n"
            ),
            "pages/sub/child-b.md": (
                "# 乙\n\n> 状态：`待讨论`\n>\n> 父主题：[子枢纽](index.md)\n\n乙的正文。\n"
            ),
        }
    )

    promote_page(root, Path("pages/topic.md"), [Path("pages/sub/index.md")])

    files = snapshot(root)
    assert set(files) == {
        "index.md",
        "conclusions.md",
        "pages/topic/index.md",
        "pages/topic/sub/index.md",
        "pages/topic/sub/child-b.md",
        "pages/child-a.md",
    }
    assert files["pages/topic/sub/index.md"] == (
        "# 子枢纽\n\n> 状态：`已关闭：纯导航页`\n>\n"
        "> 父主题：[根索引](../index.md)\n\n- [乙](child-b.md)\n"
    )
    assert files["pages/topic/sub/child-b.md"] == (
        "# 乙\n\n> 状态：`待讨论`\n>\n> 父主题：[子枢纽](index.md)\n\n乙的正文。\n"
    )
    assert not (root / "pages/sub").exists()
    assert check_draft(root).errors == ()


def test_same_span_in_other_page_is_not_silenced(
    draft_factory: DraftFactory,
) -> None:
    from draft.markdown import parse_page

    hub = parse_page(Path("pages/old/index.md"), OLD_HUB)
    nav = next(link for link in hub.links if link.destination == "a.md")
    prefix = "# 留存\n\n> 状态：`待讨论`\n>\n> 父主题：[旧枢纽](../old/index.md)\n\n"
    padding = nav.whole_span.start - len(prefix)
    assert padding >= 0
    keep = prefix + "填" * padding + "[A](a.md)。\n"
    root = draft_factory(
        {
            "index.md": INDEX,
            "conclusions.md": CONCLUSIONS,
            "pages/old/index.md": OLD_HUB,
            "pages/old/a.md": A_PAGE,
            "pages/old/keep.md": keep,
            "pages/new/index.md": NEW_HUB,
            "pages/new/b.md": B_PAGE,
        }
    )

    move_page(root, Path("pages/old/a.md"), Path("pages/new/a.md"))

    moved_keep = (root / "pages/old/keep.md").read_text(encoding="utf-8")
    assert "../new/a.md" in moved_keep
    assert check_draft(root).errors == ()


# ---------------------------------------------------------------------------
# 惰性推进与只读诊断（设计 §13.5）：结构写前置推进、恢复/检查不推进。
# ---------------------------------------------------------------------------

LAZY_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
- [乙](pages/b.md)
- [结论汇总](conclusions.md)
"""

LAZY_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [甲](pages/a.md)
"""

LAZY_A = """# 甲

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

甲。
"""

LAZY_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

乙。
"""


def _lazy_root(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": LAZY_INDEX,
            "conclusions.md": LAZY_CONCLUSIONS,
            "pages/a.md": LAZY_A,
            "pages/b.md": LAZY_B,
        }
    )


DUE_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def test_domain_move_does_not_preflight_due_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """领域 move_page 不再推进生命周期：前置推进由 CLI 编排统一完成。"""
    root = _lazy_root(draft_factory)
    trash_page(root, Path("pages/b.md"), now=DUE_NOW)

    calls: list[str] = []
    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", calls.append)
    result = move_page(
        root, Path("pages/a.md"), Path("pages/a2.md"), now=datetime(2026, 6, 10, tzinfo=UTC)
    )

    assert calls == []
    assert (root / "pages/a2.md").is_file()
    assert result.warnings == ()


def test_domain_move_corrupt_package_blocks_via_clean_graph(
    draft_factory: DraftFactory,
) -> None:
    root = _lazy_root(draft_factory)
    pending = trash_page(root, Path("pages/b.md"), now=DUE_NOW)
    (root / ".draft/trash/outgoing" / pending.operation_id / "package.json").write_text(
        "corrupt\n", encoding="utf-8"
    )
    before = (root / "pages/a.md").read_bytes()

    with pytest.raises(DraftError, match=CheckCode.TRASH_PACKAGE_CORRUPT.value):
        move_page(root, Path("pages/a.md"), Path("pages/a2.md"))

    assert (root / "pages/a.md").read_bytes() == before
    assert not (root / "pages/a2.md").exists()


def test_restore_does_not_advance_any_package(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = draft_factory(
        {
            "index.md": LAZY_INDEX.replace(
                "- [乙](pages/b.md)",
                "- [乙](pages/b.md)\n- [丙](pages/c.md)",
            ),
            "conclusions.md": LAZY_CONCLUSIONS,
            "pages/a.md": LAZY_A,
            "pages/b.md": LAZY_B,
            "pages/c.md": "# 丙\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n丙。\n",
        }
    )
    # 两个回收包都在恢复窗口内创建（互相不触发推进）。
    due = trash_page(root, Path("pages/b.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    retained = trash_page(root, Path("pages/c.md"), now=datetime(2026, 7, 21, tzinfo=UTC))
    # 手工把 due 包的期限改到已过期但未到清理期限，模拟“已到期、尚未被推进”。
    import json

    from draft.operations import TrashDeadlines

    due_path = root / ".draft/operations" / due.operation_id / "journal.json"
    due_data = json.loads(due_path.read_text(encoding="utf-8"))
    due_data["metadata"].update(
        TrashDeadlines.from_created_at(datetime(2026, 7, 10, tzinfo=UTC)).to_metadata()
    )
    due_path.write_text(json.dumps(due_data), encoding="utf-8")

    calls: list[str] = []
    monkeypatch.setattr("draft.trash_lifecycle._default_handoff", calls.append)
    from draft.trash import restore_retained_trash

    restore_retained_trash(root, retained.operation_id, now=datetime(2026, 7, 21, 6, 0, tzinfo=UTC))

    # 恢复只处理指定包：不推进任何包，适配器零调用，due 包原样保留。
    assert calls == []
    due_journal = json.loads(due_path.read_text(encoding="utf-8"))
    assert due_journal["phase"] == "trash_retained"
    assert (root / ".draft/trash/outgoing" / due.operation_id).is_dir()
