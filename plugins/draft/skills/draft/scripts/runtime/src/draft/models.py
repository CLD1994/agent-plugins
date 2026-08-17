from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from draft.observations import ObservationManifest
from draft.plan_effects import PlanEffect
from draft.vocabulary import RelationCode, StatusCode


class RootState(StrEnum):
    CANDIDATE = "candidate"
    REGISTERED = "registered"


class OperationKind(StrEnum):
    """操作种类的唯一权威来源：计划、日志与恢复分派共享同一枚举。

    日志只接受这些值；未知 kind 在读取时报 ``operation.journal_corrupt``
    （规格 §5.4、实施计划 Task 7 Step 2）。
    """

    CREATE = "create"
    MOVE = "move"
    PROMOTE = "promote"
    COLLAPSE = "collapse"
    STATUS_SET = "status.set"
    RELATION_ADD = "relation.add"
    RELATION_REMOVE = "relation.remove"
    TRASH = "trash"
    RESTORE = "restore"
    RECOVER = "recover"
    GENERATE_CONCLUSIONS = "generate-conclusions"
    ADOPT = "adopt"


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    REVIEW = "review"


class MutationKind(StrEnum):
    WRITE = "write"
    DELETE = "delete"


class OperationPhase(StrEnum):
    ALLOCATING = "allocating"
    PREPARED = "prepared"
    APPLYING = "applying"
    VALIDATING = "validating"
    ROLLING_BACK = "rolling_back"
    TRASH_RETAINED = "trash_retained"
    TRASH_HANDOFF_PENDING = "trash_handoff_pending"
    TRASH_HANDOFF_SUCCEEDED = "trash_handoff_succeeded"
    TRASH_PURGE_COMMITTED = "trash_purge_committed"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class StatusValue:
    code: StatusCode
    explanation: str | None


@dataclass(frozen=True, order=True)
class SourceSpan:
    start: int
    end: int


@dataclass(frozen=True)
class MarkdownLink:
    label: str
    destination: str
    destination_span: SourceSpan
    whole_span: SourceSpan
    line: int
    is_image: bool = False
    reference_label: str | None = None


@dataclass(frozen=True)
class Relation:
    code: RelationCode
    link: MarkdownLink


@dataclass(frozen=True)
class PageDocument:
    path: Path
    title: str
    status: StatusValue
    relations: tuple[Relation, ...]
    links: tuple[MarkdownLink, ...]
    text: str


@dataclass(frozen=True)
class CheckIssue:
    code: str
    severity: IssueSeverity
    path: Path | None
    line: int | None
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckReport:
    issues: tuple[CheckIssue, ...]

    @property
    def errors(self) -> tuple[CheckIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is IssueSeverity.ERROR)

    @property
    def warnings(self) -> tuple[CheckIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is IssueSeverity.WARNING)

    @property
    def infos(self) -> tuple[CheckIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is IssueSeverity.INFO)

    @property
    def reviews(self) -> tuple[CheckIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is IssueSeverity.REVIEW)


@dataclass(frozen=True)
class LifecycleWarning:
    """Stable, machine-readable lifecycle warning; human text is rendered only at the CLI."""

    code: str
    operation_id: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileMutation:
    path: Path
    kind: MutationKind
    before_sha256: str | None
    after_sha256: str | None
    content: bytes | None


@dataclass(frozen=True)
class OperationPlan:
    kind: OperationKind
    root: Path
    mutations: tuple[FileMutation, ...]
    root_state: RootState
    observations: ObservationManifest
    remove_empty_directories: tuple[Path, ...] = ()
    metadata: dict[str, str] = field(default_factory=lambda: dict[str, str]())
    effects: tuple[PlanEffect, ...] = ()


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    kind: str
    phase: OperationPhase
    changed_paths: tuple[Path, ...]
    warnings: tuple[LifecycleWarning, ...] = ()


@dataclass(frozen=True)
class TrashResult:
    operation_id: str
    phase: OperationPhase
    package: Path
    restore_expires_at: datetime | None = None
    purge_eligible_at: datetime | None = None
    warnings: tuple[LifecycleWarning, ...] = ()
    changed_paths: tuple[Path, ...] = ()
