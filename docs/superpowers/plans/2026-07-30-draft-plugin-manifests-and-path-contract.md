# Draft Plugin Manifests and Path Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `draft` the only active product and root-domain identity, add valid Codex and Claude Code plugin/marketplace manifests, and replace layout-derived skill lookup with an explicitly injected path.

**Architecture:** Keep one self-contained install artifact at `plugins/draft/`. Bootstrap owns deployment knowledge and normalizes host-specific plugin roots and data directories into `DRAFT_DATA`, `DRAFT_SKILL_PATH`, and an explicit uv project path; the Python core consumes those values without scanning parents. This is a pre-release clean break: no old package, CLI, environment, marker, or domain aliases remain.

**Tech Stack:** Python 3.14, uv, pytest, pyright, ruff, POSIX shell, PowerShell, JSON plugin manifests.

## Global Constraints

- The normative specification is `docs/superpowers/specs/2026-07-30-draft-plugin-manifests-and-path-contract-design.md`.
- Plugin, marketplace, skill, Python package, CLI, hook, environment, and root-domain identity are all `draft`/`Draft`.
- New managed roots use `.draft/draft.json` and `.draft/draft.provisional.json`; do not detect or migrate old names.
- Keep `format_version = 1`, `journal_version = 2`, and plugin/runtime version `0.1.0`.
- Codex setup accepts only `PLUGIN_DATA`; Claude setup accepts only `CLAUDE_PLUGIN_DATA`; the explicit platform argument decides which one is read.
- Do not add compatibility aliases, parent-directory search, resource copies, new dependencies, or a Codex hook.
- Keep `docs/discussion/` unchanged as historical evidence.
- Preserve the current uncommitted workspace and do not create Git commits unless the user explicitly asks.

---

## File Structure

- `plugins/draft/runtime/src/draft/`: the single Python package after the clean rename.
- `plugins/draft/runtime/tests/`: behavioral tests and distribution-contract tests.
- `plugins/draft/bootstrap/setup.sh`: POSIX platform setup and launcher generation.
- `plugins/draft/bootstrap/setup.ps1`: PowerShell platform setup and launcher generation.
- `plugins/draft/hooks/claude-hooks.json`: Claude `SessionStart` declaration using its persistent plugin data launcher.
- `plugins/draft/skills/draft/`: the installed skill and user-facing references.
- `plugins/draft/.codex-plugin/plugin.json`: Codex plugin manifest.
- `plugins/draft/.claude-plugin/plugin.json`: Claude Code plugin manifest.
- `.agents/plugins/marketplace.json`: Codex repo marketplace.
- `.claude-plugin/marketplace.json`: Claude Code marketplace.
- `docs/design.md`: current normative product design.

---

### Task 1: Rename the install artifact, Python package, and executable identity

**Files:**
- Create, then move with the tree: `plugins/discussion-notebook/runtime/tests/test_identity.py` → `plugins/draft/runtime/tests/test_identity.py`
- Move: `plugins/discussion-notebook/` → `plugins/draft/`
- Move: `plugins/draft/runtime/src/discussion_notebook/` → `plugins/draft/runtime/src/draft/`
- Move: `plugins/draft/skills/discussion-notebook/` → `plugins/draft/skills/draft/`
- Modify: `plugins/draft/runtime/pyproject.toml`
- Modify: `plugins/draft/runtime/uv.lock`
- Modify: every `.py` file under `plugins/draft/runtime/src/draft/`
- Modify: every `.py` file under `plugins/draft/runtime/tests/`
- Modify: `plugins/draft/hooks/claude-hooks.json`
- Modify: `plugins/draft/skills/draft/SKILL.md`

**Interfaces:**
- Consumes: existing runtime behavior and version `0.1.0`.
- Produces: import package `draft`, project `draft-runtime`, CLI `draft`, hook command `draft-hook-claude`, skill identity `draft`, and `DRAFT_DATA`.

- [ ] **Step 1: Write the failing identity test**

Add `test_identity.py` without importing the missing package at module import time:

