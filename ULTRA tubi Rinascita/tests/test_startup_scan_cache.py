"""Regression tests for faster ULTRA startup and strict AISI extraction."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import backend_logic as logic
from startup_index import StartupZzxIndex


class AisiMaterialTests(unittest.TestCase):
    def test_grade_does_not_come_from_td_drawing_number(self):
        cases = [
            ("TD9903A01316 aisi 304 tubo Ø154x2 L800 4pz.zzx", "304"),
            ("TD1702A00386 AISI304 tubo Ø154x2 L550 1pz.zzx", "304"),
            ("TD12345 AISI 316 tubo 50x50x2 L200 1pz.zzx", "316"),
            ("TD12345 aisi316 tubo 50x50x2 L200 1pz.zzx", "316"),
            ("TD12345 AISI 430 tubo 50x50x2 L200 1pz.zzx", "430"),
            ("TD9903A01316 tubo Ø154x2 L800 4pz.zzx", "UNKNOWN"),
            ("TD12345 316 tubo 50x50x2 L200 1pz.zzx", "UNKNOWN"),
        ]
        for filename, expected in cases:
            with self.subTest(filename=filename):
                size, grade, finish = logic.extract_tube_info(filename)
                self.assertEqual(grade, expected)
                self.assertNotEqual(size, "UNKNOWN_SIZE")
                self.assertEqual(finish, "2B")

    def test_aisi_grade_does_not_match_embedded_number(self):
        for filename in (
            "TD2016316 aisi 3160 tubo 50x50x2 L200 1pz.zzx",
            "TD2016316 aisi 1304 tubo 50x50x2 L200 1pz.zzx",
        ):
            self.assertEqual(logic.extract_tube_info(filename)[1], "UNKNOWN")


class PersistentStartupMetadataTests(unittest.TestCase):
    def test_cache_survives_restart_and_reparses_changed_file(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / "TD9903A01316 aisi304 tubo Ø154x2 L800 4pz.zzx"
            f.write_bytes(b"old")
            cache_file = Path(root) / "startup_index.json.gz"
            calls = []

            def parse():
                calls.append("parse")
                return {"status": "ok", "document": {"size": f.stat().st_size}}

            idx = StartupZzxIndex(cache_file, parser_revision="v1")
            self.assertEqual(idx.get(f, "geometry", parse)["status"], "ok")
            self.assertEqual(idx.get(f, "geometry", parse)["status"], "ok")
            self.assertEqual(calls, ["parse"])
            idx.save()
            self.assertTrue(cache_file.is_file())

            reopened = StartupZzxIndex(cache_file, parser_revision="v1")
            reopened.get(f, "geometry", parse)
            self.assertEqual(calls, ["parse"], "Second launch should use cache")
            reopened.get(f, "validation", lambda: [])
            reopened.save()

            new_revision = StartupZzxIndex(cache_file, parser_revision="v2")
            new_revision.get(f, "geometry", parse)
            self.assertEqual(len(calls), 2, "Changed parser must invalidate cache")

            f.write_bytes(b"updated-content")
            reopened.get(f, "geometry", parse)
            self.assertEqual(len(calls), 3, "Changed ZZX must be parsed again")

            f.rename(Path(root) / "renamed.zzx")
            reopened.prune_to_paths([])
            reopened.save()
            reset = StartupZzxIndex(cache_file, parser_revision="v1")
            self.assertEqual(len(reset._entries), 0)

    def test_transient_parser_errors_are_not_persisted(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / "example.zzx"
            f.write_bytes(b"x")
            idx = StartupZzxIndex(Path(root) / "cache.gz", parser_revision="v1")
            calls = []

            def parse():
                calls.append(True)
                if len(calls) == 1:
                    return {"status": "error", "error": "temporary"}
                return {"status": "ok", "document": {"test": 1}}

            self.assertEqual(idx.get(f, "geometry", parse)["status"], "error")
            self.assertEqual(idx.get(f, "geometry", parse)["status"], "ok")
            self.assertEqual(len(calls), 2)


class UnifiedStartupScanTests(unittest.TestCase):
    def test_igs_and_zzx_are_collected_once_with_fatto_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            work = p / "PROD 400" / "TT"
            archived = work / "fatto"
            src = work / "IGS"
            src.mkdir(parents=True)
            archived.mkdir(parents=True)
            zzx = archived / "TD1 aisi304 tubo 50x50x2 L100 1pz.zzx"
            zzx.write_bytes(b"test")
            igs = src / "TD1 aisi304 tubo 50x50x2 L100 1pz.igs"
            igs.write_bytes(b"test")
            index = logic.scan_work_files(root)
            self.assertEqual(index["zzx"], [str(zzx)])
            self.assertEqual(index["igs"], [str(igs)])

            # Finished drawings still count as existing for missing-IGS
            # warnings. This must remain true after the scan refactor.
            self.assertEqual(
                logic.find_unmatched_igs_files(root, file_index=index),
                [],
            )

    def test_folders_are_not_scanned_again_when_index_is_supplied(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root)
            f = p / "PROD 1" / "TT" / "TD1 aisi 304 tubo 40x40x2 L100 1pz.zzx"
            f.parent.mkdir(parents=True)
            f.write_bytes(b"fake")
            index = logic.scan_work_files(root)
            with (
                patch.object(logic, "load_config", return_value={"ignore_folders": []}),
                patch.object(logic, "_startup_metadata_cache") as cached,
                patch.object(logic.tubenest_engine, "describe_zzx", return_value={
                    "status": "unsupported", "error": "fake ZZX",
                }),
                patch.object(logic.os, "walk", side_effect=AssertionError("Repeated walk")),
            ):
                cached.return_value.get.side_effect = lambda _path, field, cb: cb()
                state = logic.get_current_state(root, file_index=index)
                self.assertEqual(sum(len(g["pieces"]) for g in state), 1)
                self.assertEqual(state[0]["tubeType"], "40x40x2 304 2B")
                self.assertEqual(
                    logic.find_unmatched_igs_files(root, file_index=index), []
                )


if __name__ == "__main__":
    unittest.main()
