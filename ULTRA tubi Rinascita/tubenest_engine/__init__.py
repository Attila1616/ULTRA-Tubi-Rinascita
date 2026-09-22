"""Geometry-aware ZZX reader embedded in ULTRA Tubi Rinascita.

The low-level BCMP/archive/geometry modules are derived from the standalone
TubeNest project. This package is read-only at this stage: it does not generate
or modify machine files.
"""

from .bcmp import FormatError
from .reader import (
    clear_cache,
    describe_zzx,
    read_zzx,
    read_zzx_cached,
)

__all__ = [
    "FormatError",
    "clear_cache",
    "describe_zzx",
    "read_zzx",
    "read_zzx_cached",
]

__version__ = "0.1.0"
