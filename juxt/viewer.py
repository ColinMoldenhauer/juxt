from __future__ import annotations

import html as _html
import threading
from enum import IntEnum

from PySide6.QtCore import Qt, QFileSystemWatcher, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QMainWindow,
)

from .config import Config

_COMMANDS = [
    "fit",
    "fit-height",
    "fit-width",
    "fullscreen",
    "mode",
    "quit",
    "reload",
    "reload-images",
    "save",
    "switch-last",
    "zoom",
]

_ALIASES: dict[str, str] = {
    "q": "quit",
}

# Discrete argument options for commands that take a value.
# Commands absent from this dict take no arguments (or free-text only).
_CMD_ARGS: dict[str, list[str]] = {
    "mode": ["tap", "seek", "pin"],
    "reload-images": ["true", "false"],
    "save": [],   # free-text path; empty → file dialog
    "zoom": ["50", "75", "100", "150", "200"],
}


class NavMode(IntEnum):
    TAP = 0
    SEEK = 1
    PIN = 2

    @property
    def label(self) -> str:
        return ("tap", "seek", "pin")[self]


class ImageView(QGraphicsView):
    state_changed = Signal()
    toggle_bar = Signal()
    _poll_result = Signal(object)    # emitted from poll worker; carries list or Exception
    _reload_result = Signal(object)  # emitted from reload worker; carries tuple or Exception

    def __init__(
        self,
        config: Config,
        pixmaps: dict[tuple, QPixmap],
        parent=None,
        watch: bool = True,
        remote_tmpdir: str | None = None,
        get_password: object = None,
        poll_interval: int = 0,
        remote_mtimes: dict | None = None,
    ):
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)

        self.config = config
        self.pixmaps = pixmaps
        self.axis_names = list(config.axes.keys())
        self.axis_values = list(config.axes.values())
        self.n_axes = len(self.axis_names)

        self.pos: list[int] = [0] * self.n_axes
        self.prev: list[int] | None = None

        # Most recently focused axis first; focus_stack[0] → ←/→, [1] → ↑/↓
        self.focus_stack: list[int] = list(range(self.n_axes))

        # letter → axis index; rebuilt when mode changes
        self.key_to_axis: dict[str, int] = {}
        self._rebuild_key_to_axis()

        self.nav_mode = NavMode(config.mode)

        self._flash_msg: str | None = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._watching = False
        self._watcher: QFileSystemWatcher | None = None
        self._path_to_key: dict[str, tuple] = {}

        self._remote_tmpdir: str | None = remote_tmpdir
        self._get_password: object = get_password
        self._poll_interval: int = poll_interval if poll_interval > 0 else 5
        self._poll_in_progress: bool = False
        self._reload_in_progress: bool = False
        self._remote_conn: list = [None, None]   # [ssh_client, sftp_client], worker-thread only
        self._remote_mtimes: dict = remote_mtimes if remote_mtimes is not None else {}
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._start_poll_worker)
        self._poll_result.connect(self._apply_remote_poll)
        self._reload_result.connect(self._apply_reload)

        # Active incremental-search state (value picker and multi-select).
        # None  = no active selection
        # axis phase:  {"phase": "axis",  "query": str}
        # value phase: {"phase": "value", "query": str, "axis_idx": int}
        self._sel: dict | None = None

        # Active command-mode state; None = not in command mode.
        # verb phase: {"phase": "verb", "query": str, "cursor": int}
        # arg  phase: {"phase": "arg",  "verb": str, "query": str, "cursor": int}
        self._cmd: dict | None = None

        self.setFocusPolicy(Qt.StrongFocus)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(25, 25, 25))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)

        self._refresh()

        if watch:
            if config.remote is None:
                self._start_watching(silent=True)
            elif remote_tmpdir is not None and poll_interval > 0:
                self._start_watching(silent=True)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _key(self) -> tuple:
        return tuple(self.pos)

    def _refresh(self):
        pm = self.pixmaps.get(self._key())
        if pm:
            self._item.setPixmap(pm)
            self._scene.setSceneRect(QRectF(pm.rect()))
        self.viewport().update()
        self.state_changed.emit()

    def _h_axis(self) -> int:
        return self.focus_stack[0]

    def _v_axis(self) -> int | None:
        return self.focus_stack[1] if len(self.focus_stack) >= 2 else None

    def _navigate(self, axis: int, delta: int):
        n = len(self.axis_values[axis])
        self.prev = list(self.pos)
        self.pos[axis] = (self.pos[axis] + delta) % n
        self._refresh()

    def _focus(self, axis: int):
        if axis in self.focus_stack:
            self.focus_stack.remove(axis)
        self.focus_stack.insert(0, axis)

    # ── incremental-search (value picker / multi-select) ─────────────────────

    def _sel_candidates(self) -> list[str]:
        """Items matching the current query by case-insensitive prefix."""
        assert self._sel is not None
        q = self._sel["query"]
        if self._sel["phase"] == "axis":
            return [n for n in self.axis_names if n.lower().startswith(q)]
        return [v for v in self.axis_values[self._sel["axis_idx"]] if v.lower().startswith(q)]

    def _sel_open_axis(self, initial: str = ""):
        """Enter axis-selection phase (mode 1 entry point)."""
        self._sel = {"phase": "axis", "query": initial.lower(), "cursor": 0}
        self._sel_try_commit()

    def _sel_open_value(self, axis_idx: int, initial: str = ""):
        """Enter value-selection phase for a known axis (ctrl+letter entry point)."""
        self._focus(axis_idx)
        self._sel = {"phase": "value", "query": initial.lower(), "axis_idx": axis_idx, "cursor": 0}
        self._sel_try_commit()

    def _sel_try_commit(self):
        """Auto-commit when exactly one candidate remains (greedy match).

        NOTE: the greedy auto-confirm behaviour is a candidate for a
        user-configurable flag (require explicit Enter instead).
        """
        candidates = self._sel_candidates()
        if len(candidates) == 1:
            self._sel_commit(candidates[0])
        else:
            # Clamp cursor in case the candidate list shrank
            n = len(candidates)
            self._sel["cursor"] = min(self._sel["cursor"], n - 1) if n else 0
            self.state_changed.emit()

    def _sel_commit(self, value: str):
        if self._sel["phase"] == "axis":
            axis_idx = self.axis_names.index(value)
            self._focus(axis_idx)
            self._sel = {"phase": "value", "query": "", "axis_idx": axis_idx, "cursor": 0}
            self._sel_try_commit()
        else:
            axis_idx = self._sel["axis_idx"]
            self.prev = list(self.pos)
            self.pos[axis_idx] = self.axis_values[axis_idx].index(value)
            self._sel = None
            self._refresh()

    def _sel_handle_key(self, event: QKeyEvent) -> bool:
        """Handle input while a selection is active. Returns True if consumed."""
        k = event.key()

        if k == Qt.Key_Escape:
            self._sel = None
            self.state_changed.emit()
            return True

        if k == Qt.Key_Backspace:
            q = self._sel["query"]
            if q:
                self._sel["query"] = q[:-1]
                self._sel["cursor"] = 0
                self.state_changed.emit()
            elif self._sel["phase"] == "value":
                # backspace on empty value query → back to axis search
                self._sel = {"phase": "axis", "query": "", "cursor": 0}
                self.state_changed.emit()
            else:
                self._sel = None
                self.state_changed.emit()
            return True

        if k == Qt.Key_Right:
            candidates = self._sel_candidates()
            if candidates:
                self._sel["cursor"] = (self._sel["cursor"] + 1) % len(candidates)
                self.state_changed.emit()
            return True

        if k == Qt.Key_Left:
            candidates = self._sel_candidates()
            if candidates:
                self._sel["cursor"] = (self._sel["cursor"] - 1) % len(candidates)
                self.state_changed.emit()
            return True

        if Qt.Key_1 <= k <= Qt.Key_9:
            idx = k - Qt.Key_1
            candidates = self._sel_candidates()
            if idx < len(candidates):
                self._sel_commit(candidates[idx])
            return True

        if k in (Qt.Key_Return, Qt.Key_Enter):
            candidates = self._sel_candidates()
            if candidates:
                self._sel_commit(candidates[self._sel["cursor"]])
            return True

        ch = event.text()
        if ch and ch.isprintable():
            self._sel["query"] += ch.lower()
            self._sel["cursor"] = 0
            self._sel_try_commit()
            return True

        return False

    # ── command mode ─────────────────────────────────────────────────────────

    def _cmd_candidates(self) -> list[str]:
        assert self._cmd is not None
        q = self._cmd["query"].strip()
        if self._cmd["phase"] == "verb":
            if q in _ALIASES:
                return [_ALIASES[q]]
            pool = _COMMANDS
        else:
            pool = _CMD_ARGS.get(self._cmd["verb"], [])
        return [c for c in pool if c.startswith(q)] if q else list(pool)

    def _cmd_handle_key(self, event: QKeyEvent) -> bool:
        """Handle input while command mode is active. Returns True if consumed."""
        k = event.key()

        if k == Qt.Key_Escape:
            self._cmd = None
            self.state_changed.emit()
            return True

        if k == Qt.Key_Backspace:
            if self._cmd["query"]:
                self._cmd["query"] = self._cmd["query"][:-1]
                self._cmd["cursor"] = -1 if (not self._cmd["query"] and self._cmd["phase"] == "verb") else 0
            elif self._cmd["phase"] == "arg":
                verb = self._cmd["verb"]
                self._cmd = {"phase": "verb", "query": verb, "cursor": 0}
                candidates = self._cmd_candidates()
                if verb in candidates:
                    self._cmd["cursor"] = candidates.index(verb)
            else:
                self._cmd = None
            self.state_changed.emit()
            return True

        if k == Qt.Key_Right:
            candidates = self._cmd_candidates()
            if candidates:
                cur = self._cmd["cursor"]
                self._cmd["cursor"] = 0 if cur < 0 else (cur + 1) % len(candidates)
                self.state_changed.emit()
            return True

        if k == Qt.Key_Left:
            candidates = self._cmd_candidates()
            if candidates:
                cur = self._cmd["cursor"]
                self._cmd["cursor"] = len(candidates) - 1 if cur < 0 else (cur - 1) % len(candidates)
                self.state_changed.emit()
            return True

        if k in (Qt.Key_Return, Qt.Key_Enter):
            if self._cmd["phase"] == "verb" and self._cmd["cursor"] < 0:
                self._cmd = None
                self.state_changed.emit()
                return True
            candidates = self._cmd_candidates()
            if self._cmd["phase"] == "verb":
                if not candidates:
                    self._cmd = None
                    self.state_changed.emit()
                    return True
                verb = candidates[min(self._cmd["cursor"], len(candidates) - 1)]
                if verb in _CMD_ARGS:
                    self._cmd = {"phase": "arg", "verb": verb, "query": "", "cursor": 0}
                    self.state_changed.emit()
                else:
                    self._cmd = None
                    self._cmd_execute(verb)
                    self.state_changed.emit()
            else:
                verb = self._cmd["verb"]
                arg = (
                    candidates[min(self._cmd["cursor"], len(candidates) - 1)]
                    if candidates
                    else self._cmd["query"]
                )
                self._cmd = None
                self._cmd_execute(f"{verb} {arg}" if arg else verb)
                self.state_changed.emit()
            return True

        ch = event.text()
        if ch and ch.isprintable():
            self._cmd["query"] += ch.lower()
            self._cmd["cursor"] = 0
            self.state_changed.emit()
            return True

        return False

    def _cmd_execute(self, cmd: str):
        parts = cmd.strip().split()
        if not parts:
            return
        verb = _ALIASES.get(parts[0].lower(), parts[0].lower())
        args = [a.lower() for a in parts[1:]]

        if verb == "quit":
            QApplication.instance().quit()
        elif verb == "fit":
            sub = args[0] if args else ""
            if sub in ("height", "h"):
                self.fit_height()
            elif sub in ("width", "w"):
                self.fit_width()
            else:
                self.fit_image()
        elif verb == "fit-height":
            self.fit_height()
        elif verb == "fit-width":
            self.fit_width()
        elif verb == "zoom":
            if args:
                try:
                    self.set_zoom(float(args[0]))
                except ValueError:
                    pass
        elif verb == "fullscreen":
            win = self.window()
            if win.isFullScreen():
                win.showMaximized()
            else:
                win.showFullScreen()
        elif verb == "mode":
            if args:
                arg = args[0]
                if arg in ("0", "tap"):
                    self.nav_mode = NavMode.TAP
                elif arg in ("1", "seek"):
                    self.nav_mode = NavMode.SEEK
                elif arg in ("2", "pin"):
                    self.nav_mode = NavMode.PIN
                self._rebuild_key_to_axis()
                self._sel = None
                self.state_changed.emit()
        elif verb == "reload":
            self._do_reload()
        elif verb == "reload-images":
            arg = args[0] if args else ""
            if arg == "false":
                self._stop_watching()
            elif self.config.remote is not None:
                if arg and arg != "true":
                    try:
                        self._poll_interval = max(1, int(arg))
                    except ValueError:
                        self._flash(f"invalid interval: {arg!r}")
                        return
                if self._watching:
                    self._poll_timer.stop()
                self._start_watching()
            else:
                self._start_watching()
        elif verb == "save":
            # Reconstruct path from original parts to preserve case
            path_str = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if path_str:
                self._do_save(path_str)
            else:
                from PySide6.QtWidgets import QFileDialog
                path_str, _ = QFileDialog.getSaveFileName(
                    self, "Save config", "juxt.yaml", "YAML files (*.yaml *.yml)"
                )
                if path_str:
                    self._do_save(path_str)
        elif verb == "switch-last":
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
                self._refresh()

    # ── key assignment ───────────────────────────────────────────────────────

    def _rebuild_key_to_axis(self):
        """Recompute letter→axis-index, filling any gaps with _auto_keys."""
        from .config import _auto_keys
        axes_dict = {name: self.axis_values[i] for i, name in enumerate(self.axis_names)}

        # Start from auto-assigned keys so every axis gets a letter if possible
        merged: dict[str, str] = _auto_keys(axes_dict)

        # User-configured keys override auto assignments for their specific letters
        for ch, name in self.config.keys.items():
            if name in self.axis_names:
                merged[ch] = name

        # Remove any letter that ended up pointing at an axis that already has
        # a better (user-configured) letter, to avoid duplicate axis entries
        axis_to_ch: dict[str, str] = {}
        user_keys = {ch for ch in self.config.keys if self.config.keys[ch] in self.axis_names}
        for ch, name in list(merged.items()):
            if ch in user_keys:
                axis_to_ch[name] = ch          # user binding wins unconditionally
            elif name not in axis_to_ch:
                axis_to_ch[name] = ch          # first auto assignment wins

        self.key_to_axis = {
            ch: self.axis_names.index(name)
            for name, ch in axis_to_ch.items()
        }

    # ── flash message ────────────────────────────────────────────────────────

    def _clear_flash(self):
        self._flash_msg = None
        self.state_changed.emit()

    def _flash(self, msg: str, ms: int = 2500):
        self._flash_msg = msg
        self._flash_timer.start(ms)
        self.state_changed.emit()

    def _do_save(self, path: str):
        from .config import dump_config
        try:
            dump_config(self.config, path)
            self._flash(f"saved → {path}")
        except Exception as e:
            self._flash(f"save failed: {e}")

    # ── file watcher ─────────────────────────────────────────────────────────

    def _key_to_path(self, key: tuple) -> str:
        path = self.config.template
        for i, name in enumerate(self.axis_names):
            path = path.replace(f"{{{name}}}", self.axis_values[i][key[i]])
        return path

    def _build_path_to_key(self) -> dict[str, tuple]:
        import itertools
        result = {}
        ranges = [range(len(vals)) for vals in self.axis_values]
        for indices in itertools.product(*ranges):
            result[self._key_to_path(indices)] = indices
        return result

    def _start_watching(self, *, silent: bool = False):
        if self.config.remote is None:
            if self._watcher is not None:
                self._watcher.fileChanged.disconnect(self._on_file_changed)
                self._watcher.deleteLater()
            self._path_to_key = self._build_path_to_key()
            self._watcher = QFileSystemWatcher(list(self._path_to_key.keys()), self)
            self._watcher.fileChanged.connect(self._on_file_changed)
            self._watching = True
            if not silent:
                n = len(self._path_to_key)
                self._flash(f"watching {n} file{'s' if n != 1 else ''}")
        else:
            if self._remote_tmpdir is None:
                self._flash("remote polling not available (no cache dir)")
                return
            self._poll_timer.start(self._poll_interval * 1000)
            self._watching = True
            if not silent:
                self._flash(f"polling every {self._poll_interval}s")

    def _stop_watching(self):
        if self.config.remote is None:
            if self._watcher is not None:
                self._watcher.fileChanged.disconnect(self._on_file_changed)
                self._watcher.deleteLater()
                self._watcher = None
            self._path_to_key = {}
        else:
            self._poll_timer.stop()
        self._watching = False
        self._flash("file watching disabled")

    def _on_file_changed(self, path: str):
        key = self._path_to_key.get(path)
        if key is None:
            return
        pm = QPixmap(path)
        if not pm.isNull():
            self.pixmaps[key] = pm
        # Some OS implementations drop a path from the watcher after deletion;
        # re-add it so saves that write via a temp-rename are still tracked.
        if self._watcher is not None and path not in self._watcher.files():
            self._watcher.addPath(path)
        if tuple(self.pos) == key:
            self._refresh()

    def _start_poll_worker(self):
        if self._poll_in_progress or self._remote_tmpdir is None:
            return
        self._poll_in_progress = True

        conn = self._remote_conn       # [ssh, sftp] — mutable list, worker-owned
        tmpdir = self._remote_tmpdir
        mtimes = self._remote_mtimes   # mutable dict, updated in-place by worker
        get_pw = self._get_password
        config = self.config

        def _worker():
            # Lazily connect; reconnect whenever the session is dead
            if conn[1] is None:
                try:
                    from .loader import _connect_sftp
                    conn[0], conn[1] = _connect_sftp(config.remote, get_pw)
                except Exception as e:
                    self._poll_result.emit(e)
                    return
            try:
                from .loader import poll_remote_with_sftp
                changed = poll_remote_with_sftp(
                    config.template, config.axes, tmpdir, conn[1], mtimes)
                self._poll_result.emit(changed)
            except Exception as e:
                # Session likely dead — clear so next poll reconnects
                try:
                    conn[1].close()
                    conn[0].close()
                except Exception:
                    pass
                conn[0], conn[1] = None, None
                self._poll_result.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_remote_poll(self, result: object):
        self._poll_in_progress = False
        if isinstance(result, Exception):
            self._flash(f"poll failed: {result}")
            return
        # result is list[(key, local_path|None)] for changed files only.
        # local_path is None when the remote file was deleted or moved.
        # Decode QPixmaps here on the main thread (Qt requirement).
        changed: list = result
        for key, local_path in changed:
            if local_path is None:
                from .loader import _error_pixmap
                self.pixmaps[key] = _error_pixmap(self._key_to_path(key))
            else:
                pm = QPixmap(local_path)
                self.pixmaps[key] = pm if not pm.isNull() else _error_pixmap(local_path)
        if any(tuple(self.pos) == key for key, _ in changed):
            self._refresh()

    # ── reload ────────────────────────────────────────────────────────────────

    def _do_reload(self):
        """Re-detect axes and download new files, then rebuild viewer state."""
        if self._reload_in_progress:
            return
        self._reload_in_progress = True
        # Pause polling for the duration of the reload
        if self._watching and self.config.remote is not None:
            self._poll_timer.stop()

        conn = self._remote_conn
        tmpdir = self._remote_tmpdir
        mtimes = self._remote_mtimes
        get_pw = self._get_password
        config = self.config

        def _worker():
            try:
                if config.remote is not None:
                    # Reconnect if the session is dead
                    if conn[1] is None:
                        from .loader import _connect_sftp
                        conn[0], conn[1] = _connect_sftp(config.remote, get_pw)
                    from .detect import _axes_from_sftp_template
                    new_axes = _axes_from_sftp_template(config.template, conn[1])
                    # Download only new/changed files; skip unchanged via mtimes
                    from .loader import poll_remote_with_sftp
                    poll_remote_with_sftp(
                        config.template, new_axes, tmpdir, conn[1], mtimes)
                    # Build key→local-path map for main-thread pixmap decoding
                    from itertools import product as _product
                    from pathlib import Path, PurePosixPath
                    axis_names = list(new_axes.keys())
                    axis_values = list(new_axes.values())
                    key_to_path = {}
                    for combo in _product(*axis_values):
                        mapping = dict(zip(axis_names, combo))
                        rpath = config.template.format(**mapping)
                        lpath = str(Path(tmpdir) / PurePosixPath(rpath.lstrip("/")))
                        key = tuple(vals.index(v) for vals, v in zip(axis_values, combo))
                        key_to_path[key] = lpath
                else:
                    from .detect import _axes_from_local_template
                    new_axes = _axes_from_local_template(config.template)
                    from itertools import product as _product
                    axis_names = list(new_axes.keys())
                    axis_values = list(new_axes.values())
                    key_to_path = {}
                    for combo in _product(*axis_values):
                        mapping = dict(zip(axis_names, combo))
                        key_to_path[
                            tuple(vals.index(v) for vals, v in zip(axis_values, combo))
                        ] = config.template.format(**mapping)

                from .config import Config
                new_config = Config(
                    template=config.template,
                    axes=new_axes,
                    keys=config.keys,
                    mode=config.mode,
                    remote=config.remote,
                )
                self._reload_result.emit((new_config, key_to_path))
            except Exception as e:
                if config.remote is not None:
                    conn[0], conn[1] = None, None
                self._reload_result.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_reload(self, result: object):
        self._reload_in_progress = False
        if isinstance(result, Exception):
            self._flash(f"reload failed: {result}")
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        new_config, key_to_path = result

        # Decode pixmaps on the main thread (Qt requirement)
        from .loader import _error_pixmap
        new_pixmaps: dict[tuple, QPixmap] = {}
        for key, local_path in key_to_path.items():
            pm = QPixmap(local_path)
            new_pixmaps[key] = pm if not pm.isNull() else _error_pixmap(local_path)

        # Preserve position by axis value name where possible
        old_names = self.axis_names
        old_values = self.axis_values
        old_pos = self.pos
        new_axis_names = list(new_config.axes.keys())
        new_axis_values = list(new_config.axes.values())
        new_pos = []
        for name, vals in zip(new_axis_names, new_axis_values):
            if name in old_names:
                old_i = old_names.index(name)
                old_val = old_values[old_i][old_pos[old_i]]
                new_pos.append(vals.index(old_val) if old_val in vals else 0)
            else:
                new_pos.append(0)

        self.config = new_config
        self.pixmaps = new_pixmaps
        self.axis_names = new_axis_names
        self.axis_values = new_axis_values
        self.n_axes = len(new_axis_names)
        self.pos = new_pos
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._rebuild_key_to_axis()

        # Restart poll timer / rebuild local watcher with updated paths
        if self._watching:
            if self.config.remote is None:
                if self._watcher is not None:
                    self._watcher.fileChanged.disconnect(self._on_file_changed)
                    self._watcher.deleteLater()
                self._path_to_key = self._build_path_to_key()
                self._watcher = QFileSystemWatcher(list(self._path_to_key.keys()), self)
                self._watcher.fileChanged.connect(self._on_file_changed)
            else:
                self._poll_timer.start(self._poll_interval * 1000)

        self._flash("reloaded")
        self._refresh()

    # ── public ────────────────────────────────────────────────────────────────

    def fit_image(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def fit_height(self):
        pm = self._item.pixmap()
        if pm.isNull():
            return
        factor = self.viewport().height() / pm.height()
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self._scene.sceneRect().center())

    def fit_width(self):
        pm = self._item.pixmap()
        if pm.isNull():
            return
        factor = self.viewport().width() / pm.width()
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self._scene.sceneRect().center())

    def reset_zoom(self):
        self.resetTransform()

    def set_zoom(self, pct: float):
        self.resetTransform()
        self.scale(pct / 100.0, pct / 100.0)

    # ── Qt overrides ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        mods = event.modifiers()

        # Truly global shortcuts — work in every state
        if k == Qt.Key_H and mods == Qt.ControlModifier:
            self.toggle_bar.emit()
            return
        # Ctrl+C cancels any active command or selection mode
        if k == Qt.Key_C and mods == Qt.ControlModifier:
            if self._cmd is not None or self._sel is not None:
                self._cmd = None
                self._sel = None
                self.state_changed.emit()
            return

        # Command mode intercepts all input while active
        if self._cmd is not None:
            if not self._cmd_handle_key(event):
                super().keyPressEvent(event)
            return

        # Semi-global shortcuts — work in normal mode and selection mode
        if k == Qt.Key_0 and mods == Qt.NoModifier:
            self.reset_zoom()
            return

        # Incremental-search intercepts everything while active
        if self._sel is not None:
            if not self._sel_handle_key(event):
                super().keyPressEvent(event)
            return

        # Colon opens command mode
        if event.text() == ":":
            self._cmd = {"phase": "verb", "query": "", "cursor": -1}
            self.state_changed.emit()
            return

        # Universal navigation keys (all modes)
        if k == Qt.Key_Right:
            self._navigate(self._h_axis(), 1)
            return
        if k == Qt.Key_Left:
            self._navigate(self._h_axis(), -1)
            return
        if k == Qt.Key_Down:
            v = self._v_axis()
            if v is not None:
                self._navigate(v, 1)
            return
        if k == Qt.Key_Up:
            v = self._v_axis()
            if v is not None:
                self._navigate(v, -1)
            return
        if k == Qt.Key_Space:
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
                self._refresh()
            return
        if k == Qt.Key_Home:
            self.prev = list(self.pos)
            self.pos[self._h_axis()] = 0
            self._refresh()
            return
        if k == Qt.Key_End:
            h = self._h_axis()
            self.prev = list(self.pos)
            self.pos[h] = len(self.axis_values[h]) - 1
            self._refresh()
            return
        if Qt.Key_1 <= k <= Qt.Key_9:
            idx = k - Qt.Key_1
            h = self._h_axis()
            if idx < len(self.axis_values[h]):
                self.prev = list(self.pos)
                self.pos[h] = idx
                self._refresh()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            win = self.window()
            if win.isFullScreen():
                win.showMaximized()
            else:
                win.showFullScreen()
            return
        if k == Qt.Key_Escape:
            win = self.window()
            if win.isFullScreen():
                win.showMaximized()
            return

        # Mode-specific letter handling
        ch = event.text()
        ch_lower = ch.lower()
        # When Ctrl is held, event.text() is a control character (\x01–\x1a).
        # Recover the letter from the Qt key code instead.
        if mods == Qt.ControlModifier and Qt.Key_A <= k <= Qt.Key_Z:
            ch_lower = chr(k).lower()

        if self.nav_mode == NavMode.PIN:
            if mods == Qt.ControlModifier and ch_lower in self.key_to_axis:
                self._sel_open_value(self.key_to_axis[ch_lower])
            elif mods == Qt.NoModifier and ch_lower in self.key_to_axis:
                self._focus(self.key_to_axis[ch_lower])
                self.state_changed.emit()
            else:
                super().keyPressEvent(event)

        elif self.nav_mode == NavMode.SEEK:
            if mods == Qt.NoModifier and ch.isalpha():
                self._sel_open_axis(ch)
            else:
                super().keyPressEvent(event)

        elif self.nav_mode == NavMode.TAP:
            if mods == Qt.ControlModifier and ch_lower in self.key_to_axis:
                self._sel_open_value(self.key_to_axis[ch_lower])
            elif ch_lower in self.key_to_axis and mods in (Qt.NoModifier, Qt.ShiftModifier):
                axis = self.key_to_axis[ch_lower]
                delta = -1 if ch.isupper() else 1
                self._focus(axis)
                self._navigate(axis, delta)
            else:
                super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.fit_image()

    def wheelEvent(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods == Qt.ControlModifier:
            factor = 1.15 if delta > 0 else 1 / 1.15
            self.scale(factor, factor)
        elif mods == Qt.ShiftModifier:
            if delta != 0:
                self._navigate(self._h_axis(), -1 if delta > 0 else 1)
        else:
            if delta != 0:
                self._navigate(self._h_axis(), 1 if delta > 0 else -1)

    def resizeEvent(self, event):
        super().resizeEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: Config,
        pixmaps: dict,
        watch: bool = True,
        remote_tmpdir: str | None = None,
        get_password: object = None,
        poll_interval: int = 0,
        remote_mtimes: dict | None = None,
    ):
        super().__init__()
        self.setWindowTitle("juxt")
        self.view = ImageView(
            config, pixmaps, self,
            watch=watch,
            remote_tmpdir=remote_tmpdir,
            get_password=get_password,
            poll_interval=poll_interval,
            remote_mtimes=remote_mtimes,
        )
        self.setCentralWidget(self.view)

        bar = self.statusBar()
        bar.setStyleSheet(
            "QStatusBar { background: #1a1a1a; color: #e0e0e0; "
            "font-family: 'Courier New', monospace; font-size: 9pt; "
            "border-top: 1px solid #333; }"
            "QStatusBar::item { border: none; }"
        )
        self._status_label = QLabel()
        self._status_label.setStyleSheet(
            "color: #e0e0e0; padding: 2px 8px; "
            "font-family: 'Courier New', monospace; font-size: 9pt; "
            "white-space: pre;"
        )
        bar.addWidget(self._status_label, 1)

        self.view.state_changed.connect(self._update_status)
        self.view.toggle_bar.connect(self._toggle_status_bar)

        self._update_status()
        QTimer.singleShot(0, self.view.fit_image)

    def _update_status(self):
        v = self.view

        if v._flash_msg is not None:
            self._status_label.setText(v._flash_msg)
            return

        mode_str = f"[{v.nav_mode.label}{'  ●' if v._watching else ''}]"

        if v._cmd is not None:
            candidates = v._cmd_candidates()
            query = v._cmd["query"]
            raw = v._cmd["cursor"]
            cursor = raw if raw < 0 else (min(raw, len(candidates) - 1) if candidates else 0)
            if v._cmd["phase"] == "verb":
                prompt = f":{query}▌"
                hide = False
            else:
                prompt = f":{v._cmd['verb']} {query}▌"
                hide = False
            if candidates and not hide:
                cand_str = "  ".join(
                    f"[{c}]" if i == cursor else c
                    for i, c in enumerate(candidates)
                )
                self._status_label.setText(f"{prompt}  →  {cand_str}")
            elif not candidates and v._cmd["phase"] == "verb" and query:
                self._status_label.setText(f"{prompt}  (unknown command)")
            else:
                self._status_label.setText(prompt)
            return

        if v._sel is not None:
            phase = v._sel["phase"]
            query = v._sel["query"]
            cursor = v._sel["cursor"]
            candidates = v._sel_candidates()
            if phase == "axis":
                prompt = f"axis? {query}▌"
            else:
                axis_name = v.axis_names[v._sel["axis_idx"]]
                prompt = f"{axis_name}? {query}▌"
            if candidates:
                cand_str = "  ".join(
                    f"[{c}]" if i == cursor else c
                    for i, c in enumerate(candidates)
                )
            else:
                cand_str = "(no match)"
            self._status_label.setText(f"{mode_str}  |  {prompt}  →  {cand_str}")
            return

        h_idx = v._h_axis()
        v_idx = v._v_axis()

        coord_str = "  ".join(
            f"{name}={v.axis_values[i][v.pos[i]]:<{max(len(val) for val in v.axis_values[i])}}"
            for i, name in enumerate(v.axis_names)
        )
        h_name = v.axis_names[h_idx]
        v_name = v.axis_names[v_idx] if v_idx is not None else "—"
        bind_str = f"→/← {h_name}  ↑/↓ {v_name}"
        sep = "  |  "
        if v.nav_mode == NavMode.SEEK:
            key_hints = "  ".join(v.axis_names)
            parts = [mode_str, coord_str, bind_str]
            if key_hints:
                parts.append(key_hints)
            self._status_label.setText(sep.join(parts))
        else:
            name_to_ch = {v.axis_names[idx]: ch for ch, idx in v.key_to_axis.items()}
            unbound = [n for n in v.axis_names if n not in name_to_ch]
            bound_items = [
                f"[{ch}] {v.axis_names[v.key_to_axis[ch]]}"
                for ch in sorted(v.key_to_axis)
            ]
            if unbound:
                e = _html.escape
                bound_html = "  ".join(e(b) for b in bound_items)
                unbound_html = "  ".join(
                    f'<span style="color:#cc4444">{e(n)}</span>' for n in unbound
                )
                key_hints_html = "  ".join(filter(None, [bound_html, unbound_html]))
                parts_html = sep.join(e(p) for p in [mode_str, coord_str, bind_str])
                label = parts_html + sep + key_hints_html if key_hints_html else parts_html
                self._status_label.setText(label)
            else:
                key_hints = "  ".join(bound_items)
                parts = [mode_str, coord_str, bind_str]
                if key_hints:
                    parts.append(key_hints)
                self._status_label.setText(sep.join(parts))

    def _toggle_status_bar(self):
        sb = self.statusBar()
        sb.setVisible(not sb.isVisible())
