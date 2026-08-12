from pathlib import Path

import pytest

from draft.lifecycle import initialize
from draft.models import RootState
from tests.integration.support import DraftFactory, managed_marker_text


@pytest.fixture
def draft_factory(tmp_path: Path) -> DraftFactory:
    def create(
        files: dict[str, str],
        *,
        root_state: RootState | None = RootState.REGISTERED,
    ) -> Path:
        root = tmp_path / "topic"
        root.mkdir()
        for relative, text in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (root / "pages").mkdir(exist_ok=True)
        if root_state is not None:
            internal = root / ".draft"
            internal.mkdir()
            (internal / "operations").mkdir()
            (internal / "trash" / "outgoing").mkdir(parents=True)
            # conclusions.md 只属于机器所有权下的 generated 状态；夹具携带该文件时
            # 所有权状态必须同步为 generated（规格 §12.1/§12.2）。
            from draft.vocabulary import ConclusionsState

            state = (
                ConclusionsState.GENERATED
                if "conclusions.md" in files
                else ConclusionsState.NOT_GENERATED
            )
            (internal / "draft.json").write_text(
                managed_marker_text(root_state=root_state, conclusions_state=state),
                encoding="utf-8",
            )
        return root

    return create


@pytest.fixture
def registered_draft(tmp_path: Path) -> Path:
    root = tmp_path / "registered"
    initialize(root)
    return root


@pytest.fixture
def unresolvable_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an environment where Path.expanduser() cannot determine the home directory."""

    def raise_runtime_error(self: Path) -> Path:
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(Path, "expanduser", raise_runtime_error)
