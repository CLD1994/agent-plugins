# Draft 核心与插件适配层拆分实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Draft 领域 runtime 改为通过本地路径式 `uvx` 独立运行，并把 bridge、binding、SessionStart 和宿主 CLI 完整迁移到插件根的标准库适配层。

**Architecture:** `plugins/draft/skills/draft/scripts/runtime` 只保留 `draft-runtime` 包及领域测试，Agent 以 `uvx --isolated --from "<skill_root>/scripts/runtime" draft ...` 执行核心命令。`plugins/draft/scripts/draft.py` 是独立的 PEP 723 插件入口，调用 `adapter/` 中的 bridge、binding 和 Hook 实现；适配层只使用标准库、不导入或执行核心，所有插件状态仍归宿主 `data_dir`。

**Tech Stack:** Python `>=3.12,<3.15`、PEP 723、uv/uvx、标准库 `argparse`/`dataclasses`/`hashlib`/`json`/`pathlib`/文件锁、pytest、pyright strict、ruff。

## Global Constraints

- 规范基线是 `docs/superpowers/specs/2026-08-05-draft-core-plugin-separation-design.md`（提交 `3b535b7`）；实现不得恢复被该规格取代的 launcher、bridge 或 binding 结论。
- `plugins/draft/skills/draft/` 继续是普通目录；不得创建 submodule、`.gitmodules`、生成式发布树或第二发布仓库。
- 不发布 Python 包、不新增短命令 `uvx draft`，也不新增 `inspect` CLI；路径式本地 runtime 是唯一核心来源。
- 核心不得引用宿主名、manifest、marketplace、bridge、binding、SessionStart、`PLUGIN_*`、`CLAUDE_PLUGIN_*`、`DRAFT_DATA` 或 `DRAFT_SKILL_PATH`。
- 插件适配层只依赖 Python 标准库；不得 `import draft`、调用核心 CLI、检查 `.draft/`，或复制任何 Draft 格式判断。
- 插件入口直接拥有参数解析和分派；不得增加只被它消费的 `adapter/cli.py`。
- binding 只表达不透明 `session_id` 到现存目录的关联；bind 不要求目录已 `init`/`adopt`，不运行 `draft check`。
- binding 严格只有 `session_id`、`root`、`last_page_hint` 三个字段；不保存 skill 名称或摘要。
- bridge 和 binding 只写宿主 `data_dir` 或当前用户安全运行目录；uv 缓存不得包含 Draft 业务状态。
- 核心命令固定为 `uvx --isolated --from "<skill_root>/scripts/runtime" draft ...`；保留 `--isolated`，不增加 `--no-cache` 或 `--no-env-file`。
- runtime 运行依赖固定为 `markdown-it-py>=4.2,<4.3`、`Send2Trash>=2.1,<2.2`，构建依赖固定为 `hatchling>=1.31,<1.32`。
- `runtime/uv.lock` 只服务开发与质量门禁；最终用户 `uvx` 不消费它。
- 本次发布把插件 manifest 和 runtime 分别提升到 `0.3.0`；测试不得继续断言二者永远相同。
- Hook 协议、bridge/binding/layout/log 错误必须产生模型可见诊断并返回 `0`；普通插件命令的用户输入错误返回 `2`，上下文或持久化错误返回 `4`。
- 每个任务先增加或迁移失败测试并确认红灯，再做最小实现；不得删除未被新协议明确取代的安全矩阵测试。
- 计划编写时工作区已有用户改动：`plugins/draft/skills/draft/scripts/ruff.toml` 未跟踪，runtime `pyproject.toml` 已移除内嵌 Ruff 配置。执行时把这两项作为 Task 6 的目标状态处理，不提前覆盖或丢弃。

---

## File Structure

### 插件适配层

- Create: `plugins/draft/scripts/draft.py`
  - PEP 723 稳定入口；只做参数解析、bridge 上下文取得、命令分派和输出格式选择。
- Create: `plugins/draft/scripts/adapter/__init__.py`
  - 标记标准库适配包，不导出核心能力。
- Create: `plugins/draft/scripts/adapter/errors.py`
  - 定义 `AdapterError`、退出码和普通/JSON 错误载荷。
- Create: `plugins/draft/scripts/adapter/bridge.py`
  - 从旧 launcher 提取两字段 bridge、安全运行目录、锁、原子写、冲突哨兵和 reset。
- Create: `plugins/draft/scripts/adapter/bindings.py`
  - 定义三字段 `Binding`、根/提示验证、持久格式、原子覆盖、加载和幂等解除。
- Create: `plugins/draft/scripts/adapter/session_hook.py`
  - 负责宿主变量归一化、插件布局验证、SessionStart 载荷、日志和模型可见信封。
- Create: `plugins/draft/scripts/tests/conftest.py`
  - 只把 `plugins/draft/scripts` 加入测试导入路径，提供复制插件与安全运行目录夹具。
- Create: `plugins/draft/scripts/tests/test_bridge.py`
- Create: `plugins/draft/scripts/tests/test_bindings.py`
- Create: `plugins/draft/scripts/tests/test_session_hook.py`
- Create: `plugins/draft/scripts/tests/test_cli.py`
- Create: `plugins/draft/scripts/tests/test_distribution.py`

### 核心 runtime

- Delete: `plugins/draft/skills/draft/scripts/draft.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/bindings.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/session_hook.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py`
- Delete plugin-only portions from: `plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py`
- Rewrite: `plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/test_dependency_bounds.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Modify: `plugins/draft/skills/draft/scripts/runtime/uv.lock`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/constraints/minimum-build.txt`
- Create or retain: `plugins/draft/skills/draft/scripts/ruff.toml`

### 接线与文档

- Modify: `plugins/draft/hooks/codex-hooks.json`
- Modify: `plugins/draft/hooks/claude-hooks.json`
- Modify: `plugins/draft/.codex-plugin/plugin.json`
- Modify: `plugins/draft/.claude-plugin/plugin.json`
- Modify: `plugins/draft/skills/draft/SKILL.md`
- Modify: `plugins/draft/skills/draft/references/draft-format.md`
- Delete: `plugins/draft/skills/draft/references/platform-setup.md`
- Create: `plugins/draft/references/platform-setup.md`
- Modify: `docs/design.md`

---

