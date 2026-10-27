"""Guarantee redundant straight-cut rotations are skipped without losing poses."""
import unittest

from tubenest_engine.fit import PartPose, fit_adjacent_parts
from tubenest_engine.optimizer import (
    OptimizerItem,
    _first_pose_candidates,
    _next_pose_candidates,
    _pose_branch_estimate,
    _straight_rotation_is_redundant,
    _rectangle_family,
)


def make_part(kind="Square", *, bevel=False, residual=0.0,
              curve_normal=(0.0, 0.0, 1.0), length=500.0):
    profile = {
        "kind": kind,
        "outside_width": 50.0 if kind == "Square" else 100.0,
        "outside_height": 50.0,
        "corner_radius": 0.0,
        "thickness": 2.0,
    }
    ends = []
    for n, c in enumerate((0.0, float(length))):
        ends.append({
            "label": "A" if n == 0 else "B",
            "plane_z_equals_c_plus_ax_plus_by":
                [c, (0.25 if bevel and n == 1 else 0.0), 0.0],
            "plane_max_residual_mm": float(residual),
            "operation_layer": 1,
            "boundary_signature": "same-cut",
            "process_signature": "cutting",
            "work_flags": 0,
            "curve_flags": 32,
            "curve_normal": list(curve_normal),
        })
    return {"profile": profile, "ends": ends, "overall_length": float(length)}


def make_item(kind="Square", **kwargs):
    part = make_part(kind, **kwargs)
    return OptimizerItem(
        index=0,
        instance_key="test",
        source_id="test",
        nominal_length=part["overall_length"],
        tube_part=part,
    )


class StraightRotationPruningTests(unittest.TestCase):
    def test_straight_square_has_one_rotation_with_both_reversals(self):
        item = make_item()
        self.assertTrue(_straight_rotation_is_redundant(item))
        self.assertEqual(
            [(p.axial_rotation_degrees, p.reversed_end_for_end)
             for p in _first_pose_candidates(item)],
            [(0.0, False), (0.0, True)],
        )
        next_poses = _next_pose_candidates(item, PartPose(), item, None)
        self.assertEqual(next_poses, _first_pose_candidates(item))
        self.assertEqual(_pose_branch_estimate(item, first=True), 2)
        self.assertEqual(_pose_branch_estimate(item, first=False), 2)

    def test_straight_rectangle_keeps_family_choice_and_reversal(self):
        item = make_item("Rect")
        first = _first_pose_candidates(item)
        self.assertEqual({p.axial_rotation_degrees for p in first}, {0.0, 90.0})
        self.assertEqual(len(first), 4)
        for family, rotation in [(0, 0.0), (90, 90.0)]:
            poses = _next_pose_candidates(item, PartPose(rotation), item, family)
            self.assertEqual(len(poses), 2)
            self.assertEqual({p.axial_rotation_degrees for p in poses}, {rotation})
            self.assertEqual(
                {p.reversed_end_for_end for p in poses}, {False, True}
            )
        # If a square preceded this rectangle, the rectangle still has to
        # establish the orientation family before further rectangles are added.
        first_rect = _next_pose_candidates(item, PartPose(), item, None)
        self.assertEqual({p.axial_rotation_degrees for p in first_rect}, {0.0, 90.0})
        self.assertEqual(_pose_branch_estimate(item, first=True), 4)
        # The conservative branch bound also covers rectangles following
        # squares, where both families must still be considered.
        self.assertEqual(_pose_branch_estimate(item, first=False), 4)

    def test_beveled_square_still_explores_every_rotation_and_flip(self):
        item = make_item(bevel=True)
        self.assertFalse(_straight_rotation_is_redundant(item))
        poses = _first_pose_candidates(item)
        self.assertEqual(len(poses), 8)
        self.assertEqual(
            {p.axial_rotation_degrees for p in poses},
            {0.0, 90.0, 180.0, 270.0},
        )
        self.assertEqual(
            len(_next_pose_candidates(item, PartPose(), item, None)), 8
        )

    def test_beveled_rectangle_retains_full_family_variations(self):
        item = make_item("Rect", bevel=True)
        self.assertFalse(_straight_rotation_is_redundant(item))
        self.assertEqual(len(_first_pose_candidates(item)), 8)
        self.assertEqual(
            {p.axial_rotation_degrees
             for p in _next_pose_candidates(item, PartPose(), item, 0)},
            {0.0, 180.0},
        )

    def test_nonplanar_or_transverse_normal_cannot_be_pruned(self):
        for case in (
            {"residual": 0.06},
            {"curve_normal": (1.0, 0.0, 0.0)},
        ):
            with self.subTest(case=case):
                item = make_item(**case)
                self.assertFalse(_straight_rotation_is_redundant(item))
                self.assertEqual(len(_first_pose_candidates(item)), 8)

    def test_a_missing_plane_never_qualifies(self):
        item = make_item()
        item.tube_part["ends"][0]["plane_z_equals_c_plus_ax_plus_by"] = None
        self.assertFalse(_straight_rotation_is_redundant(item))

    def test_pruned_square_rotations_have_identical_validated_fit(self):
        first, second = make_item(), make_item()
        reference = fit_adjacent_parts(
            first.tube_part, PartPose(0.0, False), 0.0,
            second.tube_part, PartPose(0.0, False), gap_mm=2.0,
        )
        for rotation in (90.0, 180.0, 270.0):
            with self.subTest(rotation=rotation):
                candidate = fit_adjacent_parts(
                    first.tube_part, PartPose(0.0, False), 0.0,
                    second.tube_part, PartPose(rotation, False), gap_mm=2.0,
                )
                self.assertAlmostEqual(reference.next_origin, candidate.next_origin)
                self.assertEqual(reference.common_line, candidate.common_line)

    def test_pruned_rectangle_within_family_has_identical_fit(self):
        first, second = make_item("Rect"), make_item("Rect")
        for previous_rotation, next_rotation in (
            (0.0, 180.0), (90.0, 270.0),
        ):
            with self.subTest(rotations=(previous_rotation, next_rotation)):
                reference = fit_adjacent_parts(
                    first.tube_part, PartPose(previous_rotation), 0.0,
                    second.tube_part, PartPose(previous_rotation), gap_mm=2.0,
                )
                rotated = fit_adjacent_parts(
                    first.tube_part, PartPose(previous_rotation), 0.0,
                    second.tube_part, PartPose(next_rotation), gap_mm=2.0,
                )
                self.assertAlmostEqual(reference.next_origin, rotated.next_origin)
                self.assertEqual(reference.common_line, rotated.common_line)


if __name__ == "__main__":
    unittest.main()