```python
from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_uses_draft_identity() -> None:
    assert importlib.util.find_spec("draft") is not None
    config = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["name"] == "draft-runtime"
    assert config["project"]["scripts"] == {
        "draft": "draft.cli:main",
        "draft-hook-claude": "draft.claude_hook:main",
    }
```

- [ ] **Step 2: Run the identity test and verify RED**

Run:

```bash
uv run --isolated --project plugins/discussion-notebook/runtime \
  pytest plugins/discussion-notebook/runtime/tests/test_identity.py -q
```

Expected: FAIL because `draft` is not importable and `pyproject.toml` still names the old project and scripts.

- [ ] **Step 3: Move the three identity-bearing directories**

Resolve each source and target explicitly, verify each source exists and each target does not, then rename only these paths:

```bash
mv plugins/discussion-notebook plugins/draft
mv plugins/draft/runtime/src/discussion_notebook plugins/draft/runtime/src/draft
mv plugins/draft/skills/discussion-notebook plugins/draft/skills/draft
```

- [ ] **Step 4: Apply the mechanical identity rewrite inside the active install artifact**

Replace only within `plugins/draft/`, excluding caches:

```text
discussion_notebook              -> draft
discussion-notebook-runtime      -> draft-runtime
discussion-notebook-hook-claude  -> draft-hook-claude
discussion-notebook              -> draft
DISCUSSION_NOTEBOOK_DATA          -> DRAFT_DATA
```

Update `pyproject.toml` to exactly:

```toml
[project]
name = "draft-runtime"
version = "0.1.0"

[project.scripts]
draft = "draft.cli:main"
draft-hook-claude = "draft.claude_hook:main"

[tool.hatch.build.targets.wheel]
packages = ["src/draft"]
```

Keep every unrelated project setting and dependency unchanged. Update `argparse.ArgumentParser(prog=...)`, the binding `skill_name`, hook envelopes, test monkeypatch paths, imports, and fixture values to the new identity.

- [ ] **Step 5: Regenerate the lock and verify GREEN**

Run:

```bash
uv lock --project plugins/draft/runtime
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_identity.py -q
uv run --isolated --project plugins/draft/runtime pytest -q
```

Expected: the identity test and the existing suite pass under package `draft`; `uv.lock` names `draft-runtime`.

- [ ] **Step 6: Inspect the focused change instead of committing**

Run:

```bash
git status --short
rg -n -uu -g '!**/.ruff_cache/**' \
  'discussion-notebook|discussion_notebook|DISCUSSION_NOTEBOOK' plugins/draft
```

Expected: the scan has no output. Do not commit.

---

### Task 2: Rename the root-domain model and on-disk namespace

**Files:**
- Modify: `plugins/draft/runtime/tests/test_identity.py`
- Modify: `plugins/draft/runtime/tests/conftest.py`
- Modify: all tests under `plugins/draft/runtime/tests/`
- Modify: all modules under `plugins/draft/runtime/src/draft/`
- Modify: `plugins/draft/runtime/pyproject.toml`

**Interfaces:**
- Consumes: package `draft` from Task 1.
- Produces: `DraftError`, `DraftGraph`, `check_draft`, `recover_draft`, `resolve_draft_root`, `draft_manifest`, `DraftFactory`, `.draft/draft.json`, and `.draft/draft.provisional.json`.

- [ ] **Step 1: Add failing root-domain behavior tests**

Append to `test_identity.py`:

```python
def test_root_domain_exports_use_draft() -> None:
    from draft import checker, errors, graph, lifecycle, paths, trash_lifecycle

    assert hasattr(errors, "DraftError")
    assert hasattr(graph, "DraftGraph")
    assert hasattr(checker, "check_draft")
    assert hasattr(trash_lifecycle, "recover_draft")
    assert hasattr(paths, "resolve_draft_root")
    assert hasattr(lifecycle, "draft_manifest")


def test_initialize_writes_only_draft_markers(tmp_path: Path) -> None:
    from draft.lifecycle import initialize

    root = tmp_path / "draft"
    initialize(root)

    assert (root / ".draft/draft.json").read_text(encoding="utf-8") == (
        '{"format_version":1}\n'
    )
    assert not (root / ".draft/draft.provisional.json").exists()
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_identity.py -q
```

