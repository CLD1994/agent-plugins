import json
from pathlib import Path

import pytest

import draft.lifecycle as lifecycle_module
from draft.checker import check_draft
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.lifecycle import adopt, initialize
from draft.models import OperationPhase, RootState
from tests.integration.support import build_valid_candidate, managed_marker_text, snapshot_tree

FINAL_MARKER_BYTES = managed_marker_text().encode("utf-8")


class SimulatedCrash(BaseException):
    """进程级中断：不被普通 except Exception 吞掉。"""


def _crash_initialize_after_final_marker(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = lifecycle_module.durable_write

    def write_then_crash(root: Path, relative: Path, value: bytes) -> None:
        original(root, relative, value)
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


def test_initialize_rejects_file_target(tmp_path: Path) -> None:
    from draft.diagnostic_codes import RootCode

    root = tmp_path / "occupied"
    root.write_text("file", encoding="utf-8")

    with pytest.raises(DraftError, match=RootCode.INVALID.value):
        initialize(root)


def test_initialize_check_raise_removes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终检查直接抛异常（而非返回错误报告）时 marker 也必须回滚（规格 §6）。"""

    def raising_check(root: Path) -> None:
        del root
        raise OSError("check failed")

    monkeypatch.setattr(lifecycle_module, "check_draft", raising_check)

    root = tmp_path / "new-topic"
    with pytest.raises(OSError, match="check failed"):
        initialize(root)

    assert not (root / ".draft/draft.json").exists()
    assert not (root / "index.md").exists()
    assert not (root / "pages").exists()
    assert not root.exists()


def test_initialize_marker_publish_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """marker 耐久发布失败时，回滚协议不得留下看似成功的半初始化根。"""
    root = tmp_path / "new-topic"
    original = lifecycle_module.durable_write

    def fail_on_marker(root: Path, relative: Path, value: bytes) -> None:
        if relative.name == "draft.json":
            raise OSError("disk full")
        original(root, relative, value)

    monkeypatch.setattr(lifecycle_module, "durable_write", fail_on_marker)

    with pytest.raises(OSError, match="disk full"):
        initialize(root)

    assert not root.exists()


def test_initialize_check_failure_removes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最终检查失败（确定性失败）时 marker 必须回滚，不能留下半初始化根（规格 §6）。"""
    from draft.models import CheckIssue, CheckReport, IssueSeverity

    def failing_check(root: Path) -> CheckReport:
        del root
        return CheckReport(
            issues=(CheckIssue("test.injected", IssueSeverity.ERROR, Path("index.md"), None),)
        )

    monkeypatch.setattr(lifecycle_module, "check_draft", failing_check)

    root = tmp_path / "new-topic"
    with pytest.raises(DraftError, match="init.invalid_template"):
        initialize(root)

    assert not (root / ".draft/draft.json").exists()
    assert not (root / "index.md").exists()
    assert not (root / "pages").exists()
    assert not root.exists()


def test_initialize_finalize_crash_leaves_complete_registered_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """init 在「正式标记已写」后中断时，marker 是最后一项变更，根已是完整登记态。

    重跑 init 报告非空；不再存在临时标记清理路径（initialize 不经 provisional）。
    """
    root = tmp_path / "new-topic"
    _crash_initialize_after_final_marker(root, monkeypatch)
    monkeypatch.undo()

    final = root / ".draft/draft.json"
    assert final.read_bytes() == FINAL_MARKER_BYTES
    assert not (root / ".draft/draft.provisional.json").exists()

    # 中断现场：marker 已耐久发布 = 登记完成，普通命令可用且无恢复需求。
    assert check_draft(root).errors == ()

    # 重跑 init 撞到 init.not_empty。
    with pytest.raises(DraftError, match="init.not_empty"):
        initialize(root)


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


def test_adopt_succeeds_on_fresh_init_candidate(tmp_path: Path) -> None:
    """真实 init --candidate 产物（无 .draft/operations、.draft/trash）上 adopt 必须成功。

    集成夹具 build_valid_candidate 预创建 operations/trash 目录，掩盖了
    首次创建 .draft/operations 改变 .draft 成员这一可解释变化。
    """
    from draft.lifecycle import adopt, initialize

    root = tmp_path / "candidate"
    initialize(root, root_state=RootState.CANDIDATE)

    adopt(root)

    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.REGISTERED
    )
    assert not (root / ".draft/operation.lock").exists()


