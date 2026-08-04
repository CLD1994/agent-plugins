"""已安装 runtime 内的 SessionStart 处理：stdin 载荷解析、绑定查找、诊断和短上下文渲染。

launcher 已经发布 bridge、同步版本环境并注入 DRAFT_DATA / DRAFT_SKILL_PATH；
本模块不再读取宿主 root/data 变量。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast

from draft.errors import DraftError
from draft.models import Binding

MAX_STDIN = 64 * 1024


def render_session_fact(session_id: str) -> str:
    return f"Draft 宿主 session_id：{session_id}\n在 Draft bind/unbind 命令中原样使用此值。"


def render_rebind(binding: Binding, source: str, hint: Path | None) -> str:
    lines = [
        "Draft 重新绑定上下文",
        f"- 活动草稿根：{binding.root}",
        f"- 必须使用的 skill：{binding.skill_name}（摘要 {binding.skill_digest}）",
        f"- 宿主触发原因：{source}",
    ]
    if hint is not None:
        lines.insert(2, f"- 最后页面提示：{hint.as_posix()}")
    lines.append("继续受管理讨论前，重新完整读取当前 SKILL.md。")
    return "\n".join(lines)


def render_diagnostic(reason: str) -> str:
    return "\n".join(
        [
            "Draft 绑定需要处理",
            f"- 原因：{reason}",
            "- 请重新选择草稿根并重新执行 bind；不要静默继续旧绑定。",
        ]
    )


def log_hook(data_dir: Path, host: str, **fields: object) -> None:
    try:
        logs = data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now(UTC).isoformat(),
            "host": host,
            **fields,
        }
        with (logs / "hook.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_hook_payload(stream: TextIO) -> tuple[dict[str, object] | None, str]:
    text = stream.read(MAX_STDIN + 1)
    if len(text) > MAX_STDIN:
        return None, "hook.stdin_oversized"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None, "hook.stdin_invalid"
    if not isinstance(value, dict):
        return None, "hook.stdin_not_object"
    payload = cast("dict[str, object]", value)
    if payload.get("hook_event_name") != "SessionStart":
        return None, "hook.unexpected_event"
    return payload, ""


def run_binding_branch(
    data_dir: Path,
    session_id: str,
    source: str,
    host: str,
    environ: Mapping[str, str],
) -> int:
    from draft import bindings

    try:
        binding = bindings.load_binding(data_dir, session_id)
        if binding is None:
            print(render_session_fact(session_id))
            log_hook(data_dir, host, branch="session_fact", source=source)
            return 0
        if not binding.root.is_dir():
            log_hook(
                data_dir, host, branch="diagnostic", source=source, error="binding.root_missing"
            )
            print(render_diagnostic("绑定根不存在；请重新选择草稿并重新 bind"))
            return 0
        skill_path = bindings.required_skill_path(environ.get("DRAFT_SKILL_PATH"))
        if bindings.skill_digest(skill_path) != binding.skill_digest:
            log_hook(
                data_dir, host, branch="diagnostic", source=source, error="binding.skill_mismatch"
            )
            print(render_diagnostic("skill 内容摘要已变化；请重新阅读 SKILL.md 并重新 bind"))
            return 0
        hint = binding.last_page_hint
        if hint is not None and not (binding.root / hint).is_file():
            hint = None
        print(render_rebind(binding, source, hint))
        log_hook(data_dir, host, branch="rebind", source=source)
        return 0
    except DraftError as error:
        log_hook(
            data_dir,
            host,
            branch="diagnostic",
            source=source,
            error=error.code,
            paths=[str(path) for path in error.paths],
        )
        print(render_diagnostic(f"{error.code}；请检查绑定状态后重新执行 bind"))
        return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
) -> int:
    import sys

    arguments = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    stream = sys.stdin if stdin is None else stdin
    if len(arguments) != 1:
        print(render_diagnostic("entry.invalid_arguments；内部 hook 只接受一个 host 参数"))
        return 0
    host = arguments[0]
    raw_data = env.get("DRAFT_DATA")
    if not raw_data:
        print(render_diagnostic("hook.environment_missing：DRAFT_DATA 未注入"))
        return 0
    data_dir = Path(raw_data)
    payload, parse_error = read_hook_payload(stream)
    if payload is None:
        log_hook(data_dir, host, branch="ignored", error=parse_error)
        return 0
    raw_source = payload.get("source")
    source = raw_source if isinstance(raw_source, str) else ""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        log_hook(
            data_dir, host, branch="ignored", source=source, error="binding.session_unavailable"
        )
        return 0
    return run_binding_branch(data_dir, session_id, source, host, env)


if __name__ == "__main__":
    raise SystemExit(main())
