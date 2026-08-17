import json
from pathlib import Path
from typing import cast

import pytest

from draft.checker import check_draft
from draft.cli import main
from draft.models import RootState
from tests.integration.support import DraftFactory

PAGE_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
- [乙](pages/b.md)
"""

PAGE_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲。。
"""

PAGE_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

乙。。
"""


def build_page_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": PAGE_A,
            "pages/b.md": PAGE_B,
        }
    )


def _candidate_with_unrelated_broken_page(draft_factory: DraftFactory) -> Path:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": PAGE_A,
            "pages/b.md": PAGE_B,
            "pages/broken.md": "无标题正文。\n",
        },
        root_state=RootState.CANDIDATE,
    )
    return root


def test_candidate_create_succeeds_with_unrelated_bad_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _candidate_with_unrelated_broken_page(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/c.md",
            "--title",
            "丙",
            "--status",
            "pending",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert (root / "pages/c.md").is_file()
    assert payload["result_type"] == "create"


def test_candidate_status_set_succeeds_with_unrelated_bad_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _candidate_with_unrelated_broken_page(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", "--root", str(root), "pages/a.md", "confirmed", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "status.set"


def test_candidate_status_set_rejects_bad_target_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _candidate_with_unrelated_broken_page(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", "--root", str(root), "pages/broken.md", "confirmed", "--json"], capsys
    )

    assert code == 2
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "page.not_found"


def test_registered_create_accepts_preexisting_temp_like_file(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """根内预存 .tmp-<32hex> 名称的普通文件不得被临时文件过滤误判为外部变化。

    回归 H3：比较探针若按文件名形状全局过滤，会把 live 成员过滤掉而与计划观察
    （记录了该成员）不一致，结构命令错误返回 operation.external_change。
    """
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": PAGE_A,
            "pages/b.md": PAGE_B,
        }
    )
    keep = root / "pages" / f"keep.tmp-{'a' * 32}"
    keep.write_text("# 保留的正文\n", encoding="utf-8")
    keep_before = keep.read_bytes()
    capsys.readouterr()

    code, _payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/c.md",
            "--title",
            "丙",
            "--status",
            "pending",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert (root / "pages/c.md").is_file()
    assert keep.read_bytes() == keep_before


def test_registered_create_rejects_any_global_error(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory(
        {
            "index.md": PAGE_INDEX,
            "pages/a.md": PAGE_A,
            "pages/b.md": PAGE_B,
            "pages/broken.md": "无标题正文。\n",
        }
    )
    capsys.readouterr()

    code, payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/c.md",
            "--title",
            "丙",
            "--status",
            "pending",
            "--json",
        ],
        capsys,
    )

    assert code == 2
    assert payload["ok"] is False
    assert not (root / "pages/c.md").exists()


def run_json(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(args)
    output = capsys.readouterr().out
    return code, cast("dict[str, object]", json.loads(output))


def test_minimal_root_first_create_creates_internal_dir_on_demand(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """最小受管根（无预建内部目录）首次 create 按需创建 .draft/operations。"""
    root = draft_factory(
        {
            "index.md": (
                "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
                "- [乙](pages/b.md)\n"
            ),
            "pages/b.md": (
                "# 乙\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n乙。\n"
            ),
        }
    )
    assert not (root / ".draft/operations").exists()

    code, _payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/a.md",
            "--title",
            "甲",
            "--status",
            "pending",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert (root / "pages/a.md").is_file()
    assert (root / ".draft/operations").exists()
    assert check_draft(root).errors == ()


def test_create_leaf_creates_page_and_updates_navigation(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/c.md",
            "--title",
            "乙：[x]",
            "--status",
            "pending",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert payload["result_type"] == "create"
    assert payload["path"] == "pages/c.md"
    changed = cast("list[str]", payload["changed_paths"])
    assert "pages/c.md" in changed
    assert "index.md" in changed

    created = (root / "pages/c.md").read_text(encoding="utf-8")
    assert "# 乙：[x]" in created
    assert "- [乙：\\[x\\]](pages/c.md)" in (root / "index.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


def test_create_dry_run_does_not_write(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/c.md",
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
    assert not (root / "pages/c.md").exists()
    assert "pages/c.md" not in (root / "index.md").read_text(encoding="utf-8")


def test_create_rejects_invalid_operand(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "../b.md",
            "--title",
            "乙",
            "--status",
            "pending",
            "--json",
        ],
        capsys,
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
        main(
            ["create", "--root", str(root), "pages/c.md", "--title", "乙", "--status", "confirmed"]
        )

    assert not (root / "pages/c.md").exists()


def test_create_inserts_before_conclusions_link(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    # 先把甲设为已确认，使 generate-conclusions 创建结论页与根导航链接。
    (root / "pages/a.md").write_text(PAGE_A.replace("待讨论", "已确认"), encoding="utf-8")
    assert main(["generate-conclusions", "--root", str(root)]) == 0
    capsys.readouterr()

    code, _payload = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/c.md",
            "--title",
            "乙",
            "--status",
            "proposed",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    index = (root / "index.md").read_text(encoding="utf-8")
    assert index.index("- [乙](pages/c.md)") < index.index("- [结论汇总](conclusions.md)")


def test_status_set_changes_and_validates_final_state(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    (root / "pages/a.md").write_text(PAGE_A.replace("待讨论", "已确认"), encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", "--root", str(root), "pages/a.md", "proposed", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "status.set"
    assert payload["status"] == "proposed"
    assert "pages/a.md" in cast("list[str]", payload["changed_paths"])
    assert "当前提案" in (root / "pages/a.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


def test_status_set_on_crlf_page_succeeds(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """CRLF 页面：写回只修改状态跨度，未修改字节（含 \r\n）保持不变。"""
    root = build_page_draft(draft_factory)
    page = root / "pages/a.md"
    text = page.read_text(encoding="utf-8")
    page.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    crlf_before = (root / "pages/a.md").read_bytes().count(b"\r\n")
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", "--root", str(root), "pages/a.md", "proposed", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "status.set"
    after_bytes = (root / "pages/a.md").read_bytes()
    # 未修改区域的换行与字节保持不变，只有状态跨度被替换。
    assert after_bytes.count(b"\r\n") == crlf_before
    assert "当前提案".encode() in after_bytes
    assert check_draft(root).errors == ()


def test_relation_add_on_crlf_page_preserves_unmodified_bytes(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """CRLF 页面：relation add 只插入新行，未修改行的换行保持不变。"""
    root = build_page_draft(draft_factory)
    page = root / "pages/a.md"
    page.write_bytes(page.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    assert b"\r\n" in page.read_bytes()
    capsys.readouterr()

    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "depends_on",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert payload["result_type"] == "relation.add"
    after_bytes = (root / "pages/a.md").read_bytes()
    # 未修改的标题行仍为 CRLF（read_bytes 不规范化换行）。
    assert after_bytes.split(b"\n")[0].endswith(b"\r")
    assert "依赖".encode() in after_bytes
    assert check_draft(root).errors == ()


def test_status_set_rejects_invalid_final_state(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["status", "set", "--root", str(root), "pages/a.md", "superseded", "--json"], capsys
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
        [
            "status",
            "set",
            "--root",
            str(root),
            "pages/a.md",
            "proposed",
            "--note",
            "新方案",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert "当前提案：新方案" in (root / "pages/a.md").read_text(encoding="utf-8")

    code, _payload = run_json(
        ["status", "set", "--root", str(root), "pages/a.md", "pending", "--clear-note", "--json"],
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
        ["status", "set", "--root", str(root), "pages/missing.md", "pending", "--json"], capsys
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
            "--root",
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


def test_relation_add_depends_on_is_single_sided_and_idempotent(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "depends_on",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert payload["result_type"] == "relation.add"
    assert "pages/a.md" in cast("list[str]", payload["changed_paths"])
    assert "依赖" in (root / "pages/a.md").read_text(encoding="utf-8")
    # b.md 不出现依赖反向。
    assert "被" not in (root / "pages/b.md").read_text(encoding="utf-8")

    # 幂等：再次添加无变化。
    code, payload2 = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "depends_on",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert cast("list[str]", payload2["changed_paths"]) == []
    assert check_draft(root).errors == ()


def test_relation_add_supersedes_is_bidirectional_and_sets_status(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "supersedes",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert set(cast("list[str]", payload["changed_paths"])) == {
        "pages/a.md",
        "pages/b.md",
    }
    assert "取代" in (root / "pages/a.md").read_text(encoding="utf-8")
    b_text = (root / "pages/b.md").read_text(encoding="utf-8")
    assert "被取代" in b_text
    assert "已取代" in b_text
    assert check_draft(root).errors == ()


def test_relation_remove_supersedes_requires_status_for_last(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()
    assert (
        main(["relation", "add", "--root", str(root), "pages/a.md", "supersedes", "pages/b.md"])
        == 0
    )
    capsys.readouterr()

    # 无 --status 删除最后一条取代关系应失败。
    code, payload = run_json(
        [
            "relation",
            "remove",
            "--root",
            str(root),
            "pages/a.md",
            "supersedes",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "relation.restored_status_required"

    # 提供 --status 则恢复目标状态。
    code, payload = run_json(
        [
            "relation",
            "remove",
            "--root",
            str(root),
            "pages/a.md",
            "supersedes",
            "pages/b.md",
            "--status",
            "proposed",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    b_text = (root / "pages/b.md").read_text(encoding="utf-8")
    assert "当前提案" in b_text
    assert "被取代" not in b_text
    assert "取代" not in (root / "pages/a.md").read_text(encoding="utf-8")
    assert check_draft(root).errors == ()


def test_relation_remove_nonexistent_is_no_op(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "relation",
            "remove",
            "--root",
            str(root),
            "pages/a.md",
            "challenges",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )

    assert code == 0
    assert cast("list[str]", payload["changed_paths"]) == []
    assert check_draft(root).errors == ()


def test_relation_self_reference_is_rejected(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "depends_on",
            "pages/a.md",
            "--json",
        ],
        capsys,
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "relation.self_reference"


def test_relation_add_cycle_is_rejected_before_write(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """已有 a 依赖 b 时，添加 b 依赖 a 必须被规划阶段拒绝，不落盘也不进入写锁验证。"""
    root = build_page_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "depends_on",
            "pages/b.md",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    a_before = (root / "pages/a.md").read_text(encoding="utf-8")

    # 预演也必须拒绝：规划边界与正式执行一致。
    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/b.md",
            "depends_on",
            "pages/a.md",
            "--dry-run",
            "--json",
        ],
        capsys,
    )
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "relation.dependency_cycle"

    # 正式执行同样拒绝，且 b.md 未被修改。
    code, payload = run_json(
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/b.md",
            "depends_on",
            "pages/a.md",
            "--json",
        ],
        capsys,
    )
    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "relation.dependency_cycle"
    assert (root / "pages/b.md").read_text(encoding="utf-8") == PAGE_B
    assert (root / "pages/a.md").read_text(encoding="utf-8") == a_before
