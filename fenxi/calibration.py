"""可选的白平衡、颜色矩阵和 gamma 标定。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class Calibration:
    gains: np.ndarray
    matrix: np.ndarray
    gamma: float
    source: str


def load_calibration(path: str | Path) -> Calibration:
    calibration_path = Path(path)
    with calibration_path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    try:
        gains = np.asarray(data.get("gains", [1, 1, 1]), dtype=np.float32)
        matrix = np.asarray(data.get("matrix", np.eye(3).tolist()), dtype=np.float32)
        gamma = float(data.get("gamma", 2.2))
    except (TypeError, ValueError) as exc:
        raise CalibrationError("标定文件包含非数值参数") from exc
    if gains.shape != (3,) or matrix.shape != (3, 3):
        raise CalibrationError("标定 gains 必须为 3 个数，matrix 必须为 3×3")
    if np.any(gains <= 0) or gamma <= 0:
        raise CalibrationError("标定 gains 和 gamma 必须为正数")
    return Calibration(gains=gains, matrix=matrix, gamma=gamma, source=str(calibration_path))


def apply_calibration(rgb_linear: np.ndarray, calibration: Calibration) -> np.ndarray:
    corrected = rgb_linear.astype(np.float32) * calibration.gains.reshape(1, 1, 3)
    corrected = np.einsum("...c,dc->...d", corrected, calibration.matrix)
    return np.clip(corrected, 0.0, 1.0)

