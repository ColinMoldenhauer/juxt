"""Tests for juxt/loader.py (requires Qt via qtbot/qapp)."""
from __future__ import annotations

import os
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


class TestLocalCachePath:
    """Remote files are cached under the temp dir, whatever their path looks like."""

    def _inside(self, path, tmp_path):
        return os.path.commonpath([os.path.abspath(path), str(tmp_path)]) == str(tmp_path)

    def test_posix_path_nests_under_tmpdir(self, tmp_path):
        from juxt.loader import local_cache_path
        assert local_cache_path(str(tmp_path), "/data/plots/a.png") == tmp_path / "data" / "plots" / "a.png"

    def test_relative_path_nests_too(self, tmp_path):
        from juxt.loader import local_cache_path
        assert local_cache_path(str(tmp_path), "plots/a.png") == tmp_path / "plots" / "a.png"

    @pytest.mark.parametrize("remote", ["/C:/Users/me/a.png", "C:/Users/me/a.png"])
    def test_windows_drive_paths_stay_inside(self, tmp_path, remote):
        """A Windows SSH server reports `/C:/...`; Python 3.12 reads `C:/...`
        as a drive path that would replace the temp dir in a plain join."""
        from juxt.loader import local_cache_path
        out = local_cache_path(str(tmp_path), remote)
        assert self._inside(out, tmp_path)
        assert out.name == "a.png"

    def test_dot_dot_cannot_escape(self, tmp_path):
        from juxt.loader import local_cache_path
        out = local_cache_path(str(tmp_path), "/data/../../../etc/a.png")
        assert self._inside(out, tmp_path)

    @pytest.mark.skipif(os.name != "nt", reason="':' is only replaced where it is not allowed")
    def test_colon_replaced_on_windows(self, tmp_path):
        from juxt.loader import local_cache_path
        assert local_cache_path(str(tmp_path), "/d/12:00.png").name == "12_00.png"


class TestPreloadRemote:
    """preload_remote over a stand-in SFTP that serves the local filesystem."""

    class _SFTP:
        def __init__(self):
            self.fetched = []

        def stat(self, path):
            return type("A", (), {"st_mtime": os.stat(path).st_mtime})()

        def get(self, remote, local):
            import shutil
            self.fetched.append(remote)
            shutil.copy(remote, local)

        def close(self):
            pass

    def test_downloads_into_tmpdir_and_decodes(self, qtbot, tmp_path, monkeypatch):
        from conftest import make_png
        from juxt.config import RemoteConfig
        from juxt.loader import MissingPixmap, preload_remote

        src = tmp_path / "remote"
        src.mkdir()
        for s in ("A", "B"):
            (src / f"{s}.png").write_bytes(make_png())
        sftp = self._SFTP()
        client = type("C", (), {"close": lambda self: None})()
        monkeypatch.setattr("juxt.loader._connect_sftp", lambda remote, pw=None: (client, sftp))
        steps = []
        pixmaps, tmpdir, mtimes = preload_remote(
            src.as_posix() + "/{s}.png", {"s": ["A", "B", "C"]}, RemoteConfig(host="h"),
            progress=lambda i, n: steps.append((i, n)))
        assert sftp.fetched == [src.as_posix() + "/A.png", src.as_posix() + "/B.png"]
        assert not isinstance(pixmaps[(0,)], MissingPixmap)
        assert not isinstance(pixmaps[(1,)], MissingPixmap)
        assert isinstance(pixmaps[(2,)], MissingPixmap)
        assert set(mtimes) == {(0,), (1,)}
        copies = [os.path.join(d, f) for d, _, fs in os.walk(tmpdir) for f in fs]
        assert len(copies) == 2
        assert steps[0] == (0, 6) and steps[-1] == (6, 6)
