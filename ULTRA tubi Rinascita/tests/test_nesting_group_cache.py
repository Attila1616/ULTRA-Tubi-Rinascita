import unittest
from unittest.mock import patch

import backend_logic


class NestingGroupCacheTests(unittest.TestCase):
    def setUp(self):
        backend_logic._NESTING_GROUP_CACHE.clear()

    def test_identical_group_request_reuses_cached_rods(self):
        groups = [{
            "tubeType": "100x100x3 304 2B",
            "pieces": [
                {
                    "instanceKey": "piece::1",
                    "sourceId": "source-1",
                    "length": 1000,
                    "filePath": "",
                    "tubePart": None,
                },
                {
                    "instanceKey": "piece::2",
                    "sourceId": "source-1",
                    "length": 1000,
                    "filePath": "",
                    "tubePart": None,
                },
            ],
        }]
        fake_rods = [{
            "rodLength": 6000.0,
            "used": 2002.0,
            "remaining": 3998.0,
            "placements": [],
            "pieces": [],
        }]

        with patch.object(
            backend_logic.tubenest_engine,
            "optimize_items_dict",
            return_value=fake_rods,
        ) as optimize:
            first = backend_logic.nest_piece_groups(groups, rod_length=6000)
            second = backend_logic.nest_piece_groups(groups, rod_length=6000)

        self.assertEqual(optimize.call_count, 1)
        self.assertFalse(first["groups"][0]["cacheHit"])
        self.assertTrue(second["groups"][0]["cacheHit"])
        self.assertEqual(first["groups"][0]["signature"], second["groups"][0]["signature"])
        self.assertEqual(first["groups"][0]["rods"], second["groups"][0]["rods"])

    def test_gap_change_invalidates_group_cache_signature(self):
        pieces = [{
            "instanceKey": "piece::1",
            "length": 1000,
            "filePath": "",
            "tubePart": None,
        }]
        a = backend_logic._nesting_group_signature(
            "100x100x3 304 2B", pieces, 6000, 2.0
        )
        b = backend_logic._nesting_group_signature(
            "100x100x3 304 2B", pieces, 6000, 3.0
        )
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
