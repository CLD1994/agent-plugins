import json
from pathlib import Path
from typing import Protocol

from draft.models import OperationKind, RootState
from draft.operations import JOURNAL_VERSION
from draft.vocabulary import ConclusionsState


def managed_marker_text(
    *,
    root_state: RootState = RootState.REGISTERED,
    conclusions_state: ConclusionsState = ConclusionsState.NOT_GENERATED,
) -> str:
    """共享正式 marker 构建器：集成夹具统一从权威类型取值，不复制协议字面量。"""
    return (
        json.dumps(
            {
                "format_version": 1,
                "root_state": root_state.value,
                "conclusions_state": conclusions_state.value,
            }
        )
        + "\n"
    )


class DraftFactory(Protocol):
    def __call__(
        self,
        files: dict[str, str],
        *,
        root_state: RootState | None = RootState.REGISTERED,
        precreate_internal: bool = False,
    ) -> Path:
        raise AssertionError("typing protocol method is not executed")


def write_raw_journal(
    root: Path,
    *,
    version: object | None = None,
    operation_id: str = "a" * 32,
    directory_id: str | None = None,
    kind: OperationKind = OperationKind.CREATE,
) -> Path:
    """写入原始操作日志：默认使用当前权威 JOURNAL_VERSION；调用方仍可显式覆盖版本。

    用于版本/身份拒绝测试时显式传 version；普通夹具不带版本避免复制版本号。
    """
    directory = root / ".draft" / "operations" / (directory_id or operation_id)
    directory.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {
        "journal_version": JOURNAL_VERSION if version is None else version,
        "operation_id": operation_id,
        "kind": kind.value,
        "phase": "completed",
        "root_state": "registered",
        "observations": [],
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = directory / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    return journal_path


def build_valid_candidate(root: Path) -> None:
    """构造合法的 candidate-marker 受管根：可被 adopt 接受的候选根。"""
    root.mkdir()
    (root / "pages").mkdir()
    # 规格 §12.2：候选目录不允许预先存在 conclusions.md（所有权为 not_generated）。
    (root / "index.md").write_text(
        "# 主题\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n",
        encoding="utf-8",
    )
    internal = root / ".draft"
    internal.mkdir()
    (internal / "operations").mkdir()
    (internal / "trash" / "outgoing").mkdir(parents=True)
    (internal / "draft.json").write_text(
        managed_marker_text(root_state=RootState.CANDIDATE),
        encoding="utf-8",
    )


def add_valid_child(root: Path, relative: Path) -> None:
    """Add a structurally valid child page plus its navigation link from the root."""
    page = root / relative
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "# 新页面\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n正文。\n",
        encoding="utf-8",
    )
    index = root / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8") + f"- [新页面]({relative.as_posix()})\n",
        encoding="utf-8",
    )


def snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
