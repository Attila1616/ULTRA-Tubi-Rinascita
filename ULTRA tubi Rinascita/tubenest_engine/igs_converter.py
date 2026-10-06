"""IGES/IGS -> ZZX conversion for one straight hollow tube.

The converter deliberately treats the IGES world orientation as arbitrary.
It detects the tube's longitudinal axis from the dominant long straight edges,
finds an intact perpendicular cross-section, aligns square/rectangular stock to
its real local faces, and finally writes all machining contours in ZZX-local
coordinates with the tube axis on +Z.

Supported stock profiles:
- round tube
- square tube with rounded corners
- rectangular tube with rounded corners

The IGES body must describe one closed hollow tube. Through holes/notches are
supported when their cut faces connect the outside and inside stock surfaces.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import math
import os
from pathlib import Path
import struct
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from scipy.optimize import linear_sum_assignment

from .archive import Archive, xml_bytes
from .bcmp import Block, FormatError, Record, vector
from .geometry import (
    Arc2D,
    Line,
    Spline,
    circle_quarters,
    composite,
    rounded_rectangle,
)


TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "templates"
    / "igs_to_zzx_base.zzx"
)

SEW_TOLERANCE_MM = 1e-4
SECTION_DEFLECTION_MM = 0.035
MESH_DEFLECTION_MM = 0.2
FEATURE_PLANE_TOLERANCE_MM = 0.04
END_MATCH_TOLERANCE_MM = 0.08
DEGENERATE_EDGE_LENGTH_MM = 1e-3


class IgsConversionError(ValueError):
    pass


def _ocp():
    try:
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeSolid,
            BRepBuilderAPI_Sewing,
        )
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.GCPnts import (
            GCPnts_AbscissaPoint,
            GCPnts_QuasiUniformDeflection,
        )
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.IGESControl import IGESControl_Reader
        from OCP.TopAbs import (
            TopAbs_EDGE,
            TopAbs_FACE,
            TopAbs_SHELL,
            TopAbs_VERTEX,
        )
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopLoc import TopLoc_Location
        from OCP.TopoDS import TopoDS
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
    except ImportError as exc:
        raise IgsConversionError(
            "Modulo CAD OpenCascade non disponibile. "
            "Installa le dipendenze aggiornate del programma."
        ) from exc

    return {
        "BRep_Tool": BRep_Tool,
        "BRepAdaptor_Curve": BRepAdaptor_Curve,
        "BRepAlgoAPI_Section": BRepAlgoAPI_Section,
        "BRepBuilderAPI_MakeSolid": BRepBuilderAPI_MakeSolid,
        "BRepBuilderAPI_Sewing": BRepBuilderAPI_Sewing,
        "BRepMesh_IncrementalMesh": BRepMesh_IncrementalMesh,
        "GCPnts_AbscissaPoint": GCPnts_AbscissaPoint,
        "GCPnts_QuasiUniformDeflection": GCPnts_QuasiUniformDeflection,
        "IFSelect_RetDone": IFSelect_RetDone,
        "IGESControl_Reader": IGESControl_Reader,
        "TopAbs_EDGE": TopAbs_EDGE,
        "TopAbs_FACE": TopAbs_FACE,
        "TopAbs_SHELL": TopAbs_SHELL,
        "TopAbs_VERTEX": TopAbs_VERTEX,
        "TopExp_Explorer": TopExp_Explorer,
        "TopLoc_Location": TopLoc_Location,
        "TopoDS": TopoDS,
        "gp_Dir": gp_Dir,
        "gp_Pln": gp_Pln,
        "gp_Pnt": gp_Pnt,
    }


def _unit(value):
    value = np.asarray(value, dtype=float)
    length = float(np.linalg.norm(value))
    if length <= 1e-12:
        raise IgsConversionError("Impossibile normalizzare un asse CAD nullo.")
    return value / length


def _canonical_axis(value):
    value = _unit(value)
    for component in value:
        if abs(float(component)) > 1e-8:
            if component < 0.0:
                value = -value
            break
    return value


def _edge_info(edge, api):
    adaptor = api["BRepAdaptor_Curve"](edge)
    start_parameter = adaptor.FirstParameter()
    end_parameter = adaptor.LastParameter()
    start = adaptor.Value(start_parameter)
    finish = adaptor.Value(end_parameter)
    a = np.array([start.X(), start.Y(), start.Z()], dtype=float)
    b = np.array([finish.X(), finish.Y(), finish.Z()], dtype=float)
    chord = float(np.linalg.norm(b - a))
    try:
        length = float(
            api["GCPnts_AbscissaPoint"].Length_s(
                adaptor,
                start_parameter,
                end_parameter,
            )
        )
    except Exception:
        length = chord
    return a, b, adaptor, length, chord


def _edge_points(
    edge,
    api,
    *,
    deflection=SECTION_DEFLECTION_MM,
    max_points=128,
):
    a, b, adaptor, length, chord = _edge_info(edge, api)

    if (
        chord > 1e-9
        and length > 1e-9
        and abs(length - chord) / length < 2e-5
    ):
        return [a, b]

    try:
        sampler = api["GCPnts_QuasiUniformDeflection"](
            adaptor,
            float(deflection),
        )
        if (
            sampler.IsDone()
            and 2 <= sampler.NbPoints() <= int(max_points)
        ):
            return [
                np.array(
                    [
                        sampler.Value(index).X(),
                        sampler.Value(index).Y(),
                        sampler.Value(index).Z(),
                    ],
                    dtype=float,
                )
                for index in range(1, sampler.NbPoints() + 1)
            ]
    except Exception:
        pass

    count = max(
        3,
        min(
            int(max_points),
            int(
                math.ceil(
                    max(length, chord)
                    / max(float(deflection) * 5.0, 0.25)
                )
            )
            + 1,
        ),
    )
    start_parameter = adaptor.FirstParameter()
    end_parameter = adaptor.LastParameter()
    result = []
    for index in range(count):
        parameter = (
            start_parameter
            + (end_parameter - start_parameter)
            * index
            / float(count - 1)
        )
        point = adaptor.Value(parameter)
        result.append(
            np.array(
                [point.X(), point.Y(), point.Z()],
                dtype=float,
            )
        )
    return result


def _point_key(point, tolerance=1e-4):
    return tuple(
        np.round(
            np.asarray(point, dtype=float) / float(tolerance)
        ).astype(np.int64)
    )


def _chain_edges(
    edges,
    api,
    *,
    ignore_degenerate=False,
):
    edge_data = []
    for edge in edges:
        points = _edge_points(
            edge,
            api,
            max_points=512,
        )
        sampled_length = float(
            np.sum(
                np.linalg.norm(
                    np.diff(
                        np.asarray(points, dtype=float),
                        axis=0,
                    ),
                    axis=1,
                )
            )
        )
        # Some CAD exporters emit microscopic/degenerated seam edges around
        # otherwise perfectly valid rounded corners. They create ambiguous
        # graph branches and can make a closed end contour look open. Ignore
        # only sub-micron edges; real section connector edges are much longer.
        if (
            ignore_degenerate
            and sampled_length <= DEGENERATE_EDGE_LENGTH_MM
        ):
            continue
        edge_data.append((edge, points))

    adjacency = defaultdict(list)

    for index, (_edge, points) in enumerate(edge_data):
        adjacency[_point_key(points[0])].append((index, 0))
        adjacency[_point_key(points[-1])].append((index, 1))

    used = set()
    loops = []

    for first_index in range(len(edge_data)):
        if first_index in used:
            continue

        used.add(first_index)
        chain = list(edge_data[first_index][1])
        start_key = _point_key(chain[0])
        current_key = _point_key(chain[-1])

        guard = 0
        while current_key != start_key and guard <= len(edge_data) + 2:
            guard += 1
            candidates = [
                item
                for item in adjacency[current_key]
                if item[0] not in used
            ]
            if not candidates:
                break

            next_index, endpoint_index = candidates[0]
            used.add(next_index)
            points = edge_data[next_index][1]
            if endpoint_index == 0:
                chain.extend(points[1:])
            else:
                chain.extend(reversed(points[:-1]))
            current_key = _point_key(chain[-1])

        if np.linalg.norm(chain[-1] - chain[0]) > 2e-3:
            raise IgsConversionError(
                "Il modello IGS contiene un contorno di taglio aperto."
            )

        chain[-1] = chain[0].copy()
        loops.append(chain)

    return loops


def _polygon_area(points_3d, project):
    points = np.asarray([project(point) for point in points_3d])
    return 0.5 * abs(
        float(
            np.sum(
                points[:, 0] * np.roll(points[:, 1], -1)
                - points[:, 1] * np.roll(points[:, 0], -1)
            )
        )
    )


def _pair_feature_boundary_loops(
    outer_loops,
    inner_loops,
):
    """Pair disconnected outer/inner openings belonging to one cut surface.

    A single CAD face can legitimately contain multiple disconnected wall
    regions. The common example is a through-hole crossing both walls of a
    hollow tube: OpenCascade may expose one cylindrical cut face whose outer
    boundary contains two loops and whose inner boundary also contains two
    loops. ZZX needs those as two independent machining contours.

    Pair each outside opening with the spatially nearest inside opening using a
    globally optimal assignment. Keeping this independent of world orientation
    also makes the rule valid for diagonally oriented source tubes.
    """
    outer_loops = list(outer_loops or [])
    inner_loops = list(inner_loops or [])

    if not outer_loops or not inner_loops:
        raise IgsConversionError(
            "Una lavorazione IGS non ha un contorno esterno/interno completo."
        )

    if len(outer_loops) != len(inner_loops):
        raise IgsConversionError(
            "Una lavorazione IGS contiene un numero diverso di aperture "
            "esterne e interne e non può ancora essere convertita in modo "
            "sicuro."
        )

    if len(outer_loops) == 1:
        return [(outer_loops[0], inner_loops[0])]

    outer_centers = np.asarray(
        [
            np.asarray(loop, dtype=float).mean(axis=0)
            for loop in outer_loops
        ]
    )
    inner_centers = np.asarray(
        [
            np.asarray(loop, dtype=float).mean(axis=0)
            for loop in inner_loops
        ]
    )
    costs = np.linalg.norm(
        outer_centers[:, None, :]
        - inner_centers[None, :, :],
        axis=2,
    )
    outer_indices, inner_indices = linear_sum_assignment(costs)

    pairs = [
        (
            outer_loops[int(outer_index)],
            inner_loops[int(inner_index)],
        )
        for outer_index, inner_index in zip(
            outer_indices,
            inner_indices,
        )
    ]
    pairs.sort(
        key=lambda pair: tuple(
            np.asarray(pair[0], dtype=float).mean(axis=0)
        )
    )
    return pairs


def _dominant_axis(shape, api):
    unique_edges = {}
    explorer = api["TopExp_Explorer"](shape, api["TopAbs_EDGE"])
    while explorer.More():
        edge = api["TopoDS"].Edge_s(explorer.Current())
        unique_edges.setdefault(hash(edge), edge)
        explorer.Next()

    candidates = []
    for edge in unique_edges.values():
        start, finish, _adaptor, length, chord = _edge_info(
            edge,
            api,
        )
        if (
            chord > 1.0
            and length > 1.0
            and abs(length - chord) / length < 2e-4
        ):
            candidates.append(
                (length, _canonical_axis(finish - start))
            )

    if not candidates:
        raise IgsConversionError(
            "Impossibile rilevare l'asse longitudinale del tubo."
        )

    maximum_length = max(item[0] for item in candidates)
    candidates = [
        item
        for item in candidates
        if item[0] >= maximum_length * 0.45
    ]

    clusters = []
    for length, direction in candidates:
        for cluster in clusters:
            if abs(float(np.dot(cluster["direction"], direction))) > 0.99985:
                if np.dot(cluster["direction"], direction) < 0.0:
                    direction = -direction
                cluster["vector"] += length * direction
                cluster["weight"] += length
                cluster["direction"] = _unit(cluster["vector"])
                break
        else:
            clusters.append(
                {
                    "direction": direction.copy(),
                    "vector": length * direction.copy(),
                    "weight": length,
                }
            )

    clusters.sort(
        key=lambda item: item["weight"],
        reverse=True,
    )
    return _canonical_axis(clusters[0]["direction"])


def _section_loops(
    solid,
    axis,
    axial_coordinate,
    basis_x,
    basis_y,
    api,
):
    origin = axis * float(axial_coordinate)
    plane = api["gp_Pln"](
        api["gp_Pnt"](*map(float, origin)),
        api["gp_Dir"](*map(float, axis)),
    )
    section = api["BRepAlgoAPI_Section"](
        solid,
        plane,
        False,
    )
    section.Build()

    edges = []
    explorer = api["TopExp_Explorer"](
        section.Shape(),
        api["TopAbs_EDGE"],
    )
    while explorer.More():
        edges.append(api["TopoDS"].Edge_s(explorer.Current()))
        explorer.Next()

    # A valid hollow round tube can intersect the section plane as exactly
    # two closed edges: one outer circle and one inner circle. Requiring four
    # edges incorrectly rejects the very common IGES representation where
    # each full circumference is stored as a single closed curve.
    if len(edges) < 2:
        return None

    try:
        loops = _chain_edges(edges, api)
    except IgsConversionError:
        return None

    def project(point):
        relative = point - origin
        return np.array(
            [
                np.dot(relative, basis_x),
                np.dot(relative, basis_y),
            ],
            dtype=float,
        )

    loops = [
        loop
        for loop in loops
        if _polygon_area(loop, project) > 1.0
    ]
    if len(loops) != 2:
        return None

    loops.sort(
        key=lambda loop: _polygon_area(loop, project),
        reverse=True,
    )
    return origin, loops, edges


def _fit_circle(points_2d):
    points = np.asarray(points_2d, dtype=float)
    x = points[:, 0]
    y = points[:, 1]
    matrix = np.column_stack(
        [2.0 * x, 2.0 * y, np.ones_like(x)]
    )
    rhs = x * x + y * y
    center_x, center_y, constant = np.linalg.lstsq(
        matrix,
        rhs,
        rcond=None,
    )[0]
    radius = math.sqrt(
        max(
            0.0,
            constant
            + center_x * center_x
            + center_y * center_y,
        )
    )
    distances = np.sqrt(
        (x - center_x) ** 2
        + (y - center_y) ** 2
    )
    residual = float(
        np.sqrt(np.mean((distances - radius) ** 2))
    )
    return (
        np.array([center_x, center_y], dtype=float),
        float(radius),
        residual,
    )


def _rounded_rectangle_boundary_error(
    points,
    width,
    height,
    radius,
):
    """Exact transverse distance to a centered rounded-rectangle boundary.

    Using nearest sampled section points is unsafe for IGES: a rounded corner
    may be represented by only a few section samples, so its longitudinal
    surface can look many millimetres away and be mistaken for a machining
    face. The signed-distance expression below evaluates the actual profile
    analytically and is independent of the source tube's world orientation.
    """
    points = np.asarray(points, dtype=float)
    half_width = float(width) / 2.0
    half_height = float(height) / 2.0
    radius = max(
        0.0,
        min(
            float(radius),
            half_width,
            half_height,
        ),
    )
    core = np.array(
        [
            half_width - radius,
            half_height - radius,
        ],
        dtype=float,
    )
    delta = np.abs(points) - core
    outside = np.maximum(delta, 0.0)
    signed_distance = (
        np.linalg.norm(outside, axis=-1)
        + np.minimum(
            np.maximum(delta[..., 0], delta[..., 1]),
            0.0,
        )
        - radius
    )
    return np.abs(signed_distance)


def _usable_boundary_loops(loops):
    """Drop zero-length seam/degenerated loops emitted by some IGES exports."""
    result = []
    for loop in loops or []:
        points = np.asarray(loop, dtype=float)
        if len(points) < 4:
            continue
        perimeter = float(
            np.sum(
                np.linalg.norm(
                    np.diff(points, axis=0),
                    axis=1,
                )
            )
        )
        if perimeter <= 1e-3:
            continue
        result.append(loop)
    return result


def _extract_display_seams(
    edge_faces,
    edge_objects,
    faces,
    to_local,
    overall_length,
    api,
):
    """Recover the stock-surface seam lines TubesT stores as channel-0 shapes.

    TubesT uses these non-machining curves to draw the white longitudinal tube
    edges in TubePro/TubesT. IGES exports often split the outside skin into more
    faces than the inside skin, especially where a conical end trim crosses a
    rounded corner. Therefore the inside seams are used as stable anchors and
    each one is matched to the nearest outside seam in transverse XY.
    """

    def collect(kind):
        seams = []
        minimum_span = max(1.0, float(overall_length) * 0.25)

        for edge_key, adjacent_faces in edge_faces.items():
            if len(adjacent_faces) < 2:
                continue

            adjacent_kinds = [
                faces[index][1]
                for index in adjacent_faces
            ]
            if not adjacent_kinds or any(
                value != kind
                for value in adjacent_kinds
            ):
                continue

            edge = edge_objects.get(edge_key)
            if edge is None:
                continue

            points = np.asarray(
                [
                    to_local(point)
                    for point in _edge_points(
                        edge,
                        api,
                        max_points=128,
                    )
                ],
                dtype=float,
            )
            if len(points) < 2:
                continue

            axial_span = float(np.ptp(points[:, 2]))
            if axial_span < minimum_span:
                continue

            if points[0, 2] > points[-1, 2]:
                points = points[::-1].copy()

            seams.append(points)

        return seams

    outer_seams = collect("outer")
    inner_seams = collect("inner")

    if not outer_seams or not inner_seams:
        return []

    outer_centers = np.asarray(
        [
            seam[:, :2].mean(axis=0)
            for seam in outer_seams
        ]
    )
    inner_centers = np.asarray(
        [
            seam[:, :2].mean(axis=0)
            for seam in inner_seams
        ]
    )

    costs = np.linalg.norm(
        inner_centers[:, None, :]
        - outer_centers[None, :, :],
        axis=2,
    )

    inner_indices, outer_indices = linear_sum_assignment(costs)
    display = []

    for inner_index, outer_index in zip(
        inner_indices,
        outer_indices,
    ):
        transverse_distance = float(
            costs[
                int(inner_index),
                int(outer_index),
            ]
        )
        # The paired inner/outer seams should be approximately one wall
        # thickness apart. Keep a generous cap for rounded-corner geometry,
        # but never pair seams from opposite sides of the tube.
        if transverse_distance > max(20.0, min(
            float(overall_length) * 0.02,
            50.0,
        )):
            continue

        display.append(
            {
                "outer": outer_seams[int(outer_index)],
                "inner": inner_seams[int(inner_index)],
                "transverse_distance": transverse_distance,
            }
        )

    display.sort(
        key=lambda item: math.atan2(
            float(item["inner"][:, 1].mean()),
            float(item["inner"][:, 0].mean()),
        )
    )
    return display


def _face_mesh_points(face, api):
    location = api["TopLoc_Location"]()
    triangulation = api["BRep_Tool"].Triangulation_s(
        face,
        location,
    )
    if triangulation is None or triangulation.NbNodes() <= 0:
        return []

    transform = location.Transformation()
    result = []
    for index in range(1, triangulation.NbNodes() + 1):
        point = triangulation.Node(index).Transformed(transform)
        result.append(
            np.array(
                [point.X(), point.Y(), point.Z()],
                dtype=float,
            )
        )
    return result


def _analyse_iges(path):
    api = _ocp()
    reader = api["IGESControl_Reader"]()
    status = reader.ReadFile(str(path))
    if status != api["IFSelect_RetDone"]:
        raise IgsConversionError(
            "OpenCascade non riesce a leggere questo file IGS/IGES."
        )

    reader.TransferRoots()

    sewing = api["BRepBuilderAPI_Sewing"](SEW_TOLERANCE_MM)
    sewing.Add(reader.OneShape())
    sewing.Perform()

    if sewing.NbFreeEdges() != 0:
        raise IgsConversionError(
            "Il modello IGS non forma un tubo chiuso: "
            f"{sewing.NbFreeEdges()} bordi liberi rilevati."
        )

    sewn_shape = sewing.SewedShape()

    shells = []
    explorer = api["TopExp_Explorer"](
        sewn_shape,
        api["TopAbs_SHELL"],
    )
    while explorer.More():
        shells.append(
            api["TopoDS"].Shell_s(explorer.Current())
        )
        explorer.Next()

    if len(shells) != 1:
        raise IgsConversionError(
            "Il file IGS deve contenere un solo tubo collegato. "
            f"Trovati {len(shells)} gusci."
        )

    solid_builder = api["BRepBuilderAPI_MakeSolid"](shells[0])
    if not solid_builder.IsDone():
        raise IgsConversionError(
            "Impossibile costruire il solido chiuso dal file IGS."
        )
    solid = solid_builder.Solid()

    axis = _dominant_axis(sewn_shape, api)

    vertices = []
    explorer = api["TopExp_Explorer"](
        sewn_shape,
        api["TopAbs_VERTEX"],
    )
    while explorer.More():
        point = api["BRep_Tool"].Pnt_s(
            api["TopoDS"].Vertex_s(explorer.Current())
        )
        vertices.append(
            np.array(
                [point.X(), point.Y(), point.Z()],
                dtype=float,
            )
        )
        explorer.Next()

    axial_values = [
        float(np.dot(point, axis))
        for point in vertices
    ]
    axial_min = min(axial_values)
    axial_max = max(axial_values)
    overall_length = axial_max - axial_min

    if overall_length <= 1.0:
        raise IgsConversionError(
            "Lunghezza tubo non valida nel modello IGS."
        )

    reference = (
        np.array([1.0, 0.0, 0.0])
        if abs(axis[0]) < 0.8
        else np.array([0.0, 1.0, 0.0])
    )
    provisional_x = _unit(
        reference - axis * np.dot(reference, axis)
    )
    provisional_y = _unit(
        np.cross(axis, provisional_x)
    )

    section_data = None
    # Probe the middle first, then fan outward. A hole, notch or unusual cut
    # can intersect any one candidate plane, so use a denser set than the old
    # seven fixed positions. This remains cheap compared with IGES sewing and
    # makes profile detection much more tolerant of real production parts.
    section_fractions = (
        0.50,
        0.45, 0.55,
        0.40, 0.60,
        0.35, 0.65,
        0.30, 0.70,
        0.25, 0.75,
        0.20, 0.80,
        0.15, 0.85,
        0.10, 0.90,
    )
    for fraction in section_fractions:
        section_data = _section_loops(
            solid,
            axis,
            axial_min + overall_length * fraction,
            provisional_x,
            provisional_y,
            api,
        )
        if section_data is not None:
            break

    if section_data is None:
        raise IgsConversionError(
            "Non trovo una sezione trasversale integra del tubo."
        )

    section_origin, section_loops, section_edges = section_data
    outer_loop, inner_loop = section_loops

    def project_provisional(point):
        relative = point - section_origin
        return np.array(
            [
                np.dot(relative, provisional_x),
                np.dot(relative, provisional_y),
            ],
            dtype=float,
        )

    outer_2d = np.asarray(
        [project_provisional(point) for point in outer_loop]
    )
    inner_2d = np.asarray(
        [project_provisional(point) for point in inner_loop]
    )

    outer_circle_center, outer_radius, outer_circle_error = (
        _fit_circle(
            outer_2d[
                :: max(1, len(outer_2d) // 1000)
            ]
        )
    )
    inner_circle_center, inner_radius, inner_circle_error = (
        _fit_circle(
            inner_2d[
                :: max(1, len(inner_2d) // 1000)
            ]
        )
    )

    if (
        outer_circle_error
        < max(0.03, outer_radius * 0.0015)
        and inner_circle_error
        < max(0.03, inner_radius * 0.0015)
    ):
        profile_kind = "Circle"
        local_x_axis = provisional_x
        local_y_axis = provisional_y
        center_x = float(
            np.dot(section_origin, provisional_x)
            + outer_circle_center[0]
        )
        center_y = float(
            np.dot(section_origin, provisional_y)
            + outer_circle_center[1]
        )
        outside_width = 2.0 * outer_radius
        outside_height = outside_width
        inside_width = 2.0 * inner_radius
        inside_height = inside_width
        corner_radius = outer_radius
        inner_corner_radius = inner_radius
        thickness = outer_radius - inner_radius
    else:
        straight_edges = []
        for edge in section_edges:
            start, finish, _adaptor, length, chord = _edge_info(
                edge,
                api,
            )
            if (
                chord > 1.0
                and length > 1.0
                and abs(length - chord) / length < 2e-4
            ):
                straight_edges.append(
                    (length, start, finish)
                )

        if not straight_edges:
            raise IgsConversionError(
                "Sezione non riconosciuta come tubo tondo, quadrato "
                "o rettangolare."
            )

        _length, start, finish = max(
            straight_edges,
            key=lambda item: item[0],
        )
        local_x_axis = _unit(finish - start)
        local_x_axis = _unit(
            local_x_axis
            - axis * np.dot(local_x_axis, axis)
        )
        local_y_axis = _unit(
            np.cross(axis, local_x_axis)
        )

        projected_outer = np.asarray(
            [
                [
                    np.dot(point, local_x_axis),
                    np.dot(point, local_y_axis),
                ]
                for point in outer_loop
            ]
        )
        center_x = float(
            (
                projected_outer[:, 0].min()
                + projected_outer[:, 0].max()
            )
            / 2.0
        )
        center_y = float(
            (
                projected_outer[:, 1].min()
                + projected_outer[:, 1].max()
            )
            / 2.0
        )

        def local_2d(point):
            return np.array(
                [
                    np.dot(point, local_x_axis) - center_x,
                    np.dot(point, local_y_axis) - center_y,
                ]
            )

        outer_2d = np.asarray(
            [local_2d(point) for point in outer_loop]
        )
        inner_2d = np.asarray(
            [local_2d(point) for point in inner_loop]
        )

        outside_width = float(np.ptp(outer_2d[:, 0]))
        outside_height = float(np.ptp(outer_2d[:, 1]))
        inside_width = float(np.ptp(inner_2d[:, 0]))
        inside_height = float(np.ptp(inner_2d[:, 1]))
        thickness = (
            (outside_width - inside_width)
            + (outside_height - inside_height)
        ) / 4.0

        outer_radius_estimates = []
        inner_radius_estimates = []
        for edge in section_edges:
            start, finish, _adaptor, length, chord = _edge_info(
                edge,
                api,
            )
            if (
                chord <= 1.0
                or abs(length - chord) / max(length, 1e-9) >= 2e-4
            ):
                continue

            start_2d = local_2d(start)
            finish_2d = local_2d(finish)
            midpoint = (start_2d + finish_2d) / 2.0

            horizontal = (
                abs(finish_2d[0] - start_2d[0])
                > abs(finish_2d[1] - start_2d[1])
            )

            if horizontal:
                if (
                    abs(
                        abs(midpoint[1])
                        - outside_height / 2.0
                    )
                    < 0.05
                ):
                    outer_radius_estimates.append(
                        (outside_width - length) / 2.0
                    )
                if (
                    abs(
                        abs(midpoint[1])
                        - inside_height / 2.0
                    )
                    < 0.05
                ):
                    inner_radius_estimates.append(
                        (inside_width - length) / 2.0
                    )
            else:
                if (
                    abs(
                        abs(midpoint[0])
                        - outside_width / 2.0
                    )
                    < 0.05
                ):
                    outer_radius_estimates.append(
                        (outside_height - length) / 2.0
                    )
                if (
                    abs(
                        abs(midpoint[0])
                        - inside_width / 2.0
                    )
                    < 0.05
                ):
                    inner_radius_estimates.append(
                        (inside_height - length) / 2.0
                    )

        usable_outer_radii = [
            value
            for value in outer_radius_estimates
            if value >= 0.0
        ]
        usable_inner_radii = [
            value
            for value in inner_radius_estimates
            if value >= 0.0
        ]
        corner_radius = (
            float(np.median(usable_outer_radii))
            if usable_outer_radii
            else max(0.0, thickness)
        )
        inner_corner_radius = (
            float(np.median(usable_inner_radii))
            if usable_inner_radii
            else max(0.0, corner_radius - thickness)
        )

        profile_kind = (
            "Square"
            if abs(outside_width - outside_height)
            <= max(
                0.05,
                0.002 * max(outside_width, outside_height),
            )
            else "Rect"
        )

    if (
        not math.isfinite(thickness)
        or thickness <= 0.0
        or thickness >= min(outside_width, outside_height) / 2.0
    ):
        raise IgsConversionError(
            "Spessore tubo non valido o non rilevabile."
        )

    def to_local(point):
        point = np.asarray(point, dtype=float)
        return np.array(
            [
                np.dot(point, local_x_axis) - center_x,
                np.dot(point, local_y_axis) - center_y,
                np.dot(point, axis) - axial_min,
            ],
            dtype=float,
        )

    surface_tolerance = max(
        0.05,
        min(0.30, thickness * 0.12),
    )

    api["BRepMesh_IncrementalMesh"](
        sewn_shape,
        MESH_DEFLECTION_MM,
    )

    faces = []
    explorer = api["TopExp_Explorer"](
        sewn_shape,
        api["TopAbs_FACE"],
    )
    while explorer.More():
        face = api["TopoDS"].Face_s(explorer.Current())
        points = _face_mesh_points(face, api)
        if not points:
            raise IgsConversionError(
                "Impossibile triangolare una superficie del tubo."
            )

        transverse = np.asarray(
            [to_local(point)[:2] for point in points]
        )

        if profile_kind == "Circle":
            outside_distances = np.abs(
                np.linalg.norm(transverse, axis=1)
                - outside_width / 2.0
            )
            inside_distances = np.abs(
                np.linalg.norm(transverse, axis=1)
                - inside_width / 2.0
            )
        else:
            outside_distances = _rounded_rectangle_boundary_error(
                transverse,
                outside_width,
                outside_height,
                corner_radius,
            )
            inside_distances = _rounded_rectangle_boundary_error(
                transverse,
                inside_width,
                inside_height,
                inner_corner_radius,
            )

        outside_error = float(
            np.percentile(outside_distances, 95)
        )
        inside_error = float(
            np.percentile(inside_distances, 95)
        )

        if outside_error <= surface_tolerance:
            face_kind = "outer"
        elif inside_error <= surface_tolerance:
            face_kind = "inner"
        else:
            face_kind = "cut"

        faces.append((face, face_kind))
        explorer.Next()

    edge_faces = defaultdict(list)
    edge_objects = {}
    face_edges = []

    for face_index, (face, _kind) in enumerate(faces):
        edges = []
        edge_explorer = api["TopExp_Explorer"](
            face,
            api["TopAbs_EDGE"],
        )
        while edge_explorer.More():
            edge = api["TopoDS"].Edge_s(
                edge_explorer.Current()
            )
            edge_key = hash(edge)
            edge_faces[edge_key].append(face_index)
            edge_objects[edge_key] = edge
            edges.append(edge)
            edge_explorer.Next()
        face_edges.append(edges)

    display_operations = _extract_display_seams(
        edge_faces,
        edge_objects,
        faces,
        to_local,
        overall_length,
        api,
    )

    cut_face_indices = {
        index
        for index, (_face, kind) in enumerate(faces)
        if kind == "cut"
    }
    cut_graph = {
        index: set()
        for index in cut_face_indices
    }

    for adjacent_faces in edge_faces.values():
        cut_neighbors = [
            index
            for index in adjacent_faces
            if index in cut_face_indices
        ]
        for index in cut_neighbors:
            cut_graph[index].update(
                other
                for other in cut_neighbors
                if other != index
            )

    cut_components = []
    visited = set()

    for start_index in cut_face_indices:
        if start_index in visited:
            continue
        stack = [start_index]
        visited.add(start_index)
        component = []

        while stack:
            index = stack.pop()
            component.append(index)
            for neighbor in cut_graph[index]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)

        cut_components.append(component)

    operations = []

    for component in cut_components:
        outer_boundary = {}
        inner_boundary = {}

        for face_index in component:
            for edge in face_edges[face_index]:
                neighbors = edge_faces[hash(edge)]
                neighbor_kinds = [
                    faces[index][1]
                    for index in neighbors
                    if index != face_index
                ]
                if "outer" in neighbor_kinds:
                    outer_boundary[hash(edge)] = edge
                if "inner" in neighbor_kinds:
                    inner_boundary[hash(edge)] = edge

        if not outer_boundary or not inner_boundary:
            continue

        outer_feature_loops = _usable_boundary_loops(
            _chain_edges(
                list(outer_boundary.values()),
                api,
                ignore_degenerate=True,
            )
        )
        inner_feature_loops = _usable_boundary_loops(
            _chain_edges(
                list(inner_boundary.values()),
                api,
                ignore_degenerate=True,
            )
        )

        boundary_pairs = _pair_feature_boundary_loops(
            outer_feature_loops,
            inner_feature_loops,
        )

        for outer_loop, inner_loop in boundary_pairs:
            outer_points = np.asarray(
                [
                    to_local(point)
                    for point in outer_loop
                ]
            )
            inner_points = np.asarray(
                [
                    to_local(point)
                    for point in inner_loop
                ]
            )

            operations.append(
                {
                    "outer": outer_points,
                    "inner": inner_points,
                    "z_min": float(outer_points[:, 2].min()),
                    "z_max": float(outer_points[:, 2].max()),
                    "z_mean": float(outer_points[:, 2].mean()),
                }
            )

    near_operations = [
        operation
        for operation in operations
        if operation["z_min"] <= END_MATCH_TOLERANCE_MM
    ]
    far_operations = [
        operation
        for operation in operations
        if operation["z_max"]
        >= overall_length - END_MATCH_TOLERANCE_MM
    ]

    if len(near_operations) != 1 or len(far_operations) != 1:
        raise IgsConversionError(
            "Non riesco a identificare in modo univoco i due tagli "
            "terminali del tubo."
        )

    near_operation = near_operations[0]
    far_operation = far_operations[0]

    if near_operation is far_operation:
        raise IgsConversionError(
            "I due estremi del tubo risultano fusi nella stessa lavorazione."
        )

    internal_operations = sorted(
        [
            operation
            for operation in operations
            if operation is not near_operation
            and operation is not far_operation
        ],
        key=lambda operation: operation["z_mean"],
    )

    ordered_operations = (
        [near_operation]
        + internal_operations
        + [far_operation]
    )

    return {
        "profile_kind": profile_kind,
        "outside_width": float(outside_width),
        "outside_height": float(outside_height),
        "thickness": float(thickness),
        "corner_radius": float(corner_radius),
        "overall_length": float(overall_length),
        "axis": [float(value) for value in axis],
        "local_x_axis": [
            float(value)
            for value in local_x_axis
        ],
        "local_y_axis": [
            float(value)
            for value in local_y_axis
        ],
        "operations": ordered_operations,
        "display_operations": display_operations,
    }


def _simplify_closed_polyline(points, tolerance=0.025):
    points = np.asarray(points, dtype=float)
    if len(points) < 3:
        raise IgsConversionError("Contorno CAD troppo corto.")

    filtered = [points[0]]
    for point in points[1:]:
        if np.linalg.norm(point - filtered[-1]) > 1e-6:
            filtered.append(point)

    points = np.asarray(filtered)
    if np.linalg.norm(points[-1] - points[0]) > 1e-5:
        points = np.vstack([points, points[0]])

    open_points = points[:-1]
    if len(open_points) <= 500:
        return np.vstack([open_points, open_points[0]])

    def rdp(values):
        if len(values) <= 2:
            return values

        start = values[0]
        finish = values[-1]
        direction = finish - start
        direction_length = np.linalg.norm(direction)

        if direction_length <= 1e-12:
            distances = np.linalg.norm(
                values - start,
                axis=1,
            )
        else:
            distances = (
                np.linalg.norm(
                    np.cross(values - start, direction),
                    axis=1,
                )
                / direction_length
            )

        split_index = int(np.argmax(distances))
        maximum_distance = float(distances[split_index])

        if maximum_distance <= float(tolerance):
            return np.vstack([start, finish])

        left = rdp(values[: split_index + 1])
        right = rdp(values[split_index:])
        return np.vstack([left[:-1], right])

    center = open_points.mean(axis=0)
    start_index = int(
        np.argmax(
            np.linalg.norm(
                open_points - center,
                axis=1,
            )
        )
    )
    open_points = np.vstack(
        [
            open_points[start_index:],
            open_points[:start_index],
        ]
    )
    midpoint = len(open_points) // 2
    first = rdp(open_points[: midpoint + 1])
    second = rdp(
        np.vstack(
            [
                open_points[midpoint:],
                open_points[0],
            ]
        )
    )
    return np.vstack(
        [
            first[:-1],
            second,
        ]
    )


def _plane_fit(points):
    points = np.asarray(points, dtype=float)
    center = points.mean(axis=0)
    _u, _s, vh = np.linalg.svd(
        points - center,
        full_matrices=False,
    )
    normal = vh[-1]
    residual = float(
        np.max(
            np.abs(
                (points - center) @ normal
            )
        )
    )
    return normal, residual


def _shape_record_from_prototype(
    prototype,
    *,
    handle,
    curve_flags,
    normal,
    channel=1,
):
    record = deepcopy(prototype)

    object_block = next(
        block
        for block in record.blocks
        if block.name == "Object"
    )
    object_block.payload = struct.pack(
        "<I",
        int(handle),
    )

    shape_block = next(
        block
        for block in record.blocks
        if block.name == "Shape"
    )
    shape_payload = bytearray(shape_block.payload)
    if len(shape_payload) < 12:
        raise FormatError("Unexpected ZZX shape template.")
    struct.pack_into(
        "<III",
        shape_payload,
        0,
        int(channel),
        0,
        0,
    )
    shape_block.payload = bytes(shape_payload)

    curve_block = next(
        block
        for block in record.blocks
        if block.name == "Curve"
    )
    curve_payload = bytearray(curve_block.payload)
    if len(curve_payload) < 44:
        raise FormatError("Unexpected ZZX curve template.")

    struct.pack_into(
        "<IdI",
        curve_payload,
        0,
        0,
        0.0,
        int(curve_flags),
    )
    curve_payload[16:44] = vector(
        tuple(map(float, normal))
    )
    curve_block.payload = bytes(curve_payload)

    return record


def _display_curve_record(points):
    """Encode a stock seam like TubesT: degree-1 Spline3D when straight."""

    points = np.asarray(points, dtype=float)
    if len(points) < 2:
        raise IgsConversionError(
            "Linea di visualizzazione tubo troppo corta."
        )

    start = points[0]
    finish = points[-1]
    direction = finish - start
    length = float(np.linalg.norm(direction))
    if length <= 1e-7:
        raise IgsConversionError(
            "Linea di visualizzazione tubo nulla."
        )

    if len(points) == 2:
        maximum_deviation = 0.0
    else:
        cross = np.cross(
            points - start,
            direction,
        )
        maximum_deviation = float(
            np.max(
                np.linalg.norm(cross, axis=1)
                / length
            )
        )

    if maximum_deviation <= 0.02:
        native_span = max(
            1e-9,
            length / 1000.0,
        )
        spline = Spline(
            1,
            [
                0.0,
                0.0,
                native_span,
                native_span,
            ],
            [
                tuple(map(float, start)),
                tuple(map(float, finish)),
            ],
            [1.0, 1.0],
        )
        return Record(
            "TGeSpline3D",
            [
                Block("Spline3D"),
                Block("Curve3D", payload=bytes(4)),
                Block("Spline3D", payload=spline.payload()),
            ],
        )

    curves = []
    for first, second in zip(points, points[1:]):
        delta = second - first
        if np.linalg.norm(delta) <= 1e-7:
            continue
        curves.append(
            Line(
                tuple(map(float, first)),
                tuple(map(float, delta)),
            )
        )
    if not curves:
        raise IgsConversionError(
            "Linea di visualizzazione tubo vuota."
        )
    return composite(curves, dimension=3)


def _polyline_record(points):
    points = _simplify_closed_polyline(points)
    curves = []

    for start, finish in zip(points, points[1:]):
        direction = finish - start
        if np.linalg.norm(direction) <= 1e-7:
            continue
        curves.append(
            Line(
                tuple(map(float, start)),
                tuple(map(float, direction)),
            )
        )

    if not curves:
        raise IgsConversionError(
            "Contorno di taglio IGS vuoto dopo la semplificazione."
        )

    return composite(curves, dimension=3)


def _build_cross_section(model):
    kind = model["profile_kind"]
    width = model["outside_width"]
    height = model["outside_height"]
    radius = model["corner_radius"]

    if kind == "Circle":
        curve_record = Record(
            "TRvCircleSection",
            [
                Block("Circle", 2, bytes(4)),
                Block(
                    "Circle",
                    1,
                    struct.pack(
                        "<d",
                        width / 2.0,
                    ),
                ),
            ],
        )
        geometry_record = composite(
            circle_quarters(width / 2.0),
            dimension=2,
        )
        return curve_record, geometry_record

    if kind == "Square":
        curve_record = Record(
            "TRvSquareSection",
            [
                Block("Square", 2, bytes(4)),
                Block(
                    "Square",
                    2,
                    struct.pack(
                        "<dd",
                        radius,
                        width,
                    ),
                ),
            ],
        )
        geometry_record = composite(
            rounded_rectangle(
                width,
                height,
                radius,
            ),
            dimension=2,
        )
        return curve_record, geometry_record

    if kind == "Rect":
        curve_record = Record(
            "TRvRectSection",
            [
                Block("Rect", 2, bytes(4)),
                Block(
                    "Rect",
                    2,
                    struct.pack(
                        "<ddd",
                        width,
                        height,
                        radius,
                    ),
                ),
            ],
        )
        geometry_record = composite(
            rounded_rectangle(
                width,
                height,
                radius,
            ),
            dimension=2,
        )
        return curve_record, geometry_record

    raise IgsConversionError(
        f"Profilo IGS non supportato: {kind}"
    )


def _write_zzx(model, source_path, output_path):
    if not TEMPLATE_PATH.exists():
        raise IgsConversionError(
            f"Template ZZX IGS mancante: {TEMPLATE_PATH}"
        )

    archive = Archive.read(TEMPLATE_PATH)

    curves_stream = archive.stream("Curves")
    segments_stream = archive.stream("Segments")
    shapes_stream = archive.stream("Shapes")
    lite_stream = archive.stream("LiteGeos")
    portions_stream = archive.stream("Portions")

    if not shapes_stream.records:
        raise IgsConversionError(
            "Template ZZX senza Shape di riferimento."
        )

    shape_prototype = shapes_stream.records[0]
    curve_record, section_geometry = _build_cross_section(model)

    curves_stream.records = [curve_record]
    shapes_stream.records = []
    lite_stream.records = [section_geometry]

    shapes_root = archive.xml("Shapes/content.xml")
    shapes_root.clear()

    segments_root = archive.xml("Segments/content.xml")
    segment = segments_root.find("TubeSegment")
    if segment is None:
        raise IgsConversionError(
            "Template ZZX senza TubeSegment."
        )

    for child in list(segment):
        segment.remove(child)

    cross_section = ET.SubElement(
        segment,
        "CrossSection",
        SectionClass=model["profile_kind"],
        ThickNess=f'{model["thickness"]:.8f}',
        DataAddr="0",
    )
    cross_section_geometry = ET.SubElement(
        cross_section,
        "Geometry",
        Class="CompositeCurve2D",
        GeoAddr="0",
    )
    segment_shape_refs = ET.SubElement(
        segment,
        "Shapes",
    )

    handles = []
    machining_handles = []
    next_handle = 1100
    geometry_pairs = []

    operations = list(model["operations"])
    display_operations = list(
        model.get("display_operations") or []
    )

    def append_shape(
        outer_geometry,
        inner_geometry,
        *,
        channel,
        curve_flags,
        normal,
        geometry_class,
        is_machining,
    ):
        nonlocal next_handle

        shape_record = _shape_record_from_prototype(
            shape_prototype,
            handle=next_handle,
            curve_flags=curve_flags,
            normal=normal,
            channel=channel,
        )
        shapes_stream.records.append(shape_record)
        lite_stream.records.extend(
            [
                outer_geometry,
                inner_geometry,
            ]
        )
        geometry_pairs.append(
            (
                shape_record,
                outer_geometry,
                inner_geometry,
            )
        )

        shape_element = ET.SubElement(
            shapes_root,
            "GeoCurve",
            Class="GeoCurve",
            Handle=str(next_handle),
            DataAddr="0",
        )
        ET.SubElement(
            shape_element,
            "Geometry",
            Class=geometry_class,
            GeoAddr="0",
        )
        ET.SubElement(
            shape_element,
            "InnerGeometry",
            Class=geometry_class,
            GeoAddr="0",
        )
        ET.SubElement(
            segment_shape_refs,
            "GeoCurve",
            Handle=str(next_handle),
        )

        handles.append(next_handle)
        if is_machining:
            machining_handles.append(next_handle)
        next_handle += 1

    def append_machining_operation(
        operation,
        *,
        is_end_cut,
    ):
        outer_points = _simplify_closed_polyline(
            operation["outer"]
        )
        inner_points = _simplify_closed_polyline(
            operation["inner"]
        )

        plane_normal, plane_residual = _plane_fit(
            outer_points
        )

        if is_end_cut:
            curve_flags = 34
            normal = (0.0, 0.0, 0.0)
        elif plane_residual <= FEATURE_PLANE_TOLERANCE_MM:
            curve_flags = 64
            normal = tuple(
                map(float, plane_normal)
            )
        else:
            curve_flags = 0
            normal = (0.0, 0.0, 0.0)

        append_shape(
            _polyline_record(outer_points),
            _polyline_record(inner_points),
            channel=1,
            curve_flags=curve_flags,
            normal=normal,
            geometry_class="CompositeCurve3D",
            is_machining=True,
        )

    if not operations:
        raise IgsConversionError(
            "Nessuna lavorazione rilevata nel file IGS."
        )

    # TubesT orders imported stock as: first cutoff, channel-0 stock seams,
    # internal machining, final cutoff. The channel-0 seams are what TubePro
    # renders as the white longitudinal tube wireframe.
    append_machining_operation(
        operations[0],
        is_end_cut=True,
    )

    for display_operation in display_operations:
        append_shape(
            _display_curve_record(
                display_operation["outer"]
            ),
            _display_curve_record(
                display_operation["inner"]
            ),
            channel=0,
            curve_flags=0,
            normal=(0.0, 0.0, 0.0),
            geometry_class="Spline3D",
            is_machining=False,
        )

    for operation in operations[1:-1]:
        append_machining_operation(
            operation,
            is_end_cut=False,
        )

    if len(operations) > 1:
        append_machining_operation(
            operations[-1],
            is_end_cut=True,
        )

    segment.set(
        "Name",
        Path(source_path).stem[:80],
    )
    segment.set(
        "CutOffA",
        str(machining_handles[0]),
    )
    segment.set(
        "CutOffB",
        str(machining_handles[-1]),
    )

    archive.entries["Curves/data.bin"] = curves_stream.encode()
    archive.entries["Shapes/data.bin"] = shapes_stream.encode()
    archive.entries["LiteGeos/data.bin"] = lite_stream.encode()

    cross_section.set(
        "DataAddr",
        str(curve_record.address),
    )
    cross_section_geometry.set(
        "GeoAddr",
        str(section_geometry.address),
    )

    shape_elements = [
        element
        for element in shapes_root
        if element.tag != "MD5"
    ]
    for (
        shape_element,
        (shape_record, outer_geometry, inner_geometry),
    ) in zip(shape_elements, geometry_pairs):
        shape_element.set(
            "DataAddr",
            str(shape_record.address),
        )
        shape_element.find("Geometry").set(
            "GeoAddr",
            str(outer_geometry.address),
        )
        shape_element.find("InnerGeometry").set(
            "GeoAddr",
            str(inner_geometry.address),
        )

    archive.entries["Shapes/content.xml"] = xml_bytes(shapes_root)
    archive.entries["Segments/content.xml"] = xml_bytes(segments_root)

    portion_record = portions_stream.records[0]
    portion_block = next(
        block
        for block in portion_record.blocks
        if block.name == "DocPortion"
    )
    raw_portion = bytearray(portion_block.payload)

    half_width = model["outside_width"] / 2.0
    half_height = model["outside_height"] / 2.0
    raw_portion[4:32] = vector(
        (
            -half_width,
            -half_height,
            0.0,
        )
    )
    raw_portion[32:60] = vector(
        (
            half_width,
            half_height,
            model["overall_length"],
        )
    )
    portion_block.payload = bytes(raw_portion)
    archive.entries["Portions/data.bin"] = portions_stream.encode()

    # The template has one pack record and one segment record. Re-encoding is
    # intentional so addresses are guaranteed to be internally consistent.
    archive.entries["Segments/data.bin"] = segments_stream.encode()

    root = archive.xml("content.xml")
    header = root.find("Header")
    if header is not None:
        header.set(
            "HandleSeed",
            str(next_handle + 100),
        )

    metadata = root.find("MetaData")
    if metadata is not None:
        title = metadata.find("Title")
        if title is not None:
            title.text = "ULTRA IGS conversion"

    archive.entries["content.xml"] = xml_bytes(root)

    viewport_root = archive.xml("Viewports/content.xml")
    viewport = viewport_root.find(".//VPort")
    if viewport is not None:
        viewport.set(
            "Handle",
            str(next_handle + 50),
        )
        translation = viewport.find("Translation")
        if translation is not None:
            translation.set(
                "X",
                f'{-model["outside_width"] * 2.0:.6f}',
            )
            translation.set(
                "Y",
                f'{-model["outside_height"] * 2.0:.6f}',
            )
    archive.entries["Viewports/content.xml"] = xml_bytes(
        viewport_root
    )

    archive.refresh_checksums()
    archive.validate()

    output_path = Path(output_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = output_path.with_name(
        f".{output_path.name}.tmp-{os.getpid()}"
    )
    if temporary_path.exists():
        temporary_path.unlink()

    try:
        archive.write(temporary_path)
        os.replace(
            temporary_path,
            output_path,
        )
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def convert_igs_to_zzx(
    igs_path,
    output_path=None,
    *,
    overwrite=False,
):
    # Keep the path spelling returned by the native Windows file dialog.
    # Path.resolve() expands Windows 8.3 aliases (RUNNER~1, etc.) and can make
    # the API report a different-looking path even though it is the same file.
    source_path = Path(
        os.path.abspath(
            os.path.expanduser(
                os.fspath(igs_path)
            )
        )
    )

    if source_path.suffix.lower() not in {".igs", ".iges"}:
        raise IgsConversionError(
            "Seleziona un file .igs oppure .iges."
        )
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    if output_path is None:
        destination = source_path.with_suffix(".zzx")
    else:
        destination = Path(
            os.path.abspath(
                os.path.expanduser(
                    os.fspath(output_path)
                )
            )
        )

    if destination.exists() and not overwrite:
        return {
            "status": "exists",
            "sourcePath": str(source_path),
            "outputPath": str(destination),
            "message": (
                "Esiste già un file ZZX con lo stesso nome."
            ),
        }

    model = _analyse_iges(source_path)
    _write_zzx(
        model,
        source_path,
        destination,
    )

    return {
        "status": "success",
        "sourcePath": str(source_path),
        "outputPath": str(destination),
        "profileKind": model["profile_kind"],
        "outsideWidthMm": model["outside_width"],
        "outsideHeightMm": model["outside_height"],
        "thicknessMm": model["thickness"],
        "cornerRadiusMm": model["corner_radius"],
        "lengthMm": model["overall_length"],
        "detectedAxis": model["axis"],
        "localXAxis": model["local_x_axis"],
        "localYAxis": model["local_y_axis"],
        "operationCount": len(model["operations"]),
        "internalOperationCount": max(
            0,
            len(model["operations"]) - 2,
        ),
        "displayShapeCount": len(
            model.get("display_operations") or []
        ),
    }