Expected: FAIL because the old root-domain names and marker paths still exist.

- [ ] **Step 3: Rename active root-domain identifiers and markers**

Apply these exact identifier changes throughout runtime source and tests:

```text
NotebookError          -> DraftError
NotebookGraph          -> DraftGraph
check_notebook         -> check_draft
recover_notebook       -> recover_draft
resolve_notebook_root  -> resolve_draft_root
notebook_manifest      -> draft_manifest
NotebookFactory        -> DraftFactory
registered_notebook    -> registered_draft
notebook_factory       -> draft_factory
notebook               -> draft
Notebook               -> Draft
草稿本                   -> 草稿
```

In `draft/graph.py`, set:

```python
INTERNAL_DIR = ".draft"
MARKER_PATH = Path(INTERNAL_DIR) / "draft.json"
PROVISIONAL_MARKER_PATH = Path(INTERNAL_DIR) / "draft.provisional.json"
```

Rename every test helper and local identifier containing the old root-domain noun. Keep the generic `page`, `relation`, `journal`, `CheckReport`, and error-code namespaces unchanged.
Change the runtime description to `Deterministic runtime for recursive discussion drafts.`.

- [ ] **Step 4: Run the domain tests and suite GREEN**

Run:

```bash
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_identity.py -q
uv run --isolated --project plugins/draft/runtime pytest -q
```

Expected: all tests pass and all state is written beneath `.draft/`.

- [ ] **Step 5: Prove the active runtime has one domain vocabulary**

Run:

```bash
rg -n -uu -g '!**/.ruff_cache/**' \
  'notebook|Notebook|草稿本|\.discussion-notebook' \
  plugins/draft/runtime/src plugins/draft/runtime/tests
```

Expected: no output. Do not add legacy-detection branches to satisfy the scan.

---

### Task 3: Replace `default_skill_path()` with explicit path injection

**Files:**
- Modify: `plugins/draft/runtime/tests/test_bindings.py`
- Modify: `plugins/draft/runtime/tests/test_cli.py`
- Modify: `plugins/draft/runtime/tests/test_claude_hook.py`
- Modify: `plugins/draft/runtime/src/draft/bindings.py`
- Modify: `plugins/draft/runtime/src/draft/cli.py`
- Modify: `plugins/draft/runtime/src/draft/claude_hook.py`

**Interfaces:**
- Consumes: `DRAFT_DATA`, `DraftError`, and package identity from Tasks 1–2.
- Produces: `required_skill_path(raw: str | None) -> Path` and `skill_digest(skill_path: Path) -> str`; CLI and hook read `DRAFT_SKILL_PATH` only at their adapter boundaries.

- [ ] **Step 1: Write failing binding path tests**

Add to `test_bindings.py`:

```python
def test_required_skill_path_accepts_explicit_regular_file(tmp_path: Path) -> None:
    skill = tmp_path / "SKILL.md"
    skill.write_text("content", encoding="utf-8")
    assert required_skill_path(str(skill)) == skill.resolve()


@pytest.mark.parametrize("raw", [None, ""])
def test_required_skill_path_rejects_missing_input(raw: str | None) -> None:
    with pytest.raises(DraftError, match="binding.skill_unavailable"):
        required_skill_path(raw)


def test_required_skill_path_rejects_non_file(tmp_path: Path) -> None:
    with pytest.raises(DraftError, match="binding.skill_unavailable"):
        required_skill_path(str(tmp_path))
```

Update the CLI round-trip test to set both:

```python
monkeypatch.setenv("DRAFT_DATA", str(data_dir))
monkeypatch.setenv("DRAFT_SKILL_PATH", str(skill))
```

Update the hook fixture to set `DRAFT_SKILL_PATH` rather than monkeypatching a locator function.

- [ ] **Step 2: Run the targeted tests and verify RED**

Run:

```bash
uv run --isolated --project plugins/draft/runtime pytest -q \
  plugins/draft/runtime/tests/test_bindings.py \
  plugins/draft/runtime/tests/test_cli.py \
  plugins/draft/runtime/tests/test_claude_hook.py
```

