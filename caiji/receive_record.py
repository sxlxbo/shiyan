#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GenX320 外部触发录制端：识别协议前导后才创建 RAW 文件。"""

import csv
import os
import sys
import time
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from sync_protocol import (
    CHANNEL_ID,
    EXPECTED_STATE_PULSES,
    MARKER_INTERVAL_US,
    PREAMBLE_PULSES,
    PROTOCOL_VERSION,
    START_PULSES,
    STATE_INTERVAL_US,
    STOP_PULSES,
    TOTAL_DURATION,
    state_metadata,
)


WAIT_TIMEOUT = 30.0
HARD_RECORD_TIMEOUT = TOTAL_DURATION + 7.0

MARKER_TOLERANCE_US = 8_000
START_TO_DATA_MIN_US = 180_000
STOP_GAP_MIN_US = 180_000
STATE_TOLERANCE_US = 20_000
POST_STOP_DRAIN = 0.08

OUTPUT_DIR = Path(__file__).resolve().parent / "trigger_records"


def init_camera():
    from metavision_core.event_io.raw_reader import initiate_device

    try:
        device = initiate_device("")
        if not device:
            raise RuntimeError("未检测到 GenX320")
        if not device.get_i_events_stream():
            raise RuntimeError("设备不提供 Events Stream")
        print("相机初始化成功")
        return device
    except Exception as exc:
        raise RuntimeError(f"相机初始化失败：{exc}") from exc


def configure_external_trigger(device):
    trigger_in = device.get_i_trigger_in()
    if not trigger_in:
        hw_identification = device.get_i_hw_identification()
        integrator = (
            hw_identification.get_integrator()
            if hw_identification is not None
            else ""
        )
        if integrator == "rp1-cfe":
            print(
                "检测到 rp1-cfe：插件未提供 I_TriggerIn，"
                "使用平台默认 EXTTRIG 数据流。"
            )
            return None
        raise RuntimeError("设备不提供 Trigger In，停止录制")
    enabled = trigger_in.enable(CHANNEL_ID)
    if enabled is False:
        raise RuntimeError(f"Trigger In 通道 {CHANNEL_ID} 启用失败")
    print(f"外部触发通道 {CHANNEL_ID} 已启用")
    return trigger_in


def in_marker_range(delta_us):
    return abs(int(delta_us) - MARKER_INTERVAL_US) <= MARKER_TOLERANCE_US


def append_trigger_rows(target, triggers):
    for event in triggers:
        target.append(
            {
                "timestamp_us": int(event["t"]),
                "id": int(event["id"]),
                "polarity": int(event["p"]),
            }
        )


def detect_preamble(rising_timestamps):
    if len(rising_timestamps) < PREAMBLE_PULSES:
        return False
    recent = rising_timestamps[-PREAMBLE_PULSES:]
    return all(in_marker_range(b - a) for a, b in zip(recent, recent[1:]))


def start_raw_log(events_stream, partial_path):
    result = events_stream.log_raw_data(str(partial_path))
    if result is False:
        raise RuntimeError("SDK 拒绝开启 RAW 录制")


def stop_raw_log(events_stream, recording):
    if recording:
        events_stream.stop_log_raw_data()


