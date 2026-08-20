from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.models import RootState
from draft.operations import recover
from draft.query import load_tolerant_view
from draft.structure import collapse_hub, move_page, promote_page
from draft.trash import trash_page
from tests.integration.support import DraftFactory, snapshot_tree

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
    import draft.operations as ops_module

    root = build_two_hub_draft(draft_factory)
    original_apply_write = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]
    calls = {"n": 0}

    def crash_on_second_apply(
        root_path: Path,
        target: Path,
        content: bytes,
        *,
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        calls["n"] += 1
        original_apply_write(
            root_path,
            target,
            content,
            expected_current=expected_current,
            code_path=code_path,
        )
        if calls["n"] == 2:  # 第二次 apply 已落盘、日志未推进：崩溃。
            raise RuntimeError("injected crash")

    monkeypatch.setattr(ops_module, "_apply_write", crash_on_second_apply)
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

    def write_then_crash(
        root_path: Path,
        target: Path,
        content: bytes,
        *,
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        original_apply_write(
            root_path,
            target,
            content,
            expected_current=expected_current,
            code_path=code_path,
        )
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

    def write_then_crash(
        root_path: Path,
        target: Path,
        content: bytes,
        *,
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        original_apply_write(
            root_path,
            target,
            content,
            expected_current=expected_current,
            code_path=code_path,
        )
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
    original_write = ops_module._AnchoredLeaf.write  # pyright: ignore[reportPrivateUsage]

    def crash_before_publish(self: Any, content: bytes) -> None:
        # 目录链已创建、目标文件发布前崩溃：本操作创建的空目录遗留。
        if "pages/topic/" in str(self.display):
            raise RuntimeError("injected crash before publish")
        original_write(self, content)

    monkeypatch.setattr(
        ops_module._AnchoredLeaf,  # pyright: ignore[reportPrivateUsage]
        "write",
        crash_before_publish,
    )
    with pytest.raises(RuntimeError, match="injected crash before publish"):
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


def test_promote_crash_before_replace_rejects_intruder_in_created_directory(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkdir 后、目标替换前，新目录内混入外部成员：恢复必须拒绝（回归 H3）。

    目标文件仍是 before 时，本操作创建的空目录也必须形成明确观察（成员可为空）；
    否则外部注入会被吸收，recover 错误地 completed 并保留外部条目。
    """
    import draft.operations as ops_module
    from draft.operations import read_lock, recover

    root = build_promote_draft(draft_factory)
    original_write = ops_module._AnchoredLeaf.write  # pyright: ignore[reportPrivateUsage]

    def crash_before_publish(self: Any, content: bytes) -> None:
        # 目录链已创建、目标文件发布前崩溃：本操作创建的空目录遗留。
        if "pages/topic/" in str(self.display):
            raise RuntimeError("injected crash before publish")
        original_write(self, content)

    monkeypatch.setattr(
        ops_module._AnchoredLeaf,  # pyright: ignore[reportPrivateUsage]
        "write",
        crash_before_publish,
    )
    with pytest.raises(RuntimeError, match="injected crash before publish"):
        promote_page(
            root, Path("pages/topic.md"), [Path("pages/child-a.md"), Path("pages/child-b.md")]
        )
    monkeypatch.undo()
    assert read_lock(root) is not None
    assert (root / "pages/topic").is_dir()

    # 外部向本操作创建但目标尚未替换的空目录注入成员。
    (root / "pages/topic/intruder.txt").write_text("入侵\n", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    held_id = read_lock(root)
    assert held_id is not None
    assert (root / "pages/topic/intruder.txt").is_file()


def test_promote_crash_recovery_rejects_same_basename_unregistered_temp(
    draft_factory: DraftFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """与 WRITE 目标同基础名但未登记的 temp-like 文件必须按外部内容拒绝（回归 H4）。

    临时文件所有权必须绑定精确路径（operation_id + mutation index），文件名形状
    不是证据：目标已落盘后，同名不同 hex 的未登记 temp-like 文件不得被过滤吸收。
    """
    import draft.operations as ops_module
    from draft.operations import read_lock, recover

    root = build_promote_draft(draft_factory)
    original_apply_write = ops_module._apply_write  # pyright: ignore[reportPrivateUsage]

    def write_then_crash(
        root_path: Path,
        target: Path,
        content: bytes,
        *,
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        original_apply_write(
            root_path,
            target,
            content,
            expected_current=expected_current,
            code_path=code_path,
        )
        if str(target).startswith("pages/topic/"):
            raise RuntimeError("injected crash after new hub file lands")

    monkeypatch.setattr(ops_module, "_apply_write", write_then_crash)
    with pytest.raises(RuntimeError, match="injected crash after new hub file lands"):
        promote_page(
            root, Path("pages/topic.md"), [Path("pages/child-a.md"), Path("pages/child-b.md")]
        )
    monkeypatch.undo()
    assert read_lock(root) is not None
    # 崩溃发生在首个 pages/topic 写（child-a.md）：目标已落盘。
    assert (root / "pages/topic/child-a.md").is_file()

    # 与已落盘目标同基础名、但非本操作登记的精确路径的 temp-like 文件。
    external_temp = root / "pages/topic" / f"child-a.md.tmp-{'c' * 32}"
    external_temp.write_text("# 外部\n", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover(root)

    held_id = read_lock(root)
    assert held_id is not None
    assert external_temp.is_file()


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

    def write_then_crash(
        root_path: Path,
        target: Path,
        content: bytes,
        *,
        expected_current: str | None,
        code_path: Path,
    ) -> None:
        original_apply_write(
            root_path,
            target,
            content,
            expected_current=expected_current,
            code_path=code_path,
        )
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
# 非父正式关系链接的目标重写（round-18 H1）
# ---------------------------------------------------------------------------

REL_INDEX = """# 主题

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [A](pages/a.md)
- [B](pages/b.md)
- [结论汇总](conclusions.md)
"""

REL_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论
"""

REL_A = """# A

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[依赖](b.md)

看 [B](b.md)。
"""

REL_B = """# B

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

看 [A](a.md)。
"""


def _relation_move_root(draft_factory: DraftFactory, *, root_state: RootState) -> Path:
    return draft_factory(
        {
            "index.md": REL_INDEX,
            "conclusions.md": REL_CONCLUSIONS,
            "pages/a.md": REL_A,
            "pages/b.md": REL_B,
        },
        root_state=root_state,
    )


def test_candidate_move_rewrites_inbound_relation_target(
    draft_factory: DraftFactory,
) -> None:
    """candidate 根：move 后入站 depends_on 目标必须重写，check 零错误（round-18 实验一）。"""
    root = _relation_move_root(draft_factory, root_state=RootState.CANDIDATE)

    move_page(root, Path("pages/b.md"), Path("pages/b2.md"))

    a_text = (root / "pages/a.md").read_text(encoding="utf-8")
    assert "> 依赖：[依赖](b2.md)" in a_text
    assert "> 依赖：[依赖](b.md)" not in a_text
    assert check_draft(root).errors == ()


def test_registered_move_rewrites_inbound_relation_target(
    draft_factory: DraftFactory,
) -> None:
    """registered 根：入站关系目标的页面必须可移动，重写后应用后完整检查通过（round-18 实验二）。"""
    root = _relation_move_root(draft_factory, root_state=RootState.REGISTERED)

    result = move_page(root, Path("pages/b.md"), Path("pages/b2.md"))

    assert result.phase == "completed"
    assert (root / "pages/b2.md").is_file()
    assert not (root / "pages/b.md").exists()
    a_text = (root / "pages/a.md").read_text(encoding="utf-8")
    assert "> 依赖：[依赖](b2.md)" in a_text
    assert check_draft(root).errors == ()


PROMOTE_REL_INDEX = """# 主题

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [主题](pages/topic.md)
- [甲](pages/child-a.md)
- [乙](pages/child-b.md)
- [笔记](pages/notes.md)
- [结论汇总](conclusions.md)
"""

PROMOTE_REL_TOPIC = """# 主题

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[依赖](child-b.md)

主题正文。
"""

PROMOTE_REL_CHILD = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲的正文。
"""

PROMOTE_REL_CHILD_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

乙的正文。
"""

PROMOTE_REL_NOTES = """# 笔记

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[依赖](topic.md)
>
> 依赖：[依赖](child-a.md)

笔记正文。
"""


def test_promote_rewrites_relations_across_depth(draft_factory: DraftFactory) -> None:
    """promote 跨深度移动：被移动页声明的出站关系与指向被移动页的入站关系都重写。"""
    root = draft_factory(
        {
            "index.md": PROMOTE_REL_INDEX,
            "conclusions.md": REL_CONCLUSIONS,
            "pages/topic.md": PROMOTE_REL_TOPIC,
            "pages/child-a.md": PROMOTE_REL_CHILD,
            "pages/child-b.md": PROMOTE_REL_CHILD_B,
            "pages/notes.md": PROMOTE_REL_NOTES,
        }
    )

    promote_page(root, Path("pages/topic.md"), [Path("pages/child-a.md")])

    topic = (root / "pages/topic/index.md").read_text(encoding="utf-8")
    # 被移动页自己声明的出站关系：目标未动，跨深度后相对目标必须重算。
    assert "> 依赖：[依赖](../child-b.md)" in topic
    assert "> 依赖：[依赖](child-b.md)" not in topic
    notes = (root / "pages/notes.md").read_text(encoding="utf-8")
    # 指向被移动页（主题与新枢纽）的入站关系：目标跨深度移动。
    assert "> 依赖：[依赖](topic/index.md)" in notes
    assert "> 依赖：[依赖](topic/child-a.md)" in notes
    assert "> 依赖：[依赖](topic.md)" not in notes
    assert check_draft(root).errors == ()


PAIR_TOPIC = """# 主题

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)
>
> 取代：[取代](child-a.md)

主题正文。
"""

PAIR_CHILD_A = """# 甲

> 状态：`已取代`
>
> 父主题：[根索引](../index.md)
>
> 被取代：[被取代](topic.md)

甲的正文。
"""


def test_promote_rewrites_bidirectional_pair_with_both_ends_moved(
    draft_factory: DraftFactory,
) -> None:
    """promote 同时移动取代配对两端：两条关系目标都按新几何重写，配对语义保持。"""
    root = draft_factory(
        {
            "index.md": PROMOTE_REL_INDEX.replace("- [笔记](pages/notes.md)\n", ""),
            "conclusions.md": REL_CONCLUSIONS,
            "pages/topic.md": PAIR_TOPIC,
            "pages/child-a.md": PAIR_CHILD_A,
            "pages/child-b.md": PROMOTE_REL_CHILD_B,
        }
    )

    promote_page(root, Path("pages/topic.md"), [Path("pages/child-a.md")])

    topic = (root / "pages/topic/index.md").read_text(encoding="utf-8")
    child_a = (root / "pages/topic/child-a.md").read_text(encoding="utf-8")
    # 取代方向（主题 -> 甲）：两端落在同一子目录，目标字符串不变但必须正确。
    assert "> 取代：[取代](child-a.md)" in topic
    # 被取代方向（甲 -> 主题）：目标页也移动，重写为新几何下的枢纽目标。
    assert "> 被取代：[被取代](index.md)" in child_a
    assert "> 被取代：[被取代](topic.md)" not in child_a
    assert check_draft(root).errors == ()


COLLAPSE_INDEX = """# 主题

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [枢纽](pages/x/index.md)
- [笔记](pages/notes.md)
- [结论汇总](conclusions.md)
"""

COLLAPSE_HUB = """# 枢纽

> 状态：`已关闭：纯导航页，不表达独立结论`
>
> 父主题：[根索引](../../index.md)
"""

COLLAPSE_NOTES = """# 笔记

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[依赖](x/index.md)

笔记正文。
"""


def test_collapse_rewrites_inbound_relation_to_hub(draft_factory: DraftFactory) -> None:
    """collapse：空枢纽折叠为叶页后，指向枢纽的入站关系目标必须同步重写。"""
    root = draft_factory(
        {
            "index.md": COLLAPSE_INDEX,
            "conclusions.md": REL_CONCLUSIONS,
            "pages/x/index.md": COLLAPSE_HUB,
            "pages/notes.md": COLLAPSE_NOTES,
        }
    )

    collapse_hub(root, Path("pages/x/index.md"))

    notes = (root / "pages/notes.md").read_text(encoding="utf-8")
    assert "> 依赖：[依赖](x.md)" in notes
    assert "> 依赖：[依赖](x/index.md)" not in notes
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


# ---------------------------------------------------------------------------
# 第十八轮 M1/A1：not_generated 下根级遗留 conclusions.md 按未受管内容处理
# ---------------------------------------------------------------------------

# round-18 实验四现场内容形状：状态标签未知 → 不可解析（page.invalid_status）。
LEGACY_CONCLUSIONS_UNPARSEABLE = """# 旧笔记

> 状态：`已迁移为正式设计，等待评审`
>
> 父主题：[根索引](index.md)

旧笔记正文。
"""

# 完整可解析的合法内容：可被当作普通页面，但不再是受管页的一部分。
LEGACY_CONCLUSIONS_PARSEABLE = """# 旧笔记

> 状态：`待讨论`
>
> 父主题：[根索引](index.md)

旧笔记正文。
"""

M1_INDEX = """# 主题

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [主题](pages/topic.md)
- [甲](pages/a.md)
- [子枢纽](pages/hub/index.md)
- [乙](pages/b.md)
"""

M1_TOPIC = """# 主题

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

主题的正文。
"""

M1_LEAF = """# {title}

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

{title}的正文。
"""

M1_HUB = """# 子枢纽

> 状态：`已关闭：纯导航页，不表达独立结论`
>
> 父主题：[根索引](../../index.md)
"""


def _legacy_conclusions_root(
    draft_factory: DraftFactory,
    *,
    legacy: str,
    root_state: RootState = RootState.CANDIDATE,
) -> Path:
    root = draft_factory(
        {
            "index.md": M1_INDEX,
            "pages/topic.md": M1_TOPIC,
            "pages/a.md": M1_LEAF.format(title="甲"),
            "pages/b.md": M1_LEAF.format(title="乙"),
            "pages/hub/index.md": M1_HUB,
        },
        root_state=root_state,
    )
    # 夹具的 marker 因不含 conclusions.md 而为 not_generated；随后按旧笔记集合
    # 写入遗留文件（模拟迁移场景）。
    (root / "conclusions.md").write_text(legacy, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "legacy",
    [LEGACY_CONCLUSIONS_UNPARSEABLE, LEGACY_CONCLUSIONS_PARSEABLE],
    ids=["unparseable", "parseable"],
)
def test_candidate_structure_commands_ignore_legacy_conclusions(
    draft_factory: DraftFactory, legacy: str
) -> None:
    """candidate × not_generated 下根级遗留 conclusions.md 不阻断结构命令（round-18 M1/A1）。

    不可解析与可完整解析两种内容断言相同：结构命令行为不依赖遗留文件的偶然可解析性。
    该文件始终按未受管内容处理；check_draft 仍恰好一条 conclusions.unexpected_file，
    容错视图 blocking_paths 不含该文件，遗留文件字节不变。
    """
    root = _legacy_conclusions_root(draft_factory, legacy=legacy)
    legacy_bytes = (root / "conclusions.md").read_bytes()

    move_page(root, Path("pages/a.md"), Path("pages/a2.md"))
    promote_page(root, Path("pages/topic.md"), [Path("pages/a2.md"), Path("pages/b.md")])
    collapse_hub(root, Path("pages/hub/index.md"))

    # move：甲迁至 pages/a2.md；promote：主题成枢纽并把甲、乙收入 pages/topic/；
    # collapse：空枢纽 pages/hub/index.md 折叠为 pages/hub.md。全部成功。
    assert (root / "pages/topic/index.md").is_file()
    assert (root / "pages/topic/a2.md").is_file()
    assert (root / "pages/topic/b.md").is_file()
    assert (root / "pages/hub.md").is_file()
    assert not (root / "pages/a2.md").exists()
    assert not (root / "pages/hub/index.md").exists()
    # 结构命令对该文件字节零触碰（任何选项下工具都不得改写未受管遗留文件）。
    assert (root / "conclusions.md").read_bytes() == legacy_bytes

    view = load_tolerant_view(root)
    assert Path("conclusions.md") not in view.blocking_paths
    assert Path("conclusions.md") not in view.graph.pages

    report = check_draft(root)
    assert [issue.code for issue in report.errors] == [CheckCode.CONCLUSIONS_UNEXPECTED_FILE.value]


M1_MOVE_INDEX = """# 主题

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
"""


def test_registered_move_still_reports_conclusions_unexpected_file(
    draft_factory: DraftFactory,
) -> None:
    """registered × not_generated + 遗留 conclusions.md：写命令仍报
    conclusions.unexpected_file，不回归为 structure.dirty（round-18 确认行）。"""
    root = draft_factory(
        {
            "index.md": M1_MOVE_INDEX,
            "pages/a.md": M1_LEAF.format(title="甲"),
        },
        root_state=RootState.REGISTERED,
    )
    # 夹具的 marker 因不含 conclusions.md 而为 not_generated；随后写入遗留文件。
    (root / "conclusions.md").write_text(LEGACY_CONCLUSIONS_UNPARSEABLE, encoding="utf-8")
    before = (root / "pages/a.md").read_bytes()
    legacy_before = (root / "conclusions.md").read_bytes()

    with pytest.raises(DraftError) as caught:
        move_page(root, Path("pages/a.md"), Path("pages/a2.md"))
    assert caught.value.code == CheckCode.CONCLUSIONS_UNEXPECTED_FILE.value
    # 门禁拒绝发生在写计划之前：被移动页与遗留文件字节都不得被触碰。
    assert (root / "pages/a.md").read_bytes() == before
    assert (root / "conclusions.md").read_bytes() == legacy_before
    assert not (root / "pages/a2.md").exists()


M1_GEN_INDEX = """# 主题

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
- [结论汇总](conclusions.md)
"""

M1_GEN_CONCLUSIONS = """# 结论汇总

> 状态：`当前提案`
>
> 父主题：[根索引](index.md)

## 已确认结论

- [甲](pages/a.md)
"""


@pytest.mark.parametrize("root_state", [RootState.CANDIDATE, RootState.REGISTERED])
def test_generated_conclusions_remain_managed_and_untouched(
    draft_factory: DraftFactory, root_state: RootState
) -> None:
    """generated 所有权下 conclusions.md 仍是受管机器产物（决策表 generated 行）。

    candidate 与 registered 各一：结构命令不重写结论列表中的旧链接（允许过时），
    文件字节不变，check 行为不变。
    """
    root = draft_factory(
        {
            "index.md": M1_GEN_INDEX,
            "conclusions.md": M1_GEN_CONCLUSIONS,
            "pages/a.md": M1_LEAF.format(title="甲"),
        },
        root_state=root_state,
    )
    conclusions_bytes = (root / "conclusions.md").read_bytes()

    move_page(root, Path("pages/a.md"), Path("pages/a2.md"))

    assert (root / "pages/a2.md").is_file()
    # 结论列表旧链接保持原样，机器结论允许过时（规格 §12.5）。
    assert (root / "conclusions.md").read_bytes() == conclusions_bytes
    assert check_draft(root).errors == ()


@pytest.mark.parametrize(
    "legacy",
    [LEGACY_CONCLUSIONS_UNPARSEABLE, LEGACY_CONCLUSIONS_PARSEABLE],
    ids=["unparseable", "parseable"],
)
@pytest.mark.parametrize(
    "operand_op",
    ["move", "promote", "collapse"],
    ids=["move", "promote", "collapse"],
)
def test_candidate_structure_command_legacy_conclusions_source_is_unknown_page(
    draft_factory: DraftFactory, legacy: str, operand_op: str
) -> None:
    """not_generated 下遗留 conclusions.md 作结构命令源操作数一律 structure.unknown_page
    （M1 合同 6 操作数语义）。

    两种遗留内容断言相同：行为不依赖偶然可解析性。拒绝发生在规划阶段的既有操作数
    校验路径，未新增错误码；遗留文件字节不变，且无任何 mutation 残留（全树快照一致）。
    """
    root = _legacy_conclusions_root(draft_factory, legacy=legacy)
    before_bytes = (root / "conclusions.md").read_bytes()
    before_tree = snapshot_tree(root)

    with pytest.raises(DraftError) as caught:
        if operand_op == "move":
            move_page(root, Path("conclusions.md"), Path("pages/renamed.md"))
        elif operand_op == "promote":
            promote_page(root, Path("conclusions.md"), [Path("pages/a.md"), Path("pages/b.md")])
        else:
            collapse_hub(root, Path("conclusions.md"))

    assert caught.value.code == "structure.unknown_page"
    assert (root / "conclusions.md").read_bytes() == before_bytes
    assert snapshot_tree(root) == before_tree
