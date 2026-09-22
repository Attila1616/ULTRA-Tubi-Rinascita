"""Scan the configured Tubi tree with the read-only TubeNest engine.

This is a development diagnostic. It never modifies ZZX files or database state.
"""
from collections import Counter
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import runtime_paths
from tubenest_engine import describe_zzx


def main():
    if not os.path.exists(runtime_paths.CONFIG_FILE):
        print(f"Config not found: {runtime_paths.CONFIG_FILE}")
        return 2

    with open(runtime_paths.CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    tubi_root = str(config.get("da_fare_path") or runtime_paths.TUBI_DIR).strip()
    if not tubi_root or not os.path.isdir(tubi_root):
        print(f"Tubi folder not found: {tubi_root}")
        return 2

    ignore = {str(x).strip().lower() for x in config.get("ignore_folders", []) if str(x).strip()}
    statuses = Counter()
    profiles = Counter()
    layers = Counter()
    failures = []
    total_segments = 0

    files = []
    for root, dirs, names in os.walk(tubi_root):
        rel = os.path.relpath(root, tubi_root)
        rel_parts = [] if rel == "." else [p.lower() for p in rel.split(os.path.sep)]
        if any(part in ignore for part in rel_parts):
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d.lower() not in ignore]
        for name in names:
            if name.lower().endswith(".zzx"):
                files.append(os.path.join(root, name))

    print(f"Variables: {runtime_paths.VARIABLES_ROOT}")
    print(f"Tubi:      {tubi_root}")
    print(f"ZZX files: {len(files)}")
    print()

    for index, path in enumerate(files, 1):
        result = describe_zzx(path)
        status = result.get("status", "error")
        statuses[status] += 1

        if status == "ok":
            doc = result["document"]
            total_segments += int(doc.get("segment_count") or 0)
            for segment in doc.get("segments", []):
                profile = segment.get("profile") or {}
                profiles[profile.get("section_class") or "UNKNOWN"] += 1
                for layer in segment.get("active_layers", []):
                    layers[str(layer)] += 1
        else:
            failures.append((path, status, result.get("error", "")))

        if index % 100 == 0:
            print(f"Scanned {index}/{len(files)}...")

    print("\n=== ZZX GEOMETRY SCAN ===")
    print(f"Files:       {len(files)}")
    print(f"Segments:    {total_segments}")
    print(f"OK:          {statuses['ok']}")
    print(f"Unsupported: {statuses['unsupported']}")
    print(f"Errors:      {statuses['error']}")
    print(f"Profiles:    {dict(sorted(profiles.items()))}")
    print(f"Layers:      {dict(sorted(layers.items()))}")

    if failures:
        print("\n=== FAILURES ===")
        for path, status, error in failures:
            print(f"[{status.upper()}] {os.path.relpath(path, tubi_root)}")
            print(f"  {error}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
