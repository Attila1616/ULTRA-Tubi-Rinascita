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
    "db-search-submit", "settings-nesting-rod-tolerance-mm",
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

    def test_locked_rod_drag_uses_geometry_not_nominal_sum(self):
        self.assertIn("async function solveLockedRodDraft(", self.script)
        self.assertIn("await window.pywebview.api.nest_locked_rod_sequence(", self.script)
        self.assertIn("const targetPlacements = await solveLockedRodDraft(", self.script)
        self.assertIn("const sourcePlacements = sourceDraft?.length", self.script)
        self.assertIn("targetRod.segments = targetDraft.map(", self.script)
        self.assertIn("targetRod.segments.map(s => s.instanceKey).join('|')", self.script)
        self.assertNotIn(
            "if (used + (parseInt(segment.length, 10) || 0) > 6000)",
            self.script,
        )

    def test_locked_rod_label_uses_optimized_placement_span(self):
        self.assertIn("function optimizedRodProgress(segments)", self.script)
        self.assertIn("Math.max(0, end - endReached)", self.script)
        self.assertIn("Rimasto da tagliare", self.script)
        self.assertIn("(ottimizzati)", self.script)
        self.assertIn("Posizioni da ricalcolare", self.script)
        self.assertIn(
            "return null; // Never mistake a sum of angled nominal lengths",
            self.script,
        )

    def test_nesting_cache_button_clears_backend_and_client(self):
        self.assertIn('data-action="clear-nesting-cache"', self.script)
        self.assertIn("async function clearCachedNestings()", self.script)
        self.assertIn("pywebview.api.clear_nesting_result_cache()", self.script)
        self.assertIn("nestingLoadingGroups.clear()", self.script)
        self.assertIn("nestingActiveGenerationByTube.clear()", self.script)
        self.assertIn("nestingCacheResetEpoch++", self.script)
        self.assertIn("await saveUiState()", self.script)

    def test_tolerance_setting_and_rod_geometry_display(self):
        self.assertIn('id="settings-nesting-rod-tolerance-mm"', self.index)
        self.assertIn("nesting_rod_tolerance_mm:", self.script)
        self.assertIn("function nestingAllowedLengthMm()", self.script)
        self.assertIn("totalUsed > nestingAllowedLengthMm()", self.script)
        self.assertIn("used > nestingAllowedLengthMm()", self.script)

    def test_tail_reserve_threshold_is_only_configured_for_deep_nesting(self):
        self.assertIn(
            'id="settings-nesting-tail-reserve-max-length-mm"', self.index
        )
        self.assertIn("nesting_tail_reserve_max_length_mm:", self.script)
        self.assertIn("config.nesting_tail_reserve_max_length_mm", self.script)
        self.assertIn("stats.reservedPieces", self.script)
        self.assertIn("stats.reserveThresholdMm", self.script)
        self.assertIn("async function optimizeGroupNesting(tubeType)", self.script)

    def test_optional_deep_nesting_button_and_eta_contract(self):
        self.assertIn('data-action="optimize-nesting-group"', self.script)
        self.assertIn("async function optimizeGroupNesting(tubeType)", self.script)
        self.assertIn("async function withNestingProgress(", self.script)
        self.assertIn("pywebview.api.optimize_calculated_nesting({", self.script)
        self.assertIn("pywebview.api.get_nesting_job_status(jobId)", self.script)
        self.assertIn("nestingAggressiveGroups.has(tubeType)", self.script)
        self.assertIn("data-nesting-progress-tube", self.script)
        self.assertIn("nestingEtaText(", self.script)
        self.assertIn("context.piecesToNest.length", self.script)
        self.assertIn("buildClientNestingSignature(", self.script)
        self.assertIn("epoch !== nestingCacheResetEpoch", self.script)
        self.assertIn("await saveUiState()", self.script)

    def test_deep_nesting_keeps_valid_fill_improvements(self):
        # Old JS veto rejected legitimate rearrangements if they added
        # slightly more than 15mm nominal stock usage; the backend now
        # evaluates both stock usage and usable offcut concentration.
        self.assertIn("stats.initialFillConcentration", self.script)
        self.assertIn("stats.finalFillConcentration", self.script)
        self.assertIn("if (stats.keptOriginal)", self.script)
        self.assertNotIn("newUsed > oldUsed + 15", self.script)
        self.assertIn("stats.guidedMoves", self.script)
        self.assertIn("stats.guidedTrials", self.script)

    def test_default_fast_nesting_still_runs_independently(self):
        self.assertIn("async function ensureGroupNesting(tubeType)", self.script)
        self.assertIn("jobId => nestGroupsBackend(", self.script)
        self.assertIn("tubeType, 'fast'", self.script)
        self.assertIn("tubeType, 'deep'", self.script)

    def test_minimum_text_height_setting_for_narrow_tubes(self):
        self.assertIn('id="settings-nested-text-marking-min-height-mm"', self.index)
        self.assertIn("nested_text_marking_min_height_mm:", self.script)
        self.assertIn("config.nested_text_marking_min_height_mm", self.script)
        self.assertIn('id="settings-round-text-head-motion-enabled"', self.index)

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
