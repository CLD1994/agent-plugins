import json
from pathlib import Path

import pytest

from draft.bindings import (
    bind,
    binding_path,
    load_binding,
    required_skill_path,
    skill_digest,
    unbind,
)
from draft.errors import DraftError
from draft.lifecycle import initialize
from draft.models import Binding


@pytest.fixture
def draft(tmp_path: Path) -> Path:
    root = tmp_path / "topic"
    initialize(root)
    return root


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    return directory


def make_binding(root: Path, session: str = "abc123", hint: Path | None = None) -> Binding:
    return Binding(
        platform="claude",
        session_id=session,
        root=root,
        last_page_hint=hint,
        skill_name="draft",
        skill_digest="test-digest",
    )


def test_binding_path_hashes_session_id(data_dir: Path) -> None:
    import hashlib

    path = binding_path(data_dir, "claude", "abc123")
    assert path.parent == data_dir / "bindings" / "claude"
    assert path.name == f"{hashlib.sha256(b'abc123').hexdigest()}.json"
    assert "abc123" not in path.name


def test_bind_writes_typed_record(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))

    path = binding_path(data_dir, "claude", "abc123")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["session_id"] == "abc123"
    assert record["platform"] == "claude"
    assert record["root"] == str(draft.resolve())
    assert record["skill_name"] == "draft"
    assert record["skill_digest"] == "test-digest"
    assert record["last_page_hint"] is None


def test_bind_rejects_unknown_platform(data_dir: Path, draft: Path) -> None:
    binding = make_binding(draft)
    binding = Binding(
        platform="vscode",
        session_id=binding.session_id,
        root=binding.root,
        last_page_hint=None,
        skill_name=binding.skill_name,
        skill_digest=binding.skill_digest,
    )
    with pytest.raises(DraftError, match="binding.invalid_platform"):
        bind(data_dir, binding)


def test_bind_rejects_unregistered_root(data_dir: Path, tmp_path: Path) -> None:
    wild = tmp_path / "wild"
    wild.mkdir()
    with pytest.raises(DraftError, match="binding.invalid_root"):
        bind(data_dir, make_binding(wild))


def test_last_page_hint_must_be_managed_page(data_dir: Path, draft: Path) -> None:
    (draft / "pages/a.md").write_text(
        "# A\n\n> 状态：`待讨论`\n>\n> 父主题：[根索引](../index.md)\n\nA。\n",
        encoding="utf-8",
    )
    index = draft / "index.md"
    index.write_text(index.read_text(encoding="utf-8") + "- [A](pages/a.md)\n", encoding="utf-8")
    bind(data_dir, make_binding(draft, hint=Path("pages/a.md")))
    record = load_binding(data_dir, "claude", "abc123")
    assert record is not None
    assert record.last_page_hint == Path("pages/a.md")

    with pytest.raises(DraftError, match="binding.invalid_hint"):
        bind(data_dir, make_binding(draft, session="other", hint=Path("pages/missing.md")))


def test_platforms_use_separate_directories(data_dir: Path, draft: Path) -> None:
    binding = make_binding(draft)
    bind(data_dir, binding)
    bind(
        data_dir,
        Binding(
            platform="codex",
            session_id="abc123",
            root=draft,
            last_page_hint=None,
            skill_name="draft",
            skill_digest="codex-digest",
        ),
    )

    assert binding_path(data_dir, "claude", "abc123").is_file()
    assert binding_path(data_dir, "codex", "abc123").is_file()
    assert load_binding(data_dir, "codex", "abc123") is not None


def test_rebind_replaces_only_that_record(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))
    bind(data_dir, make_binding(draft, session="keep"))
    replacement = Binding(
        platform="claude",
        session_id="abc123",
        root=draft,
        last_page_hint=None,
        skill_name="draft",
        skill_digest="new-digest",
    )
    bind(data_dir, replacement)

    record = load_binding(data_dir, "claude", "abc123")
    assert record is not None
    assert record.skill_digest == "new-digest"
    assert load_binding(data_dir, "claude", "keep") is not None


def test_unbind_is_idempotent_and_platform_scoped(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))
    bind(
        data_dir,
        Binding(
            platform="codex",
            session_id="abc123",
            root=draft,
            last_page_hint=None,
            skill_name="draft",
            skill_digest="codex-digest",
        ),
    )

    assert unbind(data_dir, "claude", "abc123") is True
    assert unbind(data_dir, "claude", "abc123") is False
    assert load_binding(data_dir, "codex", "abc123") is not None


def test_missing_data_dir_never_falls_back(tmp_path: Path, draft: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(DraftError, match="binding.data_dir_unavailable"):
        bind(missing, make_binding(draft))
    assert not missing.exists()


def test_invalid_binding_file_raises(data_dir: Path, draft: Path) -> None:
    path = binding_path(data_dir, "claude", "abc123")
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "claude", "abc123")


def test_skill_digest_reads_file_bytes(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("content", encoding="utf-8")
    import hashlib

    assert skill_digest(skill) == hashlib.sha256(b"content").hexdigest()


def test_required_skill_path_accepts_explicit_regular_file(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("content", encoding="utf-8")
    assert required_skill_path(str(skill)) == skill.resolve()


@pytest.mark.parametrize("raw", [None, ""])
def test_required_skill_path_rejects_missing_input(raw: str | None) -> None:
    with pytest.raises(DraftError, match="binding.skill_unavailable"):
        required_skill_path(raw)


def test_required_skill_path_rejects_non_file(tmp_path: Path) -> None:
    with pytest.raises(DraftError, match="binding.skill_unavailable"):
        required_skill_path(str(tmp_path))
