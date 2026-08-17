"""AdapterError / error_payload / render_error 单元测试：纯值函数，无文件系统。"""

from __future__ import annotations

from pathlib import Path

import pytest

from adapter.errors import EXIT_RUNTIME, EXIT_USAGE, AdapterError, error_payload, render_error

# ---------------------------------------------------------------------------
# 构造与字段不变量
# ---------------------------------------------------------------------------


def test_adapter_error_stores_code_paths_details_and_exit_code() -> None:
    error = AdapterError(
        "binding.invalid",
        Path("/data/b.json"),
        details=("a", "b"),
        exit_code=EXIT_RUNTIME,
    )
    assert error.code == "binding.invalid"
    assert error.paths == (Path("/data/b.json"),)
    assert error.details == ("a", "b")
    assert error.exit_code == EXIT_RUNTIME


def test_adapter_error_defaults_to_runtime_exit_and_empty_paths_details() -> None:
    error = AdapterError("plugin.context_invalid")
    assert error.paths == ()
    assert error.details == ()
    assert error.exit_code == EXIT_RUNTIME


def test_adapter_error_accepts_multiple_paths_as_varargs() -> None:
    error = AdapterError("code", Path("/a"), Path("/b"), Path("/c"))
    assert error.paths == (Path("/a"), Path("/b"), Path("/c"))


def test_exit_code_constants_are_stable() -> None:
    assert EXIT_USAGE == 2
    assert EXIT_RUNTIME == 4


def test_adapter_error_exit_usage_overrides_default() -> None:
    assert AdapterError("binding.root_unavailable", exit_code=EXIT_USAGE).exit_code == 2


# ---------------------------------------------------------------------------
# add_detail：追加、保持 tuple、主错误字段不变
# ---------------------------------------------------------------------------


def test_add_detail_appends_in_order_and_keeps_tuple() -> None:
    error = AdapterError("plugin.context_invalid", Path("/x.json"))
    error.add_detail("cleanup.close_failed")
    error.add_detail("cleanup.unlink_failed")
    assert error.details == ("cleanup.close_failed", "cleanup.unlink_failed")
    assert isinstance(error.details, tuple)


def test_add_detail_preserves_code_paths_and_exit_code() -> None:
    error = AdapterError("plugin.context_invalid", Path("/x.json"), exit_code=EXIT_RUNTIME)
    error.add_detail("cleanup.unlink_failed")
    assert error.code == "plugin.context_invalid"
    assert error.paths == (Path("/x.json"),)
    assert error.exit_code == EXIT_RUNTIME


# ---------------------------------------------------------------------------
# error_payload：JSON 结构与 POSIX 路径
# ---------------------------------------------------------------------------


def test_error_payload_serializes_full_structure_with_posix_paths() -> None:
    error = AdapterError(
        "plugin.context_invalid",
        Path("/tmp/bridge.json"),
        details=("unsafe mode",),
    )
    assert error_payload(error) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "plugin.context_invalid",
            "severity": "error",
            "paths": ["/tmp/bridge.json"],
            "details": ["unsafe mode"],
        },
    }


def test_error_payload_empty_paths_and_details() -> None:
    error = AdapterError("hook.invalid_arguments")
    assert error_payload(error) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "hook.invalid_arguments",
            "severity": "error",
            "paths": [],
            "details": [],
        },
    }


def test_error_payload_includes_cleanup_details_in_order() -> None:
    error = AdapterError("binding.data_dir_unavailable", Path("/d/b.json"))
    error.add_detail("cleanup.close_failed")
    error.add_detail("cleanup.unlink_failed")
    assert error_payload(error) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "binding.data_dir_unavailable",
            "severity": "error",
            "paths": ["/d/b.json"],
            "details": ["cleanup.close_failed", "cleanup.unlink_failed"],
        },
    }


