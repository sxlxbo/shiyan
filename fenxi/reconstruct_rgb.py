#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GenX320 RGB 主动照明 RAW 重建命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from calibration import load_calibration
from event_features import accumulate_features, fuse_features
from protocol_parser import (
    COLORS,
    ProtocolError,
    build_color_exposures,
    load_state_metadata,
    match_metadata_to_raw,
    validate_minimum_cycles,
    validate_state_periods,
)
from raw_decoder import iter_cd_event_chunks, read_all_triggers, read_sensor_size
from reconstruction import reconstruct
from render_result import render_four_panel, render_timing_profile
from timing import estimate_v2_timing


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "config.yaml"
DEFAULT_INPUT_DIR = SCRIPT_DIR / "input"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 GenX320 rgb-black-sync-v2 RAW 重建为 R/G/B/RGB 四宫格图片"
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="输入 .raw；省略时自动使用 fenxi/input 中唯一的 RAW",
    )
    parser.add_argument("--metadata", help="同名 rgb-black-sync-v2 CSV（当前为必需输入）")
    parser.add_argument("--output", help="输出 PNG；默认写入 fenxi/output")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="YAML 配置文件")
    parser.add_argument(
        "--method", choices=("response",), default="response", help="v2 独立黑场响应重建"
    )
    parser.add_argument("--calibration", help="可选 calibration.json")
    parser.add_argument("--no-titles", action="store_true", help="不在四格内部绘制标题")
    parser.add_argument("--save-features", action="store_true", help="保存融合后的浮点 NPZ")
    return parser.parse_args(argv)


def discover_raw(input_argument: str | None) -> Path:
    if input_argument:
        return Path(input_argument).expanduser().resolve()
    candidates = sorted(DEFAULT_INPUT_DIR.glob("*.raw"))
    if not candidates:
        raise FileNotFoundError(f"{DEFAULT_INPUT_DIR} 中没有 RAW 文件")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise RuntimeError(f"input 中有多个 RAW，请在命令行指定一个：{names}")
    return candidates[0].resolve()


def resolve_metadata(raw_path: Path, argument: str | None) -> Path | None:
    if argument:
        path = Path(argument).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"CSV 文件不存在：{path}")
        return path
    candidate = raw_path.with_suffix(".csv")
    return candidate if candidate.is_file() else None


