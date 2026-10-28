"""ETA snapshots and independent fast/deep progress backend contract."""
import unittest
from unittest.mock import patch

import backend_logic as logic


class ProgressApiTests(unittest.TestCase):
    def test_status_contains_fraction_eta_and_elapsed_time(self):
        ident = "unit-test-progress-eta"
        try:
            logic._report_nesting_job(
                ident, "Assemblaggio verghe",
                fraction=.45, eta=19.0,
                processed=9, total=20,
            )
            status = logic.get_nesting_job_status(ident)
            self.assertEqual(status["stage"], "Assemblaggio verghe")
            self.assertEqual(status["percent"], 45)
            self.assertGreaterEqual(status["etaSeconds"], 0)
            self.assertLessEqual(status["etaSeconds"], 19)
            self.assertEqual(status["processed"], 9)
            self.assertFalse(status["done"])
            self.assertNotIn("startedAt", status)
            self.assertNotIn("lastUpdatedAt", status)
            logic._report_nesting_job(
                ident, "Completato", fraction=1, eta=0, done=True
            )
            final = logic.get_nesting_job_status(ident)
            self.assertTrue(final["done"])
            self.assertEqual(final["percent"], 100)
        finally:
            logic._NESTING_JOB_STATUS.pop(ident, None)

    def test_backend_accepts_more_concentrated_rod_at_equal_rod_count(self):
        # Same total material consumption, but an L215 has moved from a
        # short donor into a nearly full receiving rod. This was previously
        # discarded by the old total-length-only acceptance rule.
        from tubenest_engine import deep_optimizer
        ident = "unit-test-215-fill-acceptance"
        old_rods = [
            {"used": 5576.0, "placements": []},
            {"used": 2984.0, "placements": []},
        ]
        new_rods = [
            {"used": 5793.0, "placements": []},
            {"used": 2767.0, "placements": []},
        ]
        payload = {
            "jobId": ident,
            "rodLength": 6000,
            "group": {
                "tubeType": "square-test",
                "pieces": [{
                    "instanceKey": "piece-0",
                    "sourceId": "draw-0",
                    "length": 4400,
                    "tubePart": {"overall_length": 4400},
                }],
            },
            "rods": old_rods,
        }
        mocked = {
            "rods": new_rods,
            "stats": {"finalRods": 2, "finalUsedMm": 8560.0},
        }
        try:
            with (
                patch.object(logic, "load_config", return_value={
                    "nesting_gap_mm": 2.0,
                    "nesting_rod_tolerance_mm": 1.0,
                }),
                patch.object(logic, "validate_tube_part_data", return_value=[]),
                patch.object(logic, "_nesting_group_signature", return_value="test"),
                patch.object(logic, "_cache_nesting_group") as cache,
                patch.object(deep_optimizer, "optimize_existing_rods",
                             return_value=mocked),
            ):
                answer = logic.optimize_calculated_nesting(payload)
                cache.assert_called_once()
            self.assertEqual(answer["status"], "success")
            self.assertNotIn("keptOriginal", answer["stats"])
            self.assertGreater(
                answer["stats"]["finalFillConcentration"],
                answer["stats"]["initialFillConcentration"],
            )
            self.assertEqual(answer["rods"], new_rods)
        finally:
            logic._NESTING_JOB_STATUS.pop(ident, None)

    def test_invalid_deep_payload_reports_error_without_cache_change(self):
        ident = "unit-test-progress-error"
        try:
            with patch.object(logic, "_cache_nesting_group") as cache:
                response = logic.optimize_calculated_nesting({
                    "jobId": ident, "group": {
                        "tubeType": "test",
                        "sources": {},
                        "pieces": [{
                            "instanceKey": "p1",
                            "sourceId": "missing",
                            "length": 100,
                        }]
                    },
                    "rods": [{"placements": [{
                        "instance_key": "p1"
                    }]}],
                })
                cache.assert_not_called()
            self.assertEqual(response["status"], "error")
            self.assertTrue(logic.get_nesting_job_status(ident)["done"])
        finally:
            logic._NESTING_JOB_STATUS.pop(ident, None)


if __name__ == "__main__":
    unittest.main()
