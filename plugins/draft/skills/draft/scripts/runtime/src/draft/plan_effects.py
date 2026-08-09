"""面向用户的公开文件影响模型。

所有写计划都从业务语义直接生成 `PlanEffect`，公开 `changed_paths` 从这些
效果派生；底层日志 mutation 只是执行与恢复的存储事实，不是公开路径权威来源。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PlanAction(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    MOVE = "move"
    REMOVE = "remove"


@dataclass(frozen=True)
class PlanEffect:
    action: PlanAction
    path: Path | None = None
    from_path: Path | None = None
    to_path: Path | None = None

    def __post_init__(self) -> None:
        if self.action is PlanAction.MOVE:
            if self.path is not None or self.from_path is None or self.to_path is None:
                raise ValueError("move effect requires from_path and to_path only")
        elif self.path is None or self.from_path is not None or self.to_path is not None:
            raise ValueError(f"{self.action.value} effect requires path only")


def public_changed_paths(effects: tuple[PlanEffect, ...]) -> tuple[Path, ...]:
    """从公开效果派生去重路径；MOVE 依次贡献 from 和 to，其余贡献 path。"""
    result: list[Path] = []
    for effect in effects:
        if effect.action is PlanAction.MOVE:
            assert effect.from_path is not None and effect.to_path is not None
            candidates = (effect.from_path, effect.to_path)
        else:
            assert effect.path is not None
            candidates = (effect.path,)
        for path in candidates:
            if path not in result:
                result.append(path)
    return tuple(result)
