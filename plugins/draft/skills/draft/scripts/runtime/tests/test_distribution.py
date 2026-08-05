from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = RUNTIME_ROOT.parents[1]
PLUGIN_ROOT = SKILL_ROOT.parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]


def test_runtime_belongs_to_the_skill() -> None:
    assert RUNTIME_ROOT == SKILL_ROOT / "scripts/runtime"
    assert (SKILL_ROOT / "SKILL.md").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()


def test_skill_is_a_self_contained_runtime_unit() -> None:
    scripts = SKILL_ROOT / "scripts"
    assert (scripts / "draft.py").is_file()
    assert (scripts / "runtime/pyproject.toml").is_file()
    assert (scripts / "runtime/uv.lock").is_file()
    assert (scripts / "runtime/src/draft/__main__.py").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()
    assert not (PLUGIN_ROOT / "bootstrap").exists()


def test_skill_has_one_python_launcher() -> None:
    scripts = SKILL_ROOT / "scripts"
    assert (scripts / "draft.py").is_file()
    assert not (scripts / "draft").exists()
    assert not (scripts / "draft.ps1").exists()
    assert not (scripts / "runtime/src/draft/entry.py").exists()
    assert not (scripts / "runtime/src/draft/plugin_context.py").exists()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast("dict[str, Any]", value)


def test_marketplaces_publish_draft() -> None:
    codex = load_json(REPO_ROOT / ".agents/plugins/marketplace.json")
    claude = load_json(REPO_ROOT / ".claude-plugin/marketplace.json")
    assert codex["name"] == "cld-plugins"
    assert codex["plugins"][0]["name"] == "draft"
    assert codex["plugins"][0]["source"]["path"] == "./plugins/draft"
    assert claude["name"] == "cld-plugins"
    assert claude["owner"] == {"name": "CLD"}
    assert claude["plugins"] == [{"name": "draft", "source": "./plugins/draft"}]


def test_plugin_manifests_match_the_install_artifact() -> None:
    import tomllib

    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["name"] == claude["name"] == PLUGIN_ROOT.name == "draft"
    runtime = tomllib.loads((RUNTIME_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_version = runtime["project"]["version"]
    assert codex["version"] == claude["version"] == runtime_version
    assert codex["skills"] == claude["skills"] == "./skills/"


def test_both_manifests_declare_their_session_start_hook() -> None:
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["hooks"] == "./hooks/codex-hooks.json"
    assert claude["hooks"] == "./hooks/claude-hooks.json"


def hook_commands() -> tuple[str, str]:
    codex = load_json(PLUGIN_ROOT / "hooks/codex-hooks.json")
    claude = load_json(PLUGIN_ROOT / "hooks/claude-hooks.json")
    codex_command = codex["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    claude_command = claude["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    return cast("str", codex_command), cast("str", claude_command)


def test_hook_commands_run_the_same_python_launcher() -> None:
    codex_command, claude_command = hook_commands()
    assert codex_command == ('uv run "${PLUGIN_ROOT}/scripts/draft.py" hook session-start codex')
    assert claude_command == (
        'uv run "${CLAUDE_PLUGIN_ROOT}/scripts/draft.py" hook session-start claude'
    )
    for command in (codex_command, claude_command):
        assert "--python" not in command
        assert "--project" not in command
        assert "--no-project" not in command
        assert ".ps1" not in command
