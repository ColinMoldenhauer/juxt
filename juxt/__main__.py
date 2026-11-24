from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
from pathlib import Path

from . import __version__
from .complete import SCRIPT_FLAGS, WORDS_FLAG, expand_home, normalize_template
from .complete import main as _complete_main

# Shell completion runs on every Tab press, so answer it here — before Qt is
# imported — which also lets `juxt` itself back the completion when the
# standalone binary is all a user has installed.
if len(sys.argv) > 1 and sys.argv[1] in (*SCRIPT_FLAGS, WORDS_FLAG):
    sys.exit(_complete_main(sys.argv[1:]))

from PySide6.QtCore import QEventLoop, Qt, QSocketNotifier, QTimer  # noqa: E402
from PySide6.QtGui import QCursor, QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
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
    has_bare_wildcard,
    order_pinned,
    parse_pins,
    prompt_rename,
    resolve_wildcard_template,
)
from .loader import preload, preload_remote, set_remote_cache_days, set_threads
from . import theme
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
        "Home / End      first / last value on the primary axis",
        "1–9             jump to Nth value on the primary axis",
        "Enter           toggle fullscreen",
        "Escape          exit fullscreen / cancel",
        ":               command mode  (:fit  :zoom N  :q)",
        "Ctrl+C          copy sidebar selection, else cancel command / picker",
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
        "wheel           step the primary (←/→) axis",
        "Shift+wheel     step the secondary (↑/↓) axis",
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
        "and Shift roles on axis keys.",
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
    plots/*.png               wildcard match, recursing into subdirs
    {root}/*.png  --root A B  pin {root} then wildcard-match within each
    {root}/{**plot}.png       {**name} captures across '/', for one axis
                              spanning an arbitrarily deep subtree
    host:/path/to/dir         remote directory over SSH
    host:/path/{sensor}.png   remote template over SSH
    config.yaml               explicit YAML config file
    (default: opens a dialog to browse a directory or build a template)

  -s, --separator SEP [...]   separator(s) for auto-detection
  -a, --auto                  skip axis naming prompt
  -V, --version               print the juxt version and exit
      --max-depth N           max subdirectory search depth
      --save PATH             save resolved config to PATH after detection
      --list-axes             run axis discovery, print each axis with all its
                              values, and exit; no window, no naming prompt
      --subset-interactive    after discovery, tick the values to load in a
                              dialogue (one column per axis) before any
                              image is read; --NAME VALUE pins then only
                              preselect, the other values stay on offer
      --NAME VALUE [...]      pin a {placeholder}'s values instead of
                              globbing for them (template/wildcard PATH
                              only, must come after PATH); repeatable per
                              placeholder; required for every {placeholder}
                              before a '*' wildcard. A value with * ? [ is
                              a glob, 're:REGEX' a regex, keeping the values
                              on disk that match (quote it)
      --full-path             show a {**name} axis as the full path matched
                              instead of pruning the part shared by every
                              match (see {root}/{**plot}.png above)
      --no-watch              disable automatic file watching (local only)
      --watch-interval SEC    poll remote for changes every SEC seconds (default: 5, 0 to disable)
      --axis-h NAME           make this the primary (←/→) axis on startup
      --axis-v NAME           make this the secondary (↑/↓) axis on startup
      --squeeze               drop axes with only one value
      --name NAME             session name shown in window title (default: the template path
                              and any pinned values)

  panels (toggled at runtime with Ctrl+Shift+I / Ctrl+Shift+H / Ctrl+Shift+K)
      --info                  start with the info sidebar open   (default: sidebar.open in settings, initially open)
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
    :fit  :fit-width  :fit-height  :fit-off  :zoom N  :fullscreen
    :grid AXIS [VALUES|NxM]  :grid-dialog  :grid-layout NxM  :grid-off
    :grid-sharex on|off  :grid-sharey on|off
    :axis-h NAME  :axis-v NAME  :axis-auto  :swap-axes
    :mode tap|seek|pin  :switch-last  :info  :keys
    :pattern PATH  :reload  :watch true|false
    :remove-axis NAME  :remove-value AXIS VALUE
    :subset AXIS [VALUES]  edit an axis: delete, reorder, rename, add
    :subset-interactive  :values AXIS  pick values from those on disk
    :change-key AXIS LETTER  :settings
    :copy-image  :copy-path  :copy-cli-command  :reveal  :write [PATH]  :quit""")


def _parse_axis_pins(unknown: list[str]) -> dict[str, list[str]]:
    """Turn leftover argv (`['--sensor', 'A', 'B', '--date', 'X']`) into
    `{'sensor': ['A', 'B'], 'date': ['X']}`.

    Axis names are taken verbatim, unlike a generic flag-to-kwarg grabber,
    since a {placeholder} name may itself contain hyphens (e.g. {yyyy-mm-dd}).
    Must appear after PATH on the command line — argparse would otherwise
    swallow a pinned value as PATH before it ever sees the real one.
    """
    pins: dict[str, list[str]] = {}
    current: str | None = None
    for arg in unknown:
        if arg.startswith("--"):
            current = arg[2:]
            if not current:
                raise SystemExit(f"Unexpected argument {arg!r}")
            if current in pins:
                raise SystemExit(f"Duplicate axis flag: --{current}")
            pins[current] = []
        else:
            if current is None:
                raise SystemExit(
                    f"Unrecognized argument {arg!r}. To pin a {{placeholder}}'s "
                    f"values, use '--NAME VALUE ...' after PATH."
                )
            pins[current].append(arg)
    for name, values in pins.items():
        if not values:
            raise SystemExit(f"--{name} was given no values to pin.")
    return pins


class _HelpAction(argparse.Action):
    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, *_):
        _print_help()
        parser.exit()


def _parse_args() -> tuple[argparse.Namespace, dict[str, list[str]]]:
    # No abbreviated options: a --NAME pin that is a prefix of a juxt option
    # would be taken for that option instead. `--s` for {s} is rejected as
    # ambiguous (--separator, --save, ...), `--gri` for {gri} becomes --grid.
    p = argparse.ArgumentParser(prog="juxt", add_help=False, allow_abbrev=False)
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
    p.add_argument("--full-path", action="store_true", default=False)
    p.add_argument("--list-axes", action="store_true", default=False)
    p.add_argument("--subset-interactive", action="store_true", default=False)
    # Initial panel visibility; both are still toggleable at runtime.
    # These are declared here so parse_known_args recognises them; an
    # unrecognised --flag after PATH is read as an axis pin instead.
    p.add_argument("--info", dest="info", action="store_true", default=None)
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
    p.add_argument("-V", "--version", action="version", version=f"juxt {__version__}")

    # Any flag not recognized above and appearing after PATH is treated as
    # '--{placeholder} VALUE ...', pinning that template placeholder to an
    # explicit value list instead of discovering it by globbing.
    # A value with a glob character or a `re:` prefix selects by pattern.
    args, unknown = p.parse_known_args()
    try:
        return args, parse_pins(_parse_axis_pins(unknown))
    except ValueError as e:
        raise SystemExit(str(e)) from None


def _resolve_config(
    args: argparse.Namespace,
    pins: dict[str, list[str]],
    ask_password,
    *,
    rename: bool = True,
) -> Config:
    """Turn PATH (directory, template, wildcard, remote or YAML) into a Config.

    *rename* allows the interactive axis-naming prompt for a plain directory
    scan; it is still skipped under `--auto`. Raises on any failure, leaving
    the reporting to the caller.
    """
    raw = expand_home(normalize_template(args.path))  # anonymous {} → {axis_1}, …; ~ → home
    prompt = rename and not args.auto
    if _is_remote_pattern(raw):
        remote_cfg, remote_tmpl = _parse_remote_pattern(raw)
        if '{' in remote_tmpl:
            axes = _axes_from_remote_template(remote_tmpl, remote_cfg, ask_password, pinned=pins)
            return Config(template=remote_tmpl, axes=axes,
                          keys=_auto_keys(axes), remote=remote_cfg)
        if pins:
            raise ValueError(
                "--NAME VALUE pinning requires a {placeholder} template, "
                "not a plain remote directory"
            )
        config, sep, n_remote = detect_config_remote(
            remote_tmpl, remote_cfg, args.separator, ask_password)
        return prompt_rename(config, n_remote, sep) if prompt else config
    if Path(raw).is_dir():
        if pins:
            raise ValueError(
                "--NAME VALUE pinning requires a {placeholder} template, not a directory"
            )
        p = Path(raw)
        files = _iter_images(p, args.max_depth)
        config, sep = detect_config(p, args.separator, args.max_depth)
        return prompt_rename(config, len(files), sep, p, args.max_depth) if prompt else config
    if raw.lower().endswith((".yaml", ".yml")):
        if pins:
            raise ValueError(
                "--NAME VALUE pinning requires a {placeholder} template, not a YAML config"
            )
        return load_config(raw)
    if has_bare_wildcard(raw):
        template, axes = resolve_wildcard_template(raw, pinned=pins)
        return Config(template=template, axes=axes, keys=_auto_keys(axes))
    if '{' in raw:
        template, axes = _axes_from_local_template(raw, pinned=pins, full_path=args.full_path)
        return Config(template=template, axes=axes, keys=_auto_keys(axes))
    raise ValueError(
        f"{raw!r} is not a directory, YAML config, template pattern, or remote path"
    )


def _may_prompt(args: argparse.Namespace) -> bool:
    """Whether resolving PATH may ask in the terminal to name the axes (a
    plain directory scan without `--auto`), see `_resolve_config`."""
    if args.auto or not args.path:
        return False
    raw = expand_home(normalize_template(args.path))
    if _is_remote_pattern(raw):
        return '{' not in _parse_remote_pattern(raw)[1]
    return Path(raw).is_dir()


def _discover_with_busy(fn, ask_password, *, app_icon, screen, interrupted,
                        label: str = "Collecting values…", delay_ms: int = 300):
    """Run `fn(ask_password)` (discovery) in a worker thread and return its
    result, raising what it raised.

    Meanwhile a busy box with *label* shows once *delay_ms* have passed, so a
    quick discovery shows nothing. Its Cancel, closing it and Ctrl+C (the
    *interrupted* flag of `_quit_on_sigint`) quit juxt. A password request
    from the worker is answered by *ask_password* on the main thread, with
    the box hidden meanwhile.

    The worker touches no Qt object: it talks to the main thread through a
    queue, which a timer here drains.
    """
    import queue
    import threading
    from PySide6.QtWidgets import QProgressDialog

    inbox: queue.Queue = queue.Queue()     # worker -> main: ("ask", label) / ("done", result)
    answers: queue.Queue = queue.Queue()   # main -> worker: the password, or None
    loop = QEventLoop()
    outcome: list = []
    cancelled = [False]

    busy = QProgressDialog(label, "Cancel", 0, 0)
    busy.setWindowTitle("juxt")
    busy.setMinimumDuration(24 * 3600 * 1000)   # shown by the timer below, not by Qt
    busy.setAutoClose(False)
    busy.setAutoReset(False)
    if not app_icon.isNull():
        busy.setWindowIcon(app_icon)
    busy.hide()

    def _cancel():
        cancelled[0] = True
        loop.quit()

    busy.canceled.connect(_cancel)   # the button, or closing the box
    busy.rejected.connect(_cancel)   # Esc

    def _show():
        if outcome or cancelled[0] or interrupted[0]:
            return
        busy.show()
        busy.move(screen.geometry().center() - busy.rect().center())
        _force_focus(busy)

    def _drain():
        if interrupted[0]:   # Ctrl+C's handler can't end a plain QEventLoop
            loop.quit()
            return
        while True:
            try:
                kind, value = inbox.get_nowait()
            except queue.Empty:
                return
            if kind == "done":
                outcome.append(value)
                loop.quit()
                return
            was_visible = busy.isVisible()
            busy.hide()
            try:
                answers.put(ask_password(value))
            except BaseException:
                answers.put(None)
                raise
            if was_visible:
                _show()

    def _ask_from_worker(text: str) -> str | None:
        inbox.put(("ask", text))
        return answers.get()

    def _work():
        try:
            result = fn(_ask_from_worker)
        except BaseException as e:   # noqa: BLE001, re-raised on the main thread
            result = e
        inbox.put(("done", result))

    show_timer = QTimer()
    show_timer.setSingleShot(True)
    show_timer.timeout.connect(_show)
    poll = QTimer()
    poll.timeout.connect(_drain)

    threading.Thread(target=_work, name="juxt-discovery", daemon=True).start()
    show_timer.start(delay_ms)
    poll.start(30)
    loop.exec()
    show_timer.stop()
    poll.stop()
    busy.close()
    busy.deleteLater()
    if not outcome:   # cancelled or interrupted; the daemon worker dies with us
        sys.exit(0)
    if isinstance(outcome[0], BaseException):
        raise outcome[0]
    return outcome[0]


def _format_axes(config: Config) -> str:
    """The resolved template followed by one line per axis, listing every value."""
    labels = [f"{name} ({len(values)}):" for name, values in config.axes.items()]
    width = max(map(len, labels), default=0)
    lines = [f"template: {config.template}"]
    if config.axes:
        lines.append("")
        lines += [
            f"{label.ljust(width)}  {'  '.join(values)}"
            for label, values in zip(labels, config.axes.values())
        ]
    return "\n".join(lines)


def _ask_password_tty(label: str) -> str | None:
    """Terminal counterpart of the Qt password dialog, for the headless modes."""
    import getpass
    try:
        return getpass.getpass(f"Password for {label}: ")
    except (EOFError, KeyboardInterrupt):
        return None


def _list_axes(args: argparse.Namespace, pins: dict[str, list[str]]) -> int:
    """`--list-axes`: run discovery, print the axes and their values, no GUI.

    Output goes to stdout; errors and the SSH password prompt stay off it.
    Never asks for axis names, whether or not `--auto` was given.
    """
    if args.path is None:
        print("Error: --list-axes needs a PATH to scan", file=sys.stderr)
        return 1
    try:
        config = _resolve_config(args, pins, _ask_password_tty, rename=False)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except AttributeError:
        pass
    print(_format_axes(config))
    return 0


def _pin_text(value) -> str:
    """A pin as typed, whether a literal str or a `PinPattern`."""
    return value if isinstance(value, str) else value.text


def _resolve_for_picker(
    args: argparse.Namespace,
    pins: dict[str, list[str]],
    ask_password,
) -> tuple[Config, dict[str, list[str]]]:
    """Discovery for `--subset-interactive`, where pins preselect instead of
    narrowing: returns the config with every value found on disk, and the
    pinned values to start out ticked.

    A pin that discovery cannot do without keeps narrowing it: values with a
    '/' in them (a glob never crosses one), a `{**name}` axis (its values are
    taken literally, not pruned). And if discovery fails without the pins
    (say, a placeholder before a '*' wildcard), it runs with all of them, as
    without the flag, and nothing is preselected.
    """
    if not pins:
        return _resolve_config(args, pins, ask_password), {}
    from .detect import _parse_template_names
    try:
        _names, _raw, recursive = _parse_template_names(args.path or "")
    except ValueError:
        recursive = None
    needed = {n: vs for n, vs in pins.items()
              if n == recursive or any("/" in _pin_text(v) for v in vs)}
    if len(needed) < len(pins):
        try:
            config = _resolve_config(args, needed, ask_password)
        except Exception as e:
            log.info("Discovery without the pins failed (%s); keeping them", e)
        else:
            # A pattern pin ticks the values it matches.
            return config, {n: order_pinned(vs, config.axes[n]) for n, vs in pins.items()
                            if n not in needed and n in config.axes}
    return _resolve_config(args, pins, ask_password), {}


def _pick_startup_values(config: Config, app_icon, screen,
                         preselect: dict[str, list[str]] | None = None) -> Config:
    """`--subset-interactive`: tick the values to load, before any image is read.

    *preselect* maps an axis to the values that start out ticked (the
    `--NAME VALUE` pins); the axis's other values are offered unticked. Every
    value of the other axes starts ticked. Cancel quits.
    """
    from .dialogs import ValuesDialog

    preselect = preselect or {}
    ticked = {name: list(preselect.get(name, vals)) for name, vals in config.axes.items()}
    dlg = ValuesDialog(ticked, dict(config.axes),
                       title="juxt: choose the values to load", apply_text="Load")
    if not app_icon.isNull():
        dlg.setWindowIcon(app_icon)
    # With the dialogue as context, the timers die with it if it is gone first.
    QTimer.singleShot(0, dlg, lambda: dlg.move(screen.geometry().center() - dlg.rect().center()))
    QTimer.singleShot(0, dlg, lambda: _force_focus(dlg))
    log.debug("startup value picker open")
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    chosen = dlg.values()
    dlg.deleteLater()
    if not accepted:
        sys.exit(0)
    return Config(template=config.template, axes=chosen, keys=config.keys,
                  mode=config.mode, remote=config.remote)


def _quit_on_sigint(app: QApplication) -> list[bool]:
    """Make Ctrl+C quit juxt from any Qt event loop, a dialogue's included.

    set_wakeup_fd writes a byte to a socket when SIGINT arrives, and a
    QSocketNotifier turns that into an event, so the handler runs at once
    even while Qt (not Python) holds the thread, without a polling timer.

    quit() only ends the main event loop, so open modal dialogues (each
    running a nested loop) are rejected first, innermost out; the code that
    opened them treats that as Cancel. Where no loop runs at all (images
    loading via processEvents) quit() has nothing to end, so the handler
    also sets the returned one-item flag for such code to check.
    """
    interrupted = [False]

    def _on_sigint(*_):
        interrupted[0] = True
        for _i in range(32):   # a bound, should a dialogue refuse to close
            modal = app.activeModalWidget()
            if modal is None:
                break
            if isinstance(modal, QDialog):
                modal.reject()
            else:
                modal.close()
        app.quit()

    signal.signal(signal.SIGINT, _on_sigint)
    rsock, wsock = socket.socketpair()
    wsock.setblocking(False)
    signal.set_wakeup_fd(wsock.fileno())
    notifier = QSocketNotifier(rsock.fileno(), QSocketNotifier.Type.Read, app)
    notifier.activated.connect(lambda *_: (rsock.recv(4096), _on_sigint()))
    app._juxt_sigint = (rsock, wsock, notifier)   # keep them alive with the app
    return interrupted


def main():
    args, pins = _parse_args()
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

    if args.list_axes:
        sys.exit(_list_axes(args, pins))

    if sys.platform == "win32" and Path(sys.executable).stem.lower() in ("python", "pythonw"):
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("juxt")

    app = QApplication(sys.argv)
    app.setApplicationName("juxt")
    # First thing, so Ctrl+C also ends the dialogues before the viewer: the
    # startup window, the password prompt, the --subset-interactive picker.
    interrupted = _quit_on_sigint(app)
    theme.apply(settings.theme)   # before the startup window, so it matches
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

    def _discover(ask_password):
        if args.subset_interactive:
            return _resolve_for_picker(args, pins, ask_password)
        return _resolve_config(args, pins, ask_password), {}

    try:
        if _may_prompt(args):   # the terminal shows progress, and asks
            config, preselect = _discover(_ask_password)
        else:
            config, preselect = _discover_with_busy(
                _discover, _ask_password, app_icon=app_icon,
                screen=_startup_screen, interrupted=interrupted)
    except Exception as e:
        if interrupted[0]:   # Ctrl+C at the password prompt
            sys.exit(0)
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.subset_interactive:
        config = _pick_startup_values(config, app_icon, _startup_screen, preselect)

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

    set_threads(settings.loading_threads)
    set_remote_cache_days(settings.remote_cache_days)
    n_images = 1
    for vs in config.axes.values():
        n_images *= len(vs)

    is_remote = config.remote is not None
    total_steps = n_images * 2 if is_remote else n_images
    first_label = f"Downloading {n_images} image{'s' if n_images != 1 else ''}…" if is_remote else f"Loading {n_images} image{'s' if n_images != 1 else ''}…"

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
        if progress.wasCanceled() or interrupted[0]:
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
        pinned_axes=list(pins),
        seek_greedy=settings.seek_greedy,
        seek_fuzzy=settings.seek_fuzzy,
        swap_modifiers=settings.swap_modifiers,
        skip_missing=settings.skip_missing,
        dim_missing=settings.dim_missing,
        sidebar_copy=settings.sidebar_copy,
        pattern_subset=settings.pattern_subset,
        keybindings=settings.keybindings,
        highlight=settings.highlight,
        highlight_candidates=settings.highlight_candidates,
        sidebar_separator=settings.sidebar_separator,
        sidebar_list_larger_than=settings.sidebar_list_larger_than,
        grid=args.grid,
        grid_values=args.grid_values,
        grid_layout=grid_layout,
        grid_sharex=not args.no_sharex,
        grid_sharey=not args.no_sharey,
        show_info=settings.sidebar_open if args.info is None else args.info,
        show_status_bar=args.status_bar,
        show_keys=args.keys,
        theme=settings.theme,
    )
    window.move(_startup_screen.geometry().topLeft())
    window.showMaximized()
    app.processEvents()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    _force_focus(window)
    window.view.fit_image()     # start app with image fit to screen

    if interrupted[0]:   # Ctrl+C after loading, before the event loop ran
        sys.exit(0)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
