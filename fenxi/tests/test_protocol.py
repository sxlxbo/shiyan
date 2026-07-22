from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from protocol_parser import (  # noqa: E402
    PROTOCOL_VERSION,
    ProtocolError,
    StateTrigger,
    TriggerEvent,
    build_color_exposures,
    load_state_metadata,
    match_metadata_to_raw,
    validate_state_sequence,
)


def make_states(cycles: int = 2) -> list[StateTrigger]:
    states = ("BLACK", "R", "BLACK", "G", "BLACK", "B")
    phases = (0, 0, 1, 1, 2, 2)
    return [
        StateTrigger(
            100_000 + i * 100_000,
            i,
            i // 6,
            phases[i % 6],
            states[i % 6],
            "BLACK_RISE" if states[i % 6] == "BLACK" else "COLOR_RISE",
        )
        for i in range(cycles * 6)
    ]


class ProtocolTests(unittest.TestCase):
    def test_builds_exposures_and_allows_final_b_without_off(self):
        exposures = build_color_exposures(make_states(2))
        self.assertEqual([item.color for item in exposures], list("RGBRGB"))
        self.assertEqual(exposures[0].baseline_black_trigger.state_index, 0)
        self.assertEqual(exposures[0].color_off_trigger.state_index, 2)
        self.assertIsNone(exposures[-1].color_off_trigger)

    def test_rejects_wrong_state_role_phase_and_gap(self):
        fields = ("state", "role", "phase_index", "state_index")
        replacements = ("G", "BLACK_RISE", 2, 8)
        for field, value in zip(fields, replacements):
            with self.subTest(field=field):
                states = make_states(1)
                states[1] = StateTrigger(**{**states[1].__dict__, field: value})
                with self.assertRaises(ProtocolError):
                    validate_state_sequence(states)

    def test_loads_v2_csv_and_rejects_wrong_version(self):
        fieldnames = [
            "protocol_version", "timestamp_us", "id", "polarity", "role",
            "state_index", "cycle_index", "phase_index", "state",
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.csv"
            with path.open("w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.DictWriter(stream, fieldnames=fieldnames)
                writer.writeheader()
                for item in make_states(1):
                    writer.writerow({
                        "protocol_version": PROTOCOL_VERSION,
                        "timestamp_us": item.timestamp_us,
                        "id": 0,
                        "polarity": 1,
                        "role": item.role,
                        "state_index": item.state_index,
                        "cycle_index": item.cycle_index,
                        "phase_index": item.phase_index,
                        "state": item.state,
                    })
            self.assertEqual(len(load_state_metadata(path)), 6)
            text = path.read_text(encoding="utf-8-sig").replace(PROTOCOL_VERSION, "rgb-sync-v1")
            path.write_text(text, encoding="utf-8-sig")
            with self.assertRaises(ProtocolError):
                load_state_metadata(path)

    def test_matches_every_csv_state_to_raw(self):
        states = make_states(1)
        raw = [TriggerEvent(item.timestamp_us + 20, 0, 1) for item in states]
        match_metadata_to_raw(states, raw, 0, 50)
        raw.pop(3)
        with self.assertRaises(ProtocolError):
            match_metadata_to_raw(states, raw, 0, 50)


if __name__ == "__main__":
    unittest.main()
