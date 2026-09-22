"""Compare normalized TubePart geometry with legacy filename metadata.

Read-only development diagnostic. Nothing in the Tubi tree is modified.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths
from tubenest_engine import describe_tube_parts


LENGTH_TOLERANCE_MM = 0.5
PROFILE_TOLERANCE_MM = 0.1


def _filename_length(name):
    match = re.search(r"(?i)(?:^|\s)L(\d+(?:\.\d+)?)", name)
    return float(match.group(1)) if match else None


def _filename_profile(name):
    circle = re.search(r"(?i)Ø\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", name)
    if circle:
        return {
            "kind": "Circle",
            "diameter": float(circle.group(1)),
            "thickness": float(circle.group(2)),
        }

    box = re.search(
        r"(?i)(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)",
        name,
    )
    if box:
        width, height, thickness = map(float, box.groups())
        return {
            "kind": "Square" if abs(width - height) <= 1e-9 else "Rect",
            "width": width,
            "height": height,
            "thickness": thickness,
        }
    return None


def _close(a, b, tolerance=PROFILE_TOLERANCE_MM):
    return a is not None and b is not None and abs(float(a) - float(b)) <= tolerance


def _profile_matches(filename_profile, geometry_profile):
    if not filename_profile:
        return None, "filename profile not recognized"

    gkind = geometry_profile.get("kind")
    fkind = filename_profile["kind"]
    if fkind == "Circle":
        if gkind != "Circle":
            return False, f"kind filename=Circle geometry={gkind}"
        if not _close(filename_profile["diameter"], geometry_profile.get("outside_diameter")):
            return False, (
                f"diameter filename={filename_profile['diameter']:.3f} "
                f"geometry={geometry_profile.get('outside_diameter')}"
            )
    else:
        if gkind not in {"Square", "Rect"}:
            return False, f"kind filename={fkind} geometry={gkind}"

        fw = filename_profile["width"]
        fh = filename_profile["height"]
        gw = geometry_profile.get("outside_width")
        gh = geometry_profile.get("outside_height")
        direct = _close(fw, gw) and _close(fh, gh)
        swapped = _close(fw, gh) and _close(fh, gw)
        if not (direct or swapped):
            return False, (
                f"size filename={fw:g}x{fh:g} "
                f"geometry={gw}x{gh}"
            )

    if not _close(filename_profile["thickness"], geometry_profile.get("thickness")):
        return False, (
            f"thickness filename={filename_profile['thickness']:.3f} "
            f"geometry={geometry_profile.get('thickness')}"
        )
    return True, ""


def main():
    with open(runtime_paths.CONFIG_FILE, "r", encoding="utf-8") as stream:
        config = json.load(stream)

    tubi_root = Path(str(config.get("da_fare_path") or runtime_paths.TUBI_DIR)).resolve()
    if not tubi_root.is_dir():
        print(f"Tubi folder not found: {tubi_root}")
        return 2

    files = sorted(tubi_root.rglob("*.zzx"), key=lambda p: str(p).casefold())

    status_counts = Counter()
    profile_kinds = Counter()
    file_versions = Counter()
    layer_sets = Counter()
    length_diffs = []
    length_mismatches = []
    profile_mismatches = []
    unrecognized_profiles = []
    multi_segment = []
    inferred_profiles = []
    total_parts = 0

    print(f"Tubi:      {tubi_root}")
    print(f"ZZX files: {len(files)}")
    print()

    for index, path in enumerate(files, 1):
        result = describe_tube_parts(path)
        status = result.get("status", "error")
        status_counts[status] += 1
        relative = str(path.relative_to(tubi_root))

        if status != "ok":
            length_mismatches.append((relative, None, None, result.get("error", "")))
            continue

        parts = result.get("parts", [])
        total_parts += len(parts)
        if len(parts) != 1:
            multi_segment.append((relative, len(parts)))

        legacy_length = _filename_length(path.name)
        filename_profile = _filename_profile(path.name)

        for part_index, part in enumerate(parts):
            geometry_length = float(part["overall_length"])
            suffix = f" [segment {part_index + 1}/{len(parts)}]" if len(parts) > 1 else ""

            file_versions[part.get("source_file_version") or "?"] += 1
            profile = part.get("profile") or {}
            profile_kinds[profile.get("kind") or "Unknown"] += 1
            layer_sets[tuple(part.get("active_layers") or [])] += 1
            if profile.get("inferred_from_geometry"):
                inferred_profiles.append(relative + suffix)

            if legacy_length is not None and len(parts) == 1:
                diff = geometry_length - legacy_length
                length_diffs.append((abs(diff), diff, relative, legacy_length, geometry_length))
                if abs(diff) > LENGTH_TOLERANCE_MM:
                    length_mismatches.append(
                        (relative, legacy_length, geometry_length, f"difference {diff:+.3f} mm")
                    )

            match, reason = _profile_matches(filename_profile, profile)
            if match is None:
                unrecognized_profiles.append(relative + suffix)
            elif not match:
                profile_mismatches.append((relative + suffix, reason))

        if index % 100 == 0:
            print(f"Compared {index}/{len(files)}...")

    length_diffs.sort(reverse=True)
    max_abs_diff = length_diffs[0][0] if length_diffs else 0.0
    mean_abs_diff = (
        sum(item[0] for item in length_diffs) / len(length_diffs)
        if length_diffs else 0.0
    )

    print("\n=== TUBEPART VALIDATION ===")
    print(f"Files:                    {len(files)}")
    print(f"Parts:                    {total_parts}")
    print(f"OK parser/model files:    {status_counts['ok']}")
    print(f"Unsupported:              {status_counts['unsupported']}")
    print(f"Errors:                   {status_counts['error']}")
    print(f"Multi-segment files:      {len(multi_segment)}")
    print(f"Profile kinds:            {dict(sorted(profile_kinds.items()))}")
    print(f"File versions:            {dict(sorted(file_versions.items()))}")
    print(f"Layer sets:               {dict(sorted((str(k), v) for k, v in layer_sets.items()))}")
    print(f"Inferred profiles:        {len(inferred_profiles)}")
    print()
    print(f"Length comparisons:       {len(length_diffs)}")
    print(f"Within ±{LENGTH_TOLERANCE_MM:g} mm:       {len(length_diffs) - len(length_mismatches)}")
    print(f"Length mismatches:        {len(length_mismatches)}")
    print(f"Max absolute difference:  {max_abs_diff:.6f} mm")
    print(f"Mean absolute difference: {mean_abs_diff:.6f} mm")
    print(f"Profile mismatches:       {len(profile_mismatches)}")
    print(f"Unrecognized filenames:   {len(unrecognized_profiles)}")

    if length_diffs:
        print("\n=== LARGEST LENGTH DIFFERENCES ===")
        for abs_diff, diff, relative, legacy, geometry in length_diffs[:20]:
            print(
                f"{diff:+10.4f} mm | filename {legacy:9.3f} | geometry {geometry:9.3f} | {relative}"
            )

    if length_mismatches:
        print("\n=== LENGTH / MODEL ISSUES ===")
        for relative, legacy, geometry, reason in length_mismatches:
            print(f"{relative}")
            if legacy is not None:
                print(f"  filename={legacy:.3f} geometry={geometry:.3f} -> {reason}")
            else:
                print(f"  {reason}")

    if profile_mismatches:
        print("\n=== PROFILE MISMATCHES ===")
        for relative, reason in profile_mismatches:
            print(f"{relative}")
            print(f"  {reason}")

    if multi_segment:
        print("\n=== MULTI-SEGMENT FILES ===")
        for relative, count in multi_segment:
            print(f"{count} segments | {relative}")

    if inferred_profiles:
        print("\n=== GEOMETRY-INFERRED PROFILES ===")
        for relative in inferred_profiles:
            print(relative)

    if unrecognized_profiles:
        print("\n=== UNRECOGNIZED FILENAME PROFILES ===")
        for relative in unrecognized_profiles:
            print(relative)

    # This diagnostic reports mismatches but does not treat them as a program failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
