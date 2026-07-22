"""按 ColorExposure 显式累计亮起、熄灭和黑场背景事件。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

from protocol_parser import COLORS, ColorExposure
from timing import TimingDiagnostics, TimingResult


class FeatureError(RuntimeError):
    pass


@dataclass
class FeatureSet:
    on_positive: np.ndarray
    on_negative: np.ndarray
    off_positive: np.ndarray
    off_negative: np.ndarray
    background_positive: np.ndarray
    background_negative: np.ndarray
    hot_pixel_mask: np.ndarray
    valid_samples: np.ndarray
    sample_metrics: list[dict[str, float | int | str | None]]
    rejection_reasons: list[list[str]]
    background_window_us: tuple[int, int]


@dataclass
class FusedFeatures:
    signed: dict[str, np.ndarray]
    magnitude: dict[str, np.ndarray]
    off_signed: dict[str, np.ndarray]
    off_magnitude: dict[str, np.ndarray]
    support: np.ndarray
    valid_counts: dict[str, int]
    raw_counts: dict[str, int]
    off_counts: dict[str, int]
    event_totals: dict[str, int]
    off_event_totals: dict[str, int]
    background_event_rate: dict[str, float]
    hot_pixel_count: int
    rejected_samples: list[dict[str, object]]


def _median3(image: np.ndarray) -> np.ndarray:
    padded = np.pad(image, 1, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return np.median(windows, axis=(-2, -1)).astype(np.float32)


def _spatial_filter(image: np.ndarray, name: str) -> np.ndarray:
    if name == "none":
        return image.astype(np.float32, copy=False)
    if name == "median3":
        return _median3(image)
    raise FeatureError(f"不支持的空间滤波器：{name}")


def _interval_assignment(
    timestamps: np.ndarray,
    trigger_times: np.ndarray,
    start_us: int,
    end_us: int,
) -> tuple[np.ndarray, np.ndarray]:
    """返回落入显式 [trigger+start, trigger+end) 窗口的事件及样本索引。"""
    if trigger_times.size == 0:
        return np.zeros(timestamps.size, dtype=bool), np.zeros(timestamps.size, dtype=np.int64)
    candidates = np.searchsorted(trigger_times, timestamps - start_us, side="right") - 1
    safe = np.clip(candidates, 0, trigger_times.size - 1)
    relative = timestamps - trigger_times[safe]
    selected = (candidates >= 0) & (relative >= start_us) & (relative < end_us)
    return selected, safe


def _add_events(
    destination_positive: np.ndarray,
    destination_negative: np.ndarray,
    selected: np.ndarray,
    sample_indices: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    polarity: np.ndarray,
) -> None:
    pos = selected & (polarity == 1)
    neg = selected & (polarity == 0)
    if np.any(pos):
        np.add.at(destination_positive, (sample_indices[pos], y[pos], x[pos]), 1)
    if np.any(neg):
        np.add.at(destination_negative, (sample_indices[neg], y[neg], x[neg]), 1)


def _robust_outliers(values: np.ndarray, limit: float) -> np.ndarray:
    if values.size < 3:
        return np.zeros(values.size, dtype=bool)
    center = float(np.median(values))
    mad = float(np.median(np.abs(values - center)))
    if mad == 0:
        return (values != center) if np.count_nonzero(values != center) == 1 else np.zeros(values.size, bool)
    return np.abs(values - center) / (1.4826 * mad) > limit


def accumulate_features(
    event_chunks: Iterable[np.ndarray],
    exposures: Sequence[ColorExposure],
    sensor_size: tuple[int, int],
    timing: TimingDiagnostics,
    timing_config: Mapping[str, int | float],
    filter_config: Mapping[str, int | float | str],
) -> FeatureSet:
    height, width = sensor_size
    count = len(exposures)
    shape = (count, height, width)
    on_pos = np.zeros(shape, dtype=np.uint32)
    on_neg = np.zeros(shape, dtype=np.uint32)
    off_pos = np.zeros(shape, dtype=np.uint32)
    off_neg = np.zeros(shape, dtype=np.uint32)
    bg_pos = np.zeros(shape, dtype=np.uint32)
    bg_neg = np.zeros(shape, dtype=np.uint32)
    on_times = np.asarray([item.color_on_trigger.timestamp_us for item in exposures], dtype=np.int64)
    off_pairs = [(i, item.color_off_trigger) for i, item in enumerate(exposures) if item.color_off_trigger]
    off_sample_indices = np.asarray([item[0] for item in off_pairs], dtype=np.int64)
    off_times = np.asarray([item[1].timestamp_us for item in off_pairs], dtype=np.int64)
    background_window = (
        int(timing_config["background_start_us"]),
        int(timing_config["background_end_us"]),
    )
    earliest_on = min(
        [timing.color_on.window_start_us]
        + [item.window_start_us for item in timing.color_on_by_color.values() if item is not None]
    )
    if not background_window[0] < background_window[1] < earliest_on:
        raise FeatureError("黑场背景窗必须严格早于 COLOR_RISE 响应窗")

    for events in event_chunks:
        timestamps = np.asarray(events["t"], dtype=np.int64)
        x = np.asarray(events["x"], dtype=np.int64)
        y = np.asarray(events["y"], dtype=np.int64)
        polarity = np.asarray(events["p"], dtype=np.int8)
        coordinates_ok = (x >= 0) & (x < width) & (y >= 0) & (y < height)

        # 分色窗口直接参与累计；公共窗口只作为整体诊断和缺失分色曲线时的回退。
        for color in COLORS:
            color_indices = np.asarray(
                [i for i, item in enumerate(exposures) if item.color == color], dtype=np.int64
            )
            color_times = on_times[color_indices]
            color_timing = timing.color_on_by_color.get(color) or timing.color_on
            selected, compact_indices = _interval_assignment(
                timestamps,
                color_times,
                color_timing.window_start_us,
                color_timing.window_end_us,
            )
            sample_indices = color_indices[compact_indices]
            _add_events(
                on_pos, on_neg, selected & coordinates_ok, sample_indices, x, y, polarity
            )

        selected, sample_indices = _interval_assignment(
            timestamps, on_times, background_window[0], background_window[1]
        )
        _add_events(bg_pos, bg_neg, selected & coordinates_ok, sample_indices, x, y, polarity)

        if off_times.size and timing.color_off is not None:
            selected, compact_indices = _interval_assignment(
                timestamps,
                off_times,
                timing.color_off.window_start_us,
                timing.color_off.window_end_us,
            )
            sample_indices = off_sample_indices[compact_indices]
            _add_events(off_pos, off_neg, selected & coordinates_ok, sample_indices, x, y, polarity)

    background_total = (bg_pos + bg_neg).sum(axis=0).astype(np.float64)
    median = float(np.median(background_total))
    mad = float(np.median(np.abs(background_total - median)))
    threshold = max(
        float(filter_config["hot_pixel_min_events"]),
        median + float(filter_config["hot_pixel_mad"]) * max(1.0, 1.4826 * mad),
    )
    hot_mask = background_total > threshold

    on_totals = (on_pos + on_neg).reshape(count, -1).sum(axis=1).astype(np.float64)
    off_totals = (off_pos + off_neg).reshape(count, -1).sum(axis=1).astype(np.float64)
    bg_totals = (bg_pos + bg_neg).reshape(count, -1).sum(axis=1).astype(np.float64)
    positive_totals = on_pos.reshape(count, -1).sum(axis=1).astype(np.float64)
    valid = np.ones(count, dtype=bool)
    reasons: list[list[str]] = [[] for _ in exposures]
    limit = float(filter_config["cycle_outlier_mad"])
    for color in COLORS:
        group = np.asarray([i for i, item in enumerate(exposures) if item.color == color])
        for values, reason in ((on_totals[group], "on_event_total"), (bg_totals[group], "background_rate")):
            flags = _robust_outliers(values, limit)
            for index in group[flags]:
                reasons[int(index)].append(reason)
                valid[int(index)] = False

    metrics: list[dict[str, float | int | str | None]] = []
    for i, exposure in enumerate(exposures):
        total = on_totals[i]
        weights = (on_pos[i] + on_neg[i]).astype(np.float64)
        yy, xx = np.indices((height, width))
        metrics.append(
            {
                "cycle_index": exposure.cycle_index,
                "phase_index": exposure.phase_index,
                "color": exposure.color,
                "on_events": int(total),
                "off_events": int(off_totals[i]),
                "positive_ratio": float(positive_totals[i] / total) if total else None,
                "centroid_x": float((weights * xx).sum() / total) if total else None,
                "centroid_y": float((weights * yy).sum() / total) if total else None,
                "background_events": int(bg_totals[i]),
                "on_off_ratio": float(total / off_totals[i]) if off_totals[i] else None,
            }
        )
    return FeatureSet(
        on_pos, on_neg, off_pos, off_neg, bg_pos, bg_neg, hot_mask, valid,
        metrics, reasons, background_window,
    )


def _fuse_stack(
    positive: np.ndarray,
    negative: np.ndarray,
    background_positive: np.ndarray,
    background_negative: np.ndarray,
    indices: np.ndarray,
    background_scale: float,
) -> tuple[np.ndarray, np.ndarray]:
    pos = np.maximum(
        positive[indices].astype(np.float32)
        - background_positive[indices].astype(np.float32) * background_scale,
        0,
    )
    neg = np.maximum(
        negative[indices].astype(np.float32)
        - background_negative[indices].astype(np.float32) * background_scale,
        0,
    )
    pos_fused = np.median(pos, axis=0)
    neg_fused = np.median(neg, axis=0)
    return pos_fused - neg_fused, pos_fused + neg_fused


def fuse_features(
    features: FeatureSet,
    exposures: Sequence[ColorExposure],
    timing: TimingDiagnostics,
    filter_config: Mapping[str, int | float | str],
) -> FusedFeatures:
    bg_duration = features.background_window_us[1] - features.background_window_us[0]
    off_duration = (
        timing.color_off.window_end_us - timing.color_off.window_start_us
        if timing.color_off is not None else 0
    )
    filter_name = str(filter_config["spatial_filter"])
    signed: dict[str, np.ndarray] = {}
    magnitude: dict[str, np.ndarray] = {}
    off_signed: dict[str, np.ndarray] = {}
    off_magnitude: dict[str, np.ndarray] = {}
    valid_counts: dict[str, int] = {}
    raw_counts: dict[str, int] = {}
    off_counts: dict[str, int] = {}
    event_totals: dict[str, int] = {}
    off_event_totals: dict[str, int] = {}
    background_rate: dict[str, float] = {}

    for color in COLORS:
        color_timing = timing.color_on_by_color.get(color) or timing.color_on
        on_duration = color_timing.window_end_us - color_timing.window_start_us
        raw = np.asarray([i for i, item in enumerate(exposures) if item.color == color])
        indices = raw[features.valid_samples[raw]]
        if indices.size == 0:
            raise FeatureError(f"颜色 {color} 没有可用亮起样本")
        on_s, on_a = _fuse_stack(
            features.on_positive, features.on_negative,
            features.background_positive, features.background_negative,
            indices, on_duration / max(1, bg_duration),
        )
        off_indices = np.asarray([
            i for i in indices if exposures[int(i)].color_off_trigger is not None
        ], dtype=np.int64)
        if off_indices.size and timing.color_off is not None:
            off_s, off_a = _fuse_stack(
                features.off_positive, features.off_negative,
                features.background_positive, features.background_negative,
                off_indices, off_duration / max(1, bg_duration),
            )
        else:
            off_s = np.zeros_like(on_s)
            off_a = np.zeros_like(on_a)
        for image in (on_s, on_a, off_s, off_a):
            image[features.hot_pixel_mask] = 0
        signed[color] = _spatial_filter(on_s, filter_name)
        magnitude[color] = _spatial_filter(on_a, filter_name)
        off_signed[color] = _spatial_filter(off_s, filter_name)
        off_magnitude[color] = _spatial_filter(off_a, filter_name)
        raw_counts[color] = int(raw.size)
        valid_counts[color] = int(indices.size)
        off_counts[color] = int(off_indices.size)
        event_totals[color] = int(sum(features.sample_metrics[int(i)]["on_events"] for i in indices))
        off_event_totals[color] = int(sum(features.sample_metrics[int(i)]["off_events"] for i in off_indices))
        background_rate[color] = float(
            sum(features.sample_metrics[int(i)]["background_events"] for i in indices)
            / max(1, indices.size * bg_duration)
        )

    support = sum(magnitude[color] for color in COLORS).astype(np.float32)
    positive = support[support > 0]
    threshold = float(np.percentile(positive, 10)) if positive.size else 0.0
    support = (support >= threshold) & (support > 0)
    rejected = [
        {
            "cycle_index": exposures[i].cycle_index,
            "phase_index": exposures[i].phase_index,
            "color": exposures[i].color,
            "reasons": features.rejection_reasons[i],
        }
        for i in range(len(exposures)) if not features.valid_samples[i]
    ]
    return FusedFeatures(
        signed, magnitude, off_signed, off_magnitude, support,
        valid_counts, raw_counts, off_counts, event_totals, off_event_totals,
        background_rate, int(features.hot_pixel_mask.sum()), rejected,
    )
