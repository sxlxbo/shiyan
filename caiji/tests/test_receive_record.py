import csv
import sys
import tempfile
import unittest
from pathlib import Path


CAIJI_DIR = Path(__file__).resolve().parents[1]
if str(CAIJI_DIR) not in sys.path:
    sys.path.insert(0, str(CAIJI_DIR))

import receive_record


class SidecarTests(unittest.TestCase):
    def test_sidecar_labels_black_and_color_edges(self):
        rises = [1_000_000 + index * 100_000 for index in range(6)]
        events = []
        for timestamp in rises:
            events.extend([
                {"timestamp_us": timestamp, "id": 0, "polarity": 1},
                {"timestamp_us": timestamp + 2_000, "id": 0, "polarity": 0},
            ])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            receive_record.write_sidecar(path, events, rises)
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))

        rise_rows = rows[::2]
        fall_rows = rows[1::2]
        self.assertEqual(
            [row["state"] for row in rise_rows],
            ["BLACK", "R", "BLACK", "G", "BLACK", "B"],
        )
        self.assertEqual(
            [row["role"] for row in rise_rows],
            ["BLACK_RISE", "COLOR_RISE"] * 3,
        )
        self.assertEqual(
            [row["role"] for row in fall_rows],
            ["BLACK_FALL", "COLOR_FALL"] * 3,
        )
        self.assertEqual({row["cycle_index"] for row in rise_rows}, {"0"})
        self.assertEqual(
            [row["phase_index"] for row in rise_rows],
            ["0", "0", "1", "1", "2", "2"],
        )
        self.assertEqual(
            {row["protocol_version"] for row in rows},
            {"rgb-black-sync-v2"},
        )

    def test_verify_raw_separates_150_states_from_stop_marker(self):
        start = [100_000, 130_000]
        states = [500_000 + index * 100_000 for index in range(150)]
        stop = [15_700_000 + index * 30_000 for index in range(4)]
        rises = start + states + stop
        events = [
            {"timestamp_us": timestamp, "id": 0, "polarity": 1}
            for timestamp in rises
        ]
        original = receive_record.read_all_triggers
        receive_record.read_all_triggers = lambda _path: events
        try:
            result = receive_record.verify_raw(Path("synthetic.raw"))
        finally:
            receive_record.read_all_triggers = original

        self.assertEqual(result["data_count"], 150)
        self.assertEqual(result["data_rises"], states)


if __name__ == "__main__":
    unittest.main()