def test_adopt_registers_a_complete_candidate(tmp_path: Path) -> None:
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".draft" not in path.parts
    }

    adopt(root)

    assert (root / ".draft/draft.json").read_bytes() == FINAL_MARKER_BYTES
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


def test_adopt_rejects_unmanaged_root(tmp_path: Path) -> None:
    """未受管目录（无 marker）不是候选：marker ENOENT 报 format.missing（round-15 H1）。

    adopt 不得把缺失改写为 adopt.invalid_candidate；不写任何内部状态。
    """
    root = tmp_path / "broken"
    root.mkdir()
    (root / "pages").mkdir()
    (root / "index.md").write_text("# 主题\n\n> 状态：`进行中`\n", encoding="utf-8")
    before = (root / "index.md").read_bytes()

    from draft.diagnostic_codes import CheckCode

    with pytest.raises(DraftError, match=CheckCode.FORMAT_MISSING.value):
        adopt(root)

    assert (root / "index.md").read_bytes() == before
    assert not (root / ".draft").exists()


def test_adopt_marker_identity_read_eio_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adopt marker 身份读取 EIO：format.invalid @ marker，无锁、无 operation（round-15 H1）。

    修复前 final_marker.is_file()/read_bytes() 会原样泄漏 OSError；snapshot 读取器把
    EACCES/EIO 稳定映射 format.invalid，且不写任何内部状态。
    """
    import os

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    marker = root / ".draft/draft.json"
    before = snapshot_tree(root)
    real_lstat = os.lstat

    def fail_marker_lstat(path: str | bytes | os.PathLike[str], *args: object) -> object:
        if os.fspath(path) == os.fspath(marker):
            raise OSError(5, "Input/output error")
        return real_lstat(path, *args)

    monkeypatch.setattr(os, "lstat", fail_marker_lstat)

    from draft.diagnostic_codes import CheckCode

    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        adopt(root)
    monkeypatch.undo()

    assert snapshot_tree(root) == before
    assert not (root / ".draft/operation.lock").exists()
    assert not (root / ".draft/operations").exists()


def test_adopt_marker_bytes_read_eio_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adopt marker bytes 读取 EIO：format.invalid @ marker，无锁、无 operation（round-15 H1）。

    修复前 final_marker.read_bytes() 原样泄漏 OSError(5)；修复后普通 I/O 失败稳定失败。
    """
    root = tmp_path / "candidate"
    build_valid_candidate(root)
    marker = root / ".draft/draft.json"
    before = snapshot_tree(root)
    real_read_bytes = Path.read_bytes

    def fail_marker_read(self: Path) -> bytes:
        if self == marker:
            raise OSError(5, "Input/output error")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fail_marker_read)

    from draft.diagnostic_codes import CheckCode

    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        adopt(root)
    monkeypatch.undo()

    assert snapshot_tree(root) == before
    assert not (root / ".draft/operation.lock").exists()
    assert not (root / ".draft/operations").exists()


def test_adopt_rejects_non_regular_marker_without_writes(
    tmp_path: Path,
) -> None:
    """marker 是目录：adopt 报 format.invalid，无锁、无 operation（round-15 H1）。"""
    from draft.diagnostic_codes import CheckCode

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    marker = root / ".draft/draft.json"
    marker.unlink()
    marker.mkdir()
    before = snapshot_tree(root)

    with pytest.raises(DraftError, match=CheckCode.FORMAT_INVALID.value):
        adopt(root)

    assert snapshot_tree(root) == before
    assert not (root / ".draft/operation.lock").exists()
    assert not (root / ".draft/operations").exists()


