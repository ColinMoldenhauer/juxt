"""Tests for juxt/loader.py (requires Qt via qtbot/qapp)."""
from __future__ import annotations

import struct
import zlib
from itertools import product as iproduct

import pytest

from juxt.loader import _error_pixmap, preload


def _make_png() -> bytes:
    """Minimal valid 1×1 RGB PNG."""
    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(struct.pack(">BBBB", 0, 128, 128, 128)))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


class TestErrorPixmap:
    def test_returns_non_null_pixmap(self, qtbot):
        pm = _error_pixmap("missing/file.png")
        assert not pm.isNull()

    def test_fixed_dimensions(self, qtbot):
        pm = _error_pixmap("x.png")
        assert pm.width() == 480
        assert pm.height() == 320


class TestPreload:
    def test_all_index_keys_present(self, flat_config, qtbot):
        pms = preload(flat_config.template, flat_config.axes)
        assert set(pms.keys()) == {(0, 0), (0, 1), (1, 0), (1, 1)}

    def test_loaded_pixmaps_are_non_null(self, flat_config, qtbot):
        pms = preload(flat_config.template, flat_config.axes)
        assert all(not pm.isNull() for pm in pms.values())

    def test_missing_file_yields_error_pixmap(self, tmp_path, qtbot):
        # Template points to non-existent files
        template = str(tmp_path / "{x}.png")
        axes = {"x": ["missing"]}
        pms = preload(template, axes)
        assert (0,) in pms
        # Error pixmap has the sentinel dimensions
        assert pms[(0,)].width() == 480

    def test_progress_callback_called(self, flat_config, qtbot):
        calls: list[tuple[int, int]] = []
        preload(flat_config.template, flat_config.axes, progress=lambda i, n: calls.append((i, n)))
        assert len(calls) > 0

    def test_progress_starts_at_zero(self, flat_config, qtbot):
        calls: list[tuple[int, int]] = []
        preload(flat_config.template, flat_config.axes, progress=lambda i, n: calls.append((i, n)))
        assert calls[0][0] == 0

    def test_progress_ends_at_total(self, flat_config, qtbot):
        calls: list[tuple[int, int]] = []
        preload(flat_config.template, flat_config.axes, progress=lambda i, n: calls.append((i, n)))
        last_i, last_n = calls[-1]
        assert last_i == last_n  # final call: i == n

    def test_total_matches_combo_count(self, flat_config, qtbot):
        calls: list[tuple[int, int]] = []
        preload(flat_config.template, flat_config.axes, progress=lambda i, n: calls.append((i, n)))
        n_combos = 2 * 2  # 2 sensors × 2 dates
        assert calls[-1][1] == n_combos

    def test_single_axis(self, tmp_path, qtbot):
        (tmp_path / "A.png").write_bytes(_make_png())
        (tmp_path / "B.png").write_bytes(_make_png())
        template = (tmp_path / "{x}.png").as_posix()
        pms = preload(template, {"x": ["A", "B"]})
        assert set(pms.keys()) == {(0,), (1,)}
        assert not pms[(0,)].isNull()
        assert not pms[(1,)].isNull()

    def test_three_axes(self, tmp_path, qtbot):
        axes = {"a": ["X", "Y"], "b": ["1", "2"], "c": ["p", "q"]}
        for combo in iproduct(*axes.values()):
            fname = "_".join(combo) + ".png"
            (tmp_path / fname).write_bytes(_make_png())
        template = (tmp_path / "{a}_{b}_{c}.png").as_posix()
        pms = preload(template, axes)
        assert len(pms) == 8  # 2³
