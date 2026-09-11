#!/usr/bin/env python3
"""从 mono-black-sync-v1 RAW 时间流重建单色事件响应图。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


PROTOCOL = "mono-black-sync-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="堆叠 GenX320 单色闪烁响应")
    parser.add_argument("raw", type=Path, help="capture_mono.py 生成的 .raw")
    parser.add_argument("--metadata", type=Path, help="同名 JSON；默认自动查找")
    parser.add_argument("--output", type=Path, help="输出 PNG；默认写入本目录 output/")
    parser.add_argument("--window-start-ms", type=float, default=1.0)
    parser.add_argument("--window-end-ms", type=float, default=40.0)
    parser.add_argument("--low-percentile", type=float, default=1.0)
    parser.add_argument("--high-percentile", type=float, default=99.5)
    parser.add_argument("--no-median", action="store_true", help="关闭 3x3 中值滤波")
    return parser.parse_args()


def load_metadata(raw: Path, metadata_arg: Path | None) -> tuple[Path, dict]:
    path = metadata_arg or raw.with_suffix(".json")
    if not path.is_file():
        raise RuntimeError(f"找不到元数据: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("protocol") != PROTOCOL:
        raise RuntimeError(f"协议应为 {PROTOCOL}，实际为 {data.get('protocol')!r}")
    return path, data


def raw_reader_class():
    try:
        from metavision_core.event_io.raw_reader import RawReader
    except ImportError as exc:
        raise RuntimeError("无法导入 Metavision SDK，需在 SDK 环境分析 .raw") from exc
    return RawReader


def read_size_and_triggers(raw: Path, channel: int) -> tuple[tuple[int, int], np.ndarray]:
    reader = raw_reader_class()(str(raw))
    rises: list[int] = []
    try:
        height, width = map(int, reader.get_size())
        while not reader.is_done():
            reader.load_delta_t(100_000)
            trigger_events = reader.get_ext_trigger_events()
            for event in trigger_events:
                if int(event["id"]) == channel and int(event["p"]) == 1:
                    rises.append(int(event["t"]))
            if len(trigger_events):
                reader.clear_ext_trigger_events()
    finally:
        del reader
    return (height, width), np.asarray(rises, dtype=np.int64)


def select_trigger_sequence(rises: np.ndarray, expected: int, hold_us: int) -> np.ndarray:
    """从可能含少量首尾杂波的 Trigger 中选择最符合固定周期的一段。"""
    if rises.size < expected:
        raise RuntimeError(f"Trigger 上升沿不足: {rises.size}，预期 {expected}")
    if rises.size == expected:
        selected = rises
    else:
        scores = []
        for start in range(rises.size - expected + 1):
            candidate = rises[start : start + expected]
            scores.append(float(np.median(np.abs(np.diff(candidate) - hold_us))))
        best = int(np.argmin(scores))
        selected = rises[best : best + expected]
    tolerance = max(20_000, int(hold_us * 0.25))
    errors = np.abs(np.diff(selected) - hold_us)
    if np.any(errors > tolerance):
        raise RuntimeError(
            f"Trigger 周期异常，最大误差 {int(errors.max())} us，容差 {tolerance} us"
        )
    return selected


def robust_valid_cycles(totals: np.ndarray) -> np.ndarray:
    if totals.size < 4:
        return np.ones(totals.size, dtype=bool)
    center = float(np.median(totals))
    mad = float(np.median(np.abs(totals - center)))
    if mad == 0:
        return totals > 0
    score = np.abs(totals - center) / (1.4826 * mad)
    return (score <= 5.0) & (totals > 0)


def accumulate_windows(
    raw: Path,
    sensor_size: tuple[int, int],
    mono_triggers: np.ndarray,
    start_us: int,
    end_us: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = sensor_size
    positive = np.zeros((mono_triggers.size, height, width), dtype=np.uint16)
    negative = np.zeros_like(positive)
    reader = raw_reader_class()(str(raw))
    try:
        while not reader.is_done():
            events = reader.load_delta_t(50_000)
            if not len(events):
                continue
            t = np.asarray(events["t"], dtype=np.int64)
            x = np.asarray(events["x"], dtype=np.int64)
            y = np.asarray(events["y"], dtype=np.int64)
            p = np.asarray(events["p"], dtype=np.int8)
            indices = np.searchsorted(mono_triggers, t - start_us, side="right") - 1
            safe = np.clip(indices, 0, mono_triggers.size - 1)
            relative = t - mono_triggers[safe]
            valid = (
                (indices >= 0) & (relative >= start_us) & (relative < end_us)
                & (x >= 0) & (x < width) & (y >= 0) & (y < height)
            )
            pos = valid & (p == 1)
            neg = valid & (p == 0)
            if np.any(pos):
                np.add.at(positive, (safe[pos], y[pos], x[pos]), 1)
            if np.any(neg):
                np.add.at(negative, (safe[neg], y[neg], x[neg]), 1)
    finally:
        del reader
    totals = (positive.astype(np.uint32) + negative).sum(axis=(1, 2))
    return positive, negative, totals


def median3(image: np.ndarray) -> np.ndarray:
    padded = np.pad(image, 1, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return np.median(windows, axis=(-2, -1)).astype(np.float32)


def tone_map(image: np.ndarray, low: float, high: float) -> np.ndarray:
    finite = image[np.isfinite(image) & (image > 0)]
    if finite.size == 0:
        raise RuntimeError("选定窗口内没有可重建事件；检查照明、Trigger 和窗口参数")
    lo, hi = np.percentile(finite, [low, high])
    if hi <= lo:
        lo, hi = 0.0, max(1.0, float(hi))
    normalized = np.clip((image - lo) / (hi - lo), 0, 1)
    normalized = np.arcsinh(5 * normalized) / np.arcsinh(5)
    return np.rint(normalized * 255).astype(np.uint8)


def save_png(path: Path, gray: np.ndarray) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("缺少 Pillow；请执行 pip install Pillow") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(gray, mode="L").save(path)


def run(args: argparse.Namespace) -> Path:
    raw = args.raw.expanduser().resolve()
    if not raw.is_file() or raw.suffix.lower() != ".raw":
        raise RuntimeError(f"无效 RAW 文件: {raw}")
    if not 0 <= args.window_start_ms < args.window_end_ms:
        raise ValueError("窗口必须满足 0 <= start < end")
    if not 0 <= args.low_percentile < args.high_percentile <= 100:
        raise ValueError("百分位参数无效")

    metadata_path, metadata = load_metadata(raw, args.metadata)
    channel = int(metadata["trigger_channel"])
    cycles = int(metadata["cycles"])
    hold_us = round(float(metadata["hold_ms"]) * 1000)
    if round(args.window_end_ms * 1000) >= hold_us:
        raise ValueError(
            f"--window-end-ms 必须小于 BLACK 切换时刻 {hold_us / 1000:.1f} ms，"
            "否则会混入熄灯响应"
        )
    sensor_size, rises = read_size_and_triggers(raw, channel)
    transitions = select_trigger_sequence(rises, cycles * 2, hold_us)
    mono_triggers = transitions[0::2]
    start_us = round(args.window_start_ms * 1000)
    end_us = round(args.window_end_ms * 1000)
    positive, negative, totals = accumulate_windows(
        raw, sensor_size, mono_triggers, start_us, end_us
    )
    valid = robust_valid_cycles(totals)
    if not np.any(valid):
        raise RuntimeError("所有亮起周期均为空或被判为异常")

    # 均值保留稀疏边缘；周期级 MAD 剔除手抖、遮挡等爆发异常。
    magnitude = (positive[valid].astype(np.float32) + negative[valid]).mean(axis=0)
    if not args.no_median:
        magnitude = median3(magnitude)
    gray = tone_map(magnitude, args.low_percentile, args.high_percentile)

    output = args.output
    if output is None:
        output = Path(__file__).parent / "output" / f"{raw.stem}_stack.png"
    output = output.expanduser().resolve()
    save_png(output, gray)
    nonzero = int(np.count_nonzero(magnitude))
    report = {
        "protocol": PROTOCOL,
        "raw": str(raw),
        "metadata": str(metadata_path.resolve()),
        "output": str(output),
        "sensor_size": {"height": sensor_size[0], "width": sensor_size[1]},
        "color": metadata["color"],
        "cycles_expected": cycles,
        "cycles_used": int(valid.sum()),
        "cycles_rejected": int((~valid).sum()),
        "window_us": [start_us, end_us],
        "events_per_cycle": [int(value) for value in totals],
        "event_total_used": int(totals[valid].sum()),
        "nonzero_pixels_after_filter": nonzero,
        "nonzero_pixel_ratio": nonzero / magnitude.size,
        "warning": (
            "有效像素很少；优先检查灯光覆盖、焦距与 Trigger 时序"
            if nonzero / magnitude.size < 0.005 else None
        ),
    }
    report_path = output.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"重建完成: {output}")
    print(f"质量报告: {report_path}")
    print(f"有效周期: {valid.sum()}/{cycles}，有效像素占比: {nonzero / magnitude.size:.2%}")
    return output


if __name__ == "__main__":
    try:
        run(parse_args())
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"重建失败: {exc}") from exc
