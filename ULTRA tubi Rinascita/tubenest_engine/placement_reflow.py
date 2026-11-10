"""Reposition locked ZZX pieces with *real* clearance, without shared cuts.

Used for explicit Shift+ZZX export and as a fail-safe when final shared-cut
verification rejects a supposedly identical common-line pair. Original
nesting geometry and locked-rod UI state are never modified.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .domain import read_tube_parts
from .fit import (
    PLANAR_FIT_TOLERANCE_MM,
    PartPose,
    fit_adjacent_parts,
    posed_ends,
)


def reflow_export_placements_with_gap(
    segments, *, gap_mm, rod_length,
):
    """Keep piece order and poses, but give every boundary the configured gap.

    Adjacency uses the actual posed start/end planes (or perimeter envelopes),
    NOT z_end + gap. Angled faces can still interlock axially.
    No common-line flags survive and no input dictionaries are mutated.
    """
    gap = float(gap_mm)
    stock_length = float(rod_length)
    if not math.isfinite(gap) or gap <= 0:
        raise ValueError(
            "Per esportare senza co-edge imposta un margine "
            "di taglio maggiore di zero nelle Impostazioni."
        )
    if not math.isfinite(stock_length) or stock_length <= 0:
        raise ValueError("La lunghezza della verga non è valida.")
    if not segments:
        raise ValueError("La verga da esportare è vuota.")

    cache = {}
    sources = []
    for index, raw in enumerate(segments):
        source_path = str(raw.get("filePath") or "").strip()
        placement = raw.get("nestPlacement")
        if not source_path or not isinstance(placement, dict):
            raise ValueError(
                f"Pezzo {index + 1}: mancano file ZZX o posa salvata."
            )
        if source_path not in cache:
            source_parts = read_tube_parts(source_path)
            if len(source_parts) != 1:
                raise ValueError(
                    f"{source_path}: il ricalcolo margini richiede un "
                    "singolo TubeSegment per file sorgente."
                )
            cache[source_path] = source_parts[0]
        part = cache[source_path]
        if (
            raw.get("segmentHandle") is not None
            and int(raw["segmentHandle"]) != int(part.segment_handle)
        ):
            raise ValueError(
                f"Pezzo {index + 1}: TubeSegment sorgente diverso."
            )
        geometry = part.to_dict()
        length = float(part.overall_length)
        if not math.isfinite(length) or length <= 0:
            raise ValueError(f"Pezzo {index + 1}: lunghezza ZZX non valida.")
        pose = PartPose(
            axial_rotation_degrees=float(
                placement.get("axial_rotation_degrees") or 0.0
            ),
            reversed_end_for_end=bool(
                placement.get("reversed_end_for_end")
            ),
        )
        if not math.isfinite(pose.axial_rotation_degrees):
            raise ValueError(f"Pezzo {index + 1}: rotazione non valida.")
        sources.append((geometry, length, pose))

    result = deepcopy(segments)
    previous_geometry = previous_pose = None
    previous_origin = previous_length = None
    for index, (segment, (geometry, length, pose)) in enumerate(
        zip(result, sources)
    ):
        if index == 0:
            origin = 0.0
        else:
            # Approximate CAD contours can deviate slightly from their
            # fitted planes. Compensate both residuals rather than
            # reducing the configured minimum physical clearance.
            previous_far = posed_ends(
                previous_geometry, previous_pose,
            )[1]
            current_near = posed_ends(
                geometry, pose,
            )[0]
            extra_clearance = 0.0
            if previous_far is not None and current_near is not None:
                residuals = (
                    float(previous_far.residual_mm or 0.0),
                    float(current_near.residual_mm or 0.0),
                )
                if all(
                    math.isfinite(value)
                    and 0.0 <= value <= PLANAR_FIT_TOLERANCE_MM
                    for value in residuals
                ):
                    extra_clearance = sum(residuals)
            effective_gap = gap + extra_clearance
            try:
                fitted = fit_adjacent_parts(
                    previous_geometry,
                    previous_pose,
                    previous_origin,
                    geometry,
                    pose,
                    gap_mm=effective_gap,
                    allow_common_line=False,
                )
            except ValueError as exc:
                raise ValueError(
                    f"Impossibile calcolare un margine sicuro tra i pezzi "
                    f"{index} e {index + 1}: {exc}"
                ) from exc
            # Preserve the user's order if the profiles theoretically fit
            # with more than a full-piece overlap (not useful machining).
            origin = max(previous_origin, float(fitted.next_origin))
        if not math.isfinite(origin):
            raise ValueError(f"Pezzo {index + 1}: posizione non valida.")

        placement = segment["nestPlacement"]
        placement["z_start"] = float(origin)
        placement["z_end"] = float(origin + length)
        placement["occupied_length"] = float(length)
        placement["common_line_before"] = False
        placement["common_line_after"] = False
        placement["gap_before_mm"] = (
            0.0 if index == 0 else effective_gap
        )
        placement["overlap_before_mm"] = (
            0.0 if index == 0
            else max(0.0, previous_origin + previous_length - origin)
        )

        previous_geometry = geometry
        previous_pose = pose
        previous_origin = origin
        previous_length = length

    used_end = max(
        float(segment["nestPlacement"]["z_end"]) for segment in result
    )
    if used_end > stock_length + 1e-5:
        raise ValueError(
            "L'esportazione senza co-edge non entra sulla verga: "
            f"{used_end:.2f} mm necessari su {stock_length:.2f} mm. "
            "Rimuovi un pezzo o cambia verga; nessun file è stato esportato."
        )
    return result
