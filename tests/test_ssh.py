"""SSH integration tests.

These tests require a live SSH server and are skipped unless the following
environment variables are set:

    TEST_SSH_HOST   hostname (e.g. "localhost")
    TEST_SSH_PORT   port     (default 2222)
    TEST_SSH_KEY    path to private key file
    TEST_SSH_USER   remote username

In the GitHub Actions workflow these are set automatically by the "Set up
local SSH server" step, which generates a throwaway key pair and starts
a temporary sshd on port 2222.  No secrets are involved.
"""
from __future__ import annotations

import os
import struct
import zlib
from itertools import product as iproduct

import pytest

from juxt.config import RemoteConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_png() -> bytes:
    """Minimal valid 1×1 RGB PNG (no external deps)."""
    def chunk(name: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(struct.pack(">BBBB", 0, 128, 128, 128)))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ssh_remote():
    """RemoteConfig for the test server; skip module if env vars are absent."""
    host = os.environ.get("TEST_SSH_HOST")
    if not host:
        pytest.skip("TEST_SSH_HOST not set — skipping SSH tests")
    return RemoteConfig(
        host=host,
        user=os.environ.get("TEST_SSH_USER"),
        port=int(os.environ.get("TEST_SSH_PORT", "2222")),
        key_path=os.environ.get("TEST_SSH_KEY"),
    )


@pytest.fixture
def remote_plot_dir(ssh_remote):
    """Upload a small 2×2 grid of PNG files to the SSH server; yield remote path."""
    from juxt.loader import _connect_sftp

    axes = {"sensor": ["A", "B"], "date": ["d1", "d2"]}
    remote_base = f"/tmp/juxt_test_{os.getpid()}"

    client, sftp = _connect_sftp(ssh_remote)
    try:
        sftp.mkdir(remote_base)
        for sensor, date in iproduct(axes["sensor"], axes["date"]):
            remote_path = f"{remote_base}/{sensor}_{date}.png"
            with sftp.open(remote_path, "wb") as fh:
                fh.write(_make_png())
    finally:
        sftp.close()
        client.close()

    yield remote_base, axes

    # Cleanup — best-effort; CI VMs are ephemeral anyway
    client2, sftp2 = _connect_sftp(ssh_remote)
    try:
        for sensor, date in iproduct(axes["sensor"], axes["date"]):
            try:
                sftp2.remove(f"{remote_base}/{sensor}_{date}.png")
            except IOError:
                pass
        try:
            sftp2.rmdir(remote_base)
        except IOError:
            pass
    finally:
        sftp2.close()
        client2.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.ssh
def test_connect_and_close(ssh_remote):
    """Basic connectivity: open and immediately close an SSH+SFTP session."""
    from juxt.loader import _connect_sftp

    client, sftp = _connect_sftp(ssh_remote)
    sftp.close()
    client.close()


@pytest.mark.ssh
def test_sftp_listdir(ssh_remote):
    """SFTP can list a well-known directory (e.g. /tmp)."""
    from juxt.loader import _connect_sftp

    client, sftp = _connect_sftp(ssh_remote)
    try:
        entries = sftp.listdir("/tmp")
        assert isinstance(entries, list)
    finally:
        sftp.close()
        client.close()


@pytest.mark.ssh
def test_detect_config_remote(ssh_remote, remote_plot_dir):
    """detect_config_remote finds the right number of files and axes."""
    from juxt.detect import detect_config_remote

    remote_base, axes = remote_plot_dir
    cfg, seps, n_files = detect_config_remote(remote_base, ssh_remote)
    assert n_files == 4
    assert len(cfg.axes) == 2
    # Template must reference the remote base directory
    assert remote_base in cfg.template


@pytest.mark.ssh
def test_detect_config_remote_sets_remote_field(ssh_remote, remote_plot_dir):
    """The returned Config carries the RemoteConfig so callers can download."""
    from juxt.detect import detect_config_remote

    remote_base, _ = remote_plot_dir
    cfg, _, _ = detect_config_remote(remote_base, ssh_remote)
    assert cfg.remote is not None
    assert cfg.remote.host == ssh_remote.host


@pytest.mark.ssh
def test_preload_remote(ssh_remote, remote_plot_dir, qtbot):
    """preload_remote downloads files and returns valid pixmaps for all combos."""
    from juxt.loader import preload_remote

    remote_base, axes = remote_plot_dir
    template = f"{remote_base}/{{sensor}}_{{date}}.png"
    pixmaps = preload_remote(template, axes, ssh_remote)
    assert len(pixmaps) == 4
    assert all(not pm.isNull() for pm in pixmaps.values())


@pytest.mark.ssh
def test_preload_remote_missing_file_uses_error_pixmap(ssh_remote, remote_plot_dir, qtbot):
    """A missing remote file produces an error-pixmap placeholder, not a crash."""
    from juxt.loader import preload_remote

    remote_base, _ = remote_plot_dir
    # Include a value that has no corresponding file on the server
    axes = {"sensor": ["A", "B", "MISSING"], "date": ["d1"]}
    template = f"{remote_base}/{{sensor}}_{{date}}.png"
    pixmaps = preload_remote(template, axes, ssh_remote)
    assert len(pixmaps) == 3
    # The MISSING entry must be the error-pixmap (480 wide)
    missing_key = (2, 0)  # index 2 for "MISSING", index 0 for "d1"
    assert pixmaps[missing_key].width() == 480
