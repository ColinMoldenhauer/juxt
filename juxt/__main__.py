from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
from pathlib import Path

from .complete import SCRIPT_FLAGS, WORDS_FLAG, normalize_template
from .complete import main as _complete_main

# Shell completion runs on every Tab press, so answer it here — before Qt is
# imported — which also lets `juxt` itself back the completion when the
# standalone binary is all a user has installed.
if len(sys.argv) > 1 and sys.argv[1] in (*SCRIPT_FLAGS, WORDS_FLAG):
    sys.exit(_complete_main(sys.argv[1:]))

from PySide6.QtCore import Qt, QSocketNotifier, QTimer  # noqa: E402
from PySide6.QtGui import QCursor, QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QInputDialog,
    QLineEdit,
    QProgressDialog,
)
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
from .settings import load_settings
from .startup import StartupDialog
from .viewer import MainWindow

log = logging.getLogger(__name__)


def _setup_logging(level: str, log_file: str | None = None) -> None:
    fmt = "%(asctime)s  %(levelname)-8s  %(name)-24s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        format=fmt,
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        _qt_log = logging.getLogger("qt")
        _qt_levels = {
            QtMsgType.QtDebugMsg:    logging.DEBUG,
            QtMsgType.QtInfoMsg:     logging.INFO,
            QtMsgType.QtWarningMsg:  logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg:    logging.CRITICAL,
        }
        def _qt_handler(t, _, msg):
            _qt_log.log(_qt_levels.get(t, logging.WARNING), msg)
        qInstallMessageHandler(_qt_handler)
    except Exception:
        pass


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
        "↑ ↓ ← →         navigate axes",
        "Space           toggle between two positions",
        "Home / End      first / last value on active axis",
        "1–9             jump to Nth value on active axis",
        "Enter           toggle fullscreen",
        "Escape          exit fullscreen / cancel",
        ":               command mode  (:fit  :zoom N  :q)",
        "Ctrl+C          cancel command / value picker",
        "Ctrl+Shift+H    toggle status bar",
        "Ctrl+Shift+I    toggle info sidebar",
        "Ctrl+Shift+G    open the grid builder",
        "Ctrl+Shift+C    copy current image path",
        "Ctrl+Shift+K    toggle shortcut sidebar",
        "",
        "view controls",
        None,  # horizontal rule
        "0               reset zoom to 100%",
        "double-click    fit image to window",
        "drag            pan the image",
        "right-click     copy path / image menu",
        "wheel           step the ←/→ axis",
        "Shift+wheel     step the ↑/↓ axis",
        "Ctrl+wheel      zoom under the cursor",
        "",
        "mode keys",
        None,  # horizontal rule
        "tap    letter  +1 on that axis  (Letter = −1)",
        "       Ctrl+letter  open value picker",
        "seek   letter  incremental axis → value search",
        "pin    letter  focus axis, then use arrows",
        "       Ctrl+letter  open value picker",
        "",
        "Key bindings are configurable in",
        "~/.juxt/settings.yaml  (also via :settings);",
        "modifiers.swap there exchanges the Ctrl",
        "and Shift roles on axis letter keys.",
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
    plots/{}/{}.png           anonymous placeholders, named axis_1, axis_2, …
    host:/path/to/dir         remote directory over SSH  (requires juxt[ssh])
    host:/path/{sensor}.png   remote template over SSH   (requires juxt[ssh])
    config.yaml               explicit YAML config file
    (default: opens a dialog to browse a directory or build a template)

  -s, --separator SEP [...]   separator(s) for auto-detection
  -a, --auto                  skip axis naming prompt
      --max-depth N           max subdirectory search depth
      --save PATH             save resolved config to PATH after detection
      --no-watch              disable automatic file watching (local only)
      --watch-interval SEC    poll remote for changes every SEC seconds (default: 5, 0 to disable)
      --axis-h NAME           lock ←/→ to this axis on startup
      --axis-v NAME           lock ↑/↓ to this axis on startup
      --squeeze               drop axes with only one value
      --name NAME             session name shown in window title (default: template basename)

  panels (toggled at runtime with Ctrl+Shift+I / Ctrl+Shift+H / Ctrl+Shift+K)
      --info                  start with the info sidebar open   (default: closed)
      --no-info               start with the info sidebar closed
      --status-bar            start with the status bar shown    (default: shown)
      --no-status-bar         start with the status bar hidden
      --keys                  start with the shortcut sidebar open (default: closed)
      --no-keys               start with the shortcut sidebar closed

  grid view
      --grid AXIS             enter grid view for AXIS on startup
                              (or build one in the UI with Ctrl+Shift+G)
      --grid-values VAL ...   show only these values in the grid (requires --grid)
      --grid-layout NxM       explicit grid layout, e.g. 2x3 (requires --grid)
      --no-sharex             disable synchronized horizontal pan/zoom in grid view
      --no-sharey             disable synchronized vertical pan/zoom in grid view
  -h, --help                  show this message and exit

  shell completion (bash / zsh)
    eval "$(juxt --bash-completion)"  complete options and {placeholder} paths

  commands (press : in the viewer, Tab completes)
    :fit  :fit-width  :fit-height  :zoom N  :fullscreen
    :grid AXIS [VALUES|NxM]  :grid-dialog  :grid-layout NxM  :ungrid
    :grid-sharex on|off  :grid-sharey on|off
    :axis-h NAME  :axis-v NAME  :axis-auto  :swap-axes
    :mode tap|seek|pin  :switch-last  :info  :keys
    :pattern PATH  :reload  :watch true|false
    :remove-axis NAME  :remove-value AXIS VALUE
    :change-key AXIS LETTER  :settings
    :copy-image  :copy-path  :write [PATH]  :quit""")


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
    p.add_argument("path", nargs="?", default=None, metavar="PATH")
    p.add_argument("-s", "--separator", metavar="SEP", nargs="+")
    p.add_argument("-a", "--auto", action="store_true")
    p.add_argument("--max-depth", type=int, default=None, metavar="N")
    p.add_argument("--save", metavar="PATH")
    p.add_argument("--no-watch", action="store_true", default=False)
    p.add_argument("--watch-interval", type=int, default=5, metavar="SEC")
    p.add_argument("--axis-h", metavar="NAME", default=None)
    p.add_argument("--axis-v", metavar="NAME", default=None)
    p.add_argument("--squeeze", action="store_true", default=False)
    p.add_argument("--name", metavar="NAME", default=None)
    # Initial panel visibility; both are still toggleable at runtime.
    p.add_argument("--info", dest="info", action="store_true", default=False)
    p.add_argument("--no-info", dest="info", action="store_false")
    p.add_argument("--status-bar", dest="status_bar", action="store_true", default=True)
    p.add_argument("--no-status-bar", dest="status_bar", action="store_false")
    p.add_argument("--keys", dest="keys", action="store_true", default=False)
    p.add_argument("--no-keys", dest="keys", action="store_false")

    g = p.add_argument_group("grid view")
    g.add_argument("--grid", metavar="AXIS", default=None)
    g.add_argument("--grid-values", metavar="VAL", nargs="+", default=None)
    g.add_argument("--grid-layout", metavar="NxM", default=None)
    g.add_argument("--no-sharex", action="store_true", default=False)
    g.add_argument("--no-sharey", action="store_true", default=False)

    p.add_argument("-h", "--help", action=_HelpAction)
    return p.parse_args()


def main():
    args = _parse_args()
    import os
    _setup_logging(
        os.environ.get("JUXT_LOG_LEVEL", "WARNING").upper(),
        os.environ.get("JUXT_LOG_FILE"),
    )
    log.debug("args: %s", args)

    settings = load_settings()
    import juxt.detect as _detect
    _detect._MAX_VALS = settings.max_vals
    _detect._MAX_VALS_DISPLAY = settings.max_vals_display

    if sys.platform == "win32" and Path(sys.executable).stem.lower() in ("python", "pythonw"):
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("juxt")

    app = QApplication(sys.argv)
    app.setApplicationName("juxt")
    _startup_screen = app.screenAt(QCursor.pos()) or app.primaryScreen()
    _logo = Path(__file__).parent / "assets" / "logo_transparent.ico"
    if not _logo.exists():  # fallback for editable installs
        _logo = Path(__file__).parent.parent / "docs" / "assets" / "logo_transparent.ico"
    app_icon = QIcon()
    if _logo.exists():
        app_icon = QIcon(str(_logo))
        if app_icon.isNull():
            log.warning("Icon file found but failed to load: %s", _logo)
        else:
            app.setWindowIcon(app_icon)
    else:
        log.warning("No logo file found at %s", _logo)

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
        QTimer.singleShot(0, lambda: dlg.move(
            _startup_screen.geometry().center() - dlg.rect().center()))
        QTimer.singleShot(0, lambda: _force_focus(dlg))
        ok = dlg.exec()
        _pw_cache[0] = dlg.textValue() if ok else None
        return _pw_cache[0]

    if args.path is None:
        dlg = StartupDialog(str(Path.home()), app_icon=app_icon)
        QTimer.singleShot(0, lambda: dlg.move(
            _startup_screen.geometry().center() - dlg.rect().center()))
        QTimer.singleShot(0, lambda: _force_focus(dlg))
        chosen = dlg.chosen_path() if dlg.exec() else ""
        if not chosen:
            sys.exit(0)
        args.path = chosen

    try:
        raw = normalize_template(args.path)  # anonymous {} → {axis_1}, {axis_2}, …
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

    if args.squeeze:
        squeezed = {k: v for k, v in config.axes.items() if len(v) > 1}
        if not squeezed:
            print("Error: --squeeze removed all axes — nothing to navigate", file=sys.stderr)
            sys.exit(1)
        if len(squeezed) < len(config.axes):
            template = config.template
            for k, v in config.axes.items():
                if len(v) == 1:
                    template = template.replace(f"{{{k}}}", v[0])
            config = Config(template=template, axes=squeezed,
                            keys=_auto_keys(squeezed), mode=config.mode, remote=config.remote)

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
    first_label = f"Downloading {n_images} image{'s' if n_images != 1 else ''}…" if is_remote else f"Loading {n_images} image{'s' if n_images != 1 else ''}…"

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

    progress = QProgressDialog(first_label, "Cancel", 0, total_steps)
    progress.setWindowTitle("juxt")
    progress.setWindowModality(Qt.ApplicationModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    app.processEvents()
    _sg = _startup_screen.geometry()
    progress.move(_sg.center() - progress.rect().center())
    if not app_icon.isNull():
        progress.setWindowIcon(app_icon)
        app.processEvents()
    _force_focus(progress)

    def on_progress(i: int, n: int):
        if progress.wasCanceled():
            sys.exit(0)
        if is_remote and i == n_images:
            progress.setLabelText(f"Loading {n_images} image{'s' if n_images != 1 else ''}…")
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
            QTimer.singleShot(0, lambda: dlg.move(
                _startup_screen.geometry().center() - dlg.rect().center()))
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

    grid_layout: tuple[int, int] | None = None
    if args.grid_layout:
        import re as _re
        m = _re.match(r'^(\d+)x(\d+)$', args.grid_layout, _re.IGNORECASE)
        if not m:
            print(f"Warning: --grid-layout {args.grid_layout!r} is not a valid NxM layout, ignoring",
                  file=sys.stderr)
        elif int(m.group(1)) < 1 or int(m.group(2)) < 1:
            print(f"Warning: --grid-layout {args.grid_layout!r} needs rows and cols >= 1, ignoring",
                  file=sys.stderr)
        else:
            grid_layout = (int(m.group(1)), int(m.group(2)))

    if args.grid and args.grid not in axis_names:
        print(f"Warning: --grid {args.grid!r} not found in axes {axis_names}", file=sys.stderr)

    if args.grid_values and not args.grid:
        print("Warning: --grid-values has no effect without --grid", file=sys.stderr)

    if args.grid_layout and not args.grid:
        print("Warning: --grid-layout has no effect without --grid", file=sys.stderr)

    window = MainWindow(
        config, pixmaps,
        watch=not args.no_watch,
        remote_tmpdir=remote_tmpdir,
        get_password=_ask_password if is_remote else None,
        poll_interval=args.watch_interval,
        remote_mtimes=remote_mtimes,
        axis_h=args.axis_h,
        axis_v=args.axis_v,
        session_name=args.name,
        seek_greedy=settings.seek_greedy,
        seek_fuzzy=settings.seek_fuzzy,
        swap_modifiers=settings.swap_modifiers,
        keybindings=settings.keybindings,
        highlight=settings.highlight,
        highlight_candidates=settings.highlight_candidates,
        grid=args.grid,
        grid_values=args.grid_values,
        grid_layout=grid_layout,
        grid_sharex=not args.no_sharex,
        grid_sharey=not args.no_sharey,
        show_info=args.info,
        show_status_bar=args.status_bar,
        show_keys=args.keys,
    )
    window.move(_startup_screen.geometry().topLeft())
    window.showMaximized()
    app.processEvents()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    _force_focus(window)
    window.view.fit_image()     # start app with image fit to screen

    # QTimer import kept for other uses; no polling timer needed for SIGINT.
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
