"""Regression coverage for IGS/ZZX quantity matches and safe repair layout."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend_logic as logic


def put(path, content="geometry-placeholder"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class IgsMatchingRepairTests(unittest.TestCase):
    def test_quantity_difference_both_directions_not_missing(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD 99" / "TT"
            first = put(folder / "IGS" / "TD001 304 100x50x3 L600 1pz.igs")
            second = put(folder / "IGS" / "TD002 316 Ø60x2 L820 10pz.iges")
            put(folder / "TD001 304 100x50x3 L600 10pz.zzx")
            put(folder / "TD002 316 Ø60x2 L820 1pz.zzx")
            rows = logic.find_unmatched_igs_files(home)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["type"] for row in rows}, {"quantity_mismatch"})
            self.assertEqual(
                {row["filePath"] for row in rows},
                {str(first), str(second)},
            )
            self.assertEqual(
                sorted(row["matchedZzxName"] for row in rows),
                ["TD001 304 100x50x3 L600 10pz.zzx", "TD002 316 Ø60x2 L820 1pz.zzx"],
            )

    def test_other_name_differences_and_same_name_matches(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            put(folder / "IGS" / "ABC 1pz.igs")
            put(folder / "ABC 1pz.zzx")
            put(folder / "IGS" / "DEF L501 1pz.igs")
            put(folder / "DEF L500 10pz.zzx")
            put(folder / "IGS" / "ZZZ 1pz.igs")
            rows = logic.find_unmatched_igs_files(home)
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["type"] for row in rows}, {"missing"})
            self.assertEqual(
                {row["fileName"] for row in rows},
                {"DEF L501 1pz.igs", "ZZZ 1pz.igs"},
            )

    def test_never_match_other_product_folders(self):
        with tempfile.TemporaryDirectory() as home:
            put(Path(home) / "P1" / "IGS" / "T 1pz.igs")
            put(Path(home) / "P2" / "T 10pz.zzx")
            rows = logic.find_unmatched_igs_files(home)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["type"], "missing")

    @staticmethod
    def fake_batch(paths, overwrite=False, output_paths=None):
        results = []
        for source, output in zip(paths, output_paths or []):
            put(output, "zzx-placeholder")
            results.append({"status": "success", "sourcePath": source, "outputPath": output})
        return {"status": "success", "results": results}

    def test_converts_existing_igs_folder_to_parent(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            source = put(folder / "IGS" / "PART 1pz.igs")
            with patch.object(logic, "convert_igs_files_to_zzx", side_effect=self.fake_batch) as batch:
                result = logic.convert_missing_igs_files_to_zzx(home)
            self.assertEqual(result["converted"], 1)
            self.assertTrue((folder / "PART 1pz.zzx").exists())
            self.assertTrue(source.exists())
            batch.assert_called_once()

    def test_moves_loose_igs_then_converts_and_does_not_touch_neighbors(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            source = put(folder / "PART L500 1pz.igs")
            neighbor = put(folder / "notes.txt", "keep me")
            with patch.object(logic, "convert_igs_files_to_zzx", side_effect=self.fake_batch):
                result = logic.convert_missing_igs_files_to_zzx(home, [str(source)])
            self.assertEqual(result["converted"], 1)
            self.assertFalse(source.exists())
            self.assertTrue((folder / "IGS" / source.name).exists())
            self.assertTrue((folder / "PART L500 1pz.zzx").exists())
            self.assertEqual(neighbor.read_text(encoding="utf-8"), "keep me")

    def test_failed_conversion_restores_original_source(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            source = put(folder / "PART 1pz.igs")
            failed = {"status": "success", "results": [
                {"status": "error", "message": "CAD parse failed"},
            ]}
            with patch.object(logic, "convert_igs_files_to_zzx", return_value=failed):
                result = logic.convert_missing_igs_files_to_zzx(home)
            self.assertEqual(result["errors"], 1)
            self.assertTrue(source.exists())
            self.assertFalse((folder / "IGS" / source.name).exists())
            self.assertFalse((folder / "PART 1pz.zzx").exists())
            self.assertTrue(result["results"][0]["sourceRestored"])

    def test_quantity_difference_is_never_auto_converted(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            source = put(folder / "IGS" / "PART 1pz.igs")
            put(folder / "PART 10pz.zzx")
            with patch.object(logic, "convert_igs_files_to_zzx") as batch:
                result = logic.convert_missing_igs_files_to_zzx(home)
            self.assertEqual(result["converted"], 0)
            self.assertEqual(result["results"], [])
            self.assertTrue(source.exists())
            batch.assert_not_called()

    def test_refuses_unverified_path_without_modifying_anything(self):
        with tempfile.TemporaryDirectory() as home:
            source = put(Path(home) / "PROD" / "IGS" / "PART 1pz.igs")
            outside = put(Path(tempfile.gettempdir()) / "ultra-not-in-work-tree.igs")
            try:
                result = logic.convert_missing_igs_files_to_zzx(home, [str(source), str(outside)])
                self.assertEqual(result["status"], "error")
                self.assertTrue(source.exists())
                self.assertTrue(outside.exists())
            finally:
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
