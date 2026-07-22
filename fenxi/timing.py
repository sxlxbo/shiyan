"""分别估计 BLACK→Color 亮起与 Color→BLACK 熄灭响应窗口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

import numpy as np

from protocol_parser import COLORS, ColorExposure, StateTrigger


class TimingError(RuntimeError):
    pass


@dataclass(frozen=True)
class TimingResult:
    bin_edges_us: np.ndarray
    event_counts: np.ndarray
    peak_offset_us: int
    window_start_us: int
    window_end_us: int
    label: str = "response"

    def as_dict(self) -> dict[str, int | str]:
        return {
            "label": self.label,
            "peak_offset_us": self.peak_offset_us,
            "window_start_us": self.window_start_us,
            "window_end_us": self.window_end_us,
            "profile_total_events": int(self.event_counts.sum()),
        }


@dataclass(frozen=True)
class TimingDiagnostics:
    color_on: TimingResult
    color_on_by_color: dict[str, TimingResult | None]
    color_off: TimingResult | None

    def as_dict(self) -> dict[str, object]:
        return {
            "color_on": self.color_on.as_dict(),
            "color_on_by_color": {
                color: None if item is None else item.as_dict()
                for color, item in self.color_on_by_color.items()
            },
            "color_off": None if self.color_off is None else self.color_off.as_dict(),
        }


def nearest_trigger_indices(
    timestamps: np.ndarray, trigger_times: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if trigger_times.size == 0:
        raise TimingError("没有可用于对齐的 Trigger")
    right = np.searchsorted(trigger_times, timestamps, side="left")
    left = np.clip(right - 1, 0, trigger_times.size - 1)
    right_clipped = np.clip(right, 0, trigger_times.size - 1)
    choose_right = (
        np.abs(timestamps - trigger_times[right_clipped])
        < np.abs(timestamps - trigger_times[left])
    )
    indices = np.where(choose_right, right_clipped, left)
    relative = timestamps - trigger_times[indices]
    return indices.astype(np.int64, copy=False), relative.astype(np.int64, copy=False)


def _profile_edges(config: Mapping[str, int | float]) -> np.ndarray:
    pre = int(config["search_pre_us"])
    post = int(config["search_post_us"])
    bin_us = int(config["bin_us"])
    if pre < 0 or post <= 0 or bin_us <= 0:
        raise TimingError("时序搜索参数无效")
    edges = np.arange(-pre, post + bin_us, bin_us, dtype=np.int64)
    if edges[-1] < post:
        edges = np.append(edges, post)
    return edges


def build_event_rate_profiles(
    event_chunks: Iterable[np.ndarray],
    trigger_groups: Mapping[str, Sequence[StateTrigger]],
    timing_config: Mapping[str, int | float],
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """单次流式扫描同时建立多组 Trigger 的相对事件率曲线。"""
    edges = _profile_edges(timing_config)
    pre, post = int(timing_config["search_pre_us"]), int(timing_config["search_post_us"])
    times = {
        name: np.asarray([item.timestamp_us for item in items], dtype=np.int64)
        for name, items in trigger_groups.items()
    }
    counts = {name: np.zeros(edges.size - 1, dtype=np.int64) for name in trigger_groups}
    for events in event_chunks:
        timestamps = np.asarray(events["t"], dtype=np.int64)
        for name, trigger_times in times.items():
            if trigger_times.size == 0:
                continue
            _, relative = nearest_trigger_indices(timestamps, trigger_times)
            selected = relative[(relative >= -pre) & (relative < post)]
            if selected.size:
                counts[name] += np.histogram(selected, bins=edges)[0]
    return edges, counts


def select_response_window(
    edges: np.ndarray,
    counts: np.ndarray,
    timing_config: Mapping[str, int | float],
    label: str = "response",
) -> TimingResult:
    if counts.size == 0 or counts.sum() == 0:
        raise TimingError(f"{label} Trigger 附近没有 CD 事件，无法确定响应窗口")
    baseline = float(np.percentile(counts, 20))
    energy = np.maximum(counts.astype(np.float64) - baseline, 0.0)
    if energy.sum() <= 0:
        energy = counts.astype(np.float64)
    peak_index = int(np.argmax(energy))
    peak_offset = int((int(edges[peak_index]) + int(edges[peak_index + 1])) // 2)
    fraction = float(timing_config["energy_fraction"])
    if not 0 < fraction <= 1:
        raise TimingError("energy_fraction 必须位于 (0, 1]")
    target = float(energy.sum()) * fraction
    left = right = peak_index
    accumulated = float(energy[peak_index])
    while accumulated < target and (left > 0 or right < energy.size - 1):
        left_value = energy[left - 1] if left > 0 else -1.0
        right_value = energy[right + 1] if right < energy.size - 1 else -1.0
        if right_value > left_value:
            right += 1
            accumulated += float(energy[right])
        else:
            left -= 1
            accumulated += float(energy[left])
    start = max(int(timing_config["window_min_us"]), int(edges[left]))
    end = min(int(timing_config["window_max_us"]), int(edges[right + 1]))
    if start >= end:
        raise TimingError(f"{label} 自动选择的响应窗口为空")
    if peak_index in {0, energy.size - 1}:
        raise TimingError(f"{label} 响应峰落在搜索边界，请扩大搜索范围")
    return TimingResult(edges, counts, peak_offset, start, end, label)


def estimate_v2_timing(
    event_chunks: Iterable[np.ndarray],
    exposures: Sequence[ColorExposure],
    timing_config: Mapping[str, int | float],
) -> TimingDiagnostics:
    on = [item.color_on_trigger for item in exposures]
    groups: dict[str, Sequence[StateTrigger]] = {"COLOR": on}
    for color in COLORS:
        groups[color] = [item.color_on_trigger for item in exposures if item.color == color]
    groups["BLACK"] = [
        item.color_off_trigger for item in exposures if item.color_off_trigger is not None
    ]
    edges, counts = build_event_rate_profiles(event_chunks, groups, timing_config)
    common = select_response_window(edges, counts["COLOR"], timing_config, "COLOR_RISE")
    by_color: dict[str, TimingResult | None] = {}
    for color in COLORS:
        by_color[color] = (
            select_response_window(edges, counts[color], timing_config, f"{color}_RISE")
            if counts[color].sum()
            else None
        )
    off = (
        select_response_window(edges, counts["BLACK"], timing_config, "BLACK_RISE")
        if counts["BLACK"].sum()
        else None
    )
    return TimingDiagnostics(common, by_color, off)


# 保留一个小型通用入口，便于单组时序测试与第三方调用。
def estimate_timing(
    event_chunks: Iterable[np.ndarray],
    triggers: Sequence[StateTrigger],
    timing_config: Mapping[str, int | float],
) -> TimingResult:
    edges, counts = build_event_rate_profiles(event_chunks, {"response": triggers}, timing_config)
    return select_response_window(edges, counts["response"], timing_config)
