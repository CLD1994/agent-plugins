import json
from pathlib import Path
from typing import cast

import pytest

from draft.checker import check_draft
from draft.cli import main
from tests.integration.support import DraftFactory

PAGE_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
"""

PAGE_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲。。
"""


def build_page_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": PAGE_A,
        }
    )


def run_json(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(args)
    output = capsys.readouterr().out
    return code, cast("dict[str, object]", json.loads(output))


def test_create_leaf_creates_page_and_updates_navigation(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["create", str(root), "pages/b.md", "--title", "乙：[x]", "--status", "pending", "--json"],
        capsys,
    )

    assert code == 0
    assert payload["result_type"] == "create"
    assert payload["path"] == "pages/b.md"
    changed = cast("list[str]", payload["changed_paths"])
    assert "pages/b.md" in changed
    assert "index.md" in changed

    created = (root / "pages/b.md").read_text(encoding="utf-8")
    assert "# 乙：[x]" in created
    assert "- [乙：\\[x\\]](pages/b.md)" in (root / "index.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


def test_create_dry_run_does_not_write(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "create",
            str(root),
            "pages/b.md",
            "--title",
            "乙",
            "--status",
            "pending",
            "--dry-run",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "create"
    assert not (root / "pages/b.md").exists()
    assert "pages/b.md" not in (root / "index.md").read_text(encoding="utf-8")


def test_create_rejects_invalid_operand(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["create", str(root), "../b.md", "--title", "乙", "--status", "pending", "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] in ("path.escape", "structure.invalid_target")
    assert not (root / "b.md").exists()


def test_create_rejects_invalid_status(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    # argparse choices 拒绝 confirmed：用法错误，不创建文件。
    with pytest.raises(SystemExit):
        main(["create", str(root), "pages/b.md", "--title", "乙", "--status", "confirmed"])

    assert not (root / "pages/b.md").exists()


def test_create_inserts_before_conclusions_link(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    # 先把甲设为已确认，使 generate-conclusions 创建结论页与根导航链接。
    (root / "pages/a.md").write_text(PAGE_A.replace("待讨论", "已确认"), encoding="utf-8")
    assert main(["generate-conclusions", str(root)]) == 0
    capsys.readouterr()

    code, _payload = run_json(
        ["create", str(root), "pages/b.md", "--title", "乙", "--status", "proposed", "--json"],
        capsys,
    )

    assert code == 0
    index = (root / "index.md").read_text(encoding="utf-8")
    assert index.index("- [乙](pages/b.md)") < index.index("- [结论汇总](conclusions.md)")


def test_status_set_changes_and_validates_final_state(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    (root / "pages/a.md").write_text(PAGE_A.replace("待讨论", "已确认"), encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", str(root), "pages/a.md", "proposed", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "status.set"
    assert payload["status"] == "proposed"
    assert "pages/a.md" in cast("list[str]", payload["changed_paths"])
    assert "当前提案" in (root / "pages/a.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


def test_status_set_rejects_invalid_final_state(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", str(root), "pages/a.md", "superseded", "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "status.invalid_final_state"
    # 未写入：状态保持待讨论。
    assert "待讨论" in (root / "pages/a.md").read_text(encoding="utf-8")


def test_status_set_note_and_clear_note(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, _payload = run_json(
        ["status", "set", str(root), "pages/a.md", "proposed", "--note", "新方案", "--json"],
        capsys,
    )
    assert code == 0
    assert "当前提案：新方案" in (root / "pages/a.md").read_text(encoding="utf-8")

    code, _payload = run_json(
        ["status", "set", str(root), "pages/a.md", "pending", "--clear-note", "--json"],
        capsys,
    )
    assert code == 0
    assert "待讨论" in (root / "pages/a.md").read_text(encoding="utf-8")
    assert "新方案" not in (root / "pages/a.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


def test_status_set_missing_page_is_page_not_found(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", str(root), "pages/missing.md", "pending", "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "page.not_found"


def test_status_set_dry_run_does_not_write(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "status",
            "set",
            str(root),
            "pages/a.md",
            "proposed",
            "--dry-run",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "status.set"
    assert "待讨论" in (root / "pages/a.md").read_text(encoding="utf-8")
