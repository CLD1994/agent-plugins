"""三字段 binding 协议：关联不透明 session_id 与工作目录，不验证 Draft。仅依赖标准库。"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from adapter.errors import EXIT_RUNTIME, EXIT_USAGE, AdapterError

_PAYLOAD_KEYS = {"session_id", "root", "last_page_hint"}


@dataclass(frozen=True)
class Binding:
    session_id: str
    root: Path
    last_page_hint: Path | None


def binding_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def binding_path(data_dir: Path, session_id: str) -> Path:
    return data_dir / "bindings" / f"{binding_key(session_id)}.json"


# ---------------------------------------------------------------------------
# 错误构造
# ---------------------------------------------------------------------------


def _data_dir_unavailable(path: Path) -> AdapterError:
    return AdapterError("binding.data_dir_unavailable", path, exit_code=EXIT_RUNTIME)


def _root_unavailable(path: Path) -> AdapterError:
    return AdapterError("binding.root_unavailable", path, exit_code=EXIT_USAGE)


def _invalid_hint(path: Path) -> AdapterError:
    return AdapterError("binding.invalid_hint", path, exit_code=EXIT_USAGE)


def _invalid(path: Path) -> AdapterError:
    return AdapterError("binding.invalid", path, exit_code=EXIT_RUNTIME)


def _session_mismatch(path: Path) -> AdapterError:
    return AdapterError("binding.session_mismatch", path, exit_code=EXIT_RUNTIME)


# ---------------------------------------------------------------------------
# 内部异常：hint 遍历遇到符号链接
# ---------------------------------------------------------------------------


class _HintSymlink(Exception):
    def __init__(self, path: Path) -> None:
        self.path = path


# ---------------------------------------------------------------------------
# 数据目录与根校验
# ---------------------------------------------------------------------------


def _require_data_dir(data_dir: Path) -> Path:
    """绝对、规范化、真实存在、可写目录；lstat 拒绝符号链接。"""
    if not data_dir.is_absolute():
        raise _data_dir_unavailable(data_dir)
    try:
        status = data_dir.lstat()
        canonical = data_dir.resolve(strict=True)
    except OSError as exc:
        raise _data_dir_unavailable(data_dir) from exc
    if (
        stat.S_ISLNK(status.st_mode)
        or canonical != data_dir
        or not stat.S_ISDIR(status.st_mode)
        or not os.access(data_dir, os.W_OK)
    ):
        raise _data_dir_unavailable(data_dir)
    return data_dir


def _canonical_root(root: Path) -> Path:
    """展开 ~，要求 root 自身为非符号链接目录，再严格 resolve。不做 Draft 校验。"""
    expanded = root.expanduser()
    try:
        status = expanded.lstat()
    except OSError as exc:
        raise _root_unavailable(root) from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise _root_unavailable(root)
    try:
        return expanded.resolve(strict=True)
    except OSError as exc:
        raise _root_unavailable(root) from exc


# ---------------------------------------------------------------------------
# 页面提示：词法解析 + 当前文件系统校验
# ---------------------------------------------------------------------------


def _lexical_hint(raw: str) -> PurePosixPath:
    """词法校验：非空、非绝对（POSIX/Windows）、不含 ..（POSIX/Windows）、后缀 .md。"""
    if not raw.strip():
        raise _invalid_hint(Path(raw))
    posix = PurePosixPath(raw)
    win = PureWindowsPath(raw)
    if posix.is_absolute() or win.is_absolute():
        raise _invalid_hint(Path(raw))
    if ".." in posix.parts or ".." in win.parts:
        raise _invalid_hint(Path(raw))
    if posix.suffix != ".md":
        raise _invalid_hint(Path(raw))
    return posix


def _walk_hint_segments(hint: PurePosixPath, canonical_root: Path) -> os.stat_result:
    """对每个具体段 lstat，拒绝符号链接。返回最终目标的 stat_result。

    FileNotFoundError 表示某段缺失；_HintSymlink 表示遇到符号链接；
    其他 OSError 表示访问错误。
    """
    cursor = canonical_root
    status: os.stat_result | None = None
    for part in hint.parts:
        cursor = cursor / part
        status = cursor.lstat()
        if stat.S_ISLNK(status.st_mode):
            raise _HintSymlink(cursor)
    assert status is not None
    return status


def _require_hint_current(hint: PurePosixPath, canonical_root: Path) -> None:
    """bind 用：词法 + 当前文件系统双重校验。"""
    try:
        status = _walk_hint_segments(hint, canonical_root)
    except _HintSymlink as exc:
        raise _invalid_hint(Path(hint)) from exc
    except OSError as exc:
        raise _invalid_hint(Path(hint)) from exc
    if not stat.S_ISREG(status.st_mode):
        raise _invalid_hint(Path(hint))


# ---------------------------------------------------------------------------
# 容器（bindings/ 目录）
# ---------------------------------------------------------------------------


def _ensure_container(container: Path) -> None:
    """创建或验证 bindings/ 为真实目录，直接位于规范化数据目录下。"""
    try:
        status = container.lstat()
    except FileNotFoundError:
        try:
            container.mkdir(mode=0o700)
        except OSError as exc:
            raise _data_dir_unavailable(container) from exc
        return
    except OSError as exc:
        raise _data_dir_unavailable(container) from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise _data_dir_unavailable(container)


def _container_is_real(container: Path) -> bool:
    """bindings/ 存在且为真实目录返回 True；缺失返回 False；其余拒绝。"""
    try:
        status = container.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _data_dir_unavailable(container) from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        raise _data_dir_unavailable(container)
    return True


# ---------------------------------------------------------------------------
# 目标对象安全
# ---------------------------------------------------------------------------


def _require_safe_target_for_overwrite(path: Path) -> None:
    """目标缺失或为安全普通文件时通过；符号链接/非普通文件拒绝为 binding.invalid。"""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise _invalid(path) from exc
    if not stat.S_ISREG(status.st_mode):
        raise _invalid(path)


def _lstat_target(path: Path) -> os.stat_result | None:
    """返回目标 lstat 结果；缺失返回 None；符号链接/非普通文件/访问错误拒绝。"""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _invalid(path) from exc
    if not stat.S_ISREG(status.st_mode):
        raise _invalid(path)
    return status


# ---------------------------------------------------------------------------
# 原子写
# ---------------------------------------------------------------------------


def _fsync_directory(path: Path) -> None:
    """尽力而为的目录 fsync：任何 OSError 完整吸收。"""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        with suppress(OSError):
            os.close(descriptor)


def _write_binding_atomic(path: Path, payload: bytes) -> None:
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError as exc:
        raise _data_dir_unavailable(path) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException as exc:
        # fdopen 失败时描述符尚未托管，补关；已关闭时这次 close 被 suppress
        with suppress(OSError):
            os.close(descriptor)
        # 主错误：OSError 映射为 data_dir_unavailable；非 OSError 原样重抛
        error: AdapterError | None = None
        if isinstance(exc, OSError):
            error = _data_dir_unavailable(path)
        # 清理临时文件：失败时作为 note 附加到主错误，不替换主错误
        try:
            os.unlink(temporary)
        except OSError as cleanup_exc:
            note = f"cleanup unlink failed for {temporary}: {cleanup_exc}"
            if error is not None:
                error.add_note(note)
            else:
                exc.add_note(note)
        if error is not None:
            raise error from exc
        raise
    _fsync_directory(path.parent)


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _binding_json(binding: Binding) -> bytes:
    payload: dict[str, object] = {
        "session_id": binding.session_id,
        "root": str(binding.root),
        "last_page_hint": (
            None
            if binding.last_page_hint is None
            else PurePosixPath(binding.last_page_hint.as_posix()).as_posix()
        ),
    }
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def _is_lexically_normalized_absolute(path_str: str) -> bool:
    path = Path(path_str)
    if not path.is_absolute():
        return False
    if "." in path.parts or ".." in path.parts:
        return False
    return str(path) == path_str


def _parse_binding(raw: object, path: Path, session_id: str) -> Binding:
    if not isinstance(raw, dict):
        raise _invalid(path)
    data = raw
    if set(data) != _PAYLOAD_KEYS:
        raise _invalid(path)

    payload_session = data["session_id"]
    root_str = data["root"]
    hint = data["last_page_hint"]

    if not isinstance(payload_session, str):
        raise _invalid(path)
    if not isinstance(root_str, str) or not root_str:
        raise _invalid(path)
    if not (hint is None or isinstance(hint, str)):
        raise _invalid(path)

    if payload_session != session_id:
        raise _session_mismatch(path)

    if not _is_lexically_normalized_absolute(root_str):
        raise _invalid(path)

    hint_path: Path | None = None
    if hint is not None:
        lexical = _lexical_hint(hint)
        hint_path = Path(lexical)

    return Binding(
        session_id=payload_session,
        root=Path(root_str),
        last_page_hint=hint_path,
    )


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------


def bind(data_dir: Path, binding: Binding) -> Binding:
    canonical_data_dir = _require_data_dir(data_dir)
    canonical_root = _canonical_root(binding.root)

    canonical_hint: Path | None = None
    if binding.last_page_hint is not None:
        lexical = _lexical_hint(binding.last_page_hint.as_posix())
        _require_hint_current(lexical, canonical_root)
        canonical_hint = Path(lexical)

    canonical = Binding(
        session_id=binding.session_id,
        root=canonical_root,
        last_page_hint=canonical_hint,
    )

    container = canonical_data_dir / "bindings"
    _ensure_container(container)

    target = binding_path(canonical_data_dir, binding.session_id)
    _require_safe_target_for_overwrite(target)
    _write_binding_atomic(target, _binding_json(canonical))
    return canonical


def load_binding(data_dir: Path, session_id: str) -> Binding | None:
    canonical_data_dir = _require_data_dir(data_dir)
    container = canonical_data_dir / "bindings"
    if not _container_is_real(container):
        return None

    target = binding_path(canonical_data_dir, session_id)
    if _lstat_target(target) is None:
        return None

    try:
        decoded = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _invalid(target) from exc
    return _parse_binding(decoded, target, session_id)


def unbind(data_dir: Path, session_id: str) -> bool:
    canonical_data_dir = _require_data_dir(data_dir)
    container = canonical_data_dir / "bindings"
    if not _container_is_real(container):
        return False

    target = binding_path(canonical_data_dir, session_id)
    if _lstat_target(target) is None:
        return False

    try:
        target.unlink()
    except OSError as exc:
        raise _data_dir_unavailable(target) from exc
    _fsync_directory(container)
    return True


def binding_root_is_current(binding: Binding) -> bool:
    """root 当前是否仍为同一规范目录。普通过期返回 False，意外错误抛出。"""
    try:
        status = binding.root.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _root_unavailable(binding.root) from exc
    if stat.S_ISLNK(status.st_mode) or not stat.S_ISDIR(status.st_mode):
        return False
    try:
        canonical = binding.root.resolve(strict=True)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _root_unavailable(binding.root) from exc
    return canonical == binding.root


def binding_hint_is_current(binding: Binding) -> bool:
    """hint 当前是否仍为根下普通文件。普通过期返回 False，意外错误抛出。"""
    if binding.last_page_hint is None:
        return True
    # root 必须先当前
    try:
        root_status = binding.root.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _root_unavailable(binding.root) from exc
    if stat.S_ISLNK(root_status.st_mode) or not stat.S_ISDIR(root_status.st_mode):
        return False
    try:
        canonical = binding.root.resolve(strict=True)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _root_unavailable(binding.root) from exc
    if canonical != binding.root:
        return False
    hint = PurePosixPath(binding.last_page_hint.as_posix())
    try:
        status = _walk_hint_segments(hint, canonical)
    except _HintSymlink:
        return False
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _invalid_hint(binding.last_page_hint) from exc
    return stat.S_ISREG(status.st_mode)
