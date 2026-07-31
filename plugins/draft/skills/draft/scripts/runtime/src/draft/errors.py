from __future__ import annotations

from pathlib import Path


class DraftError(Exception):
    def __init__(
        self,
        code: str,
        *paths: Path,
        details: tuple[str, ...] = (),
        line: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.paths = paths
        self.details = details
        self.line = line
