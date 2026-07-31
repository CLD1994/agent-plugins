from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import cast

from draft.checker import check_draft
from draft.errors import DraftError
from draft.graph import load_graph
from draft.models import Binding
from draft.operations import durable_write
from draft.paths import resolve_draft_root

_PAYLOAD_KEYS = {"session_id", "root", "last_page_hint", "skill_name", "skill_digest"}


def required_skill_path(raw: str | None) -> Path:
    if raw is None or not raw.strip():
        raise DraftError("binding.skill_unavailable")
    path = Path(raw).expanduser()
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise DraftError("binding.skill_unavailable", path)
        return path.resolve(strict=True)
    except OSError as exc:
        raise DraftError("binding.skill_unavailable", path) from exc


def skill_digest(skill_path: Path) -> str:
    try:
        return hashlib.sha256(skill_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DraftError("binding.skill_unavailable", skill_path) from exc


def _require_data_dir(data_dir: Path) -> Path:
    if not data_dir.is_dir():
        raise DraftError("binding.data_dir_unavailable", data_dir)
    return data_dir


def binding_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def binding_path(data_dir: Path, session_id: str) -> Path:
    return data_dir / "bindings" / f"{binding_key(session_id)}.json"


def _binding_json(binding: Binding) -> bytes:
    payload = {
        "session_id": binding.session_id,
        "root": str(binding.root),
        "last_page_hint": (
            None if binding.last_page_hint is None else binding.last_page_hint.as_posix()
        ),
        "skill_name": binding.skill_name,
        "skill_digest": binding.skill_digest,
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _parse_binding(raw: object, path: Path, session_id: str) -> Binding:
    if not isinstance(raw, dict):
        raise DraftError("binding.invalid", path)
    data = cast("dict[str, object]", raw)
    if set(data) != _PAYLOAD_KEYS:
        raise DraftError("binding.invalid", path)
    payload_session = data["session_id"]
    root = data["root"]
    hint = data["last_page_hint"]
    skill_name = data["skill_name"]
    digest = data["skill_digest"]
    if (
        not isinstance(payload_session, str)
        or not isinstance(root, str)
        or not (hint is None or isinstance(hint, str))
        or not isinstance(skill_name, str)
        or not isinstance(digest, str)
    ):
        raise DraftError("binding.invalid", path)
    if payload_session != session_id:
        raise DraftError("binding.session_mismatch", path)
    return Binding(
        session_id=payload_session,
        root=Path(root),
        last_page_hint=None if hint is None else Path(hint),
        skill_name=skill_name,
        skill_digest=digest,
    )


def bind(data_dir: Path, binding: Binding) -> Binding:
    _require_data_dir(data_dir)
    root = resolve_draft_root(binding.root)
    report = check_draft(root)
    if report.errors:
        codes = tuple(issue.code for issue in report.errors)
        raise DraftError("binding.invalid_root", root, details=codes)
    if binding.last_page_hint is not None:
        graph = load_graph(root)
        if binding.last_page_hint not in graph.pages:
            raise DraftError("binding.invalid_hint", binding.last_page_hint)
    canonical = Binding(
        session_id=binding.session_id,
        root=root,
        last_page_hint=binding.last_page_hint,
        skill_name=binding.skill_name,
        skill_digest=binding.skill_digest,
    )
    target = binding_path(data_dir, binding.session_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    durable_write(target, _binding_json(canonical))
    return canonical


def load_binding(data_dir: Path, session_id: str) -> Binding | None:
    path = binding_path(data_dir, session_id)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise DraftError("binding.invalid", path) from exc
    if not stat.S_ISREG(mode):
        raise DraftError("binding.invalid", path)
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DraftError("binding.invalid", path) from exc
    return _parse_binding(decoded, path, session_id)


def unbind(data_dir: Path, session_id: str) -> bool:
    _require_data_dir(data_dir)
    path = binding_path(data_dir, session_id)
    if not path.exists():
        return False
    path.unlink()
    return True
