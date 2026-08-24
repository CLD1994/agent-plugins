"""Task 4 集成测试：staging、索引目标原子发布与 rebuild flag 的耐久存储（spec §10）。

全部使用真实 tmp 文件系统；耐久步骤失败通过受真实 I/O 接口约束的 monkeypatch 注入，
每个步骤单独成用例，并同时断言目标/staging/flag 现场与稳定错误码。
"""

from __future__ import annotations

import dataclasses
import errno
import json
import os
from pathlib import Path

import pytest

from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.link_index import (
    INDEX_RELATIVE_PATH,
    LinkIndex,
    LinkIndexContent,
    materialize_index,
)
from draft.link_index_maintenance import (
    LINK_INDEX_STAGED_FILE_NAME,
    REBUILD_FLAG_RELATIVE_PATH,
    cleanup_link_index_staging,
    commit_link_index_update,
    ensure_rebuild_flag,
    maintain_link_index,
    prepare_link_index_update,
    publish_staged_link_index,
    read_link_index_authority,
    remove_rebuild_flag,
    stage_link_index,
)
from draft.models import (
    LinkIndexResult,
    OperationKind,
    OperationPhase,
    OperationResult,
    ResultWarning,
    RootState,
)
from draft.observations import EMPTY_OBSERVATIONS
from draft.operations import JOURNAL_VERSION, Journal, read_lock, sha256_bytes

_STAGED_NAME = LINK_INDEX_STAGED_FILE_NAME


def _init_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    internal = root / ".draft"
    internal.mkdir()
    (internal / "operations").mkdir()
    return root


def _index(revision: str) -> LinkIndex:
    return materialize_index(
        revision, LinkIndexContent(files={}, backlinks={}, directories={})
    )


def _journal(directory: Path, *, revision: str) -> Journal:
    return Journal(
        operation_id=directory.name,
        kind=OperationKind.LINK_INDEX_UPDATE,
        phase=OperationPhase.LINK_INDEX_UPDATING,
        root_state=RootState.REGISTERED,
        observations=EMPTY_OBSERVATIONS,
        mutations=(),
        remove_empty_directories=(),
        metadata={},
        directory=directory,
        link_index_revision=revision,
        link_index_result=None,
        link_index_sha256=None,
    )


def _persist_journal(journal: Journal) -> None:
    journal.directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": journal.operation_id,
        "kind": journal.kind.value,
        "phase": journal.phase.value,
        "root_state": journal.root_state.value,
        "observations": [],
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    if journal.link_index_revision is not None:
        payload["link_index_revision"] = journal.link_index_revision
    if journal.link_index_result is not None:
        payload["link_index_result"] = journal.link_index_result.value
    if journal.link_index_sha256 is not None:
        payload["link_index_sha256"] = journal.link_index_sha256
    (journal.directory / "journal.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_result_journal(
    root: Path,
    *,
    revision: str,
    result: LinkIndexResult,
    sha256: str | None,
    operation_id: str = "b" * 32,
) -> None:
    directory = root / ".draft" / "operations" / operation_id
    directory.mkdir(parents=True, exist_ok=True)
    phase = (
        OperationPhase.LINK_INDEX_PUBLISHED
        if result is LinkIndexResult.PUBLISHED
        else OperationPhase.LINK_INDEX_REBUILD_REQUIRED
    )
    payload: dict[str, object] = {
        "journal_version": JOURNAL_VERSION,
        "operation_id": operation_id,
        "kind": OperationKind.LINK_INDEX_UPDATE.value,
        "phase": phase.value,
        "root_state": "registered",
        "observations": [],
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
        "link_index_revision": revision,
        "link_index_result": result.value,
    }
    if sha256 is not None:
        payload["link_index_sha256"] = sha256
    (directory / "journal.json").write_text(json.dumps(payload), encoding="utf-8")


def _setup_published_artifact(root: Path, revision: str) -> bytes:
    raw = _index(revision).to_json()
    (root / INDEX_RELATIVE_PATH).write_bytes(raw)
    _write_result_journal(
        root, revision=revision, result=LinkIndexResult.PUBLISHED, sha256=sha256_bytes(raw)
    )
    return raw


def _fail_fsync_at(monkeypatch: pytest.MonkeyPatch, fail_at: frozenset[int]) -> None:
    real_fsync = os.fsync
    calls = {"n": 0}

    def flaky_fsync(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] in fail_at:
            raise OSError(f"injected fsync failure #{calls['n']}")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)


# ---------------------------------------------------------------------------
# staging（spec §10）
# ---------------------------------------------------------------------------


