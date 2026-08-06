from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = SCRIPTS_ROOT.parent
ENTRY_FILE = SCRIPTS_ROOT / "draft.py"
RUNTIME_ROOT = PLUGIN_ROOT / "skills" / "draft" / "scripts" / "runtime"
