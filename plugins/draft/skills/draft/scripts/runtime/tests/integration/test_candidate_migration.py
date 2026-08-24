from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from draft.cli import main
from draft.models import RootState


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _marker(root: Path) -> dict[str, object]:
    decoded = json.loads((root / ".draft/draft.json").read_text(encoding="utf-8"))
    return cast("dict[str, object]", decoded)


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_page(title: str, status: str, parent: str, body: str) -> str:
    return f"# {title}\n\n> 状态：`{status}`\n>\n> 父主题：[{parent}](../index.md)\n\n{body}\n"


def _run_json(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(args)
    output = capsys.readouterr().out
    return code, cast("dict[str, object]", json.loads(output))


def test_candidate_root_incremental_migration_preserves_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """完整迁移验收：固定来源零写入，candidate 根逐步 CLI，adopt 后为正式根。"""
    source = tmp_path / "source"
    source.mkdir()
    _write(source, "index.html", "<h1>旧主题</h1>\n")
    _write(source, "notes.md", "旧笔记\n")
    source_before = _snapshot(source)

    target = tmp_path / "target"
    assert main(["init", "--candidate", str(target), "--json"]) == 0
    capsys.readouterr()

    def cli(*args: str) -> int:
        if args[0] in ("status", "relation", "query"):
            return main([args[0], args[1], "--root", str(target), *args[2:], "--json"])
        return main([args[0], "--root", str(target), *args[1:], "--json"])

    # 连续普通命令，中间不运行 check。
    assert cli("create", "pages/a.md", "--title", "甲", "--status", "pending") == 0
    assert cli("create", "pages/b.md", "--title", "乙", "--status", "proposed") == 0
    assert cli("status", "set", "pages/b.md", "confirmed") == 0
    capsys.readouterr()
    # Agent 在命令间隙直接编辑标题、正文和正文链接。
    _write(target, "pages/a.md", _write_page("甲", "待讨论", "index.md", "见 [乙](b.md)。"))
    _write(
        target,
        "index.md",
        "# target\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n"
        "## 子主题\n\n- [甲](pages/a.md)\n- [乙](pages/b.md)\n",
    )
    assert cli("move", "pages/a.md", "pages/topic.md") == 0
    capsys.readouterr()
    # 候选根存在无关诊断（b.md 缺父导航）时局部命令仍成功。
    assert cli("create", "pages/c.md", "--title", "丙", "--status", "pending") == 0
    capsys.readouterr()
    # 手工建立一个未登记导航的页面：产生持久化 parent.navigation_missing 诊断。
    _write(target, "pages/d.md", _write_page("丁", "待讨论", "index.md", "丁。\n"))
    capsys.readouterr()
    # 候选根存在无关诊断时局部命令仍成功。
    assert cli("create", "pages/e.md", "--title", "戊", "--status", "pending") == 0
    capsys.readouterr()
    # 候选根存在无关诊断时 relation 仍成功：依赖判环只需两端可判定。
    assert cli("relation", "add", "pages/topic.md", "depends_on", "pages/b.md") == 0
    capsys.readouterr()

    code, count_only = _run_json(["check", "--root", str(target), "--count-only", "--json"], capsys)
    assert code == 1
    assert count_only["ok"] is False
    assert cast("int", count_only["filtered_total"]) >= 1

    # 批量修复：补全全部父导航。
    _write(
        target,
        "index.md",
        "# target\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n"
        "## 子主题\n\n"
        "- [甲](pages/topic.md)\n- [乙](pages/b.md)\n- [丙](pages/c.md)\n"
        "- [丁](pages/d.md)\n- [戊](pages/e.md)\n",
    )

    assert cli("check") == 0
    capsys.readouterr()
    # 候选根生成结论：全部受管页面可判定时允许，并推进所有权。
    assert cli("generate-conclusions") == 0
    capsys.readouterr()
    assert (target / "conclusions.md").is_file()
    assert _marker(target)["conclusions_state"] == "generated"
    # 候选根复用回收与恢复生命周期。
    code, trash_payload = _run_json(
        ["trash", "--root", str(target), "pages/e.md", "--json"], capsys
    )
    assert code == 0
    trash_id = cast("str", trash_payload["operation_id"])
    assert not (target / "pages/e.md").exists()
    code, _restore_payload = _run_json(
        ["restore", "--root", str(target), trash_id, "--json"], capsys
    )
    assert code == 0
    assert (target / "pages/e.md").is_file()
    assert cli("check") == 0
    capsys.readouterr()
    assert cli("adopt") == 0
    capsys.readouterr()
    assert cli("check") == 0
    capsys.readouterr()

    assert _marker(target)["root_state"] == RootState.REGISTERED.value
    # 无迁移专用目录或命令残留。
    assert not (target / ".draft" / "migration").exists()
    # 来源全树逐项一致。
    assert _snapshot(source) == source_before


def test_adopt_rejects_candidate_with_errors_and_keeps_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    assert main(["init", "--candidate", str(target), "--json"]) == 0
    capsys.readouterr()
    _write(target, "pages/a.md", "无标题。\n")

    code, payload = _run_json(["adopt", "--root", str(target), "--json"], capsys)
    assert code == 2
    assert payload["ok"] is False
    assert _marker(target)["root_state"] == RootState.CANDIDATE.value


def test_adopt_precheck_completes_opportunistic_maintenance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """adopt 预检查先结束独立 maintenance operation，再开始 adopt（spec §14/§16）。"""
    target = tmp_path / "target"
    assert main(["init", "--candidate", str(target), "--json"]) == 0
    capsys.readouterr()
    _write(target, "pages/a.md", _write_page("甲", "待讨论", "index.md", "甲的正文。"))
    _write(
        target,
        "index.md",
        "# target\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n"
        "## 子主题\n\n- [甲](pages/a.md)\n",
    )

    assert main(["adopt", "--root", str(target), "--json"]) == 0
    capsys.readouterr()

    assert _marker(target)["root_state"] == RootState.REGISTERED.value
    operations = target / ".draft" / "operations"
    journals = (
        [
            json.loads((directory / "journal.json").read_text(encoding="utf-8"))
            for directory in operations.iterdir()
        ]
        if operations.is_dir()
        else []
    )
    index_journals = [
        journal for journal in journals if journal.get("link_index_result") is not None
    ]
    assert index_journals, "adopt 预检查未完成索引维护"
    latest = max(index_journals, key=lambda journal: int(journal["link_index_revision"]))
    assert latest["link_index_result"] == "published"
    assert latest["root_state"] == RootState.CANDIDATE.value
