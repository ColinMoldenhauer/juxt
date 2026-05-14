from __future__ import annotations
import argparse
import signal
import sys
import warnings
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QProgressDialog

from .config import load_config
from .detect import IMAGE_EXTENSIONS, detect_config, prompt_rename
from .loader import preload
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
                   help="skip axis naming prompt and use dummy names")
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
            n_images = sum(
                1 for f in path.iterdir()
                if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
            )
            config, sep = detect_config(path, args.separator)
            if not args.auto:
                config = prompt_rename(config, n_images, sep, path)
        elif path.suffix.lower() in (".yaml", ".yml"):
            config = load_config(str(path))
        else:
            print(f"Error: {args.path!r} is not a directory or YAML config file", file=sys.stderr)
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    total = 1
    for vs in config.axes.values():
        total *= len(vs)

    progress = QProgressDialog("Loading images…", None, 0, total)
    progress.setWindowTitle("juxt")
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)

    def on_progress(i: int, n: int):
        progress.setValue(i)
        app.processEvents()

    pixmaps = preload(config.template, config.axes, on_progress)
    progress.close()

    window = MainWindow(config, pixmaps)
    window.showMaximized()
    app.processEvents()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    _force_focus(window)

    # Qt's C++ event loop blocks Python signal delivery. Install a handler
    # that calls app.quit(), and tick a no-op timer so Python wakes up
    # periodically and can actually invoke it.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sigint_tick = QTimer()
    sigint_tick.timeout.connect(lambda: None)
    sigint_tick.start(200)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
