from __future__ import annotations
from itertools import product
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap


def _error_pixmap(path: str) -> QPixmap:
    pm = QPixmap(480, 320)
    pm.fill(QColor(40, 40, 40))
    p = QPainter(pm)
    p.setPen(QColor(200, 80, 80))
    p.setFont(QFont("monospace", 10))
    p.drawText(pm.rect(), Qt.AlignCenter | Qt.TextWordWrap, f"MISSING\n{path}")
    p.end()
    return pm


def preload(
    template: str,
    axes: dict[str, list[str]],
    progress: Callable[[int, int], None] | None = None,
) -> dict[tuple[int, ...], QPixmap]:
    """Load every image into memory. Returns dict keyed by index-tuple."""
    axis_names = list(axes.keys())
    axis_values = list(axes.values())
    combos = list(product(*axis_values))

    pixmaps: dict[tuple[int, ...], QPixmap] = {}
    for i, combo in enumerate(combos):
        if progress:
            progress(i, len(combos))
        mapping = dict(zip(axis_names, combo))
        path = template.format(**mapping)
        pm = QPixmap(path)
        if pm.isNull():
            pm = _error_pixmap(path)
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))
        pixmaps[key] = pm

    if progress:
        progress(len(combos), len(combos))
    return pixmaps
