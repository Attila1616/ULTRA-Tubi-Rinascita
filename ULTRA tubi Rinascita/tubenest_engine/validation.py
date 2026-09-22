"""Filename-vs-ZZX validation policy for ULTRA.

The filename remains authoritative for production metadata such as declared wall
thickness. Geometry validation focuses on discrepancies that may indicate a
drawing/export mistake.
"""
from __future__ import annotations

import os
import re

from .domain import describe_tube_parts


LENGTH_TOLERANCE_MM = 1.0
PROFILE_TOLERANCE_MM = 0.1

_ARRAY_PREFIX_RE = re.compile(r"^\s*\d+\s*x\s+", re.IGNORECASE)


def filename_length(filename):
    match = re.search(r"(?i)(?:^|\s)L(\d+(?:\.\d+)?)", str(filename or ""))
    return float(match.group(1)) if match else None


def filename_profile(filename):
    filename = str(filename or "")
    circle = re.search(r"(?i)Ø\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", filename)
    if circle:
        return {
            "kind": "Circle",
            "diameter": float(circle.group(1)),
            # Thickness is intentionally parsed but not validated against ZZX.
            "thickness": float(circle.group(2)),
        }

    box = re.search(
        r"(?i)(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)",
        filename,
    )
    if box:
        width, height, thickness = map(float, box.groups())
        return {
            "kind": "Square" if abs(width - height) <= 1e-9 else "Rect",
            "width": width,
            "height": height,
            # Thickness is intentionally parsed but not validated against ZZX.
            "thickness": thickness,
        }
    return None


def is_known_array_without_terminal_cut(filename, error_message):
    """Allow legacy array files whose final cutoff was intentionally deleted."""
    if "no usable axial bounds" not in str(error_message or "").lower():
        return False
    return bool(_ARRAY_PREFIX_RE.match(os.path.basename(str(filename or ""))))


def _close(a, b, tolerance=PROFILE_TOLERANCE_MM):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tolerance


def profile_size_issue(filename_profile_value, geometry_profile):
    """Return a size/shape discrepancy; wall thickness is deliberately ignored."""
    if not filename_profile_value:
        return None

    fkind = filename_profile_value["kind"]
    gkind = geometry_profile.get("kind")

    if fkind == "Circle":
        if gkind != "Circle":
            return f"profilo filename=Circle, ZZX={gkind}"
        fd = filename_profile_value["diameter"]
        gd = geometry_profile.get("outside_diameter")
        if not _close(fd, gd):
            return f"diametro filename Ø{fd:g}, ZZX Ø{float(gd):g}" if gd is not None else f"diametro filename Ø{fd:g}, ZZX non disponibile"
        return None

    if gkind not in {"Square", "Rect"}:
        return f"profilo filename={fkind}, ZZX={gkind}"

    fw = filename_profile_value["width"]
    fh = filename_profile_value["height"]
    gw = geometry_profile.get("outside_width")
    gh = geometry_profile.get("outside_height")
    direct = _close(fw, gw) and _close(fh, gh)
    swapped = _close(fw, gh) and _close(fh, gw)
    if direct or swapped:
        return None

    if gw is None or gh is None:
        return f"misura filename {fw:g}x{fh:g}, misura ZZX non disponibile"
    return f"misura filename {fw:g}x{fh:g}, ZZX {float(gw):g}x{float(gh):g}"


def validate_zzx_file(path):
    """Return user-facing validation issues for one ZZX file."""
    filename = os.path.basename(path)
    result = describe_tube_parts(path)
    status = result.get("status", "error")

    if status != "ok":
        error = result.get("error", "Errore sconosciuto")
        if is_known_array_without_terminal_cut(filename, error):
            return []
        return [{
            "type": "geometry_model",
            "message": f"Geometria ZZX non utilizzabile: {error}",
        }]

    parts = result.get("parts", [])
    if len(parts) != 1:
        return [{
            "type": "segment_count",
            "message": f"Il file contiene {len(parts)} TubeSegment; atteso 1 per un file pezzo.",
        }]

    part = parts[0]
    issues = []

    declared_length = filename_length(filename)
    if declared_length is not None:
        geometry_length = float(part.get("overall_length"))
        difference = geometry_length - declared_length
        if abs(difference) > LENGTH_TOLERANCE_MM:
            issues.append({
                "type": "length",
                "message": (
                    f"Lunghezza filename L{declared_length:g}, "
                    f"ZZX {geometry_length:.3f} mm "
                    f"(differenza {difference:+.3f} mm)"
                ),
                "filenameLength": declared_length,
                "geometryLength": geometry_length,
                "differenceMm": difference,
            })

    size_issue = profile_size_issue(filename_profile(filename), part.get("profile") or {})
    if size_issue:
        issues.append({
            "type": "profile_size",
            "message": size_issue,
        })

    return issues
