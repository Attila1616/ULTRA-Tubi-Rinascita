from pathlib import Path
import unittest

from tubenest_engine import nest_items_dict, read_tube_parts


ROOT = Path(__file__).resolve().parents[1]
ROUND_SAMPLE = ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"


class NestingEngineTests(unittest.TestCase):
    def test_first_fit_decreasing_matches_legacy_behavior(self):
        rods = nest_items_dict([
            {"instanceKey": "a", "sourceId": "A", "length": 4000},
            {"instanceKey": "b", "sourceId": "B", "length": 2500},
            {"instanceKey": "c", "sourceId": "C", "length": 2000},
            {"instanceKey": "d", "sourceId": "D", "length": 1000},
        ])

        self.assertEqual(len(rods), 2)
        self.assertEqual(rods[0]["pieces"], ["A", "C"])
        self.assertEqual(rods[0]["remaining"], 0.0)
        self.assertEqual(rods[1]["pieces"], ["B", "D"])
        self.assertEqual(rods[1]["remaining"], 2500.0)

    def test_placements_have_explicit_axial_coordinates(self):
        rods = nest_items_dict([
            {"instanceKey": "a", "sourceId": "A", "length": 1000},
            {"instanceKey": "b", "sourceId": "B", "length": 500},
        ])
        placements = rods[0]["placements"]

        self.assertEqual(placements[0]["z_start"], 0.0)
        self.assertEqual(placements[0]["z_end"], 1000.0)
        self.assertEqual(placements[1]["z_start"], 1000.0)
        self.assertEqual(placements[1]["z_end"], 1500.0)

    def test_geometry_metadata_is_carried_without_changing_nominal_consumption(self):
        part = read_tube_parts(ROUND_SAMPLE)[0].to_dict()

        rods = nest_items_dict([
            {
                "instanceKey": "round-1",
                "sourceId": "piece-1",
                "length": 1215,
                "tubePart": part,
            }
        ])
        placement = rods[0]["placements"][0]

        self.assertTrue(placement["geometry_available"])
        self.assertEqual(placement["part_fingerprint"], part["part_fingerprint"])
        self.assertEqual(placement["source_sha256"], part["source_sha256"])
        self.assertEqual(len(placement["source_sha256"]), 64)
        self.assertEqual(placement["occupied_length"], 1215.0)
        self.assertEqual(placement["z_end"], 1215.0)
        self.assertIsNotNone(placement["end_a"])
        self.assertIsNotNone(placement["end_b"])

    def test_equal_lengths_keep_input_order(self):
        rods = nest_items_dict([
            {"instanceKey": "one", "sourceId": "1", "length": 1000},
            {"instanceKey": "two", "sourceId": "2", "length": 1000},
            {"instanceKey": "three", "sourceId": "3", "length": 1000},
        ])
        self.assertEqual(rods[0]["pieces"], ["1", "2", "3"])

    def test_oversize_piece_is_rejected(self):
        with self.assertRaises(ValueError):
            nest_items_dict([
                {"instanceKey": "too-long", "sourceId": "X", "length": 6001}
            ])


if __name__ == "__main__":
    unittest.main()
