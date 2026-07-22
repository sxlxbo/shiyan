"""Metavision RAW 文件的两遍流式读取封装。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import numpy as np

from protocol_parser import TriggerEvent


class RawDecodeError(RuntimeError):
    pass


def _raw_reader_class():
    try:
        from metavision_core.event_io.raw_reader import RawReader
    except ImportError as exc:
        raise RawDecodeError(
            "Metavision SDK 无法完整导入，无法读取 .raw；请在已执行 SDK 环境初始化的"
            "终端运行，并检查 metavision_core、metavision_hal 及其内部路径模块"
        ) from exc
    return RawReader


def validate_raw_path(path: str | Path) -> Path:
    raw_path = Path(path).expanduser().resolve()
    if not raw_path.is_file():
        raise RawDecodeError(f"RAW 文件不存在：{raw_path}")
    if raw_path.suffix.lower() != ".raw":
        raise RawDecodeError(f"输入必须是 .raw 文件：{raw_path}")
    return raw_path


def read_sensor_size(path: str | Path) -> tuple[int, int]:
    RawReader = _raw_reader_class()
    reader = RawReader(str(validate_raw_path(path)))
    try:
        height, width = reader.get_size()
        return int(height), int(width)
    finally:
        del reader


def read_all_triggers(
    path: str | Path, delta_t_us: int = 100_000
) -> list[TriggerEvent]:
    RawReader = _raw_reader_class()
    reader = RawReader(str(validate_raw_path(path)))
    result: list[TriggerEvent] = []
    try:
        while not reader.is_done():
            reader.load_delta_t(delta_t_us)
            triggers = reader.get_ext_trigger_events()
            if len(triggers):
                for event in triggers:
                    result.append(
                        TriggerEvent(
                            timestamp_us=int(event["t"]),
                            channel_id=int(event["id"]),
                            polarity=int(event["p"]),
                        )
                    )
                reader.clear_ext_trigger_events()
    finally:
        del reader
    return result


def iter_cd_event_chunks(
    path: str | Path, delta_t_us: int = 50_000
) -> Iterator[np.ndarray]:
    """每次调用都会新建 reader，因此可安全完成时序和特征两遍扫描。"""
    RawReader = _raw_reader_class()
    reader = RawReader(str(validate_raw_path(path)))
    try:
        while not reader.is_done():
            events = reader.load_delta_t(delta_t_us)
            if len(events):
                yield events
    finally:
        del reader
