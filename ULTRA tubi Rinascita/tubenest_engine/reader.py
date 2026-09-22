"""Read TubeNest ZZX geometry into serializable ULTRA domain metadata."""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import os
import struct

from .archive import Archive
from .bcmp import FormatError
from .geometry import Arc2D, Line, bounds, primitives
from .models import (
    EndCutInfo,
    ProfileInfo,
    ShapeInfo,
    TubeSegmentInfo,
    ZzxDocumentInfo,
)


_CACHE = {}
_CACHE_LIMIT = 2048
SUPPORTED_READ_VERSIONS = {"65542", "393222"}


def _record_map(archive, section):
    return {record.address: record for record in archive.stream(section).records}


def _object_channel(shape_record):
    block = next((b for b in shape_record.blocks if b.name == "Shape" and len(b.payload) >= 4), None)
    if block is None:
        return None
    channel = struct.unpack_from("<I", block.payload)[0]
    return channel if channel > 0 else None


def _profile_from_unknown_outline(source_section_class, thickness, native_record_class, curves):
    points = [point for curve in curves for point in curve.sample(64)]
    if not points:
        raise FormatError(f"Unsupported cross-section record: {native_record_class}")

    bb = bounds(points)
    width = float(bb[1][0] - bb[0][0])
    height = float(bb[1][1] - bb[0][1])
    lines = [curve for curve in curves if isinstance(curve, Line)]
    arcs = [curve for curve in curves if isinstance(curve, Arc2D)]

    radius = None
    if arcs:
        radii = [float(curve.radius) for curve in arcs]
        average = sum(radii) / len(radii)
        tolerance = max(1e-3, abs(average) * 1e-4)
        if max(abs(r - average) for r in radii) <= tolerance:
            radius = average

    # Observed TRvUnknownSection files can still contain an ordinary rounded
    # rectangle outline. Only promote them when the geometry itself proves it:
    # four straight sides + four equal-radius corner arcs.
    is_rounded_rectangle = len(lines) == 4 and len(arcs) == 4 and radius is not None
    if is_rounded_rectangle:
        effective_class = "Square" if abs(width - height) <= 1e-3 else "Rect"
        return ProfileInfo(
            effective_class,
            thickness,
            source_section_class=source_section_class,
            native_record_class=native_record_class,
            inferred_from_geometry=True,
            width=width if effective_class == "Rect" else None,
            height=height if effective_class == "Rect" else None,
            side=(width + height) / 2.0 if effective_class == "Square" else None,
            radius=radius,
        )

    return ProfileInfo(
        source_section_class or "Unknown",
        thickness,
        source_section_class=source_section_class,
        native_record_class=native_record_class,
        inferred_from_geometry=True,
        width=width,
        height=height,
    )


def _profile_info(section, curve_record, geos):
    section_class = str(section.get("SectionClass") or "")
    thickness = float(section.get("ThickNess") or 0.0)
    payload = curve_record.blocks[-1].payload if curve_record.blocks else b""
    common = {
        "source_section_class": section_class,
        "native_record_class": curve_record.name,
    }

    if curve_record.name == "TRvSquareSection":
        radius, side = struct.unpack("<dd", payload)
        return ProfileInfo(section_class, thickness, side=side, radius=radius, **common)
    if curve_record.name == "TRvRectSection":
        width, height, radius = struct.unpack("<ddd", payload)
        return ProfileInfo(section_class, thickness, width=width, height=height, radius=radius, **common)
    if curve_record.name == "TRvCircleSection":
        radius = struct.unpack("<d", payload)[0]
        return ProfileInfo(section_class, thickness, radius=radius, diameter=2.0 * radius, **common)
    if curve_record.name == "TRvUnknownSection":
        geometry = section.find("Geometry")
        if geometry is None or geometry.get("GeoAddr") is None:
            raise FormatError("TRvUnknownSection has no cross-section geometry")
        geo_addr = int(geometry.get("GeoAddr"))
        geometry_record = geos.get(geo_addr)
        if geometry_record is None:
            raise FormatError(f"Missing cross-section LiteGeos record at {geo_addr}")
        return _profile_from_unknown_outline(
            section_class or "Unknown",
            thickness,
            curve_record.name,
            primitives(geometry_record),
        )

    raise FormatError(f"Unsupported cross-section record: {curve_record.name}")


