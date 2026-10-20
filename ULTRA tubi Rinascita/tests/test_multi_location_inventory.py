import unittest
from unittest.mock import patch

import tube_database


class MultiLocationTubeTests(unittest.TestCase):
    def test_groups_positive_positions_and_requires_two_locations(self):
        source_rows = [
            {
                "misura": "50x50",
                "spessore": 3,
                "materiale": "304",
                "finitura": "2B",
                "forma": "quadrato",
                "quantitaTotale": 7,
                "codice": "T0500500304",
                "hasCode": True,
                "posizioni": [
                    {
                        "label": "S1 R1",
                        "scaffalatura": "Scaffalatura 1",
                        "ripiano": "Ripiano 1",
                        "quantita": 2,
                    },
                    {
                        "label": "S1 R1",
                        "scaffalatura": "Scaffalatura 1",
                        "ripiano": "Ripiano 1",
                        "quantita": 1,
                    },
                    {
                        "label": "S3 R2",
                        "scaffalatura": "Scaffalatura 3",
                        "ripiano": "Ripiano 2",
                        "quantita": 4,
                    },
                    {
                        "label": "S9 R9",
                        "scaffalatura": "Scaffalatura 9",
                        "ripiano": "Ripiano 9",
                        "quantita": 0,
                    },
                ],
            },
            {
                "misura": "80x80",
                "spessore": 2,
                "materiale": "304",
                "finitura": "2B",
                "forma": "quadrato",
                "quantitaTotale": 5,
                "codice": "",
                "hasCode": False,
                "posizioni": [
                    {
                        "label": "S2 R1",
                        "scaffalatura": "Scaffalatura 2",
                        "ripiano": "Ripiano 1",
                        "quantita": 5,
                    }
                ],
            },
        ]

        with patch(
            "tube_database.summarize_inventory",
            return_value=source_rows,
        ):
            rows = tube_database.get_multi_location_tubes(
                "unused-root"
            )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["misura"], "50x50")
        self.assertEqual(row["numeroPosizioni"], 2)
        self.assertEqual(row["quantitaTotale"], 7)
        self.assertEqual(
            [(p["label"], p["quantita"]) for p in row["posizioni"]],
            [("S1 R1", 3), ("S3 R2", 4)],
        )

    def test_empty_inventory_returns_empty_list(self):
        with patch(
            "tube_database.summarize_inventory",
            return_value=[],
        ):
            self.assertEqual(
                tube_database.get_multi_location_tubes(
                    "unused-root"
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