### Task 1: 提取标准库错误模型与安全 bridge

**Files:**
- Create: `plugins/draft/scripts/adapter/__init__.py`
- Create: `plugins/draft/scripts/adapter/errors.py`
- Create: `plugins/draft/scripts/adapter/bridge.py`
- Create: `plugins/draft/scripts/tests/conftest.py`
- Create: `plugins/draft/scripts/tests/test_bridge.py`

**Interfaces:**
- Produces: `AdapterError(code: str, *paths: Path, details: Sequence[str] = (), exit_code: int = 4)`。
- Produces: `EXIT_USAGE = 2`、`EXIT_RUNTIME = 4`、`error_payload(error) -> dict[str, object]`、`render_error(error) -> str`。
- Produces: `PluginContext(data_dir: Path)`、`BridgePaths(json: Path, conflict: Path, lock: Path)`。
- Produces: `publish_context(entry_path, data_dir, runtime_dir=None) -> PluginContext`、`load_context(...) -> PluginContext | None`、`reset_context(...) -> None`。
- Later tasks use the plugin entry `plugins/draft/scripts/draft.py` as `installation_key` input; the deleted core launcher path must never be used as the new key.

- [x] **Step 1: 建立适配层测试导入边界并迁移 bridge 安全矩阵**

Create `conftest.py` with the exact import boundary:

```python
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
```

Create `test_bridge.py` by moving the complete state matrix from current `test_plugin_context.py`, changing only imports and names:

```python
from adapter.bridge import (
    BridgePaths,
    PluginContext,
    bridge_paths,
    installation_key,
    load_context,
    per_user_runtime_dir,
    publish_context,
    reset_context,
)
from adapter.errors import AdapterError
```

The migrated tests must still cover: exact two-field schema; missing/valid/invalid/conflict states; boolean/unknown schema versions; relative/missing/file `data_dir`; symlink, non-regular, wrong-owner and group-writable bridge objects; secure/fallback runtime directory; same/different-data concurrent publishers; every lock/open/fstat/acquire/release/close, temporary-file, fsync, replace, sentinel and unlink failure; preservation of a primary error when cleanup also fails.

Keep the state distinctions exact: missing `load_context` returns `None`; valid returns `PluginContext`; unsafe/invalid bridge state raises `plugin.context_invalid`; a conflict sentinel or a second publisher with a different canonical data directory raises `plugin.context_conflict`. `reset_context` removes only the current entry's bridge JSON and conflict sentinel (the lock file is permanently retained as the shared synchronization anchor), and remains idempotent when all are absent.

Add this installation-identity assertion so the new key cannot silently remain tied to the skill launcher:

```python
def test_installation_key_uses_plugin_entry(tmp_path: Path) -> None:
    entry = tmp_path / "plugin/scripts/draft.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("# plugin entry\n", encoding="utf-8")
    expected = hashlib.sha256(str(entry.resolve()).encode("utf-8")).hexdigest()
    assert installation_key(entry) == expected
```

- [x] **Step 2: 写错误载荷与退出码失败测试**

```python
def test_adapter_error_has_stable_text_and_json() -> None:
    error = AdapterError(
        "plugin.context_invalid",
        Path("/tmp/bridge.json"),
        details=("unsafe mode",),
    )
    assert error.exit_code == 4
    assert render_error(error) == (
        "错误 plugin.context_invalid /tmp/bridge.json unsafe mode"
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

def test_usage_error_keeps_exit_two() -> None:
    assert AdapterError("binding.root_unavailable", exit_code=EXIT_USAGE).exit_code == 2
```

- [x] **Step 3: 运行新测试并确认缺少适配模块**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_bridge.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'adapter'`.

- [x] **Step 4: 实现错误模型**

```python
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
```

- [x] **Step 5: 从旧 launcher 原样提取 bridge 的安全边界**

Move these exact definitions from `plugins/draft/skills/draft/scripts/draft.py` into `adapter/bridge.py`:

```text
SCHEMA_VERSION, _EXPECTED_KEYS, PluginContext, BridgePaths,
installation_key, per_user_runtime_dir, bridge_paths,
_verify_owner_and_mode, _verify_bridge_dir, _verify_regular_file,
_lock_os, _unlock_os, _bridge_lock, _fsync_directory,
_write_json_atomic, _create_sentinel, _canonical_data_dir,
_parse_data_dir, _lstat_bridge_dir,
publish_context, load_context, reset_context
```

Make only these semantic substitutions:

```python
# old
raise LauncherError("plugin.context_invalid", path)
# new
raise AdapterError("plugin.context_invalid", path)

# old installation identity
skill/scripts/draft.py
# new installation identity supplied by callers
plugin/scripts/draft.py
```

Do not weaken `lstat`, ownership/mode checks, `O_NOFOLLOW`, locking, atomic replacement, conflict sentinel, directory fsync or cleanup-note behavior.

- [x] **Step 6: 运行 bridge 定向测试**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_bridge.py -q
```

Expected: all bridge/error tests pass; concurrent tests finish without timeout.

- [x] **Step 7: 提交 bridge 提取**

```bash
git add plugins/draft/scripts/adapter plugins/draft/scripts/tests/conftest.py \
  plugins/draft/scripts/tests/test_bridge.py
git commit -m "feat(draft): extract plugin bridge adapter"
```

---

### Task 2: 实现不验证 Draft 的三字段 binding

**Files:**
- Create: `plugins/draft/scripts/adapter/bindings.py`
- Create: `plugins/draft/scripts/tests/test_bindings.py`

**Interfaces:**
- Consumes: `AdapterError`、`EXIT_USAGE`、`EXIT_RUNTIME`。
- Produces: `Binding(session_id: str, root: Path, last_page_hint: Path | None)`。
- Produces: `binding_key(session_id: str) -> str`、`binding_path(data_dir, session_id) -> Path`。
- Produces: `bind(data_dir: Path, binding: Binding) -> Binding`、`load_binding(...) -> Binding | None`、`unbind(...) -> bool`。
- Produces: `binding_root_is_current(binding: Binding) -> bool`、`binding_hint_is_current(binding: Binding) -> bool`，供 Hook 复用同一路径安全规则。
- Root validity is filesystem validity only; no later task may add `check_draft`, `load_graph`, `.draft/` or skill digest checks.

