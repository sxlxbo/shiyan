from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol_parser import build_color_exposures  # noqa: E402
from test_protocol import make_states  # noqa: E402
from timing import estimate_v2_timing, nearest_trigger_indices  # noqa: E402
from synthetic_events import make_events  # noqa: E402


CONFIG = {
    "search_pre_us": 5_000, "search_post_us": 50_000, "bin_us": 1_000,
    "energy_fraction": 0.9, "window_min_us": -5_000, "window_max_us": 40_000,
}


class TimingTests(unittest.TestCase):
    def test_nearest_trigger_handles_negative_offsets(self):
        indices, relative = nearest_trigger_indices(
            np.asarray([98_000, 102_000, 199_000]), np.asarray([100_000, 200_000])
        )
        np.testing.assert_array_equal(indices, [0, 0, 1])
        np.testing.assert_array_equal(relative, [-2_000, 2_000, -1_000])

    def test_separates_color_black_and_per_color_peaks(self):
        exposures = build_color_exposures(make_states(4))
        offsets = {"R": 2_200, "G": 4_200, "B": 6_200}
        rows = []
        for item in exposures:
            rows.extend([(1, 1, 1, item.color_on_trigger.timestamp_us + offsets[item.color])] * 10)
            if item.color_off_trigger:
                rows.extend([(2, 2, 0, item.color_off_trigger.timestamp_us + 8_200)] * 10)
        result = estimate_v2_timing([make_events(rows)], exposures, CONFIG)
        self.assertTrue(1_500 <= result.color_on_by_color["R"].peak_offset_us <= 2_500)
        self.assertTrue(5_500 <= result.color_on_by_color["B"].peak_offset_us <= 6_500)
        self.assertTrue(7_500 <= result.color_off.peak_offset_us <= 8_500)


if __name__ == "__main__":
    unittest.main()
