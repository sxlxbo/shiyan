#!/usr/bin/env python3
"""GenX320 单色闪烁时间流采集。

在同一颜色的 BLACK/MONO 间切换，并在每次 LED 更新后发出硬件 Trigger。
RAW 保存全部 CD 与 Trigger 事件，JSON 保存可复现实验参数。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


PROTOCOL = "mono-black-sync-v1"
TRIGGER_CHANNEL = 0
TRIGGER_GPIO = 17
LED_COUNT = 256
TRIGGER_WIDTH_S = 0.002
COLOR_RGB = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="录制单色 BLACK/MONO 时间流")
    parser.add_argument("--color", choices=COLOR_RGB, default="red")
    parser.add_argument("--brightness", type=float, default=0.20)
    parser.add_argument("--cycles", type=int, default=30)
    parser.add_argument("--hold-ms", type=float, default=100.0)
    parser.add_argument("--pre-roll-ms", type=float, default=500.0)
    parser.add_argument("--post-roll-ms", type=float, default=300.0)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "records")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not 0 < args.brightness <= 1:
        raise ValueError("--brightness 必须在 (0, 1] 内")
    if args.cycles < 3:
        raise ValueError("--cycles 至少为 3")
    if args.hold_ms < 50:
        raise ValueError("--hold-ms 至少为 50，给光学响应留下足够时间")
    if args.pre_roll_ms < 0 or args.post_roll_ms < 0:
        raise ValueError("pre/post roll 不能为负数")


def center_block_indices() -> list[int]:
    """16x16 普通逐行灯板的中心 4x4；蛇形灯板需按实物修改。"""
    return [row * 16 + col for row in range(6, 10) for col in range(6, 10)]


def set_light(pixels, indices: list[int], rgb: tuple[int, int, int]) -> None:
    pixels.fill((0, 0, 0))
    for index in indices:
        pixels[index] = rgb
    pixels.show()


def sleep_until(deadline: float) -> None:
    delay = deadline - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def pulse(pin) -> None:
    pin.on()
    time.sleep(TRIGGER_WIDTH_S)
    pin.off()


def init_camera():
    from metavision_core.event_io.raw_reader import initiate_device

    device = initiate_device("")
    if not device or not device.get_i_events_stream():
        raise RuntimeError("未检测到可用的 GenX320 Events Stream")
    trigger_in = device.get_i_trigger_in()
    if trigger_in is not None:
        result = trigger_in.enable(TRIGGER_CHANNEL)
        if result is False:
            raise RuntimeError(f"无法启用 Trigger In 通道 {TRIGGER_CHANNEL}")
    return device


def verify_trigger_rises(raw_path: Path, expected: int, hold_us: int) -> list[int]:
    """关闭录制后从 RAW 回读 Trigger，避免把无同步文件标成成功。"""
    from metavision_core.event_io.raw_reader import RawReader

    reader = RawReader(str(raw_path))
    rises: list[int] = []
    try:
        while not reader.is_done():
            reader.load_delta_t(100_000)
            events = reader.get_ext_trigger_events()
            for event in events:
                if int(event["id"]) == TRIGGER_CHANNEL and int(event["p"]) == 1:
                    rises.append(int(event["t"]))
            if len(events):
                reader.clear_ext_trigger_events()
    finally:
        del reader
    if len(rises) != expected:
        raise RuntimeError(f"RAW 中有 {len(rises)} 个 Trigger 上升沿，预期 {expected}")
    tolerance = max(20_000, int(hold_us * 0.25))
    errors = [abs((right - left) - hold_us) for left, right in zip(rises, rises[1:])]
    if errors and max(errors) > tolerance:
        raise RuntimeError(f"RAW Trigger 最大周期误差 {max(errors)} us，超过 {tolerance} us")
    return rises


def run(args: argparse.Namespace) -> Path:
    validate_args(args)
    try:
        import board
        import neopixel_spi as neopixel
        from gpiozero import DigitalOutputDevice
        from metavision_core.event_io import EventsIterator
    except ImportError as exc:
        raise RuntimeError(
            "缺少树莓派采集依赖；请在已配置 Metavision SDK、board、"
            "neopixel_spi 和 gpiozero 的树莓派环境运行"
        ) from exc

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"genx320_mono_{args.color}_{stamp}"
    partial = output_dir / f"{stem}.partial.raw"
    final = output_dir / f"{stem}.raw"
    metadata_path = output_dir / f"{stem}.json"

    device = init_camera()
    stream = device.get_i_events_stream()
    iterator = EventsIterator.from_device(device=device, delta_t=5_000)
    pin = DigitalOutputDevice(TRIGGER_GPIO, initial_value=False)
    pixels = neopixel.NeoPixel_SPI(
        board.SPI(), LED_COUNT, pixel_order=neopixel.GRB, auto_write=False
    )
    pixels.brightness = args.brightness
    targets = center_block_indices()
    transitions: list[dict[str, object]] = []
    recording = False

    total_s = (args.pre_roll_ms + args.post_roll_ms) / 1000 + args.cycles * 2 * args.hold_ms / 1000
    print(f"输出: {final}")
    print(f"协议: {PROTOCOL}，{args.color}，{args.cycles} 周期，预计 {total_s:.2f} s")
    try:
        set_light(pixels, targets, (0, 0, 0))
        result = stream.log_raw_data(str(partial))
        if result is False:
            raise RuntimeError("SDK 拒绝开启 RAW 录制")
        recording = True
        started = time.monotonic()
        next_transition = started + args.pre_roll_ms / 1000
        transition_index = 0
        expected_transitions = args.cycles * 2
        finish_at: float | None = None

        for _events in iterator:
            now = time.monotonic()
            if transition_index < expected_transitions and now >= next_transition:
                state = "MONO" if transition_index % 2 == 0 else "BLACK"
                rgb = COLOR_RGB[args.color] if state == "MONO" else (0, 0, 0)
                scheduled = next_transition
                set_light(pixels, targets, rgb)
                shown = time.monotonic()
                pulse(pin)
                transitions.append({
                    "index": transition_index,
                    "state": state,
                    "scheduled_s": scheduled - started,
                    "shown_s": shown - started,
                    "overrun_ms": max(0.0, shown - scheduled) * 1000,
                })
                transition_index += 1
                next_transition += args.hold_ms / 1000
                if transition_index == expected_transitions:
                    finish_at = time.monotonic() + args.post_roll_ms / 1000
                print(f"切换 {transition_index:>3}/{expected_transitions}: {state:<5}", end="\r")
            if finish_at is not None and now >= finish_at:
                break
        print()
    finally:
        if recording:
            stream.stop_log_raw_data()
        pin.off()
        pin.close()
        pixels.fill((0, 0, 0))
        pixels.show()

    if len(transitions) != args.cycles * 2:
        raise RuntimeError(f"采集提前结束，仅完成 {len(transitions)} 次切换；保留 {partial.name}")
    if not partial.is_file() or partial.stat().st_size == 0:
        raise RuntimeError("SDK 未生成有效 RAW 文件")

    trigger_rises = verify_trigger_rises(
        partial, args.cycles * 2, round(args.hold_ms * 1000)
    )

    metadata = {
        "protocol": PROTOCOL,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "raw_file": final.name,
        "color": args.color,
        "rgb": COLOR_RGB[args.color],
        "brightness": args.brightness,
        "cycles": args.cycles,
        "hold_ms": args.hold_ms,
        "pre_roll_ms": args.pre_roll_ms,
        "post_roll_ms": args.post_roll_ms,
        "trigger_channel": TRIGGER_CHANNEL,
        "trigger_gpio": TRIGGER_GPIO,
        "trigger_width_ms": TRIGGER_WIDTH_S * 1000,
        "first_trigger_state": "MONO",
        "raw_trigger_rise_count": len(trigger_rises),
        "raw_first_trigger_us": trigger_rises[0],
        "raw_last_trigger_us": trigger_rises[-1],
        "transitions": transitions,
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(partial, final)
    print(f"采集完成: {final}")
    print(f"元数据:   {metadata_path}")
    return final


if __name__ == "__main__":
    try:
        run(parse_args())
    except (KeyboardInterrupt, RuntimeError, ValueError) as exc:
        raise SystemExit(f"采集失败: {exc}") from exc
