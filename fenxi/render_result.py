"""严格等分的四宫格结果图与时序诊断图。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from reconstruction import ReconstructionResult
from timing import TimingDiagnostics, TimingResult


class RenderError(RuntimeError):
    pass


def _as_rgb(gray: np.ndarray) -> np.ndarray:
    if gray.ndim != 2 or gray.dtype != np.uint8:
        raise RenderError("单通道面板必须是 uint8 灰度图")
    return np.repeat(gray[..., None], 3, axis=2)


def _draw_title(panel: Image.Image, title: str) -> None:
    draw = ImageDraw.Draw(panel, "RGBA")
    font = ImageFont.load_default()
    box_height = min(18, max(12, panel.height // 12))
    draw.rectangle((0, 0, panel.width, box_height), fill=(0, 0, 0, 150))
    draw.text((4, 2), title, font=font, fill=(255, 255, 255, 255))


def render_four_panel(
    result: ReconstructionResult, output_path: str | Path, titles: bool = True
) -> Path:
    r = result.channel_uint8["R"]
    g = result.channel_uint8["G"]
    b = result.channel_uint8["B"]
    if r.shape != g.shape or r.shape != b.shape:
        raise RenderError("R/G/B 面板尺寸不一致")
    height, width = r.shape
    if result.rgb_uint8.shape != (height, width, 3):
        raise RenderError("RGB 面板尺寸不一致")

    arrays = [_as_rgb(r), _as_rgb(g), _as_rgb(b), result.rgb_uint8]
    labels = ["R-Channel", "G-Channel", "B-Channel", "Merged-RGB"]
    panels = [Image.fromarray(array) for array in arrays]
    if titles:
        for panel, label in zip(panels, labels):
            _draw_title(panel, label)

    canvas = Image.new("RGB", (2 * width, 2 * height))
    canvas.paste(panels[0], (0, 0))
    canvas.paste(panels[1], (width, 0))
    canvas.paste(panels[2], (0, height))
    canvas.paste(panels[3], (width, height))
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, format="PNG")
    return target


def render_timing_profile(
    timing: TimingResult | TimingDiagnostics, output_path: str | Path
) -> Path:
    width, height = 900, 320
    margin = 40
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    if isinstance(timing, TimingDiagnostics):
        profiles = [("COLOR", timing.color_on, (30, 90, 190))]
        profiles.extend(
            (color, item, line_color)
            for color, line_color in zip("RGB", ((210, 40, 40), (20, 150, 60), (80, 80, 210)))
            if (item := timing.color_on_by_color[color]) is not None
        )
        if timing.color_off is not None:
            profiles.append(("BLACK", timing.color_off, (40, 40, 40)))
        primary = timing.color_on
    else:
        profiles = [(timing.label, timing, (30, 90, 190))]
        primary = timing
    maximum = max(1.0, max(float(item.event_counts.max()) for _, item, _ in profiles))
    centers = (primary.bin_edges_us[:-1] + primary.bin_edges_us[1:]) / 2
    x_min, x_max = float(centers.min()), float(centers.max())

    def project_x(value: float) -> int:
        return margin + int((value - x_min) / max(1.0, x_max - x_min) * (width - 2 * margin))

    def project_y(value: float) -> int:
        return height - margin - int(value / maximum * (height - 2 * margin))

    draw.line((margin, height - margin, width - margin, height - margin), fill="black")
    draw.line((margin, margin, margin, height - margin), fill="black")
    legend_x = margin
    for label, profile, color in profiles:
        points = [
            (project_x(float(x)), project_y(float(y)))
            for x, y in zip(centers, profile.event_counts.astype(np.float64))
        ]
        if len(points) >= 2:
            draw.line(points, fill=color, width=2)
        draw.text((legend_x, 24), label, fill=color)
        legend_x += 58
    for offset, color in (
        (primary.window_start_us, (0, 150, 0)),
        (primary.window_end_us, (0, 150, 0)),
        (primary.peak_offset_us, (200, 40, 40)),
    ):
        x = project_x(offset)
        draw.line((x, margin, x, height - margin), fill=color, width=2)
    draw.text((margin, 8), "Trigger-aligned event profile (us)", fill="black")
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return target
