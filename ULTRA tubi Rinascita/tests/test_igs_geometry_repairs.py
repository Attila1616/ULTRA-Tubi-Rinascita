"""Tests for cautious repair of slightly imperfect IGES machining boundaries."""

import unittest
from unittest.mock import patch

import numpy as np

from tubenest_engine.igs_converter import (
    IgsConversionError,
    _chain_cut_boundary,
    _chain_edges,
)


class IgesCutBoundaryRepairTests(unittest.TestCase):
    @staticmethod
    def edges_with_endpoint_gap(gap):
        edges = ["bottom", "right", "top", "left"]
        points = {
            "bottom": [
                np.array([0.0, 0.0, 0.0]),
                np.array([40.0, 0.0, 0.0]),
            ],
            "right": [
                np.array([40.0 + gap, 0.0, 0.0]),
                np.array([40.0, 20.0, 0.0]),
            ],
            "top": [
                np.array([40.0, 20.0, 0.0]),
                np.array([0.0, 20.0, 0.0]),
            ],
            "left": [
                np.array([0.0, 20.0, 0.0]),
                np.array([0.0, gap, 0.0]),
            ],
        }
        return edges, points

    def test_small_cut_gaps_are_repaired_only_after_strict_failure(self):
        edges, points = self.edges_with_endpoint_gap(0.006)
        with (
            patch(
                "tubenest_engine.igs_converter._edge_points",
                side_effect=lambda edge, _api, **_kw: points[edge],
            ),
            patch("tubenest_engine.igs_converter._igs_log") as log,
        ):
            with self.assertRaises(IgsConversionError):
                _chain_edges(edges, {}, ignore_degenerate=True)

            repaired = _chain_cut_boundary(
                edges, {}, "test.igs", "outer"
            )

        self.assertEqual(len(repaired), 1)
        np.testing.assert_allclose(repaired[0][0], repaired[0][-1])
        log.assert_called_once()

    def test_large_gaps_are_never_silently_connected(self):
        edges, points = self.edges_with_endpoint_gap(0.08)
        with patch(
            "tubenest_engine.igs_converter._edge_points",
            side_effect=lambda edge, _api, **_kw: points[edge],
        ):
            with self.assertRaises(IgsConversionError):
                _chain_cut_boundary(edges, {}, "test.igs", "outer")

    def test_precise_contours_do_not_trigger_repair(self):
        edges, points = self.edges_with_endpoint_gap(0.0001)
        with (
            patch(
                "tubenest_engine.igs_converter._edge_points",
                side_effect=lambda edge, _api, **_kw: points[edge],
            ),
            patch("tubenest_engine.igs_converter._igs_log") as log,
        ):
            loops = _chain_cut_boundary(edges, {}, "test.igs", "outer")
        self.assertEqual(len(loops), 1)
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
