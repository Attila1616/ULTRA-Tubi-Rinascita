"""Verify safe export behavior when final common-line geometry is rejected."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import backend_logic as logic


def _segment():
    return {
        "filePath": "source.zzx",
        "fileName": "source.zzx",
        "nestPlacement": {
            "z_start": 0.0, "z_end": 100.0,
            "axial_rotation_degrees": 0.0,
            "reversed_end_for_end": False,
            "common_line_before": True,
        },
    }


class LockedRodExportFallbackTests(unittest.TestCase):
    def _invoke(self, *, shift=False, first_export_rejected=False):
        originals = [_segment()]
        shifted = deepcopy(originals)
        shifted[0]["nestPlacement"]["common_line_before"] = False
        exported_payloads = []

        def fake_export(current, *_args, **_kwargs):
            exported_payloads.append(deepcopy(current))
            if first_export_rejected and len(exported_payloads) == 1:
                raise ValueError(
                    "Shared-cut verification failed after final positioning. "
                    "Shared-boundary Shapes 1017/1032 outer contours "
                    "differ by 0.0351678 mm"
                )
            return {
                "path": "generated.zzx",
                "warnings": [],
                "pieceCount": len(current),
            }

        with (
            patch.object(logic, "load_config", return_value={
                "nested_zzx_output_dir": "fake-export-path",
                "nesting_gap_mm": 2.0,
                "nested_text_marking_enabled": False,
                "nested_round_tip_marking_enabled": False,
            }),
            patch.object(
                logic, "configured_nesting_rod_length",
                return_value=6000.0,
            ),
            patch.object(
                logic.tubenest_engine,
                "reflow_export_placements_with_gap",
                return_value=shifted,
            ) as reflow,
            patch.object(
                logic.tubenest_engine,
                "export_flat_nested_rod_to_directory",
                side_effect=fake_export,
            ),
        ):
            result = logic.export_locked_rod_zzx({
                "rodId": "rod-1",
                "tubeType": "100x100x3",
                "segments": originals,
                "ignoreCommonLines": shift,
                "rodLength": 6000.0,
            })

        return result, originals, shifted, exported_payloads, reflow.call_count

    def test_invalid_shared_pair_retries_with_gap_and_reports_it(self):
        result, originals, shifted, attempts, reflows = self._invoke(
            first_export_rejected=True
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], originals)
        self.assertEqual(attempts[1], shifted)
        self.assertEqual(reflows, 1)
        self.assertTrue(result["commonLineFallback"])
        self.assertEqual(result["gapUsedMm"], 2.0)
        self.assertTrue(result["warnings"])

    def test_shift_skips_common_line_attempt_entirely(self):
        result, originals, shifted, attempts, reflows = self._invoke(
            shift=True
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(attempts, [shifted])
        self.assertEqual(reflows, 1)
        self.assertFalse(result["commonLineFallback"])

    def test_normal_valid_common_line_is_untouched(self):
        result, originals, shifted, attempts, reflows = self._invoke()
        self.assertEqual(result["status"], "success")
        self.assertEqual(attempts, [originals])
        self.assertEqual(reflows, 0)
        self.assertNotIn("commonLineGapMode", result)


if __name__ == "__main__":
    unittest.main()
