import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from draft.cli import main
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