Expected: FAIL because `required_skill_path` and `DRAFT_SKILL_PATH` consumption do not exist.

- [ ] **Step 3: Implement the narrow resource boundary**

In `bindings.py`, remove `default_skill_path()` and add:

```python
import stat


def required_skill_path(raw: str | None) -> Path:
    if raw is None or not raw.strip():
        raise DraftError("binding.skill_unavailable")
    path = Path(raw).expanduser()
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise DraftError("binding.skill_unavailable", path)
        return path.resolve(strict=True)
    except OSError as exc:
        raise DraftError("binding.skill_unavailable", path) from exc


def skill_digest(skill_path: Path) -> str:
    try:
        return hashlib.sha256(skill_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise DraftError("binding.skill_unavailable", skill_path) from exc
```

In the CLI bind adapter and Claude hook adapter, use:

```python
skill_path = bindings.required_skill_path(os.environ.get("DRAFT_SKILL_PATH"))
digest = bindings.skill_digest(skill_path)
```

Do not pass a plugin root into the Python core and do not scan `__file__` parents.

- [ ] **Step 4: Run targeted and full tests GREEN**

Run:

```bash
uv run --isolated --project plugins/draft/runtime pytest -q \
  plugins/draft/runtime/tests/test_bindings.py \
  plugins/draft/runtime/tests/test_cli.py \
  plugins/draft/runtime/tests/test_claude_hook.py
uv run --isolated --project plugins/draft/runtime pytest -q
```

Expected: all tests pass; no test monkeypatches a default path resolver.

- [ ] **Step 5: Prove layout independence**

Add a test that copies only `SKILL.md` to an unrelated temporary depth, sets `DRAFT_SKILL_PATH` to it, and completes CLI bind. Run that test alone and confirm it passes without knowledge of `plugins/draft/`.

---

### Task 4: Formalize cross-platform bootstrap and Claude hook paths

**Files:**
- Create: `plugins/draft/runtime/tests/test_bootstrap.py`
- Modify: `plugins/draft/bootstrap/setup.sh`
- Modify: `plugins/draft/bootstrap/setup.ps1`
- Modify: `plugins/draft/hooks/claude-hooks.json`

**Interfaces:**
- Consumes: plugin tree `plugins/draft/`, CLI `draft`, hook `draft-hook-claude`, `PLUGIN_DATA`, and `CLAUDE_PLUGIN_DATA`.
- Produces: `bootstrap/setup.sh codex|claude`, `bootstrap/setup.ps1 -Platform codex|claude`, and generated launchers exporting `DRAFT_DATA` and `DRAFT_SKILL_PATH`.

- [ ] **Step 1: Write POSIX bootstrap contract tests**

Create `test_bootstrap.py` with these helpers and real subprocess tests:

