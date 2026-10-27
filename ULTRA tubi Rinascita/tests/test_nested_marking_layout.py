"""PROD / folder TD / file TD / length marking layout regression tests."""
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

    def test_normal_code_in_folder_and_filename(self):
        text = self.build_marking(
            "TD0002A00189 aisi 304 tubolare 50x50x2 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD.00318", "TD.00189", "L200"],
        )

    def test_two_file_td_codes_each_get_separate_line(self):
        text = self.build_marking(
            "TD0002A00189 TD0003B00456 aisi 304 L200 2pz.zzx"
        )
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD.00318", "TD.00189", "TD.00456", "L200"],
        )

    def test_no_file_td_omits_only_file_entry(self):
        text = self.build_marking("aisi 304 tubolare 50x50x2 L200 2pz.zzx")
        self.assertEqual(text.splitlines(), ["p prod 80", "TD.00318", "L200"])

    def test_no_td_anywhere_keeps_prod_and_length(self):
        text = self.build_marking("tubo 50x50x2 L200 2pz.zzx", folder="TT")
        self.assertEqual(text.splitlines(), ["p prod 80", "L200"])

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

    def test_shortened_codes_preserve_leading_zeroes(self):
        text = self.build_marking("TD0000A00001 L200.zzx", folder="TD9999Z00000")
        self.assertEqual(
            text.splitlines(),
            ["p prod 80", "TD.00000", "TD.00001", "L200"],
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
