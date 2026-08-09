from pathlib import Path

import pytest

from draft.lifecycle import initialize
from tests.integration.support import DraftFactory


@pytest.fixture
def draft_factory(tmp_path: Path) -> DraftFactory:
    def create(files: dict[str, str], *, registered: bool = True) -> Path:
        root = tmp_path / "topic"
        root.mkdir()
        for relative, text in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (root / "pages").mkdir(exist_ok=True)
        if registered:
            internal = root / ".draft"
            internal.mkdir()
            (internal / "operations").mkdir()
            (internal / "trash" / "outgoing").mkdir(parents=True)
            (internal / "draft.json").write_text(
                '{"format_version":1,"conclusions_state":"not_generated"}\n', encoding="utf-8"
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