```python
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _write_fake_uv(bin_dir: Path) -> None:
    uv = bin_dir / "uv"
    uv.write_text(
        """#!/bin/sh
case "$*" in
    "--version") echo "uv 1.0" ;;
    sync\ *) exit 0 ;;
    *" draft --version") echo "0.1.0" ;;
    *" python --version") echo "Python 3.14.0" ;;
    *) echo "unexpected fake uv call: $*" >&2; exit 99 ;;
esac
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)


def run_setup(
    tmp_path: Path,
    platform: str,
    *,
    cwd: Path | None = None,
    **values: str,
) -> subprocess.CompletedProcess[str]:
    case_dir = Path(tempfile.mkdtemp(prefix="case-", dir=tmp_path))
    artifact = case_dir / "draft"
    shutil.copytree(PLUGIN_ROOT, artifact)
    fake_bin = case_dir / "bin"
    fake_bin.mkdir()
    _write_fake_uv(fake_bin)
    work = cwd or case_dir / "work"
    work.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.pop("PLUGIN_DATA", None)
    env.pop("CLAUDE_PLUGIN_DATA", None)
    env.update(values)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        ["sh", str(artifact / "bootstrap/setup.sh"), platform],
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_codex_setup_uses_only_plugin_data(tmp_path: Path) -> None:
    codex_data = tmp_path / "codex"
    claude_data = tmp_path / "claude"
    codex_data.mkdir()
    claude_data.mkdir()
    result = run_setup(
        tmp_path,
        "codex",
        PLUGIN_DATA=str(codex_data),
        CLAUDE_PLUGIN_DATA=str(claude_data),
    )
    assert result.returncode == 0, result.stderr
    launcher = (codex_data / "bin/draft").read_text(encoding="utf-8")
    assert f"DRAFT_DATA='{codex_data}'" in launcher
    assert "skills/draft/SKILL.md" in launcher
    assert not (claude_data / "bin").exists()


def test_claude_setup_uses_only_claude_plugin_data(tmp_path: Path) -> None:
    data = tmp_path / "claude"
    data.mkdir()
    result = run_setup(tmp_path, "claude", CLAUDE_PLUGIN_DATA=str(data))
    assert result.returncode == 0, result.stderr
    assert (data / "bin/draft").is_file()
    assert (data / "bin/draft-hook-claude").is_file()


def test_setup_rejects_unknown_platform_and_missing_data(tmp_path: Path) -> None:
    assert run_setup(tmp_path, "other").returncode == 2
    work = tmp_path / "fallback-check"
    work.mkdir()
    assert run_setup(tmp_path, "codex", cwd=work).returncode != 0
    assert list(work.iterdir()) == []
```

- [ ] **Step 2: Run bootstrap tests and verify RED**

Run:

```bash
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_bootstrap.py -q
```

Expected: FAIL because setup still assumes a Tavern project root and accepts no explicit platform.

- [ ] **Step 3: Rewrite POSIX setup around the explicit platform contract**

The outer selection must be exactly:

```sh
PLATFORM=${1-}
case "$PLATFORM" in
    codex) DATA=${PLUGIN_DATA-} ;;
    claude) DATA=${CLAUDE_PLUGIN_DATA-} ;;
    *) echo "用法：setup.sh codex|claude" >&2; exit 2 ;;
esac

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
PLUGIN_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd -P)
RUNTIME="$PLUGIN_ROOT/runtime"
SKILL="$PLUGIN_ROOT/skills/draft/SKILL.md"

if [ -z "$DATA" ] || [ ! -d "$DATA" ] || [ ! -w "$DATA" ]; then
    echo "平台数据目录不可用" >&2
    exit 1
fi
DATA=$(CDPATH='' cd -- "$DATA" && pwd -P)
```

Generate `bin/draft` with quoted absolute values through the existing `sq()` helper:

```sh
cat > "$DATA/bin/draft" <<EOF
#!/bin/sh
export DRAFT_DATA=$(sq "$DATA")
export DRAFT_SKILL_PATH=$(sq "$SKILL")
export UV_PROJECT_ENVIRONMENT=$(sq "$DATA/venv")
exec uv run --project $(sq "$RUNTIME") --frozen --no-sync draft "\$@"
EOF
chmod +x "$DATA/bin/draft"
```

Generate the hook launcher with the same three exports and:

```sh
exec uv run --project $(sq "$RUNTIME") --frozen --no-sync \
  draft-hook-claude --data-dir $(sq "$DATA") "\$@"
```

Keep the runtime manifest and setup status, but remove all project-root calculation, Tavern settings JSON generation, and PATH-injection instructions.

- [ ] **Step 4: Mirror the same contract in PowerShell**

Use a mandatory validated parameter and a single explicit data source:

```powershell
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('codex', 'claude')]
    [string]$Platform
)

$PluginRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runtime = Join-Path $PluginRoot 'runtime'
$Skill = Join-Path $PluginRoot 'skills/draft/SKILL.md'
$Data = if ($Platform -eq 'codex') { $env:PLUGIN_DATA } else { $env:CLAUDE_PLUGIN_DATA }
if (-not $Data -or -not (Test-Path -PathType Container $Data)) {
    throw '平台数据目录不可用'
}
$Data = (Resolve-Path $Data).Path
```

