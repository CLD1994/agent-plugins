import json
from pathlib import Path
from typing import cast

import pytest

import draft.lifecycle as lifecycle_module
from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.lifecycle import adopt, initialize
from tests.integration.support import add_valid_child, build_valid_candidate

FINAL_MARKER_BYTES = b'{"format_version":1,"conclusions_state":"not_generated"}\n'


class SimulatedCrash(BaseException):
    """进程级中断：不被普通 except Exception 吞掉。"""


def _decoded_marker(path: Path, value: bytes) -> dict[str, object]:
    if path.name not in ("draft.json", "draft.provisional.json"):
        return {}
    decoded = json.loads(value)
    return cast("dict[str, object]", decoded) if isinstance(decoded, dict) else {}


def _crash_after_provisional_marker(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = lifecycle_module.durable_write

    def write_then_crash(path: Path, value: bytes) -> None:
        original(path, value)
        decoded = _decoded_marker(path, value)
        if decoded.get("adopt_state") == "provisional":
            raise SimulatedCrash

    monkeypatch.setattr(lifecycle_module, "durable_write", write_then_crash)
    with pytest.raises(SimulatedCrash):
        adopt(root)


def _crash_initialize_after_final_marker(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = lifecycle_module.durable_write

    def write_then_crash(path: Path, value: bytes) -> None:
        original(path, value)
        if value == FINAL_MARKER_BYTES:
            raise SimulatedCrash

    monkeypatch.setattr(lifecycle_module, "durable_write", write_then_crash)
    with pytest.raises(SimulatedCrash):
        initialize(root)


def test_initialize_creates_a_clean_self_contained_draft(tmp_path: Path) -> None:
    root = tmp_path / "new-topic"

    initialize(root)

    assert (root / "index.md").is_file()
    assert not (root / "conclusions.md").exists()  # 结论页改为按需生成
    assert (root / "pages").is_dir()
    assert (root / ".draft/draft.json").read_bytes() == FINAL_MARKER_BYTES
    assert not (root / ".draft/operations").exists()
    assert not (root / ".draft/trash").exists()
    assert check_draft(root).errors == ()


def test_initialize_rejects_nonempty_directory(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    (root / "notes.md").write_text("keep", encoding="utf-8")

    with pytest.raises(DraftError, match="init.not_empty"):
        initialize(root)

    assert (root / "notes.md").read_text(encoding="utf-8") == "keep"


def test_initialize_finalize_crash_is_cleaned_by_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init 在「正式标记已写、临时标记未删」中断时，重跑应清理临时标记并完成登记。"""
    root = tmp_path / "new-topic"
    _crash_initialize_after_final_marker(root, monkeypatch)
    monkeypatch.undo()

    final = root / ".draft/draft.json"
    provisional = root / ".draft/draft.provisional.json"
    assert final.read_bytes() == FINAL_MARKER_BYTES
    assert provisional.is_file()

    # 中断现场：普通命令被临时标记阻断为恢复需求。
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        check_draft(root)

    # 重跑 init 会撞到 init.not_empty；真正解死锁的是 adopt 的已提交待清理路径。
    with pytest.raises(DraftError, match="adopt.already_registered"):
        adopt(root)
    assert not provisional.exists()
    assert final.read_bytes() == FINAL_MARKER_BYTES
    assert check_draft(root).errors == ()


def test_initialize_accepts_empty_directory_and_keeps_it_on_later_use(
    tmp_path: Path,
) -> None:
    root = tmp_path / "empty"
    root.mkdir()

    initialize(root)

    index_text = (root / "index.md").read_text(encoding="utf-8")
    assert "# empty" in index_text
    assert "已关闭：纯导航页，不表达独立结论" in index_text
    assert "- [结论汇总](conclusions.md)" not in index_text
    assert not (root / "conclusions.md").exists()


def test_adopt_registers_a_complete_candidate(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }

    adopt(root)

    assert (root / ".draft/draft.json").read_bytes() == FINAL_MARKER_BYTES
    assert not (root / ".draft/operations").exists()
    assert not (root / ".draft/trash").exists()
    after = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".draft" not in path.parts
    }
    assert after == before
    assert check_draft(root).errors == ()


def test_adopt_rejects_registered_root(tmp_path: Path) -> None:
    root = tmp_path / "registered"
    initialize(root)

    with pytest.raises(DraftError, match="adopt.already_registered"):
        adopt(root)


def test_adopt_rejects_malformed_candidate_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "index.md").write_text("# 主题\n\n> 状态：`进行中`\n", encoding="utf-8")
    before = (root / "index.md").read_bytes()

    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)

    assert (root / "index.md").read_bytes() == before
    assert not (root / ".draft").exists()


def test_adopt_rejects_candidate_with_lock(tmp_path: Path) -> None:
    root = tmp_path / "locked"
    build_valid_candidate(root)
    (root / ".draft").mkdir()
    (root / ".draft/operation.lock").write_text(
        '{"operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n', encoding="utf-8"
    )

    with pytest.raises(DraftError, match="adopt.locked"):
        adopt(root)

    assert not (root / ".draft/draft.json").exists()


def test_adopt_revalidates_content_before_marker_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    original_hash = lifecycle_module.sha256_file
    calls = 0

    def poisoned_hash(path: Path) -> str | None:
        nonlocal calls
        if path.name == "index.md":
            calls += 1
            if calls == 2:
                path.write_text(
                    path.read_text(encoding="utf-8") + "\n外部并发编辑。\n",
                    encoding="utf-8",
                )
        return original_hash(path)

    monkeypatch.setattr(lifecycle_module, "sha256_file", poisoned_hash)

    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)

    assert not (root / ".draft/draft.json").exists()
    assert not (root / ".draft").exists()
    assert "外部并发编辑" in (root / "index.md").read_text(encoding="utf-8")


def test_adopt_removes_marker_when_recheck_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    original = lifecycle_module.durable_write

    def poisoned_write(path: Path, value: bytes) -> None:
        index = root / "index.md"
        index.write_text(
            index.read_text(encoding="utf-8") + "- [不存在](pages/missing.md)\n",
            encoding="utf-8",
        )
        original(path, value)

    monkeypatch.setattr(lifecycle_module, "durable_write", poisoned_write)

    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)

    assert not (root / ".draft/draft.json").exists()
    assert not (root / ".draft").exists()
    assert "- [不存在](pages/missing.md)" in (root / "index.md").read_text(encoding="utf-8")


def test_adopt_rejects_valid_manifest_change_after_marker_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    original = lifecycle_module.durable_write

    injected = False

    def write_then_add_valid_page(path: Path, value: bytes) -> None:
        nonlocal injected
        original(path, value)
        decoded = _decoded_marker(path, value)
        if decoded.get("adopt_state") == "provisional" and not injected:
            injected = True
            add_valid_child(root, Path("pages/new.md"))

    monkeypatch.setattr(lifecycle_module, "durable_write", write_then_add_valid_page)

    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)

    assert (root / "pages/new.md").is_file()
    assert not (root / ".draft/draft.json").exists()
    assert not (root / ".draft").exists()


def test_adopt_rollback_leaves_no_final_and_retry_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """回滚只清理临时文件（不触碰正式标记路径），失败方随后重跑 adopt 能成功登记。"""
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    original = lifecycle_module.durable_write

    injected = False

    def write_then_add_valid_page(path: Path, value: bytes) -> None:
        nonlocal injected
        original(path, value)
        decoded = _decoded_marker(path, value)
        if decoded.get("adopt_state") == "provisional" and not injected:
            injected = True
            add_valid_child(root, Path("pages/new.md"))

    monkeypatch.setattr(lifecycle_module, "durable_write", write_then_add_valid_page)
    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)
    monkeypatch.undo()

    # 回滚只清理独立临时文件；正式标记路径未写，目录仍是候选；并发页面保留。
    assert not (root / ".draft/draft.json").exists()
    assert not (root / ".draft/draft.provisional.json").exists()
    assert (root / "pages/new.md").is_file()

    # 失败方重跑 adopt：候选清单包含并发页面，能成功登记。
    adopt(root)
    final_marker = root / ".draft/draft.json"
    assert final_marker.read_bytes() == FINAL_MARKER_BYTES
    assert not (root / ".draft/draft.provisional.json").exists()
    assert check_draft(root).errors == ()


def test_adopt_cleans_provisional_when_final_already_written(tmp_path: Path) -> None:
    """finalize 两步间中断（final 已写、临时未删）时，再次 adopt 应耐久清理临时标记。"""
    from draft.lifecycle import draft_manifest, provisional_marker_bytes

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    # 先算候选清单（此时还没有任何内部状态）。
    before = draft_manifest(root, mode="candidate")
    internal = root / ".draft"
    internal.mkdir()
    # 构造 finalize 中断现场：有效正式标记 + 残留独立临时标记。
    final = internal / "draft.json"
    final.write_bytes(FINAL_MARKER_BYTES)
    provisional = internal / "draft.provisional.json"
    provisional.write_bytes(provisional_marker_bytes(before))

    # 普通命令被临时标记阻断为恢复需求。
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        check_draft(root)

    # 再跑 adopt：应把"有效 final + 临时"解释为已提交待清理，耐久删除临时标记。
    with pytest.raises(DraftError, match="adopt.already_registered"):
        adopt(root)
    assert not provisional.exists()
    assert final.read_bytes() == FINAL_MARKER_BYTES
    # 清理后普通命令恢复可用。
    assert check_draft(root).errors == ()


def test_invalid_provisional_marker_is_reported_not_ignored(tmp_path: Path) -> None:
    """正式标记有效 + 临时标记存在但无效：registered 检查必须报错，不得当作不存在放行。"""
    root = tmp_path / "nb"
    initialize(root)
    provisional = root / ".draft/draft.provisional.json"

    # 损坏 JSON。
    provisional.write_text("corrupt\n", encoding="utf-8")
    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        check_draft(root)

    # 非临时格式（正式形状的标记出现在临时路径）。
    provisional.write_bytes(FINAL_MARKER_BYTES)
    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        check_draft(root)

    # adopt 的已提交待清理路径仍能耐久清掉残留临时标记。
    with pytest.raises(DraftError, match="adopt.already_registered"):
        adopt(root)
    assert not provisional.exists()
    assert check_draft(root).errors == ()


def test_provisional_marker_path_directory_is_rejected(tmp_path: Path) -> None:
    """临时标记路径为目录（已存在但非普通文件）时，普通检查与恢复都必须报稳定错误。"""
    from draft.trash_lifecycle import recover_draft

    root = tmp_path / "nb"
    initialize(root)
    (root / ".draft/draft.provisional.json").mkdir()

    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        check_draft(root)
    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        recover_draft(root)


def test_symlinked_provisional_marker_is_rejected(tmp_path: Path) -> None:
    """临时标记路径为符号链接（含断链目标）时必须报 path.symlink_escape。"""
    root = tmp_path / "nb"
    initialize(root)
    external = tmp_path / "external.json"
    external.write_bytes(FINAL_MARKER_BYTES)
    provisional = root / ".draft/draft.provisional.json"
    provisional.symlink_to(external)

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        check_draft(root)
    # 外部目标内容不被读取改动。
    assert external.read_bytes() == FINAL_MARKER_BYTES


def test_provisional_crash_blocks_ordinary_operations_and_adopt_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from draft.structure import collapse_hub, move_page, promote_page
    from draft.trash import trash_page
    from draft.trash_lifecycle import recover_draft

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    _crash_after_provisional_marker(root, monkeypatch)
    monkeypatch.undo()

    # 临时标记现在位于独立文件 draft.provisional.json。
    marker = root / ".draft/draft.provisional.json"
    assert marker.is_file()

    with pytest.raises(DraftError, match="adopt.recovery_required"):
        check_draft(root)
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        check_draft(root, mode="candidate")
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        recover_draft(root)
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        move_page(root, Path("conclusions.md"), Path("pages/moved.md"))
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        promote_page(root, Path("conclusions.md"), [])
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        collapse_hub(root, Path("conclusions.md"))
    with pytest.raises(DraftError, match="adopt.recovery_required"):
        trash_page(root, Path("conclusions.md"))

    adopt(root)

    # 续跑完成后：正式标记写入 draft.json，独立临时文件被清理。
    final_marker = root / ".draft/draft.json"
    assert final_marker.read_bytes() == FINAL_MARKER_BYTES
    assert not marker.exists()
    assert check_draft(root).errors == ()


def test_rollback_keeps_final_marker_but_deletes_provisional(tmp_path: Path) -> None:
    """并发 adopt 竞态：回滚时若标记已被他方发布为 final，绝不能删除；临时标记仍正常删除。"""
    from draft.lifecycle import (
        FINAL_MARKER_BYTES as FINAL,
    )
    from draft.lifecycle import (
        draft_manifest,
        provisional_marker_bytes,
        rollback_provisional_marker_durably,
    )

    # final 标记（模拟并发 adopt 发布成功）：回滚必须保留（不属于本次临时标记）。
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    before1 = draft_manifest(root, mode="candidate")
    internal = root / ".draft"
    internal.mkdir()
    marker = internal / "draft.json"
    marker.write_bytes(FINAL)
    rollback_provisional_marker_durably(root, provisional_marker_bytes(before1))
    assert marker.is_file()
    assert marker.read_bytes() == FINAL

    # 临时标记（本 adopt 自己的，位于独立文件）：回滚仍正常删除。
    root2 = tmp_path / "candidate2"
    build_valid_candidate(root2)
    before = draft_manifest(root2, mode="candidate")
    internal2 = root2 / ".draft"
    internal2.mkdir()
    marker2 = internal2 / "draft.provisional.json"
    marker2.write_bytes(provisional_marker_bytes(before))
    rollback_provisional_marker_durably(root2, provisional_marker_bytes(before))
    assert not marker2.exists()


def test_provisional_crash_then_changed_page_rolls_back_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    _crash_after_provisional_marker(root, monkeypatch)
    monkeypatch.undo()

    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + "\n并发补充。\n",
        encoding="utf-8",
    )

    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)

    assert not (root / ".draft/draft.json").exists()
    assert not (root / ".draft").exists()
    assert "并发补充" in index.read_text(encoding="utf-8")
