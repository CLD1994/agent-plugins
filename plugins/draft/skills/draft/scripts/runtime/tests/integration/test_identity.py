from __future__ import annotations

import ast
import importlib.util
import tomllib
from pathlib import Path

from tests.support.paths import RUNTIME_ROOT


def test_runtime_uses_draft_identity() -> None:
    assert importlib.util.find_spec("draft") is not None
    config = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "draft-runtime"
    assert config["project"]["scripts"] == {"draft": "draft.cli:main"}
    assert config["tool"]["uv"]["package"] is True


def test_version_comes_from_installed_metadata() -> None:
    import draft

    config = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert draft.__version__ == config["project"]["version"]


def test_root_domain_exports_use_draft() -> None:
    from draft import checker, errors, graph, lifecycle, paths, trash_lifecycle

    assert hasattr(errors, "DraftError")
    assert hasattr(graph, "DraftGraph")
    assert hasattr(checker, "check_draft")
    assert hasattr(trash_lifecycle, "recover_draft")
    assert hasattr(paths, "resolve_draft_root")
    assert hasattr(lifecycle, "initialize")


def test_core_runtime_owns_no_plugin_responsibilities() -> None:
    import draft.models

    assert not hasattr(draft.models, "Binding")

    # 架构依赖边界：只拒绝核心源码对插件模块的 import。基于 AST 判断，
    # 字符串、注释与错误消息不参与，避免把文档文案变成变更探测器。
    plugin_modules = {"adapter", "bridge", "bindings", "session_hook", "plugin_context", "entry"}
    src_root = RUNTIME_ROOT / "src" / "draft"
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".")[0]
                    assert root_name not in plugin_modules, (
                        f"{path} imports plugin module {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                root_name = node.module.split(".")[0]
                assert root_name not in plugin_modules, (
                    f"{path} imports from plugin module {node.module}"
                )


def test_initialize_writes_only_draft_markers(tmp_path: Path) -> None:
    from draft.lifecycle import initialize
    from tests.integration.support import managed_marker_text

    root = tmp_path / "draft"
    initialize(root)

    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == managed_marker_text()
    assert not (root / ".draft/draft.provisional.json").exists()
