import json
from pathlib import Path
from typing import Protocol


class DraftFactory(Protocol):
    def __call__(self, files: dict[str, str], *, registered: bool = True) -> Path:
        raise AssertionError("typing protocol method is not executed")


def write_raw_journal(
    root: Path,
    *,
    version: object,
    operation_id: str = "a" * 32,
    directory_id: str | None = None,
) -> Path:
    """写入原始操作日志：调用方显式控制版本与身份，用于版本/身份拒绝测试。"""
    directory = root / ".draft" / "operations" / (directory_id or operation_id)
    directory.mkdir(parents=True, exist_ok=True)
    journal: dict[str, object] = {
        "journal_version": version,
        "operation_id": operation_id,
        "kind": "test",
        "phase": "completed",
        "validation_mode": "registered",
        "mutations": [],
        "remove_empty_directories": [],
        "metadata": {},
    }
    journal_path = directory / "journal.json"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    return journal_path


def build_valid_candidate(root: Path) -> None:
    root.mkdir()
    (root / "pages").mkdir()
    # 规格 §12.2：候选目录不允许预先存在 conclusions.md（所有权为 not_generated）。
    (root / "index.md").write_text(
        "# 主题\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n",
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