def test_error_payload_paths_use_posix_form() -> None:
    error = AdapterError("code", Path("/a/b/c.json"))
    assert error_payload(error) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "code",
            "severity": "error",
            "paths": ["/a/b/c.json"],
            "details": [],
        },
    }


# ---------------------------------------------------------------------------
# render_error：文本渲染
# ---------------------------------------------------------------------------


def test_render_error_joins_chinese_prefix_code_paths_and_details() -> None:
    error = AdapterError(
        "plugin.context_invalid", Path("/tmp/bridge.json"), details=("unsafe mode",)
    )
    assert render_error(error) == "错误 plugin.context_invalid /tmp/bridge.json unsafe mode"


def test_render_error_without_paths_or_details() -> None:
    assert render_error(AdapterError("hook.invalid_arguments")) == "错误 hook.invalid_arguments"


def test_render_error_preserves_cleanup_details_in_order() -> None:
    error = AdapterError("plugin.context_invalid", Path("/x.json"))
    error.add_detail("cleanup.unlock_failed")
    error.add_detail("cleanup.close_failed")
    assert render_error(error) == (
        "错误 plugin.context_invalid /x.json cleanup.unlock_failed cleanup.close_failed"
    )


# ---------------------------------------------------------------------------
# 边界：空值、控制字符、孤立代理码位、长度
# ---------------------------------------------------------------------------


def test_render_error_preserves_control_characters_literally() -> None:
    error = AdapterError("code\tbad", Path("/x"), details=("line\nbreak", "\x00null"))
    assert render_error(error) == "错误 code\tbad /x line\nbreak \x00null"


def test_error_payload_preserves_control_characters_in_strings() -> None:
    error = AdapterError("code\tbad", Path("/x\t"), details=("a\nb",))
    assert error_payload(error) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "code\tbad",
            "severity": "error",
            "paths": ["/x\t"],
            "details": ["a\nb"],
        },
    }


def test_render_error_preserves_lone_surrogate_path_without_sanitizing() -> None:
    # 错误层不做 UTF-8 校验：孤立代理码位原样渲染，不丢弃不转义。
    error = AdapterError("plugin.context_invalid", Path("/tmp/\ud800"))
    assert render_error(error) == "错误 plugin.context_invalid /tmp/\ud800"


def test_error_payload_preserves_lone_surrogate_path() -> None:
    error = AdapterError("plugin.context_invalid", Path("/tmp/\ud800"))
    assert error_payload(error) == {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": "plugin.context_invalid",
            "severity": "error",
            "paths": ["/tmp/\ud800"],
            "details": [],
        },
    }


def test_render_error_handles_long_detail_string() -> None:
    long_detail = "x" * 10_000
    error = AdapterError("code", details=(long_detail,))
    assert render_error(error) == f"错误 code {long_detail}"


def test_error_payload_handles_many_details() -> None:
    details = [f"d{i}" for i in range(500)]
    error = AdapterError("code", details=details)
    expected: dict[str, object] = {
        "ok": False,
        "result_type": "error",
        "error": {"code": "code", "severity": "error", "paths": [], "details": details},
    }
    assert error_payload(error) == expected


@pytest.mark.parametrize(
    ("code", "paths", "details"),
    [
        ("", (), ()),
        ("code", (Path("/p"),), ()),
        ("code", (), ("d",)),
        ("code", (Path("/p"),), ("d1", "d2")),
    ],
)
def test_render_error_and_payload_agree_on_field_presence(
    code: str, paths: tuple[Path, ...], details: tuple[str, ...]
) -> None:
    error = AdapterError(code, *paths, details=details)
    text = render_error(error)
    # 文本与 JSON 都包含 code、每个 path 与每个 detail
    assert code in text
    for path in paths:
        assert path.as_posix() in text
    for detail in details:
        assert detail in text
    expected: dict[str, object] = {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": code,
            "severity": "error",
            "paths": [p.as_posix() for p in paths],
            "details": list(details),
        },
    }
    assert error_payload(error) == expected
