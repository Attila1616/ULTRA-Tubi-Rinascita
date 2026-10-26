import os
import tempfile
import unittest
from unittest.mock import patch

import backend_logic


class IgsBatchConversionTests(unittest.TestCase):
    def test_worker_count_uses_multiple_cores_but_is_capped(self):
        with patch.object(
            backend_logic.os,
            "cpu_count",
            return_value=24,
        ):
            self.assertEqual(
                backend_logic._igs_batch_worker_count(1),
                1,
            )
            self.assertEqual(
                backend_logic._igs_batch_worker_count(4),
                4,
            )
            self.assertEqual(
                backend_logic._igs_batch_worker_count(50),
                8,
            )

    def test_folder_scan_finds_igs_recursively_and_ignores_other_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = os.path.join(temp_dir, "PROD", "TT", "pezzo")
            os.makedirs(nested, exist_ok=True)

            expected = [
                os.path.join(temp_dir, "root.igs"),
                os.path.join(nested, "part_a.IGS"),
                os.path.join(nested, "part_b.iges"),
            ]
            for path in expected:
                with open(path, "w", encoding="ascii") as handle:
                    handle.write("dummy")

            with open(
                os.path.join(nested, "already.zzx"),
                "w",
                encoding="ascii",
            ) as handle:
                handle.write("dummy")
            with open(
                os.path.join(nested, "notes.txt"),
                "w",
                encoding="ascii",
            ) as handle:
                handle.write("dummy")

            result = backend_logic.find_igs_files_in_folder(
                temp_dir
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 3)
        self.assertEqual(
            sorted(
                os.path.normcase(path)
                for path in result["paths"]
            ),
            sorted(
                os.path.normcase(path)
                for path in expected
            ),
        )

    def test_folder_scan_rejects_missing_folder(self):
        result = backend_logic.find_igs_files_in_folder(
            os.path.join(
                tempfile.gettempdir(),
                "ultra-folder-that-does-not-exist-123456",
            )
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["paths"], [])
        self.assertEqual(result["count"], 0)

    def test_single_file_batch_uses_serial_worker(self):
        fake_result = {
            "status": "success",
            "sourcePath": "A.igs",
            "outputPath": "A.zzx",
        }
        with patch.object(
            backend_logic,
            "_convert_igs_worker",
            return_value=fake_result,
        ) as worker:
            result = backend_logic.convert_igs_files_to_zzx(
                ["A.igs"],
                overwrite=True,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["workersUsed"], 1)
        self.assertEqual(
            result["results"][0]["outputPath"],
            "A.zzx",
        )
        worker.assert_called_once_with(
            ("A.igs", True)
        )


if __name__ == "__main__":
    unittest.main()
