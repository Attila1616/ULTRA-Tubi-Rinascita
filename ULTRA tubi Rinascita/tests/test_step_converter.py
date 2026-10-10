from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tubenest_engine.igs_converter import IgsConversionError
from tubenest_engine.step_converter import (
    _model_geometry_signature,
    _sanitize_component_name,
    _tube_filename,
    convert_step_to_zzx,
)


def _tube_model():
    near = np.asarray(
        [
            [-60.0, -60.0, 0.0],
            [60.0, -60.0, 0.0],
            [60.0, 60.0, 0.0],
            [-60.0, 60.0, 0.0],
            [-60.0, -60.0, 0.0],
        ],
        dtype=float,
    )
    inner_near = near * np.array([0.9333333333, 0.9333333333, 1.0])
    far = near.copy()
    far[:, 2] = 4680.0
    inner_far = inner_near.copy()
    inner_far[:, 2] = 4680.0

    return {
        "profile_kind": "Square",
        "outside_width": 120.0,
        "outside_height": 120.0,
        "thickness": 4.0,
        "corner_radius": 6.0,
        "overall_length": 4680.0,
        "axis": [0.0, 1.0, 0.0],
        "local_x_axis": [1.0, 0.0, 0.0],
        "local_y_axis": [0.0, 0.0, 1.0],
        "operations": [
            {
                "outer": near,
                "inner": inner_near,
                "z_min": 0.0,
                "z_max": 0.0,
                "z_mean": 0.0,
            },
            {
                "outer": far,
                "inner": inner_far,
                "z_min": 4680.0,
                "z_max": 4680.0,
                "z_mean": 4680.0,
            },
        ],
        "display_operations": [],
    }


def _with_side_hole(model, z_position):
    copied = {
        key: (
            list(value)
            if key == "operations"
            else value
        )
        for key, value in model.items()
    }
    copied["operations"] = [
        {
            key: (
                np.asarray(value, dtype=float).copy()
                if key in {"outer", "inner"}
                else value
            )
            for key, value in operation.items()
        }
        for operation in model["operations"]
    ]

    z = float(z_position)
    outer = np.asarray(
        [
            [60.0, -8.0, z - 8.0],
            [60.0, 8.0, z - 8.0],
            [60.0, 8.0, z + 8.0],
            [60.0, -8.0, z + 8.0],
            [60.0, -8.0, z - 8.0],
        ],
        dtype=float,
    )
    inner = outer.copy()
    inner[:, 0] = 56.0

    copied["operations"].insert(
        1,
        {
            "outer": outer,
            "inner": inner,
            "z_min": z - 8.0,
            "z_max": z + 8.0,
            "z_mean": z,
        },
    )
    return copied


def _reverse_model_axis(model):
    length = float(model["overall_length"])
    copied = dict(model)
    copied["operations"] = []

    for operation in reversed(model["operations"]):
        transformed = {}
        for key in ("outer", "inner"):
            points = np.asarray(
                operation[key],
                dtype=float,
            ).copy()
            points[:, 1] *= -1.0
            points[:, 2] = length - points[:, 2]
            transformed[key] = points

        transformed["z_min"] = float(
            transformed["outer"][:, 2].min()
        )
        transformed["z_max"] = float(
            transformed["outer"][:, 2].max()
        )
        transformed["z_mean"] = float(
            transformed["outer"][:, 2].mean()
        )
        copied["operations"].append(transformed)

    return copied


