from __future__ import annotations

import atexit
import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import product
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable

log = logging.getLogger(__name__)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

if TYPE_CHECKING:
    from .config import RemoteConfig


class MissingPixmap(QPixmap):
    """Placeholder for a combination whose file is absent or unreadable."""


def _error_pixmap(path: str) -> QPixmap:
    pm = MissingPixmap(480, 320)
    pm.fill(QColor(40, 40, 40))
    p = QPainter(pm)
    p.setPen(QColor(200, 80, 80))
    p.setFont(QFont("monospace", 10))
    p.drawText(pm.rect(), Qt.AlignCenter | Qt.TextWordWrap, f"MISSING\n{path}")
    p.end()
    return pm


# ── parallel loading ─────────────────────────────────────────────────────────

# Images read and decoded, or downloaded, at once (`loading.threads`); 0 picks
# a default from the CPU count.  Decoding a large PNG takes tens of
# milliseconds and reading it off a network share longer still, so a pool
# cuts startup several-fold; past ~8 threads disks and shares stop scaling.
_threads = 0
_MAX_AUTO_THREADS = 8
# SFTP channels opened on one SSH connection.  OpenSSH allows 10 sessions per
# connection by default (MaxSessions), and the session holds one already.
_MAX_SFTP_CHANNELS = 6


def set_threads(n: int) -> None:
    """Set the `loading.threads` setting: 0 = automatic."""
    global _threads
    _threads = max(0, int(n))


def threads() -> int:
    """How many images to read and decode at once."""
    return _threads or max(1, min(_MAX_AUTO_THREADS, os.cpu_count() or 1))


Key = tuple[int, ...]


def load_images(
    jobs: Iterable[tuple[Key, str | None, str]],
    progress: Callable[[int, int], None] | None = None,
    *,
    offset: int = 0,
    total: int | None = None,
) -> dict[Key, QPixmap]:
    """Read and decode the images of *jobs* in a thread pool.

    Each job is `(key, path, shown)`: the file to load (None when there is
    none) and the path the MISSING placeholder names if it cannot be read.
    Workers produce QImages; the QPixmaps are made here, so call this on the
    GUI thread.  At most two images per worker are in flight, which bounds
    the memory held by decoded images not yet handed over.

    *progress* is called as `progress(offset + done, total)`, once before the
    first image and after each one; *total* defaults to `offset` plus the
    number of jobs.  It may raise (a cancel button, Ctrl+C) to abandon the
    load: images already being decoded finish, the rest are skipped.
    """
    jobs = list(jobs)
    total = offset + len(jobs) if total is None else total
    out: dict[Key, QPixmap] = {}
    if progress:
        progress(offset, total)
    todo = iter(jobs)
    in_flight: dict = {}
    n_threads = threads()
    pool = ThreadPoolExecutor(n_threads, thread_name_prefix="juxt-load")
    try:
        def fill():
            while len(in_flight) < 2 * n_threads:
                job = next(todo, None)
                if job is None:
                    return
                key, path, shown = job
                if path is None:
                    out[key] = _error_pixmap(shown)
                    continue
                in_flight[pool.submit(QImage, path)] = (key, shown)

        fill()
        done_n = len(out)
        if progress and done_n:
            progress(offset + done_n, total)
        while in_flight:
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in finished:
                key, shown = in_flight.pop(fut)
                img = fut.result()
                out[key] = _error_pixmap(shown) if img.isNull() else QPixmap.fromImage(img)
            fill()
            if progress:
                progress(offset + len(out), total)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return out


def preload(
    template: str,
    axes: dict[str, list[str]],
    progress: Callable[[int, int], None] | None = None,
) -> dict[tuple[int, ...], QPixmap]:
    """Load every image into memory. Returns dict keyed by index-tuple."""
    axis_names = list(axes.keys())
    axis_values = list(axes.values())
    jobs = []
    for combo in product(*axis_values):
        path = template.format(**dict(zip(axis_names, combo)))
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))
        jobs.append((key, path, path))
    pixmaps = load_images(jobs, progress)
    if progress:
        progress(len(jobs), len(jobs))
    return pixmaps


def local_cache_path(tmpdir: str, remote_path: str) -> Path:
    """Where the local copy of *remote_path* goes: under *tmpdir*, always.

    The remote path's parts are joined one by one, so an absolute one never
    replaces *tmpdir*: Python 3.12's pathlib reads `C:/x`, which a Windows
    SSH server reports as `/C:/x`, as a drive path of its own. `..` parts
    are dropped, and on Windows ':' and '\\' (not allowed in a file name
    there, or a separator) become '_'.
    """
    parts = []
    for part in PurePosixPath(remote_path).parts:
        if part in ("/", ".", ".."):
            continue
        if os.name == "nt":
            part = part.replace(":", "_").replace("\\", "_")
        parts.append(part)
    return Path(tmpdir).joinpath(*parts)