- [x] **Step 1: 写三字段 schema、普通目录和不透明 session 测试**

```python
def test_bind_accepts_unregistered_directory_and_writes_exact_schema(
    tmp_path: Path,
) -> None:
    data_dir = (tmp_path / "data").resolve()
    data_dir.mkdir()
    root = tmp_path / "ordinary"
    root.mkdir()

    result = bind(data_dir, Binding("../会话/\\opaque", root, None))

    assert result.root == root.resolve()
    payload = json.loads(binding_path(data_dir, result.session_id).read_text("utf-8"))
    assert payload == {
        "session_id": "../会话/\\opaque",
        "root": str(root.resolve()),
        "last_page_hint": None,
    }
    assert set(payload) == {"session_id", "root", "last_page_hint"}

def test_binding_key_never_uses_session_as_a_path(tmp_path: Path) -> None:
    session_id = "../斜线/和\\unicode"
    data_dir = tmp_path / "data"
    assert binding_path(data_dir, session_id) == (
        data_dir / "bindings" / f"{hashlib.sha256(session_id.encode()).hexdigest()}.json"
    )
```

- [x] **Step 2: 写根与页面提示状态矩阵**

Parameterize exact invalid roots: missing path, regular file, root itself a symlink, and an inaccessible/non-canonical `data_dir`. All root failures are `binding.root_unavailable` with exit `2`; data directory failures are `binding.data_dir_unavailable` with exit `4`.

```python
@pytest.mark.parametrize(
    "hint",
    ["", "   ", "/etc/passwd", "../outside.md", "pages/../../x.md", "C:\\x.md", "pages/a.txt"],
)
def test_hint_rejects_empty_absolute_escape_and_non_markdown(
    workspace: tuple[Path, Path], hint: str
) -> None:
    data_dir, root = workspace
    with pytest.raises(AdapterError, match="binding.invalid_hint") as caught:
        bind(data_dir, Binding("s", root, Path(hint)))
    assert caught.value.exit_code == 2

def test_hint_requires_regular_file_without_symlink_segments(
    workspace: tuple[Path, Path]
) -> None:
    data_dir, root = workspace
    (root / "pages").mkdir()
    target = root / "pages/a.md"
    target.write_text("ordinary markdown\n", encoding="utf-8")
    assert bind(data_dir, Binding("ok", root, Path("pages/a.md"))).last_page_hint == Path(
        "pages/a.md"
    )
    target.unlink()
    target.symlink_to(root / "real.md")
    with pytest.raises(AdapterError, match="binding.invalid_hint"):
        bind(data_dir, Binding("link", root, Path("pages/a.md")))
```

Also cover a symlink in an intermediate hint segment and both POSIX/Windows `..` parsing.

- [x] **Step 3: 写严格加载、原子覆盖和直接解除测试**

```python
def test_old_five_field_payload_is_invalid_but_rebind_overwrites_it(
    workspace: tuple[Path, Path]
) -> None:
    data_dir, root = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text(json.dumps({
        "session_id": "s", "root": str(root), "last_page_hint": None,
        "skill_name": "draft", "skill_digest": "old",
    }), encoding="utf-8")
    with pytest.raises(AdapterError, match="binding.invalid"):
        load_binding(data_dir, "s")
    bind(data_dir, Binding("s", root, None))
    assert load_binding(data_dir, "s") == Binding("s", root.resolve(), None)

def test_unbind_removes_malformed_regular_payload_without_parsing(
    workspace: tuple[Path, Path]
) -> None:
    data_dir, _ = workspace
    target = binding_path(data_dir, "s")
    target.parent.mkdir()
    target.write_text("{broken", encoding="utf-8")
    assert unbind(data_dir, "s") is True
    assert unbind(data_dir, "s") is False
```

Add exact failures for missing/extra fields, wrong field types, relative or lexically non-canonical persisted roots, lexically invalid persisted hints, session mismatch, corrupt JSON, a symlink/non-directory `bindings/` container, symlink/non-regular record, failed `mkdir`/temporary write/fsync/replace/unlink, and cleanup failure preserving the primary error.

- [x] **Step 4: 运行新测试并确认模块不存在**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_bindings.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'adapter.bindings'`.

- [x] **Step 5: 实现三字段模型和验证边界**

Start with these exact declarations:

```python
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
```

Implement `_require_data_dir` with absolute/canonical/existing/directory/writable checks and `lstat` rejection of a symlink. Implement `_canonical_root` by expanding `~`, requiring the supplied root itself to be a non-symlink directory, then resolving it strictly.

Split hint handling into one lexical parser and one current-filesystem check. The lexical parser uses both `PurePosixPath` and `PureWindowsPath`: reject empty, absolute, any `..` part under either parser, or a suffix other than `.md`. The current check walks every concrete segment with `lstat`, rejects symlinks, and requires the target to be a regular file under the canonical root. Bind requires both; load requires only lexical validity so a once-valid hint can later become stale without corrupting the binding.

When loading, require the persisted root to be a nonempty absolute, lexically normalized path without resolving it or requiring it to exist. This preserves the distinct `root missing later` state. Implement `binding_root_is_current` with `lstat`, non-symlink directory and strict canonical-equality checks; implement `binding_hint_is_current` by reusing the same segment-walk helper. Both predicates return `False` for an ordinary stale filesystem state but convert unexpected access errors at the narrow operation into `AdapterError` so Hook can diagnose them.

Create or open `bindings/` only after `lstat` proves it is a real directory directly below the canonical data directory; reject a symlink or non-directory as `binding.data_dir_unavailable`. Implement same-directory `mkstemp` + `fsync` + `os.replace` + best-effort directory fsync for bind. A pre-existing safe regular file may be atomically overwritten without parsing, which is how explicit rebind repairs an old schema. Never follow or repair a pre-existing symlink/non-regular target.

Implement `load_binding` as the only parser of an existing payload and distinguish missing, invalid and session mismatch. Implement `unbind` by hashing the session, `lstat`-checking the target, unlinking an existing regular file without JSON parsing, returning `False` only for `FileNotFoundError`, and rejecting a symlink or non-regular object as `binding.invalid`.

Map unsafe/inaccessible data or binding-container creation, temporary write, file `fsync`, replace and unlink failures to `binding.data_dir_unavailable`; map an unsafe target object, target read failure, invalid JSON/schema and lookup failure after a valid container to `binding.invalid`. Preserve the original exception chain. Directory `fsync` remains best-effort; temporary cleanup failure is appended to an existing primary error instead of replacing it.

- [x] **Step 6: 运行 binding 定向测试**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_bindings.py -q
```