def resolve_output(raw_path: Path, argument: str | None) -> Path:
    if argument:
        target = Path(argument).expanduser().resolve()
    else:
        target = DEFAULT_OUTPUT_DIR / f"{raw_path.stem}_reconstruction.png"
    if target.suffix.lower() != ".png":
        raise ValueError("输出文件扩展名必须是 .png")
    return target


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    required = ("protocol", "timing", "filter", "reconstruction", "render")
    if not isinstance(config, dict) or any(key not in config for key in required):
        raise ValueError(f"配置文件缺少必要部分：{', '.join(required)}")
    return config


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def run(args: argparse.Namespace) -> tuple[Path, Path]:
    raw_path = discover_raw(args.input)
    metadata_path = resolve_metadata(raw_path, args.metadata)
    if metadata_path is None:
        raise ProtocolError(
            "rgb-black-sync-v2 当前要求同名 CSV；RAW 本身不能证明照明协议版本，禁止静默猜测"
        )
    output_path = resolve_output(raw_path, args.output)
    config = load_config(args.config)
    protocol = config["protocol"]
    warnings: list[str] = []

    print(f"[1/6] 读取 RAW Trigger：{raw_path}")
    sensor_size = read_sensor_size(raw_path)
    raw_triggers = read_all_triggers(raw_path)
    print(f"[2/6] 校验 v2 CSV 与六状态周期：{metadata_path}")
    state_triggers = load_state_metadata(
        metadata_path,
        int(protocol["channel_id"]),
        str(protocol["version"]),
    )
    match_metadata_to_raw(
        state_triggers,
        raw_triggers,
        int(protocol["channel_id"]),
        int(protocol["csv_match_tolerance_us"]),
    )
    validate_state_periods(
        state_triggers,
        int(protocol["state_interval_us"]),
        int(protocol["state_tolerance_us"]),
    )
    exposures = build_color_exposures(state_triggers)
    metadata_mode = "csv"

    complete_cycles = validate_minimum_cycles(
        state_triggers, int(protocol["minimum_complete_cycles"])
    )
    expected_pulses = int(protocol["expected_state_pulses"])
    if len(state_triggers) != expected_pulses:
        warnings.append(
            f"正式状态 Trigger 为 {len(state_triggers)}，配置预期为 {expected_pulses}"
        )
    if complete_cycles != int(protocol["expected_complete_cycles"]):
        warnings.append(
            f"完整六状态周期为 {complete_cycles}，配置预期为 {protocol['expected_complete_cycles']}"
        )
    for color in COLORS:
        color_count = sum(item.color == color for item in exposures)
        if color_count != int(protocol["expected_color_samples"]):
            warnings.append(
                f"{color} 亮起样本为 {color_count}，配置预期为 {protocol['expected_color_samples']}"
            )

    print("[3/6] 估计 LED 光学响应相对 Trigger 的时间窗口")
    timing = estimate_v2_timing(iter_cd_event_chunks(raw_path), exposures, config["timing"])
    if timing.color_on.peak_offset_us < 0:
        warnings.append(
            f"亮起响应峰比 Trigger 早 {-timing.color_on.peak_offset_us} us，已启用负偏移窗口"
        )
    peak_warning = int(config["timing"]["color_peak_warning_us"])
    for color, color_timing in timing.color_on_by_color.items():
        if color_timing and abs(color_timing.peak_offset_us - timing.color_on.peak_offset_us) > peak_warning:
            warnings.append(
                f"{color} 峰值偏离公共亮起窗口中心：{color_timing.peak_offset_us} us"
            )

    print("[4/6] 累计正/负事件并融合重复 RGB 周期")
    features = accumulate_features(
        iter_cd_event_chunks(raw_path),
        exposures,
        sensor_size,
        timing,
        config["timing"],
        config["filter"],
    )
    fused = fuse_features(features, exposures, timing, config["filter"])
    for color, valid_count in fused.valid_counts.items():
        total_count = fused.raw_counts[color]
        if valid_count < 0.8 * total_count:
            warnings.append(f"{color} 通道只保留 {valid_count}/{total_count} 个亮起样本")
        if valid_count < int(protocol["minimum_complete_cycles"]):
            raise ProtocolError(f"{color} 只有 {valid_count} 个有效样本，无法重建")

    calibration = load_calibration(args.calibration) if args.calibration else None
    print(f"[5/6] 使用 {args.method} 方法重建 RGB")
    result = reconstruct(
        fused,
        args.method,
        config["reconstruction"],
        config["render"],
        calibration,
    )

    print("[6/6] 输出四宫格 PNG、时序图和 JSON 报告")
    render_four_panel(
        result,
        output_path,
        titles=bool(config["render"].get("titles", True)) and not args.no_titles,
    )
    timing_path = output_path.with_name(f"{output_path.stem}_timing.png")
    render_timing_profile(timing, timing_path)
    report_path = output_path.with_suffix(".report.json")
    report = {
        "created_at": datetime.now().astimezone().isoformat(),
        "input_raw": str(raw_path),
        "metadata": str(metadata_path),
        "metadata_mode": metadata_mode,
        "protocol_version": protocol["version"],
        "output_png": str(output_path),
        "timing_png": str(timing_path),
        "sensor": {"height": sensor_size[0], "width": sensor_size[1]},
        "raw_trigger_events": len(raw_triggers),
        "state_trigger_count": len(state_triggers),
        "black_rise_count": sum(item.role == "BLACK_RISE" for item in state_triggers),
        "color_rise_count": len(exposures),
        "complete_six_state_cycles": complete_cycles,
        "timing": timing.as_dict(),
        "background_window_us": list(features.background_window_us),
        "raw_color_samples": fused.raw_counts,
        "valid_color_samples": fused.valid_counts,
        "off_response_samples": fused.off_counts,
        "on_event_totals": fused.event_totals,
        "off_event_totals": fused.off_event_totals,
        "on_off_ratios": {
            color: fused.event_totals[color] / fused.off_event_totals[color]
            if fused.off_event_totals[color] else None for color in COLORS
        },
        "background_event_rate_per_us": fused.background_event_rate,
        "effective_pixels": {
            color: int(np.count_nonzero(fused.magnitude[color])) for color in COLORS
        },
        "rejected_samples": fused.rejected_samples,
        "hot_pixel_count": fused.hot_pixel_count,
        "method": args.method,
        "normalization": result.normalization,
        "color_fidelity": result.color_fidelity,
        "calibration": None if calibration is None else calibration.source,
        "warnings": warnings,
        "config": config,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as stream:
        json.dump(_json_ready(report), stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    if args.save_features:
        feature_path = output_path.with_suffix(".features.npz")
        np.savez_compressed(
            feature_path,
            signed_r=fused.signed["R"],
            signed_g=fused.signed["G"],
            signed_b=fused.signed["B"],
            magnitude_r=fused.magnitude["R"],
            magnitude_g=fused.magnitude["G"],
            magnitude_b=fused.magnitude["B"],
            off_signed_r=fused.off_signed["R"],
            off_signed_g=fused.off_signed["G"],
            off_signed_b=fused.off_signed["B"],
            off_magnitude_r=fused.off_magnitude["R"],
            off_magnitude_g=fused.off_magnitude["G"],
            off_magnitude_b=fused.off_magnitude["B"],
            support=fused.support,
            rgb_linear=result.rgb_linear,
        )

    print(f"完成：{output_path}")
    print(f"报告：{report_path}")
    if warnings:
        print("警告：")
        for warning in warnings:
            print(f"  - {warning}")
    return output_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except KeyboardInterrupt:
        print("用户中断。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"重建失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
