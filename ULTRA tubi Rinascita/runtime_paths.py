"""Runtime path resolution for ULTRA Tubi Rinascita.

The application code may live in a Git-controlled Program directory while mutable
user data lives in a separate Variables directory.

Resolution order:
1. ULTRA_VARIABLES_DIR environment variable, when set.
2. An initialized "Variables" folder beside/above the program in common layouts.
3. The legacy project directory (backward-compatible behavior).
"""

import os
import sys


IS_FROZEN = getattr(sys, "frozen", False)
PROJECT_ROOT = (
    os.path.dirname(os.path.abspath(sys.executable))
    if IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)
RESOURCE_ROOT = getattr(sys, "_MEIPASS", PROJECT_ROOT)

_VARIABLE_SENTINELS = (
    "config.json",
    "state.json",
    "history.json",
    "Tubi",
    "Database Tubi",
    "Conteggio Tubi.xlsx",
    "Conteggio tubi.xlsx",
    "codici_tubi.json",
)


def _normalize(path):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(str(path))))


def _looks_initialized(path):
    if not os.path.isdir(path):
        return False
    return any(os.path.exists(os.path.join(path, name)) for name in _VARIABLE_SENTINELS)


def candidate_variables_dirs(project_root=None):
    """Return possible external Variables locations in priority order."""
    root = _normalize(project_root or PROJECT_ROOT)
    parent = os.path.dirname(root)
    grandparent = os.path.dirname(parent)

    candidates = []
    override = str(os.environ.get("ULTRA_VARIABLES_DIR") or "").strip()
    if override:
        candidates.append(_normalize(override))

    # Common layouts:
    #   <workspace>/Program/ULTRA tubi Rinascita + <workspace>/Variables
    #   <workspace>/ULTRA tubi Rinascita         + <workspace>/Variables
    #   <project>/Variables
    candidates.extend(
        [
            os.path.join(parent, "Variables"),
            os.path.join(grandparent, "Variables"),
            os.path.join(root, "Variables"),
        ]
    )

    unique = []
    seen = set()
    for candidate in candidates:
        normalized = _normalize(candidate)
        key = os.path.normcase(normalized)
        if key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def resolve_variables_root(project_root=None):
    """Resolve the directory that owns mutable runtime data."""
    root = _normalize(project_root or PROJECT_ROOT)

    override = str(os.environ.get("ULTRA_VARIABLES_DIR") or "").strip()
    if override:
        return _normalize(override)

    for candidate in candidate_variables_dirs(root):
        if _looks_initialized(candidate):
            return candidate

    # Legacy behavior: mutable files live beside the program.
    return root


VARIABLES_ROOT = resolve_variables_root()
USING_EXTERNAL_VARIABLES = (
    os.path.normcase(_normalize(VARIABLES_ROOT))
    != os.path.normcase(_normalize(PROJECT_ROOT))
)


def data_file(name):
    """Return the preferred writable path for a mutable runtime file."""
    return os.path.join(VARIABLES_ROOT, name)


CONFIG_FILE = data_file("config.json")
UI_STATE_FILE = data_file("state.json")
HISTORY_FILE = data_file("history.json")
DATABASE_DIR = data_file("Database Tubi")
CODE_CATALOG_FILE = data_file("codici_tubi.json")
INVENTORY_XLSX_FILE = data_file("Conteggio Tubi.xlsx")
TUBI_DIR = data_file("Tubi")


def describe():
    return {
        "project_root": PROJECT_ROOT,
        "resource_root": RESOURCE_ROOT,
        "variables_root": VARIABLES_ROOT,
        "using_external_variables": USING_EXTERNAL_VARIABLES,
        "config_file": CONFIG_FILE,
        "state_file": UI_STATE_FILE,
        "history_file": HISTORY_FILE,
    }
