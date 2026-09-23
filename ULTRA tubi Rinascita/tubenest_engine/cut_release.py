"""Final-position release-start repair and shared-cut deduplication.

Derived from the confirmed cut-release handoff. Duplicate removal is restricted
by caller-supplied adjacent/shared-boundary identity and then geometrically
verified; no global same-Z merging is performed.
"""
import math
import struct

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree

from .archive import xml_bytes
from .geometry import bounds, primitives
from .release_starts import select_face_start, select_round_start


CUTOFF_FLAG = 32
EXCLUSION_WORK_BIT = 0x2
DEFAULT_DUPLICATE_TOLERANCE_MM = 0.002
DEFAULT_JOIN_TOLERANCE_MM = 0.01
DEFAULT_PLANARITY_TOLERANCE_MM = 0.002
DEFAULT_DEDUP_TOLERANCE_MM = DEFAULT_DUPLICATE_TOLERANCE_MM


def shape_channel(record):
    block = next(
        block for block in record.blocks
        if block.name == "Shape"
    )
    if len(block.payload) < 12:
        raise ValueError("Unexpected Shape payload")
    return struct.unpack_from("<I", block.payload, 0)[0]


def work_flags(record):
    block = next(
        block for block in record.blocks
        if block.name == "Shape"
    )
    if len(block.payload) < 12:
        raise ValueError("Unexpected Shape payload")
    return struct.unpack_from("<I", block.payload, 8)[0]


def active_layer1(record):
    return (
        shape_channel(record) == 1
        and not (work_flags(record) & EXCLUSION_WORK_BIT)
    )


def enabled_machining(record):
    """Enabled machining operation regardless of its preserved channel."""
    return (
        shape_channel(record) > 0
        and not (work_flags(record) & EXCLUSION_WORK_BIT)
    )


def curve_flags(record):
    block = next(
        block for block in record.blocks
        if block.name == "Curve"
    )
    if len(block.payload) < 44:
        raise ValueError("Unexpected Curve payload")
    return struct.unpack_from("<I", block.payload, 12)[0]


def _record_handle(record):
    block = next(
        block for block in record.blocks
        if block.name == "Object"
    )
    if len(block.payload) < 4:
        raise ValueError("Unexpected Object payload")
    return str(
        struct.unpack_from("<I", block.payload, 0)[0]
    )


def sampled_distance(left, right):
    """Bidirectional sampled/refined evidence, not a certified Hausdorff bound."""
    def direction(first_curves, second_curves):
        coarse = [
            (index, curve.at(step / 64))
            for index, curve in enumerate(second_curves)
            for step in range(65)
        ]
        tree = cKDTree([point for _, point in coarse])
        worst = 0.0

        for curve in first_curves:
            for point in curve.sample(48):
                _, ids = tree.query(
                    point,
                    k=min(4, len(coarse)),
                )
                best = float("inf")
                for coarse_index in {
                    coarse[int(raw_index)][0]
                    for raw_index in np.atleast_1d(ids)
                }:
                    candidate = second_curves[coarse_index]

                    def distance(normalized_t):
                        return sum(
                            (left_value - right_value) ** 2
                            for left_value, right_value in zip(
                                point,
                                candidate.at(normalized_t),
                            )
                        )

                    optimum = minimize_scalar(
                        distance,
                        bounds=(0, 1),
                        method="bounded",
                        options={"xatol": 1e-12},
                    )
                    best = min(
                        best,
                        optimum.fun,
                        distance(0),
                        distance(1),
                    )
                worst = max(worst, math.sqrt(best))
        return worst

    return max(
        direction(left, right),
        direction(right, left),
    )


def whole_planar_contour(
    curves,
    *,
    join_tolerance_mm=DEFAULT_JOIN_TOLERANCE_MM,
    planarity_tolerance_mm=DEFAULT_PLANARITY_TOLERANCE_MM,
):
    """Validate one saved cutoff loop without modifying its geometry.

    Junction tolerance only answers whether successive serialized primitives
    belong to one loop. It is deliberately separate from the much tighter
    tolerance used to decide whether two different contours coincide.
    """
    if not curves:
        return False
    if any(
        math.dist(curve.at(1), next_curve.at(0)) > join_tolerance_mm
        for curve, next_curve in zip(curves, curves[1:] + curves[:1])
    ):
        return False

    points = np.asarray(
        [point for curve in curves for point in curve.sample(16)]
    )
    center = points.mean(axis=0)
    _, singular, vectors = np.linalg.svd(
        points - center,
        full_matrices=False,
    )
    return (
        len(singular) == 3
        and singular[1] > planarity_tolerance_mm
        and max(abs((points - center) @ vectors[-1]))
        <= planarity_tolerance_mm
    )


