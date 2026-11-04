"""Manually requested purchase tubes, preserving live stock and reorder rules."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import backend_logic as logic
import tube_database as db


def tube(misura, spessore, materiale="304", finitura="2B"):
    return {
        "misura": misura,
        "spessore": spessore,
        "materiale": materiale,
        "finitura": finitura,
    }


def row(misura, spessore, codice, quantita, priority="media"):
    data = tube(misura, spessore)
    data.update({
        "codice": codice,
        "forma": db.classify_tube(misura)[0],
        "quantitaTotale": quantita,
        "priorita": priority,
    })
    return data


class ManualOrderPersistenceTests(TestCase):
    def test_bulk_selection_is_persistent_unique_and_reversible(self):
        a = tube("50x50", 2)
        b = tube("60.3", 2, "316")
        key_a, key_b = db.identity_key_from_tube(a), db.identity_key_from_tube(b)
        with TemporaryDirectory() as root, patch.object(
            db, "_CONFIGURED_DATABASE_DIR", None
        ), patch.object(
            db, "iter_position_tubes", return_value=[
                ("M1", "scaffale", "1", a),
                ("M2", "terra", "2", a),
                ("M1", "scaffale", "1", b),
            ]
        ):
            first = db.set_manual_order_tubes(root, {
                "operation": "add", "keys": [key_a, key_a, key_b]
            })
            self.assertEqual(first["status"], "success")
            self.assertEqual(first["changed"], 2)
            self.assertEqual(db.load_manual_order_keys(root), {key_a, key_b})
            self.assertTrue(Path(db.manual_order_path(root)).is_file())
            repeat = db.set_manual_order_tubes(
                root, {"operation": "add", "keys": [key_a, key_b]}
            )
            self.assertEqual(repeat["changed"], 0)
            self.assertEqual(repeat["selected_count"], 2)
            removed = db.set_manual_order_tubes(
                root, {"operation": "remove", "keys": [key_a]}
            )
            self.assertEqual(removed["changed"], 1)
            self.assertEqual(db.load_manual_order_keys(root), {key_b})

    def test_invalid_selection_does_not_change_existing_orders(self):
        a = tube("40x40", 2)
        key = db.identity_key_from_tube(a)
        with TemporaryDirectory() as root, patch.object(
            db, "_CONFIGURED_DATABASE_DIR", None
        ), patch.object(db, "iter_position_tubes", return_value=[
            ("A", "scaffale", "B", a)
        ]):
            self.assertEqual(db.set_manual_order_tubes(root, {
                "keys": [key],
            })["status"], "success")
            rejected = db.set_manual_order_tubes(root, {
                "keys": [db.identity_key_from_tube(tube("99x99", 4))]
            })
            self.assertEqual(rejected["status"], "error")
            self.assertEqual(db.load_manual_order_keys(root), {key})


class OrderTxtTests(TestCase):
    def test_manual_and_automatic_tubes_are_not_duplicated_and_show_live_quantity(self):
        auto = row("50x50", 2, "TA0001A00001", 1, "media")
        manual = row("60x60", 3, "TA0002A00002", 17, "media")
        no_code = row("80x40", 2, "", 9, "")
        manually_selected = {
            db.identity_key_from_tube(manual),
            db.identity_key_from_tube(auto),
            db.identity_key_from_tube(no_code),
        }
        with (
            TemporaryDirectory() as outdir,
            patch.object(logic, "ensure_tube_database",
                         return_value={"status": "success"}),
            patch.object(logic, "load_config", return_value={
                "order_priority_low_quantity": 1,
                "order_priority_medium_quantity": 3,
                "order_priority_high_quantity": 5,
                "order_include_low_priority": True,
            }),
            patch.object(db, "get_order_priority_rows",
                         return_value=[auto, manual]),
            patch.object(db, "load_manual_order_keys",
                         return_value=manually_selected),
            patch.object(db, "summarize_inventory",
                         return_value=[auto, manual, no_code]),
            patch.object(logic, "_get_docx_output_dir_fallback",
                         return_value=outdir),
            patch.object(logic.os, "startfile", create=True),
        ):
            result = logic.generate_tube_order_txt()
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["rows_count"], 3)
            content = Path(result["path"]).read_text(encoding="utf-8-sig")
            self.assertEqual(content.count("TA0001A00001"), 1)
            self.assertEqual(content.count("TA0002A00002"), 1)
            self.assertIn("Giacenza attuale: 1 verghe", content)
            self.assertIn("Giacenza attuale: 17 verghe", content)
            self.assertIn("Giacenza attuale: 9 verghe", content)
            self.assertIn("CODICE MANCANTE AISI 304", content)
            self.assertIn(
                "TA0002A00002 AISI 304 TUBOLARE QUADRO 60x60x3\n"
                "Giacenza attuale: 17 verghe",
                content,
            )

    def test_only_low_stock_auto_orders_still_work(self):
        auto = row("50x50", 2, "TA0001A00001", 2, "media")
        too_much_stock = row("60x60", 2, "TA0002A00002", 20, "media")
        with (
            TemporaryDirectory() as outdir,
            patch.object(logic, "ensure_tube_database",
                         return_value={"status": "success"}),
            patch.object(logic, "load_config", return_value={}),
            patch.object(db, "get_order_priority_rows",
                         return_value=[auto, too_much_stock]),
            patch.object(db, "load_manual_order_keys", return_value=set()),
            patch.object(db, "summarize_inventory",
                         return_value=[auto, too_much_stock]),
            patch.object(logic, "_get_docx_output_dir_fallback",
                         return_value=outdir),
            patch.object(logic.os, "startfile", create=True),
        ):
            result = logic.generate_tube_order_txt()
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["rows_count"], 1)
            content = Path(result["path"]).read_text(encoding="utf-8-sig")
            self.assertIn("TA0001A00001", content)
            self.assertNotIn("TA0002A00002", content)


if __name__ == "__main__":
    import unittest
    unittest.main()
