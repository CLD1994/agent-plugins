import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from draft.cli import main
from draft.diagnostic_codes import CheckCode, QueryCode
from draft.errors import DraftError
from draft.models import RootState
from tests.integration.support import DraftFactory

QUERY_INDEX = """# Topic

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
- [乙](pages/b.md)
- [结论汇总](conclusions.md)
"""

QUERY_CONCLUSIONS = """# 结论汇总

> 状态：`已确认`
>
> 父主题：[根索引](index.md)

## 已确认结论
"""

QUERY_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

见 [乙](b.md) 与 [图](../asset.png)。
"""

QUERY_B = """# 乙

> 状态：`当前提案`
>
> 父主题：[根索引](../index.md)
>
> 依赖：[甲](a.md)

提到 [甲](a.md)。
"""


def build_query_draft(draft_factory: DraftFactory) -> Path:
    root = draft_factory(
        {
            "index.md": QUERY_INDEX,
            "conclusions.md": QUERY_CONCLUSIONS,
            "pages/a.md": QUERY_A,
            "pages/b.md": QUERY_B,
        }
    )
    (root / "asset.png").write_bytes(b"png")
    return root


def run_json(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object]]:
    code = main(args)
    output = capsys.readouterr().out
    return code, cast("dict[str, object]", json.loads(output))


def _broken_query_root(draft_factory: DraftFactory, *, root_state: RootState) -> Path:
    files = {
        "index.md": QUERY_INDEX,
        "conclusions.md": QUERY_CONCLUSIONS,
        "pages/a.md": QUERY_A,
        "pages/b.md": QUERY_B,
    }
    root = draft_factory(files, root_state=root_state)
    (root / "asset.png").write_bytes(b"png")
    # 一个无法解析的页面：无标题 → 阻碍页面。
    (root / "pages/broken.md").write_text("无标题正文。\n", encoding="utf-8")
    return root


def test_query_backlinks_candidate_partial_returns_complete_false(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _broken_query_root(draft_factory, root_state=RootState.CANDIDATE)
    capsys.readouterr()

    code, payload = run_json(
        ["query", "backlinks", "--root", str(root), "pages/a.md", "--json"], capsys
    )

    assert code == 0
    assert payload["complete"] is False
    assert payload["blocking_paths"] == ["pages/broken.md"]
    assert payload["blocking_total"] == 1
    assert payload["blocking_has_more"] is False


def test_query_status_candidate_partial_returns_complete_false(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _broken_query_root(draft_factory, root_state=RootState.CANDIDATE)
    capsys.readouterr()

    code, payload = run_json(["query", "status", "--root", str(root), "pending", "--json"], capsys)

    assert code == 0
    assert payload["complete"] is False
    assert "pages/broken.md" in cast("list[str]", payload["blocking_paths"])


def test_query_page_candidate_incomplete_when_counts_unreliable(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _broken_query_root(draft_factory, root_state=RootState.CANDIDATE)
    capsys.readouterr()

    code, payload = run_json(["query", "page", "--root", str(root), "pages/a.md", "--json"], capsys)

    assert code == 2
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == QueryCode.INCOMPLETE.value
    assert error["details"] == ["1", "pages/broken.md"]


def test_query_page_succeeds_on_clean_candidate_with_complete_true(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = draft_factory(
        {
            "index.md": QUERY_INDEX,
            "conclusions.md": QUERY_CONCLUSIONS,
            "pages/a.md": QUERY_A,
            "pages/b.md": QUERY_B,
        },
        root_state=RootState.CANDIDATE,
    )
    capsys.readouterr()

    code, payload = run_json(["query", "page", "--root", str(root), "pages/a.md", "--json"], capsys)

    assert code == 0
    assert payload["complete"] is True
    assert payload["path"] == "pages/a.md"


def test_query_backlinks_registered_corrupt_fails_with_specific_code(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _broken_query_root(draft_factory, root_state=RootState.REGISTERED)
    capsys.readouterr()

    code, payload = run_json(
        ["query", "backlinks", "--root", str(root), "pages/a.md", "--json"], capsys
    )

    assert code == 2
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    # 传播最具体的损坏：broken.md 无法解析，而不是伪造部分结果。
    assert error["code"] == CheckCode.PAGE_MISSING_TITLE.value
    assert error["paths"] == ["pages/broken.md"]


def test_query_page_returns_routing_summary(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(["query", "page", "--root", str(root), "pages/a.md", "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "query.page"
    assert payload["path"] == "pages/a.md"
    assert payload["status"] == "pending"
    assert payload["parent"] == "index.md"
    assert payload["link_count"] == 2
    # 规格 §11：成功的 query page 必须显式返回 complete=true 与 blocking_paths=[]。
    assert payload["complete"] is True
    assert payload["blocking_paths"] == []


def test_query_links_returns_deduplicated_resources(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["query", "links", "--root", str(root), "pages/a.md", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "query.links"
    assert payload["total"] == 2
    items = payload["items"]
    assert isinstance(items, list)
    first = cast("dict[str, object]", items[0])
    assert first["target"] == "pages/b.md"
    assert first["kind"] == "page"
    assert first["target_state"] == "present"


def test_query_succeeds_with_missing_body_link_resource_fact(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """正文链接目标缺失是查询结果数据：query page 与 links 必须成功返回资源事实。"""
    root = build_query_draft(draft_factory)
    (root / "pages/a.md").write_text(
        (root / "pages/a.md").read_text(encoding="utf-8") + "\n见 [断链](missing.md)。\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["query", "page", "--root", str(root), "pages/a.md", "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "query.page"

    code, payload = run_json(
        ["query", "links", "--root", str(root), "pages/a.md", "--json"], capsys
    )

    assert code == 0
    items = cast("list[dict[str, object]]", payload["items"])
    missing = next(item for item in items if item["target"] == "pages/missing.md")
    assert missing["target_state"] == "missing"
    assert missing["kind"] == "page"


def test_query_page_missing_page_is_page_not_found_exit_two(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["query", "page", "--root", str(root), "pages/missing.md", "--json"], capsys
    )

    assert code == 2
    assert payload["result_type"] == "error"
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "page.not_found"


def test_query_backlinks_resolves_root_relative_target(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["query", "backlinks", "--root", str(root), "pages/a.md", "--json"], capsys
    )

    assert code == 0
    assert payload["result_type"] == "query.backlinks"
    items = cast("list[dict[str, object]]", payload["items"])
    assert [item["page"] for item in items] == [
        "index.md",
        "pages/b.md",
    ]


def test_query_relations_outgoing_and_incoming(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, out = run_json(
        [
            "query",
            "relations",
            "--root",
            str(root),
            "pages/b.md",
            "--direction",
            "outgoing",
            "--json",
        ],
        capsys,
    )
    assert code == 0
    assert out["total"] == 2
    _code, inc = run_json(
        [
            "query",
            "relations",
            "--root",
            str(root),
            "pages/a.md",
            "--direction",
            "incoming",
            "--json",
        ],
        capsys,
    )
    assert inc["total"] == 1
    items = cast("list[dict[str, object]]", inc["items"])
    assert items[0]["relation"] == "depends_on"
    assert items[0]["page"] == "pages/b.md"


def test_query_status_filters_by_stable_code(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(["query", "status", "--root", str(root), "pending", "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "query.status"
    items = cast("list[dict[str, object]]", payload["items"])
    assert [item["page"] for item in items] == ["pages/a.md"]


def test_query_count_only_shares_pagination_metadata(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, normal = run_json(["query", "links", "--root", str(root), "pages/a.md", "--json"], capsys)
    assert code == 0
    _code, counted = run_json(
        ["query", "links", "--root", str(root), "pages/a.md", "--count-only", "--json"], capsys
    )

    assert counted["items"] == []
    assert counted["total"] == normal["total"]
    assert counted["offset"] == normal["offset"]
    assert counted["limit"] == normal["limit"]
    assert counted["has_more"] == normal["has_more"]
    assert counted["next_offset"] == normal["next_offset"]


def test_query_recoverable_lists_only_in_window(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    from draft.trash import trash_page

    # 回收一个没有入站引用的页面（asset.png 不是页面；新建孤儿页）。
    (root / "pages/orphan.md").write_text(
        "# 孤儿\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n孤立。\n",
        encoding="utf-8",
    )
    (root / "index.md").write_text(
        (root / "index.md")
        .read_text(encoding="utf-8")
        .replace(
            "- [结论汇总](conclusions.md)\n",
            "- [孤儿](pages/orphan.md)\n- [结论汇总](conclusions.md)\n",
        ),
        encoding="utf-8",
    )
    retained = trash_page(root, Path("pages/orphan.md"), now=datetime(2026, 8, 20, tzinfo=UTC))
    capsys.readouterr()

    code, payload = run_json(["query", "recoverable", "--root", str(root), "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "query.recoverable"
    assert "as_of" in payload
    items = cast("list[dict[str, object]]", payload["items"])
    assert [item["operation_id"] for item in items] == [retained.operation_id]


def test_query_invalid_pagination_is_exit_two(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        ["query", "links", "--root", str(root), "pages/a.md", "--limit", "101", "--json"], capsys
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "query.invalid_pagination"


def test_query_inbound_relations_require_existing_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """入站关系查询的目标必须存在，不存在时返回 page.not_found 而不是空成功。"""
    root = build_query_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(
        [
            "query",
            "relations",
            "--root",
            str(root),
            "pages/nope.md",
            "--direction",
            "incoming",
            "--json",
        ],
        capsys,
    )

    assert code == 2
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "page.not_found"


# ---------------------------------------------------------------------------
# Task 9：query 的机会式索引维护与缓存状态等价性（spec §14）
# ---------------------------------------------------------------------------


def _index_state(root: Path) -> tuple[dict[str, bytes] | None, bytes | None, bool]:
    """记录 .draft/operations、索引制品与 flag 摘要（spec §14 无副作用判定）。"""
    operations = root / ".draft" / "operations"
    ops = None
    if operations.is_dir():
        ops = {
            path.relative_to(operations).as_posix(): path.read_bytes()
            for path in sorted(operations.rglob("*"))
            if path.is_file()
        }
    index = root / ".draft" / "link-index.json"
    index_bytes = index.read_bytes() if index.is_file() else None
    flag = (root / ".draft" / "link-index-rebuild-required.flag").exists()
    return (ops, index_bytes, flag)


def test_query_backlinks_equivalence_across_index_states(
    draft_factory: DraftFactory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一根在 fresh/absent/stale/rebuild-required 状态返回相同结果（spec §14/§19）。"""
    root = build_query_draft(draft_factory)
    capsys.readouterr()
    args = ["query", "backlinks", "--root", str(root), "pages/a.md", "--json"]
    expected = ["index.md", "pages/b.md"]

    code, absent = run_json(args, capsys)
    assert code == 0
    items = cast("list[dict[str, object]]", absent["items"])
    assert [item["page"] for item in items] == expected
    assert "warnings" not in absent

    code, fresh = run_json(args, capsys)
    assert code == 0
    assert fresh == absent

    # stale：修改正文后查询发布下一 revision，结果仍与完整事实计算相同。
    (root / "pages" / "b.md").write_text(
        (root / "pages" / "b.md").read_text(encoding="utf-8") + "再看 [甲](a.md)。\n",
        encoding="utf-8",
    )
    code, stale = run_json(args, capsys)
    assert code == 0
    assert stale == absent

    # rebuild_required：扫描失败时查询回退图内完整事实并返回一次降级 warning。
    import draft.link_index_maintenance as maintenance

    def fail_scan(root: Path, baseline: object, *, known_documents: object = ()) -> object:
        raise DraftError("operation.index_update_failed", root)

    monkeypatch.setattr(maintenance, "scan_link_index", fail_scan)
    code, rebuild = run_json(args, capsys)
    monkeypatch.undo()
    assert code == 0
    items = cast("list[dict[str, object]]", rebuild["items"])
    assert [item["page"] for item in items] == expected
    assert rebuild["warnings"] == [
        {"code": "link_index.rebuild_required", "severity": "warning"}
    ]

    # 恢复扫描后（rebuild_required 状态）查询发布并返回相同结果。
    code, recovered = run_json(args, capsys)
    assert code == 0
    assert recovered == absent


