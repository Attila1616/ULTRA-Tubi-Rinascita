"""Domain models for geometry-aware tube parts read from ZZX."""
from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class ProfileInfo:
    section_class: str
    thickness: float
    source_section_class: Optional[str] = None
    native_record_class: Optional[str] = None
    inferred_from_geometry: bool = False
    width: Optional[float] = None
    height: Optional[float] = None
    side: Optional[float] = None
    radius: Optional[float] = None
    diameter: Optional[float] = None


@dataclass
class ShapeInfo:
    handle: int
    kind: str
    operation_layer: Optional[int] = None
    geometry_class: Optional[str] = None
    geometry_addr: Optional[int] = None
    sampled_bounds: Optional[list] = None
    primitive_types: list = field(default_factory=list)
    point_count: int = 0
    start_parameter: Optional[float] = None
    start_point: Optional[list] = None
    is_end_cut: bool = False
    is_marking: bool = False


@dataclass
class EndCutInfo:
    handle: int
    operation_layer: Optional[int]
    sampled_bounds: Optional[list]
    plane_z_equals_c_plus_ax_plus_by: Optional[list]
    plane_max_residual_mm: Optional[float]
    cut_angle_to_axis_degrees: Optional[float]
    cut_angle_from_perpendicular_degrees: Optional[float]
    start_parameter: Optional[float] = None
    start_point: Optional[list] = None


@dataclass
class TubeSegmentInfo:
    handle: int
    name: str
    profile: ProfileInfo
    cut_off_a: int
    cut_off_b: int
    shapes: list = field(default_factory=list)
    end_cuts: list = field(default_factory=list)
    raw_bounds: Optional[list] = None
    raw_axial_length: Optional[float] = None
    active_layers: list = field(default_factory=list)
    marking_shape_count: int = 0


@dataclass
class ZzxDocumentInfo:
    source_path: str
    file_size: int
    modified_ns: int
    document_type: str
    file_version: str
    default_channel: Optional[int]
    source_sha256: str
    segments: list = field(default_factory=list)

    @property
    def active_layers(self):
        values = set()
        for segment in self.segments:
            values.update(segment.active_layers)
        return sorted(values)

    def to_dict(self):
        data = asdict(self)
        data["active_layers"] = self.active_layers
        data["segment_count"] = len(self.segments)
        return data
