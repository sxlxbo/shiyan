"""发送端和接收端共用的 GenX320 黑场基准 RGB 同步协议。"""

PROTOCOL_VERSION = "rgb-black-sync-v2"
CHANNEL_ID = 0

# 一个完整周期固定为 BLACK/R/BLACK/G/BLACK/B。修改采集长度时只改
# COLOR_CYCLE_COUNT，确保录制永远不会结束在半个周期中。
FORMAL_STATE_SEQUENCE = ("BLACK", "R", "BLACK", "G", "BLACK", "B")
COLOR_CYCLE_COUNT = 25
STATE_HOLD_S = 0.100
STATE_INTERVAL_US = round(STATE_HOLD_S * 1_000_000)
EXPECTED_STATE_PULSES = COLOR_CYCLE_COUNT * len(FORMAL_STATE_SEQUENCE)
TOTAL_DURATION = EXPECTED_STATE_PULSES * STATE_HOLD_S

TRIGGER_PULSE_WIDTH_S = 0.002
MARKER_INTERVAL_S = 0.030
MARKER_INTERVAL_US = round(MARKER_INTERVAL_S * 1_000_000)
PREAMBLE_PULSES = 3
PREAMBLE_GUARD_S = 0.500
START_PULSES = 2
START_TO_DATA_GUARD_S = 0.300
DATA_TO_STOP_GUARD_S = 0.300
STOP_PULSES = 4


def state_metadata(state_index):
    """返回正式状态 Trigger 的周期、相位、目标状态和语义角色。"""
    if not 0 <= state_index < EXPECTED_STATE_PULSES:
        raise IndexError(f"state_index 越界：{state_index}")
    position = state_index % len(FORMAL_STATE_SEQUENCE)
    state = FORMAL_STATE_SEQUENCE[position]
    return {
        "state_index": state_index,
        "cycle_index": state_index // len(FORMAL_STATE_SEQUENCE),
        "phase_index": position // 2,
        "state": state,
        "role": "BLACK_RISE" if state == "BLACK" else "COLOR_RISE",
    }
