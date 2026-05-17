from __future__ import annotations
import argparse
import signal
import socket
import sys
import warnings
from pathlib import Path

from PySide6.QtCore import Qt, QSocketNotifier, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QProgressDialog

from .config import load_config
from .detect import _iter_images, detect_config, prompt_rename
from .loader import preload, preload_remote
from .viewer import MainWindow


def _force_focus(window):
    """Bring the window to the foreground even when launched from a terminal.

    On Windows the OS blocks focus-stealing by default. The AttachThreadInput
    trick borrows the foreground thread's lock so SetForegroundWindow succeeds.
    On other platforms activateWindow/raise_ is sufficient.
    """
    if sys.platform == "win32":
        import ctypes
        hwnd = int(window.winId())
        fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
        fg_tid = ctypes.windll.user32.GetWindowThreadProcessId(fg_hwnd, None)
        our_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if fg_tid != our_tid:
            ctypes.windll.user32.AttachThreadInput(fg_tid, our_tid, True)
            ctypes.windll.user32.BringWindowToTop(hwnd)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.AttachThreadInput(fg_tid, our_tid, False)
    else:
        window.activateWindow()
        window.raise_()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="juxt", description="Geospatial plot comparison tool")
    p.add_argument("path", nargs="?", default=".", metavar="PATH",
                   help="directory to scan, or YAML config file (default: current directory)")
    p.add_argument("-s", "--separator", metavar="SEP", nargs="+",
                   help="separator(s) for auto-detection, space-separated (default: auto)")
    p.add_argument("-a", "--auto", action="store_true",
                   help="skip axis naming prompt and use auto-detected names")
    p.add_argument("--max-depth", type=int, default=None, metavar="N",
                   help="maximum subdirectory depth for image search (default: unlimited)")
    return p.parse_args()


def main():
    args = _parse_args()

    if sys.platform == "win32" and Path(sys.executable).stem.lower() in ("python", "pythonw"):
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("juxt")

    app = QApplication(sys.argv)
    app.setApplicationName("juxt")
    _logo = Path(__file__).parent / "assets" / "logo_transparent.ico"
    if not _logo.exists():  # fallback for editable installs
        _logo = Path(__file__).parent.parent / "docs" / "assets" / "logo_transparent.ico"
    app_icon = QIcon()
    if _logo.exists():
        app_icon = QIcon(str(_logo))
        if app_icon.isNull():
            warnings.warn(f"Icon file found but failed to load: {_logo}")
        else:
            app.setWindowIcon(app_icon)
    else:
        warnings.warn(f"No logo file found at {_logo}")

    try:
        path = Path(args.path)
        if path.is_dir():
            files = _iter_images(path, args.max_depth)
            config, sep = detect_config(path, args.separator, args.max_depth)
            if not args.auto:
                config = prompt_rename(config, len(files), sep, path, args.max_depth)
        elif path.suffix.lower() in (".yaml", ".yml"):
            config = load_config(str(path))
        else:
            print(f"Error: {args.path!r} is not a directory or YAML config file", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    n_images = 1
    for vs in config.axes.values():
        n_images *= len(vs)

    is_remote = config.remote is not None
    total_steps = n_images * 2 if is_remote else n_images
    first_label = "Downloading images…" if is_remote else "Loading images…"

    # Reliable Ctrl+C in any nested Qt event loop (loading, dialogs, etc.).
    # set_wakeup_fd writes a byte to wsock when SIGINT arrives; QSocketNotifier
    # delivers it as a socket event so Python runs the handler immediately
    # without needing a polling timer.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    _rsock, _wsock = socket.socketpair()
    _wsock.setblocking(False)
    signal.set_wakeup_fd(_wsock.fileno())
    _signotifier = QSocketNotifier(_rsock.fileno(), QSocketNotifier.Type.Read)
    _signotifier.activated.connect(lambda *_: (_rsock.recv(4096), app.quit()))

    progress = QProgressDialog(first_label, None, 0, total_steps)
    progress.setWindowTitle("juxt")
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    def on_progress(i: int, n: int):
        if is_remote and i == n_images:
            progress.setLabelText("Loading images…")
        progress.setValue(i)
        app.processEvents()

    if is_remote:
        def get_password(label: str) -> str | None:
            # Drop ApplicationModal before hiding so Qt doesn't keep progress
            # at the top of the modal stack, which would block the password dialog.
            progress.setWindowModality(Qt.NonModal)
            progress.hide()
            app.processEvents()
            dlg = QInputDialog()
            dlg.setWindowTitle("SSH Authentication")
            dlg.setLabelText(f"Password / passphrase for {label}:")
            dlg.setTextEchoMode(QLineEdit.EchoMode.Password)
            QTimer.singleShot(0, lambda: _force_focus(dlg))
            ok = dlg.exec()
            progress.setWindowModality(Qt.ApplicationModal)
            progress.show()
            return dlg.textValue() if ok else None

        pixmaps = preload_remote(config.template, config.axes, config.remote, on_progress, get_password)
    else:
        pixmaps = preload(config.template, config.axes, on_progress)
    progress.close()

    window = MainWindow(config, pixmaps)
    window.showMaximized()
    app.processEvents()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    _force_focus(window)

    # QTimer import kept for other uses; no polling timer needed for SIGINT.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
