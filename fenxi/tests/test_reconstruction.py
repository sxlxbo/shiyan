from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_features import FusedFeatures  # noqa: E402
from reconstruction import ReconstructionError, reconstruct  # noqa: E402
from render_result import render_four_panel  # noqa: E402


RENDER = {
    "low_percentile": 1.0, "high_percentile": 99.0,
    "tone_map": "asinh", "gamma": 2.2,
}


def sample_fused(height: int = 8, width: int = 10, zero: bool = False) -> FusedFeatures:
    yy, xx = np.mgrid[:height, :width]
    base = np.zeros((height, width), np.float32) if zero else (
        ((xx - width / 2) ** 2 + (yy - height / 2) ** 2) < 10
    ).astype(np.float32) * 5
    zeros = {color: np.zeros_like(base) for color in "RGB"}
    magnitude = {"R": base * 3, "G": base * 2, "B": base * 0.2}
    return FusedFeatures(
        signed={color: image.copy() for color, image in magnitude.items()},
        magnitude=magnitude, off_signed=zeros, off_magnitude={k: v.copy() for k, v in zeros.items()},
        support=base > 0, valid_counts={c: 3 for c in "RGB"}, raw_counts={c: 3 for c in "RGB"},
        off_counts={c: 3 for c in "RGB"}, event_totals={c: 10 for c in "RGB"},
        off_event_totals={c: 10 for c in "RGB"}, background_event_rate={c: 0.0 for c in "RGB"},
        hot_pixel_count=0, rejected_samples=[],
    )


class ReconstructionTests(unittest.TestCase):
    def test_response_preserves_weak_b_and_four_panel_size(self):
        result = reconstruct(sample_fused(), "response", {"epsilon": 1e-6}, RENDER)
        self.assertLess(float(result.channel_float["B"].max()), float(result.channel_float["R"].max()))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.png"
            render_four_panel(result, output, titles=False)
            with Image.open(output) as image:
                self.assertEqual(image.size, (20, 16))

    def test_v2_rejects_log_ls(self):
        with self.assertRaises(ReconstructionError):
            reconstruct(sample_fused(), "log-ls", {"epsilon": 1e-6}, RENDER)

    def test_zero_events_do_not_create_nan(self):
        result = reconstruct(sample_fused(zero=True), "response", {"epsilon": 1e-6}, RENDER)
        self.assertTrue(np.isfinite(result.rgb_linear).all())
        self.assertEqual(int(result.rgb_uint8.max()), 0)


if __name__ == "__main__":
    unittest.main()