Generate `draft.cmd`, `draft-hook-claude.cmd`, and the PowerShell hook launcher with `DRAFT_DATA`, `DRAFT_SKILL_PATH`, and `UV_PROJECT_ENVIRONMENT`. Do not emit Tavern settings.

- [ ] **Step 5: Point the Claude hook at its generated data launcher**

Set `hooks/claude-hooks.json` command to:

```json
"\"${CLAUDE_PLUGIN_DATA}\"/bin/draft-hook-claude"
```

This file remains Claude-specific. Do not reuse it as a Codex hook.

- [ ] **Step 6: Run bootstrap tests and syntax gates GREEN**

Run:

```bash
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_bootstrap.py -q
sh -n plugins/draft/bootstrap/setup.sh
if command -v pwsh >/dev/null 2>&1; then
  pwsh -NoProfile -Command \
    "[void][System.Management.Automation.Language.Parser]::ParseFile(\
      'plugins/draft/bootstrap/setup.ps1',[ref]\$null,[ref]\$null)"
fi
```

Expected: tests pass; all available syntax checks exit 0.

---

### Task 5: Add Codex and Claude plugin and marketplace manifests

**Files:**
- Create: `.agents/plugins/marketplace.json`
- Create: `.claude-plugin/marketplace.json`
- Create: `plugins/draft/.codex-plugin/plugin.json`
- Create: `plugins/draft/.claude-plugin/plugin.json`
- Create: `plugins/draft/runtime/tests/test_distribution.py`

**Interfaces:**
- Consumes: self-contained `plugins/draft/` and Claude hook from Task 4.
- Produces: four discoverable manifests, all naming `draft` version `0.1.0` and sourcing `./plugins/draft`.

- [ ] **Step 1: Write failing distribution tests**

Create `test_distribution.py` with exact roots and a JSON reader, then assert all four manifests:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast("dict[str, Any]", value)


def test_marketplaces_publish_draft() -> None:
    codex = load_json(REPO_ROOT / ".agents/plugins/marketplace.json")
    claude = load_json(REPO_ROOT / ".claude-plugin/marketplace.json")
    assert codex["name"] == "draft"
    assert codex["plugins"][0]["name"] == "draft"
    assert codex["plugins"][0]["source"]["path"] == "./plugins/draft"
    assert claude["name"] == "draft"
    assert claude["owner"] == {"name": "CLD"}
    assert claude["plugins"] == [{"name": "draft", "source": "./plugins/draft"}]


def test_plugin_manifests_match_the_install_artifact() -> None:
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["name"] == claude["name"] == PLUGIN_ROOT.name == "draft"
    assert codex["version"] == claude["version"] == "0.1.0"
    assert codex["skills"] == claude["skills"] == "./skills/"
    assert "hooks" not in codex
    assert claude["hooks"] == "./hooks/claude-hooks.json"
```

- [ ] **Step 2: Run distribution tests and verify RED**

Run:

```bash
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_distribution.py -q
```

Expected: FAIL because the four manifests do not exist.

- [ ] **Step 3: Scaffold the Codex manifest and repo marketplace**

Run the provided plugin-creator script once against the existing plugin:

```bash
python3 /mnt/c/Users/CLD/.codex/skills/.system/plugin-creator/scripts/create_basic_plugin.py \
  draft \
  --path /home/cld/workspace/discussion-notebook/plugins \
  --marketplace-path /home/cld/workspace/discussion-notebook/.agents/plugins/marketplace.json \
  --marketplace-name draft \
  --with-marketplace \
  --force
