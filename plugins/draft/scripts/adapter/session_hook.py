"""SessionStart Hook 适配层：宿主上下文、载荷、信封与日志，不调用 Draft 核心。

仅依赖 Python 标准库与 adapter 内部模块。八步执行顺序见设计规格
"SessionStart 数据流"：宿主变量 -> 布局校验 -> bridge 发布 -> 载荷 -> binding ->
日志 -> 输出。布局与宿主上下文失败先于 bridge 发布；载荷校验晚于 bridge 发布。
"""

from __future__ import annotations

import json
import shlex
import stat
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from adapter.bindings import (
    Binding,
    binding_hint_is_current,
    binding_root_is_current,
    load_binding,
    session_id_is_valid,
)
from adapter.bridge import publish_context
from adapter.errors import AdapterError

MAX_STDIN = 64 * 1024

HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}

HOST_SOURCES = {
    "codex": ("startup", "resume", "clear", "compact"),
    "claude": ("startup", "resume", "clear", "compact", "fork"),
}

_SESSION_START_EVENT = "SessionStart"

# Hook 诊断允许进入模型可见输出的稳定清理码白名单（按执行顺序）。
# 路径、note、底层 OSError 文本与任意非清理用途的 detail 一律不在此列。
_HOOK_DIAGNOSTIC_DETAIL_WHITELIST = (
    "cleanup.unlock_failed",
    "cleanup.close_failed",
    "cleanup.unlink_failed",
)


@dataclass(frozen=True)
class HostContext:
    host: str
    plugin_root: Path
    data_dir: Path
    entry_path: Path
    skill_path: Path


@dataclass(frozen=True)
class HookPayload:
    session_id: str
    source: str


# ---------------------------------------------------------------------------
# 共享 shell 引用助手
# ---------------------------------------------------------------------------


def _shell_quote(path: Path) -> str:
    """命令示例中的路径统一经 shlex.quote 处理。"""
    return shlex.quote(str(path))


# ---------------------------------------------------------------------------
# 宿主上下文解析
# ---------------------------------------------------------------------------


def _environment_missing(*paths: Path) -> AdapterError:
    return AdapterError("hook.environment_missing", *paths, exit_code=4)


def _plugin_root_mismatch(*paths: Path) -> AdapterError:
    return AdapterError("hook.plugin_root_mismatch", *paths, exit_code=4)


def _layout_invalid(*paths: Path) -> AdapterError:
    return AdapterError("hook.layout_invalid", *paths, exit_code=4)


def resolve_host_context(host: str, entry_path: Path, environ: Mapping[str, str]) -> HostContext:
    """选择唯一宿主变量对，lstat 校验入口与插件根，严格解析，校验 SKILL.md。

    返回仅含规范绝对路径的 HostContext。布局或宿主上下文失败先于 bridge 发布。
    """
    if host not in HOST_ENV:
        raise AdapterError("hook.unknown_host", exit_code=4)
    root_var, data_var = HOST_ENV[host]
    raw_root = environ.get(root_var, "")
    raw_data = environ.get(data_var, "")
    if not raw_root or not raw_data:
        raise _environment_missing()
    plugin_root_raw = Path(raw_root)
    data_dir = Path(raw_data)
    if not plugin_root_raw.is_absolute() or not data_dir.is_absolute():
        raise _environment_missing(plugin_root_raw, data_dir)
    # data_dir 词法规范化（消除 . / ..，解析已存在的符号链接组件），不要求存在：
    # 相对路径已在上方拒绝；不存在的绝对 data_dir 仍由 publish_context 的
    # _canonical_data_dir(resolve(strict=True)) 归一为 plugin.context_invalid。
    data_dir = data_dir.resolve(strict=False)

    # lstat 入口：不跟随符号链接，必须为普通文件
    try:
        entry_status = entry_path.lstat()
    except OSError as exc:
        raise _layout_invalid(entry_path) from exc
    if stat.S_ISLNK(entry_status.st_mode) or not stat.S_ISREG(entry_status.st_mode):
        raise _layout_invalid(entry_path)

    # lstat 插件根：不跟随符号链接，必须为目录
    try:
        root_status = plugin_root_raw.lstat()
    except OSError as exc:
        raise _plugin_root_mismatch(plugin_root_raw) from exc
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        raise _plugin_root_mismatch(plugin_root_raw)

    # 严格解析为规范绝对路径
    try:
        canonical_entry = entry_path.resolve(strict=True)
        canonical_root = plugin_root_raw.resolve(strict=True)
    except OSError as exc:
        raise _plugin_root_mismatch(entry_path, plugin_root_raw) from exc

    # 入口必须位于 plugin_root/scripts/draft.py
    if canonical_entry != canonical_root / "scripts" / "draft.py":
        raise _plugin_root_mismatch(canonical_entry, canonical_root)

    # lstat SKILL.md：不跟随符号链接，必须为普通文件
    skill_path = canonical_root / "skills" / "draft" / "SKILL.md"
    try:
        skill_status = skill_path.lstat()
    except OSError as exc:
        raise _layout_invalid(skill_path) from exc
    if stat.S_ISLNK(skill_status.st_mode) or not stat.S_ISREG(skill_status.st_mode):
        raise _layout_invalid(skill_path)

    return HostContext(
        host=host,
        plugin_root=canonical_root,
        data_dir=data_dir,
        entry_path=canonical_entry,
        skill_path=skill_path,
    )


