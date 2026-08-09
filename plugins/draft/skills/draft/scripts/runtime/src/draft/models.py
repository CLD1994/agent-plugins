from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from draft.plan_effects import PlanEffect
from draft.vocabulary import RelationCode, StatusCode

ValidationMode = Literal["registered", "candidate"]


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
    kind: str
    root: Path
    mutations: tuple[FileMutation, ...]
    validation_mode: ValidationMode = "registered"
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
