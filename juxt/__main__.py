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

from .config import Config, _auto_keys, dump_config, load_config
from .detect import (
    _axes_from_local_template,
    _axes_from_remote_template,
    _is_remote_pattern,
    _iter_images,
    _parse_remote_pattern,
    detect_config,
    detect_config_remote,
    prompt_rename,
)
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


def _print_help() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    content = [
        "",
        "juxt — navigate N-dimensional plot hypercubes",
        "",
        "Flip through congruent images using",
        "keyboard-driven axis control to spot visual",
        "differences at a glance.",
        "",
        "keys (all modes)",
        None,  # horizontal rule
        "↑ ↓ ← →    navigate axes",
        "Space       toggle between two positions",
        "1–9         jump to Nth value on active axis",
        ":           command mode  (:fit  :zoom N  :q)",
        "Ctrl+H      toggle status bar",
        "",
    ]
    pad = 2
    max_w = max(len(line) for line in content if line is not None)
    inner = max_w + 2 * pad
    top = "╭" + "─" * inner + "╮"
    bot = "╰" + "─" * inner + "╯"
    rows = [top]
    for line in content:
        if line is None:
            rows.append("│" + " " * pad + "─" * (inner - 2 * pad) + " " * pad + "│")
        else:
            rows.append("│" + " " * pad + line.ljust(inner - 2 * pad) + " " * pad + "│")
    rows.append(bot)
    print("\n".join(rows))
    print("""
  usage:  juxt [options] [PATH]

  PATH accepts several forms (auto-detected):
    /path/to/dir              scan directory, detect axes from filenames
    plots/{sensor}_{date}.png local template with explicit placeholders
    host:/path/to/dir         remote directory over SSH  (requires juxt[ssh])
    host:/path/{sensor}.png   remote template over SSH   (requires juxt[ssh])
    config.yaml               explicit YAML config file
    (default: current directory)

  -s, --separator SEP [...]   separator(s) for auto-detection
  -a, --auto                  skip axis naming prompt
      --max-depth N           max subdirectory search depth
      --save PATH             save resolved config to PATH after detection
      --no-watch              disable automatic file watching (local only)
      --watch-interval SEC    poll remote for changes every SEC seconds (default: 5, 0 to disable)
      --axis-h NAME           lock ←/→ to this axis on startup
      --axis-v NAME           lock ↑/↓ to this axis on startup
  -h, --help                  show this message and exit

  navigation modes (switch with :mode)
    tap   letter=+1/LETTER=-1 on that axis (default)
    seek  type to search axes and values by prefix
    pin   letter focuses axis; arrows navigate""")


