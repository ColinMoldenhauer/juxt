"""Tests for juxt/loader.py (requires Qt via qtbot/qapp)."""
from __future__ import annotations

import os
import struct
import zlib
from itertools import product as iproduct
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Parallel loading
# ---------------------------------------------------------------------------

class TestLoadImages:
    @pytest.fixture
    def files(self, tmp_path):
        from conftest import make_png

        paths = []
        for i in range(20):
            p = tmp_path / f"{i}.png"
            p.write_bytes(make_png(r=i))
            paths.append(str(p))
        return paths

    def test_loads_every_job(self, qtbot, files):
        from juxt.loader import MissingPixmap, load_images

        out = load_images([((i,), p, p) for i, p in enumerate(files)])
        assert sorted(out) == [(i,) for i in range(20)]
        assert not any(isinstance(pm, MissingPixmap) for pm in out.values())

    def test_missing_and_unreadable_get_the_placeholder(self, qtbot, tmp_path):
        from juxt.loader import MissingPixmap, load_images

        bad = tmp_path / "bad.png"
        bad.write_bytes(b"not a png")
        out = load_images([((0,), None, "gone.png"), ((1,), str(bad), str(bad))])
        assert all(isinstance(pm, MissingPixmap) for pm in out.values())

    @pytest.mark.parametrize("n_threads", [1, 4])
    def test_progress_counts_up_to_the_total(self, qtbot, files, n_threads):
        from juxt.loader import load_images, set_threads

        set_threads(n_threads)
        try:
            calls = []
            load_images([((i,), p, p) for i, p in enumerate(files)],
                        lambda i, n: calls.append((i, n)), offset=5, total=40)
        finally:
            set_threads(0)
        assert calls[0] == (5, 40) and calls[-1] == (25, 40)
        assert [i for i, _ in calls] == sorted(i for i, _ in calls)

    def test_raising_progress_abandons_the_load(self, qtbot, files):
        from juxt.loader import load_images

        class Stop(Exception):
            pass

        def progress(i, n):
            if i >= 3:
                raise Stop

        with pytest.raises(Stop):
            load_images([((i,), p, p) for i, p in enumerate(files)], progress)

    def test_threads_setting(self):
        from juxt.loader import set_threads, threads

        set_threads(3)
        assert threads() == 3
        set_threads(0)
        assert 1 <= threads() <= 8


