"""Geometry-aware ZZX reader embedded in ULTRA Tubi Rinascita.

The low-level BCMP/archive/geometry modules are derived from the standalone
TubeNest project. This package is read-only at this stage: it does not generate
or modify machine files.
"""

from .bcmp import FormatError
from .domain import (
    TubePart,
    build_tube_part,
    build_tube_parts,
    describe_tube_parts,
    read_tube_parts,
)
from .nesting import NestItem, NestPlacement, NestRod, nest_items, nest_items_dict
from .fit import (
    AdjacencyFit,
    PartPose,
    PosedEnd,
    equivalent_axial_rotations,
    fall_friendly_final_cut,
    fit_adjacent_parts,
    posed_ends,
    rectangular_rotation_family,
    tail_flip_eligibility,
)
from .reader import (
    clear_cache,
    describe_zzx,
    read_zzx,
    read_zzx_cached,
)

__all__ = [
    "FormatError",
    "TubePart",
    "build_tube_part",
    "build_tube_parts",
    "describe_tube_parts",
    "read_tube_parts",
    "NestItem",
    "NestPlacement",
    "NestRod",
    "nest_items",
    "nest_items_dict",
    "AdjacencyFit",
    "PartPose",
    "PosedEnd",
    "equivalent_axial_rotations",
    "fall_friendly_final_cut",
    "fit_adjacent_parts",
    "posed_ends",
    "rectangular_rotation_family",
    "tail_flip_eligibility",
    "clear_cache",
    "describe_zzx",
    "read_zzx",
    "read_zzx_cached",
]

__version__ = "0.1.0"
