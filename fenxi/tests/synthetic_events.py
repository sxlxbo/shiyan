from __future__ import annotations

import numpy as np


EVENT_DTYPE = np.dtype([("x", "u2"), ("y", "u2"), ("p", "u1"), ("t", "i8")])


def make_events(rows: list[tuple[int, int, int, int]]) -> np.ndarray:
    events = np.zeros(len(rows), dtype=EVENT_DTYPE)
    for index, (x, y, polarity, timestamp) in enumerate(rows):
        events[index] = (x, y, polarity, timestamp)
    return np.sort(events, order="t")