def test_query_page_backlink_count_equivalence_across_index_states(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """query page 的 backlink count 与 query backlinks 使用同一严格事实（spec §20）。"""
    root = build_query_draft(draft_factory)
    capsys.readouterr()
    args = ["query", "page", "--root", str(root), "pages/a.md", "--json"]

    code, absent = run_json(args, capsys)
    assert code == 0
    assert absent["backlink_count"] == 2  # index.md 导航 + b.md 正文
    assert "warnings" not in absent

    code, fresh = run_json(args, capsys)
    assert code == 0
    assert fresh == absent


def test_query_backlinks_journal_durability_failure_is_nonzero(
    draft_factory: DraftFactory,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """journal 耐久失败：普通 query 非零退出（spec §14）。"""
    import draft.link_index_maintenance as maintenance

    def fail_write(journal: object) -> None:
        raise DraftError(CheckCode.OPERATION_DURABILITY_FAILED.value, root / ".draft")

    root = build_query_draft(draft_factory)
    capsys.readouterr()
    monkeypatch.setattr(maintenance, "write_journal", fail_write)

    code, payload = run_json(
        ["query", "backlinks", "--root", str(root), "pages/a.md", "--json"], capsys
    )

    monkeypatch.undo()
    assert code == 3
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == CheckCode.OPERATION_DURABILITY_FAILED.value


def test_query_links_and_unrelated_queries_do_not_touch_index(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """query links 与不消费链接索引的查询不触发维护（spec §14/§22）。"""
    root = build_query_draft(draft_factory)
    capsys.readouterr()
    before = _index_state(root)

    assert run_json(["query", "links", "--root", str(root), "pages/a.md", "--json"], capsys)[0] == 0
    assert (
        run_json(
            [
                "query",
                "relations",
                "--root",
                str(root),
                "pages/b.md",
                "--direction",
                "outgoing",
                "--json",
            ],
            capsys,
        )[0]
        == 0
    )
    assert (
        run_json(
            [
                "query",
                "relations",
                "--root",
                str(root),
                "pages/a.md",
                "--direction",
                "incoming",
                "--json",
            ],
            capsys,
        )[0]
        == 0
    )
    assert run_json(["query", "status", "--root", str(root), "pending", "--json"], capsys)[0] == 0
    assert run_json(["query", "recoverable", "--root", str(root), "--json"], capsys)[0] == 0

    assert _index_state(root) == before


def test_dry_run_commands_leave_no_index_side_effects(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """dry-run 不创建 journal、索引或 flag（spec §14/§23）。"""
    root = build_query_draft(draft_factory)
    # 提供无入站正文引用的隔离页供 trash 预演。
    (root / "pages" / "isolated.md").write_text(
        "# 隔离\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\n正文。\n",
        encoding="utf-8",
    )
    (root / "index.md").write_text(
        (root / "index.md")
        .read_text(encoding="utf-8")
        .replace(
            "- [结论汇总](conclusions.md)\n",
            "- [隔离](pages/isolated.md)\n- [结论汇总](conclusions.md)\n",
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    before = _index_state(root)

    commands = [
        ["move", "--root", str(root), "pages/a.md", "pages/z.md", "--dry-run"],
        [
            "create",
            "--root",
            str(root),
            "pages/new.md",
            "--title",
            "新",
            "--status",
            "pending",
            "--dry-run",
        ],
        ["status", "set", "--root", str(root), "pages/a.md", "confirmed", "--dry-run"],
        [
            "relation",
            "add",
            "--root",
            str(root),
            "pages/a.md",
            "depends_on",
            "index.md",
            "--dry-run",
        ],
        ["promote", "--root", str(root), "pages/a.md", "pages/b.md", "--dry-run"],
        ["trash", "--root", str(root), "pages/isolated.md", "--dry-run"],
        ["generate-conclusions", "--root", str(root), "--dry-run"],
    ]
    for args in commands:
        code, _payload = run_json([*args, "--json"], capsys)
        assert code == 0, f"dry-run 失败: {args}"

    assert _index_state(root) == before


def test_query_status_invalid_code_is_usage_error(
    draft_factory: DraftFactory,
) -> None:
    """非法状态值由 argparse 拒绝，不泄漏 ValueError。"""
    root = build_query_draft(draft_factory)
    with pytest.raises(SystemExit) as caught:
        main(["query", "status", "--root", str(root), "bogus"])
    assert caught.value.code == 2


def test_query_missing_subcommand_is_usage_error() -> None:
    """draft query 缺少二级命令时是用法错误，不触发 AttributeError。"""
    with pytest.raises(SystemExit) as caught:
        main(["query"])
    assert caught.value.code == 2


# ---------------------------------------------------------------------------
# 第十八轮 M1/A1：not_generated 下根级遗留 conclusions.md 的查询语义
# ---------------------------------------------------------------------------

LEGACY_UNPARSEABLE = """# 旧笔记

> 状态：`已迁移为正式设计，等待评审`
>
> 父主题：[根索引](index.md)

旧笔记正文。
"""

LEGACY_PARSEABLE = """# 旧笔记

> 状态：`待讨论`
>
> 父主题：[根索引](index.md)

旧笔记正文。
"""

LEGACY_QUERY_INDEX = """# Theme

> 状态：`已关闭：纯导航页，不表达独立结论`

## 子主题

- [甲](pages/a.md)
"""

LEGACY_QUERY_A = """# 甲

> 状态：`待讨论`
>
> 父主题：[根索引](../index.md)

甲的正文。
"""


def _legacy_query_root(
    draft_factory: DraftFactory, *, root_state: RootState, legacy: str = LEGACY_UNPARSEABLE
) -> Path:
    root = draft_factory(
        {
            "index.md": LEGACY_QUERY_INDEX,
            "pages/a.md": LEGACY_QUERY_A,
        },
        root_state=root_state,
    )
    (root / "conclusions.md").write_text(legacy, encoding="utf-8")
    return root


def test_query_status_candidate_legacy_conclusions_does_not_block(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """candidate × not_generated + 遗留 conclusions.md：集合查询 complete=true 且
    blocking_paths 不含该文件（round-18 M1/A1）。"""
    root = _legacy_query_root(draft_factory, root_state=RootState.CANDIDATE)
    capsys.readouterr()

    code, payload = run_json(["query", "status", "--root", str(root), "pending", "--json"], capsys)

    assert code == 0
    assert payload["complete"] is True
    assert payload["blocking_paths"] == []


def test_query_status_registered_legacy_conclusions_no_longer_blocks(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """registered × not_generated + 遗留 conclusions.md：查询门禁不再因该文件拒绝。

    该文件被 A1 重分类为未受管后，registered 查询从"拒绝并传播最具体损坏"
    （page.invalid_status）变为成功——已登记的行为变化（round-18 M1/A1）。
    """
    root = _legacy_query_root(draft_factory, root_state=RootState.REGISTERED)
    capsys.readouterr()

    code, payload = run_json(["query", "status", "--root", str(root), "pending", "--json"], capsys)

    assert code == 0
    assert payload["complete"] is True
    assert payload["blocking_paths"] == []


def test_query_page_legacy_conclusions_is_page_not_found(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """即使遗留内容可完整解析，not_generated 下 conclusions.md 也不再是受管页：
    query page 报 page.not_found（A1 重分类的一致后果）。"""
    root = _legacy_query_root(
        draft_factory, root_state=RootState.CANDIDATE, legacy=LEGACY_PARSEABLE
    )
    capsys.readouterr()

    code, payload = run_json(
        ["query", "page", "--root", str(root), "conclusions.md", "--json"], capsys
    )

    assert code == 2
    assert payload["ok"] is False
    error = payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "page.not_found"