def test_adopt_rejects_marker_symlink_without_writes(
    tmp_path: Path,
) -> None:
    """marker 是 symlink：adopt 报 path.symlink_escape，无锁、无 operation（round-15 H1）。"""
    from draft.diagnostic_codes import CheckCode

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    marker = root / ".draft/draft.json"
    marker.rename(marker.with_suffix(".json.real"))
    marker.symlink_to(marker.with_suffix(".json.real"))
    before = snapshot_tree(root)

    with pytest.raises(DraftError, match=CheckCode.PATH_SYMLINK_ESCAPE.value):
        adopt(root)

    assert snapshot_tree(root) == before
    assert not (root / ".draft/operation.lock").exists()
    assert not (root / ".draft/operations").exists()


def test_adopt_rejects_unknown_marker_version_without_writes(
    tmp_path: Path,
) -> None:
    """marker 版本未知整数：adopt 报 format.unsupported，无锁、无 operation（round-15 H1）。"""
    import json

    from draft.diagnostic_codes import CheckCode

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    marker = root / ".draft/draft.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["format_version"] = 2
    marker.write_text(json.dumps(data), encoding="utf-8")
    before = snapshot_tree(root)

    with pytest.raises(DraftError, match=CheckCode.FORMAT_UNSUPPORTED.value):
        adopt(root)

    assert snapshot_tree(root) == before
    assert not (root / ".draft/operation.lock").exists()
    assert not (root / ".draft/operations").exists()


