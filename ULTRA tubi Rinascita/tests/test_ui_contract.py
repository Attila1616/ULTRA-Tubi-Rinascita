"""Light-interface regression contract; run with python -m unittest discover -s tests.

Intentionally no GUI or Python backend dependencies: verifies that UI upgrades
keep the existing JS bridge hooks, interactive elements and original IDs.
"""
from pathlib import Path
import re
import shutil
import subprocess
import unittest

WEB = Path(__file__).resolve().parents[1] / "web"
LEGACY_IDS = set(["search-dimensione","search-spessore","search-materiale","search-finitura","search-btn","current-folder-path","main-view-btn","history-view-btn","database-view-btn","summary-btn","tt-docx-btn","refresh-button","igs-to-zzx-btn","igs-folder-to-zzx-btn","settings-btn","debug-btn","tube-list-container","history-container","start-date","end-date","clear-filter-btn","remove-selected-btn","remove-all-btn","history-list","tube-database-container","db-filter-misura","db-filter-spessore","db-filter-forma","db-filter-materiale","db-filter-finitura","db-filter-codice","db-apply-filters-btn","db-clear-filters-btn","db-list-view-btn","db-panel-view-btn","db-expand-all-scaff-btn","db-collapse-all-scaff-btn","db-expand-all-ripiani-btn","db-collapse-all-ripiani-btn","db-add-scaffolding-btn","db-refresh-btn","db-import-btn","db-validate-btn","db-import-codes-btn","db-add-code-btn","db-order-priorities-btn","db-order-txt-btn","db-codes-docx-btn","db-multi-location-docx-btn","db-export-xlsx-btn","adhoc-docx-btn","adhoc-reset-btn","tube-database-status","tube-database-list","summary-modal","close-summary-btn","threshold-slider","threshold-value","summary-preview","generate-file-btn","generate-print-btn","debug-modal","close-debug-btn","debug-info-area","search-modal","close-search-btn","search-result","db-tube-modal","db-tube-modal-title","db-tube-modal-close","db-tube-modal-source","db-modal-misura","db-modal-spessore","db-modal-materiale","db-modal-finitura","db-modal-quantita","db-modal-target-section","db-modal-target-location","db-modal-target-ripiano","db-modal-new-ripiano","db-tube-modal-cancel","db-tube-modal-confirm","inventory-use-modal","inventory-use-close","inventory-use-note","inventory-use-position","inventory-use-cancel","inventory-use-confirm","settings-modal","settings-close","settings-da-fare-path","settings-inventory-xlsx-path","settings-database-tubi-dir","settings-codici-tubi-path","settings-docx-output-dir","settings-summary-txt-output-dir","settings-summary-docx-output-dir","settings-tt-docx-output-dir","settings-adhoc-docx-output-dir","settings-codes-docx-output-dir","settings-inventory-xlsx-output-dir","settings-order-txt-output-dir","settings-nested-zzx-output-dir","settings-nesting-gap-mm","settings-nesting-rotation-visualization-enabled","settings-nesting-debug-enabled","settings-nested-text-marking-enabled","settings-nested-text-marking-height-mm","settings-round-text-head-motion-enabled","settings-round-tip-marking-mode","settings-round-tip-marking-length-mm","settings-order-low-quantity","settings-order-medium-quantity","settings-order-high-quantity","settings-order-include-low-priority","settings-inventory-request-enabled","settings-inventory-request-folder","settings-lock-unlocked-color","settings-lock-locked-color","settings-ignore-input","settings-ignore-add","settings-ignore-list","settings-cancel","settings-save","inventory-export-modal","inventory-export-close","inventory-export-include-missing","inventory-export-cancel","inventory-export-confirm","order-priority-modal","order-priority-close","order-priority-search","order-priority-select-all","order-priority-bulk-value","order-priority-apply-bulk","order-priority-status","order-priority-rows","order-priority-cancel","order-priority-save"])
NEW_IDS = {
    "db-quick-search", "db-quick-missing", "db-quick-multi",
    "db-location-filter", "db-clear-quick", "db-overview",
    "db-search-header-slot", "db-search-database-slot", "db-shared-search",
    "db-search-submit",
}
REPLACED_QUICK_SEARCH_IDS = {
    "search-dimensione", "search-spessore", "search-materiale",
    "search-finitura", "search-btn",
}

class ModernUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (WEB / "index.html").read_text(encoding="utf-8")
        cls.script = (WEB / "script.js").read_text(encoding="utf-8")
        cls.modern = (WEB / "ui-improvements.js").read_text(encoding="utf-8")
        cls.css = (WEB / "ui-modern.css").read_text(encoding="utf-8")

    def test_preserves_all_original_element_ids(self):
        present = re.findall(r'\bid="([^"]+)"', self.index)
        self.assertEqual(len(present), len(set(present)), "Duplicate HTML IDs")
        # Only the superseded five-field header lookup was intentionally removed.
        self.assertFalse((LEGACY_IDS - REPLACED_QUICK_SEARCH_IDS) - set(present),
                         "An unrelated original element ID was removed")
        self.assertFalse(REPLACED_QUICK_SEARCH_IDS & set(present),
                         "The obsolete search UI is still present")
        self.assertTrue(NEW_IDS.issubset(set(present)))
        self.assertEqual(present.count("db-quick-search"), 1,
                         "One shared database search input must serve all tabs")

    def test_original_scripts_and_styles_still_loaded_in_order(self):
        self.assertIn('href="style.css"', self.index)
        self.assertIn('href="ui-modern.css"', self.index)
        self.assertIn('src="script.js"', self.index)
        self.assertIn('src="ui-improvements.js"', self.index)
        self.assertLess(self.index.index('href="style.css"'), self.index.index('href="ui-modern.css"'))
        self.assertLess(self.index.index('src="script.js"'), self.index.index('src="ui-improvements.js"'))

    def test_critical_desktop_operations_remain_bound(self):
        for handler in (
            "function handleTubeDatabaseClick(",
            "function submitTubeDatabaseOperation(",
            "function handleRodDrop(",
            "function exportLockedRodZzx(",
            "function nestLockedRodInPlace(",
            "function renderTubeDatabasePanels(",
        ):
            with self.subTest(handler=handler):
                self.assertIn(handler, self.script)
        for action in ("toggle-location", "toggle-ripiano", "add-here", "edit-code", "move", "remove"):
            with self.subTest(action=action):
                self.assertIn('data-db-action="' + action + '"', self.script)
        self.assertIn("updateGuardedButtonStates()", self.modern)

    def test_cross_tab_database_search_contract(self):
        # The control must be moved, rather than cloned or reimplemented.
        self.assertIn("destination.appendChild(searchGroup)", self.modern)
        self.assertIn("await window.showTubeDatabaseView()", self.modern)
        self.assertIn("if (event.key !== 'Enter') return;", self.modern)
        self.assertIn("search.focus()", self.modern)
        self.assertIn("searchTimer = setTimeout(refreshVisibleDatabase", self.modern)
        self.assertIn('id="db-shared-search"', self.index)
        self.assertIn('id="db-search-header-slot"', self.index)
        self.assertIn('id="db-search-database-slot"', self.index)
        self.assertIn("#db-search-database-slot #db-search-submit { display: none; }", self.css)

    def test_light_only_presentation(self):
        self.assertIn("color-scheme: light", self.css)
        self.assertNotIn("prefers-color-scheme: dark", self.css)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_javascript_syntax(self):
        for filename in ("script.js", "ui-improvements.js"):
            with self.subTest(filename=filename):
                checked = subprocess.run(
                    ["node", "--check", str(WEB / filename)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(checked.returncode, 0, checked.stderr)


if __name__ == "__main__":
    unittest.main()