def test_stage_link_index_writes_staging_in_operation_directory(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    index = _index("1")

    digest = stage_link_index(journal, index)

    staging = directory / _STAGED_NAME
    assert staging.is_file()
    assert staging.read_bytes() == index.to_json()
    assert digest == sha256_bytes(index.to_json())
    # operation 目录没有随机索引形似文件。
    assert {entry.name for entry in directory.iterdir()} == {"journal.json", _STAGED_NAME}


def test_stage_link_index_is_idempotent_for_same_content(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    index = _index("1")

    first = stage_link_index(journal, index)
    second = stage_link_index(journal, index)

    assert first == second
    assert (directory / _STAGED_NAME).read_bytes() == index.to_json()


def test_stage_link_index_rejects_existing_staging_with_wrong_digest(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    (directory / _STAGED_NAME).write_bytes(b"wrong content")

    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_stage_link_index_rejects_symlink_staging(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    (directory / _STAGED_NAME).symlink_to(root / ".draft")

    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_stage_link_index_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    real_open = os.open

    def fail_staging_write(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if flags & os.O_CREAT and "link-index.staged.json" in str(path):
            raise OSError("injected write failure")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", fail_staging_write)
    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert not (directory / _STAGED_NAME).exists()


def test_stage_link_index_file_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    _fail_fsync_at(monkeypatch, frozenset({1}))

    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert not (directory / _STAGED_NAME).exists()
    assert (directory / f"{_STAGED_NAME}.pending").is_file()


def test_stage_link_index_operation_directory_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    _fail_fsync_at(monkeypatch, frozenset({2}))

    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert not (directory / _STAGED_NAME).exists()
    assert (directory / f"{_STAGED_NAME}.pending").is_file()


def test_stage_link_index_external_change_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """durable_write 的 external_change（pending/target 瞬时出现）必须原样传播。"""
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    real_open = os.open

    def file_exists(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if flags & os.O_CREAT and "link-index.staged.json" in str(path):
            raise FileExistsError("already exists")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", file_exists)
    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    monkeypatch.undo()
    assert caught.value.code == "operation.external_change"


def test_stage_link_index_rejects_symlink_ancestor(tmp_path: Path) -> None:
    """staging 祖先（``.draft/operations``）为符号链接时报不一致，与 publish 边界一致。"""
    root = _init_root(tmp_path)
    real_ops = root / ".draft" / "real-operations"
    real_ops.mkdir()
    target = real_ops / ("a" * 32)
    target.mkdir()
    journal = _journal(target, revision="1")
    _persist_journal(journal)
    # 把 .draft/operations 换成指向真实 operations 的符号链接。
    (root / ".draft" / "operations").rmdir()
    (root / ".draft" / "operations").symlink_to(real_ops, target_is_directory=True)
    journal = dataclasses.replace(
        journal, directory=root / ".draft" / "operations" / ("a" * 32)
    )

    with pytest.raises(DraftError) as caught:
        stage_link_index(journal, _index("1"))
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


# ---------------------------------------------------------------------------
# 发布目标分类与原子替换（spec §10 决策表）
# ---------------------------------------------------------------------------


def test_publish_first_index_allowed_when_target_missing(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    index = _index("1")
    digest = stage_link_index(journal, index)

    publish_staged_link_index(root, journal, digest)

    target = root / INDEX_RELATIVE_PATH
    assert target.is_file()
    assert target.read_bytes() == index.to_json()
    # staging 被原子替换消费。
    assert not (directory / _STAGED_NAME).exists()
    # .draft/ 没有随机 .link-index.* 文件。
    assert not any(
        entry.name.startswith(".link-index.") for entry in (root / ".draft").iterdir()
    )


def test_publish_first_index_rejects_existing_regular_target(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))
    (root / INDEX_RELATIVE_PATH).write_bytes(b"existing index")

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    # 现场保留：既有目标未覆盖，staging 未消费。
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == b"existing index"
    assert (directory / _STAGED_NAME).is_file()


def test_publish_after_rebuild_required_allows_missing_or_regular_target(
    tmp_path: Path,
) -> None:
    root = _init_root(tmp_path)
    _write_result_journal(root, revision="2", result=LinkIndexResult.REBUILD_REQUIRED, sha256=None)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="3")
    _persist_journal(journal)
    index = _index("3")
    digest = stage_link_index(journal, index)

    # 目标缺失：允许发布。
    publish_staged_link_index(root, journal, digest)
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == index.to_json()

    # 再次：目标现在是普通旧文件，rebuild_required 仍允许替换。
    _write_result_journal(root, revision="4", result=LinkIndexResult.REBUILD_REQUIRED, sha256=None)
    journal2 = _journal(directory, revision="5")
    _persist_journal(journal2)
    index2 = _index("5")
    digest2 = stage_link_index(journal2, index2)
    publish_staged_link_index(root, journal2, digest2)
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == index2.to_json()


def test_publish_after_published_requires_matching_target(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    _setup_published_artifact(root, revision="1")
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="2")
    _persist_journal(journal)
    index = _index("2")
    digest = stage_link_index(journal, index)

    publish_staged_link_index(root, journal, digest)

    target = root / INDEX_RELATIVE_PATH
    assert target.read_bytes() == index.to_json()
    assert not (directory / _STAGED_NAME).exists()


def test_publish_after_published_rejects_missing_target(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    _write_result_journal(root, revision="1", result=LinkIndexResult.PUBLISHED, sha256="a" * 64)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="2")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("2"))

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert (directory / _STAGED_NAME).is_file()


def test_publish_after_published_rejects_mismatched_artifact(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    _setup_published_artifact(root, revision="1")
    # 篡改目标内容，使其与 journal 不一致。
    (root / INDEX_RELATIVE_PATH).write_bytes(b"tampered")
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="2")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("2"))

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == b"tampered"


def test_publish_after_published_rejects_non_monotonic_staged_revision(
    tmp_path: Path,
) -> None:
    root = _init_root(tmp_path)
    published_raw = _setup_published_artifact(root, revision="1")
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    # 现场保留：目标未覆盖，staging 未消费。
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == published_raw
    assert (directory / _STAGED_NAME).is_file()


def test_publish_rejects_symlink_target(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))
    (root / INDEX_RELATIVE_PATH).symlink_to(root / ".draft")

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert (root / INDEX_RELATIVE_PATH).is_symlink()
    assert (directory / _STAGED_NAME).is_file()


def test_publish_rejects_symlink_staging(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    (directory / _STAGED_NAME).symlink_to(root / ".draft")

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, sha256_bytes(_index("1").to_json()))
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert not (root / INDEX_RELATIVE_PATH).exists()


def test_publish_rejects_directory_target(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))
    (root / INDEX_RELATIVE_PATH).mkdir(parents=True)

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_publish_rejects_staging_sha256_mismatch(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    stage_link_index(journal, _index("1"))

    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, "f" * 64)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert not (root / INDEX_RELATIVE_PATH).exists()


def test_publish_syncs_target_parent_then_operation_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同步顺序属于耐久协议：replace 后先同步 .draft/ 再同步 operation 目录。"""
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))

    import draft.link_index_maintenance as maintenance

    real_fsync_dir = maintenance.fsync_directory
    synced: list[Path] = []

    def recording_fsync_dir(path: Path) -> None:
        synced.append(path)
        real_fsync_dir(path)

    monkeypatch.setattr(maintenance, "fsync_directory", recording_fsync_dir)
    publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()

    assert synced[0] == root / ".draft"
    assert synced[1] == directory


def test_publish_replace_failure_preserves_staging_and_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))

    def fail_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert not (root / INDEX_RELATIVE_PATH).exists()
    assert (directory / _STAGED_NAME).is_file()


def test_publish_dot_draft_fsync_failure_reports_not_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    index = _index("1")
    digest = stage_link_index(journal, index)

    import draft.link_index_maintenance as maintenance

    real_fsync_dir = maintenance.fsync_directory

    def fail_dot_draft(path: Path) -> None:
        if path == root / ".draft":
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path)
        real_fsync_dir(path)

    monkeypatch.setattr(maintenance, "fsync_directory", fail_dot_draft)
    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    # 替换已发生但 .draft/ 未同步：目标内容存在，staging 已消费。
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == index.to_json()
    assert not (directory / _STAGED_NAME).exists()


def test_publish_operation_directory_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    index = _index("1")
    digest = stage_link_index(journal, index)

    import draft.link_index_maintenance as maintenance

    real_fsync_dir = maintenance.fsync_directory

    def fail_op_dir(path: Path) -> None:
        if path == directory:
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path)
        real_fsync_dir(path)

    monkeypatch.setattr(maintenance, "fsync_directory", fail_op_dir)
    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == index.to_json()
    assert not (directory / _STAGED_NAME).exists()


def test_publish_staging_read_eacces_maps_to_index_update_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))
    real_open = os.open

    def fail_read(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if (
            not (flags & os.O_DIRECTORY)
            and (flags & os.O_NOFOLLOW)
            and str(path).endswith(_STAGED_NAME)
        ):
            raise OSError(errno.EACCES, "injected")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", fail_read)
    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert not (root / INDEX_RELATIVE_PATH).exists()
    assert (directory / _STAGED_NAME).is_file()


def test_publish_staging_read_enoent_maps_to_inconsistent_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("1"))
    real_open = os.open

    def fail_read(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if (
            not (flags & os.O_DIRECTORY)
            and (flags & os.O_NOFOLLOW)
            and str(path).endswith(_STAGED_NAME)
        ):
            raise OSError(errno.ENOENT, "injected")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", fail_read)
    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert not (root / INDEX_RELATIVE_PATH).exists()


def test_publish_published_target_read_eacces_maps_to_index_update_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    published_raw = _setup_published_artifact(root, revision="1")
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="2")
    _persist_journal(journal)
    digest = stage_link_index(journal, _index("2"))
    real_open = os.open

    def fail_read(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if (
            not (flags & os.O_DIRECTORY)
            and (flags & os.O_NOFOLLOW)
            and str(path).endswith("link-index.json")
        ):
            raise OSError(errno.EACCES, "injected")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", fail_read)
    with pytest.raises(DraftError) as caught:
        publish_staged_link_index(root, journal, digest)
    monkeypatch.undo()
    assert caught.value.code == "operation.index_update_failed"
    assert (root / INDEX_RELATIVE_PATH).read_bytes() == published_raw
    assert (directory / _STAGED_NAME).is_file()


# ---------------------------------------------------------------------------
# rebuild-required flag（spec §10/§11）
# ---------------------------------------------------------------------------


def test_ensure_rebuild_flag_creates_zero_byte_file(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    warning = ensure_rebuild_flag(root)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    assert warning is None
    assert flag.is_file()
    assert flag.read_bytes() == b""


def test_ensure_rebuild_flag_is_idempotent(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    assert ensure_rebuild_flag(root) is None
    assert ensure_rebuild_flag(root) is None
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()


def test_ensure_rebuild_flag_rejects_symlink(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.symlink_to(root / ".draft")

    with pytest.raises(DraftError) as caught:
        ensure_rebuild_flag(root)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_ensure_rebuild_flag_rejects_directory(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    flag.mkdir(parents=True)

    with pytest.raises(DraftError) as caught:
        ensure_rebuild_flag(root)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_ensure_rebuild_flag_create_failure_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    real_open = os.open

    def fail_flag_create(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if flags & os.O_CREAT:
            raise OSError("injected create failure")
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", fail_flag_create)
    warning = ensure_rebuild_flag(root)
    monkeypatch.undo()
    assert warning == ResultWarning("link_index.rebuild_flag_update_failed")
    assert not flag.exists()


def test_ensure_rebuild_flag_directory_fsync_failure_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH

    import draft.link_index_maintenance as maintenance

    real_fsync_dir = maintenance.fsync_directory

    def fail_dot_draft(path: Path) -> None:
        if path == root / ".draft":
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path)
        real_fsync_dir(path)

    monkeypatch.setattr(maintenance, "fsync_directory", fail_dot_draft)
    warning = ensure_rebuild_flag(root)
    monkeypatch.undo()
    assert warning == ResultWarning("link_index.rebuild_flag_update_failed")
    # os.open 已创建 flag；目录项未同步，调用方只收到 warning。
    assert flag.is_file()


def test_remove_rebuild_flag_removes_zero_byte_file(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    assert ensure_rebuild_flag(root) is None
    warning = remove_rebuild_flag(root)
    assert warning is None
    assert not (root / REBUILD_FLAG_RELATIVE_PATH).exists()


def test_remove_rebuild_flag_idempotent_when_missing(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    assert remove_rebuild_flag(root) is None
    assert not (root / REBUILD_FLAG_RELATIVE_PATH).exists()


def test_remove_rebuild_flag_rejects_symlink(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.symlink_to(root / ".draft")

    with pytest.raises(DraftError) as caught:
        remove_rebuild_flag(root)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_remove_rebuild_flag_rejects_directory(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    flag.mkdir(parents=True)

    with pytest.raises(DraftError) as caught:
        remove_rebuild_flag(root)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value


def test_remove_rebuild_flag_rejects_symlink_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "internal-target"
    target.mkdir()
    (target / "operations").mkdir()
    (target / "link-index-rebuild-required.flag").write_bytes(b"")
    (root / ".draft").symlink_to(target, target_is_directory=True)

    with pytest.raises(DraftError) as caught:
        remove_rebuild_flag(root)
    assert caught.value.code == CheckCode.PATH_SYMLINK_ESCAPE.value


def test_remove_rebuild_flag_unlink_failure_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    assert ensure_rebuild_flag(root) is None

    def fail_unlink(path: object, *args: object, **kwargs: object) -> None:
        raise OSError("injected unlink failure")

    monkeypatch.setattr(os, "unlink", fail_unlink)
    warning = remove_rebuild_flag(root)
    monkeypatch.undo()
    assert warning == ResultWarning("link_index.rebuild_flag_update_failed")
    assert flag.exists()


def test_remove_rebuild_flag_directory_fsync_failure_returns_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_root(tmp_path)
    flag = root / REBUILD_FLAG_RELATIVE_PATH
    assert ensure_rebuild_flag(root) is None

    import draft.link_index_maintenance as maintenance

    real_fsync_dir = maintenance.fsync_directory

    def fail_dot_draft(path: Path) -> None:
        if path == root / ".draft":
            raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, path)
        real_fsync_dir(path)

    monkeypatch.setattr(maintenance, "fsync_directory", fail_dot_draft)
    warning = remove_rebuild_flag(root)
    monkeypatch.undo()
    assert warning == ResultWarning("link_index.rebuild_flag_update_failed")
    assert not flag.exists()


# ---------------------------------------------------------------------------
# staging 清理（spec §12）
# ---------------------------------------------------------------------------


def test_cleanup_link_index_staging_removes_staged_file(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    stage_link_index(journal, _index("1"))

    cleanup_link_index_staging(journal)

    assert not (directory / _STAGED_NAME).exists()
    assert (directory / "journal.json").is_file()


def test_cleanup_link_index_staging_idempotent_when_missing(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)

    cleanup_link_index_staging(journal)

    assert not (directory / _STAGED_NAME).exists()


def test_cleanup_link_index_staging_rejects_symlink(tmp_path: Path) -> None:
    root = _init_root(tmp_path)
    directory = root / ".draft" / "operations" / ("a" * 32)
    journal = _journal(directory, revision="1")
    _persist_journal(journal)
    (directory / _STAGED_NAME).symlink_to(root / ".draft")

    with pytest.raises(DraftError) as caught:
        cleanup_link_index_staging(journal)
    assert caught.value.code == CheckCode.OPERATION_INCONSISTENT_STATE.value
    assert (directory / _STAGED_NAME).is_symlink()


# ---------------------------------------------------------------------------
# Task 5：独立 link-index.update maintenance operation（spec §5/§14/§15）
# ---------------------------------------------------------------------------


def _maintenance_root(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / ".draft" / "operations").mkdir(parents=True)
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _operation_dirs(root: Path) -> list[Path]:
    operations = root / ".draft" / "operations"
    if not operations.is_dir():
        return []
    return sorted(
        (operations / entry.name for entry in operations.iterdir()),
        key=lambda path: path.name,
    )


def _journal_payload(root: Path) -> dict[str, object]:
    """返回最新权威（数值最大 revision）operation 的 journal 负载。"""
    best: tuple[int, dict[str, object]] | None = None
    for directory in _operation_dirs(root):
        payload = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
        revision = payload.get("link_index_revision")
        if revision is None:
            continue
        numeric = int(revision)
        if best is None or numeric > best[0]:
            best = (numeric, payload)
    assert best is not None, "no journal with a link_index_revision found"
    return best[1]


def _journal_phase(root: Path) -> str:
    """返回最新权威 operation 的 phase；只有一个 operation 时等价于现场 phase。"""
    dirs = _operation_dirs(root)
    assert len(dirs) == 1
    payload = json.loads((dirs[0] / "journal.json").read_text(encoding="utf-8"))
    return payload["phase"]


def test_maintain_first_publish_creates_revision_one(tmp_path: Path) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})

    result = maintain_link_index(root)

    assert result.published is True
    assert result.index is not None
    assert result.index.revision == "1"
    assert result.warnings == ()
    target = root / INDEX_RELATIVE_PATH
    assert target.is_file()
    payload = _journal_payload(root)
    assert payload["phase"] == "completed"
    assert payload["link_index_revision"] == "1"
    assert payload["link_index_result"] == "published"
    assert read_lock(root) is None


def test_maintain_candidate_root_records_candidate_root_state(tmp_path: Path) -> None:
    """T5 review P3-3：candidate 根的维护 journal root_state 必须记录 candidate。

    通知是首个触达 candidate 根的维护消费者；真实根状态必须经 maintain_link_index
    穿通到 preallocate_journal，而不是硬编码 registered。
    """
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})

    result = maintain_link_index(root, root_state=RootState.CANDIDATE)

    assert result.published is True
    payload = _journal_payload(root)
    assert payload["root_state"] == "candidate"
    assert payload["link_index_result"] == "published"


def test_maintain_no_change_does_not_allocate_operation(tmp_path: Path) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    assert maintain_link_index(root).published is True
    before = _operation_dirs(root)

    result = maintain_link_index(root)

    assert result.published is False
    assert result.index is None
    assert result.warnings == ()
    assert _operation_dirs(root) == before


def test_maintain_stale_increments_revision_numerically(tmp_path: Path) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    assert maintain_link_index(root).published is True
    (root / "pages" / "a.md").write_text("# A changed\n", encoding="utf-8")

    result = maintain_link_index(root)

    assert result.published is True
    assert result.index is not None
    assert result.index.revision == "2"
    payload = _journal_payload(root)
    assert payload["link_index_revision"] == "2"
    assert payload["link_index_result"] == "published"


def test_maintain_scan_failure_records_rebuild_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def fail_scan(root: Path, baseline: object, *, known_documents: object = ()) -> object:
        raise DraftError("operation.index_update_failed", root)

    monkeypatch.setattr(maintenance, "scan_link_index", fail_scan)

    result = maintain_link_index(root)

    monkeypatch.undo()
    # P2-1：published 只表示已发布索引制品；rebuild_required 路径 published=False。
    assert result.published is False
    assert result.index is None
    assert result.warnings == (ResultWarning("link_index.rebuild_required"),)
    payload = _journal_payload(root)
    assert payload["phase"] == "completed"
    assert payload["link_index_result"] == "rebuild_required"
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()
    assert read_lock(root) is None


def test_maintain_publish_failure_degrades_to_rebuild_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def fail_publish(root: Path, journal: object, expected_sha256: str) -> None:
        raise DraftError("operation.index_update_failed", root / INDEX_RELATIVE_PATH)

    monkeypatch.setattr(maintenance, "publish_staged_link_index", fail_publish)

    result = maintain_link_index(root)

    monkeypatch.undo()
    assert result.published is False
    assert result.index is None
    assert result.warnings == (ResultWarning("link_index.rebuild_required"),)
    payload = _journal_payload(root)
    assert payload["phase"] == "completed"
    assert payload["link_index_result"] == "rebuild_required"
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()
    # P3-2：降级路径写 completed 前清理残留 staging。
    dirs = _operation_dirs(root)
    assert not (dirs[0] / LINK_INDEX_STAGED_FILE_NAME).exists()
    assert read_lock(root) is None


def test_revision_advance_race_abandons_candidate(tmp_path: Path) -> None:
    """spec §15 并发令牌：取锁后 authority 已推进，放弃旧候选，不发布/不降级。

    第一个 maintenance 发布 revision "1"；第二个修改文件并发布 "2"；携带 observed
    revision "1" 的旧候选 commit 必须放弃——不创建新 operation、不写 rebuild_required。
    """
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    assert maintain_link_index(root).published is True  # revision "1"
    (root / "pages" / "a.md").write_text("# A changed\n", encoding="utf-8")

    # 扫描时观察到 revision "1"。
    prepared = prepare_link_index_update(root)
    assert prepared.observed_revision == "1"
    assert prepared.scan is not None

    # 另一个受协调 operation 在 commit 前发布了 "2"。
    assert maintain_link_index(root).published is True
    assert read_link_index_authority(root).revision == "2"

    result = commit_link_index_update(root, prepared)

    assert result.published is False
    assert result.index is None
    assert result.warnings == ()
    # 旧候选被放弃：没有产生 revision "3" 的新 operation，也没有降级。
    assert len(_operation_dirs(root)) == 2
    assert read_link_index_authority(root).revision == "2"
    assert not (root / REBUILD_FLAG_RELATIVE_PATH).exists()


def test_maintain_scan_external_change_records_rebuild_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §14/§15：扫描期间根再次变化时不发布混合时点索引，耐久记录 rebuild_required。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def unstable_verify(
        root: Path, expected_directories: object, expected_identities: object
    ) -> None:
        raise DraftError("operation.external_change", root)

    monkeypatch.setattr(maintenance, "_verify_root_stable", unstable_verify)

    result = maintain_link_index(root)

    monkeypatch.undo()
    assert result.published is False
    assert result.index is None
    assert result.warnings == (ResultWarning("link_index.rebuild_required"),)
    payload = _journal_payload(root)
    assert payload["phase"] == "completed"
    assert payload["link_index_result"] == "rebuild_required"
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()
    assert read_lock(root) is None


def test_maintain_lock_held_returns_unpublished_without_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    from draft.operations import acquire_lock

    acquire_lock(root, "f" * 32)
    scanned: dict[str, bool] = {"called": False}
    import draft.link_index_maintenance as maintenance

    def scan_spy(root: Path, baseline: object, *, known_documents: object = ()) -> object:
        scanned["called"] = True
        raise AssertionError("must not scan when lock is held")

    monkeypatch.setattr(maintenance, "scan_link_index", scan_spy)

    result = maintain_link_index(root)

    monkeypatch.undo()
    assert result.published is False
    assert result.index is None
    assert result.warnings == ()
    assert not scanned["called"]


# ---------------------------------------------------------------------------
# maintenance phase 中断现场（spec §12；恢复由 Task 7 接入）
# ---------------------------------------------------------------------------


def _fail_write_at(monkeypatch: pytest.MonkeyPatch, fail_at: frozenset[int]) -> None:
    import draft.link_index_maintenance as maintenance

    real_write = maintenance.write_journal
    calls = {"n": 0}

    def flaky_write(journal: Journal) -> None:
        calls["n"] += 1
        if calls["n"] in fail_at:
            raise DraftError(
                CheckCode.OPERATION_DURABILITY_FAILED.value, Path("/draft/.draft/operations")
            )
        real_write(journal)

    monkeypatch.setattr(maintenance, "write_journal", flaky_write)


def test_interrupt_at_allocating_preserves_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    _fail_write_at(monkeypatch, frozenset({1}))  # 第一次 maintenance 写是 link_index_updating

    with pytest.raises(DraftError):
        maintain_link_index(root)

    monkeypatch.undo()
    assert _journal_phase(root) == "allocating"
    assert not (root / INDEX_RELATIVE_PATH).exists()
    assert read_lock(root) is not None


def test_interrupt_at_link_index_updating_preserves_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def crash_in_stage(journal: object, index: object) -> str:
        raise RuntimeError("process crash during staging")

    monkeypatch.setattr(maintenance, "stage_link_index", crash_in_stage)

    with pytest.raises(RuntimeError):
        maintain_link_index(root)

    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_updating"
    assert not (root / INDEX_RELATIVE_PATH).exists()
    assert read_lock(root) is not None


def test_interrupt_after_target_replace_preserves_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    _fail_write_at(monkeypatch, frozenset({2}))  # 第二次是 link_index_published

    with pytest.raises(DraftError):
        maintain_link_index(root)

    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_updating"
    assert (root / INDEX_RELATIVE_PATH).is_file()
    dirs = _operation_dirs(root)
    assert not (dirs[0] / LINK_INDEX_STAGED_FILE_NAME).exists()
    assert read_lock(root) is not None


def test_interrupt_at_link_index_published_preserves_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    _fail_write_at(monkeypatch, frozenset({3}))  # 第三次是 completed

    with pytest.raises(DraftError):
        maintain_link_index(root)

    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_published"
    assert (root / INDEX_RELATIVE_PATH).is_file()
    assert read_lock(root) is not None


def test_interrupt_at_link_index_rebuild_required_preserves_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def fail_scan(root: Path, baseline: object, *, known_documents: object = ()) -> object:
        raise DraftError("operation.index_update_failed", root)

    monkeypatch.setattr(maintenance, "scan_link_index", fail_scan)
    _fail_write_at(monkeypatch, frozenset({3}))  # rebuild 路径第三次是 completed

    with pytest.raises(DraftError):
        maintain_link_index(root)

    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_rebuild_required"
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()
    assert read_lock(root) is not None


def test_interrupt_at_completed_preserves_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def fail_release(root: Path, operation_id: str) -> None:
        raise DraftError(
            CheckCode.OPERATION_DURABILITY_FAILED.value, root / ".draft" / "operation.lock"
        )

    monkeypatch.setattr(maintenance, "release_lock", fail_release)

    with pytest.raises(DraftError):
        maintain_link_index(root)

    monkeypatch.undo()
    assert _journal_phase(root) == "completed"
    assert read_lock(root) is not None


# ---------------------------------------------------------------------------
# maintenance 恢复（spec §12）：allocating 可回滚，三索引 phase 前向收敛，completed 幂等清理。
# ---------------------------------------------------------------------------


def _recover_maintenance(root: Path) -> OperationResult:
    from draft.operations import recover

    return recover(root)


def _operation_journal(root: Path, op_id: str) -> Journal:
    from draft.operations import operation_journal_path, read_journal

    return read_journal(operation_journal_path(root, op_id))


def test_recover_maintenance_allocating_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allocating 中断且无索引副作用：maintenance 写 rolled_back 并释放锁（spec §12）。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    _fail_write_at(monkeypatch, frozenset({1}))  # 停在 allocating（updating 未耐久）
    with pytest.raises(DraftError):
        maintain_link_index(root)
    monkeypatch.undo()
    assert _journal_phase(root) == "allocating"
    op_id = read_lock(root)
    assert op_id is not None

    result = _recover_maintenance(root)

    assert result.phase is OperationPhase.ROLLED_BACK
    journal = _operation_journal(root, op_id)
    assert journal.phase is OperationPhase.ROLLED_BACK
    assert read_lock(root) is None


def test_recover_maintenance_allocating_with_staging_is_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allocating 不应有 staging：出现未知内部文件时报不一致并保留现场（spec §12）。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    _fail_write_at(monkeypatch, frozenset({1}))
    with pytest.raises(DraftError):
        maintain_link_index(root)
    monkeypatch.undo()
    op_id = read_lock(root)
    assert op_id is not None
    op_dir = root / ".draft/operations" / op_id
    (op_dir / LINK_INDEX_STAGED_FILE_NAME).write_text("{}", encoding="utf-8")

    with pytest.raises(DraftError, match=CheckCode.OPERATION_INCONSISTENT_STATE.value):
        _recover_maintenance(root)

    assert read_lock(root) == op_id
    assert (op_dir / LINK_INDEX_STAGED_FILE_NAME).exists()
    journal = _operation_journal(root, op_id)
    assert journal.phase is OperationPhase.ALLOCATING


def test_recover_maintenance_updating_enters_rebuild_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """maintenance 在 link_index_updating 中断：进入 rebuild_required 并完成（spec §12）。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def crash_in_stage(journal: object, index: object) -> str:
        raise RuntimeError("process crash during staging")

    monkeypatch.setattr(maintenance, "stage_link_index", crash_in_stage)
    with pytest.raises(RuntimeError):
        maintain_link_index(root)
    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_updating"
    op_id = read_lock(root)
    assert op_id is not None

    result = _recover_maintenance(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = _operation_journal(root, op_id)
    assert journal.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert journal.link_index_revision == "1"
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()
    assert read_lock(root) is None


def test_recover_maintenance_published_completes_and_preserves_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """maintenance 在 link_index_published 中断：验证制品后完成，不产生新 revision（spec §12）。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    _fail_write_at(monkeypatch, frozenset({3}))  # 停在 published（completed 未耐久）
    with pytest.raises(DraftError):
        maintain_link_index(root)
    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_published"
    op_id = read_lock(root)
    assert op_id is not None
    artifact = root / INDEX_RELATIVE_PATH
    artifact_bytes = artifact.read_bytes()
    journal_before = _operation_journal(root, op_id)

    result = _recover_maintenance(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = _operation_journal(root, op_id)
    assert journal.link_index_result is LinkIndexResult.PUBLISHED
    assert journal.link_index_revision == journal_before.link_index_revision
    assert journal.link_index_sha256 == journal_before.link_index_sha256
    assert artifact.read_bytes() == artifact_bytes
    assert read_lock(root) is None


def test_recover_maintenance_rebuild_required_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """maintenance 在 link_index_rebuild_required 中断：补 flag 后完成（spec §12）。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def fail_scan(root: Path, baseline: object, *, known_documents: object = ()) -> object:
        raise DraftError("operation.index_update_failed", root)

    monkeypatch.setattr(maintenance, "scan_link_index", fail_scan)
    _fail_write_at(monkeypatch, frozenset({3}))  # rebuild 路径停在 rebuild_required
    with pytest.raises(DraftError):
        maintain_link_index(root)
    monkeypatch.undo()
    assert _journal_phase(root) == "link_index_rebuild_required"
    op_id = read_lock(root)
    assert op_id is not None

    result = _recover_maintenance(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = _operation_journal(root, op_id)
    assert journal.link_index_result is LinkIndexResult.REBUILD_REQUIRED
    assert journal.link_index_revision == "1"
    assert (root / REBUILD_FLAG_RELATIVE_PATH).is_file()
    assert read_lock(root) is None


def test_recover_maintenance_completed_releases_lock_and_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """completed 中断于释放锁前：recover 幂等清理锁和已知 staging（spec §12）。"""
    root = _maintenance_root(tmp_path, {"pages/a.md": "# A\n"})
    import draft.link_index_maintenance as maintenance

    def fail_release(root: Path, operation_id: str) -> None:
        raise DraftError(
            CheckCode.OPERATION_DURABILITY_FAILED.value, root / ".draft" / "operation.lock"
        )

    monkeypatch.setattr(maintenance, "release_lock", fail_release)
    with pytest.raises(DraftError):
        maintain_link_index(root)
    monkeypatch.undo()
    assert _journal_phase(root) == "completed"
    op_id = read_lock(root)
    assert op_id is not None
    op_dir = root / ".draft/operations" / op_id
    (op_dir / LINK_INDEX_STAGED_FILE_NAME).write_text("{}", encoding="utf-8")

    result = _recover_maintenance(root)

    assert result.phase is OperationPhase.COMPLETED
    journal = _operation_journal(root, op_id)
    assert journal.phase is OperationPhase.COMPLETED
    assert not (op_dir / LINK_INDEX_STAGED_FILE_NAME).exists()
    assert read_lock(root) is None