def _normalized_settings(record):
    output = []
    for block in record.blocks:
        if block.name == "Object":
            continue

        raw = bytearray(block.payload)
        if block.name == "Shape":
            if len(raw) < 12:
                raise ValueError("Unexpected Shape payload")
            raw[:4] = bytes(4)
            flags = struct.unpack_from(
                "<I",
                raw,
                8,
            )[0]
            struct.pack_into(
                "<I",
                raw,
                8,
                flags & ~EXCLUSION_WORK_BIT,
            )

        if block.name == "Curve":
            if len(raw) < 44:
                raise ValueError("Unexpected Curve payload")
            raw[4:12] = bytes(8)
            for offset in (20, 28, 36):
                if (
                    struct.unpack_from(
                        "<d",
                        raw,
                        offset,
                    )[0]
                    == 0
                ):
                    struct.pack_into(
                        "<d",
                        raw,
                        offset,
                        0.0,
                    )

        output.append(
            (
                block.name,
                block.version,
                bytes(raw),
            )
        )

    return output, record.tail


def _shape_geometry(
    shape_xml,
    lite_by_addr,
    tag,
):
    element = shape_xml.find(tag)
    if element is None:
        return None

    geo_addr = element.get("GeoAddr")
    if geo_addr is None:
        return None

    record = lite_by_addr.get(int(geo_addr))
    if record is None:
        raise ValueError(
            f"Missing LiteGeos record {geo_addr}"
        )
    return list(primitives(record))


def _verify_duplicate_pair(
    left_handle,
    right_handle,
    *,
    shapes_by_handle,
    records_by_handle,
    lite_by_addr,
    duplicate_tolerance_mm,
    join_tolerance_mm,
    planarity_tolerance_mm,
):
    left_xml = shapes_by_handle[left_handle]
    right_xml = shapes_by_handle[right_handle]
    left_record = records_by_handle[left_handle]
    right_record = records_by_handle[right_handle]

    if not (
        curve_flags(left_record)
        & CUTOFF_FLAG
    ):
        raise ValueError(
            f"Shared-boundary Shape {left_handle} is not a cutoff"
        )
    if not (
        curve_flags(right_record)
        & CUTOFF_FLAG
    ):
        raise ValueError(
            f"Shared-boundary Shape {right_handle} is not a cutoff"
        )

    left_outer = _shape_geometry(
        left_xml,
        lite_by_addr,
        "Geometry",
    )
    right_outer = _shape_geometry(
        right_xml,
        lite_by_addr,
        "Geometry",
    )
    if not left_outer or not right_outer:
        raise ValueError(
            "Shared cutoff requires explicit outer geometry"
        )

    if not whole_planar_contour(
        left_outer,
        join_tolerance_mm=join_tolerance_mm,
        planarity_tolerance_mm=planarity_tolerance_mm,
    ):
        raise ValueError(
            f"Shared-boundary Shape {left_handle} is not "
            "a closed planar contour"
        )
    if not whole_planar_contour(
        right_outer,
        join_tolerance_mm=join_tolerance_mm,
        planarity_tolerance_mm=planarity_tolerance_mm,
    ):
        raise ValueError(
            f"Shared-boundary Shape {right_handle} is not "
            "a closed planar contour"
        )

    left_box = np.asarray(
        bounds(
            point
            for curve in left_outer
            for point in curve.sample(48)
        )
    )
    right_box = np.asarray(
        bounds(
            point
            for curve in right_outer
            for point in curve.sample(48)
        )
    )
    if (
        np.max(
            abs(left_box - right_box)
        )
        > duplicate_tolerance_mm
    ):
        raise ValueError(
            f"Shared-boundary Shapes {left_handle}/"
            f"{right_handle} have a real gap larger than "
            f"{duplicate_tolerance_mm:g} mm"
        )

    left_inner = _shape_geometry(
        left_xml,
        lite_by_addr,
        "InnerGeometry",
    )
    right_inner = _shape_geometry(
        right_xml,
        lite_by_addr,
        "InnerGeometry",
    )
    if (
        (left_inner is None)
        != (right_inner is None)
    ):
        raise ValueError(
            f"Shared-boundary Shapes {left_handle}/"
            f"{right_handle} disagree on inner contour"
        )

    outer_error = sampled_distance(
        left_outer,
        right_outer,
    )
    if outer_error > duplicate_tolerance_mm:
        raise ValueError(
            f"Shared-boundary Shapes {left_handle}/"
            f"{right_handle} outer contours differ by "
            f"{outer_error:.6g} mm"
        )

    inner_error = None
    if left_inner:
        inner_error = sampled_distance(
            left_inner,
            right_inner,
        )
        if inner_error > duplicate_tolerance_mm:
            raise ValueError(
                f"Shared-boundary Shapes {left_handle}/"
                f"{right_handle} inner contours differ by "
                f"{inner_error:.6g} mm"
            )

    if (
        _normalized_settings(left_record)
        != _normalized_settings(right_record)
    ):
        raise ValueError(
            f"Coincident cutoffs {left_handle}/"
            f"{right_handle} have conflicting process "
            "metadata; review required"
        )

    return {
        "a": left_handle,
        "b": right_handle,
        "sampled_outer_error_mm": float(
            outer_error
        ),
        "sampled_inner_error_mm": (
            None
            if inner_error is None
            else float(inner_error)
        ),
    }