def _connect_sftp(
    remote: "RemoteConfig",
    get_password: Callable[[str], str | None] | None = None,
) -> "tuple[paramiko.SSHClient, paramiko.SFTPClient]":
    """Open an authenticated SSH+SFTP session, optionally prompting for a password.

    Resolves host aliases and keys via ~/.ssh/config.
    """
    import paramiko   # here, not at the top: only remote sessions pay its import time

    log.debug("Connecting to %s@%s:%s", remote.user or "", remote.host, remote.port)
    host, user, port, key = remote.host, remote.user, remote.port, remote.key_path
    ssh_cfg_path = Path("~/.ssh/config").expanduser()
    if ssh_cfg_path.exists():
        ssh_cfg = paramiko.SSHConfig()
        ssh_cfg.parse(ssh_cfg_path.open())
        h = ssh_cfg.lookup(host)
        host = h.get("hostname", host)
        user = user or h.get("user")
        port = port if port != 22 else int(h.get("port", 22))
        if not key and h.get("identityfile"):
            key = h["identityfile"][0]

    def _make_client(password: str | None = None) -> "paramiko.SSHClient":
        c = paramiko.SSHClient()
        c.load_system_host_keys()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = {"hostname": host, "port": port}
        if user:
            kw["username"] = user
        if password:
            kw["password"] = password
        elif key:
            kw["key_filename"] = str(Path(key).expanduser())
        c.connect(**kw)
        return c

    try:
        client = _make_client()
    except (paramiko.AuthenticationException, paramiko.PasswordRequiredException):
        if get_password is None:
            raise
        label = f"{user + '@' if user else ''}{host}"
        pw = get_password(label)
        if pw is None:
            raise paramiko.AuthenticationException("No password provided")
        client = _make_client(password=pw)
    sftp = client.open_sftp()
    log.info("Connected to %s via SFTP", host)
    return client, sftp


# ── remote download cache ────────────────────────────────────────────────────

# Days a downloaded file is kept unused (`loading.remote_cache_days`); 0 keeps
# nothing past the session, in a temp dir as before the cache existed.
_remote_cache_days = 7


def set_remote_cache_days(days: float) -> None:
    global _remote_cache_days
    _remote_cache_days = max(0.0, float(days))


def remote_cache_root() -> Path:
    """The per-user cache directory for remote downloads (`JUXT_CACHE_DIR`
    overrides it, which the tests use)."""
    override = os.environ.get("JUXT_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "juxt" / "Cache" / "ssh"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "juxt" / "ssh"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "juxt" / "ssh"


def remote_cache_dir(remote: "RemoteConfig") -> Path:
    """The cache directory of *remote*, one per `user@host_port` under
    `remote_cache_root`; not created here."""
    name = f"{remote.user + '@' if remote.user else ''}{remote.host}_{remote.port}"
    name = "".join(c if c.isalnum() or c in "@._-" else "_" for c in name)
    return remote_cache_root() / name


def cached_pattern(remote: "RemoteConfig", remote_pattern: str) -> str:
    """*remote_pattern* (a remote directory, template or wildcard path) moved
    to where its downloads sit in the cache, for browsing them offline.

    The same mapping as the downloads themselves (`local_cache_path`), so
    `{placeholders}` and wildcards carry over unchanged.
    """
    return local_cache_path(str(remote_cache_dir(remote)), remote_pattern).as_posix()


def mark_cache_used(paths) -> None:
    """Count a local read of cached downloads as a use, so a cache that is
    only browsed offline (`juxt --cached`) is not pruned under the viewer:
    access time = now, the modification time (the server's) is kept."""
    now = time.time()
    for path in paths:
        try:
            os.utime(path, (now, os.stat(path).st_mtime))
        except OSError:
            pass


def remote_download_dir(remote: "RemoteConfig") -> str:
    """Where the files of a session on *remote* are downloaded to.

    With the cache on, a directory per `user@host_port` that outlives the
    session, so the next one only fetches what changed on the server; with
    `remote_cache_days: 0`, a temp dir removed at exit.
    """
    if _remote_cache_days <= 0:
        tmpdir = tempfile.mkdtemp(prefix="juxt_ssh_")
        atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)
        return tmpdir
    path = remote_cache_dir(remote)
    path.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=prune_remote_cache, daemon=True,
                     name="juxt-cache-prune").start()
    return str(path)