class TestRemoteCache:
    """fetch_remote over a stand-in SFTP serving the local filesystem, with
    sizes, so the download cache can tell a file it already has."""

    class _SFTP:
        def __init__(self):
            self.fetched = []

        def stat(self, path):
            st = os.stat(path)
            return type("A", (), {"st_mtime": int(st.st_mtime), "st_size": st.st_size})()

        def get(self, remote, local):
            import shutil
            self.fetched.append(remote)
            shutil.copy(remote, local)

    @pytest.fixture
    def remote(self, tmp_path):
        from conftest import make_png

        src = tmp_path / "remote"
        src.mkdir()
        for s in ("A", "B"):
            (src / f"{s}.png").write_bytes(make_png())
        return src

    def _jobs(self, src, names=("A", "B", "C")):
        return [((i,), (src / f"{n}.png").as_posix()) for i, n in enumerate(names)]

    def test_second_fetch_comes_from_the_cache(self, remote, tmp_path):
        from juxt.loader import fetch_remote

        sftp, cache = self._SFTP(), str(tmp_path / "cache")
        first = fetch_remote(self._jobs(remote), cache, sftp, {})
        assert [first[(i,)][0] for i in range(3)] == ["fetched", "fetched", "missing"]
        sftp.fetched.clear()
        second = fetch_remote(self._jobs(remote), cache, sftp, {})
        assert [second[(i,)][0] for i in range(3)] == ["cached", "cached", "missing"]
        assert sftp.fetched == []
        leftovers = [f for _d, _s, fs in os.walk(cache) for f in fs if f.endswith(".part")]
        assert leftovers == []

    def test_changed_file_is_fetched_again(self, remote, tmp_path):
        from conftest import make_png
        from juxt.loader import fetch_remote

        sftp, cache = self._SFTP(), str(tmp_path / "cache")
        fetch_remote(self._jobs(remote), cache, sftp, {})
        (remote / "A.png").write_bytes(make_png(r=1) + b"\0")       # new size
        sftp.fetched.clear()
        fetch_remote(self._jobs(remote), cache, sftp, {})
        assert sftp.fetched == [(remote / "A.png").as_posix()]

    def test_skip_unchanged_reports_same(self, remote, tmp_path):
        from juxt.loader import fetch_remote

        sftp, cache, mtimes = self._SFTP(), str(tmp_path / "cache"), {}
        fetch_remote(self._jobs(remote), cache, sftp, mtimes)
        again = fetch_remote(self._jobs(remote), cache, sftp, mtimes, skip_unchanged=True)
        assert again[(0,)][0] == again[(1,)][0] == "same"

    def test_download_dir_outlives_the_session_unless_disabled(self, monkeypatch, tmp_path):
        from juxt.config import RemoteConfig
        from juxt.loader import remote_download_dir, set_remote_cache_days

        r = RemoteConfig(host="box", user="me")
        kept = remote_download_dir(r)
        assert kept.startswith(os.environ["JUXT_CACHE_DIR"]) and kept.endswith("me@box_22")
        set_remote_cache_days(0)
        try:
            assert not remote_download_dir(r).startswith(os.environ["JUXT_CACHE_DIR"])
        finally:
            set_remote_cache_days(7)

    def test_cached_pattern_is_where_the_downloads_go(self):
        from juxt.config import RemoteConfig
        from juxt.loader import cached_pattern, local_cache_path, remote_download_dir

        r = RemoteConfig(host="box", user="me", port=2222)
        pattern = cached_pattern(r, "/data/{sensor}/{**plot}_*.png")
        root = Path(os.environ["JUXT_CACHE_DIR"]).as_posix()
        assert pattern == f"{root}/me@box_2222/data/{{sensor}}/{{**plot}}_*.png"
        download = local_cache_path(remote_download_dir(r), "/data/A/x/y_1.png")
        assert download.as_posix() == f"{root}/me@box_2222/data/A/x/y_1.png"

    def test_mark_cache_used_keeps_the_servers_mtime(self, tmp_path):
        from juxt.loader import mark_cache_used

        f = tmp_path / "a.png"
        f.write_bytes(b"x")
        os.utime(f, (1000, 2000))
        mark_cache_used([str(f), str(tmp_path / "gone.png")])
        st = os.stat(f)
        assert st.st_mtime == 2000 and st.st_atime > 2000

    def test_prune_drops_files_unused_for_days(self, tmp_path):
        import time

        from juxt.loader import prune_remote_cache

        old, new = tmp_path / "h" / "old.png", tmp_path / "h" / "new.png"
        old.parent.mkdir()
        old.write_bytes(b"x")
        new.write_bytes(b"x")
        week_ago = time.time() - 8 * 86400
        os.utime(old, (week_ago, week_ago))
        assert prune_remote_cache(7, tmp_path) == 1
        assert not old.exists() and new.exists()

    def test_prune_spares_a_fresh_empty_directory(self, tmp_path):
        """A download may have just made it; its file is still to come."""
        import time

        from juxt.loader import prune_remote_cache

        stale, fresh = tmp_path / "h" / "stale", tmp_path / "h" / "fresh"
        stale.mkdir(parents=True)
        fresh.mkdir()
        week_ago = time.time() - 8 * 86400
        os.utime(stale, (week_ago, week_ago))
        prune_remote_cache(7, tmp_path)
        assert fresh.is_dir() and not stale.exists()

    def test_preload_remote_reuses_an_earlier_session(self, qtbot, remote, monkeypatch):
        from juxt.config import RemoteConfig
        from juxt.loader import MissingPixmap, preload_remote

        sftp = self._SFTP()
        client = type("C", (), {"close": lambda self: None})()
        sftp.close = lambda: None
        monkeypatch.setattr("juxt.loader._connect_sftp", lambda remote, pw=None: (client, sftp))
        args = (remote.as_posix() + "/{s}.png", {"s": ["A", "B"]}, RemoteConfig(host="h"))
        preload_remote(*args)
        sftp.fetched.clear()
        pixmaps, _dir, mtimes = preload_remote(*args)
        assert sftp.fetched == []
        assert not any(isinstance(pm, MissingPixmap) for pm in pixmaps.values())
        assert set(mtimes) == {(0,), (1,)}
