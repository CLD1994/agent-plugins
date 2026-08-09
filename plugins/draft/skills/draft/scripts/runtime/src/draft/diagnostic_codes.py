from __future__ import annotations

from enum import StrEnum


class CheckCode(StrEnum):
    CANDIDATE_ALREADY_REGISTERED = "candidate.already_registered"
    CANDIDATE_INTERNAL_STATE = "candidate.internal_state"
    FORMAT_MISSING = "format.missing"
    FORMAT_INVALID = "format.invalid"
    FORMAT_UNSUPPORTED = "format.unsupported"
    ROOT_MISSING_FILE = "root.missing_file"
    PAGE_UNMANAGED_PATH = "page.unmanaged_path"
    PAGE_MISSING_TITLE = "page.missing_title"
    PAGE_MISSING_MACHINE_BLOCK = "page.missing_machine_block"
    PAGE_MISSING_STATUS = "page.missing_status"
    PAGE_DUPLICATE_STATUS = "page.duplicate_status"
    PAGE_UNKNOWN_LABEL = "page.unknown_label"
    PAGE_INVALID_STATUS = "page.invalid_status"
    PAGE_INVALID_RELATION = "page.invalid_relation"
    PAGE_PARSE_ERROR = "page.parse_error"
    PAGE_CAPACITY = "page.capacity"
    LINK_MISSING = "link.missing"
    LINK_ESCAPE = "link.escape"
    PATH_SYMLINK_ESCAPE = "path.symlink_escape"
    RELATION_DUPLICATE = "relation.duplicate"
    PARENT_ROOT_DECLARED = "parent.root_declared"
    PARENT_MISSING = "parent.missing"
    PARENT_MULTIPLE = "parent.multiple"
    LAYOUT_PARENT_MISMATCH = "layout.parent_mismatch"
    PARENT_CYCLE = "parent.cycle"
    PARENT_UNREACHABLE = "parent.unreachable"
    PARENT_NAVIGATION_MISSING = "parent.navigation_missing"
    HUB_EMPTY = "hub.empty"
    DEPENDENCY_SELF = "dependency.self"
    DEPENDENCY_CYCLE = "dependency.cycle"
    REPLACEMENT_PAIR_MISSING = "replacement.pair_missing"
    REPLACEMENT_STATUS_MISMATCH = "replacement.status_mismatch"
    REPLACEMENT_CYCLE = "replacement.cycle"
    CHALLENGE_PAIR_MISSING = "challenge.pair_missing"
    OPERATION_JOURNAL_CORRUPT = "operation.journal_corrupt"
    OPERATION_RECOVERY_REQUIRED = "operation.recovery_required"
    OPERATION_ORPHAN_ALLOCATING = "operation.orphan_allocating"
    OPERATION_INCONSISTENT_STATE = "operation.inconsistent_state"
    TRASH_DEADLINES_INVALID = "trash.deadlines_invalid"
    TRASH_PACKAGE_MISSING = "trash.package_missing"
    TRASH_PACKAGE_CORRUPT = "trash.package_corrupt"
    TRASH_RETAINED = "trash.retained"
    TRASH_HANDOFF_PENDING = "trash.handoff_pending"
    TRASH_PENDING_UNEXPLAINED = "trash.pending_unexplained"


class RootMissingKind(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"


class InvalidStatusReason(StrEnum):
    NOT_SINGLE_CODE_SPAN = "not_single_code_span"
    EMPTY_EXPLANATION = "empty_explanation"
    UNKNOWN_PREFIX = "unknown_prefix"


class InvalidRelationReason(StrEnum):
    LINK_CARDINALITY_OR_IMAGE = "link_cardinality_or_image"
    EXTRA_CONTENT = "extra_content"


class ParseErrorReason(StrEnum):
    LINK_DESTINATION_MISSING = "link_destination_missing"
    IMAGE_DESTINATION_MISSING = "image_destination_missing"
    SOURCE_ALIGNMENT_FAILED = "source_alignment_failed"
    LAZY_CONTINUATION = "lazy_continuation"
    MACHINE_BLOCK_MAP_MISSING = "machine_block_map_missing"


class LinkEscapeKind(StrEnum):
    DESTINATION = "destination"
    MANAGED_PATH = "managed_path"


class ReplacementStatusReason(StrEnum):
    RELATION_REQUIRES_STATUS = "relation_requires_status"
    STATUS_REQUIRES_RELATION = "status_requires_relation"
