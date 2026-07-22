from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_features import accumulate_features, fuse_features  # noqa: E402
from protocol_parser import build_color_exposures  # noqa: E402
from test_protocol import make_states  # noqa: E402
from synthetic_events import make_events  # noqa: E402
from timing import TimingDiagnostics, TimingResult  # noqa: E402


def profile(label: str, start: int = 0, end: int = 5_000) -> TimingResult:
    return TimingResult(
        np.asarray([-5_000, 0, 5_000]), np.asarray([0, 10]), 2_000,
        start, end, label,
    )


class FeatureTests(unittest.TestCase):
    def test_windows_do_not_crosstalk_and_final_b_has_no_off(self):
        exposures = build_color_exposures(make_states(3))
        timing = TimingDiagnostics(
            profile("COLOR"), {color: profile(color) for color in "RGB"}, profile("BLACK")
        )
        rows = []
        for item in exposures:
            on = item.color_on_trigger.timestamp_us
            rows.extend([(1, 1, 1, on + 2_000)] * 3)
            rows.extend([(2, 2, 0, on - 20_000)] * 2)
            rows.append((3, 3, 1, on + 5_000))  # 响应窗上界，必须排除
            if item.color_off_trigger:
                rows.extend([(1, 2, 0, item.color_off_trigger.timestamp_us + 2_000)] * 4)
        filter_config = {
            "hot_pixel_mad": 8.0, "hot_pixel_min_events": 1000,
            "cycle_outlier_mad": 5.0, "spatial_filter": "none",
        }
        features = accumulate_features(
            [make_events(rows)], exposures, (4, 4), timing,
            {"background_start_us": -50_000, "background_end_us": -10_000},
            filter_config,
        )
        self.assertEqual(int(features.on_positive[:, 1, 1].sum()), 27)
        self.assertEqual(int(features.background_negative[:, 2, 2].sum()), 18)
        self.assertEqual(int(features.on_positive[:, 3, 3].sum()), 0)
        self.assertEqual(int(features.off_negative[:, 2, 1].sum()), 32)
        fused = fuse_features(features, exposures, timing, filter_config)
        self.assertEqual(fused.valid_counts, {"R": 3, "G": 3, "B": 3})
        self.assertEqual(fused.off_counts, {"R": 3, "G": 3, "B": 2})

    def test_single_outlier_only_rejects_its_color(self):
        exposures = build_color_exposures(make_states(5))
        timing = TimingDiagnostics(
            profile("COLOR"), {color: profile(color) for color in "RGB"}, None
        )
        rows = []
        for i, item in enumerate(exposures):
            repeats = 100 if item.color == "G" and item.cycle_index == 2 else 3
            rows.extend([(1, 1, 1, item.color_on_trigger.timestamp_us + 2_000)] * repeats)
        config = {
            "hot_pixel_mad": 8.0, "hot_pixel_min_events": 1000,
            "cycle_outlier_mad": 5.0, "spatial_filter": "none",
        }
        features = accumulate_features(
            [make_events(rows)], exposures, (3, 3), timing,
            {"background_start_us": -50_000, "background_end_us": -10_000}, config,
        )
        rejected = [i for i, valid in enumerate(features.valid_samples) if not valid]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(exposures[rejected[0]].color, "G")


if __name__ == "__main__":
    unittest.main()
