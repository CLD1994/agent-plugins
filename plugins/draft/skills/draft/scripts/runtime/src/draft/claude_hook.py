from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from draft import bindings
from draft.errors import DraftError
from draft.models import Binding

_MAX_STDIN = 64 * 1024
_SOURCES = frozenset({"startup", "resume", "clear", "compact"})


def _log(data_dir: Path, event: str, **fields: object) -> None:
    try:
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        with (logs / "hook-claude.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _envelope(text: str) -> str:
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": text,
            }
        },
        ensure_ascii=False,
    )


def _rebind_envelope(binding: Binding, source: str, hint: Path | None) -> str:
    lines = [
        "draft 重新绑定",
        f"- 活动草稿根：{binding.root}",
    ]
    if hint is not None:
        lines.append(f"- 最后页面提示：{hint.as_posix()}")
    lines.extend(
        [
            f"- 必须使用的 skill：{binding.skill_name}（摘要 {binding.skill_digest}）",
            f"- 触发原因：{source}",
            "继续受管理讨论前，重新读取完整 SKILL.md，并以根索引和当前对话判断下一步；"
            "不要把页面提示当作阅读游标。",
        ]
    )
    return "\n".join(lines)


def _diagnostic_envelope(reason: str) -> str:
    return "\n".join(
        [
            "draft 绑定需要处理",
            f"- 原因：{reason}",
            "- 请重新选择草稿根并重新执行 bind；不要静默继续旧绑定。",
        ]
    )


def _read_stdin(data_dir: Path) -> dict[str, object] | None:
    text = sys.stdin.read(_MAX_STDIN + 1)
    if len(text) > _MAX_STDIN:
        _log(data_dir, "stdin_oversized")
        return None
    try:
        decoder = json.JSONDecoder()
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError:
        _log(data_dir, "stdin_invalid_json")
        return None
    if text[end:].strip():
        _log(data_dir, "stdin_trailing_data")
        return None
    if not isinstance(value, dict):
        _log(data_dir, "stdin_not_object")
        return None
    return cast("dict[str, object]", value)


def _run(data_dir: Path) -> str | None:
    payload = _read_stdin(data_dir)
    if payload is None:
        return None
    session_id = payload.get("session_id")
    event = payload.get("hook_event_name")
    source = payload.get("source")
    if event != "SessionStart":
        return None
    if not isinstance(session_id, str) or not session_id:
        _log(data_dir, "session_id_missing")
        return None
    if not isinstance(source, str) or source not in _SOURCES:
        _log(data_dir, "source_unknown", source=str(source))
        return None
    binding = bindings.load_binding(data_dir, "claude", session_id)
    if binding is None:
        return None
    if not binding.root.is_dir():
        _log(data_dir, "binding_root_missing", session=session_id)
        return _envelope(_diagnostic_envelope("绑定根不存在；请重新选择草稿并重新 bind"))
    skill_path = bindings.required_skill_path(os.environ.get("DRAFT_SKILL_PATH"))
    current_digest = bindings.skill_digest(skill_path)
    if current_digest != binding.skill_digest:
        _log(data_dir, "skill_digest_mismatch", session=session_id)
        return _envelope(
            _diagnostic_envelope("skill 内容摘要已变化；请重新阅读 SKILL.md 并重新 bind")
        )
    hint = binding.last_page_hint
    if hint is not None and not (binding.root / hint).is_file():
        hint = None
    return _envelope(_rebind_envelope(binding, source, hint))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="draft-hook-claude")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    data_dir = Path(cast(str, args.data_dir)).expanduser()
    if not data_dir.is_dir() or not os.access(data_dir, os.W_OK):
        print(
            json.dumps(
                {
                    "ok": False,
                    "result_type": "error",
                    "error": {
                        "code": "binding.data_dir_unavailable",
                        "severity": "error",
                        "paths": [str(data_dir)],
                        "details": [],
                    },
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 4
    try:
        output = _run(data_dir)
    except DraftError as error:
        _log(data_dir, "binding_error", code=error.code)
        return 0
    if output is not None:
        print(output)
    return 0