def collect_protocol(device):
    from metavision_core.event_io import EventsIterator

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = OUTPUT_DIR / f"genx320_led_sync_{stamp}.raw"
    partial_path = OUTPUT_DIR / f"genx320_led_sync_{stamp}.partial.raw"
    sidecar_path = final_path.with_suffix(".csv")

    events_stream = device.get_i_events_stream()
    iterator = EventsIterator.from_device(device=device, delta_t=10_000)
    armed_at = time.monotonic()
    recording = False
    state = "ARMED"
    preamble_rises = []
    recorded_events = []
    start_rises = []
    state_rises = []
    stop_rises = []
    record_started_at = None
    stop_detected_at = None
    last_rise = None

    print(f"ARMED：等待协议前导，超时 {WAIT_TIMEOUT:.0f} s；此时不会创建 RAW。")
    try:
        for _ in iterator:
            now = time.monotonic()
            triggers = iterator.reader.get_ext_trigger_events()
            current = []
            if len(triggers):
                append_trigger_rows(current, triggers)
                iterator.reader.clear_ext_trigger_events()

            if state == "ARMED":
                if now - armed_at >= WAIT_TIMEOUT:
                    raise TimeoutError("等待前导超时；未创建 RAW 文件")
                for event in current:
                    if event["id"] != CHANNEL_ID or event["polarity"] != 1:
                        continue
                    preamble_rises.append(event["timestamp_us"])
                    preamble_rises = preamble_rises[-PREAMBLE_PULSES:]
                    if detect_preamble(preamble_rises):
                        start_raw_log(events_stream, partial_path)
                        recording = True
                        record_started_at = now
                        state = "WAIT_START"
                        print("前导确认，RAW 已开启；等待 START 标记。")
                        break
                continue

            recorded_events.extend(current)
            if now - record_started_at >= HARD_RECORD_TIMEOUT:
                raise TimeoutError("录制硬超时，未收到完整 STOP 标记")

            for event in current:
                if event["id"] != CHANNEL_ID or event["polarity"] != 1:
                    continue
                timestamp = event["timestamp_us"]
                delta = None if last_rise is None else timestamp - last_rise
                last_rise = timestamp

                if state == "WAIT_START":
                    if not start_rises or (delta is not None and in_marker_range(delta)):
                        start_rises.append(timestamp)
                    else:
                        start_rises = [timestamp]
                    if len(start_rises) == START_PULSES:
                        state = "WAIT_DATA"
                        print("START 标记确认，等待首个正式 BLACK 脉冲。")

                elif state == "WAIT_DATA":
                    if timestamp - start_rises[-1] >= START_TO_DATA_MIN_US:
                        state_rises.append(timestamp)
                        state = "DATA"
                        print("正式 BLACK/R/G/B 状态段开始。")

                elif state == "DATA":
                    if delta is not None and abs(delta - STATE_INTERVAL_US) <= STATE_TOLERANCE_US:
                        state_rises.append(timestamp)
                    elif delta is not None and delta >= STOP_GAP_MIN_US:
                        stop_rises = [timestamp]
                        state = "WAIT_STOP"
                    else:
                        raise RuntimeError(f"状态周期异常：相邻上升沿 {delta} us")

                elif state == "WAIT_STOP":
                    if delta is not None and in_marker_range(delta):
                        stop_rises.append(timestamp)
                    else:
                        raise RuntimeError("STOP 标记格式无效")
                    if len(stop_rises) == STOP_PULSES:
                        state = "DRAIN"
                        stop_detected_at = now
                        print("STOP 标记确认，正在收尾。")

            if state == "DRAIN" and now - stop_detected_at >= POST_STOP_DRAIN:
                break

        if state != "DRAIN":
            raise RuntimeError(f"数据流提前结束，当前状态：{state}")
    finally:
        stop_raw_log(events_stream, recording)

    if not partial_path.exists() or partial_path.stat().st_size == 0:
        raise RuntimeError("SDK 未生成有效的 RAW 文件")

    verification = verify_raw(partial_path)
    if verification["data_count"] != EXPECTED_STATE_PULSES:
        raise RuntimeError(
            f"正式状态脉冲数 {verification['data_count']}，预期 {EXPECTED_STATE_PULSES}；"
            f"保留 partial 文件供诊断"
        )

    write_sidecar(sidecar_path, verification["events"], verification["data_rises"])
    os.replace(partial_path, final_path)
    print(f"录制及校验完成：{final_path}")
    print(f"RGB 元数据：{sidecar_path}")
    return final_path


