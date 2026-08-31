"""Modal dialogues driven from the viewer's key bindings.

Currently only the grid builder (``Ctrl+Shift+G`` / ``:grid-dialog``), which is
a point-and-click front end for the ``:grid`` command family.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

# Matches the viewport / status-bar palette in viewer.py.
_STYLE = """
QDialog { background: #191919; }
QLabel, QCheckBox, QRadioButton { color: #e0e0e0;
    font-family: 'Courier New', monospace; font-size: 9pt; }
QLabel#hint { color: #888888; }
QComboBox, QSpinBox, QListWidget {
    background: #111; color: #ddd; border: 1px solid #333;
    selection-background-color: #2a4a6a; selection-color: #fff;
    font-family: 'Courier New', monospace; font-size: 9pt; padding: 2px; }
QComboBox QAbstractItemView { background: #111; color: #ddd;
    border: 1px solid #333; }
QSpinBox:disabled { color: #666; border-color: #2a2a2a; }
QCheckBox:disabled { color: #666; }
/* Give the step buttons a visible box of their own.  The arrows themselves are
   painted by _ArrowSpinBox — see the note there. */
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border; width: 15px;
    background: #222; border-left: 1px solid #333; }
QSpinBox::up-button   { subcontrol-position: top right;
    border-bottom: 1px solid #333; }
QSpinBox::down-button { subcontrol-position: bottom right; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #2c2c2c; }
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed { background: #2a4a6a; }
QPushButton { background: #222; color: #e0e0e0; border: 1px solid #444;
    padding: 4px 12px;
    font-family: 'Courier New', monospace; font-size: 9pt; }
QPushButton:hover { background: #2c2c2c; }
QPushButton:default { border: 1px solid #6af; }
QPushButton:disabled { color: #666; border-color: #333; }
QCheckBox::indicator, QListWidget::indicator {
    width: 12px; height: 12px;
    background: #111; border: 1px solid #555; }
QCheckBox::indicator:checked, QListWidget::indicator:checked {
    background: #6af; border: 1px solid #6af; }
QCheckBox::indicator:disabled, QListWidget::indicator:disabled {
    border-color: #333; }
"""

# Longer value lists scroll rather than growing the dialogue past this.
_MAX_LIST_ROWS = 12


class _ArrowSpinBox(QSpinBox):
    """A spin box that paints its own step arrows.

    Giving a spin box any stylesheet switches it to ``QStyleSheetStyle``, which
    draws the native arrows so dark they vanish against the box — and once
    ``::up-button`` carries a rule of its own, drops them entirely.  Qt only
    accepts an ``image:`` URL as a replacement, so paint the triangles here
    rather than shipping a pair of icon assets for two glyphs.
    """

    _ARROW_W = 7      # triangle base, px
    _ARROW_H = 4      # triangle height, px

    def _sub_rect(self, which: QStyle.SubControl):
        opt = QStyleOptionSpinBox()
        self.initStyleOption(opt)
        return self.style().subControlRect(
            QStyle.ComplexControl.CC_SpinBox, opt, which, self
        )

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        hw, hh = self._ARROW_W / 2, self._ARROW_H / 2
        for which, up in (
            (QStyle.SubControl.SC_SpinBoxUp, True),
            (QStyle.SubControl.SC_SpinBoxDown, False),
        ):
            r = self._sub_rect(which)
            if r.isEmpty():
                continue
            # Dim the arrow when that direction has nowhere left to go.
            live = self.isEnabled() and (
                self.value() < self.maximum() if up else self.value() > self.minimum()
            )
            p.setBrush(QColor("#cccccc" if live else "#555555"))
            # Qt reports the button rect a couple of px wider than the frame;
            # keep the glyph inside the widget either way.
            cx = min(r.center().x() + 0.5, self.width() - hw - 3)
            cy = r.center().y() + 0.5
            tip, base = (cy - hh, cy + hh) if up else (cy + hh, cy - hh)
            p.drawPolygon(QPolygonF([
                QPointF(cx - hw, base), QPointF(cx + hw, base), QPointF(cx, tip),
            ]))
        p.end()


@dataclass
class GridSpec:
    """The grid configuration chosen in :class:`GridDialog`."""

    axis: int
    values: list[int] | None          # None = every value on the axis
    layout: tuple[int, int] | None    # None = fit to the viewport aspect ratio
    sharex: bool
    sharey: bool


def auto_layout(n: int, img_w: int, img_h: int, vp_w: int, vp_h: int) -> tuple[int, int]:
    """Rows/cols the viewer would pick for *n* cells — mirrors ``_enter_grid``."""
    vp_ar = vp_w / max(1, vp_h)
    cols, rows = min(
        ((c, math.ceil(n / c)) for c in range(1, n + 1)),
        key=lambda cr: abs((cr[0] * img_w) / max(1, cr[1] * img_h) - vp_ar),
    )
    return rows, cols


class GridDialog(QDialog):
    """Pick an axis, a value subset and a layout for grid view.

    Read :attr:`spec` after ``exec()`` returns :data:`Accepted`; a return of
    :attr:`EXIT` means the user asked to leave grid view.
    """

    EXIT = 2  # third exec() result, alongside Accepted / Rejected

    def __init__(
        self,
        axis_names: list[str],
        axis_values: list[list[str]],
        *,
        axis: int = 0,
        values: list[int] | None = None,
        layout: tuple[int, int] | None = None,
        sharex: bool = True,
        sharey: bool = True,
        in_grid: bool = False,
        auto_layout_for=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.axis_names = axis_names
        self.axis_values = axis_values
        self.spec: GridSpec | None = None
        self._auto_layout_for = auto_layout_for
        self._initial_values = values

        self.setWindowTitle("Grid view")
        self.setModal(True)
        self.setStyleSheet(_STYLE)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.axis_box = QComboBox()
        self.axis_box.addItems(axis_names)
        self.axis_box.setCurrentIndex(max(0, min(axis, len(axis_names) - 1)))
        form.addRow("Axis", self.axis_box)

        self.value_list = QListWidget()
        self.value_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        form.addRow("Values", self.value_list)

        btn_all = QPushButton("All")
        btn_none = QPushButton("None")
        btn_invert = QPushButton("Invert")
        sel_row = QHBoxLayout()
        sel_row.setContentsMargins(0, 0, 0, 0)
        for b in (btn_all, btn_none, btn_invert):
            sel_row.addWidget(b)
        sel_row.addStretch(1)
        sel_widget = QWidget()
        sel_widget.setLayout(sel_row)
        form.addRow("", sel_widget)

        self.auto_check = QCheckBox("auto (fit viewport)")
        self.rows_spin = _ArrowSpinBox()
        self.cols_spin = _ArrowSpinBox()
        for s in (self.rows_spin, self.cols_spin):
            s.setRange(1, 99)
        layout_row = QHBoxLayout()
        layout_row.setContentsMargins(0, 0, 0, 0)
        layout_row.addWidget(self.auto_check)
        layout_row.addWidget(self.rows_spin)
        layout_row.addWidget(QLabel("×"))
        layout_row.addWidget(self.cols_spin)
        layout_row.addStretch(1)
        layout_widget = QWidget()
        layout_widget.setLayout(layout_row)
        form.addRow("Layout", layout_widget)

        self.sharex_check = QCheckBox("horizontal")
        self.sharey_check = QCheckBox("vertical")
        self.sharex_check.setChecked(sharex)
        self.sharey_check.setChecked(sharey)
        sync_row = QHBoxLayout()
        sync_row.setContentsMargins(0, 0, 0, 0)
        sync_row.addWidget(self.sharex_check)
        sync_row.addWidget(self.sharey_check)
        sync_row.addStretch(1)
        sync_widget = QWidget()
        sync_widget.setLayout(sync_row)
        form.addRow("Sync pan/zoom", sync_widget)

        self.hint = QLabel()
        self.hint.setObjectName("hint")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Show grid")
        if in_grid:
            exit_btn = self.buttons.addButton(
                "Exit grid", QDialogButtonBox.ButtonRole.DestructiveRole
            )
            exit_btn.clicked.connect(lambda: self.done(self.EXIT))

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addWidget(self.hint)
        root.addWidget(self.buttons)

        self.axis_box.currentIndexChanged.connect(self._populate_values)
        self.value_list.itemChanged.connect(self._update_hint)
        self.auto_check.toggled.connect(self._on_auto_toggled)
        self.rows_spin.valueChanged.connect(self._on_spin_edited)
        self.cols_spin.valueChanged.connect(self._on_spin_edited)
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_invert.clicked.connect(self._invert)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        self._populate_values()
        self._initial_values = None  # only prefills the axis it came from
        self.auto_check.setChecked(layout is None)
        if layout is not None:
            self.rows_spin.setValue(layout[0])
            self.cols_spin.setValue(layout[1])
        self._on_auto_toggled(self.auto_check.isChecked())

    # ── value list ───────────────────────────────────────────────────────────

    def _populate_values(self):
        axis = self.axis_box.currentIndex()
        preset = self._initial_values
        self.value_list.blockSignals(True)
        self.value_list.clear()
        for i, val in enumerate(self.axis_values[axis]):
            item = QListWidgetItem(val)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = preset is None or i in preset
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.value_list.addItem(item)
        self.value_list.blockSignals(False)
        self._fit_list_height()
        self._update_hint()

    def _fit_list_height(self):
        """Grow the list with its contents, up to _MAX_LIST_ROWS rows."""
        n = self.value_list.count()
        if not n:
            return
        row_h = self.value_list.sizeHintForRow(0)
        rows = min(n, _MAX_LIST_ROWS)
        frame = 2 * self.value_list.frameWidth() + 4
        self.value_list.setFixedHeight(rows * row_h + frame)

    def _set_all(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.value_list.blockSignals(True)
        for i in range(self.value_list.count()):
            self.value_list.item(i).setCheckState(state)
        self.value_list.blockSignals(False)
        self._update_hint()

    def _invert(self):
        self.value_list.blockSignals(True)
        for i in range(self.value_list.count()):
            item = self.value_list.item(i)
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
        self.value_list.blockSignals(False)
        self._update_hint()

    def checked_indices(self) -> list[int]:
        return [
            i for i in range(self.value_list.count())
            if self.value_list.item(i).checkState() == Qt.CheckState.Checked
        ]

    # ── layout / hint ────────────────────────────────────────────────────────

    def _on_auto_toggled(self, auto: bool):
        if auto:
            self._seed_spins()
        self._update_hint()

    def _on_spin_edited(self, *_):
        """A spin box only changes under the user's hand — ``_seed_spins`` blocks
        its signals — so treat any change as the user overriding ``auto``."""
        if self.auto_check.isChecked():
            self.auto_check.setChecked(False)  # re-enters via _on_auto_toggled
            return
        self._update_hint()

    def _seed_spins(self):
        """Show the layout auto would pick, so unticking it starts from there."""
        n = len(self.checked_indices())
        if not n or self._auto_layout_for is None:
            return
        rows, cols = self._auto_layout_for(n)
        for spin, val in ((self.rows_spin, rows), (self.cols_spin, cols)):
            spin.blockSignals(True)
            spin.setValue(val)
            spin.blockSignals(False)

    def _update_hint(self, *_):
        n = len(self.checked_indices())
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if n == 0:
            self.hint.setText("select at least one value")
            ok.setEnabled(False)
            return
        ok.setEnabled(True)
        if self.auto_check.isChecked():
            if self._auto_layout_for is not None:
                self._seed_spins()
                rows, cols = self._auto_layout_for(n)
                self.hint.setText(f"{n} cells → {rows}×{cols} (auto)")
            else:
                self.hint.setText(f"{n} cells, layout fitted to the viewport")
            return
        rows, cols = self.rows_spin.value(), self.cols_spin.value()
        slots = rows * cols
        if slots < n:
            self.hint.setText(
                f"{n} cells → {rows}×{cols}, {slots} at a time — step the axis to page"
            )
        else:
            blank = slots - n
            extra = f", {blank} empty" if blank else ""
            self.hint.setText(f"{n} cells → {rows}×{cols}{extra}")

    # ── result ───────────────────────────────────────────────────────────────

    def _accept(self):
        axis = self.axis_box.currentIndex()
        checked = self.checked_indices()
        if not checked:
            return
        all_checked = len(checked) == len(self.axis_values[axis])
        layout = (
            None if self.auto_check.isChecked()
            else (self.rows_spin.value(), self.cols_spin.value())
        )
        self.spec = GridSpec(
            axis=axis,
            values=None if all_checked else checked,
            layout=layout,
            sharex=self.sharex_check.isChecked(),
            sharey=self.sharey_check.isChecked(),
        )
        log.debug("grid dialog: %s", self.spec)
        self.accept()