def _solve3(matrix, vector):
    a = [list(map(float, row)) + [float(rhs)] for row, rhs in zip(matrix, vector)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(a[row][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ValueError("Singular plane fit")
        a[col], a[pivot] = a[pivot], a[col]
        scale = a[col][col]
        for j in range(col, 4):
            a[col][j] /= scale
        for row in range(3):
            if row == col:
                continue
            factor = a[row][col]
            for j in range(col, 4):
                a[row][j] -= factor * a[col][j]
    return [a[i][3] for i in range(3)]


def _fit_plane(points):
    points = [tuple(map(float, p)) for p in points if len(p) >= 3]
    if len(points) < 3:
        return None, None, None, None

    n = float(len(points))
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sz = sum(p[2] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    syy = sum(p[1] * p[1] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    sxz = sum(p[0] * p[2] for p in points)
    syz = sum(p[1] * p[2] for p in points)
    try:
        c, ax, by = _solve3(
            [[n, sx, sy], [sx, sxx, sxy], [sy, sxy, syy]],
            [sz, sxz, syz],
        )
    except ValueError:
        return None, None, None, None

    residual = max(abs(p[2] - (c + ax * p[0] + by * p[1])) for p in points)
    angle_axis = math.degrees(math.atan2(1.0, math.hypot(ax, by)))
    angle_perpendicular = 90.0 - angle_axis
    return [c, ax, by], residual, angle_axis, angle_perpendicular


def _shape_points(shape_element, geos):
    geometry = shape_element.find("Geometry")
    if geometry is None or geometry.get("GeoAddr") is None:
        return [], None, None, []
    addr = int(geometry.get("GeoAddr"))
    record = geos.get(addr)
    if record is None:
        raise FormatError(f"Missing LiteGeos record at {addr}")
    curves = primitives(record)
    points = [point for curve in curves for point in curve.sample(64)]
    return points, geometry.get("Class"), addr, curves


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_zzx(path):
    path = os.path.abspath(path)
    stat = os.stat(path)
    source_sha256 = _sha256_file(path)
    archive = Archive.read(path)
    root = archive.xml("content.xml")
    doc_type = str(root.get("DocType") or "")
    file_version = str(root.get("FileVer") or "")
    if doc_type != "NestResults3D" or file_version not in SUPPORTED_READ_VERSIONS:
        raise FormatError(f"Unsupported ZZX dialect: {doc_type} / {file_version}")

    header = root.find("Header")
    default_channel = None
    if header is not None and header.get("DefaultChannel") is not None:
        default_channel = int(header.get("DefaultChannel"))

    curve_records = _record_map(archive, "Curves")
    shape_records = _record_map(archive, "Shapes")
    geos = _record_map(archive, "LiteGeos")
    shapes_xml = {
        int(e.get("Handle")): e
        for e in archive.xml("Shapes/content.xml")
        if e.tag != "MD5" and e.get("Handle") is not None
    }

    segments = []
    for segment in archive.xml("Segments/content.xml").findall("TubeSegment"):
        handle = int(segment.get("Handle"))
        section = segment.find("CrossSection")
        if section is None:
            raise FormatError(f"TubeSegment {handle} has no CrossSection")
        curve_addr = int(section.get("DataAddr"))
        curve_record = curve_records.get(curve_addr)
        if curve_record is None:
            raise FormatError(f"Missing Curves record at {curve_addr}")
        profile = _profile_info(section, curve_record, geos)

        cut_a = int(segment.get("CutOffA"))
        cut_b = int(segment.get("CutOffB"))
        end_handles = {cut_a, cut_b}
        shape_infos = []
        points_by_handle = {}
        layers = set()
        all_end_points = []
        marking_count = 0

        shape_refs = segment.find("Shapes")
        for ref in list(shape_refs) if shape_refs is not None else []:
            shape_handle = int(ref.get("Handle"))
            xml_shape = shapes_xml.get(shape_handle)
            if xml_shape is None:
                raise FormatError(f"Missing shape XML handle {shape_handle}")
            data_addr = int(xml_shape.get("DataAddr"))
            shape_record = shape_records.get(data_addr)
            if shape_record is None:
                raise FormatError(f"Missing Shapes record at {data_addr}")

            channel = _object_channel(shape_record)
            if channel is not None:
                layers.add(channel)

            points, geometry_class, geometry_addr, curves = _shape_points(xml_shape, geos)
            points_by_handle[shape_handle] = points
            is_end = shape_handle in end_handles
            if is_end:
                all_end_points.extend(points)
            is_marking = channel is not None and channel != (default_channel or 1)
            if is_marking:
                marking_count += 1

            primitive_names = dict(Counter(type(curve).__name__ for curve in curves))
            shape_infos.append(
                ShapeInfo(
                    handle=shape_handle,
                    kind=xml_shape.tag,
                    operation_layer=channel,
                    geometry_class=geometry_class,
                    geometry_addr=geometry_addr,
                    sampled_bounds=bounds(points) if points else None,
                    primitive_types=primitive_names,
                    point_count=len(points),
                    is_end_cut=is_end,
                    is_marking=is_marking,
                )
            )

        end_cuts = []
        shape_by_handle = {shape.handle: shape for shape in shape_infos}
        for end_handle in (cut_a, cut_b):
            points = points_by_handle.get(end_handle, [])
            plane, residual, angle_axis, angle_perp = _fit_plane(points)
            shape_info = shape_by_handle.get(end_handle)
            end_cuts.append(
                EndCutInfo(
                    handle=end_handle,
                    operation_layer=shape_info.operation_layer if shape_info else None,
                    sampled_bounds=bounds(points) if points else None,
                    plane_z_equals_c_plus_ax_plus_by=plane,
                    plane_max_residual_mm=residual,
                    cut_angle_to_axis_degrees=angle_axis,
                    cut_angle_from_perpendicular_degrees=angle_perp,
                )
            )

        segment_bounds = bounds(all_end_points) if all_end_points else None
        raw_axial_length = (
            segment_bounds[1][2] - segment_bounds[0][2]
            if segment_bounds and len(segment_bounds[0]) >= 3
            else None
        )
        segments.append(
            TubeSegmentInfo(
                handle=handle,
                name=str(segment.get("Name") or ""),
                profile=profile,
                cut_off_a=cut_a,
                cut_off_b=cut_b,
                shapes=shape_infos,
                end_cuts=end_cuts,
                raw_bounds=segment_bounds,
                raw_axial_length=raw_axial_length,
                active_layers=sorted(layers),
                marking_shape_count=marking_count,
            )
        )

    return ZzxDocumentInfo(
        source_path=path,
        file_size=stat.st_size,
        modified_ns=stat.st_mtime_ns,
        document_type=doc_type,
        file_version=file_version,
        default_channel=default_channel,
        source_sha256=source_sha256,
        segments=segments,
    )


def read_zzx_cached(path):
    path = os.path.abspath(path)
    stat = os.stat(path)
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(path)
    if cached and cached[0] == signature:
        return cached[1]
    document = read_zzx(path)
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[path] = (signature, document)
    return document


def clear_cache():
    _CACHE.clear()


def describe_zzx(path):
    """Return JSON-safe metadata without allowing parser failures to break ULTRA."""
    try:
        document = read_zzx_cached(path)
        return {"status": "ok", "document": document.to_dict()}
    except FormatError as exc:
        return {"status": "unsupported", "error": str(exc)}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