```

The explicit marketplace name is required by the user-approved unified `draft` identity. The script may overwrite only the new Codex manifest and new Codex marketplace entry; verify the rest of `plugins/draft/` is unchanged.

- [ ] **Step 4: Replace scaffold copy with exact Codex metadata**

Set `.codex-plugin/plugin.json` to:

```json
{
  "name": "draft",
  "version": "0.1.0",
  "description": "Maintain long-running, branching design drafts as self-contained Markdown.",
  "author": {"name": "CLD"},
  "skills": "./skills/",
  "interface": {
    "displayName": "Draft",
    "shortDescription": "Maintain long-running design drafts",
    "longDescription": "Structure long-running, branching design discussions as self-contained Markdown drafts.",
    "developerName": "CLD",
    "category": "Productivity",
    "capabilities": ["Read", "Write"],
    "defaultPrompt": [
      "Use Draft to structure this long-running design discussion.",
      "Check the current Draft for structural errors."
    ]
  }
}
```

Keep the generated Codex marketplace policy at `AVAILABLE` and `ON_INSTALL`, category `Productivity`, and top-level display name `Draft`.

- [ ] **Step 5: Add exact Claude manifests**

Create `.claude-plugin/marketplace.json`:

```json
{
  "name": "draft",
  "owner": {"name": "CLD"},
  "plugins": [
    {"name": "draft", "source": "./plugins/draft"}
  ]
}
```

Create `plugins/draft/.claude-plugin/plugin.json`:

```json
{
  "name": "draft",
  "version": "0.1.0",
  "description": "Maintain long-running, branching design drafts as self-contained Markdown.",
  "author": {"name": "CLD"},
  "skills": "./skills/",
  "hooks": "./hooks/claude-hooks.json"
}
```

- [ ] **Step 6: Validate and run distribution tests GREEN**

Run:

```bash
python3 /mnt/c/Users/CLD/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/draft
uv run --isolated --project plugins/draft/runtime \
  pytest plugins/draft/runtime/tests/test_distribution.py -q
