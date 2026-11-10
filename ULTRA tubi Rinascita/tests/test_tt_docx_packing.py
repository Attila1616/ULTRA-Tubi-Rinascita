"""TT DOCX page packing: keep whole TD groups and print cutting guides.

Run with: python -m unittest tests.test_tt_docx_packing
"""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from docx import Document
from docx.oxml.ns import qn


APP_FILE = Path(__file__).resolve().parents[1] / "tt_docx_generator.pyw"


def _load_generator():
    # The implementation also runs as a stand-alone Windows hotkey program.
    # Its GUI/Explorer integrations are unused while rendering DOCX files.
    keyboard = types.ModuleType("keyboard")
    win32com = types.ModuleType("win32com")
    win32client = types.ModuleType("win32com.client")
    win32com.client = win32client
    runtime_paths = types.ModuleType("runtime_paths")
    runtime_paths.CONFIG_FILE = ""
    stubs = {
        "keyboard": keyboard,
        "win32com": win32com,
        "win32com.client": win32client,
        "runtime_paths": runtime_paths,
    }
    loader = importlib.machinery.SourceFileLoader(
        "tt_docx_generator_test_module", str(APP_FILE)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        loader.exec_module(module)
    return module


def _file(measure="60x60x3", index=0):
    return f"TD123456 aisi 304 tubolare {measure} L315 {index + 1}pz.zzx"


class TestTtDocxPacking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generator = _load_generator()

    def test_multiple_small_td_groups_share_one_docx_section(self):
        doc = Document()
        self.generator.set_a4(doc)
        groups = [
            ("TD100", [_file(index=0)]),
            ("TD200", [_file(measure="50x50x2", index=1)]),
            ("TD300", [_file(measure="80x80x3", index=2)]),
        ]
        self.generator.render_td_groups(doc, "PROD 474", [], groups)

        # The DOCX keeps one section while entire TD groups remain unsplittable.
        self.assertEqual(len(doc.sections), 1)
        self.assertEqual(len(doc.tables), 3)
        self.assertFalse(doc.sections[0].header.paragraphs[0].text)

        for index, outer in enumerate(doc.tables):
            self.assertTrue(
                outer.rows[0]._tr.xpath("./w:trPr/w:cantSplit"),
                f"TD {index} outer row must never split",
            )
            cell = outer.cell(0, 0)
            self.assertIn(groups[index][0], cell.paragraphs[0].text)
            self.assertEqual(len(cell.tables), 1)
            self.assertEqual(len(cell.tables[0].rows), 2)
            self.assertEqual(
                [c.text for c in cell.tables[0].rows[0].cells],
                ["OK", "File", "Qta", "Firma Magazzino"],
            )
            # The visual order must be TD title, measure, then nested table.
            children = list(cell._tc)
            title_index = children.index(cell.paragraphs[0]._p)
            measure_index = children.index(cell.paragraphs[1]._p)
            nested_index = children.index(cell.tables[0]._tbl)
            self.assertLess(title_index, measure_index)
            self.assertLess(measure_index, nested_index)
            self.assertFalse(cell.tables[0]._tbl.tblPr.xpath("./w:tblpPr"))
            column_mm = [col.width.mm for col in cell.tables[0].columns]
            self.assertAlmostEqual(sum(column_mm), 180.0, delta=0.2)
            self.assertGreater(column_mm[1], column_mm[3])
            self.assertGreater(column_mm[3], column_mm[2])
            self.assertGreater(column_mm[2], column_mm[0])

        # Dashed lines are after each group, except the final one.
        for outer in doc.tables[:2]:
            cell = outer.cell(0, 0)
            self.assertTrue(
                cell._tc.xpath(".//w:pBdr/w:bottom[@w:val='dashed']")
            )

    def test_pseudo_nesting_fills_gaps_and_lines_stop_at_page_end(self):
        # Four TDs can share just two sheets. The larger TD is placed
        # beside Taglio instead of wasting its remaining page space.
        taglio = [_file(index=i) for i in range(5)]
        groups = [
            ("TD LARGE", [_file(index=i) for i in range(8)]),
            ("TD MEDIUM", [_file(index=i) for i in range(3)]),
            ("TD SMALL", [_file(index=i) for i in range(2)]),
        ]

        plan = self.generator._plan_td_pages("PROD 474", taglio, groups)
        self.assertEqual(len(plan), 2)
        self.assertEqual(
            [[item["title"] for item in page] for page in plan],
            [
                ["PROD 474: Taglio Principale", "PROD 474: TD LARGE"],
                ["PROD 474: TD MEDIUM", "PROD 474: TD SMALL"],
            ],
        )
        self.assertEqual(
            sum(len(page) for page in plan),
            4,
            "Every TD group must be exported exactly once",
        )

        doc = Document()
        self.generator.set_a4(doc)
        self.generator.render_td_groups(doc, "PROD 474", taglio, groups)
        self.assertEqual(len(doc.tables), 4)
        self.assertEqual(len(doc.sections), 1)
        self.assertEqual(
            len(doc._element.body.xpath(".//w:br[@w:type='page']")),
            1,
            "The two planned pages must have one explicit page break",
        )
        has_line = [
            bool(table.cell(0, 0)._tc.xpath(
                ".//w:pBdr/w:bottom[@w:val='dashed']"
            ))
            for table in doc.tables
        ]
        self.assertEqual(
            has_line, [True, False, True, False],
            "Cut lines must occur only between TDs on the same page",
        )

    def test_height_estimate_uses_actual_minimum_row_height(self):
        short_name = [_file(index=i) for i in range(10)]
        grouped = self.generator.group_files_by_measure(short_name)
        estimated = self.generator._td_group_estimated_height_mm(grouped)
        self.assertLess(
            estimated, 155,
            "Ten fixed 10 mm rows must not be charged 12+ mm each",
        )
        self.assertGreater(
            self.generator.MAX_SINGLE_PAGE_TD_MM, 250.0,
            "Do not waste 32 mm of printable A4 space",
        )

    def test_single_group_or_oversized_page_never_has_trailing_cut_line(self):
        large = [_file(index=i) for i in range(40)]
        other = [_file(index=0)]

        doc = Document()
        self.generator.set_a4(doc)
        self.generator.render_td_groups(
            doc, "PROD 474", large, [("TD SMALL", other)]
        )
        pages = self.generator._plan_td_pages(
            "PROD 474", large, [("TD SMALL", other)]
        )
        self.assertEqual([len(page) for page in pages], [1, 1])
        self.assertEqual(len(doc.tables), 2)
        self.assertFalse(
            doc._element.body.xpath(".//w:pBdr/w:bottom[@w:val='dashed']")
        )
        self.assertEqual(
            len(doc._element.body.xpath(".//w:br[@w:type='page']")),
            1,
        )

    def test_large_td_group_is_not_wrapped_in_unsplittable_container(self):
        doc = Document()
        self.generator.set_a4(doc)
        large = [_file(index=i) for i in range(40)]
        self.generator.render_td_groups(
            doc, "PROD 474", [], [("TD BIG", large)]
        )
        self.assertEqual(len(doc.tables), 1)
        self.assertEqual(len(doc.tables[0].rows), 41)
        self.assertIn("TD BIG", doc.paragraphs[0].text)

    def test_auto_save_uses_packed_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "PROD 474"
            taglio = root / "TT" / "Tubolare 60x60"
            td = root / "TT" / "TD1801A00096"
            taglio.mkdir(parents=True)
            td.mkdir(parents=True)
            (taglio / _file(index=0)).write_bytes(b"")
            (td / _file(measure="80x80x3", index=1)).write_bytes(b"")

            ok, path = self.generator.generate_docx_for_prod_auto(
                str(root), output_dir=str(root)
            )
            self.assertTrue(ok, path)
            document = Document(path)
            self.assertEqual(len(document.sections), 1)
            self.assertEqual(len(document.tables), 2)
            self.assertIn(
                "PROD 474: Taglio Principale",
                document.tables[0].cell(0, 0).paragraphs[0].text,
            )
            self.assertIn(
                "PROD 474: TD1801A00096",
                document.tables[1].cell(0, 0).paragraphs[0].text,
            )


if __name__ == "__main__":
    unittest.main()