def test_adopt_snapshot_switch_candidate_to_registered_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adopt snapshot 后 live marker 切成 registered：锁内观察拒绝（round-15 H1）。

    通过真实 candidate 根 + 受接口约束的 draft.graph.read_marker_snapshot 替身注入
    snapshot 后切换；adopt 不得形成 before==after journal，marker 保持外部切换后的
    registered，本次准备日志按既有冲突协议终态化且锁释放。
    """
    import draft.graph as graph_module
    from draft.graph import MARKER_PATH, MarkerSnapshot

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    registered_bytes = managed_marker_text(root_state=RootState.REGISTERED).encode("utf-8")

    real_snapshot = graph_module.read_marker_snapshot
    fired = False

    def snapshot_then_switch(path: Path) -> MarkerSnapshot:
        nonlocal fired
        snapshot = real_snapshot(path)
        if not fired:
            fired = True
            (path / MARKER_PATH).write_bytes(registered_bytes)
        return snapshot

    monkeypatch.setattr(graph_module, "read_marker_snapshot", snapshot_then_switch)

    with pytest.raises(DraftError, match="operation.external_change"):
        adopt(root)

    assert (root / ".draft/draft.json").read_bytes() == registered_bytes
    assert not (root / ".draft/operation.lock").exists()
    # 本次 adopt 准备日志按既有冲突协议安全终态化（rolled_back），无未终态活动操作。
    ops = root / ".draft/operations"
    if ops.exists():
        for journal_dir in ops.iterdir():
            raw = json.loads((journal_dir / "journal.json").read_text(encoding="utf-8"))
            assert raw["phase"] in ("rolled_back", "completed")


def test_adopt_rejects_malformed_candidate_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    build_valid_candidate(root)
    (root / "index.md").write_text("# 主题\n", encoding="utf-8")
    before = (root / "index.md").read_bytes()

    with pytest.raises(DraftError, match="adopt.invalid_candidate"):
        adopt(root)

    # 失败不改写 marker：根仍为候选，可修复后重试。
    assert (root / "index.md").read_bytes() == before
    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.CANDIDATE
    )


def test_adopt_rejects_candidate_with_lock(tmp_path: Path) -> None:
    root = tmp_path / "locked"
    build_valid_candidate(root)
    (root / ".draft/operation.lock").write_text(
        '{"operation_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}\n', encoding="utf-8"
    )

    with pytest.raises(DraftError, match="adopt.locked"):
        adopt(root)

    assert (root / ".draft/draft.json").is_file()


def test_adopt_rejects_concurrent_page_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """锁内观察复核发现页面并发变化时不应用 marker 变更，根保持候选。"""
    import draft.operations as ops_module

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    from draft.operations import Journal

    original_verify = ops_module._verify_plan_observations  # pyright: ignore[reportPrivateUsage]

    def verify_then_edit(root_path: Path, journal: Journal) -> None:
        index = root / "index.md"
        index.write_text(index.read_text(encoding="utf-8") + "\n并发补充。\n", encoding="utf-8")
        original_verify(root_path, journal)

    monkeypatch.setattr(ops_module, "_verify_plan_observations", verify_then_edit)

    with pytest.raises(DraftError, match="operation.external_change"):
        adopt(root)

    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.CANDIDATE
    )
    assert "并发补充" in (root / "index.md").read_text(encoding="utf-8")


def test_adopt_observations_cover_pre_existing_internal_protocol_files(
    tmp_path: Path,
) -> None:
    """adopt 观察清单必须纳入既有操作日志与权威删除包（规格 §12.1）。"""
    from draft.graph import load_graph_tolerant, read_marker_snapshot
    from draft.observations import graph_observations, internal_protocol_observations

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    # 既有终态操作日志与权威删除包。
    journal_dir = root / ".draft/operations" / ("b" * 32)
    journal_dir.mkdir(parents=True)
    (journal_dir / "journal.json").write_text("{}", encoding="utf-8")
    package = root / ".draft/trash/outgoing" / ("c" * 32) / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text('{"version":1}\n', encoding="utf-8")

    graph = load_graph_tolerant(root).graph
    manifest = internal_protocol_observations(graph)
    merged = graph_observations(graph, marker=read_marker_snapshot(root)).entries + manifest.entries

    assert Path(".draft/operations") / ("b" * 32) / "journal.json" in {
        entry.path for entry in merged
    }
    assert Path(".draft/trash/outgoing") / ("c" * 32) / "package.json" in {
        entry.path for entry in merged
    }
    # marker 由 graph_observations 的 snapshot 恰好提供一项，internal_protocol_observations
    # 已跳过 MARKER_PATH（round-15 H1）：合并后不得出现重复 marker 观察。
    marker_entries = [entry for entry in merged if entry.path == Path(".draft/draft.json")]
    assert len(marker_entries) == 1


def test_adopt_rejects_concurrent_internal_protocol_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adopt 锁内复核发现内部协议文件并发变化时不应用 marker 变更（规格 §12.1）。"""
    from datetime import UTC, datetime

    import draft.operations as ops_module
    from draft.trash import trash_page
    from tests.integration.support import add_valid_child

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    # 建立合法持有中的权威删除包：trash 一个候选页（orphan 包会被 check 拒绝）。
    add_valid_child(root, Path("pages/obsolete.md"))
    pending = trash_page(root, Path("pages/obsolete.md"), now=datetime(2026, 7, 21, 12, tzinfo=UTC))
    package = root / ".draft/trash/outgoing" / pending.operation_id / "package.json"
    assert package.is_file()

    from draft.operations import Journal

    original_verify = ops_module._verify_plan_observations  # pyright: ignore[reportPrivateUsage]

    def verify_then_edit(root_path: Path, journal: Journal) -> None:
        package.write_text(
            package.read_text(encoding="utf-8") + "\n// 外部修改\n", encoding="utf-8"
        )
        original_verify(root_path, journal)

    monkeypatch.setattr(ops_module, "_verify_plan_observations", verify_then_edit)

    with pytest.raises(DraftError, match="operation.external_change"):
        adopt(root)

    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.CANDIDATE
    )


def _crash_during_adopt(root: Path, monkeypatch: pytest.MonkeyPatch, *, on_verify: bool) -> None:
    """在 adopt 执行期注入进程级中断：before marker 应用时或 marker 应用后。"""
    import draft.operations as ops_module

    if on_verify:

        def crash_before_apply(path: Path, target: Path, content: bytes, **kwargs: object) -> None:
            raise SimulatedCrash

        monkeypatch.setattr(ops_module, "_apply_write", crash_before_apply)
    else:

        def crash_before_finalize(*args: object, **kwargs: object) -> object:
            raise SimulatedCrash

        monkeypatch.setattr(ops_module, "_validate_and_complete", crash_before_finalize)
    with pytest.raises(SimulatedCrash):
        adopt(root)
    monkeypatch.undo()