class StepConverterTests(unittest.TestCase):
    def test_windows_unsafe_component_name_is_sanitized(self):
        self.assertEqual(
            _sanitize_component_name('TA:9900/A00080* "test"'),
            "TA_9900_A00080_ _test_",
        )

    def test_tube_filename_contains_profile_length_and_quantity(self):
        filename = _tube_filename(
            "TA9900A00080_oa_0",
            _tube_model(),
            4,
        )
        self.assertEqual(
            filename,
            "TA9900A00080_oa_0 tubolare 120x120x4 L4680 4pz.zzx",
        )

    def test_geometry_signature_merges_same_tube_from_opposite_end(self):
        model = _with_side_hole(
            _tube_model(),
            750.0,
        )
        reversed_model = _reverse_model_axis(model)

        self.assertEqual(
            _model_geometry_signature(model),
            _model_geometry_signature(reversed_model),
        )

    def test_geometry_signature_keeps_holes_at_different_positions_separate(self):
        first = _with_side_hole(
            _tube_model(),
            750.0,
        )
        second = _with_side_hole(
            _tube_model(),
            900.0,
        )

        self.assertNotEqual(
            _model_geometry_signature(first),
            _model_geometry_signature(second),
        )

    def test_step_conversion_merges_duplicate_geometry_quantities(self):
        model = _with_side_hole(
            _tube_model(),
            750.0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "assembly.stp"
            source.write_text("mock STEP", encoding="ascii")
            assembly = {
                "definitions": [
                    {
                        "key": (1,),
                        "name": "TA9900A00080_oa_0",
                        "shape": object(),
                        "occurrence_count": 1,
                    },
                    {
                        "key": (2,),
                        "name": "TA9900A00080_oa_1",
                        "shape": object(),
                        "occurrence_count": 1,
                    },
                ],
                "occurrence_count": 2,
                "root_count": 1,
            }

            def fake_write(_model, _source, destination):
                Path(destination).write_bytes(b"ZZX")

            with (
                patch(
                    "tubenest_engine.step_converter._step_api",
                    return_value={},
                ),
                patch(
                    "tubenest_engine.step_converter._collect_step_definitions",
                    return_value=assembly,
                ),
                patch(
                    "tubenest_engine.step_converter._export_shape_to_iges",
                ),
                patch(
                    "tubenest_engine.step_converter._analyse_iges",
                    side_effect=[model, model],
                ),
                patch(
                    "tubenest_engine.step_converter._write_zzx",
                    side_effect=fake_write,
                ),
            ):
                result = convert_step_to_zzx(source)

            self.assertEqual(result["tubeDefinitionCount"], 2)
            self.assertEqual(result["uniqueTubeCount"], 1)
            self.assertEqual(result["mergedDefinitionCount"], 1)
            self.assertEqual(result["convertedCount"], 1)
            self.assertEqual(
                result["results"][0]["occurrenceCount"],
                2,
            )
            self.assertEqual(
                result["results"][0]["mergedDefinitionCount"],
                2,
            )

            output = Path(result["results"][0]["outputPath"])
            self.assertEqual(
                output.name,
                "TA9900A00080 tubolare 120x120x4 L4680 2pz.zzx",
            )

    def test_step_conversion_does_not_merge_different_hole_positions(self):
        first = _with_side_hole(
            _tube_model(),
            750.0,
        )
        second = _with_side_hole(
            _tube_model(),
            900.0,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "assembly.stp"
            source.write_text("mock STEP", encoding="ascii")
            assembly = {
                "definitions": [
                    {
                        "key": (1,),
                        "name": "TA9900A00080_oa_0",
                        "shape": object(),
                        "occurrence_count": 1,
                    },
                    {
                        "key": (2,),
                        "name": "TA9900A00080_oa_1",
                        "shape": object(),
                        "occurrence_count": 1,
                    },
                ],
                "occurrence_count": 2,
                "root_count": 1,
            }

            def fake_write(_model, _source, destination):
                Path(destination).write_bytes(b"ZZX")

            with (
                patch(
                    "tubenest_engine.step_converter._step_api",
                    return_value={},
                ),
                patch(
                    "tubenest_engine.step_converter._collect_step_definitions",
                    return_value=assembly,
                ),
                patch(
                    "tubenest_engine.step_converter._export_shape_to_iges",
                ),
                patch(
                    "tubenest_engine.step_converter._analyse_iges",
                    side_effect=[first, second],
                ),
                patch(
                    "tubenest_engine.step_converter._write_zzx",
                    side_effect=fake_write,
                ),
            ):
                result = convert_step_to_zzx(source)

            self.assertEqual(result["tubeDefinitionCount"], 2)
            self.assertEqual(result["uniqueTubeCount"], 2)
            self.assertEqual(result["mergedDefinitionCount"], 0)
            self.assertEqual(result["convertedCount"], 2)

    def test_step_conversion_skips_non_tubes_and_writes_detected_tubes(self):
        model = _tube_model()

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "assembly.stp"
            source.write_text("mock STEP", encoding="ascii")

            assembly = {
                "definitions": [
                    {
                        "key": (1,),
                        "name": "TA9900A00080_oa_0",
                        "shape": object(),
                        "occurrence_count": 4,
                    },
                    {
                        "key": (2,),
                        "name": "PLATE_01",
                        "shape": object(),
                        "occurrence_count": 2,
                    },
                ],
                "occurrence_count": 6,
                "root_count": 1,
            }

            def fake_write(_model, _source, destination):
                Path(destination).write_bytes(b"ZZX")

            with (
                patch(
                    "tubenest_engine.step_converter._step_api",
                    return_value={},
                ),
                patch(
                    "tubenest_engine.step_converter._collect_step_definitions",
                    return_value=assembly,
                ),
                patch(
                    "tubenest_engine.step_converter._export_shape_to_iges",
                ),
                patch(
                    "tubenest_engine.step_converter._analyse_iges",
                    side_effect=[
                        model,
                        IgsConversionError(
                            "Non trovo una sezione trasversale integra del tubo."
                        ),
                    ],
                ),
                patch(
                    "tubenest_engine.step_converter._write_zzx",
                    side_effect=fake_write,
                ),
            ):
                result = convert_step_to_zzx(source)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["componentDefinitionCount"], 2)
            self.assertEqual(result["componentOccurrenceCount"], 6)
            self.assertEqual(result["tubeDefinitionCount"], 1)
            self.assertEqual(result["convertedCount"], 1)
            self.assertEqual(result["skippedCount"], 1)
            self.assertEqual(result["errorCount"], 0)

            output = Path(result["results"][0]["outputPath"])
            self.assertTrue(output.exists())
            self.assertEqual(
                output.name,
                "TA9900A00080_oa_0 tubolare 120x120x4 L4680 4pz.zzx",
            )
            self.assertEqual(
                result["skipped"][0]["componentName"],
                "PLATE_01",
            )


if __name__ == "__main__":
    unittest.main()
