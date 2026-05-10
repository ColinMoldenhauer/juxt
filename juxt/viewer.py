from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QColor, QFont, QKeyEvent, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QMainWindow

from .config import Config


class ImageView(QGraphicsView):
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

        self._overlay_on = True

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
        elif k == Qt.Key_H:
            self._overlay_on = not self._overlay_on
            self.viewport().update()
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

    def drawForeground(self, painter: QPainter, rect):
        """Draw status overlay in viewport coordinates (immune to pan/zoom)."""
        if not self._overlay_on:
            return

        painter.save()
        painter.resetTransform()
        painter.setClipping(False)

        font = QFont("Courier New" if __import__("sys").platform == "win32" else "monospace", 10)
        painter.setFont(font)
        fm = painter.fontMetrics()

        h_idx = self._h_axis()
        v_idx = self._v_axis()

        # Line 1: current coordinate values
        coord_str = "  ".join(
            f"{name}={self.axis_values[i][self.pos[i]]}"
            for i, name in enumerate(self.axis_names)
        )

        # Line 2: arrow bindings
        h_name = self.axis_names[h_idx]
        v_name = self.axis_names[v_idx] if v_idx is not None else "—"
        bind_str = f"→/← {h_name}    ↑/↓ {v_name}"

        # Line 3: key hints
        key_hints = "  ".join(
            f"[{ch}] {self.axis_names[self.key_to_axis[ch]]}"
            for ch in sorted(self.key_to_axis)
        )

        lines = [coord_str, bind_str]
        if key_hints:
            lines.append(key_hints)

        margin = 8
        pad = 6
        line_h = fm.height() + 2
        box_w = max(fm.horizontalAdvance(l) for l in lines) + pad * 2
        box_h = line_h * len(lines) + pad * 2 - 2

        painter.fillRect(margin, margin, box_w, box_h, QColor(0, 0, 0, 170))
        painter.setPen(QColor(255, 255, 255))
        for j, line in enumerate(lines):
            painter.drawText(margin + pad, margin + pad + fm.ascent() + j * line_h, line)

        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, config: Config, pixmaps: dict):
        super().__init__()
        self.setWindowTitle("juxt")
        self.view = ImageView(config, pixmaps, self)
        self.setCentralWidget(self.view)
        self.resize(1280, 860)
        QTimer.singleShot(0, self.view.fit_image)
