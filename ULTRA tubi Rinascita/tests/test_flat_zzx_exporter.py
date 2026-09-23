import struct
import tempfile
import unittest
from pathlib import Path

from tubenest_engine.archive import Archive
from tubenest_engine.domain import read_tube_parts
from tubenest_engine.flat_exporter import export_flat_nested_rod
from tubenest_engine.geometry import Line
from tubenest_engine.reader import read_zzx_cached


APP_ROOT = Path(__file__).resolve().parents[1]


class FlatNestedZzxExporterTests(unittest.TestCase):
    def test_two_real_text_parts_flatten_to_one_segment(self):
        source = APP_ROOT / "Tube with sample text.zzx"
        self.assertTrue(source.is_file())

        source_archive = Archive.read(source)
        source_archive.validate()
        source_segment = source_archive.xml("Segments/content.xml").find("TubeSegment")
        source_shape_count = len(list(source_segment.find("Shapes")))

        part = read_tube_parts(source)[0]
        length = float(part.overall_length)

        placements = [
            {
                "instanceKey": "text::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                },
            },
            {
                "instanceKey": "text::2",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": length + 2.0,
                    "z_end": 2.0 * length + 2.0,
                    "axial_rotation_degrees": 180.0,
                    "reversed_end_for_end": True,
                    "common_line_before": False,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "flat_text.zzx"
            result = export_flat_nested_rod(
                placements,
                output,
                rod_length=6000.0,
                gap_mm=2.0,
            )

            self.assertEqual(result["segmentCount"], 1)
            self.assertEqual(result["pieceCount"], 2)
            self.assertEqual(
                result["exportMode"],
                "single_segment_positioned_contours",
            )

            archive = Archive.read(output)
            archive.validate()

            segments = archive.xml("Segments/content.xml").findall("TubeSegment")
            self.assertEqual(len(segments), 1)
            refs = list(segments[0].find("Shapes"))
            self.assertEqual(len(refs), source_shape_count * 2)

            pack = archive.xml("Portions/content.xml").find("DocPortion/PackSegments")
            self.assertEqual(len(pack.findall("TubeSegment")), 1)
            self.assertEqual(len(pack.findall("WorkSeq/Seg")), 1)

            merged_part = read_tube_parts(output)[0]
            self.assertAlmostEqual(
                merged_part.overall_length,
                2.0 * length + 2.0,
                places=4,
            )
            self.assertGreaterEqual(merged_part.marking_feature_count, 2)

    def test_flat_export_respects_optimizer_rotation_and_reversal(self):
        source = APP_ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)

        placements = [
            {
                "instanceKey": "round::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 90.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                },
            },
            {
                "instanceKey": "round::2",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": length + 2.0,
                    "z_end": 2.0 * length + 2.0,
                    "axial_rotation_degrees": 270.0,
                    "reversed_end_for_end": True,
                    "common_line_before": False,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "flat_round.zzx"
            result = export_flat_nested_rod(
                placements,
                output,
                rod_length=6000.0,
                gap_mm=2.0,
            )
            self.assertEqual(result["segmentCount"], 1)
            self.assertAlmostEqual(result["placements"][0]["axialRotationDegrees"], 0.0)
            self.assertAlmostEqual(result["placements"][1]["axialRotationDegrees"], 180.0)
            self.assertTrue(result["placements"][1]["reversedEndForEnd"])

            archive = Archive.read(output)
            archive.validate()
            merged_part = read_tube_parts(output)[0]
            self.assertAlmostEqual(
                merged_part.overall_length,
                2.0 * length + 2.0,
                places=4,
            )

    def test_noncoincident_forced_common_line_stops_export(self):
        source = APP_ROOT / (
            "Round tube Ø30 L1215, first cut 0° layer 1, "
            "second cut 45° layer 4.zzx"
        )
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "round::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                },
            },
            {
                "instanceKey": "round::2",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": length,
                    "z_end": 2.0 * length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": False,
                    "common_line_before": True,
                },
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "must_not_export_overlap.zzx"
            with self.assertRaisesRegex(
                ValueError,
                "Shared-cut verification failed",
            ):
                export_flat_nested_rod(
                    placements,
                    output,
                )
            self.assertFalse(output.exists())


    def test_machining_shapes_are_ordered_left_to_right_after_reversal(self):
        source = APP_ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "round::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 0.0,
                    "reversed_end_for_end": True,
                    "common_line_before": False,
                },
            },
            {
                "instanceKey": "round::2",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": length + 2.0,
                    "z_end": 2.0 * length + 2.0,
                    "axial_rotation_degrees": 180.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "ordered.zzx"
            export_flat_nested_rod(placements, output)
            document = read_zzx_cached(output)
            machining_centers = []
            for shape in document.segments[0].shapes:
                if shape.operation_layer is None or not shape.sampled_bounds:
                    continue
                machining_centers.append(
                    (
                        float(shape.sampled_bounds[0][2])
                        + float(shape.sampled_bounds[1][2])
                    )
                    / 2.0
                )

            self.assertEqual(
                machining_centers,
                sorted(machining_centers),
            )

    def test_angled_end_cut_start_is_minimum_z_after_pose(self):
        source = APP_ROOT / "Round tube Ø30 L1215, first cut 0° layer 1, second cut 45° layer 4.zzx"
        part = read_tube_parts(source)[0]
        length = float(part.overall_length)
        placements = [
            {
                "instanceKey": "round::1",
                "filePath": str(source),
                "fileName": source.name,
                "segmentHandle": part.segment_handle,
                "nestPlacement": {
                    "z_start": 0.0,
                    "z_end": length,
                    "axial_rotation_degrees": 270.0,
                    "reversed_end_for_end": False,
                    "common_line_before": False,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "falling_start.zzx"
            export_flat_nested_rod(placements, output)
            document = read_zzx_cached(output)
            angled = next(
                end
                for end in document.segments[0].end_cuts
                if float(end.cut_angle_from_perpendicular_degrees or 0.0) > 1.0
            )
            self.assertIsNotNone(angled.start_point)
            self.assertIsNotNone(angled.sampled_bounds)
            self.assertAlmostEqual(
                float(angled.start_point[2]),
                float(angled.sampled_bounds[0][2]),
                delta=0.25,
            )


if __name__ == "__main__":
    unittest.main()