# ---------------------------------------------------------------------------
# 载荷读取与校验
# ---------------------------------------------------------------------------


def read_hook_payload(stream: BinaryIO, host: str) -> HookPayload:
    """读取至多 MAX_STDIN + 1 个原始字节，校验对象/事件/session/宿主 source。

    边界按字节而非字符计算；大小检查先于任何解码。解码使用严格 UTF-8，
    不使用替换字符或忽略错误。不修改字符串值：session_id 原样保留，
    不截断、不解释、不做 Unicode 规范化。
    """
    try:
        raw = stream.read(MAX_STDIN + 1)
    except OSError as exc:
        raise AdapterError("hook.stdin_unavailable", exit_code=4) from exc
    if len(raw) > MAX_STDIN:
        raise AdapterError("hook.stdin_too_large", exit_code=4)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AdapterError("hook.stdin_invalid", exit_code=4) from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AdapterError("hook.stdin_invalid", exit_code=4) from exc
    if not isinstance(value, dict):
        raise AdapterError("hook.stdin_not_object", exit_code=4)
    payload = cast("dict[str, object]", value)
    if payload.get("hook_event_name") != _SESSION_START_EVENT:
        raise AdapterError("hook.unexpected_event", exit_code=4)
    session_id = payload.get("session_id")
    if not isinstance(session_id, str):
        raise AdapterError("binding.session_unavailable", exit_code=4)
    # 孤立代理码位无法安全进入协议或文件系统操作：按载荷无效处理。
    # 不截断、不解释、不规范化；禁止到达 binding_key 的 UTF-8 编码。
    if not session_id_is_valid(session_id):
        raise AdapterError("hook.stdin_invalid", exit_code=4)
    source = payload.get("source")
    if not isinstance(source, str):
        raise AdapterError("hook.unexpected_source", exit_code=4)
    legal_sources = HOST_SOURCES.get(host)
    if legal_sources is None or source not in legal_sources:
        raise AdapterError("hook.unexpected_source", exit_code=4)
    return HookPayload(session_id=session_id, source=source)


# ---------------------------------------------------------------------------
# 信封（纯函数）
# ---------------------------------------------------------------------------


def render_session_fact(session_id: str, entry_path: Path) -> str:
    """未绑定信封：原始 session_id、规范插件入口、bind/unbind 命令格式。"""
    quoted_entry = _shell_quote(entry_path)
    return "\n".join(
        [
            "Draft 会话未绑定",
            f"- 宿主 session_id：{json.dumps(session_id, ensure_ascii=False)}",
            f"- 插件入口：{entry_path}",
            f"- 绑定格式：uv run {quoted_entry} bind ROOT --session SESSION_ID "
            "[--last-page-hint PAGE]",
            f"- 解除格式：uv run {quoted_entry} unbind --session SESSION_ID",
        ]
    )


def render_rebind(
    binding: Binding,
    source: str,
    hint: Path | None,
    skill_path: Path,
    entry_path: Path,
) -> str:
    """恢复信封：根、可选提示、source、当前 skill、入口、阅读要求与核心检查命令。"""
    skill_root = skill_path.parent
    lines = [
        "Draft 会话已绑定",
        f"- 草稿根：{binding.root}",
    ]
    if hint is not None:
        lines.append(f"- 最后页面提示：{PurePosixPath(hint.as_posix()).as_posix()}")
    lines.extend(
        [
            f"- 宿主触发原因：{source}",
            f"- 当前 skill：{skill_path}",
            f"- 插件入口：{entry_path}",
            "- 继续受管理讨论前，重新完整阅读当前 SKILL.md。",
            "- 修改前运行核心检查：uvx --isolated --from "
            f"{_shell_quote(skill_root / 'scripts' / 'runtime')} "
            f"draft check {_shell_quote(binding.root)}",
        ]
    )
    return "\n".join(lines)


