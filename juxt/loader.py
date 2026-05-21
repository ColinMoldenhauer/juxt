from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
from itertools import product
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable

log = logging.getLogger(__name__)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

if TYPE_CHECKING:
    from .config import RemoteConfig


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


def _connect_sftp(
    remote: "RemoteConfig",
    get_password: Callable[[str], str | None] | None = None,
) -> "tuple[paramiko.SSHClient, paramiko.SFTPClient]":
    """Open an authenticated SSH+SFTP session, optionally prompting for a password.

    Resolves host aliases and keys via ~/.ssh/config.
    Requires paramiko: pip install juxt[ssh]
    """
    try:
        import paramiko
    except ImportError:
        raise ImportError(
            "paramiko is required for SSH support. Install it with:\n"
            "    pip install juxt[ssh]"
        )

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


def preload_remote(
    template: str,
    axes: dict[str, list[str]],
    remote: "RemoteConfig",
    progress: Callable[[int, int], None] | None = None,
    get_password: Callable[[str], str | None] | None = None,
) -> tuple[dict[tuple[int, ...], QPixmap], str, dict[tuple[int, ...], float]]:
    """Download every image from a remote host via SFTP, then preload into pixmaps.

    Progress runs 0 → 2*N: first half is downloading, second half is pixmap decoding.
    Returns (pixmaps, tmpdir, mtimes) where mtimes maps each coordinate key to the
    remote file's mtime at download time — used by poll_remote_with_sftp to skip
    unchanged files on subsequent polls.
    Requires paramiko: pip install juxt[ssh]
    """
    axis_names = list(axes.keys())
    axis_values = list(axes.values())
    combos = list(product(*axis_values))
    n = len(combos)

    tmpdir = tempfile.mkdtemp(prefix="juxt_ssh_")
    atexit.register(shutil.rmtree, tmpdir, ignore_errors=True)

    client, sftp = _connect_sftp(remote, get_password)

    mtimes: dict[tuple[int, ...], float] = {}
    for i, combo in enumerate(combos):
        if progress:
            progress(i, n * 2)
        mapping = dict(zip(axis_names, combo))
        remote_path = template.format(**mapping)
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))
        local_path = Path(tmpdir) / PurePosixPath(remote_path.lstrip("/"))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            attr = sftp.stat(remote_path)
            mtimes[key] = attr.st_mtime
            sftp.get(remote_path, str(local_path))
        except OSError:
            log.warning("Remote file not found: %s", remote_path)

    sftp.close()
    client.close()

    local_template = str(Path(tmpdir) / PurePosixPath(template.lstrip("/")))

    def _offset_progress(i: int, _n: int):
        if progress:
            progress(n + i, n * 2)

    return preload(local_template, axes, _offset_progress), tmpdir, mtimes


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
    """
    axis_names = list(axes.keys())
    axis_values = list(axes.values())
    combos = list(product(*axis_values))

    changed: list[tuple[tuple[int, ...], str | None]] = []
    for i, combo in enumerate(combos):
        if on_progress:
            on_progress(i, len(combos))
        mapping = dict(zip(axis_names, combo))
        remote_path = template.format(**mapping)
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))

        try:
            new_mtime = sftp.stat(remote_path).st_mtime
        except OSError:
            if key in mtimes:
                # File previously existed but is now gone — surface the change
                del mtimes[key]
                changed.append((key, None))
            continue

        if mtimes.get(key) == new_mtime:
            continue  # unchanged — skip download entirely

        local_path = Path(tmpdir) / PurePosixPath(remote_path.lstrip("/"))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sftp.get(remote_path, str(local_path))
            mtimes[key] = new_mtime
            changed.append((key, str(local_path)))
        except OSError:
            pass

    return changed
