from __future__ import annotations

import html as _html
import logging
import re as _re
import threading
from enum import IntEnum
from typing import Literal

from PySide6.QtCore import QEvent, Qt, QFileSystemWatcher, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QWidget,
)

from .complete import (
    complete_path,
    complete_placeholder_name,
    local_listdir,
    normalize_template,
    placeholder_html,
    reset_placeholder_shapes,
    sftp_listdir,
)
from .config import Config
from .settings import (
    DEFAULT_HIGHLIGHT,
    DEFAULT_HIGHLIGHT_CANDIDATES,
    SETTINGS_PATH,
    Highlight,
    load_settings,
    parse_highlight,
)

log = logging.getLogger(__name__)

# Fallback highlight formats, used until settings are applied.
_HL_DEFAULT = parse_highlight(DEFAULT_HIGHLIGHT)
_HL_DEFAULT_CANDIDATES = parse_highlight(DEFAULT_HIGHLIGHT_CANDIDATES)

# Colour of a non-highlighted value link in the info sidebar.
_LINK_COLOR = "#ddd"

# ── info-sidebar links ────────────────────────────────────────────────────────

_VALUE_HREF = "juxt:value/{axis}/{value}"
_VALUE_HREF_RE = _re.compile(r"^juxt:value/(\d+)/(\d+)$")


def _sp(text: str) -> str:
    """Escape *text* for rich text, keeping runs of spaces intact."""
    return _html.escape(text).replace(" ", "&nbsp;")


def _hl_css(hl: Highlight) -> str:
    """Inline CSS for a parsed highlight format."""
    parts = []
    if hl.color:
        parts.append(f"color:{hl.color}")
    if hl.bold:
        parts.append("font-weight:bold")
    if hl.italic:
        parts.append("font-style:italic")
    parts.append("text-decoration:underline" if hl.underline else "text-decoration:none")
    return ";".join(parts)


def _hl(text: str, hl: Highlight = _HL_DEFAULT) -> str:
    """Apply the highlight format *hl* to the already-escaped *text*."""
    if hl.raw is not None:
        return hl.raw.replace("{}", text)
    return (f'<span style="{_hl_css(hl)}">'
            f'{_sp(hl.prefix)}{text}{_sp(hl.suffix)}</span>')


def _rich(text: str) -> str:
    """Mark *text* as rich text for _ElidingLabel, even without any tag."""
    return f"<span>{text}</span>"


def _candidate_html(
    candidates: list[str], cursor: int, hl: Highlight = _HL_DEFAULT_CANDIDATES
) -> str:
    """Render a status-bar candidate list, highlighting the one at *cursor*."""
    return "&nbsp;&nbsp;".join(
        _hl(_sp(c), hl) if i == cursor else _sp(c)
        for i, c in enumerate(candidates)
    )


def _value_href(axis_idx: int, val_idx: int) -> str:
    """Anchor target encoding one (axis, value) pair for the info sidebar."""
    return _VALUE_HREF.format(axis=axis_idx, value=val_idx)


def _parse_value_href(href: str) -> tuple[int, int] | None:
    """Inverse of :func:`_value_href`; returns None for foreign hrefs."""
    m = _VALUE_HREF_RE.match(href)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ── key-binding parser ────────────────────────────────────────────────────────

_MOD_MAP: dict[str, int] = {
    "ctrl": Qt.ControlModifier.value, "control": Qt.ControlModifier.value,
    "shift": Qt.ShiftModifier.value,
    "alt": Qt.AltModifier.value,
    "meta": Qt.MetaModifier.value,
}

_KEY_MAP: dict[str, int] = {
    "return": Qt.Key_Return, "enter": Qt.Key_Return,
    "escape": Qt.Key_Escape, "esc": Qt.Key_Escape,
    "space": Qt.Key_Space, "tab": Qt.Key_Tab,
    "backspace": Qt.Key_Backspace, "delete": Qt.Key_Delete,
    "home": Qt.Key_Home, "end": Qt.Key_End,
    "left": Qt.Key_Left, "right": Qt.Key_Right,
    "up": Qt.Key_Up, "down": Qt.Key_Down,
    **{f"f{n}": getattr(Qt, f"Key_F{n}") for n in range(1, 13)},
    **{str(n): getattr(Qt, f"Key_{n}") for n in range(10)},
}


def _parse_key(s: str) -> tuple[int, int]:
    """Parse 'Ctrl+Shift+H' → (Qt.Key value, modifier flags int)."""
    mods = 0
    key: int | None = None
    for part in (p.strip() for p in s.split("+")):
        lower = part.lower()
        if lower in _MOD_MAP:
            mods |= _MOD_MAP[lower]
        elif len(part) == 1 and part.isalpha():
            key = getattr(Qt, f"Key_{part.upper()}")
        elif lower in _KEY_MAP:
            key = _KEY_MAP[lower]
        else:
            raise ValueError(f"unknown key token: {part!r}")
    if key is None:
        raise ValueError(f"no key in binding: {s!r}")
    return key, mods


def _chord_str(key: int, mods: int) -> str:
    """Reconstruct a human-readable chord like 'Ctrl+Shift+H' from parsed values."""
    parts = []
    if mods & Qt.ControlModifier.value:
        parts.append("Ctrl")
    if mods & Qt.ShiftModifier.value:
        parts.append("Shift")
    if mods & Qt.AltModifier.value:
        parts.append("Alt")
    if mods & Qt.MetaModifier.value:
        parts.append("Meta")
    if Qt.Key_A <= key <= Qt.Key_Z:
        parts.append(chr(key - Qt.Key_A + ord('A')))
    else:
        for name, val in _KEY_MAP.items():
            if val == key and not name[0].isdigit():
                parts.append(name.capitalize())
                break
        else:
            parts.append(f"0x{key:x}")
    return "+".join(parts) if parts else "?"


def _keybinding_conflicts(
    parsed_bindings: dict[tuple[int, int], str],
    axis_letters: set[str],
) -> list[str]:
    """Return one human-readable description per binding that shadows navigation.

    Conflicts per mode (key type → affected modes):
      bare letter       → seek always; tap + pin if the letter is an axis key
      Shift+letter      → tap if letter is an axis key (navigate -1)
      Ctrl+letter       → tap + pin if letter is an axis key (value picker)
    """
    ctrl = Qt.ControlModifier.value
    shift = Qt.ShiftModifier.value
    conflicts = []
    for (key, mods), action in sorted(parsed_bindings.items()):
        if not (Qt.Key_A <= key <= Qt.Key_Z):
            continue
        letter = chr(key - Qt.Key_A + ord('a'))
        clashing = []
        if mods == 0:
            clashing.append("seek")
            if letter in axis_letters:
                clashing += ["tap", "pin"]
        elif mods == shift:
            if letter in axis_letters:
                clashing.append("tap")
        elif mods == ctrl:
            if letter in axis_letters:
                clashing += ["tap", "pin"]
        if clashing:
            axis_note = f" (axis key '{letter}')" if letter in axis_letters else ""
            conflicts.append(
                f"{_chord_str(key, mods)} → {action!r}: shadows "
                f"{'/'.join(clashing)} mode{axis_note}"
            )
    return conflicts


_COMMANDS = [
    "axis-auto",
    "axis-h",
    "axis-v",
    "change-key",
    "copy-image",
    "copy-path",
    "fit",
    "fit-height",
    "fit-width",
    "fullscreen",
    "grid",
    "grid-layout",
    "grid-sharex",
    "grid-sharey",
    "info",
    "mode",
    "pattern",
    "quit",
    "reload",
    "remove-axis",
    "settings",
    "remove-value",
    "ungrid",
    "watch",
    "write",
    "swap-axes",
    "switch-last",
    "zoom",
]

_ALIASES: dict[str, str] = {
    "q": "quit",
    "w": "write",
}

# Discrete argument options for commands that take a value.
# Commands absent from this dict take no arguments (or free-text only).
# axis-h and axis-v completions are dynamic (axis names); handled in _cmd_candidates.
_CMD_ARGS: dict[str, list[str]] = {
    "axis-h": [],
    "axis-v": [],
    "change-key": [],
    "grid": [],
    "grid-layout": [],
    "grid-sharex": ["on", "off"],
    "grid-sharey": ["on", "off"],
    "mode": ["tap", "seek", "pin"],
    "pattern": [],  # free-text path / template
    "remove-axis": [],
    "remove-value": [],
    "watch": ["true", "false"],
    "write": [],    # free-text path; empty → file dialog
    "zoom": ["50", "75", "100", "150", "200"],
}