def prune_remote_cache(days: float | None = None, root: Path | None = None) -> int:
    """Delete cached downloads not used for *days*; returns how many.

    "Used" is the file's access time, which juxt sets itself on every
    download or cache hit (mount options like `noatime` only stop the
    automatic updates), while the modification time holds the server's.
    """
    days = _remote_cache_days if days is None else days
    root = remote_cache_root() if root is None else root
    if days <= 0 or not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for dirpath, _dirs, files in os.walk(root, topdown=False):
        for f in files:
            path = os.path.join(dirpath, f)
            try:
                if os.stat(path).st_atime < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        # A directory goes once empty, but only if it, too, has been left
        # alone that long: a download running meanwhile may just have made
        # it and not written its file yet.
        if dirpath != str(root):
            try:
                if os.stat(dirpath).st_mtime < cutoff:
                    os.rmdir(dirpath)
            except OSError:
                pass
    if removed:
        log.info("Pruned %d cached download(s) unused for %g days", removed, days)
    return removed


def _cache_hit(local: Path, attr) -> bool:
    """Whether *local* already holds the remote file described by *attr*."""
    size = getattr(attr, "st_size", None)
    if size is None:
        return False
    try:
        st = local.stat()
    except OSError:
        return False
    return st.st_size == size and int(st.st_mtime) == int(attr.st_mtime)


def _mark_used(local: Path, remote_mtime: float) -> None:
    """Access time = now (for pruning), modification time = the server's."""
    try:
        os.utime(local, (time.time(), remote_mtime))
    except OSError:
        pass


def _sftp_channels(sftp) -> list:
    """*sftp* plus extra SFTP channels on its SSH connection, one per
    download thread; just *sftp* if the server refuses more."""
    channels = [sftp]
    want = min(threads(), _MAX_SFTP_CHANNELS)
    try:
        import paramiko
        transport = sftp.get_channel().get_transport()
        while len(channels) < want:
            channels.append(paramiko.SFTPClient.from_transport(transport))
    except Exception as e:      # stand-in clients, MaxSessions reached, …
        if len(channels) == 1:
            log.debug("Downloading over one SFTP channel (%s)", e)
    return channels


def fetch_remote(
    jobs: list[tuple[Key, str]],
    local_dir: str,
    sftp,
    mtimes: dict[Key, float],
    progress: Callable[[int, int], None] | None = None,
    *,
    skip_unchanged: bool = False,
    offset: int = 0,
    total: int | None = None,
) -> dict[Key, tuple[str, str | None]]:
    """Bring the remote files of *jobs* (`(key, remote_path)`) into *local_dir*.

    Runs over several SFTP channels of *sftp*'s connection at once.  A file
    whose local copy matches the server's size and modification time is not
    downloaded again.  Each download goes to a temp name first and is renamed
    into place, so a session reading the shared cache never sees half a file.

    Returns `{key: (status, local_path)}`, status one of "fetched", "cached",
    "missing" (no such remote file; path None) and, with *skip_unchanged*,
    "same" (the mtime recorded in *mtimes* still holds; nothing done).
    *mtimes* is updated with the server's mtime of every file present.
    *progress* behaves as in :func:`load_images`.
    """
    total = offset + len(jobs) if total is None else total
    if progress:
        progress(offset, total)
    out: dict[Key, tuple[str, str | None]] = {}
    if not jobs:
        return out
    channels = _sftp_channels(sftp)
    free: queue.Queue = queue.Queue()
    for ch in channels:
        free.put(ch)
    known = dict(mtimes)

    def _one(key: Key, remote_path: str) -> tuple[str, str | None, float | None]:
        ch = free.get()
        try:
            try:
                attr = ch.stat(remote_path)
            except OSError:
                return "missing", None, None
            local = local_cache_path(local_dir, remote_path)
            if skip_unchanged and known.get(key) == attr.st_mtime:
                return "same", str(local), attr.st_mtime
            if _cache_hit(local, attr):
                _mark_used(local, attr.st_mtime)
                return "cached", str(local), attr.st_mtime
            local.parent.mkdir(parents=True, exist_ok=True)
            part = local.with_name(f".{local.name}.{os.getpid()}.{threading.get_ident()}.part")
            try:
                ch.get(remote_path, str(part))
                os.replace(part, local)
            except OSError:
                part.unlink(missing_ok=True)
                return "missing", None, None
            _mark_used(local, attr.st_mtime)
            return "fetched", str(local), attr.st_mtime
        finally:
            free.put(ch)

    pool = ThreadPoolExecutor(len(channels), thread_name_prefix="juxt-fetch")
    try:
        pending = {pool.submit(_one, key, rp): key for key, rp in jobs}
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in finished:
                key = pending.pop(fut)
                status, local, mtime = fut.result()
                out[key] = (status, local)
                if mtime is not None:
                    mtimes[key] = mtime
            if progress:
                progress(offset + len(out), total)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        for ch in channels[1:]:
            try:
                ch.close()
            except Exception:
                pass
    return out


