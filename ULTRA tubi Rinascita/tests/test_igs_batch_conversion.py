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