class _HelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, *_):
        _print_help()
        parser.exit()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="juxt", add_help=False)
    p.add_argument("path", nargs="?", default=".", metavar="PATH")
    p.add_argument("-s", "--separator", metavar="SEP", nargs="+")
    p.add_argument("-a", "--auto", action="store_true")
    p.add_argument("--max-depth", type=int, default=None, metavar="N")
    p.add_argument("--save", metavar="PATH")
    p.add_argument("--no-watch", action="store_true", default=False)
    p.add_argument("--watch-interval", type=int, default=5, metavar="SEC")
    p.add_argument("--axis-h", metavar="NAME", default=None)
    p.add_argument("--axis-v", metavar="NAME", default=None)
    p.add_argument("-h", "--help", action=_HelpAction)
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

    _pw_cache: list[str | None] = [None]

    def _ask_password(label: str) -> str | None:
        """Password dialog for the detection phase (no progress to manage)."""
        if _pw_cache[0] is not None:
            return _pw_cache[0]
        dlg = QInputDialog()
        dlg.setWindowTitle("SSH Authentication")
        dlg.setLabelText(f"Password for {label}:")
        dlg.setTextEchoMode(QLineEdit.EchoMode.Password)
        if not app_icon.isNull():
            QTimer.singleShot(0, lambda: dlg.setWindowIcon(app_icon))
        QTimer.singleShot(0, lambda: _force_focus(dlg))
        ok = dlg.exec()
        _pw_cache[0] = dlg.textValue() if ok else None
        return _pw_cache[0]

    try:
        raw = args.path
        if _is_remote_pattern(raw):
            remote_cfg, remote_tmpl = _parse_remote_pattern(raw)
            if '{' in remote_tmpl:
                axes = _axes_from_remote_template(remote_tmpl, remote_cfg, _ask_password)
                config = Config(template=remote_tmpl, axes=axes,
                                keys=_auto_keys(axes), remote=remote_cfg)
            else:
                config, sep, n_remote = detect_config_remote(
                    remote_tmpl, remote_cfg, args.separator, _ask_password)
                if not args.auto:
                    config = prompt_rename(config, n_remote, sep)
        elif Path(raw).is_dir():
            p = Path(raw)
            files = _iter_images(p, args.max_depth)
            config, sep = detect_config(p, args.separator, args.max_depth)
            if not args.auto:
                config = prompt_rename(config, len(files), sep, p, args.max_depth)
        elif raw.lower().endswith((".yaml", ".yml")):
            config = load_config(raw)
        elif '{' in raw:
            axes = _axes_from_local_template(raw)
            config = Config(template=raw, axes=axes, keys=_auto_keys(axes))
        else:
            print(
                f"Error: {raw!r} is not a directory, YAML config, "
                "template pattern, or remote path",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.save:
        try:
            dump_config(config, args.save)
            print(f"Config saved to {args.save}")
        except Exception as e:
            print(f"Warning: could not save config: {e}", file=sys.stderr)

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
    app.processEvents()
    if not app_icon.isNull():
        progress.setWindowIcon(app_icon)
        app.processEvents()
    _force_focus(progress)

    def on_progress(i: int, n: int):
        if is_remote and i == n_images:
            progress.setLabelText("Loading images…")
        progress.setValue(i)
        app.processEvents()

    if is_remote:
        def get_password(label: str) -> str | None:
            if _pw_cache[0] is not None:
                return _pw_cache[0]
            # Drop ApplicationModal before hiding so Qt doesn't keep progress
            # at the top of the modal stack, which would block the password dialog.
            progress.setWindowModality(Qt.NonModal)
            progress.hide()
            app.processEvents()
            dlg = QInputDialog()
            dlg.setWindowTitle("SSH Authentication")
            dlg.setLabelText(f"Password for {label}:")
            dlg.setTextEchoMode(QLineEdit.EchoMode.Password)
            if not app_icon.isNull():
                QTimer.singleShot(0, lambda: dlg.setWindowIcon(app_icon))
            QTimer.singleShot(0, lambda: _force_focus(dlg))
            ok = dlg.exec()
            progress.setWindowModality(Qt.ApplicationModal)
            progress.show()
            _pw_cache[0] = dlg.textValue() if ok else None
            return _pw_cache[0]

        pixmaps, remote_tmpdir, remote_mtimes = preload_remote(
            config.template, config.axes, config.remote, on_progress, get_password)
    else:
        pixmaps = preload(config.template, config.axes, on_progress)
        remote_tmpdir = None
        remote_mtimes = None
    progress.close()

    axis_names = list(config.axes.keys())
    for flag, name in (("--axis-h", args.axis_h), ("--axis-v", args.axis_v)):
        if name and name not in axis_names:
            print(f"Warning: {flag} {name!r} not found in axes {axis_names}", file=sys.stderr)

    window = MainWindow(
        config, pixmaps,
        watch=not args.no_watch,
        remote_tmpdir=remote_tmpdir,
        get_password=_ask_password if is_remote else None,
        poll_interval=args.watch_interval,
        remote_mtimes=remote_mtimes,
        axis_h=args.axis_h,
        axis_v=args.axis_v,
    )
    window.showMaximized()
    app.processEvents()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    _force_focus(window)

    # QTimer import kept for other uses; no polling timer needed for SIGINT.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