def render_diagnostic(error: AdapterError) -> str:
    """诊断信封：恰好一处“Draft 插件诊断”，包含错误码与白名单清理次要诊断码。

    仅显示白名单 cleanup.* 稳定码（按 details 中的执行顺序）；不输出路径、note、
    底层错误文本或任意非清理用途的 detail，避免向模型暴露可能已失效的绑定根、
    宿主数据目录或临时文件路径。
    """
    lines = [
        "Draft 插件诊断",
        f"- 错误码：{error.code}",
    ]
    for detail in error.details:
        if detail in _HOOK_DIAGNOSTIC_DETAIL_WHITELIST:
            lines.append(f"- 次要诊断码：{detail}")
    lines.append("- 请检查插件安装或重新执行 bind。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 日志（唯一显式尽力而为的文件操作）
# ---------------------------------------------------------------------------


def log_hook(data_dir: Path | None, host: str, **fields: object) -> AdapterError | None:
    """尽力而为的宿主 Hook 日志。失败返回 hook.log_unavailable 而非抛出。

    类型安全（lstat，不跟随符号链接）：logs/ 必须不存在或是真实目录；
    hook.jsonl 必须不存在或是普通文件。符号链接、目录替代普通文件、FIFO 等
    异常类型一律拒绝且不得打开。显式尽力而为语义不变：不引入目录句柄或
    完整抗竞争架构。
    """
    if data_dir is None:
        return None
    logs = data_dir / "logs"
    target = logs / "hook.jsonl"
    try:
        try:
            logs_status = logs.lstat()
        except FileNotFoundError:
            logs_status = None
        if logs_status is not None and not stat.S_ISDIR(logs_status.st_mode):
            return AdapterError("hook.log_unavailable", data_dir, exit_code=4)
        try:
            target_status = target.lstat()
        except FileNotFoundError:
            target_status = None
        if target_status is not None and not stat.S_ISREG(target_status.st_mode):
            return AdapterError("hook.log_unavailable", data_dir, exit_code=4)
        logs.mkdir(parents=True, exist_ok=True)
        record = {
            "at": datetime.now(UTC).isoformat(),
            "host": host,
            **fields,
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return None
    except OSError:
        return AdapterError("hook.log_unavailable", data_dir, exit_code=4)


# ---------------------------------------------------------------------------
# Hook 边界
# ---------------------------------------------------------------------------


def run_session_start(
    host: str,
    *,
    entry_path: Path,
    environ: Mapping[str, str],
    stdin: BinaryIO,
    runtime_dir: Path | None = None,
) -> int:
    """按八步顺序执行 SessionStart；所有失败归一为模型可见诊断并返回 0。

    布局或宿主上下文失败先于 bridge 发布；载荷校验晚于 bridge 发布。stdin 为
    二进制流（宿主原始字节），字节边界与严格 UTF-8 校验见 read_hook_payload。
    stderr 保持为空，所有诊断输出到 stdout。
    """
    primary: str
    log_data_dir: Path | None = None
    log_fields: dict[str, object] = {}

    try:
        ctx = resolve_host_context(host, entry_path, environ)
        log_data_dir = ctx.data_dir
        # bridge 发布（或验证）发生在载荷校验之前
        publish_context(ctx.entry_path, ctx.data_dir, runtime_dir)
        payload = read_hook_payload(stdin, host)
        log_fields["source"] = payload.source
        binding = load_binding(ctx.data_dir, payload.session_id)
        if binding is None:
            primary = render_session_fact(payload.session_id, ctx.entry_path)
            log_fields["branch"] = "session_fact"
        else:
            root_current = binding_root_is_current(binding)
            if not root_current:
                # root 后续缺失/非目录/被符号链接替换：要求显式重新绑定
                error = AdapterError("binding.root_missing", exit_code=4)
                primary = render_diagnostic(error)
                log_fields["branch"] = "diagnostic"
                log_fields["error"] = error.code
            else:
                hint_current = binding_hint_is_current(binding)
                hint = binding.last_page_hint if hint_current else None
                primary = render_rebind(
                    binding, payload.source, hint, ctx.skill_path, ctx.entry_path
                )
                log_fields["branch"] = "rebind"
    except AdapterError as error:
        primary = render_diagnostic(error)
        log_fields["branch"] = "diagnostic"
        log_fields["error"] = error.code

    # 日志失败仅追加诊断，不丢弃主分支
    log_error = log_hook(log_data_dir, host, **log_fields)
    _write_stdout(primary)
    if log_error is not None:
        _write_stdout(render_diagnostic(log_error))
    return 0


def _write_stdout(text: str) -> None:
    """写到 stdout（被 capsys 捕获），每条信封独占一行。"""
    sys.stdout.write(text + "\n")