Expected: all tests pass; no test fixture calls `draft.lifecycle.initialize` or creates `.draft/`.

- [x] **Step 7: 静态证明适配层未导入核心**

Run:

```bash
if rg -n '^(from|import) draft([ .]|$)' plugins/draft/scripts/adapter; then
  exit 1
fi
```

Expected: no matches and exit `0`.

- [x] **Step 8: 提交 binding 协议**

```bash
git add plugins/draft/scripts/adapter/bindings.py \
  plugins/draft/scripts/tests/test_bindings.py
git commit -m "feat(draft): add plugin binding protocol"
```

---

### Task 3: 实现不调用核心的 SessionStart 协议

**Files:**
- Create: `plugins/draft/scripts/adapter/session_hook.py`
- Create: `plugins/draft/scripts/tests/test_session_hook.py`

**Interfaces:**
- Consumes: `publish_context`、`load_binding`、`AdapterError`；不得消费 `draft` 包或子进程执行器。
- Produces: `HostContext(host, plugin_root, data_dir, entry_path, skill_path)`。
- Produces: `resolve_host_context(host, entry_path, environ) -> HostContext`、`read_hook_payload(stream, host) -> HookPayload`、`run_session_start(host, *, entry_path, environ, stdin, runtime_dir=None) -> int`。
- Invariant: `run_session_start` 可执行后，所有参数、宿主环境、布局、bridge、载荷、binding 和日志失败都产生模型可见诊断并返回 `0`。

- [x] **Step 1: 写宿主选择、布局和执行顺序失败测试**

Create a copied-plugin fixture with the exact target layout:

```text
<plugin>/scripts/draft.py
<plugin>/skills/draft/SKILL.md
<plugin-data>/
```

Parameterize `HOST_ENV` and legal sources:

```python
HOST_ENV = {
    "codex": ("PLUGIN_ROOT", "PLUGIN_DATA"),
    "claude": ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA"),
}
HOST_SOURCES = {
    "codex": ("startup", "resume", "clear", "compact"),
    "claude": ("startup", "resume", "clear", "compact", "fork"),
}
```

Tests must prove that each host reads only its own pair even when the other pair contains decoys; unknown hosts do not probe either pair; missing variables, relative/non-canonical `data_dir`, a symlink plugin root, root/entry mismatch, missing/non-regular/symlink `SKILL.md`, and an entry outside `<plugin>/scripts/draft.py` render distinct stable codes.

Add a spy around `publish_context` and assert the order:

```python
def test_bridge_is_published_before_payload_validation(setup: Setup) -> None:
    code, output = run_hook(setup, host="codex", stdin="{bad json")
    assert code == 0
    assert "hook.stdin_invalid" in output
    assert load_context(setup.entry, setup.runtime_dir) == PluginContext(setup.data_dir)
```

Layout or host-context failure occurs before bridge publication. Payload validation, including missing session, occurs after publication.

- [x] **Step 2: 写载荷与所有字符串 session 的协议矩阵**

Cover the 64 KiB boundary, oversized input, an `OSError` from `stream.read`, invalid JSON, non-object JSON, wrong event name, missing/non-string `session_id`, non-string source, and a source not legal for the selected host. Every failure must print exactly one diagnostic envelope containing its code and return `0`; none may be silently logged and discarded.

Accept every string session without trimming or normalization:

```python
@pytest.mark.parametrize("session", ["", "abc123", "会话-☃-123", "a b\tc", "../x\\y"])
def test_every_string_is_an_opaque_session_identity(setup: Setup, session: str) -> None:
    code, output = run_hook(setup, session=session)
    assert code == 0
    assert json.dumps(session, ensure_ascii=False) in output
    assert "SESSION_ID" in output
```

The raw value is shown as a JSON string on its own line. Command examples keep the literal placeholder `SESSION_ID`; they never interpolate the session into a path or shell fragment.

- [x] **Step 3: 写未绑定、恢复和损坏状态的信封测试**

For an unbound session require byte-identical output containing:

```text
Draft 会话未绑定
- 宿主 session_id：<JSON string>
- 插件入口：<canonical absolute plugin entry>
- 绑定格式：uv run '<plugin entry>' bind ROOT --session SESSION_ID [--last-page-hint PAGE]
- 解除格式：uv run '<plugin entry>' unbind --session SESSION_ID
```

For a valid binding require root, optional hint, source, canonical `SKILL.md`, canonical plugin entry, a requirement to reread the complete current skill, and this exact core command shape before modification:

```text
uvx --isolated --from '<skill root>/scripts/runtime' draft check '<bound root>'
```

Add state tests for:

- valid root with/without hint;
- hint target later missing, non-regular, or reached through a newly introduced symlink: omit only the hint and retain the root binding;
- root later missing, non-directory, or replaced by a symlink: render `binding.root_missing` and require explicit rebind;
- malformed/old-schema binding: render `binding.invalid`;
- payload session mismatch: render `binding.session_mismatch`;
- repeated invocation with identical files and payload: stdout is byte-identical;
- skill contents changed between invocations: binding remains valid and output still points to the current `SKILL.md`; no digest branch exists.

Paths in command examples must be POSIX-shell quoted with one shared helper based on `shlex.quote`; tests include spaces and a single quote in plugin/root paths.

- [x] **Step 4: 写 Hook 失败归一化和日志降级测试**

Parameterize every `AdapterError` from host selection, layout, bridge publication, binding load and logging setup. Assert:

```python
assert code == 0
assert captured.err == ""
assert output.count("Draft 插件诊断") == 1
assert expected_code in output
```

Patch `logs.mkdir`, log open and log write separately to raise `OSError`, and supply a pre-existing symlink/non-directory `logs` object; each is best-effort, preserves the already selected session fact/rebind/binding diagnostic, appends one `hook.log_unavailable` model-visible diagnostic, and still returns `0`. Assert `subprocess`, `draft`, `.draft`, `check_draft` and `load_graph` are never imported or invoked.

