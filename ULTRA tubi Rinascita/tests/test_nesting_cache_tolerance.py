"""Cache reset and configurable stock tolerance regression checks."""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import backend_logic as logic


class RodToleranceTests(unittest.TestCase):
    def test_default_is_one_mm_above_nominal_length(self):
        self.assertEqual(logic.configured_nesting_rod_length({}, 6000), 6001)

    def test_custom_tolerance(self):
        self.assertEqual(logic.configured_nesting_rod_length(
            {"nesting_rod_tolerance_mm": 2.5}, 6000
        ), 6002.5)
        self.assertEqual(logic.configured_nesting_rod_length(
            {"nesting_rod_tolerance_mm": 0}, 6000
        ), 6000)

    def test_invalid_negative_or_oversized_value_clamped(self):
        self.assertEqual(logic.configured_nesting_rod_length(
            {"nesting_rod_tolerance_mm": -1}, 6000
        ), 6000)
        self.assertEqual(logic.configured_nesting_rod_length(
            {"nesting_rod_tolerance_mm": 1000}, 6000
        ), 6050)
        self.assertEqual(logic.configured_nesting_rod_length(
            {"nesting_rod_tolerance_mm": "nan"}, 6000
        ), 6001)


class CacheResetTests(unittest.TestCase):
    def test_clearing_removes_disk_cache_and_prevents_stale_repopulation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            filename = os.path.join(tempdir, "nesting_cache.json")
            saved_entries = dict(logic._NESTING_GROUP_CACHE)
            try:
                with (
                    patch.object(logic, "NESTING_CACHE_FILE", filename),
                    patch.object(logic, "_NESTING_GROUP_CACHE_LOADED", True),
                ):
                    logic._NESTING_GROUP_CACHE.clear()
                    before = logic._NESTING_CACHE_GENERATION
                    signature = "a" * 64
                    logic._cache_nesting_group(signature, [{"placements": []}])
                    self.assertTrue(os.path.exists(filename))
                    self.assertEqual(len(logic._NESTING_GROUP_CACHE), 1)

                    result = logic.clear_nesting_result_cache()
                    self.assertEqual(result["status"], "success")
                    self.assertEqual(result["removed"], 1)
                    self.assertFalse(os.path.exists(filename))
                    self.assertEqual(logic._NESTING_GROUP_CACHE, {})
                    self.assertGreater(logic._NESTING_CACHE_GENERATION, before)

                    logic._cache_nesting_group(
                        signature, [{"placements": []}], generation=before
                    )
                    self.assertEqual(logic._NESTING_GROUP_CACHE, {})
                    self.assertFalse(os.path.exists(filename))

                    logic._cache_nesting_group(
                        signature, [{"placements": []}],
                        generation=logic._NESTING_CACHE_GENERATION,
                    )
                    self.assertTrue(os.path.exists(filename))
            finally:
                logic._NESTING_GROUP_CACHE.clear()
                logic._NESTING_GROUP_CACHE.update(saved_entries)


if __name__ == "__main__":
    unittest.main()
