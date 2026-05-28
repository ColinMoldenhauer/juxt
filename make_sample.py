"""Generate synthetic test PNGs for examples/sample_config.yaml (uses only PySide6)."""
from __future__ import annotations
import colorsys
import random
import sys
from itertools import product
from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

# must create QApplication before any QPixmap/QPainter work
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication(sys.argv)

SENSORS   = ["ASCAT", "SMAP", "SMOS"]
DATES     = ["2024-03-15", "2024-03-16"]
OVERPASSES = ["AM", "PM"]
SOURCES   = ["L2", "L3"]

W, H = 900, 620
GRID_COLS, GRID_ROWS = 18, 12

# Hue per sensor, so sensors look clearly different
SENSOR_HUE = {"ASCAT": 0.58, "SMAP": 0.30, "SMOS": 0.02}


def make_image(sensor: str, date: str, overpass: str, source: str) -> QPixmap:
    pm = QPixmap(W, H)
    pm.fill(QColor(20, 20, 35))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    hue = SENSOR_HUE[sensor]
    date_offset = DATES.index(date)
    overpass_offset = OVERPASSES.index(overpass)
    source_offset = SOURCES.index(source)

    rng = random.Random(hash((sensor, date, overpass, source)))

    # ── fake heatmap grid ────────────────────────────────────────────────────
    cell_w = (W - 40) // GRID_COLS
    cell_h = (H - 80) // GRID_ROWS
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            # value modulated by all params so every combo looks distinct
            base = rng.random()
            val = (base + date_offset * 0.12 + overpass_offset * 0.08 + source_offset * 0.06) % 1.0
            sat = 0.55 + overpass_offset * 0.15
            r, g, b = colorsys.hsv_to_rgb(hue, sat, 0.3 + val * 0.65)
            color = QColor(int(r * 255), int(g * 255), int(b * 255))
            x = 20 + col * cell_w
            y = 40 + row * cell_h
            p.fillRect(x, y, cell_w - 1, cell_h - 1, color)

    # ── title bar ────────────────────────────────────────────────────────────
    p.fillRect(0, 0, W, 36, QColor(0, 0, 0, 200))
    p.setFont(QFont("Courier New" if sys.platform == "win32" else "monospace", 11))
    p.setPen(QColor(220, 220, 220))
    label = (
        f"sensor={sensor}    date={date}    "
        f"overpass={overpass}    source={source}"
    )
    p.drawText(QRect(8, 0, W - 16, 36), Qt.AlignVCenter | Qt.AlignLeft, label)

    # ── colorbar strip at bottom ─────────────────────────────────────────────
    bar_y = H - 18
    for x in range(W):
        r, g, b = colorsys.hsv_to_rgb(hue, 0.7, x / W)
        p.setPen(QColor(int(r * 255), int(g * 255), int(b * 255)))
        p.drawLine(x, bar_y, x, H - 4)

    p.end()
    return pm


def main():
    NESTED = False

    out = Path("examples/sample_plots_nested") if NESTED else Path("examples/sample_plots")
    combos = list(product(SENSORS, DATES, OVERPASSES, SOURCES))
    for sensor, date, overpass, source in combos:

        # output path
        if NESTED:
            path = out / sensor / overpass / f"{date}_{source}.png"
        else:
            path = out / f"{sensor}_{date}_{overpass}_{source}.png"

        path.parent.mkdir(exist_ok=True, parents=True)

        pm = make_image(sensor, date, overpass, source)
        pm.save(str(path))
        print(f"  {path}")
    print(f"\n{len(combos)} images -> {out}/")
    print("Run:  juxt examples/sample_config.yaml")
    print("  or: python -m juxt examples/sample_config.yaml")


if __name__ == "__main__":
    main()