def test_adopt_crash_before_marker_is_forwarded_by_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """marker 仍是 before 时崩溃：recover 复核观察未变后前向完成登记。"""
    from draft.trash_lifecycle import recover_interrupted_operations

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    _crash_during_adopt(root, monkeypatch, on_verify=True)

    # 锁、日志与 candidate marker 仍保留，普通命令要求恢复。
    from draft.operations import read_lock

    assert read_lock(root) is not None
    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.CANDIDATE
    )

    recover_interrupted_operations(root)

    assert (root / ".draft/draft.json").read_bytes() == FINAL_MARKER_BYTES
    assert check_draft(root).errors == ()


def test_adopt_recover_with_observation_change_raises_external_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adopt 在 marker 写入前崩溃、恢复时观察已变：安全回滚后必须抛 external_change，
    不能把"已放弃 adopt"报告为恢复成功（规格 §12.2 外部变化分区）。"""
    from draft.operations import operation_journal_path, read_journal, read_lock
    from draft.trash_lifecycle import recover_interrupted_operations

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    _crash_during_adopt(root, monkeypatch, on_verify=True)
    adopt_id = read_lock(root)
    assert adopt_id is not None
    # 外部编辑一个被观察页面。
    index = root / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "\n并发修改。\n", encoding="utf-8")

    with pytest.raises(DraftError, match="operation.external_change"):
        recover_interrupted_operations(root)

    # 已安全放弃：marker 仍 candidate、外部修改保留、日志终态化、锁释放。
    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.CANDIDATE
    )
    assert "并发修改" in (root / "index.md").read_text(encoding="utf-8")
    journal = read_journal(operation_journal_path(root, adopt_id))
    assert journal.phase is OperationPhase.ROLLED_BACK
    assert read_lock(root) is None


def test_adopt_recover_rejects_intruder_in_created_operations_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adopt 首次创建 .draft/operations 后，目录内混入未指定条目时恢复必须失败关闭。

    回归 P1：计划期 .draft/operations 不存在、没有观察基线；若恢复不验证该新
    目录内部，外部文件会被静默吸收并前向完成登记（规格 §12.2 外部变化分区）。
    """
    from draft.operations import operation_journal_path, read_journal, read_lock
    from draft.trash_lifecycle import recover_interrupted_operations

    root = tmp_path / "candidate"
    # 默认最小根：.draft/operations 由本次 adopt 首次创建。
    build_valid_candidate(root)
    _crash_during_adopt(root, monkeypatch, on_verify=True)
    adopt_id = read_lock(root)
    assert adopt_id is not None
    assert (root / ".draft/operations").is_dir()

    # 外部向本操作新建的内部协议目录注入未指定文件。
    (root / ".draft/operations/intruder.json").write_text("x", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        recover_interrupted_operations(root)

    # 分类失败关闭：外部文件、marker、journal 和锁全部保留。
    assert (root / ".draft/operations/intruder.json").is_file()
    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text(
        root_state=RootState.CANDIDATE
    )
    journal = read_journal(operation_journal_path(root, adopt_id))
    assert journal.phase is OperationPhase.APPLYING
    assert read_lock(root) == adopt_id


def test_adopt_crash_after_marker_never_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """marker 已是 after 时崩溃：recover 只完成提交与清理，绝不回滚根身份。"""
    from draft.trash_lifecycle import recover_interrupted_operations

    root = tmp_path / "candidate"
    build_valid_candidate(root)
    _crash_during_adopt(root, monkeypatch, on_verify=False)

    # marker 已登记，但日志提交未完成（锁仍在）。
    from draft.operations import read_lock

    assert read_lock(root) is not None
    assert (root / ".draft/draft.json").read_bytes() == FINAL_MARKER_BYTES

    recover_interrupted_operations(root)

    assert (root / ".draft/draft.json").read_bytes() == FINAL_MARKER_BYTES
    assert check_draft(root).errors == ()
