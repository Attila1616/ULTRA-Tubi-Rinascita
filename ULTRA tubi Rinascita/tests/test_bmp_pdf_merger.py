"""BMP printout merging by global hotkey; no physical BMP files are modified."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import re

from PIL import Image

from bmp_pdf_hotkey import BmpPdfHotkey, normalize_bmp_hotkey
from bmp_pdf_merger import (
    get_selected_bmp_files,
    merge_selected_bmps,
    plan_bmp_groups,
)


def bmp(path, color=(10, 80, 190), size=(60, 90)):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    image.save(path, format="BMP")
    return path


def pdf_pages(path):
    # ReportLab writes a /Type /Page per page; /Pages is excluded by \b.
    return len(re.findall(rb"/Type\s*/Page\b", path.read_bytes()))


class BmpPdfMergeTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_multiple_prod_groups_numeric_order_and_originals_untouched(self):
        folder = self.root / "FOGLI STAMPATI"
        names = [
            "PROD.498.4.BMP", "PROD.498.1.BMP", "PROD.498.3.BMP",
            "PROD.498.2.BMP", "PROD.508.1.BMP", "PRODM.57-SCALA.1.BMP",
        ]
        paths = [
            bmp(folder / name, color=(index * 27, index * 8, 90))
            for index, name in enumerate(names)
        ]
        original_bytes = [p.read_bytes() for p in paths]
        plan, problems, skipped = plan_bmp_groups(reversed(paths))
        self.assertEqual(problems, [])
        self.assertEqual(skipped, [])
        prod_498 = next(group for group in plan if group["name"] == "PROD.498")
        self.assertEqual(prod_498["pages"], [1, 2, 3, 4])

        result = merge_selected_bmps(reversed(paths))
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["created"]), 3)
        self.assertEqual(pdf_pages(folder / "PROD.498.pdf"), 4)
        self.assertEqual(pdf_pages(folder / "PROD.508.pdf"), 1)
        self.assertEqual(pdf_pages(folder / "PRODM.57-SCALA.pdf"), 1)
        self.assertEqual(original_bytes, [p.read_bytes() for p in paths])

    def test_no_overwrite_and_same_prod_in_different_folders(self):
        first = bmp(self.root / "one" / "PROD.498.1.BMP")
        second = bmp(self.root / "two" / "PROD.498.1.BMP")
        output = self.root / "one" / "PROD.498.pdf"
        output.write_bytes(b"untouched")
        result = merge_selected_bmps([second, first])
        self.assertEqual(result["status"], "success")
        self.assertEqual(output.read_bytes(), b"untouched")
        self.assertTrue((self.root / "one" / "PROD.498 (2).pdf").is_file())
        self.assertTrue((self.root / "two" / "PROD.498.pdf").is_file())

    def test_missing_page_rejects_entire_prod(self):
        first = bmp(self.root / "PROD.498.1.BMP")
        third = bmp(self.root / "PROD.498.3.BMP")
        result = merge_selected_bmps([third, first])
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(result["created"]), 0)
        self.assertIn("2", result["errors"][0]["reason"])
        self.assertFalse((self.root / "PROD.498.pdf").exists())

    def test_duplicated_page_number_rejects_group(self):
        one = bmp(self.root / "PROD.498.1.BMP")
        one_alias = bmp(self.root / "PROD.498.01.BMP")
        # Leading zero is deliberately not a canonical page number.
        groups, errs, ignored = plan_bmp_groups([one, one_alias])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(errs), 0)
        self.assertEqual(len(ignored), 1)

    def test_invalid_bmp_does_not_leave_partial_pdf(self):
        first = bmp(self.root / "PROD.498.1.BMP")
        bad = self.root / "PROD.498.2.BMP"
        bad.write_text("not a bitmap", encoding="utf8")
        result = merge_selected_bmps([bad, first])
        self.assertEqual(result["status"], "error")
        self.assertFalse((self.root / "PROD.498.pdf").exists())
        self.assertEqual(list(self.root.glob(".tubenest-bmp-*")), [])

    def test_unnumbered_bitmap_is_ignored_not_mismerged(self):
        good = bmp(self.root / "PROD.498.1.BMP")
        strange = bmp(self.root / "PROD.498.BMP")
        result = merge_selected_bmps([strange, good])
        self.assertEqual(result["status"], "partial")
        self.assertTrue((self.root / "PROD.498.pdf").is_file())
        self.assertEqual(len(result["skipped"]), 1)


class FakeItem:
    def __init__(self, path):
        self.Path = str(path)


class FakeSelection:
    def __init__(self, paths):
        self.paths = paths
        self.Count = len(paths)

    def Item(self, index):
        return FakeItem(self.paths[index])


class FakeWindow:
    def __init__(self, hwnd, paths):
        self.HWND = hwnd
        self.FullName = r"C:\Windows\explorer.exe"
        self.Document = type("Doc", (), {"SelectedItems": lambda self_: FakeSelection(paths)})()


class FakeShell:
    def __init__(self, windows):
        self.windows = windows

    def Windows(self):
        return self.windows


class ActiveExplorerTests(TestCase):
    def test_hotkey_does_not_merge_background_explorer_selection(self):
        with TemporaryDirectory() as root:
            base = Path(root)
            wrong = bmp(base / "PROD.100.1.BMP")
            correct = bmp(base / "PROD.498.1.BMP")
            shell = FakeShell([
                FakeWindow(22, [wrong]),
                FakeWindow(33, [correct]),
            ])
            self.assertEqual(
                get_selected_bmp_files(shell=shell, foreground_hwnd=33),
                [str(correct)],
            )
            self.assertEqual(
                get_selected_bmp_files(shell=shell, foreground_hwnd=44),
                [],
            )


class FakeKeyboard:
    def __init__(self):
        self.active = {}
        self.counter = 0

    def add_hotkey(self, key, callback, **_kwargs):
        self.counter += 1
        self.active[self.counter] = (key, callback)
        return self.counter

    def remove_hotkey(self, handle):
        del self.active[handle]


class HotkeySettingsTests(TestCase):
    def test_hotkey_defaults_and_safety(self):
        self.assertEqual(normalize_bmp_hotkey("Ctrl+Alt+B"), "ctrl+alt+b")
        self.assertEqual(normalize_bmp_hotkey("SHIFT + ALT + F4"), "alt+shift+f4")
        for value in ("", "b", "ctrl+b", "win+b", "ctrl+alt", "ctrl+ctrl+b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_bmp_hotkey(value)

    def test_register_reconfigure_and_stop(self):
        backend = FakeKeyboard()
        hotkey = BmpPdfHotkey(keyboard_backend=backend)
        self.assertEqual(hotkey.configure("ctrl+alt+b"), "ctrl+alt+b")
        self.assertEqual(len(backend.active), 1)
        self.assertEqual(hotkey.configure("alt+shift+m"), "alt+shift+m")
        self.assertEqual(
            [value[0] for value in backend.active.values()],
            ["alt+shift+m"],
        )
        hotkey.stop()
        self.assertEqual(backend.active, {})


if __name__ == "__main__":
    import unittest
    unittest.main()