```

Expected: Codex validation passes and distribution tests pass.

---

### Task 6: Update the skill and normative design

**Files:**
- Modify: `plugins/draft/skills/draft/SKILL.md`
- Move: `plugins/draft/skills/draft/references/notebook-format.md` → `plugins/draft/skills/draft/references/draft-format.md`
- Rewrite: `plugins/draft/skills/draft/references/platform-setup.md`
- Modify: `docs/design.md`
- Preserve unchanged: `docs/discussion/**`

**Interfaces:**
- Consumes: final identifiers, bootstrap commands, marker paths, and manifests from Tasks 1–5.
- Produces: one current user-facing vocabulary and accurate install/setup instructions.

- [ ] **Step 1: Record the failing active-document vocabulary scan**

Run:

```bash
rg -n -uu \
  'discussion-notebook|discussion_notebook|DISCUSSION_NOTEBOOK|\.discussion-notebook|notebook|Notebook|草稿本' \
  docs/design.md plugins/draft/skills
```

Expected: matches appear throughout the active design and skill documents.

- [ ] **Step 2: Rename the format reference and update the skill**

Rename the file to `references/draft-format.md`. In `SKILL.md`:

```yaml
---
name: draft
description: ...
---
```

Use `# Draft`, `draft` CLI commands, `.draft/`, and `references/draft-format.md`. Replace the root-domain Chinese term with “草稿”; preserve the existing page, relationship, approval, and hard-stop semantics.

- [ ] **Step 3: Rewrite platform setup for installed plugins**

`platform-setup.md` must contain the two explicit entry forms:

```text
bootstrap/setup.sh codex
bootstrap/setup.sh claude
bootstrap/setup.ps1 -Platform codex
bootstrap/setup.ps1 -Platform claude
```

Document that Codex supplies `PLUGIN_DATA`, Claude supplies `CLAUDE_PLUGIN_DATA`, and generated launchers normalize them to `DRAFT_DATA` plus `DRAFT_SKILL_PATH`. Remove Tavern paths, project settings fragments, and incubation claims.

- [ ] **Step 4: Update the normative design without rewriting history**

In `docs/design.md`:

- rename the product, root domain, CLI, package, markers, environment variables, plugin tree, and skill reference;
- change status from incubation/future extraction to completed incubation and current independent-plugin delivery;
- point the conclusions link at `docs/discussion/2026-07-18-recursive-discussion-notebook-skill/conclusions.md` using a repository-relative link;
- remove or replace the broken external remediation-plan link;
- retain protocol semantics and version values unless the approved specification changed them.

- [ ] **Step 5: Run skill validation and the vocabulary scan GREEN**

Run:

```bash
python3 /mnt/c/Users/CLD/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/draft/skills/draft
rg -n -uu \
  'discussion-notebook|discussion_notebook|DISCUSSION_NOTEBOOK|\.discussion-notebook|notebook|Notebook|草稿本' \
  docs/design.md plugins/draft/skills
```

Expected: skill validation passes and the scan has no output.

---

### Task 7: Run complete verification from source and copied install artifact

**Files:**
- Verify only; modify a file only if a gate exposes a contract violation, then rerun the failing gate and all later gates.

**Interfaces:**
- Consumes: all deliverables from Tasks 1–6.
- Produces: fresh evidence that source, manifests, launchers, and copied install artifact satisfy the specification.

- [ ] **Step 1: Run the complete Python quality gates**

Run:

```bash
uv run --isolated --project plugins/draft/runtime pytest -q
uv run --isolated --project plugins/draft/runtime pyright
uv run --isolated --project plugins/draft/runtime ruff check .
uv run --isolated --project plugins/draft/runtime ruff format --check .
```

Expected: zero test failures, zero type errors, zero lint errors, and no formatting changes required.

- [ ] **Step 2: Run manifest, skill, JSON, and shell gates**

Run:

```bash
python3 /mnt/c/Users/CLD/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  plugins/draft
python3 /mnt/c/Users/CLD/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  plugins/draft/skills/draft
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
python3 -m json.tool .claude-plugin/marketplace.json >/dev/null
python3 -m json.tool plugins/draft/.codex-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/draft/.claude-plugin/plugin.json >/dev/null
python3 -m json.tool plugins/draft/hooks/claude-hooks.json >/dev/null
sh -n plugins/draft/bootstrap/setup.sh
```

Expected: all commands exit 0.

- [ ] **Step 3: Prove forbidden active identifiers are absent**

Run:

```bash
rg -n -uu -g '!**/.ruff_cache/**' \
  'discussion-notebook|discussion_notebook|DISCUSSION_NOTEBOOK|\.discussion-notebook|notebook|Notebook|草稿本' \
  docs/design.md plugins/draft
```

Expected: no output. The approved specification and `docs/discussion/` are intentionally outside this scan.

- [ ] **Step 4: Smoke-test a copied install artifact**

Use an explicit temporary directory and clean it through a trap:

```bash
DRAFT_SMOKE_DIR=$(mktemp -d)
trap 'rm -rf -- "$DRAFT_SMOKE_DIR"' EXIT
cp -R plugins/draft "$DRAFT_SMOKE_DIR/draft"
mkdir -p "$DRAFT_SMOKE_DIR/codex-data" "$DRAFT_SMOKE_DIR/managed"
PLUGIN_DATA="$DRAFT_SMOKE_DIR/codex-data" \
  sh "$DRAFT_SMOKE_DIR/draft/bootstrap/setup.sh" codex
"$DRAFT_SMOKE_DIR/codex-data/bin/draft" init "$DRAFT_SMOKE_DIR/managed"
"$DRAFT_SMOKE_DIR/codex-data/bin/draft" check "$DRAFT_SMOKE_DIR/managed"
"$DRAFT_SMOKE_DIR/codex-data/bin/draft" bind "$DRAFT_SMOKE_DIR/managed" \
  --platform codex --session smoke
test -f "$DRAFT_SMOKE_DIR/managed/.draft/draft.json"
test -f "$DRAFT_SMOKE_DIR/codex-data/bindings/codex/$(printf smoke | sha256sum | cut -d' ' -f1).json"
```

Expected: every command exits 0 and all created state stays inside the copied artifact's explicit data and managed-root directories.

- [ ] **Step 5: Final diff and requirement audit**

Run:

```bash
if git rev-parse --verify HEAD >/dev/null 2>&1; then
  git diff --check
else
  ! rg -n '[[:blank:]]+$' .agents .claude-plugin docs/design.md plugins/draft
fi
git status --short
find plugins/draft -type l -print
```

Re-read every success condition in the specification and map it to Tasks 1–7. Expected: no whitespace errors, no unexpected symlinks, no repository writes outside the requested files, and no unverified requirement. Do not commit.
