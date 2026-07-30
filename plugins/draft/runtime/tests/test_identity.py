from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_uses_draft_identity() -> None:
    assert importlib.util.find_spec("draft") is not None
    config = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "draft-runtime"
    assert config["project"]["scripts"] == {
        "draft": "draft.cli:main",
        "draft-hook-claude": "draft.claude_hook:main",
    }


def test_root_domain_exports_use_draft() -> None:
    from draft import checker, errors, graph, lifecycle, paths, trash_lifecycle

    assert hasattr(errors, "DraftError")
    assert hasattr(graph, "DraftGraph")
    assert hasattr(checker, "check_draft")
    assert hasattr(trash_lifecycle, "recover_draft")
    assert hasattr(paths, "resolve_draft_root")
    assert hasattr(lifecycle, "draft_manifest")


def test_initialize_writes_only_draft_markers(tmp_path: Path) -> None:
    from draft.lifecycle import initialize

    root = tmp_path / "draft"
    initialize(root)

    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == ('{"format_version":1}\n')
    assert not (root / ".draft/draft.provisional.json").exists()
