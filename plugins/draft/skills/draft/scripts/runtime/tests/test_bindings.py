import hashlib
import json
from pathlib import Path

import pytest

from draft.bindings import (
    bind,
    binding_key,
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
        session_id=session,
        root=root,
        last_page_hint=hint,
        skill_name="draft",
        skill_digest="test-digest",
    )


def test_binding_path_hashes_opaque_session_id(data_dir: Path) -> None:
    session = "../斜线/和\\unicode"
    path = binding_path(data_dir, session)
    assert path.parent == data_dir / "bindings"
    assert path.name == f"{hashlib.sha256(session.encode('utf-8')).hexdigest()}.json"


@pytest.mark.parametrize("session", ["", "/", "\\", "..", "   ", "会话-１２３"])
def test_binding_path_accepts_opaque_sessions(data_dir: Path, session: str) -> None:
    path = binding_path(data_dir, session)
    assert path.parent == data_dir / "bindings"
    assert path.name == f"{binding_key(session)}.json"
    assert path.resolve().parent == (data_dir / "bindings").resolve()


def test_bind_writes_typed_record(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))

    path = binding_path(data_dir, "abc123")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert set(record) == {
        "session_id",
        "root",
        "last_page_hint",
        "skill_name",
        "skill_digest",
    }
    assert record["session_id"] == "abc123"
    assert record["root"] == str(draft.resolve())
    assert record["skill_name"] == "draft"
    assert record["skill_digest"] == "test-digest"
    assert record["last_page_hint"] is None


@pytest.mark.parametrize("session", ["", "/", "\\", "..", "   ", "会话-１２３"])
def test_bind_and_load_round_trip_opaque_sessions(
    data_dir: Path, draft: Path, session: str
) -> None:
    bind(data_dir, make_binding(draft, session=session))
    record = load_binding(data_dir, session)
    assert record is not None
    assert record.session_id == session
    assert record.root == draft.resolve()
    assert unbind(data_dir, session) is True
    assert load_binding(data_dir, session) is None


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
    record = load_binding(data_dir, "abc123")
    assert record is not None
    assert record.last_page_hint == Path("pages/a.md")

    with pytest.raises(DraftError, match="binding.invalid_hint"):
        bind(data_dir, make_binding(draft, session="other", hint=Path("pages/missing.md")))


def test_rebind_replaces_only_that_record(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))
    bind(data_dir, make_binding(draft, session="keep"))
    replacement = Binding(
        session_id="abc123",
        root=draft,
        last_page_hint=None,
        skill_name="draft",
        skill_digest="new-digest",
    )
    bind(data_dir, replacement)

    record = load_binding(data_dir, "abc123")
    assert record is not None
    assert record.skill_digest == "new-digest"
    assert load_binding(data_dir, "keep") is not None


def test_unbind_is_idempotent(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))

    assert unbind(data_dir, "abc123") is True
    assert unbind(data_dir, "abc123") is False


def test_missing_data_dir_never_falls_back(tmp_path: Path, draft: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(DraftError, match="binding.data_dir_unavailable"):
        bind(missing, make_binding(draft))
    assert not missing.exists()


def test_data_dir_boundary_rejects_non_canonical_dirs(
    tmp_path: Path, draft: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = (tmp_path / "data").resolve()
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    monkeypatch.chdir(tmp_path)
    invalid = [Path("."), Path("data"), link, tmp_path / "missing", draft / "index.md"]
    for candidate in invalid:
        with pytest.raises(DraftError, match="binding.data_dir_unavailable"):
            bind(candidate, make_binding(draft))
        with pytest.raises(DraftError, match="binding.data_dir_unavailable"):
            load_binding(candidate, "abc123")
        with pytest.raises(DraftError, match="binding.data_dir_unavailable"):
            unbind(candidate, "abc123")
    assert not (tmp_path / "bindings").exists()
    assert list(real.iterdir()) == []


def test_data_dir_boundary_accepts_canonical_absolute_dir(tmp_path: Path, draft: Path) -> None:
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir()
    bind(data_dir, make_binding(draft))
    assert load_binding(data_dir, "abc123") is not None
    assert unbind(data_dir, "abc123") is True


def test_load_returns_none_only_when_file_missing(data_dir: Path) -> None:
    assert load_binding(data_dir, "abc123") is None


def test_load_rejects_session_payload_mismatch(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft, session="expected"))
    path = binding_path(data_dir, "expected")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session_id"] = "other"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DraftError, match="binding.session_mismatch"):
        load_binding(data_dir, "expected")


def test_load_rejects_symlink(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))
    path = binding_path(data_dir, "abc123")
    target = path.read_bytes()
    path.unlink()
    real = data_dir / "real.json"
    real.write_bytes(target)
    path.symlink_to(real)
    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "abc123")


def test_load_rejects_directory(data_dir: Path) -> None:
    path = binding_path(data_dir, "abc123")
    path.mkdir(parents=True)
    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "abc123")


def test_load_rejects_broken_json(data_dir: Path) -> None:
    path = binding_path(data_dir, "abc123")
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")

    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "abc123")


def test_load_rejects_missing_field(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))
    path = binding_path(data_dir, "abc123")
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["skill_digest"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "abc123")


def test_load_rejects_extra_platform_field(data_dir: Path, draft: Path) -> None:
    bind(data_dir, make_binding(draft))
    path = binding_path(data_dir, "abc123")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["platform"] = "claude"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "abc123")


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", 42),
        ("root", None),
        ("last_page_hint", 7),
        ("skill_name", ["draft"]),
        ("skill_digest", False),
    ],
)
def test_load_rejects_wrong_field_types(
    data_dir: Path, draft: Path, field: str, value: object
) -> None:
    bind(data_dir, make_binding(draft))
    path = binding_path(data_dir, "abc123")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DraftError, match="binding.invalid"):
        load_binding(data_dir, "abc123")


def test_skill_digest_reads_file_bytes(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("content", encoding="utf-8")

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
