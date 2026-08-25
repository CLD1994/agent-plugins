import json
from pathlib import Path
from typing import NoReturn, cast

import pytest

from draft.checker import check_draft
from draft.cli import main
from draft.diagnostic_codes import CheckCode
from draft.errors import DraftError
from draft.graph import read_marker
from draft.operations import Journal
from draft.vocabulary import ConclusionsState
from tests.integration.support import DraftFactory

CONC_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
- [乙](pages/b.md)
"""

CONC_A = """# 甲

> 状态：`已确认`
>
> 父主题：[根索引](../index.md)

甲。。
"""

CONC_B = """# 乙

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

乙。。
"""


def build_conc_draft(draft_factory: DraftFactory) -> Path:
    return draft_factory(
        {
            "index.md": CONC_INDEX,
            "pages/a.md": CONC_A,
            "pages/b.md": CONC_B,
        }
    )


def run_json(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(args)
    output = capsys.readouterr().out
    return code, cast("dict[str, object]", json.loads(output))


def test_generate_conclusions_creates_page_and_advances_owner(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "generate-conclusions"
    assert payload["generated"] is True
    assert "warnings" not in payload
    changed = cast("list[str]", payload["changed_paths"])
    assert "conclusions.md" in changed
    assert "index.md" in changed
    assert all(not p.startswith(".draft/") for p in changed)

    conclusions = (root / "conclusions.md").read_text(encoding="utf-8")
    assert "# 结论汇总" in conclusions
    assert "当前提案" in conclusions
    assert "- [甲](pages/a.md)" in conclusions
    assert read_marker(root).conclusions_state is ConclusionsState.GENERATED
    # 生成后图结构干净。
    assert check_draft(root).errors == ()


def test_generate_conclusions_idempotent_second_run(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    capsys.readouterr()
    assert main(["generate-conclusions", "--root", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    assert cast("list[str]", payload["changed_paths"]) == []
    assert "warnings" not in payload


def test_generate_conclusions_no_confirmed_is_no_op(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    (root / "pages/a.md").write_text(
        (root / "pages/a.md").read_text(encoding="utf-8").replace("已确认", "待讨论"),
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is False
    assert payload["reason"] == "no_confirmed_pages"
    assert cast("list[str]", payload["changed_paths"]) == []
    assert "warnings" not in payload
    assert not (root / "conclusions.md").exists()
    assert read_marker(root).conclusions_state is ConclusionsState.NOT_GENERATED


def test_generate_conclusions_cli_projects_publish_failure_warning(
    draft_factory: DraftFactory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实 CLI 入口在索引发布降级时保留业务结果并投影统一 warning。"""
    import draft.link_index_maintenance as maintenance

    def fail_publish(root: Path, journal: Journal, expected_sha256: str) -> NoReturn:
        del journal, expected_sha256
        raise DraftError("operation.index_update_failed", root)

    root = build_conc_draft(draft_factory)
    monkeypatch.setattr(maintenance, "publish_staged_link_index", fail_publish)
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    assert payload["warnings"] == [{"code": "link_index.rebuild_required", "severity": "warning"}]
    assert (root / "conclusions.md").is_file()
    assert (root / ".draft/link-index-rebuild-required.flag").is_file()
    journal_paths = tuple((root / ".draft/operations").glob("*/journal.json"))
    assert len(journal_paths) == 1
    journal_payload = json.loads(journal_paths[0].read_text(encoding="utf-8"))
    assert journal_payload["phase"] == "completed"
    assert journal_payload["link_index_result"] == "rebuild_required"
    assert "link_index_sha256" not in journal_payload


