import sys
from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType

from tests.support.paths import ENTRY_FILE


def load_plugin_entry() -> ModuleType:
    spec = spec_from_file_location("draft_plugin_entry_under_test", ENTRY_FILE)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load plugin entry: {ENTRY_FILE}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module
