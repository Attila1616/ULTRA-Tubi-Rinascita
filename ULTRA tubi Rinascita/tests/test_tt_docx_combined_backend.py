"""Integration tests for regular and combined cross-PROD TT export actions."""
import types
import unittest
from unittest.mock import Mock, patch

import backend_logic as logic


class CombinedTtExportApiTests(unittest.TestCase):
    def test_regular_multi_selection_generates_individuals_and_combined(self):
        individual = Mock(side_effect=[
            (True, "out/PROD 100 Lista TT.docx"),
            (True, "out/PROD 200 Lista TT.docx"),
        ])
        combined = Mock(return_value=(True, "out/Lista TT COMBINATA.docx"))
        module = types.SimpleNamespace(
            get_selected_folders=lambda: ["PROD 100", "PROD 200"],
            generate_docx_for_prod_auto=individual,
            generate_combined_docx_for_prods=combined,
        )
        with (
            patch.object(logic, "_load_tt_docx_module", return_value=module),
            patch.object(logic, "_get_docx_output_dir_fallback", return_value="out"),
        ):
            result = logic.generate_tt_docx_from_explorer_selection("work")

        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["generated"]), 2)
        self.assertEqual(result["combined"], "out/Lista TT COMBINATA.docx")
        self.assertEqual(individual.call_count, 2)
        combined.assert_called_once_with(
            ["PROD 100", "PROD 200"], output_dir="out",
        )

    def test_combined_only_does_not_generate_individuals(self):
        individual = Mock(side_effect=AssertionError("Individual export unwanted"))
        combined = Mock(return_value=(True, "out/Lista TT COMBINATA.docx"))
        module = types.SimpleNamespace(
            get_selected_folders=lambda: ["PROD 100", "PROD 200"],
            generate_docx_for_prod_auto=individual,
            generate_combined_docx_for_prods=combined,
        )
        with (
            patch.object(logic, "_load_tt_docx_module", return_value=module),
            patch.object(logic, "_get_docx_output_dir_fallback", return_value="out"),
        ):
            result = logic.generate_tt_docx_from_explorer_selection(
                "work", combined_only=True,
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["generated"], [])
        self.assertEqual(result["combined"], "out/Lista TT COMBINATA.docx")
        individual.assert_not_called()

    def test_one_prod_only_creates_original_individual_file(self):
        combined = Mock(side_effect=AssertionError("Should not combine one PROD"))
        module = types.SimpleNamespace(
            get_selected_folders=lambda: ["PROD 100"],
            generate_docx_for_prod_auto=lambda *_args, **_kw: (
                True, "out/PROD 100 Lista TT.docx",
            ),
            generate_combined_docx_for_prods=combined,
        )
        with (
            patch.object(logic, "_load_tt_docx_module", return_value=module),
            patch.object(logic, "_get_docx_output_dir_fallback", return_value="out"),
        ):
            result = logic.generate_tt_docx_from_explorer_selection("work")
        self.assertEqual(result["status"], "success")
        self.assertIsNone(result["combined"])
        combined.assert_not_called()

    def test_new_ui_action_is_wired(self):
        from pathlib import Path

        app_root = Path(__file__).resolve().parents[1]
        html = (app_root / "web" / "index.html").read_text(encoding="utf-8")
        script = (app_root / "web" / "script.js").read_text(encoding="utf-8")
        self.assertIn('id="tt-docx-merged-btn"', html)
        self.assertIn("generateTTDocxFromSelection(true)", script)
        self.assertIn("resp.combined", script)


if __name__ == "__main__":
    unittest.main()
