"""Fast tube intake and order registration: quantities, destinations and flags."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import backend_logic as logic
import tube_database as db


def sample(misura="60x60", spessore="2", quantita="4", materiale="304", finitura="2B"):
    return {
        "misura": misura, "spessore": spessore, "quantita": quantita,
        "materiale": materiale, "finitura": finitura,
    }


class FastTubeBatchTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.config = patch.object(db, "_CONFIGURED_DATABASE_DIR", None)
        self.codes = patch.object(db, "_CONFIGURED_CODE_CATALOG_PATH", None)
        self.config.start()
        self.codes.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.codes.stop)
        db.write_json(db.manifest_path(self.root), {
            "schema_version": 1,
            "locations": [
                {"name": "Terra", "type": "terra", "file": "Terra.json"},
                {"name": "Scaffalatura 1", "type": "scaffalatura", "file": "S1.json"},
            ],
        })
        db.write_json(str(Path(db.database_dir(self.root)) / "Terra.json"), {
            "schema_version": 1,
            "name": "Terra", "type": "terra",
            "ripiani": [{"name": "Per Terra", "tubi": [
                db.make_tube("60x60", 2, "304", "2B", 3),
            ]}],
        })
        db.write_json(str(Path(db.database_dir(self.root)) / "S1.json"), {
            "schema_version": 1,
            "name": "Scaffalatura 1", "type": "scaffalatura",
            "ripiani": [
                {"name": "Ripiano 1", "tubi": []},
                {"name": "Ripiano 2", "tubi": []},
            ],
        })

    def terra_tubes(self):
        return db.load_location_by_name(self.root, "Terra")[1]["ripiani"][0]["tubi"]

    def test_orders_can_include_new_stockless_sizes_and_requested_quantities(self):
        requested = [sample("80x80", "3", "7"), sample("60.3", "2", "2", "316")]
        result = db.register_fast_tube_list(self.root, {
            "mode": "order", "items": requested,
        })
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["quantity"], 9)
        self.assertEqual(self.terra_tubes()[0]["quantita"], 3)
        keys = db.load_manual_order_keys(self.root)
        self.assertEqual(len(keys), 2)
        amounts = db.load_manual_order_quantities(self.root)
        self.assertEqual(amounts[db.identity_key_from_values("80x80", 3, "304", "2B")], 7)
        self.assertEqual(amounts[db.identity_key_from_values("60.3", 2, "316", "2B")], 2)

    def test_receive_merges_duplicate_rows_and_only_clears_received_orders(self):
        existing_order = db.identity_key_from_values("60x60", "2", "304", "2B")
        other_order = db.identity_key_from_values("80x80", 3, "316", "BA")
        db._save_manual_order_state(self.root, {existing_order, other_order}, {
            existing_order: 5, other_order: 4,
        })
        result = db.register_fast_tube_list(self.root, {
            "mode": "receive", "target_location": "Terra",
            "target_ripiano": "Per Terra",
            "items": [
                sample("60x60", 2, "4"),
                sample("60x60", "2", "2"),
                sample("Ø60,3", "1,5", "1", "316", "BA"),
            ],
        })
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["quantity"], 7)
        self.assertEqual(result["rows_count"], 2)
        self.assertEqual(result["cleared_order_count"], 1)
        tubes = {
            db.identity_key_from_tube(tube): tube["quantita"]
            for tube in self.terra_tubes()
        }
        self.assertEqual(tubes[existing_order], 9)
        self.assertEqual(tubes[db.identity_key_from_values("60.3", "1.5", "316", "BA")], 1)
        self.assertEqual(db.load_manual_order_keys(self.root), {other_order})
        self.assertEqual(db.load_manual_order_quantities(self.root), {other_order: 4})

    def test_selecting_other_existing_location_and_ripiano(self):
        result = db.register_fast_tube_list(self.root, {
            "mode": "receive", "target_location": "Scaffalatura 1",
            "target_ripiano": "Ripiano 2",
            "items": [sample("40x20", 2, 6)],
        })
        self.assertEqual(result["status"], "success")
        shelves = db.load_location_by_name(self.root, "Scaffalatura 1")[1]["ripiani"]
        self.assertEqual(shelves[0]["tubi"], [])
        self.assertEqual(shelves[1]["tubi"][0]["quantita"], 6)
        self.assertEqual(self.terra_tubes()[0]["quantita"], 3)

    def test_invalid_batch_and_missing_location_do_not_change_stock(self):
        for data in (
            {"mode": "receive", "items": [sample(), sample("60x60", "2", "1.5")]},
            {"mode": "receive", "items": [sample("abc", 2, 3)]},
            {"mode": "receive", "items": [sample()], "target_location": "Non esiste"},
            {"mode": "receive", "items": [sample()], "target_location": "Scaffalatura 1",
             "target_ripiano": "Ripiano 99"},
        ):
            with self.subTest(data=data):
                result = db.register_fast_tube_list(self.root, data)
                self.assertEqual(result["status"], "error")
                self.assertEqual(self.terra_tubes()[0]["quantita"], 3)
        self.assertEqual(db.load_manual_order_keys(self.root), set())

    def test_zero_stock_manual_order_can_be_removed_using_regular_ui_action(self):
        request = sample("90x90", "3", "6")
        result = db.register_fast_tube_list(self.root, {
            "mode": "order", "items": [request],
        })
        self.assertEqual(result["status"], "success")
        key = db.identity_key_from_values("90x90", 3, "304", "2B")
        result = db.set_manual_order_tubes(self.root, {
            "operation": "remove", "keys": [key],
        })
        self.assertEqual(result["status"], "success")
        self.assertNotIn(key, db.load_manual_order_keys(self.root))
        self.assertNotIn(key, db.load_manual_order_quantities(self.root))

    def test_legacy_manual_selection_keeps_explicit_quantities(self):
        row = db.make_tube("60x60", 2, "304", "2B", 1)
        a = db.identity_key_from_tube(row)
        another = db.identity_key_from_values("40x20", 2, "304", "2B")
        db._save_manual_order_state(self.root, {a, another}, {a: 8, another: 3})
        result = db.set_manual_order_tubes(self.root, {
            "operation": "remove", "keys": [a],
        })
        self.assertEqual(result["status"], "success")
        self.assertEqual(db.load_manual_order_quantities(self.root), {another: 3})

    def test_manual_zero_stock_appears_in_order_txt(self):
        new_key = db.identity_key_from_values("90x90", 3, "304", "2B")
        db._save_manual_order_state(self.root, {new_key}, {new_key: 6})
        with (
            patch.object(logic, "ensure_tube_database",
                         return_value={"status": "success"}),
            patch.object(logic, "load_config", return_value={}),
            patch.object(db, "get_order_priority_rows", return_value=[]),
            patch.object(db, "summarize_inventory", return_value=[]),
            patch.object(db, "code_catalog_map", return_value={}),
            patch.object(logic, "_get_docx_output_dir_fallback",
                         return_value=self.root),
            patch.object(logic.os, "startfile", create=True),
            patch.object(logic, "VARIABLES_ROOT", self.root),
        ):
            result = logic.generate_tube_order_txt()
        self.assertEqual(result["status"], "success")
        content = Path(result["path"]).read_text(encoding="utf-8-sig")
        self.assertIn("TUBOLARE QUADRO 90x90x3", content)
        self.assertIn("Giacenza attuale: 0 verghe", content)
        self.assertIn("Quantità richiesta: 6 verghe", content)

    def test_ui_has_tab_path_and_two_explicit_actions(self):
        app_root = Path(__file__).resolve().parents[1]
        html = (app_root / "web" / "index.html").read_text(encoding="utf-8")
        script = (app_root / "web" / "script.js").read_text(encoding="utf-8")
        for expected in (
            'id="db-fast-receive-btn"', 'id="db-fast-order-btn"',
            'id="fast-tube-location"', 'id="fast-tube-ripiano"',
            'id="fast-tube-confirm"',
        ):
            self.assertIn(expected, html)
        self.assertIn("register_fast_tube_list(payload)", script)
        self.assertIn("function handleFastTubeKeydown(event)", script)


if __name__ == "__main__":
    import unittest
    unittest.main()
