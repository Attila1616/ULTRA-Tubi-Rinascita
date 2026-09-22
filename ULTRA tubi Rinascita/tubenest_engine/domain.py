"""Normalized geometry-aware tube-part domain model.

This module converts raw ZZX reader output into the stable representation that
future nesting, common-line and export code should consume. It is deliberately
read-only and does not modify machine files.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .bcmp import FormatError
from .models import ProfileInfo, ShapeInfo, TubeSegmentInfo, ZzxDocumentInfo
from .reader import read_zzx_cached


@dataclass
class TubeProfile:
    kind: str
    thickness: float
    outside_width: Optional[float] = None
    outside_height: Optional[float] = None
    outside_diameter: Optional[float] = None
    corner_radius: Optional[float] = None
    source_section_class: Optional[str] = None
    native_record_class: Optional[str] = None
    inferred_from_geometry: bool = False
    equivalent_axial_rotations_degrees: list = field(default_factory=list)
    continuous_axial_rotation_symmetry: bool = False


@dataclass
class PartEnd:
    label: str
    shape_handle: int
    operation_layer: Optional[int]
    plane_z_equals_c_plus_ax_plus_by: Optional[list]
    plane_max_residual_mm: Optional[float]
    angle_from_perpendicular_degrees: Optional[float]
    sampled_bounds: Optional[list]
    start_parameter: Optional[float] = None
    start_point: Optional[list] = None


@dataclass
class PartFeature:
    shape_handle: int
    feature_type: str
    operation_layer: Optional[int]
    shape_kind: str
    geometry_class: Optional[str]
    sampled_bounds: Optional[list]
    primitive_types: dict = field(default_factory=dict)
    point_count: int = 0


@dataclass
class TubePart:
    source_path: str
    source_sha256: str
    source_file_version: str
    segment_handle: int
    segment_name: str
    part_fingerprint: str
    profile: TubeProfile
    axial_min: float
    axial_max: float
    overall_length: float
    ends: list = field(default_factory=list)
    features: list = field(default_factory=list)
    active_layers: list = field(default_factory=list)
    marking_feature_count: int = 0
    cutting_feature_count: int = 0

    def to_dict(self):
        return asdict(self)


def _normalize_bounds(bounds_value, axial_min):
    if not bounds_value:
        return None
    result = [list(bounds_value[0]), list(bounds_value[1])]
    if len(result[0]) >= 3:
        result[0][2] -= axial_min
        result[1][2] -= axial_min
    return result


def _normalize_point(point, axial_min):
    if not point:
        return None
    result = list(map(float, point))
    if len(result) >= 3:
        result[2] -= axial_min
    return result


def _normalize_plane(plane, axial_min):
    if not plane:
        return None
    c, a, b = map(float, plane)
    return [c - axial_min, a, b]


def _profile(profile: ProfileInfo):
    kind = profile.section_class or "Unknown"
    if kind == "Circle":
        return TubeProfile(
            kind="Circle",
            thickness=float(profile.thickness),
            outside_width=profile.diameter,
            outside_height=profile.diameter,
            outside_diameter=profile.diameter,
            corner_radius=None,
            source_section_class=profile.source_section_class,
            native_record_class=profile.native_record_class,
            inferred_from_geometry=bool(profile.inferred_from_geometry),
            continuous_axial_rotation_symmetry=True,
        )

    if kind == "Square":
        side = profile.side
        if side is None and profile.width is not None and profile.height is not None:
            side = (float(profile.width) + float(profile.height)) / 2.0
        return TubeProfile(
            kind="Square",
            thickness=float(profile.thickness),
            outside_width=side,
            outside_height=side,
            corner_radius=profile.radius,
            source_section_class=profile.source_section_class,
            native_record_class=profile.native_record_class,
            inferred_from_geometry=bool(profile.inferred_from_geometry),
            equivalent_axial_rotations_degrees=[0.0, 90.0, 180.0, 270.0],
        )

    if kind == "Rect":
        return TubeProfile(
            kind="Rect",
            thickness=float(profile.thickness),
            outside_width=profile.width,
            outside_height=profile.height,
            corner_radius=profile.radius,
            source_section_class=profile.source_section_class,
            native_record_class=profile.native_record_class,
            inferred_from_geometry=bool(profile.inferred_from_geometry),
            equivalent_axial_rotations_degrees=[0.0, 180.0],
        )

    return TubeProfile(
        kind=kind,
        thickness=float(profile.thickness),
        outside_width=profile.width,
        outside_height=profile.height,
        outside_diameter=profile.diameter,
        corner_radius=profile.radius,
        source_section_class=profile.source_section_class,
        native_record_class=profile.native_record_class,
        inferred_from_geometry=bool(profile.inferred_from_geometry),
        equivalent_axial_rotations_degrees=[0.0],
    )


def _feature(shape: ShapeInfo, axial_min, default_channel):
    if shape.is_end_cut:
        return None
    if shape.operation_layer is None:
        # Channel-zero / metadata-only display geometry is not a machining feature.
        return None

    feature_type = "marking" if shape.operation_layer != default_channel else "cut"
    return PartFeature(
        shape_handle=int(shape.handle),
        feature_type=feature_type,
        operation_layer=shape.operation_layer,
        shape_kind=shape.kind,
        geometry_class=shape.geometry_class,
        sampled_bounds=_normalize_bounds(shape.sampled_bounds, axial_min),
        primitive_types=dict(shape.primitive_types or {}),
        point_count=int(shape.point_count or 0),
    )


def build_tube_part(document: ZzxDocumentInfo, segment: TubeSegmentInfo):
    if not segment.raw_bounds or len(segment.raw_bounds[0]) < 3:
        raise FormatError(f"TubeSegment {segment.handle} has no usable axial bounds")

    axial_min = float(segment.raw_bounds[0][2])
    axial_max = float(segment.raw_bounds[1][2])
    overall_length = axial_max - axial_min
    if overall_length <= 0:
        raise FormatError(f"TubeSegment {segment.handle} has non-positive axial length")

    default_channel = int(document.default_channel or 1)
    ends = []
    for label, end in zip(("A", "B"), segment.end_cuts):
        ends.append(
            PartEnd(
                label=label,
                shape_handle=int(end.handle),
                operation_layer=end.operation_layer,
                plane_z_equals_c_plus_ax_plus_by=_normalize_plane(
                    end.plane_z_equals_c_plus_ax_plus_by,
                    axial_min,
                ),
                plane_max_residual_mm=end.plane_max_residual_mm,
                angle_from_perpendicular_degrees=end.cut_angle_from_perpendicular_degrees,
                sampled_bounds=_normalize_bounds(end.sampled_bounds, axial_min),
                start_parameter=end.start_parameter,
                start_point=_normalize_point(end.start_point, axial_min),
            )
        )

    features = []
    for shape in segment.shapes:
        feature = _feature(shape, axial_min, default_channel)
        if feature is not None:
            features.append(feature)

    return TubePart(
        source_path=document.source_path,
        source_sha256=document.source_sha256,
        source_file_version=document.file_version,
        segment_handle=int(segment.handle),
        segment_name=segment.name,
        part_fingerprint=f"{document.source_sha256}:{segment.handle}",
        profile=_profile(segment.profile),
        axial_min=axial_min,
        axial_max=axial_max,
        overall_length=overall_length,
        ends=ends,
        features=features,
        active_layers=list(segment.active_layers),
        marking_feature_count=sum(f.feature_type == "marking" for f in features),
        cutting_feature_count=sum(f.feature_type == "cut" for f in features),
    )


def build_tube_parts(document: ZzxDocumentInfo):
    return [build_tube_part(document, segment) for segment in document.segments]


def read_tube_parts(path):
    return build_tube_parts(read_zzx_cached(path))


def describe_tube_parts(path):
    try:
        parts = read_tube_parts(path)
        return {"status": "ok", "parts": [part.to_dict() for part in parts]}
    except FormatError as exc:
        return {"status": "unsupported", "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
