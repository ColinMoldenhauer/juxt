from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QLabel, QMainWindow

from .config import Config


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

        # Current position (indices) and previous for spacebar toggle
        self.pos: list[int] = [0] * self.n_axes
        self.prev: list[int] | None = None

        # Focus stack: axis indices, most recently focused first.
        # pos[focus_stack[0]] is cycled by →/←; pos[focus_stack[1]] by ↑/↓.
        self.focus_stack: list[int] = list(range(self.n_axes))

        # letter → axis index
        self.key_to_axis: dict[str, int] = {
            ch: self.axis_names.index(name)
            for ch, name in config.keys.items()
            if name in self.axis_names
        }

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
        self.viewport().update()
        self.state_changed.emit()

    # ── public ────────────────────────────────────────────────────────────────

    def fit_image(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def reset_zoom(self):
        self.resetTransform()

    # ── Qt overrides ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()

        if k == Qt.Key_Right:
            self._navigate(self._h_axis(), 1)
        elif k == Qt.Key_Left:
            self._navigate(self._h_axis(), -1)
        elif k == Qt.Key_Down:
            v = self._v_axis()
            if v is not None:
                self._navigate(v, 1)
        elif k == Qt.Key_Up:
            v = self._v_axis()
            if v is not None:
                self._navigate(v, -1)
        elif k == Qt.Key_Space:
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
                self._refresh()
        elif k == Qt.Key_Home:
            self.prev = list(self.pos)
            self.pos[self._h_axis()] = 0
            self._refresh()
        elif k == Qt.Key_End:
            h = self._h_axis()
            self.prev = list(self.pos)
            self.pos[h] = len(self.axis_values[h]) - 1
            self._refresh()
        elif Qt.Key_1 <= k <= Qt.Key_9:
            idx = k - Qt.Key_1  # 0-based
            h = self._h_axis()
            if idx < len(self.axis_values[h]):
                self.prev = list(self.pos)
                self.pos[h] = idx
                self._refresh()
        elif k == Qt.Key_F:
            self.fit_image()
        elif k == Qt.Key_0:
            self.reset_zoom()
        elif k == Qt.Key_H and event.modifiers() & Qt.ControlModifier:
            self.toggle_bar.emit()
        elif k in (Qt.Key_Return, Qt.Key_Enter):
            win = self.window()
            if win.isFullScreen():
                win.showMaximized()
            else:
                win.showFullScreen()
        elif k == Qt.Key_Escape:
            win = self.window()
            if win.isFullScreen():
                win.showMaximized()
        else:
            ch = event.text().lower()
            if ch in self.key_to_axis:
                self._focus(self.key_to_axis[ch])
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
        self._status_label.setStyleSheet("color: #e0e0e0; padding: 2px 8px;")
        bar.addWidget(self._status_label, 1)

        self.view.state_changed.connect(self._update_status)
        self.view.toggle_bar.connect(self._toggle_status_bar)

        self._update_status()
        QTimer.singleShot(0, self.view.fit_image)

    def _update_status(self):
        v = self.view
        h_idx = v._h_axis()
        v_idx = v._v_axis()

        coord_str = "  ".join(
            f"{name}={v.axis_values[i][v.pos[i]]}"
            for i, name in enumerate(v.axis_names)
        )
        h_name = v.axis_names[h_idx]
        v_name = v.axis_names[v_idx] if v_idx is not None else "—"
        bind_str = f"→/← {h_name}  ↑/↓ {v_name}"
        key_hints = "  ".join(
            f"[{ch}] {v.axis_names[v.key_to_axis[ch]]}"
            for ch in sorted(v.key_to_axis)
        )

        parts = [coord_str, bind_str]
        if key_hints:
            parts.append(key_hints)
        self._status_label.setText("  |  ".join(parts))

    def _toggle_status_bar(self):
        sb = self.statusBar()
        sb.setVisible(not sb.isVisible())
