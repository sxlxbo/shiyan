#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""树莓派 5：驱动 WS2812B，并向 GenX320 发送可识别的同步协议。"""

import csv
import time
from datetime import datetime
from pathlib import Path

import board
import neopixel_spi as neopixel
from gpiozero import DigitalOutputDevice

from sync_protocol import (
    DATA_TO_STOP_GUARD_S,
    EXPECTED_RGB_PULSES,
    MARKER_INTERVAL_S,
    PREAMBLE_GUARD_S,
    PREAMBLE_PULSES,
    PROTOCOL_VERSION,
    RGB_INTERVAL_S,
    START_PULSES,
    START_TO_DATA_GUARD_S,
    STOP_PULSES,
    TOTAL_DURATION,
    TRIGGER_PULSE_WIDTH_S,
)


TRIGGER_OUT_PIN = 17
LED_COUNT = 256
SAFE_BRIGHTNESS = 0.2

COLOR_SEQUENCE = [
    ("R", (255, 0, 0)),
    ("G", (0, 255, 0)),
    ("B", (0, 0, 255)),
]


def get_center_block_indices():
    """返回普通逐行映射的 16x16 灯板中心 4x4 索引。"""
    return [row * 16 + col for row in range(6, 10) for col in range(6, 10)]


def sleep_until(deadline):
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(remaining)


def send_pulse(trigger_pin):
    trigger_pin.on()
    time.sleep(TRIGGER_PULSE_WIDTH_S)
    trigger_pin.off()


def send_marker(trigger_pin, count, name):
    first_deadline = time.monotonic()
    for index in range(count):
        sleep_until(first_deadline + index * MARKER_INTERVAL_S)
        send_pulse(trigger_pin)
    print(f"  {name}: {count} 个脉冲")


def set_pixels(pixels, target_pixels, rgb):
    pixels.fill((0, 0, 0))
    for index in target_pixels:
        pixels[index] = rgb
    pixels.show()


def sync_led_and_trigger():
    output_dir = Path(__file__).resolve().parent / "trigger_records"
    output_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = output_dir / (
        f"led_schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

    trigger_pin = DigitalOutputDevice(TRIGGER_OUT_PIN, initial_value=False)
    spi = board.SPI()
    pixels = neopixel.NeoPixel_SPI(
        spi, LED_COUNT, pixel_order=neopixel.GRB, auto_write=False
    )
    pixels.brightness = SAFE_BRIGHTNESS
    target_pixels = get_center_block_indices()
    cycle_count = EXPECTED_RGB_PULSES
    rows = []

    print("=" * 58)
    print(f"GenX320 RGB 同步协议 {PROTOCOL_VERSION}")
    print(
        f"正式段: {cycle_count} 次切换 / {TOTAL_DURATION:.1f} s, "
        f"{RGB_INTERVAL_S * 1000:.0f} ms 周期, "
        f"Trigger {TRIGGER_PULSE_WIDTH_S * 1000:.1f} ms"
    )
    print("Trigger 在 pixels.show() 返回后发出；首个正式颜色固定为 R。")
    print("=" * 58)

    try:
        set_pixels(pixels, target_pixels, (0, 0, 0))
        send_marker(trigger_pin, PREAMBLE_PULSES, "PREAMBLE")
        time.sleep(PREAMBLE_GUARD_S)
        send_marker(trigger_pin, START_PULSES, "START")
        time.sleep(START_TO_DATA_GUARD_S)

        first_deadline = time.monotonic()
        for cycle_index in range(cycle_count):
            planned = first_deadline + cycle_index * RGB_INTERVAL_S
            sleep_until(planned)
            actual_before_show = time.monotonic()
            color, rgb = COLOR_SEQUENCE[cycle_index % len(COLOR_SEQUENCE)]
            set_pixels(pixels, target_pixels, rgb)
            shown_at = time.monotonic()
            send_pulse(trigger_pin)
            triggered_at = time.monotonic()
            overrun_ms = max(0.0, actual_before_show - planned) * 1000
            rows.append(
                {
                    "cycle_index": cycle_index,
                    "color": color,
                    "planned_monotonic_s": f"{planned:.9f}",
                    "shown_monotonic_s": f"{shown_at:.9f}",
                    "trigger_finished_monotonic_s": f"{triggered_at:.9f}",
                    "start_overrun_ms": f"{overrun_ms:.3f}",
                }
            )
            print(
                f"  RGB: {cycle_index + 1:>3}/{cycle_count} | {color} | "
                f"超期 {overrun_ms:.2f} ms",
                end="\r",
            )

        print()
        time.sleep(DATA_TO_STOP_GUARD_S)
        send_marker(trigger_pin, STOP_PULSES, "STOP")
        print(f"完成：{cycle_count} 次 RGB 切换。")
    except KeyboardInterrupt:
        print("\n用户中断；不会发送伪造的 STOP 标记。")
    finally:
        trigger_pin.off()
        trigger_pin.close()
        pixels.fill((0, 0, 0))
        pixels.show()
        if rows:
            with schedule_path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            print(f"LED 时序日志：{schedule_path}")
        print("软屏已熄灭，GPIO 已释放。")


if __name__ == "__main__":
    sync_led_and_trigger()
