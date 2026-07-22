"""rgb-black-sync-v2 协议和 sidecar CSV 解析。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


PROTOCOL_VERSION = "rgb-black-sync-v2"
COLORS = ("R", "G", "B")
STATE_PATTERN = ("BLACK", "R", "BLACK", "G", "BLACK", "B")
PHASE_PATTERN = (0, 0, 1, 1, 2, 2)
REQUIRED_CSV_COLUMNS = {
    "protocol_version",
    "timestamp_us",
    "id",
    "polarity",
    "role",
    "state_index",
    "cycle_index",
    "phase_index",
    "state",
}


class ProtocolError(ValueError):
    """输入元数据不符合 rgb-black-sync-v2 时抛出。"""


@dataclass(frozen=True)
class TriggerEvent:
    timestamp_us: int
    channel_id: int
    polarity: int


@dataclass(frozen=True)
class StateTrigger:
    timestamp_us: int
    state_index: int
    cycle_index: int
    phase_index: int
    state: str
    role: str


@dataclass(frozen=True)
class ColorExposure:
    cycle_index: int
    phase_index: int
    color: str
    baseline_black_trigger: StateTrigger
    color_on_trigger: StateTrigger
    color_off_trigger: StateTrigger | None


def _as_int(row: Mapping[str, str], field: str, line_number: int) -> int:
    try:
        return int(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ProtocolError(f"CSV 第 {line_number} 行的 {field} 不是整数") from exc


def load_state_metadata(
    path: str | Path,
    channel_id: int = 0,
    protocol_version: str = PROTOCOL_VERSION,
) -> list[StateTrigger]:
    """读取并严格校验 v2 CSV 中的全部正式状态上升沿。"""
    csv_path = Path(path)
    with csv_path.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED_CSV_COLUMNS - set(reader.fieldnames or ())
        if missing:
            raise ProtocolError(f"CSV 缺少列：{', '.join(sorted(missing))}")

        result: list[StateTrigger] = []
        for line_number, row in enumerate(reader, start=2):
            version = (row.get("protocol_version") or "").strip()
            if version != protocol_version:
                raise ProtocolError(
                    f"CSV 第 {line_number} 行协议版本为 {version!r}，应为 {protocol_version!r}"
                )
            role = (row.get("role") or "").strip().upper()
            # START/STOP/PREAMBLE 仍可存在于同一 sidecar，但只有正式状态参与解析。
            if role not in {"BLACK_RISE", "COLOR_RISE"}:
                continue
            if _as_int(row, "id", line_number) != channel_id:
                raise ProtocolError(f"CSV 第 {line_number} 行使用了错误 Trigger 通道")
            if _as_int(row, "polarity", line_number) != 1:
                raise ProtocolError(f"CSV 第 {line_number} 行正式状态不是上升沿")
            result.append(
                StateTrigger(
                    timestamp_us=_as_int(row, "timestamp_us", line_number),
                    state_index=_as_int(row, "state_index", line_number),
                    cycle_index=_as_int(row, "cycle_index", line_number),
                    phase_index=_as_int(row, "phase_index", line_number),
                    state=(row.get("state") or "").strip().upper(),
                    role=role,
                )
            )

    if not result:
        raise ProtocolError("CSV 中没有 BLACK_RISE/COLOR_RISE 正式状态")
    validate_state_sequence(result)
    return result


def validate_state_sequence(triggers: Sequence[StateTrigger]) -> None:
    if not triggers:
        raise ProtocolError("正式状态 Trigger 序列为空")
    previous_t: int | None = None
    for expected_index, item in enumerate(triggers):
        if item.state_index != expected_index:
            raise ProtocolError(
                f"state_index 不连续：位置 {expected_index} 得到 {item.state_index}"
            )
        expected_cycle = expected_index // 6
        expected_state = STATE_PATTERN[expected_index % 6]
        expected_phase = PHASE_PATTERN[expected_index % 6]
        expected_role = "BLACK_RISE" if expected_state == "BLACK" else "COLOR_RISE"
        if item.cycle_index != expected_cycle:
            raise ProtocolError(
                f"state_index={expected_index} 的 cycle_index 应为 {expected_cycle}，实际为 {item.cycle_index}"
            )
        if item.phase_index != expected_phase:
            raise ProtocolError(
                f"state_index={expected_index} 的 phase_index 应为 {expected_phase}，实际为 {item.phase_index}"
            )
        if item.state != expected_state:
            raise ProtocolError(
                f"state_index={expected_index} 应为 {expected_state}，实际为 {item.state!r}"
            )
        if item.role != expected_role:
            raise ProtocolError(
                f"state_index={expected_index} 应使用 {expected_role}，实际为 {item.role!r}"
            )
        if previous_t is not None and item.timestamp_us <= previous_t:
            raise ProtocolError("正式状态 Trigger 时间戳不是严格递增")
        previous_t = item.timestamp_us

    if len(triggers) % 6:
        raise ProtocolError(f"正式状态数 {len(triggers)} 不能组成完整六状态周期")


def build_color_exposures(triggers: Sequence[StateTrigger]) -> list[ColorExposure]:
    """将严格六状态序列转换成独立的 BLACK→Color 样本。"""
    validate_state_sequence(triggers)
    exposures: list[ColorExposure] = []
    for index, color_on in enumerate(triggers):
        if color_on.role != "COLOR_RISE":
            continue
        baseline = triggers[index - 1]
        following = triggers[index + 1] if index + 1 < len(triggers) else None
        color_off = following if following is not None and following.role == "BLACK_RISE" else None
        if color_off is None and not (
            index == len(triggers) - 1 and color_on.state == "B"
        ):
            raise ProtocolError(
                f"cycle={color_on.cycle_index} color={color_on.state} 缺少后续 BLACK"
            )
        exposures.append(
            ColorExposure(
                cycle_index=color_on.cycle_index,
                phase_index=color_on.phase_index,
                color=color_on.state,
                baseline_black_trigger=baseline,
                color_on_trigger=color_on,
                color_off_trigger=color_off,
            )
        )
    return exposures


def match_metadata_to_raw(
    state_triggers: Sequence[StateTrigger],
    raw_triggers: Sequence[TriggerEvent],
    channel_id: int,
    tolerance_us: int,
) -> None:
    """确保 sidecar 的每个正式时间戳确实存在于 RAW。"""
    raw_rises = sorted(
        event.timestamp_us
        for event in raw_triggers
        if event.channel_id == channel_id and event.polarity == 1
    )
    if not raw_rises:
        raise ProtocolError(f"RAW 中没有通道 {channel_id} 的 Trigger 上升沿")
    cursor = 0
    for item in state_triggers:
        while cursor < len(raw_rises) and raw_rises[cursor] < item.timestamp_us - tolerance_us:
            cursor += 1
        if cursor >= len(raw_rises) or abs(raw_rises[cursor] - item.timestamp_us) > tolerance_us:
            raise ProtocolError(f"CSV 时间戳 {item.timestamp_us} us 无法在 RAW Trigger 中匹配")


def validate_state_periods(
    triggers: Sequence[StateTrigger], expected_us: int, tolerance_us: int
) -> None:
    for previous, current in zip(triggers, triggers[1:]):
        delta = current.timestamp_us - previous.timestamp_us
        if abs(delta - expected_us) > tolerance_us:
            raise ProtocolError(
                f"正式状态间隔异常：state {previous.state_index}→{current.state_index} 为 {delta} us"
            )


def validate_minimum_cycles(
    triggers: Sequence[StateTrigger], minimum_complete_cycles: int
) -> int:
    complete_cycles = len(triggers) // 6
    if complete_cycles < minimum_complete_cycles:
        raise ProtocolError(
            f"只有 {complete_cycles} 个完整六状态周期，至少需要 {minimum_complete_cycles} 个"
        )
    return complete_cycles
