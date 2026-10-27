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

    def test_completed_zzx_in_fatto_matches_active_igs(self):
        with tempfile.TemporaryDirectory() as home:
            tube = Path(home) / "PROD 123" / "TT" / "TD1905A00318" / "tubolare 50x50x2"
            igs = put(tube / "IGS" / "TD1905A00318 L250 10pz.igs")
            completed = put(tube / "fatto" / "TD1905A00318 L250 10pz (fatti 10).zzx")
            self.assertEqual(logic.find_unmatched_igs_files(home), [])
            self.assertTrue(igs.exists())
            self.assertTrue(completed.exists())

    def test_partially_completed_zzx_in_fatto_also_matches(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            put(folder / "IGS" / "PART L500 10pz.iges")
            put(folder / "FATTO" / "PART L500 10pz (FATTI 3).ZZX")
            self.assertEqual(logic.find_unmatched_igs_files(home), [])

    def test_archived_nested_fatto_paths_are_indexed(self):
        with tempfile.TemporaryDirectory() as home:
            job = Path(home) / "PROD" / "TT"
            put(job / "TD123" / "tubolare" / "IGS" / "PART L450 10pz.igs")
            put(job / "fatto" / "TD123" / "tubolare" / "fatto"
                / "PART L450 10pz (fatti 10).zzx")
            self.assertEqual(logic.find_unmatched_igs_files(home), [])

    def test_archived_zzx_quantity_difference_remains_information_only(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            put(folder / "IGS" / "TD001 L600 1pz.igs")
            completed = put(folder / "fatto" / "TD001 L600 10pz (fatti 10).zzx")
            issues = logic.find_unmatched_igs_files(home)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["type"], "quantity_mismatch")
            self.assertEqual(issues[0]["matchedZzxPath"], str(completed))

    def test_completed_zzx_in_another_prod_must_not_match(self):
        with tempfile.TemporaryDirectory() as home:
            put(Path(home) / "PROD A" / "TT" / "IGS" / "PART 1pz.igs")
            put(Path(home) / "PROD B" / "TT" / "fatto"
                / "PART 1pz (fatti 1).zzx")
            issues = logic.find_unmatched_igs_files(home)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["type"], "missing")

    def test_genuinely_missing_zzx_still_reported_next_to_archived_zzx(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            put(folder / "IGS" / "HAS MATCH 1pz.igs")
            put(folder / "IGS" / "ACTUALLY MISSING 1pz.igs")
            put(folder / "fatto" / "HAS MATCH 1pz (fatti 1).zzx")
            issues = logic.find_unmatched_igs_files(home)
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0]["fileName"], "ACTUALLY MISSING 1pz.igs")
            self.assertEqual(issues[0]["type"], "missing")

    def test_archived_igs_are_never_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            put(folder / "fatto" / "TD123" / "IGS" / "ARCHIVED 1pz.igs")
            put(Path(home) / "PROD2" / "TT - Fatto" / "IGS" / "FINISHED 1pz.iges")
            self.assertEqual(logic.find_unmatched_igs_files(home), [])

    def test_completed_zzx_does_not_trigger_bulk_reconversion(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            source = put(folder / "IGS" / "PART 10pz.igs")
            put(folder / "fatto" / "PART 10pz (fatti 10).zzx")
            with patch.object(logic, "convert_igs_files_to_zzx") as converter:
                result = logic.convert_missing_igs_files_to_zzx(home)
            self.assertEqual(result["converted"], 0)
            self.assertEqual(result["results"], [])
            self.assertTrue(source.exists())
            converter.assert_not_called()

    def test_archive_check_accepts_matching_completed_zzx(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            put(folder / "IGS" / "PART 2pz.igs")
            put(folder / "fatto" / "PART 2pz (fatti 2).zzx")
            self.assertTrue(logic._is_folder_completed(str(folder), home))
            put(folder / "IGS" / "MISSING 1pz.igs")
            self.assertFalse(logic._is_folder_completed(str(folder), home))

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

    def test_manual_conversion_skips_existing_without_relocation(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD" / "TT"
            source = put(folder / "PART 1pz.igs")
            old_zzx = put(folder / "PART 1pz.zzx", "keep-existing")
            with patch.object(logic, "convert_igs_files_to_zzx") as batch:
                result = logic.convert_selected_igs_files_to_zzx([str(source)])
            self.assertEqual(result["existing"], 1)
            self.assertTrue(source.exists())
            self.assertEqual(old_zzx.read_text(encoding="utf-8"), "keep-existing")
            self.assertFalse((folder / "IGS").exists())
            batch.assert_not_called()

    def test_manual_conversion_preserves_selection_result_order(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD"
            exists = put(folder / "FIRST 1pz.igs")
            put(folder / "FIRST 1pz.zzx", "do-not-touch")
            missing = put(folder / "SECOND 1pz.igs")
            with patch.object(logic, "convert_igs_files_to_zzx", side_effect=self.fake_batch):
                result = logic.convert_selected_igs_files_to_zzx(
                    [str(exists), str(missing)]
                )
            self.assertEqual(
                [row["status"] for row in result["results"]],
                ["exists", "success"],
            )
            self.assertEqual(result["converted"], 1)
            self.assertTrue((folder / "IGS" / "SECOND 1pz.igs").exists())

    def test_explicit_manual_overwrite_uses_requested_target(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / "PROD"
            source = put(folder / "IGS" / "SAMPLE 1pz.igs")
            target = put(folder / "SAMPLE 1pz.zzx", "old")
            with patch.object(logic, "convert_igs_files_to_zzx", side_effect=self.fake_batch) as batch:
                result = logic.convert_selected_igs_files_to_zzx(
                    [str(source)], overwrite=True
                )
            self.assertEqual(result["converted"], 1)
            self.assertEqual(target.read_text(encoding="utf-8"), "zzx-placeholder")
            self.assertTrue(batch.call_args.kwargs["overwrite"])

    def test_refuses_unverified_path_without_modifying_anything(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as other:
            source = put(Path(home) / "PROD" / "IGS" / "PART 1pz.igs")
            outside = put(Path(other) / "ultra-not-in-work-tree.igs")
            result = logic.convert_missing_igs_files_to_zzx(home, [str(source), str(outside)])
            self.assertEqual(result["status"], "error")
            self.assertTrue(source.exists())
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
