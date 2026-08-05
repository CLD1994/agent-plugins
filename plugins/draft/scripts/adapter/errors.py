from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

EXIT_USAGE = 2
EXIT_RUNTIME = 4


class AdapterError(Exception):
    def __init__(
        self,
        code: str,
        *paths: Path,
        details: Sequence[str] = (),
        exit_code: int = EXIT_RUNTIME,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.paths = paths
        self.details = tuple(details)
        self.exit_code = exit_code


def error_payload(error: AdapterError) -> dict[str, object]:
    return {
        "ok": False,
        "result_type": "error",
        "error": {
            "code": error.code,
            "severity": "error",
            "paths": [path.as_posix() for path in error.paths],
            "details": list(error.details),
        },
    }


def render_error(error: AdapterError) -> str:
    parts = ["错误", error.code, *(str(path) for path in error.paths), *error.details]
    return " ".join(parts)
