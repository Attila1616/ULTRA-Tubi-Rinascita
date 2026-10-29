"""PROD / full folder TD / abbreviated file TD / length engraving regression tests."""
import os
import unittest

from backend_logic import _build_nested_marking_text
from tubenest_engine.text_marking import marking_layout_candidates


class NestedMarkingLayoutTests(unittest.TestCase):
    def build_marking(self, filename, *, folder="TD1905A00318 1pz",
                      main_folder="03 p prod 80", length=200):
        segment = {
            "mainFolder": main_folder,
            "filePath": os.path.join(
                "Tubi", main_folder, "TT", folder, "tubolare 50x50x2", filename
            ),
            "fileName": filename,
            "length": length,
        }
        return _build_nested_marking_text(segment, {})

    def test_normal_folder_full_and_file_short(self):
        text = self.build_marking(
            "TD0002A00189 aisi 304 tubolare 50x50x2 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD1905A00318", "TD.00189", "L200"],
        )

    def test_two_different_file_td_codes_each_get_a_line(self):
        text = self.build_marking(
            "TD0002A00189 TD0003B00456 aisi 304 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD1905A00318", "TD.00189", "TD.00456", "L200"],
        )

    def test_identical_folder_and_file_td_printed_once_full(self):
        text = self.build_marking(
            "TD1905A00318 aisi 304 tubolare 50x50x2 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(), ["p prod 80", "TD1905A00318", "L200"]
        )

    def test_identical_codes_ignore_case(self):
        text = self.build_marking("td1905a00318 L200 2pz.zzx")
        self.assertEqual(text.splitlines(), ["p prod 80", "TD1905A00318", "L200"])

    def test_folder_match_and_different_file_code(self):
        text = self.build_marking(
            "TD1905A00318 TD0002A00189 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD1905A00318", "TD.00189", "L200"],
        )

    def test_only_full_identifier_equality_counts_as_duplicate(self):
        # These codes share the last 5 digits, but their full IDs differ.
        text = self.build_marking("TD9999Z00318 L200 2pz.zzx")
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD1905A00318", "TD.00318", "L200"],
        )

    def test_duplicate_file_td_identifiers_printed_once(self):
        text = self.build_marking(
            "TD0002A00189 TD0002A00189 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(), ["p prod 80", "TD1905A00318", "TD.00189", "L200"]
        )

    def test_only_first_td_folder_below_tt_counts(self):
        segment = {
            "mainFolder": "03 p prod 80",
            "filePath": os.path.join(
                "Da Fare", "03 p prod 80", "TT",
                "TD1111A00001", "TD2222B00002", "TD3333C00003",
                "tubolare 20x20x2", "TD4444D00004 L200.zzx",
            ),
            "fileName": "TD4444D00004 L200.zzx",
            "length": 200,
        }
        self.assertEqual(
            _build_nested_marking_text(segment, {}).splitlines(),
            ["p prod 80", "TD1111A00001", "TD.00004", "L200"],
        )

    def test_first_td_folder_may_be_below_non_td_tt_subfolder(self):
        segment = {
            "mainFolder": "03 p prod 80",
            "filePath": os.path.join(
                "Da Fare", "03 p prod 80", "TT",
                "altro", "TD1111A00001", "TD2222B00002",
                "tubo", "TD1111A00001 L300.zzx",
            ),
            "fileName": "TD1111A00001 L300.zzx",
            "length": 300,
        }
        self.assertEqual(
            _build_nested_marking_text(segment, {}).splitlines(),
            ["p prod 80", "TD1111A00001", "L300"],
        )

    def test_no_file_td_omits_only_file_entry(self):
        text = self.build_marking("aisi 304 tubolare 50x50x2 L200 2pz.zzx")
        self.assertEqual(text.splitlines(), ["p prod 80", "TD1905A00318", "L200"])

    def test_no_td_anywhere_keeps_prod_and_length(self):
        text = self.build_marking("tubo 50x50x2 L200 2pz.zzx", folder="TT")
        self.assertEqual(text.splitlines(), ["p prod 80", "L200"])

    def test_td_root_folder_replaces_prod_and_nested_folder_td(self):
        text = self.build_marking(
            "TD0002A00189 L200 1pz.zzx",
            main_folder="TD9999Z00123 produzione",
            folder="TD1905A00318 2pz",
        )
        self.assertEqual(
            text.splitlines(),
            ["TD9999Z00123", "TD.00189", "L200"],
        )

    def test_td_root_same_as_file_code_prints_full_once(self):
        text = self.build_marking(
            "TD1905A00318 L200 1pz.zzx",
            main_folder="TD1905A00318 2pz",
            folder="ALTRO",
        )
        self.assertEqual(text.splitlines(), ["TD1905A00318", "L200"])

    def test_td_root_without_td_in_filename_has_no_prod_line(self):
        text = self.build_marking(
            "tubo L200 2pz.zzx", main_folder="TD1905A00318", folder="ALTRO"
        )
        self.assertEqual(text.splitlines(), ["TD1905A00318", "L200"])

    def test_td_root_with_file_variant_keeps_letter_after_length(self):
        text = self.build_marking(
            "TD1905A00318B L200 2pz.zzx",
            main_folder="TD1905A00318 lotto 4",
            folder="ALTRO",
        )
        self.assertEqual(text.splitlines(), ["TD1905A00318", "L200 B"])

    def test_ta_prefix_not_treated_as_td(self):
        text = self.build_marking("TA0002A00189 L200.zzx", folder="TT")
        self.assertEqual(text.splitlines(), ["p prod 80", "L200"])

    def test_variant_immediately_after_td_code(self):
        text = self.build_marking("TD0002A00189B aisi 304 L200 2pz.zzx")
        self.assertEqual(text.splitlines()[-2:], ["TD.00189", "L200 B"])

    def test_variant_separated_after_td_code(self):
        text = self.build_marking("TD0002A00189 A aisi 304 L200 2pz.zzx")
        self.assertEqual(text.splitlines()[-1], "L200 A")

    def test_variant_after_length_separated(self):
        text = self.build_marking("TD0002A00189 aisi 304 L200 C 2pz.zzx")
        self.assertEqual(text.splitlines()[-1], "L200 C")

    def test_variant_immediately_after_length(self):
        text = self.build_marking("TD0002A00189 L200A 2pz.zzx")
        self.assertEqual(text.splitlines()[-1], "L200 A")

    def test_length_variant_takes_priority_over_td_variant(self):
        text = self.build_marking("TD0002A00189B aisi 304 L200 C 2pz.zzx")
        self.assertEqual(text.splitlines()[-1], "L200 C")

    def test_internal_td_letter_is_not_variant(self):
        text = self.build_marking("TD0002A00189 aisi 304 L200 2pz.zzx")
        self.assertEqual(text.splitlines()[-1], "L200")

    def test_unrelated_words_not_mistaken_for_revision(self):
        text = self.build_marking("TD0002A00189 AISI 304 L200 2pz.zzx")
        self.assertEqual(text.splitlines()[-1], "L200")

    def test_variant_in_folder_used_only_when_not_in_filename(self):
        text = self.build_marking(
            "tubo L200 2pz.zzx", folder="TD1905A00318 C"
        )
        self.assertEqual(text.splitlines()[-1], "L200 C")

    def test_shortened_file_codes_preserve_leading_zeroes(self):
        text = self.build_marking("TD0000A00001 L200.zzx", folder="TD9999Z00000")
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD9999Z00000", "TD.00001", "L200"],
        )

    def test_multiline_layout_never_joins_or_reorders_fields(self):
        marking = self.build_marking(
            "TD0002A00189 TD0003B00456 L200 B 2pz.zzx"
        )
        lines = marking.splitlines()
        self.assertEqual(marking_layout_candidates(marking), [lines])
        self.assertEqual(len(lines), 5)

    def test_legacy_pipe_labels_still_supported_for_older_callers(self):
        old = marking_layout_candidates("PROD 80 | TD1905A00318 | L200")
        self.assertTrue(old)
        self.assertGreaterEqual(len(old), 2)


if __name__ == "__main__":
    unittest.main()
