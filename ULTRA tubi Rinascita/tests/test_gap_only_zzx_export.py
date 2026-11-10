"""Safety regression for Shift+ZZX export without co-edge cuts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tubenest_engine.placement_reflow import reflow_export_placements_with_gap
from tubenest_engine.flat_exporter import export_flat_nested_rod
from tubenest_engine.domain import read_tube_parts


APP_ROOT = Path(__file__).resolve().parents[1]


def _fake_part(length=100.0, c_near=10.0, c_far=90.0):
    # A / / pair: physical axial bounding boxes may overlap by 18 mm,
    # even with >=2 mm clearance at EVERY point of the common cross-section.
    part = {
        "overall_length": length,
        "profile": {
            "kind": "Square",
            "outside_width": 40.0,
            "outside_height": 40.0,
            "thickness": 2.0,
            "corner_radius": 0.0,
        },
        "ends": [
            {
                "label": "A",
                "plane_z_equals_c_plus_ax_plus_by": [c_near, 0.5, 0.0],
                "plane_max_residual_mm": 0.0,
                "operation_layer": 1,
            },
            {
                "label": "B",
                "plane_z_equals_c_plus_ax_plus_by": [c_far, 0.5, 0.0],
                "plane_max_residual_mm": 0.0,
                "operation_layer": 1,
            },
        ],
    }

    class Part:
        segment_handle = 1001
        overall_length = length

        def to_dict(self):
            return deepcopy(part)

    return Part()


def _source(path, key, index, common):
    return {
        "filePath": path,
        "fileName": Path(path).name,
        "instanceKey": key,
        "segmentHandle": 1001,
        "nestPlacement": {
            "z_start": float(index * 100.0),
            "z_end": float((index + 1) * 100.0),
            "axial_rotation_degrees": 0.0,
            "reversed_end_for_end": False,
            "common_line_before": common,
            "common_line_after": True,
        },
    }


class GapOnlyExportTests(unittest.TestCase):
    def test_angled_pieces_keep_slashes_and_interlock_with_real_gap(self):
        first = _source("dummy.zzx", "a", 0, False)
        second = _source("dummy.zzx", "b", 1, True)
        original = deepcopy([first, second])

        with patch(
            "tubenest_engine.placement_reflow.read_tube_parts",
            return_value=[_fake_part()],
        ):
            placements = reflow_export_placements_with_gap(
                [first, second], gap_mm=2.0, rod_length=6000.0
            )

        self.assertEqual([first, second], original, "Never modify the UI nesting")
        self.assertEqual(placements[0]["nestPlacement"]["z_start"], 0.0)
        self.assertAlmostEqual(placements[1]["nestPlacement"]["z_start"], 82.0)
        self.assertAlmostEqual(placements[1]["nestPlacement"]["z_end"], 182.0)
        self.assertEqual(placements[1]["nestPlacement"]["overlap_before_mm"], 18.0)
        self.assertEqual(placements[1]["nestPlacement"]["gap_before_mm"], 2.0)
        for segment in placements:
            saved = segment["nestPlacement"]
            self.assertFalse(saved["common_line_before"])
            self.assertFalse(saved["common_line_after"])
            self.assertFalse(saved["reversed_end_for_end"])
            self.assertEqual(saved["axial_rotation_degrees"], 0.0)

    def test_reject_when_gap_reflow_does_not_fit_stock(self):
        sources = [
            _source("dummy.zzx", "a", 0, False),
            _source("dummy.zzx", "b", 1, True),
        ]
        before = deepcopy(sources)
        with patch(
            "tubenest_engine.placement_reflow.read_tube_parts",
            return_value=[_fake_part()],
        ):
            with self.assertRaisesRegex(ValueError, "non entra sulla verga"):
                reflow_export_placements_with_gap(
                    sources, gap_mm=2.0, rod_length=181.0
                )
        self.assertEqual(sources, before)

    def test_gap_only_can_export_real_zzx_without_shared_deletion(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        parts = read_tube_parts(source)
        self.assertEqual(len(parts), 1)
        part = parts[0]
        length = float(part.overall_length)
        segments = [
            {
                "instanceKey": f"source::{i}",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": length * i,
                    "z_end": length * (i + 1),
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": i > 0,
                },
            }
            for i in range(2)
        ]
        fitted = reflow_export_placements_with_gap(
            segments, gap_mm=2.0, rod_length=6000.0
        )
        self.assertTrue(
            all(not s["nestPlacement"]["common_line_before"] for s in fitted)
        )
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / "separated.zzx"
            result = export_flat_nested_rod(
                fitted, out, rod_length=6000.0, gap_mm=2.0
            )
            self.assertTrue(out.is_file())
            self.assertEqual(result["pieceCount"], 2)
            self.assertEqual(
                result["cutReleaseRepair"]["rejected_shared_pairs"], []
            )

    def test_button_shift_option_is_passed_to_python(self):
        script = (APP_ROOT / "web" / "script.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("ignoreCommonLines: !!event.shiftKey", script)
        self.assertIn("ignoreCommonLines: !!ignoreCommonLines", script)


if __name__ == "__main__":
    unittest.main()