- [x] **Step 5: 运行新测试并确认模块不存在**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_session_hook.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'adapter.session_hook'`.

- [x] **Step 6: 实现宿主上下文、载荷、信封和 Hook 边界**

Start with these declarations:

```python
MAX_STDIN = 64 * 1024

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
```

`resolve_host_context` selects exactly one `HOST_ENV` pair, `lstat`-validates the supplied entry and declared plugin root without following symlinks, resolves them strictly, requires `entry == plugin_root / "scripts/draft.py"`, and `lstat`-validates a regular non-symlink `skills/draft/SKILL.md`. It returns only canonical absolute paths. Use stable codes `hook.unknown_host`, `hook.environment_missing`, `hook.plugin_root_mismatch`, and `hook.layout_invalid`.

`read_hook_payload` reads a binary stream up to `MAX_STDIN + 1` raw bytes, checks the byte limit before any UTF-8 decoding, then applies strict-UTF-8 decode, object, event, session and per-host source validation without modifying string values. Use `hook.stdin_too_large`, `hook.stdin_invalid`, `hook.stdin_not_object`, `hook.unexpected_event`, `binding.session_unavailable`, and `hook.unexpected_source`.

Implement `render_session_fact`, `render_rebind` and `render_diagnostic` as pure functions. Implement `log_hook(...) -> AdapterError | None` as the only explicitly best-effort file operation: it returns `hook.log_unavailable` instead of raising, and the caller appends that diagnostic without discarding its primary branch. Convert each `OSError` at its host-layout, stdin-read, binding or log operation into a stable `AdapterError` (`hook.stdin_unavailable` for stream reads); do not rely on one broad filesystem catch. `run_session_start` follows the eight-step order in the design specification, uses `binding_root_is_current`/`binding_hint_is_current`, catches `AdapterError` at the Hook boundary, and always returns `0`.

- [x] **Step 7: 运行 SessionStart 定向测试和无核心依赖检查**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_session_hook.py -q
if rg -n '(import draft|from draft|subprocess|check_draft|load_graph|\.draft/)' \
  plugins/draft/scripts/adapter/session_hook.py; then
  exit 1
fi
```

Expected: tests pass; static check has no matches.

- [x] **Step 8: 提交 Hook 协议**

```bash
git add plugins/draft/scripts/adapter/session_hook.py \
  plugins/draft/scripts/tests/test_session_hook.py
git commit -m "feat(draft): add plugin session hook adapter"
```

---

### Task 4: 建立插件 CLI 并切换 Hook 接线

**Files:**
- Create: `plugins/draft/scripts/draft.py`
- Create: `plugins/draft/scripts/tests/test_cli.py`
- Modify: `plugins/draft/hooks/codex-hooks.json`
- Modify: `plugins/draft/hooks/claude-hooks.json`

**Interfaces:**
- Produces the only plugin commands: `bind`、`unbind`、`plugin-context reset`、`hook session-start HOST`。
- Produces internal callable `dispatch_plugin_command(arguments, *, entry_path, environ, stdin, runtime_dir) -> int`; `main` only supplies process defaults.
- Ordinary bind/unbind loads bridge by the canonical plugin entry; reset acts on that installation key without loading bridge.
- PEP 723 declares `requires-python = ">=3.12,<3.15"` and `dependencies = []`.

- [x] **Step 1: 写参数、分派、输出和退出码失败测试**

Create subprocess and in-process tests for:

```text
uv run <entry> bind ROOT --session SESSION_ID [--last-page-hint PAGE]
uv run <entry> unbind --session SESSION_ID
uv run <entry> plugin-context reset
uv run <entry> hook session-start codex|claude
```

For bind and unbind, `--json` must work immediately before or after the subcommand and at the end of its arguments. Assert exact success values:

```python
assert payload == {"ok": True, "result_type": "bind", "root": str(root.resolve())}
assert payload == {"ok": True, "result_type": "unbind", "removed": True}
```

Assert ordinary text is one line, parse/usage/root/hint failures return `2`, context/persistence failures return `4`, JSON failures use `error_payload`, and no traceback is emitted. Unknown top-level commands, missing bind/unbind options, or `--json` on reset/hook are usage errors. Once the argv prefix is `hook session-start`, however, missing/extra/unknown `HOST` is a Hook protocol diagnostic and returns `0`.

Use these exact ordinary success lines so human and Agent callers receive the same stable facts:

```text
绑定成功 <canonical absolute root>
解除成功 removed=true
解除成功 removed=false
```

- [x] **Step 2: 写 bridge 加载和 reset 顺序测试**

Cover missing, invalid and conflict bridge states for both bind and unbind. Missing maps to `plugin.context_unavailable`, not a fallback data directory. A valid bridge allows binding an ordinary directory with no `.draft/`.

Patch `load_context` to fail and verify reset never calls it:

```python
def test_reset_precedes_and_avoids_bridge_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_cli, "load_context", unexpected_call)
    assert plugin_cli.main(["plugin-context", "reset"], entry_path=ENTRY) == 0
```

Reset removes only bridge JSON/conflict/lock for the current entry, remains silent on success, and leaves `data_dir`, bindings and logs untouched.

- [x] **Step 3: 写 Hook CLI 的“始终成功启动会话”测试**

Monkeypatch `run_session_start` over all success and failure branches. Assert `hook session-start HOST` forwards the canonical current entry, environment, stdin and optional test runtime directory exactly once and returns `0`, including when the adapter reports bad host/layout/bridge/payload/binding/log states. `hook session-start` with zero or more than one host prints `hook.invalid_arguments` and returns `0`; only shapes that do not enter the `hook session-start` namespace use argparse's exit `2`.

- [x] **Step 4: 运行新 CLI 测试并确认入口不存在**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_cli.py -q
```

Expected: failures show missing `plugins/draft/scripts/draft.py` or importable plugin CLI.

- [x] **Step 5: 实现无第三方依赖插件入口**

Use this header and callable boundary:

```python
# /// script
# requires-python = ">=3.12,<3.15"
# dependencies = []
# ///

