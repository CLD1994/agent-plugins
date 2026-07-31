"""宿主无关的 SessionStart 绑定查找、摘要验证、诊断和短上下文渲染。

模块导入图只依赖标准库与 draft.errors/draft.models，稳定入口的引导解释器可以直接导入；
draft.bindings 依赖第三方解析链，延迟到同步后的持久环境解释器中导入。
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from draft.errors import DraftError
from draft.models import Binding


def render_session_fact(session_id: str) -> str:
    return f"Draft 宿主 session_id：{session_id}\n在 scripts/draft bind/unbind 中原样使用此值。"


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


def run_binding_branch(data_dir: Path, session_id: str, source: str, host: str) -> int:
    from draft import bindings  # 第三方解析链只在同步后的环境解释器中可用

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
        skill_path = bindings.required_skill_path(os.environ.get("DRAFT_SKILL_PATH"))
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m draft.session_hook")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--host", required=True)
    args = parser.parse_args(argv)
    return run_binding_branch(
        Path(cast(str, args.data_dir)),
        cast(str, args.session_id),
        cast(str, args.source),
        cast(str, args.host),
    )


if __name__ == "__main__":
    raise SystemExit(main())