def preload_remote(
    template: str,
    axes: dict[str, list[str]],
    remote: "RemoteConfig",
    progress: Callable[[int, int], None] | None = None,
    get_password: Callable[[str], str | None] | None = None,
) -> tuple[dict[tuple[int, ...], QPixmap], str, dict[tuple[int, ...], float]]:
    """Download every image from a remote host via SFTP, then preload into pixmaps.

    Progress runs 0 → 2*N: first half is downloading, second half is pixmap decoding.
    Returns (pixmaps, local_dir, mtimes) where mtimes maps each coordinate key to the
    remote file's mtime at download time — used by poll_remote_with_sftp to skip
    unchanged files on subsequent polls.  Files still in the download cache from
    an earlier session are not fetched again.
    """
    axis_names = list(axes.keys())
    axis_values = list(axes.values())
    jobs: list[tuple[Key, str]] = []
    for combo in product(*axis_values):
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))
        jobs.append((key, template.format(**dict(zip(axis_names, combo)))))
    n = len(jobs)

    local_dir = remote_download_dir(remote)
    client, sftp = _connect_sftp(remote, get_password)
    mtimes: dict[Key, float] = {}
    try:
        got = fetch_remote(jobs, local_dir, sftp, mtimes, progress, total=2 * n)
    finally:
        sftp.close()
        client.close()
    fetched = sum(status == "fetched" for status, _ in got.values())
    log.info("Remote: %d downloaded, %d from the cache, %d missing", fetched,
             sum(status == "cached" for status, _ in got.values()),
             sum(status == "missing" for status, _ in got.values()))
    for key, rp in jobs:
        if got[key][0] == "missing":
            log.warning("Remote file not found: %s", rp)

    pixmaps = load_images(((key, got[key][1], got[key][1] or rp) for key, rp in jobs),
                          progress, offset=n, total=2 * n)
    if progress:
        progress(n * 2, n * 2)
    return pixmaps, local_dir, mtimes


def download_remote_files(
    jobs: list[tuple[tuple[int, ...], str]],
    tmpdir: str,
    sftp: "paramiko.SFTPClient",
    mtimes: dict[tuple[int, ...], float],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[tuple[tuple[int, ...], str | None]]:
    """Download each `(key, remote_path)` of *jobs* into *tmpdir*.

    Records the remote mtime of every file fetched in *mtimes* under its key,
    so later polls skip it while unchanged. Returns `(key, local_path)` pairs
    in job order, with `local_path` None for a file the remote does not have;
    the caller decodes the pixmaps on the main thread.
    """
    got = fetch_remote(jobs, tmpdir, sftp, mtimes, on_progress)
    return [(key, got[key][1]) for key, _ in jobs]


def poll_remote_with_sftp(
    template: str,
    axes: dict[str, list[str]],
    tmpdir: str,
    sftp: "paramiko.SFTPClient",
    mtimes: dict[tuple[int, ...], float],
    on_progress: Callable[[int, int], None] | None = None,
) -> list[tuple[tuple[int, ...], str]]:
    """Check for changed files and download only those whose remote mtime differs.

    *mtimes* is updated in-place for every file that is re-downloaded.
    Returns a list of (key, local_path) pairs for the changed files only —
    the caller loads QPixmap objects on the main thread from those paths.
    A file that existed before and is now gone is reported with path None.
    """
    axis_names = list(axes.keys())
    axis_values = list(axes.values())
    jobs: list[tuple[Key, str]] = []
    for combo in product(*axis_values):
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))
        jobs.append((key, template.format(**dict(zip(axis_names, combo)))))
    before = set(mtimes)
    got = fetch_remote(jobs, tmpdir, sftp, mtimes, on_progress, skip_unchanged=True)
    changed: list[tuple[Key, str | None]] = []
    for key, _rp in jobs:
        status, local = got[key]
        if status == "missing":
            if key in before:       # it was there before: surface the change
                mtimes.pop(key, None)
                changed.append((key, None))
        elif status != "same":
            changed.append((key, local))
    return changed