def main(
    argv: Sequence[str] | None = None,
    *,
    entry_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    stdin: TextIO | None = None,
    runtime_dir: Path | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    active_entry = Path(__file__) if entry_path is None else entry_path
    active_environ = os.environ if environ is None else environ
    active_stdin = sys.stdin if stdin is None else stdin
    return dispatch_plugin_command(
        arguments,
        entry_path=active_entry,
        environ=active_environ,
        stdin=active_stdin,
        runtime_dir=runtime_dir,
    )
```

Pre-dispatch the exact `hook session-start` prefix so even its argument-count failures use a model-visible Hook diagnostic and return `0`. Normalize `--json` only for bind/unbind before ordinary argparse dispatch so all documented positions are equivalent. Use a small `ArgumentParser` subclass whose `error()` raises `AdapterError("plugin.invalid_arguments", details=(message,), exit_code=2)` while `--help` still exits `0`. For ordinary operations call `load_context(entry)` exactly once; turn `None` into `AdapterError("plugin.context_unavailable")`. Construct `Binding` only from CLI values. Catch `AdapterError` once at the ordinary-command boundary and select text/JSON rendering without traceback. Dispatch reset before bridge loading and delegate a well-shaped Hook entirely to `run_session_start`.

- [x] **Step 6: 切换两个宿主 Hook 到插件入口**

Set the command literals exactly:

```json
"uv run \"${PLUGIN_ROOT}/scripts/draft.py\" hook session-start codex"
"uv run \"${CLAUDE_PLUGIN_ROOT}/scripts/draft.py\" hook session-start claude"
```

Do not add runtime/project flags or `_session-start` compatibility aliases.

- [x] **Step 7: 运行适配层全量定向测试**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests -q
```

Expected: bridge, binding, SessionStart and CLI suites pass.

- [x] **Step 8: 提交插件入口和 Hook 切换**

```bash
git add plugins/draft/scripts/draft.py plugins/draft/scripts/tests/test_cli.py \
  plugins/draft/hooks/codex-hooks.json plugins/draft/hooks/claude-hooks.json
git commit -m "feat(draft): route plugin commands through adapter"
```

---

### Task 5: 从核心 runtime 删除插件职责

**Files:**
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/src/draft/models.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/bindings.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/src/draft/session_hook.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py`

**Behavior contract:** Core public commands remain `init`、`adopt`、`check`、`move`、`promote`、`collapse`、`trash`、`restore`、`recover` and `--version`; all domain output and error semantics remain unchanged.

- [x] **Step 1: 先增加核心命令面和边界失败测试**

Extend `test_cli.py`:

```python
def test_core_help_excludes_plugin_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    for token in ("bind", "unbind", "hook", "session-start", "plugin-context"):
        assert token not in help_text

@pytest.mark.parametrize("command", ["bind", "unbind", "hook", "_session-start"])
def test_removed_plugin_commands_are_usage_errors(command: str) -> None:
    with pytest.raises(SystemExit) as caught:
        main([command])
    assert caught.value.code == 2
```

Extend `test_identity.py` so `draft.models` has no `Binding` and the runtime source tree contains none of the forbidden plugin terms from Global Constraints. Run only these tests and confirm they fail against the current core CLI/model.

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py -q
```

Expected: the new command-surface and `Binding` absence assertions fail while retained domain cases stay green.

- [x] **Step 2: 删除 CLI 分支、模型和插件模块**

Remove bind/unbind parser construction, `_session-start` pre-dispatch, `DRAFT_DATA`/`DRAFT_SKILL_PATH` reads, plugin result serialization and binding imports from `cli.py`. Remove `Binding` from `models.py` without changing domain dataclasses.

Delete `src/draft/bindings.py` and `src/draft/session_hook.py`. Delete their old location-specific suites only after the equivalent safety and protocol matrices pass under `plugins/draft/scripts/tests`; delete `test_plugin_context.py` because its complete matrix was migrated in Task 1. Remove only plugin-specific cases from core `test_cli.py`.

- [x] **Step 3: 运行核心定向回归与插件适配测试**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py \
         plugins/draft/scripts/tests -q
```

Expected: all pass; core domain help matrix still covers every retained command.

- [x] **Step 4: 静态审计整个核心子树**

Run:

```bash
if rg -n '(PLUGIN_ROOT|PLUGIN_DATA|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA|DRAFT_DATA|DRAFT_SKILL_PATH|SessionStart|session-start|plugin\.context|binding\.)' \
  plugins/draft/skills/draft/scripts/runtime/src; then
  exit 1
fi
```

Expected: no matches. Do not broaden the pattern to the design history under `docs/`.

- [x] **Step 5: 提交核心职责删除**

```bash
git add -A plugins/draft/skills/draft/scripts/runtime/src \
  plugins/draft/skills/draft/scripts/runtime/tests/test_cli.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_identity.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_bindings.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_session_hook.py \
  plugins/draft/skills/draft/scripts/runtime/tests/test_plugin_context.py
git commit -m "refactor(draft): remove plugin behavior from core"
```

---

### Task 6: 用路径式 uvx 取代核心 launcher 并锁定依赖窗口

**Files:**
- Delete: `plugins/draft/skills/draft/scripts/draft.py`
- Delete: `plugins/draft/skills/draft/scripts/runtime/tests/test_launcher.py`
- Rewrite: `plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py`
- Modify: `plugins/draft/skills/draft/scripts/runtime/pyproject.toml`
- Modify: `plugins/draft/skills/draft/scripts/runtime/uv.lock`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/constraints/minimum-runtime.txt`
- Create: `plugins/draft/skills/draft/scripts/runtime/tests/constraints/minimum-build.txt`
- Create or retain: `plugins/draft/skills/draft/scripts/ruff.toml`

**Behavior contract:** The only core invocation prefix is `uvx --isolated --from "<skill_root>/scripts/runtime" draft`; uv owns environment selection and cache reuse, while uv startup failures retain uv stderr and exit status.

- [x] **Step 1: 重写 bootstrap/distribution 测试并先得到红灯**

Replace fake-launcher tests with subprocess tests that copy only `skills/draft` beneath unrelated parent directories, then compute the runtime path from that copy. Assert:

1. `draft --version` reports the existing output shape with value `0.3.0` from the copy;
2. `draft init ROOT` and `draft check ROOT` pass without plugin files or repository parents;
3. `scripts/draft.py` is absent and no `.venv`/Draft-managed `envs/` appears in the copied skill or business root;
4. a deterministic missing-interpreter case (`UV_PYTHON` points to a nonexistent absolute path and Python downloads are disabled) returns uv's nonzero status and stderr, not a Draft JSON error;
5. `runtime/uv.lock` may be absent from a copied smoke fixture and path-based uvx still resolves from `pyproject.toml`.

Use `UV_CACHE_DIR=<temp>/uv-cache` to isolate assertions. Run the test and confirm it fails because the launcher still exists, versions/dependencies are old, and the new uvx smoke expectations are unmet.

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py -q
```

- [x] **Step 2: 写最低边界和最高可用组合测试**

Create exact constraint files:

```text
# tests/constraints/minimum-runtime.txt
markdown-it-py==4.2.0
Send2Trash==2.1.0
```

```text
# tests/constraints/minimum-build.txt
hatchling==1.31.0
```

In `test_dependency_bounds.py`, add a lowest-bound subprocess suite using a fresh cache. First run a version probe in the constrained environment and assert stdout is exactly `markdown-it-py=4.2.0` and `Send2Trash=2.1.0`; use `importlib.metadata.version` in the `python -c` probe, not imports from the packages under test. Then run:

```bash
uvx --isolated --cache-dir "$CACHE" \
  --constraints tests/constraints/minimum-runtime.txt \
  --build-constraints tests/constraints/minimum-build.txt \
  --from . --with pytest pytest tests -q \
  --ignore=tests/test_bootstrap.py \
  --ignore=tests/test_dependency_bounds.py \
  --ignore=tests/test_distribution.py
```

Run subprocesses with `cwd=RUNTIME_ROOT`. The three `--ignore` flags prevent recursive integration-suite execution while every domain test still runs under the constrained environment. The fresh-cache local-package build plus the exact `--build-constraints` file and successful test exit prove the build backend works at `hatchling==1.31.0`; do not try to import the isolated build backend from the completed tool environment.

Add a separate highest-available suite using another empty cache and:

```bash
uvx --isolated --cache-dir "$HIGH_CACHE" --refresh --resolution highest \
  --from . --with pytest pytest tests -q \
  --ignore=tests/test_bootstrap.py \
  --ignore=tests/test_dependency_bounds.py \
  --ignore=tests/test_distribution.py
```

Precede that command with the same `python -c` metadata probe in the same cache and assert the reported `(major, minor)` pairs are `(4, 2)` and `(2, 1)`. Do not pin or assert patch or transitive dependency versions.

- [x] **Step 3: 写缓存复用与同名工具隔离测试**

With an initially empty cache, run version/init/check once without `--offline`; snapshot the relative paths and nanosecond mtimes beneath that cache's `environments-v2/`, then repeat the same commands against the same copied runtime and cache with `--offline`. The second run must pass and leave the environment snapshot identical, proving `--isolated` reused the uv tool environment rather than rebuilding it. No Draft-managed environment may appear anywhere in the copied skill, business root or host data.

Create two independent runtime copies sharing one test cache; keep the first at `0.3.0` and change only the second fixture's `project.version` to synthetic `0.3.1`. Launch both `uvx --isolated --from <copy> draft --version` processes concurrently, assert their outputs remain `0.3.0` and `0.3.1`, and assert two distinct environment identities exist below `environments-v2/`. This is the regression for simultaneous sessions using different immutable runtime releases.

Build a temporary local package that exposes a conflicting `draft` console script and install it into a test-specific `UV_TOOL_DIR`. Its output must be `wrong global draft`. With that same `UV_TOOL_DIR`, run:

```bash
uvx --isolated --from "<copied-skill>/scripts/runtime" draft --version
```

Assert output is `0.3.0` and never contains `wrong global draft`. This proves `--isolated` ignores installed tools while the online/offline pair proves it still reuses the uv tool environment and dependency cache.

- [x] **Step 4: 更新核心包元数据与上级 Ruff 配置**

Set these exact declarations:

```toml
[project]
version = "0.3.0"
dependencies = [
    "markdown-it-py>=4.2,<4.3",
    "Send2Trash>=2.1,<2.2",
]

[build-system]
requires = ["hatchling>=1.31,<1.32"]
```

Keep development tools in `dependency-groups.dev`, using the same `hatchling>=1.31,<1.32` window for its Hatchling entry; remove `../draft.py` from `tool.pyright.include`, and do not re-add Ruff configuration to `runtime/pyproject.toml`. Retain/create `plugins/draft/skills/draft/scripts/ruff.toml` exactly as:

```toml
line-length = 100
target-version = "py312"
src = ["runtime/src"]

[lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
```

The executor must preserve the user's pre-existing worktree edits to these two Ruff files and incorporate them as this target state, not overwrite unrelated changes.

- [x] **Step 5: 删除 launcher 并更新开发锁**

Delete `plugins/draft/skills/draft/scripts/draft.py` and `runtime/tests/test_launcher.py`. Remove launcher/platform-parent assumptions from `test_distribution.py`; retain assertions that runtime belongs to the copied skill, provides `draft.cli:main`, and contains no plugin-only modules.

Run:

```bash
cd plugins/draft/skills/draft/scripts/runtime
uv lock
uv lock --check
```

Expected: lock resolves within every declared direct/build range. Do not add a runtime wrapper, managed cache directory, `--locked`, `--no-env-file`, or fallback command.

- [x] **Step 6: 运行 uvx 启动、边界与缓存门禁**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests/test_bootstrap.py \
         plugins/draft/skills/draft/scripts/runtime/tests/test_distribution.py -q
```

Then run the lowest-bound and highest-available commands implemented in Step 2. Expected: copied-skill, offline reuse, conflicting-tool, lowest and highest suites all pass.

- [x] **Step 7: 提交 uvx 核心入口迁移**

```bash
git add -A plugins/draft/skills/draft/scripts/draft.py \
  plugins/draft/skills/draft/scripts/ruff.toml \
  plugins/draft/skills/draft/scripts/runtime/pyproject.toml \
  plugins/draft/skills/draft/scripts/runtime/uv.lock \
  plugins/draft/skills/draft/scripts/runtime/tests
git commit -m "refactor(draft): run core through path-based uvx"
```

---

### Task 7: 更新文档、版本关系和完整分发门禁

**Files:**
- Modify: `plugins/draft/.codex-plugin/plugin.json`
- Modify: `plugins/draft/.claude-plugin/plugin.json`
- Modify: `plugins/draft/skills/draft/SKILL.md`
- Modify: `plugins/draft/skills/draft/references/draft-format.md`
- Delete: `plugins/draft/skills/draft/references/platform-setup.md`
- Create: `plugins/draft/references/platform-setup.md`
- Modify: `docs/design.md`
- Create: `plugins/draft/scripts/tests/test_distribution.py`

- [x] **Step 1: 写 manifest、物理边界和版本关系失败测试**

In plugin-level `test_distribution.py`, assert:

- both manifests declare plugin version `0.3.0`, `./skills/`, and their own Hook file;
- both plugin versions match each other, but the test does not compare them to runtime version;
- Codex and Claude marketplace registries still select the ordinary `plugins/draft` directory and require no recursive Git operation;
- Hook command literals are exactly those from Task 4;
- plugin entry, skill, runtime project and adapter modules are ordinary files;
- skill-level `scripts/draft.py`, `.gitmodules`, gitlink mode `160000`, generated distribution trees and plugin imports under the core source do not exist;
- adapter source has no non-stdlib import and no execution/import reference to Draft core.

Run the test before document/manifest changes and confirm version and physical-boundary assertions fail.

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_distribution.py -q
```

- [x] **Step 2: 写无仓库父目录的完整插件分发 smoke test**

For each host, create an independent copy of `plugins/draft/`, host `data_dir` and safe runtime directory beneath a temporary parent that has no `.git` and no repository parent. This isolation avoids correctly triggering a bridge conflict by publishing two hosts' different data directories under one installation key. In each parameterized case:

1. set only that host's root/data variables and invoke the copied `scripts/draft.py hook session-start HOST`;
2. parse the unbound envelope and assert its entry and command formats point only into the copy;
3. invoke copied-entry bind against an ordinary existing directory, rerun Hook, and assert root/current copied skill/current copied runtime check command;
4. invoke copied-entry unbind and assert the next Hook is unbound;
5. run copied-runtime version/init/check through `uvx --isolated --from` with a test-specific cache;
6. assert bindings/logs exist only under the selected host `data_dir`, the bridge exists only in that case's safe runtime directory, Draft business files exist only under the chosen root, and the uv cache has no `bindings/`, `hook.jsonl` or `.draft/` filesystem entries.

Run once with an empty cache and network permitted, then repeat core smoke with the same cache plus `--offline`. Assert no path in output or state resolves back to the repository checkout.

- [x] **Step 3: 分离核心和插件文档**

Update `SKILL.md` so setup and every domain example derive `<skill_root>` from the loaded skill and use exactly:

```text
uvx --isolated --from "<skill_root>/scripts/runtime" draft ...
```

It must explain that uv startup failures keep uv diagnostics, `setup` means running `draft --version`, first dependency resolution may use the configured index, and later runs reuse uv cache. Remove bind/unbind, SessionStart, bridge, host data, manifests, marketplaces, plugin modes, old launcher, `DRAFT_*`, `--locked`, `--no-env-file`, managed environment path and `runtime.cache_unavailable` language.

Remove plugin material from `references/draft-format.md`. Move and rewrite `references/platform-setup.md` at plugin root to document host variables, bridge states, three-field binding, plugin CLI command forms, reset scope, Hook diagnostics/return behavior, and recovery. The core skill must not link to this plugin-only reference.

- [x] **Step 4: 更新主设计和独立版本说明**

Update the affected architecture, command-flow, state, setup, failure, testing and release sections in `docs/design.md` to match the approved specification. Explicitly state that plugin and runtime both happen to become `0.3.0` in this release but have independent version semantics thereafter. Remove all claims that core owns plugin bridge/binding/SessionStart or that final-user uvx consumes `uv.lock`.

Set both plugin manifests to `0.3.0`; retain their existing names, skill paths and Hook file declarations.

- [x] **Step 5: 运行分发和文档边界测试**

Run:

```bash
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests/test_distribution.py \
         plugins/draft/scripts/tests/test_cli.py \
         plugins/draft/scripts/tests/test_session_hook.py -q
```

Then audit current product files only:

```bash
if rg -n '(PLUGIN_ROOT|PLUGIN_DATA|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA|DRAFT_DATA|DRAFT_SKILL_PATH|SessionStart|bridge|binding|marketplace)' \
  plugins/draft/skills/draft/SKILL.md \
  plugins/draft/skills/draft/references \
  plugins/draft/skills/draft/scripts/runtime/src; then
  exit 1
fi
```

Expected: tests pass and audit has no matches.

- [x] **Step 6: 运行最终质量门禁**

Run from the repository root:

```bash
uv lock --project plugins/draft/skills/draft/scripts/runtime --check
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/skills/draft/scripts/runtime/tests -q
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pytest plugins/draft/scripts/tests -q
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  pyright plugins/draft/skills/draft/scripts/runtime/src \
          plugins/draft/skills/draft/scripts/runtime/tests \
          plugins/draft/scripts
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  ruff check --config plugins/draft/skills/draft/scripts/ruff.toml \
             plugins/draft/skills/draft/scripts/runtime \
             plugins/draft/scripts
uv run --project plugins/draft/skills/draft/scripts/runtime --locked \
  ruff format --check --config plugins/draft/skills/draft/scripts/ruff.toml \
             plugins/draft/skills/draft/scripts/runtime \
             plugins/draft/scripts
git diff --check
```

Also rerun Task 6's minimum-bound, fresh-highest, copied-skill online/offline and conflicting-global-tool suites. Review every Global Constraint against the final tree; green tests alone do not waive that semantic audit.

- [x] **Step 7: 提交文档、版本和分发门禁**

```bash
git add plugins/draft/.codex-plugin/plugin.json \
  plugins/draft/.claude-plugin/plugin.json \
  plugins/draft/skills/draft/SKILL.md \
  plugins/draft/skills/draft/references/draft-format.md \
  plugins/draft/skills/draft/references/platform-setup.md \
  plugins/draft/references/platform-setup.md \
  plugins/draft/scripts/tests/test_distribution.py \
  docs/design.md
git commit -m "docs(draft): document separated core and plugin runtime"
```

The final commit must leave `git status --short` clean except for changes that demonstrably predated execution and were not part of this plan.
