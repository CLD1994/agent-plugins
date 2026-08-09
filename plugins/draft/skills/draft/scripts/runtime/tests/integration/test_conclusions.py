import json
from pathlib import Path
from typing import cast

import pytest

from draft.checker import check_draft
from draft.cli import main
from draft.graph import read_marker
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

    code, payload = run_json(["generate-conclusions", str(root), "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "generate-conclusions"
    assert payload["generated"] is True
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
    assert main(["generate-conclusions", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    assert cast("list[str]", payload["changed_paths"]) == []


def test_generate_conclusions_no_confirmed_is_no_op(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    (root / "pages/a.md").write_text(
        (root / "pages/a.md").read_text(encoding="utf-8").replace("已确认", "待讨论"),
        encoding="utf-8",
    )
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is False
    assert payload["reason"] == "no_confirmed_pages"
    assert cast("list[str]", payload["changed_paths"]) == []
    assert not (root / "conclusions.md").exists()
    assert read_marker(root).conclusions_state is ConclusionsState.NOT_GENERATED


def test_generate_conclusions_dry_run_does_not_write(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", str(root), "--dry-run", "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "plan"
    assert payload["operation"] == "generate-conclusions"
    assert not (root / "conclusions.md").exists()
    assert read_marker(root).conclusions_state is ConclusionsState.NOT_GENERATED


def test_generate_conclusions_rebuilds_damaged_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    assert main(["generate-conclusions", str(root)]) == 0
    # 破坏结论页并漂移根导航链接。
    (root / "conclusions.md").write_text("损坏\n", encoding="utf-8")
    capsys.readouterr()

    code, payload = run_json(["generate-conclusions", str(root), "--json"], capsys)

    assert code == 0
    assert payload["generated"] is True
    assert "conclusions.md" in cast("list[str]", payload["changed_paths"])
    assert check_draft(root).errors == ()


def test_conclusions_page_queryable_as_managed_page(
    draft_factory: DraftFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build_conc_draft(draft_factory)
    assert main(["generate-conclusions", str(root)]) == 0
    capsys.readouterr()

    code, payload = run_json(["query", "page", str(root), "conclusions.md", "--json"], capsys)

    assert code == 0
    assert payload["result_type"] == "query.page"
    assert payload["parent"] == "index.md"