def _assert_no_unknown_removed_consumers(
    archive,
    shapes_root,
    removed,
):
    removed = set(removed)
    for name in archive.entries:
        if (
            not name.endswith("content.xml")
            or name
            in (
                "Shapes/content.xml",
                "Segments/content.xml",
            )
        ):
            continue

        root = archive.xml(name)
        for element in root.iter():
            if (
                element.get("Handle") in removed
                or element.get("CopyHandle") in removed
            ):
                raise ValueError(
                    "Removed Shape handle is referenced by "
                    f"{name}; review required"
                )

    for element in shapes_root:
        if (
            element.tag == "MD5"
            or element.get("Handle") in removed
        ):
            continue
        if element.get("CopyHandle") in removed:
            raise ValueError(
                "Removed Shape is a surviving CopyHandle source"
            )


def repair_single_segment_cut_release(
    archive,
    *,
    profile_kind,
    outside_width=None,
    outside_height=None,
    outside_diameter=None,
    shared_pairs=(),
    release_handles=(),
    duplicate_tolerance_mm=DEFAULT_DUPLICATE_TOLERANCE_MM,
    join_tolerance_mm=DEFAULT_JOIN_TOLERANCE_MM,
    planarity_tolerance_mm=DEFAULT_PLANARITY_TOLERANCE_MM,
):
    """Repair exact release starts and adjacent shared-cut duplicates.

    Geometry must already be in final positioned-stock coordinates,
    with +Z toward the retained/chuck side. Round start selection is
    mathematically/file validated by the handoff but remains pending
    manual TubePro confirmation.
    """
    duplicate_tolerance_mm = float(duplicate_tolerance_mm)
    join_tolerance_mm = float(join_tolerance_mm)
    planarity_tolerance_mm = float(planarity_tolerance_mm)
    if (
        not math.isfinite(duplicate_tolerance_mm)
        or not 0 < duplicate_tolerance_mm <= 0.01
    ):
        raise ValueError("Duplicate tolerance must be >0 and <=0.01 mm")
    if (
        not math.isfinite(join_tolerance_mm)
        or not 0 < join_tolerance_mm <= 0.05
    ):
        raise ValueError("Contour join tolerance must be >0 and <=0.05 mm")
    if (
        not math.isfinite(planarity_tolerance_mm)
        or not 0 < planarity_tolerance_mm <= 0.01
    ):
        raise ValueError("Planarity tolerance must be >0 and <=0.01 mm")

    segments_root = archive.xml(
        "Segments/content.xml"
    )
    segments = segments_root.findall(
        "TubeSegment"
    )
    if len(segments) != 1:
        raise ValueError(
            "Cut-release repair requires one flattened TubeSegment"
        )

    segment = segments[0]
    refs = segment.find("Shapes")
    if refs is None:
        raise ValueError(
            "Flattened TubeSegment has no Shapes list"
        )
    order = [
        ref.get("Handle")
        for ref in refs
    ]

    shapes_root = archive.xml(
        "Shapes/content.xml"
    )
    shapes_by_handle = {
        element.get("Handle"): element
        for element in shapes_root
        if element.tag != "MD5"
    }

    shape_stream = archive.stream("Shapes")
    records_by_handle = {
        _record_handle(record): record
        for record in shape_stream.records
    }
    lite_by_addr = {
        record.address: record
        for record in archive.stream(
            "LiteGeos"
        ).records
    }

    removed_to_retained = {}
    duplicate_audit = []
    rejected_pairs = []
    disabled_groups = []

    for raw_left, raw_right in shared_pairs:
        left_handle = removed_to_retained.get(
            str(raw_left),
            str(raw_left),
        )
        right_handle = removed_to_retained.get(
            str(raw_right),
            str(raw_right),
        )

        if left_handle == right_handle:
            continue

        if (
            left_handle not in shapes_by_handle
            or right_handle not in shapes_by_handle
        ):
            raise ValueError(
                f"Shared-boundary Shape {left_handle}/"
                f"{right_handle} is missing"
            )

        if (
            left_handle not in order
            or right_handle not in order
        ):
            raise ValueError(
                "Shared-boundary Shape is missing from "
                "operation list"
            )

        try:
            comparison = _verify_duplicate_pair(
                left_handle,
                right_handle,
                shapes_by_handle=shapes_by_handle,
                records_by_handle=records_by_handle,
                lite_by_addr=lite_by_addr,
                duplicate_tolerance_mm=duplicate_tolerance_mm,
                join_tolerance_mm=join_tolerance_mm,
                planarity_tolerance_mm=planarity_tolerance_mm,
            )
        except ValueError as exc:
            # A common-line candidate is not automatically a duplicate. If
            # complete contour/process verification fails, preserve both
            # operations exactly as they are rather than deleting a real cut.
            rejected_pairs.append(
                {
                    "a": left_handle,
                    "b": right_handle,
                    "reason": str(exc),
                }
            )
            continue

        duplicate_audit.append(comparison)

        pair = [
            left_handle,
            right_handle,
        ]
        active = [
            handle
            for handle in pair
            if active_layer1(
                records_by_handle[handle]
            )
        ]

        if not active:
            disabled_groups.append(pair)
            continue

        keep = min(
            active,
            key=order.index,
        )
        for handle in pair:
            if handle != keep:
                removed_to_retained[
                    handle
                ] = keep

    for attribute in (
        "CutOffA",
        "CutOffB",
    ):
        handle = segment.get(attribute)
        if handle in removed_to_retained:
            segment.set(
                attribute,
                removed_to_retained[handle],
            )

    if (
        segment.get("CutOffA")
        == segment.get("CutOffB")
    ):
        raise ValueError(
            "Deduplication would collapse both stock ends"
        )

    _assert_no_unknown_removed_consumers(
        archive,
        shapes_root,
        removed_to_retained,
    )

    for ref in list(refs):
        if (
            ref.get("Handle")
            in removed_to_retained
        ):
            refs.remove(ref)

    for handle in removed_to_retained:
        shapes_root.remove(
            shapes_by_handle[handle]
        )

    remapped_release_handles = []
    seen = set()
    for handle in (
        list(release_handles)
        + [
            segment.get("CutOffA"),
            segment.get("CutOffB"),
        ]
    ):
        handle = removed_to_retained.get(
            str(handle),
            str(handle),
        )
        if handle not in seen:
            remapped_release_handles.append(
                handle
            )
            seen.add(handle)

    removed_flags = {
        handle: [
            shape_channel(
                records_by_handle[handle]
            ),
            work_flags(
                records_by_handle[handle]
            ),
        ]
        for handle in removed_to_retained
    }

    starts = []
    for handle in remapped_release_handles:
        shape_xml = shapes_by_handle.get(
            handle
        )
        record = records_by_handle.get(
            handle
        )
        if (
            shape_xml is None
            or record is None
            or handle in removed_to_retained
        ):
            continue

        if (
            not enabled_machining(record)
            or not (
                curve_flags(record)
                & CUTOFF_FLAG
            )
        ):
            continue

        curves = _shape_geometry(
            shape_xml,
            lite_by_addr,
            "Geometry",
        )
        if not curves:
            raise ValueError(
                f"Cutoff {handle} has no explicit outer geometry"
            )

        if profile_kind == "Circle":
            diameter = float(
                outside_diameter or 0.0
            )
            if (
                not math.isfinite(diameter)
                or diameter <= 0
            ):
                raise ValueError(
                    "Invalid circle diameter for release repair"
                )
            selected = select_round_start(
                curves,
                diameter / 2.0,
            )

        elif profile_kind in (
            "Square",
            "Rect",
        ):
            width = float(
                outside_width or 0.0
            )
            height = float(
                outside_height or 0.0
            )
            if not all(
                math.isfinite(value)
                and value > 0
                for value in (
                    width,
                    height,
                )
            ):
                raise ValueError(
                    "Invalid flat profile dimensions for release repair"
                )
            selected = select_face_start(
                curves,
                width,
                height,
            )

        else:
            raise ValueError(
                f"Unsupported profile {profile_kind!r} "
                "for release repair"
            )

        selected = dict(selected)
        selected["parameter"] = float(
            selected["parameter"]
        )
        selected["point"] = tuple(
            float(value)
            for value in selected["point"]
        )
        selected["child_index"] = int(
            selected["child_index"]
        )
        selected["child_t"] = float(
            selected["child_t"]
        )
        selected["minimum_z"] = float(
            selected["minimum_z"]
        )

        curve_block = next(
            block
            for block in record.blocks
            if block.name == "Curve"
        )
        if len(curve_block.payload) < 44:
            raise ValueError(
                "Unexpected Curve payload"
            )

        raw = bytearray(
            curve_block.payload
        )
        before = struct.unpack_from(
            "<d",
            raw,
            4,
        )[0]
        struct.pack_into(
            "<d",
            raw,
            4,
            selected["parameter"],
        )
        curve_block.payload = bytes(raw)

        starts.append(
            {
                "handle": handle,
                "old_parameter": before,
                **selected,
            }
        )

    kept_records = [
        record
        for record in shape_stream.records
        if (
            _record_handle(record)
            not in removed_to_retained
        )
    ]
    shape_stream.records = kept_records
    archive.entries[
        "Shapes/data.bin"
    ] = shape_stream.encode()

    records_by_handle = {
        _record_handle(record): record
        for record in kept_records
    }
    for handle, element in (
        shapes_by_handle.items()
    ):
        if handle in removed_to_retained:
            continue
        element.set(
            "DataAddr",
            str(
                records_by_handle[
                    handle
                ].address
            ),
        )

    archive.entries[
        "Shapes/content.xml"
    ] = xml_bytes(shapes_root)
    archive.entries[
        "Segments/content.xml"
    ] = xml_bytes(segments_root)

    archive.refresh_checksums()
    archive.validate()

    return {
        "removed_to_retained": (
            removed_to_retained
        ),
        "duplicate_comparisons": (
            duplicate_audit
        ),
        "rejected_shared_pairs": (
            rejected_pairs
        ),
        "disabled_duplicate_groups": (
            disabled_groups
        ),
        "start_points": starts,
        "removed_channel_work_flags": (
            removed_flags
        ),
        "profile": profile_kind,
        "candidate_shared_pairs": [
            [str(left), str(right)]
            for left, right in shared_pairs
        ],
        "duplicate_tolerance_mm": duplicate_tolerance_mm,
        "join_tolerance_mm": join_tolerance_mm,
        "planarity_tolerance_mm": planarity_tolerance_mm,
        "comparison_tolerance_mm": duplicate_tolerance_mm,
        "axis_convention": (
            "+Z toward chuck; positioned-stock coordinates "
            "are nonnegative"
        ),
        "round_acceptance": (
            "mathematical/file-validation tests passed; "
            "manual TubePro confirmation pending"
            if profile_kind == "Circle"
            else (
                "square/rectangular rule derived from "
                "TubePro-confirmed fixture"
            )
        ),
    }
