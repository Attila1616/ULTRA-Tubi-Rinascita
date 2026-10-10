"""Geometry-aware ZZX tools embedded in ULTRA Tubi Rinascita."""

from .bcmp import FormatError
from .domain import (
    TubePart,
    build_tube_part,
    build_tube_parts,
    describe_tube_parts,
    read_tube_parts,
)
from .nesting import NestItem, NestPlacement, NestRod, nest_items, nest_items_dict
from .optimizer import (
    diagnose_items_dict,
    get_parallel_stats,
    optimize_items,
    optimize_items_dict,
)
from .fit import (
    AdjacencyFit,
    PartPose,
    PosedEnd,
    equivalent_axial_rotations,
    fit_adjacent_parts,
    posed_ends,
    rectangular_rotation_family,
    tail_flip_eligibility,
)
from .toolpath import curve_start_parameter, point_at_composite_parameter
from .exporter import export_nested_rod, export_nested_rod_to_directory
from .flat_exporter import export_flat_nested_rod, export_flat_nested_rod_to_directory
from .igs_converter import IgsConversionError, convert_igs_to_zzx
from .step_converter import StepConversionError, convert_step_to_zzx
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
    "optimize_items",
    "optimize_items_dict",
    "diagnose_items_dict",
    "get_parallel_stats",
    "AdjacencyFit",
    "PartPose",
    "PosedEnd",
    "equivalent_axial_rotations",
    "fit_adjacent_parts",
    "posed_ends",
    "rectangular_rotation_family",
    "tail_flip_eligibility",
    "curve_start_parameter",
    "point_at_composite_parameter",
    "export_nested_rod",
    "export_nested_rod_to_directory",
    "export_flat_nested_rod",
    "export_flat_nested_rod_to_directory",
    "IgsConversionError",
    "convert_igs_to_zzx",
    "StepConversionError",
    "convert_step_to_zzx",
    "clear_cache",
    "describe_zzx",
    "read_zzx",
    "read_zzx_cached",
]

__version__ = "0.1.0"
