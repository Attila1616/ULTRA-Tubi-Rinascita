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