# Commands whose argument is free-text — preserve case when the user types it.
_FREE_TEXT_ARGS = {"change-key", "pattern", "remove-axis", "remove-value", "write"}

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_COMMAND_HELP: dict[str, str] = {
    "axis-auto":    "restore dynamic axis-to-arrow assignment",
    "change-key":   "assign a key letter to an axis  (e.g. change-key sensor x)",
    "copy-image":   "copy current image to clipboard",
    "copy-path":    "copy current image file path to clipboard",
    "axis-h":       "lock ←/→ to a named axis",
    "axis-v":       "lock ↑/↓ to a named axis",
    "fit":          "fit image to window",
    "fit-height":   "fit image height to viewport",
    "fit-width":    "fit image width to viewport",
    "fullscreen":   "toggle fullscreen",
    "grid":         "expand an axis into a tiled grid  (e.g. grid sensor  /  grid sensor SMAP SMOS  /  grid sensor 2x2)",
    "grid-layout":  "change the grid layout without exiting  (e.g. grid-layout 2x3)",
    "grid-sharex":  "toggle or set synchronized horizontal pan/zoom  (on / off)",
    "grid-sharey":  "toggle or set synchronized vertical pan/zoom  (on / off)",
    "info":         "toggle the info sidebar",
    "mode":         "switch navigation mode  (tap / seek / pin)",
    "pattern":      "change the template / source path without restarting",
    "quit":         "quit juxt",
    "reload":       "re-detect axes and reload images",
    "settings":     "open ~/.juxt/settings.yaml in the default editor",
    "remove-axis":  "remove an axis (collapses to its current value)",
    "remove-value": "remove a value from an axis  (e.g. remove-value sensor SMAP)",
    "ungrid":       "return to single-image view",
    "write":        "write current config to a YAML file",
    "swap-axes":    "swap the ←/→ and ↑/↓ axis bindings",
    "switch-last":  "toggle between current and previous position",
    "watch":        "enable / disable / configure file watching",
    "zoom":         "set zoom level  (e.g. zoom 150)",
}


