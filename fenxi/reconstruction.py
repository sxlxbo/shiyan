"""将三个独立 BLACK→Color 响应重建为相对 RGB。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from calibration import Calibration, apply_calibration
from event_features import FusedFeatures
from protocol_parser import COLORS


class ReconstructionError(RuntimeError):
    pass


@dataclass
class ReconstructionResult:
    channel_float: dict[str, np.ndarray]
    channel_uint8: dict[str, np.ndarray]
    rgb_linear: np.ndarray
    rgb_uint8: np.ndarray
    normalization: dict[str, float | str]
    color_fidelity: str


def percentile_bounds(
    values: np.ndarray, low: float, high: float, positive_only: bool = True
) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if positive_only:
        finite = finite[finite > 0]
    if finite.size == 0:
        return 0.0, 1.0
    lo = float(np.percentile(finite, low))
    hi = float(np.percentile(finite, high))
    if hi <= lo:
        # 常量正响应也应显示出来；若以该常量同时作为黑点会得到全黑图。
        if hi > 0:
            lo = 0.0
        else:
            hi = 1.0
    return lo, hi


def tone_map_shared(
    channels: Mapping[str, np.ndarray], render_config: Mapping[str, float | str | bool]
) -> tuple[dict[str, np.ndarray], dict[str, float | str]]:
    low = float(render_config["low_percentile"])
    high = float(render_config["high_percentile"])
    combined = np.concatenate([np.ravel(channels[color]) for color in COLORS])
    lo, hi = percentile_bounds(combined, low, high)
    method = str(render_config["tone_map"])
    result: dict[str, np.ndarray] = {}
    for color in COLORS:
        normalized = np.clip((channels[color].astype(np.float32) - lo) / (hi - lo), 0, 1)
        if method == "asinh":
            normalized = np.arcsinh(5.0 * normalized) / np.arcsinh(5.0)
        elif method == "log1p":
            normalized = np.log1p(9.0 * normalized) / np.log(10.0)
        elif method != "linear":
            raise ReconstructionError(f"未知 tone_map：{method}")
        result[color] = normalized.astype(np.float32)
    return result, {"low": lo, "high": hi, "tone_map": method}


def reconstruct(
    fused: FusedFeatures,
    method: str,
    reconstruction_config: Mapping[str, float],
    render_config: Mapping[str, float | str | bool],
    calibration: Calibration | None = None,
) -> ReconstructionResult:
    if method != "response":
        raise ReconstructionError(
            "rgb-black-sync-v2 只支持 response；旧 log-ls 连续换色方程不适用于独立黑场响应"
        )
    channel_source = {
        color: np.where(fused.support, fused.magnitude[color], 0.0).astype(np.float32)
        for color in COLORS
    }
    channel_float, normalization = tone_map_shared(channel_source, render_config)
    channel_uint8 = {
        color: np.rint(channel_float[color] * 255).astype(np.uint8) for color in COLORS
    }

    rgb_linear = np.stack([channel_float[color] for color in COLORS], axis=-1)

    fidelity = "uncalibrated"
    gamma = float(render_config["gamma"])
    if calibration is not None:
        rgb_linear = apply_calibration(rgb_linear, calibration)
        gamma = calibration.gamma
        fidelity = "calibrated"
    rgb_linear = np.clip(rgb_linear, 0.0, 1.0).astype(np.float32)
    rgb_display = np.power(rgb_linear, 1.0 / gamma)
    rgb_uint8 = np.rint(np.clip(rgb_display, 0, 1) * 255).astype(np.uint8)
    normalization.update({"gamma": gamma, "method": method})

    return ReconstructionResult(
        channel_float=channel_float,
        channel_uint8=channel_uint8,
        rgb_linear=rgb_linear,
        rgb_uint8=rgb_uint8,
        normalization=normalization,
        color_fidelity=fidelity,
    )
