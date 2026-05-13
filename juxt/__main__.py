from __future__ import annotations
import signal
import sys
import warnings
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QProgressDialog

from .config import load_config
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


def main():
    if len(sys.argv) < 2:
        print("Usage: juxt <config.yaml>")
        sys.exit(1)

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
        config = load_config(sys.argv[1])
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
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
