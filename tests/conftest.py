"""Shared fixtures for the juxt test suite."""
from __future__ import annotations

import struct
import zlib
from itertools import product

import pytest

from juxt.config import Config, _auto_keys


# ---------------------------------------------------------------------------
# Minimal PNG helper (no external deps)
# ---------------------------------------------------------------------------

def make_png(r: int = 128, g: int = 128, b: int = 128) -> bytes:
    """Return bytes of a minimal valid 1×1 RGB PNG."""
    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(struct.pack(">BBBB", 0, r, g, b)))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Axis definitions shared across tests
# ---------------------------------------------------------------------------

FLAT_AXES: dict[str, list[str]] = {"sensor": ["A", "B"], "date": ["d1", "d2"]}
NESTED_AXES: dict[str, list[str]] = {
    "sensor": ["A", "B"],
    "overpass": ["AM", "PM"],
    "date": ["d1", "d2"],
}


# ---------------------------------------------------------------------------
# File-system fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def flat_plot_dir(tmp_path):
    """2×2 = 4 PNG files using flat naming: sensor_date.png.

    Uses a 'plots/' subdirectory so tests can write config files to tmp_path
    without polluting the image directory that _auto_discover reads.
    """
    plot_dir = tmp_path / "plots"
    plot_dir.mkdir()
    for sensor, date in product(FLAT_AXES["sensor"], FLAT_AXES["date"]):
        (plot_dir / f"{sensor}_{date}.png").write_bytes(make_png())
    return plot_dir


@pytest.fixture
def nested_plot_dir(tmp_path):
    """2×2×2 = 8 PNGs in sensor/overpass/ subdirs: A/AM/d1.png etc."""
    plot_dir = tmp_path / "plots"
    plot_dir.mkdir()
    for sensor, overpass, date in product(*NESTED_AXES.values()):
        path = plot_dir / sensor / overpass / f"{date}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(make_png())
    return plot_dir


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def flat_config(flat_plot_dir):
    """Config pointing at flat_plot_dir (used in loader tests)."""
    axes = {k: list(v) for k, v in FLAT_AXES.items()}
    # Keep forward slashes so template works cross-platform in preload()
    template = (flat_plot_dir / "{sensor}_{date}.png").as_posix()
    return Config(template=template, axes=axes, keys=_auto_keys(axes))


@pytest.fixture
def viewer_config():
    """Minimal Config for viewer tests — no real files required."""
    axes = {k: list(v) for k, v in FLAT_AXES.items()}
    return Config(template="fake/{sensor}_{date}.png", axes=axes, keys=_auto_keys(axes))


# ---------------------------------------------------------------------------
# Qt fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mini_pixmaps(qtbot):
    """2×2 QPixmap dict keyed by (sensor_idx, date_idx) — no file I/O."""
    from PySide6.QtGui import QColor, QPixmap

    pms: dict[tuple[int, ...], QPixmap] = {}
    for i in range(2):
        for j in range(2):
            pm = QPixmap(100, 100)
            pm.fill(QColor(i * 80 + 40, j * 80 + 40, 120))
            pms[(i, j)] = pm
    return pms


@pytest.fixture
def image_view(viewer_config, mini_pixmaps, qtbot):
    """ImageView widget in a known initial state (mode 0, pos=[0,0])."""
    from juxt.viewer import ImageView

    v = ImageView(viewer_config, mini_pixmaps)
    qtbot.addWidget(v)
    v.resize(800, 600)
    v.show()
    return v
