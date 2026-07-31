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
    assert (scripts / "draft").is_file()
    assert (scripts / "draft.ps1").is_file()
    assert (scripts / "runtime/pyproject.toml").is_file()
    assert (scripts / "runtime/uv.lock").is_file()
    assert (scripts / "runtime/src/draft/__main__.py").is_file()
    assert not (PLUGIN_ROOT / "runtime").exists()
    assert not (PLUGIN_ROOT / "bootstrap").exists()


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
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["name"] == claude["name"] == PLUGIN_ROOT.name == "draft"
    assert codex["version"] == claude["version"] == "0.1.0"
    assert codex["skills"] == claude["skills"] == "./skills/"


def test_both_manifests_declare_their_session_start_hook() -> None:
    codex = load_json(PLUGIN_ROOT / ".codex-plugin/plugin.json")
    claude = load_json(PLUGIN_ROOT / ".claude-plugin/plugin.json")
    assert codex["hooks"] == "./hooks/codex-hooks.json"
    assert claude["hooks"] == "./hooks/claude-hooks.json"


def test_hook_commands_use_the_stable_skill_entry() -> None:
    codex = load_json(PLUGIN_ROOT / "hooks/codex-hooks.json")
    claude = load_json(PLUGIN_ROOT / "hooks/claude-hooks.json")
    codex_command = codex["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    claude_command = claude["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert "${PLUGIN_ROOT}/skills/draft/scripts/draft" in codex_command
    assert "${CLAUDE_PLUGIN_ROOT}/skills/draft/scripts/draft" in claude_command
    assert "PLUGIN_DATA" not in codex_command
    assert "CLAUDE_PLUGIN_DATA" not in claude_command
    assert "/bin/draft" not in codex_command + claude_command