def _cmd_query_display(query: str, caret: int, max_visible: int = 55) -> str:
    """Return a viewport into *query* with ▌ at *caret*, scrolling to keep it visible."""
    if len(query) <= max_visible:
        return query[:caret] + "▌" + query[caret:]
    # Keep caret roughly centred in the window
    start = max(0, min(caret - max_visible // 2, len(query) - max_visible))
    end = start + max_visible
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(query) else ""
    return f"{prefix}{query[start:caret]}▌{query[caret:end]}{suffix}"


class _ElidingLabel(QLabel):
    """QLabel that elides its content (plain text or HTML) to fit the available width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_plain = ""
        self._full_html: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        if "<" in text:
            self._full_html = text
            self._full_plain = _html.unescape(_re.sub(r"<[^>]+>", "", text))
        else:
            self._full_html = None
            self._full_plain = text
        self._recompute()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recompute()

    def _recompute(self):
        w = self.width()
        if w <= 0:
            return
        fm = self.fontMetrics()
        if fm.horizontalAdvance(self._full_plain) <= w:
            super().setText(self._full_html if self._full_html is not None else self._full_plain)
        else:
            super().setText(fm.elidedText(self._full_plain, Qt.TextElideMode.ElideRight, w))


class _Cancelled(BaseException):
    """Raised inside a pattern worker to signal user-initiated cancellation."""


class NavMode(IntEnum):
    TAP = 0
    SEEK = 1
    PIN = 2

    @property
    def label(self) -> str:
        return ("tap", "seek", "pin")[self]


def _window_title(config: "Config", session_name: str | None = None) -> str:
    from pathlib import PurePosixPath
    if session_name:
        return f"juxt | {session_name}"
    parts = PurePosixPath(config.template).parts
    name = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[0] if parts else config.template)
    if config.remote:
        return f"juxt | {config.remote.host}: {name}"
    return f"juxt | {name}"


class _CellView(QGraphicsView):
    """Single-image viewport used inside GridWidget."""

    zoomed = Signal(object)  # emits self.transform() after a wheel-zoom

    def __init__(self, label: str, parent=None):
        scene = QGraphicsScene()
        super().__init__(scene, parent)
        self._scene = scene
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        scene.addItem(self._item)
        self._fit: Literal["image", "height", "width"] | None = None
        self._label_text = label

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(25, 25, 25))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

    def set_pixmap(self, pm: QPixmap):
        self._item.setPixmap(pm)
        self._scene.setSceneRect(QRectF(pm.rect()))
        if self._fit is not None:
            getattr(self, f"fit_{self._fit}")()

    def fit_image(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._fit = "image"

    def fit_height(self):
        pm = self._item.pixmap()
        if pm.isNull():
            return
        self.resetTransform()
        self.scale(self.viewport().height() / pm.height(),
                   self.viewport().height() / pm.height())
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "height"

    def fit_width(self):
        pm = self._item.pixmap()
        if pm.isNull():
            return
        self.resetTransform()
        self.scale(self.viewport().width() / pm.width(),
                   self.viewport().width() / pm.width())
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "width"

    def set_zoom(self, pct: float):
        self._fit = None
        self.resetTransform()
        self.scale(pct / 100.0, pct / 100.0)

    def reset_zoom(self):
        self._fit = None
        self.resetTransform()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 ** (event.angleDelta().y() / 120)
            self.scale(factor, factor)
            self._fit = None
            self.zoomed.emit(self.transform())
        else:
            super().wheelEvent(event)

    def drawForeground(self, painter: QPainter, _rect):
        if not self._label_text:
            return
        painter.save()
        painter.resetTransform()   # switch to viewport pixel coordinates
        fm = painter.fontMetrics()
        pad_x, pad_y, margin = 5, 2, 4
        text_w = fm.horizontalAdvance(self._label_text)
        text_h = fm.height()
        bg = QRectF(margin, margin, text_w + 2 * pad_x, text_h + 2 * pad_y)
        painter.fillRect(bg, QColor(0, 0, 0, 160))
        painter.setPen(QColor(224, 224, 224))
        painter.drawText(
            int(bg.x() + pad_x),
            int(bg.y() + pad_y + text_h - fm.descent()),
            self._label_text,
        )
        painter.restore()

    def mouseDoubleClickEvent(self, _event):
        self.fit_image()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit is not None:
            getattr(self, f"fit_{self._fit}")()


class GridWidget(QWidget):
    """Grid of independent image viewports with optional scroll/zoom sync."""

    def __init__(
        self,
        image_view: "ImageView",
        rows: int,
        cols: int,
        labels: list[str],
        sharex: bool,
        sharey: bool,
    ):
        super().__init__()
        self._image_view = image_view
        self._sharex = sharex
        self._sharey = sharey
        self._syncing = False

        layout = QGridLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)
        self._cells: list[_CellView] = []

        for i, label in enumerate(labels):
            row, col = i // cols, i % cols
            cell = _CellView(label)
            layout.addWidget(cell, row, col)
            self._cells.append(cell)
            cell.horizontalScrollBar().valueChanged.connect(
                lambda val, c=cell: self._on_hscroll(val, c)
            )
            cell.verticalScrollBar().valueChanged.connect(
                lambda val, c=cell: self._on_vscroll(val, c)
            )
            cell.zoomed.connect(lambda t, c=cell: self._on_zoom(t, c))

    # ── sync handlers ─────────────────────────────────────────────────────────

    def _on_hscroll(self, val: int, source: _CellView):
        if self._syncing or not self._sharex:
            return
        self._syncing = True
        for cell in self._cells:
            if cell is not source:
                cell.horizontalScrollBar().setValue(val)
        self._syncing = False

    def _on_vscroll(self, val: int, source: _CellView):
        if self._syncing or not self._sharey:
            return
        self._syncing = True
        for cell in self._cells:
            if cell is not source:
                cell.verticalScrollBar().setValue(val)
        self._syncing = False

    def _on_zoom(self, transform, source: _CellView):
        if self._syncing or (not self._sharex and not self._sharey):
            return
        self._syncing = True
        h = source.horizontalScrollBar().value()
        v = source.verticalScrollBar().value()
        for cell in self._cells:
            if cell is not source:
                cell.setTransform(transform)
                if self._sharex:
                    cell.horizontalScrollBar().setValue(h)
                if self._sharey:
                    cell.verticalScrollBar().setValue(v)
        self._syncing = False

    # ── public API ────────────────────────────────────────────────────────────

    def update_cells(self, base_pos: list, axis_idx: int, indices: list[int], pixmaps: dict):
        for grid_i, vi in enumerate(indices):
            if grid_i >= len(self._cells):
                break
            key = list(base_pos)
            key[axis_idx] = vi
            pm = pixmaps.get(tuple(key))
            if pm and not pm.isNull():
                self._cells[grid_i].set_pixmap(pm)

    def fit_image(self):
        for cell in self._cells:
            cell.fit_image()

    def fit_height(self):
        for cell in self._cells:
            cell.fit_height()

    def fit_width(self):
        for cell in self._cells:
            cell.fit_width()

    def set_zoom(self, pct: float):
        for cell in self._cells:
            cell.set_zoom(pct)

    def reset_zoom(self):
        for cell in self._cells:
            cell.reset_zoom()

    def set_sharex(self, val: bool):
        self._sharex = val

    def set_sharey(self, val: bool):
        self._sharey = val

    def keyPressEvent(self, event):
        self._image_view.keyPressEvent(event)

    def mouseDoubleClickEvent(self, _event):
        self.fit_image()


class ImageView(QGraphicsView):
    state_changed = Signal()
    toggle_bar = Signal()
    toggle_info = Signal()
    config_changed = Signal()           # emitted after reload/pattern successfully replaces config
    grid_mode_changed = Signal(object)  # emits GridWidget on enter, None on exit
    _poll_result = Signal(object)       # emitted from poll worker; carries list or Exception
    _reload_result = Signal(object)     # emitted from reload worker; carries tuple or Exception
    _pattern_result = Signal(object)    # emitted from pattern worker; carries dict or Exception
    _pattern_progress = Signal(int, int, str)  # value, total, label

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
        axis_h: str | None = None,
        axis_v: str | None = None,
        seek_greedy: bool = True,
        keybindings: dict[str, str] | None = None,
        grid: str | None = None,
        grid_values: list[str] | None = None,
        grid_layout: tuple[int, int] | None = None,
        grid_sharex: bool = True,
        grid_sharey: bool = True,
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

        self._active_axis: int | None = None
        self._active_axis_timer = QTimer(self)
        self._active_axis_timer.setSingleShot(True)
        self._active_axis_timer.timeout.connect(self._clear_active_axis)

        self._watching = False
        self._watcher: QFileSystemWatcher | None = None
        self._path_to_key: dict[str, tuple] = {}
        self._tab_matches: list[str] = []
        self._fit: Literal["image", "height", "width"] | None = None
        self._pre_fullscreen_maximized: bool | None = None
        self._seek_greedy: bool = seek_greedy
        self._keybindings: dict[tuple[int, int], str] = {}
        for _ks, _action in (keybindings or {}).items():
            try:
                _k, _m = _parse_key(_ks)
                self._keybindings[(_k, _m)] = _action
            except ValueError as e:
                log.warning("Invalid keybinding %r: %s", _ks, e)

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
        self._pattern_result.connect(self._apply_pattern)
        self._pattern_progress.connect(self._on_pattern_progress)
        self._pattern_dlg = None
        self._initial_fit_done = False
        self._locked_h: int | None = self.axis_names.index(axis_h) if axis_h and axis_h in self.axis_names else None
        self._locked_v: int | None = self.axis_names.index(axis_v) if axis_v and axis_v in self.axis_names else None

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
        QApplication.instance().installEventFilter(self)
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

        # Grid-view state
        self._grid_axis: int | None = None
        self._grid_filter: list[int] | None = None   # None = all values
        self._grid_layout: tuple[int, int] | None = None  # explicit (rows, cols)
        self._grid_sharex: bool = grid_sharex
        self._grid_sharey: bool = grid_sharey
        self._grid_widget: GridWidget | None = None

        self._refresh()

        if grid and grid in self.axis_names:
            axis_idx = self.axis_names.index(grid)
            filter_indices: list[int] | None = None
            if grid_values:
                vals = self.axis_values[axis_idx]
                filter_indices = [i for i, v in enumerate(vals) if v in grid_values] or None
            log.debug("startup grid: axis=%r filter=%r layout=%r", grid, filter_indices, grid_layout)
            # Defer until the event loop starts so MainWindow has connected grid_mode_changed
            QTimer.singleShot(0, lambda: self._enter_grid(axis_idx, filter_indices, grid_layout))

        if watch:
            if config.remote is None:
                self._start_watching(silent=True)
            elif remote_tmpdir is not None and poll_interval > 0:
                self._start_watching(silent=True)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _key(self) -> tuple:
        return tuple(self.pos)

    def _refresh(self):
        if self._grid_axis is not None:
            self._refresh_grid()
            return
        pm = self.pixmaps.get(self._key())
        if pm:
            self._item.setPixmap(pm)
            self._scene.setSceneRect(QRectF(pm.rect()))
        self.viewport().update()
        self.state_changed.emit()

    def _refresh_grid(self):
        if self._grid_widget is None:
            log.debug("_refresh_grid: no grid widget, skipping")
            return
        indices = (self._grid_filter if self._grid_filter is not None
                   else list(range(len(self.axis_values[self._grid_axis]))))
        log.debug("_refresh_grid: axis=%d indices=%s pos=%s", self._grid_axis, indices, self.pos)
        self._grid_widget.update_cells(self.pos, self._grid_axis, indices, self.pixmaps)
        self.state_changed.emit()

    def _enter_grid(self, axis_idx: int, filter_indices: list[int] | None, layout: tuple[int, int] | None):
        import math
        self._grid_axis = axis_idx
        self._grid_filter = filter_indices
        self._grid_layout = layout

        all_vals = self.axis_values[axis_idx]
        indices = filter_indices if filter_indices is not None else list(range(len(all_vals)))
        n = len(indices)
        log.debug("_enter_grid: axis=%r n=%d filter=%r layout=%r", self.axis_names[axis_idx], n, filter_indices, layout)

        # Sample image dimensions for aspect-ratio-aware auto layout
        img_w, img_h = 800, 600
        for vi in indices:
            key = list(self.pos)
            key[axis_idx] = vi
            pm = self.pixmaps.get(tuple(key))
            if pm and not pm.isNull():
                img_w, img_h = pm.width(), pm.height()
                break
        log.debug("_enter_grid: image size %dx%d", img_w, img_h)

        if layout is not None:
            rows, cols = layout
        else:
            vp = self.viewport()
            vp_ar = vp.width() / max(1, vp.height())
            cols, rows = min(
                ((c, math.ceil(n / c)) for c in range(1, n + 1)),
                key=lambda cr: abs((cr[0] * img_w) / max(1, cr[1] * img_h) - vp_ar),
            )
        log.info("grid: axis=%r  %d cells  layout=%dx%d  sharex=%s  sharey=%s",
                 self.axis_names[axis_idx], n, rows, cols, self._grid_sharex, self._grid_sharey)

        labels = [all_vals[vi] for vi in indices]
        gw = GridWidget(self, rows, cols, labels, self._grid_sharex, self._grid_sharey)
        self._grid_widget = gw
        self._refresh_grid()
        self.grid_mode_changed.emit(gw)

    def _exit_grid(self):
        log.info("grid: exiting grid mode (was axis=%r)", self.axis_names[self._grid_axis] if self._grid_axis is not None else None)
        self._grid_axis = None
        self._grid_filter = None
        self._grid_layout = None
        self._grid_widget = None
        self.grid_mode_changed.emit(None)
        self._refresh()

    def _h_axis(self) -> int:
        return self._locked_h if self._locked_h is not None else self.focus_stack[0]

    def _v_axis(self) -> int | None:
        if self._locked_v is not None:
            return self._locked_v
        return self.focus_stack[1] if len(self.focus_stack) >= 2 else None

    def _navigate(self, axis: int, delta: int):
        n = len(self.axis_values[axis])
        self.prev = list(self.pos)
        self.pos[axis] = (self.pos[axis] + delta) % n
        self._active_axis = axis
        self._active_axis_timer.start(1000)
        self._refresh()

    def _focus(self, axis: int):
        if axis in self.focus_stack:
            self.focus_stack.remove(axis)
        self.focus_stack.insert(0, axis)

    def goto_value(self, axis: int, val_idx: int):
        """Jump to *val_idx* on *axis* and focus it (info-sidebar clicks)."""
        if not 0 <= axis < len(self.axis_values):
            return
        if not 0 <= val_idx < len(self.axis_values[axis]):
            return
        self._focus(axis)
        self._active_axis = axis
        self._active_axis_timer.start(1000)
        if self.pos[axis] != val_idx:
            self.prev = list(self.pos)
            self.pos[axis] = val_idx
        self._refresh()

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

        Controlled by seek.greedy in ~/.juxt/settings.yaml.
        """
        candidates = self._sel_candidates()
        if len(candidates) == 1 and self._seek_greedy:
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
            verb = self._cmd["verb"]
            if verb in ("axis-h", "axis-v", "remove-axis"):
                pool = list(self.axis_names)
            elif verb == "grid":
                return self._grid_cmd_candidates(self._cmd["query"])
            else:
                pool = _CMD_ARGS.get(verb, [])
        return [c for c in pool if c.startswith(q)] if q else list(pool)

    def _grid_cmd_candidates(self, query: str) -> list[str]:
        """Multi-token candidate list for the :grid arg phase (axis [val …] [NxM])."""
        if " " not in query:
            return [n for n in self.axis_names if n.lower().startswith(query.lower())] if query else list(self.axis_names)
        tokens = query.split(" ")
        axis_name = tokens[0]
        axis_idx = next((i for i, n in enumerate(self.axis_names) if n.lower() == axis_name.lower()), None)
        if axis_idx is None:
            return []
        all_vals = [str(v) for v in self.axis_values[axis_idx]]
        partial = "" if query.endswith(" ") else tokens[-1]
        used = set(t.lower() for t in (tokens[1:-1] if not query.endswith(" ") else tokens[1:]) if t)
        remaining = [v for v in all_vals if v.lower() not in used]
        if partial:
            remaining = [v for v in remaining if v.lower().startswith(partial.lower())]
        return remaining

    def _cmd_handle_key(self, event: QKeyEvent) -> bool:
        """Handle input while command mode is active. Returns True if consumed."""
        k = event.key()
        is_free = (self._cmd.get("phase") == "arg"
                   and self._cmd.get("verb") in _FREE_TEXT_ARGS)

        if k == Qt.Key_Escape:
            self._cmd = None
            self._tab_matches = []
            self.state_changed.emit()
            return True

        if k == Qt.Key_Backspace:
            q = self._cmd["query"]
            if is_free:
                caret = self._cmd.get("caret", len(q))
                if caret > 0:
                    self._cmd["query"] = q[:caret - 1] + q[caret:]
                    self._cmd["caret"] = caret - 1
                    self._tab_matches = []
                    self.state_changed.emit()
            elif q:
                self._cmd["query"] = q[:-1]
                self._cmd["cursor"] = -1 if (not self._cmd["query"] and self._cmd["phase"] == "verb") else 0
                self.state_changed.emit()
            elif self._cmd["phase"] == "arg":
                verb = self._cmd["verb"]
                self._cmd = {"phase": "verb", "query": verb, "cursor": 0}
                candidates = self._cmd_candidates()
                if verb in candidates:
                    self._cmd["cursor"] = candidates.index(verb)
                self.state_changed.emit()
            else:
                self._cmd = None
                self.state_changed.emit()
            return True

        if k == Qt.Key_Delete:
            if is_free:
                q = self._cmd["query"]
                caret = self._cmd.get("caret", len(q))
                if caret < len(q):
                    self._cmd["query"] = q[:caret] + q[caret + 1:]
                    self._tab_matches = []
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Tab:
            log.debug("_cmd_handle_key: Tab  is_free=%s cmd=%s", is_free, self._cmd)
            if is_free:
                self._complete_path()
            elif self._cmd.get("phase") == "arg" and self._cmd.get("verb") == "grid":
                q = self._cmd["query"]
                candidates = self._cmd_candidates()
                cur = min(self._cmd["cursor"], len(candidates) - 1) if candidates else -1
                log.debug("  grid tab: q=%r candidates=%s cur=%d", q, candidates, cur)
                if cur >= 0:
                    if " " not in q:
                        self._cmd["query"] = candidates[cur] + " "
                    elif q.endswith(" "):
                        self._cmd["query"] = q + candidates[cur] + " "
                    else:
                        prefix = q.rsplit(" ", 1)[0]
                        self._cmd["query"] = prefix + " " + candidates[cur] + " "
                    self._cmd["cursor"] = 0
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Right:
            if is_free:
                q = self._cmd["query"]
                caret = self._cmd.get("caret", len(q))
                self._cmd["caret"] = min(caret + 1, len(q))
                self.state_changed.emit()
            else:
                candidates = self._cmd_candidates()
                if candidates:
                    cur = self._cmd["cursor"]
                    self._cmd["cursor"] = 0 if cur < 0 else (cur + 1) % len(candidates)
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Left:
            if is_free:
                caret = self._cmd.get("caret", len(self._cmd["query"]))
                self._cmd["caret"] = max(caret - 1, 0)
                self.state_changed.emit()
            else:
                candidates = self._cmd_candidates()
                if candidates:
                    cur = self._cmd["cursor"]
                    self._cmd["cursor"] = len(candidates) - 1 if cur < 0 else (cur - 1) % len(candidates)
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Home:
            if is_free:
                self._cmd["caret"] = 0
                self.state_changed.emit()
            return True

        if k == Qt.Key_End:
            if is_free:
                self._cmd["caret"] = len(self._cmd["query"])
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
                    initial = self._pattern_str() if verb == "pattern" else ""
                    self._cmd = {"phase": "arg", "verb": verb, "query": initial,
                                 "cursor": 0, "caret": len(initial)}
                    self.state_changed.emit()
                else:
                    self._cmd = None
                    self._cmd_execute(verb)
                    self.state_changed.emit()
            else:
                verb = self._cmd["verb"]
                q = self._cmd["query"]
                cur = min(self._cmd["cursor"], len(candidates) - 1) if candidates else -1
                if verb == "grid" and " " in q:
                    # multi-token: preserve already-typed tokens, complete last partial
                    if q.endswith(" "):
                        prefix = q.rstrip()
                        selected = candidates[cur] if cur >= 0 else ""
                        arg = f"{prefix} {selected}".strip() if selected else prefix
                    else:
                        prefix, partial = q.rsplit(" ", 1)
                        selected = candidates[cur] if cur >= 0 else partial
                        arg = f"{prefix} {selected}".strip()
                else:
                    arg = candidates[cur] if cur >= 0 else q
                self._cmd = None
                self._cmd_execute(f"{verb} {arg}" if arg else verb)
                self.state_changed.emit()
            return True

        ch = event.text()
        if ch and ch.isprintable():
            if is_free:
                q = self._cmd["query"]
                caret = self._cmd.get("caret", len(q))
                self._cmd["query"] = q[:caret] + ch + q[caret:]
                self._cmd["caret"] = caret + 1
                self._tab_matches = []
            else:
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
        elif verb == "copy-path":
            path = self._key_to_path(self._key())
            QApplication.clipboard().setText(path)
            self._flash(f"copied: {path}")
        elif verb == "copy-image":
            pm = self.pixmaps.get(self._key())
            if pm and not pm.isNull():
                QApplication.clipboard().setPixmap(pm)
                self._flash("image copied to clipboard")
            else:
                self._flash("no image to copy")
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
            self._toggle_fullscreen()
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
        elif verb == "settings":
            from .settings import ensure_settings_file, SETTINGS_PATH
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            path = ensure_settings_file(SETTINGS_PATH)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif verb == "watch":
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
        elif verb == "write":
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
        elif verb == "pattern":
            raw_arg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if raw_arg:
                self._do_pattern(raw_arg)
            else:
                self._flash("usage: pattern <path-or-template>")
        elif verb == "axis-h":
            if args:
                name = args[0]
                if name in self.axis_names:
                    self._locked_h = self.axis_names.index(name)
                    self.state_changed.emit()
                else:
                    self._flash(f"unknown axis: {name!r}")
        elif verb == "axis-v":
            if args:
                name = args[0]
                if name in self.axis_names:
                    self._locked_v = self.axis_names.index(name)
                    self.state_changed.emit()
                else:
                    self._flash(f"unknown axis: {name!r}")
        elif verb == "axis-auto":
            self._locked_h = None
            self._locked_v = None
            self.state_changed.emit()
        elif verb == "swap-axes":
            h, v = self._h_axis(), self._v_axis()
            if v is not None:
                if self._locked_h is not None or self._locked_v is not None:
                    self._locked_h, self._locked_v = v, h
                else:
                    self.focus_stack[0], self.focus_stack[1] = v, h
                self.state_changed.emit()
        elif verb == "switch-last":
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
                self._refresh()
        elif verb == "grid":
            raw_args = parts[1:] if len(parts) > 1 else []

            # Optional first token: axis name (case-insensitive)
            axis_idx = self._h_axis()
            if raw_args and raw_args[0].lower() in [n.lower() for n in self.axis_names]:
                axis_idx = next(i for i, n in enumerate(self.axis_names) if n.lower() == raw_args[0].lower())
                raw_args = raw_args[1:]

            # Optional last token: NxM layout override
            layout: tuple[int, int] | None = None
            if raw_args and _re.match(r'^\d+x\d+$', raw_args[-1], _re.IGNORECASE):
                nr, nc = map(int, raw_args[-1].lower().split('x'))
                if nr < 1 or nc < 1:
                    self._flash("layout rows and cols must be ≥ 1")
                    return
                layout = (nr, nc)
                raw_args = raw_args[:-1]

            # Remaining tokens: value subset (original-case match, case-insensitive)
            filter_indices: list[int] | None = None
            if raw_args:
                all_vals = self.axis_values[axis_idx]
                filter_indices = []
                for v in raw_args:
                    match = next((i for i, val in enumerate(all_vals) if val.lower() == v.lower()), None)
                    if match is None:
                        self._flash(f"unknown value: {v!r}")
                        return
                    if match not in filter_indices:
                        filter_indices.append(match)

            self._enter_grid(axis_idx, filter_indices, layout)
        elif verb == "ungrid":
            if self._grid_axis is not None:
                self._exit_grid()
        elif verb == "grid-layout":
            if not args:
                self._flash("usage: grid-layout NxM  (e.g. grid-layout 2x3)")
                return
            m = _re.match(r'^(\d+)x(\d+)$', args[0], _re.IGNORECASE)
            if not m:
                self._flash(f"invalid layout: {args[0]!r}  (expected NxM)")
                return
            nr, nc = int(m.group(1)), int(m.group(2))
            if nr < 1 or nc < 1:
                self._flash("rows and cols must be ≥ 1")
                return
            if self._grid_axis is None:
                self._flash("not in grid mode")
                return
            self._enter_grid(self._grid_axis, self._grid_filter, (nr, nc))
        elif verb == "grid-sharex":
            if not args:
                self._grid_sharex = not self._grid_sharex
            elif args[0] == "on":
                self._grid_sharex = True
            elif args[0] == "off":
                self._grid_sharex = False
            if self._grid_widget is not None:
                self._grid_widget.set_sharex(self._grid_sharex)
            self._flash(f"sharex {'on' if self._grid_sharex else 'off'}")
            self.state_changed.emit()
        elif verb == "grid-sharey":
            if not args:
                self._grid_sharey = not self._grid_sharey
            elif args[0] == "on":
                self._grid_sharey = True
            elif args[0] == "off":
                self._grid_sharey = False
            if self._grid_widget is not None:
                self._grid_widget.set_sharey(self._grid_sharey)
            self._flash(f"sharey {'on' if self._grid_sharey else 'off'}")
            self.state_changed.emit()
        elif verb == "info":
            self.toggle_info.emit()
        elif verb == "remove-axis":
            name = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not name:
                name = self.axis_names[self._h_axis()]
            if name not in self.axis_names:
                self._flash(f"unknown axis: {name!r}")
            elif len(self.axis_names) <= 1:
                self._flash("cannot remove the only axis")
            else:
                self._do_remove_axis(self.axis_names.index(name))
        elif verb == "remove-value":
            raw = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            axis_name = next((n for n in self.axis_names
                              if raw == n or raw.startswith(n + " ")), None)
            if axis_name is None:
                self._flash("usage: remove-value AXIS VALUE")
                return
            val_str = raw[len(axis_name):].strip()
            if not val_str:
                self._flash(f"usage: remove-value {axis_name} VALUE")
                return
            axis_idx = self.axis_names.index(axis_name)
            vals = self.axis_values[axis_idx]
            if val_str not in vals:
                self._flash(f"unknown value: {val_str!r}")
            elif len(vals) <= 1:
                self._flash("cannot remove the only value in an axis")
            else:
                self._do_remove_value(axis_idx, vals.index(val_str))
        elif verb == "change-key":
            raw = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            tokens = raw.rsplit(None, 1)
            if len(tokens) == 2:
                axis_name, letter = tokens
                if axis_name not in self.axis_names:
                    self._flash(f"unknown axis: {axis_name!r}")
                elif not (len(letter) == 1 and letter.isalpha()):
                    self._flash("letter must be a single a–z character")
                else:
                    self._do_change_key(axis_name, letter.lower())
            elif len(tokens) == 1:
                axis_name = tokens[0]
                if axis_name not in self.axis_names:
                    self._flash(f"unknown axis: {axis_name!r}")
                else:
                    self._do_change_key(axis_name, None)
            else:
                self._flash("usage: change-key AXIS LETTER")

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

    def _clear_active_axis(self):
        self._active_axis = None
        self.state_changed.emit()

    def apply_settings(self, settings) -> None:
        """Apply a freshly loaded Settings object without restarting."""
        self._seek_greedy = settings.seek_greedy
        new_kb: dict[tuple[int, int], str] = {}
        for ks, action in settings.keybindings.items():
            try:
                k, m = _parse_key(ks)
                new_kb[(k, m)] = action
            except ValueError as e:
                log.warning("Invalid keybinding %r: %s", ks, e)
        self._keybindings = new_kb

    def _dispatch_action(self, action: str):
        if action == "toggle-statusbar":
            self.toggle_bar.emit()
        elif action == "toggle-info":
            self.toggle_info.emit()
        else:
            self._cmd_execute(action)

    def _toggle_fullscreen(self):
        win = self.window()
        if win.isFullScreen():
            if self._pre_fullscreen_maximized:
                win.showMaximized()
            else:
                win.showNormal()
            self._pre_fullscreen_maximized = None
        else:
            self._pre_fullscreen_maximized = win.isMaximized()
            win.showFullScreen()

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
            n = len(self._path_to_key)
            log.info("Watching %d local file%s", n, "s" if n != 1 else "")
            if not silent:
                self._flash(f"watching {n} file{'s' if n != 1 else ''}")
        else:
            if self._remote_tmpdir is None:
                self._flash("remote polling not available (no cache dir)")
                return
            self._poll_timer.start(self._poll_interval * 1000)
            self._watching = True
            log.info("Polling remote every %ds", self._poll_interval)
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
        log.info("File watching stopped")
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
            log.warning("Remote poll failed: %s", result)
            self._flash(f"poll failed: {result}")
            return
        # result is list[(key, local_path|None)] for changed files only.
        # local_path is None when the remote file was deleted or moved.
        # Decode QPixmaps here on the main thread (Qt requirement).
        changed: list = result
        if changed:
            log.debug("Remote poll: %d file(s) changed", len(changed))
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
        self.state_changed.emit()
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
                    # Always open a fresh connection for reload so that
                    # directory listings are not stale and we don't race
                    # with a concurrent poll worker on the shared conn.
                    from .loader import _connect_sftp
                    try:
                        if conn[1] is not None:
                            conn[1].close()
                        if conn[0] is not None:
                            conn[0].close()
                    except Exception:
                        pass
                    conn[0], conn[1] = _connect_sftp(config.remote, get_pw)
                    from .detect import _axes_from_sftp_template
                    new_axes = _axes_from_sftp_template(config.template, conn[1])
                    if not new_axes:
                        raise ValueError("no images found for current template")
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
                    if not new_axes:
                        raise ValueError("no images found for current template")
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
            log.warning("Reload failed: %s", result)
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

        # Remap locked axes by name to new indices (clear if axis disappeared)
        old_locked_h_name = old_names[self._locked_h] if self._locked_h is not None and self._locked_h < len(old_names) else None
        old_locked_v_name = old_names[self._locked_v] if self._locked_v is not None and self._locked_v < len(old_names) else None

        # Exit grid mode before replacing axes (axis indices will shift)
        if self._grid_axis is not None:
            self._exit_grid()

        self.config = new_config
        self.pixmaps = new_pixmaps
        self.axis_names = new_axis_names
        self.axis_values = new_axis_values
        self.n_axes = len(new_axis_names)
        self.pos = new_pos
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._locked_h = new_axis_names.index(old_locked_h_name) if old_locked_h_name in new_axis_names else None
        self._locked_v = new_axis_names.index(old_locked_v_name) if old_locked_v_name in new_axis_names else None
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

        log.info("Reload complete: %s", self.config.template)
        self._flash("reloaded")
        self._refresh()
        self.config_changed.emit()

    # ── in-place config edits ─────────────────────────────────────────────────

    def _apply_axes_change(self, new_config, new_pixmaps: dict):
        """Apply a modified config+pixmaps in-place, preserving position by value name."""
        old_names = self.axis_names
        new_axis_names = list(new_config.axes.keys())
        new_axis_values = list(new_config.axes.values())
        new_pos = []
        for name, vals in zip(new_axis_names, new_axis_values):
            if name in old_names:
                old_i = old_names.index(name)
                old_val = self.axis_values[old_i][self.pos[old_i]]
                new_pos.append(vals.index(old_val) if old_val in vals else 0)
            else:
                new_pos.append(0)
        old_h_name = old_names[self._locked_h] if self._locked_h is not None and self._locked_h < len(old_names) else None
        old_v_name = old_names[self._locked_v] if self._locked_v is not None and self._locked_v < len(old_names) else None
        if self._grid_axis is not None:
            self._exit_grid()
        self.config = new_config
        self.pixmaps = new_pixmaps
        self.axis_names = new_axis_names
        self.axis_values = new_axis_values
        self.n_axes = len(new_axis_names)
        self.pos = new_pos
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._locked_h = new_axis_names.index(old_h_name) if old_h_name in new_axis_names else None
        self._locked_v = new_axis_names.index(old_v_name) if old_v_name in new_axis_names else None
        self._sel = None
        self._rebuild_key_to_axis()
        if self._watching and self.config.remote is None:
            if self._watcher is not None:
                self._watcher.fileChanged.disconnect(self._on_file_changed)
                self._watcher.deleteLater()
            self._path_to_key = self._build_path_to_key()
            self._watcher = QFileSystemWatcher(list(self._path_to_key.keys()), self)
            self._watcher.fileChanged.connect(self._on_file_changed)
        self._refresh()

    def _do_remove_axis(self, axis_idx: int):
        j = axis_idx
        v = self.pos[j]
        removed_name = self.axis_names[j]
        new_pixmaps = {
            key[:j] + key[j + 1:]: pm
            for key, pm in self.pixmaps.items()
            if key[j] == v
        }
        new_axes = {
            name: vals
            for i, (name, vals) in enumerate(zip(self.axis_names, self.axis_values))
            if i != j
        }
        from .config import Config
        new_config = Config(
            template=self.config.template,
            axes=new_axes,
            keys={k: n for k, n in self.config.keys.items() if n != removed_name},
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._apply_axes_change(new_config, new_pixmaps)
        self._flash(f"removed axis {removed_name!r}")

    def _do_remove_value(self, axis_idx: int, val_idx: int):
        j, v = axis_idx, val_idx
        axis_name = self.axis_names[j]
        removed_val = self.axis_values[j][v]
        new_pixmaps = {}
        for key, pm in self.pixmaps.items():
            if key[j] == v:
                continue
            new_j = key[j] - 1 if key[j] > v else key[j]
            new_pixmaps[key[:j] + (new_j,) + key[j + 1:]] = pm
        new_axes = dict(self.config.axes)
        new_axes[axis_name] = self.axis_values[j][:v] + self.axis_values[j][v + 1:]
        from .config import Config
        new_config = Config(
            template=self.config.template,
            axes=new_axes,
            keys=self.config.keys,
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._apply_axes_change(new_config, new_pixmaps)
        self._flash(f"removed {axis_name}={removed_val}")

    def _do_change_key(self, axis_name: str, letter: str | None):
        new_keys = {k: n for k, n in self.config.keys.items()
                    if n != axis_name and k != letter}
        if letter is not None:
            new_keys[letter] = axis_name
        from .config import Config
        self.config = Config(
            template=self.config.template,
            axes=self.config.axes,
            keys=new_keys,
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._rebuild_key_to_axis()
        self.state_changed.emit()
        self._flash(f"{letter} → {axis_name}" if letter else f"removed key for {axis_name!r}")

    # ── pattern change ────────────────────────────────────────────────────────

    def _on_pattern_progress(self, value: int, total: int, label: str):
        dlg = self._pattern_dlg
        if dlg is None:
            return
        if label:
            dlg.setLabelText(label)
        if dlg.maximum() != total:
            dlg.setMaximum(total)
        dlg.setValue(value)

    def _complete_path(self):
        """Tab-complete the path portion of a free-text command argument.

        Placeholders count as wildcards, so a template can be completed from
        the top down — `plots/{sensor}/2024-0` + Tab lists what every sensor
        directory holds — instead of completing one concrete path first and
        deleting the components that should vary.
        """
        query = self._cmd["query"]
        caret = self._cmd.get("caret", len(query))
        prefix = query[:caret]
        suffix = query[caret:]

        comp = complete_placeholder_name(prefix, self.axis_names)
        if comp is None:
            from .detect import _is_remote_pattern
            if _is_remote_pattern(prefix):
                comp = self._complete_path_remote(prefix)
            else:
                comp = complete_path(prefix, local_listdir())
        if comp is None:
            return

        self._tab_matches = comp.matches
        self._cmd["query"] = prefix + comp.append + suffix
        self._cmd["caret"] = len(prefix) + len(comp.append)
        self.state_changed.emit()

    def _complete_path_remote(self, prefix: str):
        """Complete a `host:/path` prefix over the session's SFTP connection."""
        if self.config.remote is None or self._remote_conn[1] is None:
            return None
        from .detect import _parse_remote_pattern
        try:
            remote_cfg, remote_path = _parse_remote_pattern(prefix)
        except Exception:
            return None
        # Only the host we are already connected to can be listed
        if remote_cfg.host != self.config.remote.host:
            return None
        return complete_path(remote_path, sftp_listdir(self._remote_conn[1]))

    def _pattern_str(self) -> str:
        """Reconstruct the full pattern string for the current config."""
        if self.config.remote is not None:
            rc = self.config.remote
            prefix = f"{rc.user}@{rc.host}" if rc.user else rc.host
            return f"{prefix}:{self.config.template}"
        return self.config.template

    def _do_pattern(self, raw: str):
        """Change the template/source entirely without restarting juxt."""
        if self._reload_in_progress:
            self._flash("reload already in progress")
            return
        raw = normalize_template(raw)  # anonymous {} → {axis_1}, {axis_2}, …
        self._reload_in_progress = True
        if self._watching and self.config.remote is not None:
            self._poll_timer.stop()

        get_pw = self._get_password

        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog("Detecting axes…", "Cancel", 0, 0, self.window())
        dlg.setWindowTitle("juxt")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        self._pattern_dlg = dlg

        cancel_event = threading.Event()
        dlg.canceled.connect(cancel_event.set)

        def _worker():
            def _check():
                if cancel_event.is_set():
                    raise _Cancelled()

            try:
                from pathlib import Path, PurePosixPath
                from itertools import product as _product
                from .detect import (
                    _is_remote_pattern, _parse_remote_pattern,
                    _axes_from_sftp_template, _axes_from_local_template,
                    detect_config,
                )
                from .config import Config, _auto_keys, load_config

                new_conn: list = [None, None]
                new_tmpdir: str | None = None
                new_mtimes: dict = {}

                if _is_remote_pattern(raw):
                    remote_cfg, remote_tmpl = _parse_remote_pattern(raw)
                    if '{' not in remote_tmpl:
                        raise ValueError(
                            "remote directory detection not supported via :pattern; "
                            "use a template with {placeholders}"
                        )
                    self._pattern_progress.emit(0, 0, "Connecting…")
                    _check()
                    from .loader import _connect_sftp
                    new_conn[0], new_conn[1] = _connect_sftp(remote_cfg, get_pw)
                    self._pattern_progress.emit(0, 0, "Detecting axes…")
                    _check()
                    new_axes = _axes_from_sftp_template(remote_tmpl, new_conn[1])
                    if not new_axes:
                        raise ValueError(f"no images found for pattern {remote_tmpl!r}")
                    n = 1
                    for vs in new_axes.values():
                        n *= len(vs)
                    new_config = Config(
                        template=remote_tmpl, axes=new_axes,
                        keys=_auto_keys(new_axes), remote=remote_cfg,
                    )
                    import tempfile
                    new_tmpdir = tempfile.mkdtemp(prefix="juxt_")
                    self._pattern_progress.emit(0, n, f"Downloading {n} image{'s' if n != 1 else ''}…")

                    def _dl_progress(i, _n):
                        _check()
                        self._pattern_progress.emit(i + 1, n, "")

                    from .loader import poll_remote_with_sftp
                    poll_remote_with_sftp(
                        remote_tmpl, new_axes, new_tmpdir, new_conn[1], new_mtimes,
                        on_progress=_dl_progress,
                    )
                    axis_names = list(new_axes.keys())
                    axis_values = list(new_axes.values())
                    key_to_path = {}
                    for combo in _product(*axis_values):
                        mapping = dict(zip(axis_names, combo))
                        rpath = remote_tmpl.format(**mapping)
                        lpath = str(Path(new_tmpdir) / PurePosixPath(rpath.lstrip("/")))
                        key_to_path[tuple(vals.index(v) for vals, v in zip(axis_values, combo))] = lpath

                elif Path(raw).is_dir():
                    new_config, _ = detect_config(Path(raw), None, None)
                    if not new_config.axes:
                        raise ValueError(f"no images found in directory {raw!r}")
                    axis_names = list(new_config.axes.keys())
                    axis_values = list(new_config.axes.values())
                    n = 1
                    for vs in axis_values:
                        n *= len(vs)
                    _check()
                    self._pattern_progress.emit(0, n, f"Loading {n} image{'s' if n != 1 else ''}…")
                    key_to_path = {
                        tuple(vals.index(v) for vals, v in zip(axis_values, combo)):
                        new_config.template.format(**dict(zip(axis_names, combo)))
                        for combo in _product(*axis_values)
                    }

                elif raw.lower().endswith((".yaml", ".yml")):
                    new_config = load_config(raw)
                    if not new_config.axes:
                        raise ValueError(f"no axes defined in config {raw!r}")
                    axis_names = list(new_config.axes.keys())
                    axis_values = list(new_config.axes.values())
                    n = 1
                    for vs in axis_values:
                        n *= len(vs)
                    _check()
                    self._pattern_progress.emit(0, n, f"Loading {n} image{'s' if n != 1 else ''}…")
                    key_to_path = {
                        tuple(vals.index(v) for vals, v in zip(axis_values, combo)):
                        new_config.template.format(**dict(zip(axis_names, combo)))
                        for combo in _product(*axis_values)
                    }

                elif '{' in raw:
                    new_axes = _axes_from_local_template(raw)
                    if not new_axes:
                        raise ValueError(f"no images found for pattern {raw!r}")
                    new_config = Config(template=raw, axes=new_axes, keys=_auto_keys(new_axes))
                    axis_names = list(new_axes.keys())
                    axis_values = list(new_axes.values())
                    n = 1
                    for vs in axis_values:
                        n *= len(vs)
                    _check()
                    self._pattern_progress.emit(0, n, f"Loading {n} image{'s' if n != 1 else ''}…")
                    key_to_path = {
                        tuple(vals.index(v) for vals, v in zip(axis_values, combo)):
                        raw.format(**dict(zip(axis_names, combo)))
                        for combo in _product(*axis_values)
                    }

                else:
                    raise ValueError(
                        f"{raw!r} is not a directory, YAML config, "
                        "template pattern, or remote path"
                    )

                self._pattern_result.emit({
                    "config": new_config,
                    "key_to_path": key_to_path,
                    "conn": new_conn,
                    "tmpdir": new_tmpdir,
                    "mtimes": new_mtimes,
                    "n_images": n,
                })
            except _Cancelled:
                self._pattern_result.emit(None)
            except Exception as e:
                self._pattern_result.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_pattern(self, result: object):
        self._reload_in_progress = False

        def _close_dlg():
            if self._pattern_dlg is not None:
                self._pattern_dlg.close()
                self._pattern_dlg = None

        if result is None:  # cancelled by user
            _close_dlg()
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        if isinstance(result, Exception):
            log.warning("Pattern change failed: %s", result)
            _close_dlg()
            self._flash(f"pattern failed: {result}")
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        payload = result
        new_config = payload["config"]
        key_to_path = payload["key_to_path"]
        new_conn: list = payload["conn"]
        new_tmpdir: str | None = payload["tmpdir"]
        new_mtimes: dict = payload["mtimes"]
        n_images: int = payload.get("n_images", len(key_to_path))

        from .loader import _error_pixmap
        new_pixmaps: dict[tuple, QPixmap] = {}
        for i, (key, local_path) in enumerate(key_to_path.items()):
            if self._pattern_dlg is not None and self._pattern_dlg.wasCanceled():
                _close_dlg()
                if self._watching and self.config.remote is not None:
                    self._poll_timer.start(self._poll_interval * 1000)
                return
            self._pattern_progress.emit(i, n_images, "")
            pm = QPixmap(local_path)
            new_pixmaps[key] = pm if not pm.isNull() else _error_pixmap(local_path)

        _close_dlg()

        # Stop current watching before replacing state
        if self._watching:
            if self.config.remote is None:
                if self._watcher is not None:
                    self._watcher.fileChanged.disconnect(self._on_file_changed)
                    self._watcher.deleteLater()
                    self._watcher = None
                self._path_to_key = {}
            else:
                self._poll_timer.stop()
            self._watching = False

        # Replace remote connection/tmpdir
        try:
            if self._remote_conn[1] is not None:
                self._remote_conn[1].close()
            if self._remote_conn[0] is not None:
                self._remote_conn[0].close()
        except Exception:
            pass
        self._remote_conn = new_conn
        self._remote_tmpdir = new_tmpdir
        self._remote_mtimes = new_mtimes

        # Full viewer state reset (axes may be completely different)
        if self._grid_axis is not None:
            self._exit_grid()
        self.config = new_config
        self.pixmaps = new_pixmaps
        self.axis_names = list(new_config.axes.keys())
        self.axis_values = list(new_config.axes.values())
        self.n_axes = len(self.axis_names)
        self.pos = [0] * self.n_axes
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._locked_h = None
        self._locked_v = None
        self._rebuild_key_to_axis()

        # Restart watching with the new config
        if new_config.remote is not None and new_tmpdir is not None:
            self._start_watching(silent=True)
        elif new_config.remote is None:
            self._start_watching(silent=True)

        log.info("Pattern changed to %s", self.config.template)
        self._flash("pattern updated")
        self._refresh()
        self.config_changed.emit()

    # ── public ────────────────────────────────────────────────────────────────

    def fit_image(self):
        if self._grid_widget is not None:
            self._grid_widget.fit_image()
            return
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._fit = "image"

    def fit_height(self):
        if self._grid_widget is not None:
            self._grid_widget.fit_height()
            return
        pm = self._item.pixmap()
        if pm.isNull():
            return
        factor = self.viewport().height() / pm.height()
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "height"

    def fit_width(self):
        if self._grid_widget is not None:
            self._grid_widget.fit_width()
            return
        pm = self._item.pixmap()
        if pm.isNull():
            return
        factor = self.viewport().width() / pm.width()
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "width"

    def reset_zoom(self):
        if self._grid_widget is not None:
            self._grid_widget.reset_zoom()
            return
        self._fit = None
        self.resetTransform()

    def set_zoom(self, pct: float):
        if self._grid_widget is not None:
            self._grid_widget.set_zoom(pct)
            return
        self._fit = None
        self.resetTransform()
        self.scale(pct / 100.0, pct / 100.0)

    # ── Qt overrides ──────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        # Tab is consumed by Qt's focus traversal before keyPressEvent sees it.
        # Intercept it at the application level so it reaches _cmd_handle_key
        # regardless of which widget currently holds focus (e.g. a _CellView in grid mode).
        # Only intercept during command mode — normal Tab behaviour is preserved elsewhere.
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key_Tab
                and self._cmd is not None):
            log.debug("eventFilter: Tab intercepted (cmd=%s)", self._cmd)
            self._cmd_handle_key(event)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        mods = event.modifiers()

        # Configurable keybindings — checked first, work in every state
        bound = self._keybindings.get((k, mods.value))
        if bound is not None:
            self._dispatch_action(bound)
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
            self._toggle_fullscreen()
            return
        if k == Qt.Key_Escape:
            win = self.window()
            if win.isFullScreen():
                self._toggle_fullscreen()
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

    def mouseDoubleClickEvent(self, _event):
        self.fit_image()

    def wheelEvent(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods == Qt.ControlModifier:
            factor = 1.15 if delta > 0 else 1 / 1.15
            self.scale(factor, factor)
            self._fit = None    # reset _fit state
        elif mods == Qt.ShiftModifier:
            v = self._v_axis()
            if delta != 0 and v is not None:
                self._navigate(v, 1 if delta > 0 else -1)
        else:
            if delta != 0:
                self._navigate(self._h_axis(), 1 if delta > 0 else -1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit is not None:
            _fitter = getattr(self, f"fit_{self._fit}")
            _fitter()   # keep fit state after resize


class InfoPanel(QTextEdit):
    """Read-only sidebar listing every axis; values are clickable links."""

    value_clicked = Signal(int, int)  # axis index, value index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumWidth(180)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(
            "QTextEdit { background: #111; color: #ddd; "
            "font-family: 'Courier New', monospace; font-size: 9pt; "
            "border: none; padding: 8px; }"
        )
        # Anchors carry the value links; keep them looking like plain values.
        self.document().setDefaultStyleSheet(
            f"a {{ color: {_LINK_COLOR}; text-decoration: none; }}"
        )
        self.viewport().setMouseTracking(True)
        self._highlight = _HL_DEFAULT

    def set_highlight(self, hl: Highlight) -> None:
        """Set the format used for the current value on each axis."""
        self._highlight = hl

    def mousePressEvent(self, event):
        href = self.anchorAt(event.position().toPoint())
        super().mousePressEvent(event)
        w = self.window()
        if hasattr(w, "view"):
            w.view.setFocus()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = _parse_value_href(href)
        if target is not None:
            self.value_clicked.emit(*target)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        over_link = _parse_value_href(self.anchorAt(event.position().toPoint()))
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if over_link is not None
            else Qt.CursorShape.IBeamCursor
        )

    def refresh(self, view: "ImageView"):
        e = _html.escape
        path = view._key_to_path(view._key())
        parts = [
            f"<span style='color:#888'>path</span><br>"
            f"{e(path)}<br><br>"
        ]
        for i, (name, vals) in enumerate(zip(view.axis_names, view.axis_values)):
            cur = view.pos[i]
            val_html = "&nbsp;&nbsp;".join(
                f'<a href="{_value_href(i, j)}">'
                f'{_hl(_sp(v), self._highlight) if j == cur else _sp(v)}</a>'
                for j, v in enumerate(vals)
            )
            parts.append(
                f"<span style='color:#888'>{e(name)}</span><br>{val_html}<br><br>"
            )
        self.setHtml(
            "<html><body style='font-family:\"Courier New\",monospace; "
            "font-size:9pt; color:#ddd; background:#111; margin:8px;'>"
            + "".join(parts)
            + "</body></html>"
        )


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
        axis_h: str | None = None,
        axis_v: str | None = None,
        session_name: str | None = None,
        seek_greedy: bool = True,
        keybindings: dict[str, str] | None = None,
        highlight: Highlight | None = None,
        highlight_candidates: Highlight | None = None,
        grid: str | None = None,
        grid_values: list[str] | None = None,
        grid_layout: tuple[int, int] | None = None,
        grid_sharex: bool = True,
        grid_sharey: bool = True,
    ):
        super().__init__()
        self._session_name = session_name
        self._hl = highlight or _HL_DEFAULT
        self._hl_candidates = highlight_candidates or _HL_DEFAULT_CANDIDATES
        self.setWindowTitle(_window_title(config, session_name))
        self.view = ImageView(
            config, pixmaps, self,
            watch=watch,
            remote_tmpdir=remote_tmpdir,
            get_password=get_password,
            poll_interval=poll_interval,
            remote_mtimes=remote_mtimes,
            axis_h=axis_h,
            axis_v=axis_v,
            seek_greedy=seek_greedy,
            keybindings=keybindings,
            grid=grid,
            grid_values=grid_values,
            grid_layout=grid_layout,
            grid_sharex=grid_sharex,
            grid_sharey=grid_sharey,
        )
        self._stack = QStackedWidget()
        self._stack.addWidget(self.view)
        self.setCentralWidget(self._stack)

        bar = self.statusBar()
        bar.setStyleSheet(
            "QStatusBar { background: #1a1a1a; color: #e0e0e0; "
            "font-family: 'Courier New', monospace; font-size: 9pt; "
            "border-top: 1px solid #333; }"
            "QStatusBar::item { border: none; }"
        )
        self._status_label = _ElidingLabel()
        self._status_label.setStyleSheet(
            "color: #e0e0e0; padding: 2px 8px; "
            "font-family: 'Courier New', monospace; font-size: 9pt; "
            "white-space: pre;"
        )
        bar.addWidget(self._status_label, 1)

        self._help_label = QLabel()
        self._help_label.setStyleSheet(
            "color: #888888; padding: 2px 16px 2px 8px; "
            "font-family: 'Courier New', monospace; font-size: 9pt; "
            "white-space: pre;"
        )
        bar.addPermanentWidget(self._help_label)

        self._info_panel = InfoPanel()
        self._info_panel.set_highlight(self._hl)
        self._info_dock = QDockWidget("Info", self)
        self._info_dock.setWidget(self._info_panel)
        self._info_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self._info_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._info_dock)
        self._info_dock.hide()
        self._info_panel.value_clicked.connect(self.view.goto_value)

        self.view.state_changed.connect(self._update_status)
        self.view.state_changed.connect(self._refresh_info_panel)
        self.view.toggle_bar.connect(self._toggle_status_bar)
        self.view.toggle_info.connect(self._toggle_info_panel)
        self.view.config_changed.connect(
            lambda: self.setWindowTitle(_window_title(self.view.config, self._session_name))
        )
        self.view.grid_mode_changed.connect(self._on_grid_mode_changed)

        self._bar_auto_shown = False
        self._bar_hide_timer = QTimer(self)
        self._bar_hide_timer.setSingleShot(True)
        self._bar_hide_timer.setInterval(50)
        self._bar_hide_timer.timeout.connect(self._auto_hide_bar)

        self._spinner_frame = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(100)
        self._spinner_timer.timeout.connect(self._tick_spinner)

        self._settings_watcher = QFileSystemWatcher(self)
        if SETTINGS_PATH.exists():
            self._settings_watcher.addPath(str(SETTINGS_PATH))
        self._settings_watcher.fileChanged.connect(self._on_settings_changed)

        self._update_status()
        self._warn_keybinding_conflicts(flash=False)

    def _tick_spinner(self):
        self._spinner_frame += 1
        self._update_status()

    def _update_status(self):
        v = self.view
        sb = self.statusBar()
        interactive = v._cmd is not None or v._sel is not None
        if interactive:
            self._bar_hide_timer.stop()
            if not sb.isVisible():
                self._bar_auto_shown = True
                sb.show()
        elif self._bar_auto_shown and not self._bar_hide_timer.isActive():
            self._bar_hide_timer.start()
        self._help_label.setText("")

        if v._reload_in_progress:
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
            self._status_label.setText(
                f"{_SPINNER[self._spinner_frame % len(_SPINNER)]}  reloading…"
            )
            return

        if self._spinner_timer.isActive():
            self._spinner_timer.stop()
            self._spinner_frame = 0

        if v._flash_msg is not None:
            self._status_label.setText(v._flash_msg)
            return

        grid_str = f"  grid:{v.axis_names[v._grid_axis]}" if v._grid_axis is not None else ""
        mode_str = f"[{v.nav_mode.label}{grid_str}]{'  ●' if v._watching else ''}"

        if v._cmd is not None:
            candidates = v._cmd_candidates()
            query = v._cmd["query"]
            raw = v._cmd["cursor"]
            cursor = raw if raw < 0 else (min(raw, len(candidates) - 1) if candidates else 0)
            prompt_html = None
            if v._cmd["phase"] == "verb":
                prompt = f":{query}▌"
                hlit = candidates[min(cursor, len(candidates) - 1)] if candidates and cursor >= 0 else None
                desc = _COMMAND_HELP.get(hlit, "") if hlit else ""
            else:
                verb = v._cmd["verb"]
                if verb in _FREE_TEXT_ARGS:
                    caret = v._cmd.get("caret", len(query))
                    shown = _cmd_query_display(query, caret)
                    prompt = f":{verb} {shown}"
                    # Colour each {placeholder} so a multi-axis template stays
                    # readable while it is being typed.
                    prompt_html = _sp(f":{verb} ") + placeholder_html(shown)
                else:
                    prompt = f":{verb} {query}▌"
                desc = _COMMAND_HELP.get(v._cmd["verb"], "")
            if v._tab_matches:
                self._help_label.setText("  ".join(v._tab_matches))
            elif desc:
                self._help_label.setText(f"( {desc} )")
            if candidates:
                cand_html = _candidate_html(candidates, cursor, self._hl_candidates)
                head = prompt_html if prompt_html is not None else _sp(prompt)
                self._status_label.setText(
                    _rich(f"{head}&nbsp;&nbsp;→&nbsp;&nbsp;{cand_html}")
                )
            elif v._cmd["phase"] == "verb" and query:
                self._status_label.setText(
                    _rich(f"{_sp(prompt)}&nbsp;&nbsp;(unknown&nbsp;command)")
                )
            else:
                self._status_label.setText(
                    _rich(prompt_html if prompt_html is not None else _sp(prompt))
                )
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
            cand_html = (
                _candidate_html(candidates, cursor, self._hl_candidates) if candidates
                else _sp("(no match)")
            )
            self._status_label.setText(_rich(
                f"{_sp(mode_str)}&nbsp;&nbsp;|&nbsp;&nbsp;{_sp(prompt)}"
                f"&nbsp;&nbsp;→&nbsp;&nbsp;{cand_html}"
            ))
            return

        h_idx = v._h_axis()
        v_idx = v._v_axis()

        coord_parts = [
            f"{name}={v.axis_values[i][v.pos[i]]:<{max(len(val) for val in v.axis_values[i])}}"
            for i, name in enumerate(v.axis_names)
        ]
        coord_str = "  ".join(coord_parts)
        h_name = v.axis_names[h_idx]
        v_name = v.axis_names[v_idx] if v_idx is not None else "—"
        bind_str = f"→/← {h_name}  ↑/↓ {v_name}"
        sep = "  |  "
        sp = _sp
        sep_html = "&nbsp;&nbsp;|&nbsp;&nbsp;"
        active = v._active_axis
        coord_html = (
            "&nbsp;&nbsp;".join(
                _hl(sp(p), self._hl) if i == active else sp(p)
                for i, p in enumerate(coord_parts)
            ) if active is not None else None
        )
        if v.nav_mode == NavMode.SEEK:
            key_hints = "  ".join(v.axis_names)
            if coord_html is not None:
                parts_html = [sp(mode_str), coord_html, sp(bind_str)]
                if key_hints:
                    parts_html.append(sp(key_hints))
                self._status_label.setText(sep_html.join(parts_html))
            else:
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
            if unbound or coord_html is not None:
                bound_html = "&nbsp;&nbsp;".join(sp(b) for b in bound_items)
                unbound_html = "&nbsp;&nbsp;".join(
                    f'<span style="color:#cc4444">{sp(n)}</span>' for n in unbound
                )
                key_hints_html = "&nbsp;&nbsp;".join(filter(None, [bound_html, unbound_html]))
                c = coord_html if coord_html is not None else sp(coord_str)
                parts_html = sep_html.join([sp(mode_str), c, sp(bind_str)])
                label = parts_html + sep_html + key_hints_html if key_hints_html else parts_html
                self._status_label.setText(label)
            else:
                key_hints = "  ".join(bound_items)
                parts = [mode_str, coord_str, bind_str]
                if key_hints:
                    parts.append(key_hints)
                self._status_label.setText(sep.join(parts))

    def showEvent(self, event):
        super().showEvent(event)
        if not self.view._initial_fit_done:
            self.view._initial_fit_done = True
            QTimer.singleShot(0, self.view.fit_image)

    def _on_grid_mode_changed(self, grid_widget):
        if grid_widget is not None:
            self._stack.addWidget(grid_widget)
            self._stack.setCurrentWidget(grid_widget)
            grid_widget.setFocus()
            QTimer.singleShot(0, grid_widget.fit_image)
        else:
            old = self._stack.widget(1) if self._stack.count() > 1 else None
            self._stack.setCurrentWidget(self.view)
            if old is not None:
                self._stack.removeWidget(old)
                old.deleteLater()
            self.view.setFocus()
            QTimer.singleShot(0, self.view.fit_image)

    def _toggle_status_bar(self):
        sb = self.statusBar()
        self._bar_auto_shown = False
        self._bar_hide_timer.stop()
        sb.setVisible(not sb.isVisible())

    def _auto_hide_bar(self):
        self._bar_auto_shown = False
        self.statusBar().hide()

    def _toggle_info_panel(self):
        if self._info_dock.isVisible():
            self._info_dock.hide()
        else:
            self._info_panel.refresh(self.view)
            self._info_dock.show()

    def _refresh_info_panel(self):
        if self._info_dock.isVisible():
            self._info_panel.refresh(self.view)

    def _warn_keybinding_conflicts(self, *, flash: bool = True) -> bool:
        """Log (and optionally flash) warnings for bindings that shadow navigation."""
        axis_letters = set(self.view.key_to_axis.keys())
        conflicts = _keybinding_conflicts(self.view._keybindings, axis_letters)
        for msg in conflicts:
            log.warning("Keybinding conflict: %s", msg)
        if flash and conflicts:
            if len(conflicts) == 1:
                self.view._flash(f"keybinding conflict: {conflicts[0]}", ms=4000)
            else:
                self.view._flash(
                    f"{len(conflicts)} keybinding conflict(s) — see log", ms=4000
                )
        return bool(conflicts)

    def _on_settings_changed(self, path: str):
        # Some editors save via a temp-rename; re-add so we keep getting events.
        if not self._settings_watcher.files():
            self._settings_watcher.addPath(str(SETTINGS_PATH))
        settings = load_settings(SETTINGS_PATH)
        self.view.apply_settings(settings)
        self._hl = settings.highlight
        self._hl_candidates = settings.highlight_candidates
        self._info_panel.set_highlight(self._hl)
        self._refresh_info_panel()
        self._update_status()
        reset_placeholder_shapes()  # {date} & friends may have been re-configured
        import juxt.detect as _detect
        _detect._MAX_VALS = settings.max_vals
        _detect._MAX_VALS_DISPLAY = settings.max_vals_display
        log.info("Settings reloaded from %s", path)
        had_conflicts = self._warn_keybinding_conflicts()
        if not had_conflicts:
            self.view._flash("settings reloaded")
