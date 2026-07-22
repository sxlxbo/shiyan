import sys
import unittest
from pathlib import Path


CAIJI_DIR = Path(__file__).resolve().parents[1]
if str(CAIJI_DIR) not in sys.path:
    sys.path.insert(0, str(CAIJI_DIR))

import sync_protocol as protocol


class SyncProtocolTests(unittest.TestCase):
    def test_default_duration_is_25_complete_black_rgb_cycles(self):
        self.assertEqual(protocol.FORMAL_STATE_SEQUENCE, (
            "BLACK", "R", "BLACK", "G", "BLACK", "B"
        ))
        self.assertEqual(protocol.COLOR_CYCLE_COUNT, 25)
        self.assertEqual(protocol.EXPECTED_STATE_PULSES, 150)
        self.assertAlmostEqual(protocol.TOTAL_DURATION, 15.0)

    def test_state_metadata_has_unambiguous_roles(self):
        expected = [
            (0, 0, "BLACK", "BLACK_RISE"),
            (0, 0, "R", "COLOR_RISE"),
            (0, 1, "BLACK", "BLACK_RISE"),
            (0, 1, "G", "COLOR_RISE"),
            (0, 2, "BLACK", "BLACK_RISE"),
            (0, 2, "B", "COLOR_RISE"),
            (1, 0, "BLACK", "BLACK_RISE"),
        ]
        actual = []
        for index in range(7):
            item = protocol.state_metadata(index)
            actual.append((
                item["cycle_index"], item["phase_index"],
                item["state"], item["role"]
            ))
        self.assertEqual(actual, expected)

    def test_state_metadata_rejects_out_of_range_index(self):
        with self.assertRaises(IndexError):
            protocol.state_metadata(-1)
        with self.assertRaises(IndexError):
            protocol.state_metadata(protocol.EXPECTED_STATE_PULSES)


if __name__ == "__main__":
    unittest.main()