def read_all_triggers(raw_file):
    from metavision_core.event_io.raw_reader import RawReader

    reader = RawReader(str(raw_file))
    events = []
    while not reader.is_done():
        reader.load_delta_t(100_000)
        triggers = reader.get_ext_trigger_events()
        if len(triggers):
            append_trigger_rows(events, triggers)
            reader.clear_ext_trigger_events()
    return events


def verify_raw(raw_file):
    events = read_all_triggers(raw_file)
    wrong_channel = [event for event in events if event["id"] != CHANNEL_ID]
    rises = [
        event["timestamp_us"]
        for event in events
        if event["id"] == CHANNEL_ID and event["polarity"] == 1
    ]
    if wrong_channel:
        raise RuntimeError(f"RAW 中发现 {len(wrong_channel)} 个错误通道 Trigger 事件")

    # RAW 从前导确认之后开始，首个完整短间隔对是 START。
    start_index = next(
        (index for index in range(len(rises) - 1) if in_marker_range(rises[index + 1] - rises[index])),
        None,
    )
    if start_index is None:
        raise RuntimeError("RAW 中未找到 START 标记")
    data_start = start_index + START_PULSES
    while data_start < len(rises) and rises[data_start] - rises[start_index + 1] < START_TO_DATA_MIN_US:
        data_start += 1
    if data_start >= len(rises):
        raise RuntimeError("RAW 中没有正式状态脉冲")

    data_rises = [rises[data_start]]
    cursor = data_start + 1
    while cursor < len(rises):
        delta = rises[cursor] - data_rises[-1]
        if abs(delta - STATE_INTERVAL_US) <= STATE_TOLERANCE_US:
            data_rises.append(rises[cursor])
            cursor += 1
        elif delta >= STOP_GAP_MIN_US:
            break
        else:
            raise RuntimeError(f"RAW 正式状态段周期异常：{delta} us")

    stop = rises[cursor:cursor + STOP_PULSES]
    if len(stop) != STOP_PULSES or not all(
        in_marker_range(b - a) for a, b in zip(stop, stop[1:])
    ):
        raise RuntimeError("RAW 中 STOP 标记不完整")

    print(
        f"RAW 校验：上升沿 {len(rises)}，下降沿 "
        f"{sum(event['polarity'] == 0 for event in events)}，"
        f"正式状态 {len(data_rises)}。"
    )
    return {"events": events, "data_rises": data_rises, "data_count": len(data_rises)}


def write_sidecar(path, events, data_rises):
    data_index = {timestamp: index for index, timestamp in enumerate(data_rises)}
    fieldnames = [
        "protocol_version",
        "timestamp_us",
        "id",
        "polarity",
        "role",
        "state_index",
        "cycle_index",
        "phase_index",
        "state",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            index = data_index.get(event["timestamp_us"])
            metadata = state_metadata(index) if index is not None else None
            role = metadata["role"] if metadata is not None else "MARKER"
            if index is None and event["polarity"] == 0 and data_rises:
                candidate = bisect_right(data_rises, event["timestamp_us"]) - 1
                if (
                    candidate >= 0
                    and 0 <= event["timestamp_us"] - data_rises[candidate] <= 10_000
                ):
                    index = candidate
                    metadata = state_metadata(index)
                    role = metadata["role"].replace("_RISE", "_FALL")
            writer.writerow(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    **event,
                    "role": role,
                    "state_index": "" if metadata is None else metadata["state_index"],
                    "cycle_index": "" if metadata is None else metadata["cycle_index"],
                    "phase_index": "" if metadata is None else metadata["phase_index"],
                    "state": "" if metadata is None else metadata["state"],
                }
            )


def main():
    device = None
    try:
        device = init_camera()
        configure_external_trigger(device)
        collect_protocol(device)
    except KeyboardInterrupt:
        print("用户中断。未通过校验的录制会保留为 .partial.raw。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        if device is not None:
            del device
    return 0


if __name__ == "__main__":
    sys.exit(main())