def test_generate_conclusions_dry_run_does_not_write(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["generate-conclusions", "--root", str(root), "--dry-run", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "generate-conclusions"
    effects = cast("list[dict[str, object]]", payload["effects"])
    assert any(e.get("path") == "conclusions.md" for e in effects)
    assert "changed_paths" not in payload
    assert not (root / "conclusions.md").exists()
    assert read_marker(root).conclusions_state is ConclusionsState.NOT_GENERATED


def test_generate_conclusions_dry_run_no_confirmed_is_empty_effects(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """无已确认页面时预演成功，返回空 effects 与业务事实，而不是报错。"""
    root = build_conc_draft(draft_factory)
    (root / "pages/a.md").write_text(
        (root / "pages/a.md").read_text(encoding="utf-8").replace("已确认", "待讨论"),
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(
        ["generate-conclusions", "--root", str(root), "--dry-run", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "generate-conclusions"
    assert payload["generated"] is False
    assert payload["reason"] == "no_confirmed_pages"
    assert cast("list[dict[str, object]]", payload["effects"]) == []
    assert not (root / "conclusions.md").exists()


def test_generate_conclusions_dry_run_idempotent_is_empty_effects(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """已生成且无变化时预演成功，返回空 effects，而不是报错。"""
    root = build_conc_draft(draft_factory)
    assert main(["generate-conclusions", "--root", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(
        ["generate-conclusions", "--root", str(root), "--dry-run", "--json"], capsys
    )

    assert code == 0
    assert payload["generated"] is True
    assert cast("list[dict[str, object]]", payload["effects"]) == []


def test_generate_conclusions_dry_run_matches_real_on_damaged_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """已生成态损坏结论页时，预演与正式执行调用同一计划构造边界（规格 §13）。"""
    root = build_conc_draft(draft_factory)
    assert main(["generate-conclusions", "--root", str(root)]) == 0
    (root / "conclusions.md").write_text("损坏\n", encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(
        ["generate-conclusions", "--root", str(root), "--dry-run", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "generate-conclusions"
    effects = cast("list[dict[str, object]]", payload["effects"])
    assert any(e.get("path") == "conclusions.md" for e in effects)
    assert "changed_paths" not in payload
    # 预演不执行写入：损坏文件保持原样。
    assert (root / "conclusions.md").read_text(encoding="utf-8") == "损坏\n"


def test_generate_conclusions_handles_bracket_titles(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """标题含 ``[``/``]`` 必须能由生成器表示，生成条目的链接标签还原为原始标题。"""
    root = build_conc_draft(draft_factory)
    capsys.readouterr()
    code, _ = run_json(
        [
            "create",
            "--root",
            str(root),
            "pages/br.md",
            "--title",
            "甲]乙",
            "--status",
            "proposed",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    code, _ = run_json(
        ["status", "set", "--root", str(root), "pages/br.md", "confirmed", "--json"], capsys
    )
    assert code == 0

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    conclusions = (root / "conclusions.md").read_text(encoding="utf-8")
    assert "- [甲\\]乙](pages/br.md)" in conclusions
    from draft.markdown import parse_page

    document = parse_page(Path("conclusions.md"), conclusions)
    link = next(link for link in document.links if "pages/br.md" in link.destination)
    assert link.label == "甲]乙"
    assert check_draft(root).errors == ()


def test_generate_conclusions_repairs_noncanonical_nav_label(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """生成器必须修复根导航结论链接的规范标签与字面目标，不能只按目标等价放行。"""
    root = draft_factory(
        {
            "index.md": (
                "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
                "- [错误名字](./conclusions.md)\n- [甲](pages/a.md)\n"
            ),
            "conclusions.md": (
                "# 结论汇总\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](index.md)\n\n"
                "## 已确认结论\n\n- [甲](pages/a.md)\n"
            ),
            "pages/a.md": "# 甲\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\n甲。\n",
        }
    )
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    index = (root / "index.md").read_text(encoding="utf-8")
    assert "- [结论汇总](conclusions.md)" in index
    assert "- [错误名字]" not in index
    assert check_draft(root).errors == ()


def test_generate_conclusions_rebuilds_damaged_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    assert main(["generate-conclusions", "--root", str(root)]) == 0
    # 破坏结论页并漂移根导航链接。
    (root / "conclusions.md").write_text("损坏\n", encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    assert "conclusions.md" in cast("list[str]", payload["changed_paths"])
    assert check_draft(root).errors == ()


def test_generate_conclusions_follows_navigation_declaration_order(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """结论条目按根导航声明顺序深度优先输出，不按路径排序。"""
    root = draft_factory(
        {
            "index.md": (
                "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n"
                "- [乙](pages/b.md)\n- [甲](pages/a.md)\n"
            ),
            "pages/a.md": "# 甲\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\n甲。\n",
            "pages/b.md": "# 乙\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\n乙。\n",
        }
    )
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    conclusions = (root / "conclusions.md").read_text(encoding="utf-8")
    items = [line for line in conclusions.splitlines() if line.startswith("- [")]
    assert items == ["- [乙](pages/b.md)", "- [甲](pages/a.md)"], items


def test_generate_conclusions_fails_on_invalid_navigation(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """声明了父关系但导航遍历无法到达的页面使生成失败，不退回路径排序。"""
    root = draft_factory(
        {
            "index.md": "# Topic\n\n> 状态：`已关闭：纯导航页，不表达独立结论`\n\n## 子主题\n\n",
            "pages/a.md": "# 甲\n\n> 状态：`已确认`\n>\n> 父主题：[根索引](../index.md)\n\n甲。\n",
        }
    )
    (root / "pages/b.md").write_text(
        "# 乙\n\n> 状态：`已确认`\n>\n> 父主题：[缺失](../missing.md)\n\n乙。\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "conclusions.invalid_navigation"


def test_generate_conclusions_still_rejects_legacy_file_when_not_generated(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """not_generated + 遗留 conclusions.md：生成守卫的拒绝语义保持不变（round-18 M1/A1）。

    A1 后图成员条件（``CONCLUSIONS_PAGE in graph.pages``）不再命中，但物理文件
    存在条件仍命中：generate-conclusions 必须继续报 conclusions.unexpected_file。
    """
    root = build_conc_draft(draft_factory)  # candidate 默认；夹具无 conclusions.md → not_generated
    (root / "conclusions.md").write_text("# 遗留笔记\n\n旧笔记正文。\n", encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", "--root", str(root), "--json"], capsys)

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.CONCLUSIONS_UNEXPECTED_FILE.value
    # 拒绝时不写任何业务 mutation：conclusions.md 保持原样，marker 仍 not_generated。
    assert (root / "conclusions.md").read_text(encoding="utf-8") == "# 遗留笔记\n\n旧笔记正文。\n"
    assert read_marker(root).conclusions_state is ConclusionsState.NOT_GENERATED


def test_conclusions_page_queryable_as_managed_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    assert main(["generate-conclusions", "--root", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(
        ["query", "page", "--root", str(root), "conclusions.md", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "query.page"
    assert payload["parent"] == "index.md"
