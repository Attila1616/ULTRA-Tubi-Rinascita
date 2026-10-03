import tempfile
import unittest
from pathlib import Path

import backend_logic


class PersistentNestingCacheTests(unittest.TestCase):
    def setUp(self):
        self.old_file = backend_logic.NESTING_CACHE_FILE
        self.old_cache = backend_logic._NESTING_GROUP_CACHE
        self.old_loaded = backend_logic._NESTING_GROUP_CACHE_LOADED

        self.tmp = tempfile.TemporaryDirectory()
        backend_logic.NESTING_CACHE_FILE = str(
            Path(self.tmp.name) / "nesting_cache.json"
        )
        backend_logic._NESTING_GROUP_CACHE = {}
        backend_logic._NESTING_GROUP_CACHE_LOADED = True

    def tearDown(self):
        backend_logic.NESTING_CACHE_FILE = self.old_file
        backend_logic._NESTING_GROUP_CACHE = self.old_cache
        backend_logic._NESTING_GROUP_CACHE_LOADED = self.old_loaded
        self.tmp.cleanup()

    def test_cache_round_trip_survives_memory_reset(self):
        signature = "a" * 64
        rods = [
            {
                "used": 1234.5,
                "placements": [
                    {"instance_key": "A::1"}
                ],
            }
        ]
        backend_logic._cache_nesting_group(signature, rods)

        backend_logic._NESTING_GROUP_CACHE = {}
        backend_logic._NESTING_GROUP_CACHE_LOADED = False
        backend_logic._load_persistent_nesting_cache()

        self.assertEqual(
            backend_logic._NESTING_GROUP_CACHE[signature],
            rods,
        )

    def test_signature_is_independent_of_piece_order(self):
        a = {
            "instanceKey": "A::1",
            "length": 100.0,
            "filePath": "A.zzx",
            "tubePart": {
                "part_fingerprint": "part-a",
                "source_sha256": "sha-a",
            },
        }
        b = {
            "instanceKey": "B::1",
            "length": 200.0,
            "filePath": "B.zzx",
            "tubePart": {
                "part_fingerprint": "part-b",
                "source_sha256": "sha-b",
            },
        }

        first = backend_logic._nesting_group_signature(
            "40x40x3 304 2B",
            [a, b],
            6000,
            2.0,
        )
        second = backend_logic._nesting_group_signature(
            "40x40x3 304 2B",
            [b, a],
            6000,
            2.0,
        )

        self.assertEqual(first, second)

    def test_adding_piece_invalidates_signature(self):
        piece = {
            "instanceKey": "A::1",
            "length": 100.0,
            "filePath": "A.zzx",
            "tubePart": {
                "part_fingerprint": "part-a",
                "source_sha256": "sha-a",
            },
        }
        extra = dict(piece)
        extra["instanceKey"] = "A::2"

        before = backend_logic._nesting_group_signature(
            "40x40x3 304 2B",
            [piece],
            6000,
            2.0,
        )
        after = backend_logic._nesting_group_signature(
            "40x40x3 304 2B",
            [piece, extra],
            6000,
            2.0,
        )

        self.assertNotEqual(before, after)

    def test_source_change_invalidates_signature(self):
        piece = {
            "instanceKey": "A::1",
            "length": 100.0,
            "filePath": "A.zzx",
            "tubePart": {
                "part_fingerprint": "part-a",
                "source_sha256": "sha-a",
            },
        }
        changed = {
            **piece,
            "tubePart": {
                "part_fingerprint": "part-a-new",
                "source_sha256": "sha-a-new",
            },
        }

        before = backend_logic._nesting_group_signature(
            "40x40x3 304 2B",
            [piece],
            6000,
            2.0,
        )
        after = backend_logic._nesting_group_signature(
            "40x40x3 304 2B",
            [changed],
            6000,
            2.0,
        )

        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
