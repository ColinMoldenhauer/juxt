from __future__ import annotations

from enum import IntEnum

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
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
    "switch-last",
    "zoom",
]

_ALIASES: dict[str, str] = {
    "q": "quit",
}

# Discrete argument options for commands that take a value.
# Commands absent from this dict take no arguments (or free-text only).
_CMD_ARGS: dict[str, list[str]] = {
    "mode": ["twin", "multi-select", "case-sensitive"],
    "zoom": ["50", "75", "100", "150", "200"],
}


class NavMode(IntEnum):
    TWIN = 0
    MULTI_SELECT = 1
    CASE_SENSITIVE = 2

    @property
    def label(self) -> str:
        return ("twin", "multi-select", "case-sensitive")[self]


class ImageView(QGraphicsView):
    state_changed = Signal()
    toggle_bar = Signal()

    def __init__(self, config: Config, pixmaps: dict[tuple, QPixmap], parent=None):
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

        # letter → axis index
        self.key_to_axis: dict[str, int] = {
            ch: self.axis_names.index(name)
            for ch, name in config.keys.items()
            if name in self.axis_names
        }

        self.nav_mode = NavMode(config.mode)

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
                self._cmd["cursor"] = 0
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
                self._cmd["cursor"] = (self._cmd["cursor"] + 1) % len(candidates)
                self.state_changed.emit()
            return True

        if k == Qt.Key_Left:
            candidates = self._cmd_candidates()
            if candidates:
                self._cmd["cursor"] = (self._cmd["cursor"] - 1) % len(candidates)
                self.state_changed.emit()
            return True

        if k in (Qt.Key_Return, Qt.Key_Enter):
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
                if arg in ("0", "twin"):
                    self.nav_mode = NavMode.TWIN
                elif arg in ("1", "multi", "multi-select"):
                    self.nav_mode = NavMode.MULTI_SELECT
                elif arg in ("2", "case", "case-sensitive"):
                    self.nav_mode = NavMode.CASE_SENSITIVE
                self._sel = None
                self.state_changed.emit()
        elif verb == "switch-last":
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
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
            self._cmd = {"phase": "verb", "query": "", "cursor": 0}
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

        if self.nav_mode == NavMode.TWIN:
            if mods == Qt.ControlModifier and ch_lower in self.key_to_axis:
                self._sel_open_value(self.key_to_axis[ch_lower])
            elif mods == Qt.NoModifier and ch_lower in self.key_to_axis:
                self._focus(self.key_to_axis[ch_lower])
                self.state_changed.emit()
            else:
                super().keyPressEvent(event)

        elif self.nav_mode == NavMode.MULTI_SELECT:
            if mods == Qt.NoModifier and ch.isalpha():
                self._sel_open_axis(ch)
            else:
                super().keyPressEvent(event)

        elif self.nav_mode == NavMode.CASE_SENSITIVE:
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
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def resizeEvent(self, event):
        super().resizeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, config: Config, pixmaps: dict):
        super().__init__()
        self.setWindowTitle("juxt")
        self.view = ImageView(config, pixmaps, self)
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
            "font-family: 'Courier New', monospace; font-size: 9pt;"
        )
        bar.addWidget(self._status_label, 1)

        self.view.state_changed.connect(self._update_status)
        self.view.toggle_bar.connect(self._toggle_status_bar)

        self._update_status()
        QTimer.singleShot(0, self.view.fit_image)

    def _update_status(self):
        v = self.view
        mode_str = f"[{v.nav_mode.label}]"

        if v._cmd is not None:
            candidates = v._cmd_candidates()
            cursor = min(v._cmd["cursor"], len(candidates) - 1) if candidates else 0
            query = v._cmd["query"]
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
        key_hints = "  ".join(
            f"[{ch}] {v.axis_names[v.key_to_axis[ch]]}"
            for ch in sorted(v.key_to_axis)
        )

        parts = [mode_str, coord_str, bind_str]
        if key_hints:
            parts.append(key_hints)
        self._status_label.setText("  |  ".join(parts))

    def _toggle_status_bar(self):
        sb = self.statusBar()
        sb.setVisible(not sb.isVisible())
