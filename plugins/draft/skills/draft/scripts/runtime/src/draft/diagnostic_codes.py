from __future__ import annotations

from enum import StrEnum


class RootCode(StrEnum):
    INVALID = "root.invalid"
    NOT_FOUND = "root.not_found"
    AMBIGUOUS = "root.ambiguous"


class QueryCode(StrEnum):
    INCOMPLETE = "query.incomplete"


class CheckCode(StrEnum):
    FORMAT_MISSING = "format.missing"
    FORMAT_INVALID = "format.invalid"
    FORMAT_UNSUPPORTED = "format.unsupported"
    INTERNAL_UNEXPECTED_ENTRY = "internal.unexpected_entry"
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
    PAGE_FRONTMATTER_INVALID = "page.frontmatter_invalid"
    PAGE_STATUS_INVALID = "page.status_invalid"
    PAGE_NOTE_INVALID = "page.note_invalid"
    PAGE_PARENT_INVALID = "page.parent_invalid"
    RELATION_DECLARATION_INVALID = "relation.declaration_invalid"
    LINK_MISSING = "link.missing"
    LINK_INVALID = "link.invalid"
    LINK_UNAVAILABLE = "link.unavailable"
    LINK_ESCAPE = "link.escape"
    PATH_SYMLINK_ESCAPE = "path.symlink_escape"
    PATH_INTERNAL = "path.internal"
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
    OPERATION_JOURNAL_VERSION_UNSUPPORTED = "operation.journal_version_unsupported"
    OPERATION_RECOVERY_REQUIRED = "operation.recovery_required"
    OPERATION_ORPHAN_ALLOCATING = "operation.orphan_allocating"
    OPERATION_INCONSISTENT_STATE = "operation.inconsistent_state"
    OPERATION_DURABILITY_FAILED = "operation.durability_failed"
    TRASH_DEADLINES_INVALID = "trash.deadlines_invalid"
    TRASH_PACKAGE_MISSING = "trash.package_missing"
    TRASH_PACKAGE_CORRUPT = "trash.package_corrupt"
    TRASH_RETAINED = "trash.retained"
    TRASH_HANDOFF_PENDING = "trash.handoff_pending"
    TRASH_PENDING_UNEXPLAINED = "trash.pending_unexplained"
    CONCLUSIONS_UNEXPECTED_FILE = "conclusions.unexpected_file"
    CONCLUSIONS_SHAPE_INVALID = "conclusions.shape_invalid"
    CONCLUSIONS_NAVIGATION_INVALID = "conclusions.navigation_invalid"


# 稳定协议码的单一权威（spec §10/§11）：降级精确匹配与 warning 依赖这些字面量一致。
# 刻意不作为 CheckCode 枚举成员：这些码在 src 各处仍有大量裸 raise 字面量，进入
# CheckCode 会触发 test_registered_diagnostic_codes_have_one_literal_owner 的全量
# 替换要求；本文件是它们唯一的定义处，消费方从这里导入。
OPERATION_INDEX_UPDATE_FAILED = "operation.index_update_failed"
OPERATION_EXTERNAL_CHANGE = "operation.external_change"
OPERATION_PLAN_INVALID = "operation.plan_invalid"
LINK_INDEX_REBUILD_REQUIRED = "link_index.rebuild_required"
LINK_INDEX_REBUILD_FLAG_UPDATE_FAILED = "link_index.rebuild_flag_update_failed"


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


class FrontmatterReason(StrEnum):
    MISSING = "missing"
    DELIMITER_INVALID = "delimiter_invalid"
    YAML_INVALID = "yaml_invalid"
    NOT_MAPPING = "not_mapping"
    DUPLICATE_KEY = "duplicate_key"
    UNSUPPORTED_FEATURE = "unsupported_feature"
    UNKNOWN_KEY = "unknown_key"
    INVALID_VALUE_TYPE = "invalid_value_type"


class PageStatusReason(StrEnum):
    MISSING = "missing"
    INVALID_TYPE = "invalid_type"
    INVALID_VALUE = "invalid_value"


class PageNoteReason(StrEnum):
    EMPTY = "empty"
    MULTILINE = "multiline"
    CONTROL_CHARACTER = "control_character"


class PageParentReason(StrEnum):
    UNEXPECTED = "unexpected"
    MISSING = "missing"
    INVALID_PATH = "invalid_path"


class RelationDeclarationReason(StrEnum):
    CONTAINER_EMPTY = "container_empty"
    MEMBER_SHAPE = "member_shape"
    TYPE_UNKNOWN = "type_unknown"
    TARGET_PATH_INVALID = "target_path_invalid"


HEAD_DIAGNOSTIC_CODES: frozenset[CheckCode] = frozenset(
    {
        CheckCode.PAGE_FRONTMATTER_INVALID,
        CheckCode.PAGE_STATUS_INVALID,
        CheckCode.PAGE_NOTE_INVALID,
        CheckCode.PAGE_PARENT_INVALID,
        CheckCode.RELATION_DECLARATION_INVALID,
    }
)
