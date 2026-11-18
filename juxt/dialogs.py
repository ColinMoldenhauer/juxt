"""Modal dialogues driven from the viewer's key bindings.

The grid builder (``Ctrl+Shift+G`` / ``:grid-dialog``), a point-and-click front
end for the ``:grid`` command family, and the value picker
(``:subset-interactive``, ``--subset-interactive``, the ``[+]`` next to an axis
in the info sidebar), which adds and removes the values of one or all axes.
"""
from __future__ import annotations

import logging
import math
import string
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme as _theme

log = logging.getLogger(__name__)

# Filled from the active theme (see juxt/theme.py) when a dialogue opens.
_STYLE = string.Template("""
QDialog { background: $surface; }
QLabel, QCheckBox, QRadioButton { color: $text;
    font-family: 'Courier New', monospace; font-size: 9pt; }
QLabel#hint { color: $muted; }
QComboBox, QSpinBox, QListWidget, QLineEdit {
    background: $panel; color: $text; border: 1px solid $border;
    selection-background-color: $selection; selection-color: $selection_text;
    font-family: 'Courier New', monospace; font-size: 9pt; padding: 2px; }
QComboBox QAbstractItemView { background: $panel; color: $text;
    border: 1px solid $border; }
QSpinBox:disabled { color: $disabled; border-color: $border; }
QCheckBox:disabled { color: $disabled; }
/* Give the step buttons a visible box of their own.  The arrows themselves are
   painted by _ArrowSpinBox — see the note there. */
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: border; width: 15px;
    background: $control; border-left: 1px solid $border; }
QSpinBox::up-button   { subcontrol-position: top right;
    border-bottom: 1px solid $border; }
QSpinBox::down-button { subcontrol-position: bottom right; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: $hover; }
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed { background: $selection; }
QPushButton { background: $control; color: $text; border: 1px solid $border_strong;
    padding: 4px 12px;
    font-family: 'Courier New', monospace; font-size: 9pt; }
QPushButton:hover { background: $hover; }
QPushButton:default { border: 1px solid $accent; }
QPushButton:disabled { color: $disabled; border-color: $border; }
QCheckBox::indicator, QListWidget::indicator {
    width: 12px; height: 12px;
    background: $panel; border: 1px solid $border_strong; }
QCheckBox::indicator:checked, QListWidget::indicator:checked {
    background: $accent; border: 1px solid $accent; }
QCheckBox::indicator:disabled, QListWidget::indicator:disabled {
    border-color: $border; }
""")

# Longer value lists scroll rather than growing the dialogue past this.
_MAX_LIST_ROWS = 12
_MAX_VALUE_ROWS = 20   # the value picker has room to spare
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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
            t = _theme.current()
            p.setBrush(QColor(t.arrow if live else t.disabled))
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
        self.setStyleSheet(_STYLE.substitute(vars(_theme.current())))

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
                f"{n} values → {rows}×{cols}, {slots} panes — "
                f"the focused pane cycles the other {n - slots}"
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


def merge_values(current: list[str], found: list[str]) -> list[str]:
    """The values of *current* and *found* in the order the value picker lists them.

    The new values (in *found* only) slot into place when *current* is sorted,
    numerically if every value is a number and as text otherwise, so a plain
    discovered axis stays sorted. An axis the user reordered keeps its order,
    and the new values follow it.
    """
    seen = set(current)
    new = [v for v in found if v not in seen]
    union = list(current) + new
    if not new:
        return union
    try:
        nums = {v: float(v) for v in union}
        key = nums.__getitem__ if all(math.isfinite(n) for n in nums.values()) else None
    except ValueError:
        key = None
    if key is None:
        key = str
    if list(current) == sorted(current, key=key):
        return sorted(union, key=key)
    return union


class _AxisPanel(QWidget):
    """One axis of the value picker: a filter, the checklist, All / None,
    and a line for the pending change or the search spinner."""

    changed = Signal()

    def __init__(self, axis_name: str, current: list[str], *, title: bool,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.axis_name = axis_name
        self._current = list(current)
        self._found: set[str] | None = None   # None until the listing is in
        self._error: str | None = None
        self._new: list[str] = []
        self._spin_frame = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._spin)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.installEventFilter(self)

        self.value_list = QListWidget()
        # Click, Shift+click, Ctrl+click and dragging over row text select
        # rows; Space or a double-click then toggles the whole selection.
        self.value_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.value_list.viewport().installEventFilter(self)
        self.value_list.installEventFilter(self)
        # While a press on a box (or a double-click) is dragged across rows:
        # the state each row passed over is given.
        self._paint: Qt.CheckState | None = None
        # The selection as it was before the latest press on row text: the
        # first click of a double-click narrows it to one row on release.
        self._sel_before: list[QListWidgetItem] = []

        btn_all = QPushButton("All")
        btn_none = QPushButton("None")
        sel_row = QHBoxLayout()
        sel_row.setContentsMargins(0, 0, 0, 0)
        for b in (btn_all, btn_none):
            sel_row.addWidget(b)
        sel_row.addStretch(1)

        self.hint = QLabel()
        self.hint.setObjectName("hint")
        # Always one line high, empty or not, so side-by-side columns keep
        # their lists and buttons level.
        self.hint.setFixedHeight(self.hint.fontMetrics().height() + 2)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        if title:
            head = QLabel(axis_name)
            head.setStyleSheet(f"color: {_theme.current().header};")
            col.addWidget(head)
        col.addWidget(self.filter_edit)
        col.addWidget(self.value_list, 1)   # the list takes any spare height
        col.addLayout(sel_row)
        col.addWidget(self.hint)

        self._populate({})
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.value_list.itemChanged.connect(self._on_item_changed)
        btn_all.clicked.connect(lambda: self._set_visible(True))
        btn_none.clicked.connect(lambda: self._set_visible(False))

    # ── listing ──────────────────────────────────────────────────────────────

    @property
    def loading(self) -> bool:
        """Whether the listing of the values on disk is still outstanding."""
        return self._found is None and self._error is None

    def start_spinner(self) -> None:
        self._spin_timer.start()
        self._update_hint()

    def set_found(self, found: list[str], error: str | None = None) -> None:
        """Take in the values found on disk (or why listing them failed).

        The new values join the list in place; every tick the user already
        set is kept, and so is the filter.
        """
        ticks = {item.data(Qt.ItemDataRole.UserRole):
                 item.checkState() == Qt.CheckState.Checked for item in self._items()}
        self._spin_timer.stop()
        self._error = error
        self._found = None if error is not None else set(found)
        current = set(self._current)
        self._new = [] if error is not None else [v for v in found if v not in current]
        self.value_list.blockSignals(True)
        self.value_list.clear()
        self._populate(ticks)
        self.value_list.blockSignals(False)
        self._apply_filter(self.filter_edit.text())
        self._update_hint()

    def _spin(self):
        self._spin_frame += 1
        self._update_hint()

    # ── list ─────────────────────────────────────────────────────────────────

    def _populate(self, ticks: dict[str, bool]):
        """Fill the list; *ticks* keeps the user's choice per value, the rest
        start ticked exactly when the axis has them."""
        t = _theme.current()
        current = set(self._current)
        # Without a listing to compare against, nothing can be called missing.
        listed = self._found is not None
        for val in merge_values(self._current, self._new):
            text = val
            item = QListWidgetItem()
            if val not in current:
                item.setForeground(QColor(t.accent))   # new: the colour says so
            elif listed and val not in self._found:
                text += "  no files"
                item.setForeground(QColor(t.muted))
            item.setText(text)
            item.setData(Qt.ItemDataRole.UserRole, val)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = ticks.get(val, val in current)
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.value_list.addItem(item)

    def row_height(self) -> int:
        self.value_list.ensurePolished()
        n = self.value_list.count()
        return max(self.value_list.sizeHintForRow(0) if n else 0,
                   self.value_list.fontMetrics().height() + 6)

    def content_width(self) -> int:
        """Width the column wants: its longest value, with room for the box."""
        self.value_list.ensurePolished()
        return max(180, self.value_list.sizeHintForColumn(0) + 50)

    def _items(self):
        return [self.value_list.item(i) for i in range(self.value_list.count())]

    def _apply_filter(self, text: str):
        q = text.strip().lower()
        for item in self._items():
            val = item.data(Qt.ItemDataRole.UserRole)
            item.setHidden(bool(q) and q not in val.lower())

    def _set_visible(self, checked: bool):
        """Tick or untick every row the filter shows."""
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.value_list.blockSignals(True)
        for item in self._items():
            if not item.isHidden():
                item.setCheckState(state)
        self.value_list.blockSignals(False)
        self._on_item_changed()

    def _on_item_changed(self, *_):
        self._update_hint()
        self.changed.emit()

    def _on_indicator(self, item: QListWidgetItem, x: int) -> bool:
        """Whether *x* (viewport coordinates) falls on *item*'s check box."""
        style = self.value_list.style()
        zone = (style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth)
                + 2 * style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin) + 4)
        return x - self.value_list.visualItemRect(item).left() < zone

    def _selected(self) -> list[QListWidgetItem]:
        """The selected rows the filter shows (a Shift+click range may span hidden ones)."""
        return [it for it in self.value_list.selectedItems() if not it.isHidden()]

    def _set_states(self, items: list[QListWidgetItem], state: Qt.CheckState):
        """Give every row in *items* *state*, with one hint update for all."""
        self.value_list.blockSignals(True)
        for it in items:
            it.setCheckState(state)
        self.value_list.blockSignals(False)
        self._on_item_changed()

    @staticmethod
    def _flipped(item: QListWidgetItem) -> Qt.CheckState:
        return (Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked)

    def _toggle_with_selection(self, item: QListWidgetItem) -> Qt.CheckState:
        """Flip *item*, and with it the rest of the selection it was part of
        before this double-click began; returns the state given."""
        state = self._flipped(item)
        group = self._sel_before if item in self._sel_before else [item]
        self._set_states(group, state)
        if len(group) > 1:   # put back the selection the first click narrowed
            for it in group:
                it.setSelected(True)
        return state

    def _viewport_event(self, event) -> bool:
        """Mouse handling on the list; True when the event is used up."""
        t = event.type()
        left = getattr(event, "button", lambda: None)() == Qt.MouseButton.LeftButton
        if t in (event.Type.MouseButtonPress, event.Type.MouseButtonDblClick) and left:
            pos = event.position().toPoint()
            item = self.value_list.itemAt(pos)
            if item is None:
                return False
            on_box = self._on_indicator(item, pos.x())
            if on_box:
                # Every press on a box toggles it, the second of a
                # double-click too, and a drag carries that state along.
                self._paint = self._flipped(item)
                self._set_states([item], self._paint)
                return True
            if t == event.Type.MouseButtonDblClick:
                self._paint = self._toggle_with_selection(item)
                return True
            self._sel_before = self._selected()
            return False   # a press on the text: Qt selects
        if t == event.Type.MouseMove and self._paint is not None:
            item = self.value_list.itemAt(event.position().toPoint())
            if item is not None and not item.isHidden() and item.checkState() != self._paint:
                self._set_states([item], self._paint)
            return True
        if t == event.Type.MouseButtonRelease and self._paint is not None:
            self._paint = None
            return True
        return False

    def eventFilter(self, obj, event):
        if obj is self.value_list.viewport() and self._viewport_event(event):
            return True
        # Space toggles the selected rows together (all on unless all are on).
        if (obj is self.value_list and event.type() == event.Type.KeyPress
                and event.key() == Qt.Key.Key_Space):
            selected = self._selected()
            if not selected and self.value_list.currentItem() is not None:
                selected = [self.value_list.currentItem()]
            if selected:
                all_on = all(it.checkState() == Qt.CheckState.Checked for it in selected)
                self._set_states(selected, Qt.CheckState.Unchecked if all_on
                                 else Qt.CheckState.Checked)
            return True
        # Down from the filter field steps into the list, onto the first row
        # the filter left, where Space toggles it.
        if (obj is self.filter_edit and event.type() == event.Type.KeyPress
                and event.key() == Qt.Key.Key_Down):
            for i, item in enumerate(self._items()):
                if not item.isHidden():
                    self.value_list.setCurrentRow(i)
                    self.value_list.setFocus()
                    break
            return True
        return super().eventFilter(obj, event)

    # ── result ───────────────────────────────────────────────────────────────

    def values(self) -> list[str]:
        """The ticked values, in list order."""
        return [
            item.data(Qt.ItemDataRole.UserRole) for item in self._items()
            if item.checkState() == Qt.CheckState.Checked
        ]

    def _update_hint(self):
        chosen = self.values()
        current = set(self._current)
        added = sum(v not in current for v in chosen)
        removed = len(current - set(chosen))
        change = "  ".join(t for t in (f"+{added}" if added else "",
                                       f"−{removed}" if removed else "") if t)
        if self._error is not None:
            text = f"listing failed: {self._error}"
        elif not chosen:
            text = "tick at least one value"
        else:
            text = change
        if self.loading and self._spin_timer.isActive():
            spin = f"{_SPINNER[self._spin_frame % len(_SPINNER)]}  searching disk…"
            text = f"{spin}   {text}" if text else spin
        self.hint.setText(text)


class ValuesDialog(QDialog):
    """Tick the values each axis should have, out of those found on disk.

    *axes* maps each axis to offer to its current values, one column per
    axis (a single axis gets no column title; the window title names it).
    Each column lists the current values (ticked) and the values found on
    disk that the axis does not have yet (unticked, in the accent colour).

    *found* maps an axis to the values on disk. An axis missing from it is
    still being searched: its column shows a spinner until :meth:`set_found`
    brings the listing in. After ``exec()`` returns :data:`Accepted`,
    :meth:`values` maps every axis to its ticked values, in list order.
    """

    def __init__(
        self,
        axes: dict[str, list[str]],
        found: dict[str, list[str]] | None = None,
        *,
        title: str | None = None,
        apply_text: str = "Apply",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        found = found or {}
        names = list(axes)
        self.setWindowTitle(title or (f"Values of {names[0]}" if len(names) == 1
                                      else "Values"))
        self.setModal(True)
        self.setStyleSheet(_STYLE.substitute(vars(_theme.current())))

        self.panels: list[_AxisPanel] = []
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        for name in names:
            panel = _AxisPanel(name, axes[name], title=len(names) > 1)
            panel.changed.connect(self._update_ok)
            if name in found:
                panel.set_found(found[name])
            else:
                panel.start_spinner()
            self.panels.append(panel)
            row.addWidget(panel, 1)

        holder = QWidget()
        holder.setLayout(row)
        self._scroll = QScrollArea()
        self._scroll.setWidget(holder)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(apply_text)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.addWidget(self._scroll, 1)
        root.addWidget(self.buttons)

        self._size_to_lists()
        self._update_ok()
        self.panels[0].filter_edit.setFocus()

    @property
    def axis_names(self) -> list[str]:
        return [p.axis_name for p in self.panels]

    def panel(self, axis: str) -> _AxisPanel:
        return next(p for p in self.panels if p.axis_name == axis)

    @property
    def loading(self) -> bool:
        """Whether some column is still waiting for its listing."""
        return any(p.loading for p in self.panels)

    def set_found(self, axis: str, found: list[str], error: str | None = None) -> None:
        """Bring in the listing of one axis; see :meth:`_AxisPanel.set_found`."""
        self.panel(axis).set_found(found, error)
        self._grow_to_lists()
        self._update_ok()

    def values(self) -> dict[str, list[str]]:
        """Every axis's ticked values, in list order."""
        return {p.axis_name: p.values() for p in self.panels}

    # ── size ─────────────────────────────────────────────────────────────────

    def _size_to_lists(self):
        """Open tall enough for the longest list (up to a screenful of rows)
        and wide enough for every column, scrolling sideways past the screen.
        No list is pinned to that height, so the layout cannot squeeze the
        buttons onto one."""
        self.ensurePolished()
        row_h = max(p.row_height() for p in self.panels)
        n = max(p.value_list.count() for p in self.panels)
        rows = max(6, min(n, _MAX_VALUE_ROWS))
        for p in self.panels:
            p.value_list.setMinimumHeight(4 * row_h)
            p.setMinimumWidth(min(p.content_width(), 260))
        frame = 2 * self.panels[0].value_list.frameWidth() + 8
        list_h = rows * row_h + frame
        chrome = (self.sizeHint().height()
                  - max(p.value_list.sizeHint().height() for p in self.panels))
        width = sum(p.content_width() for p in self.panels) + 16 * (len(self.panels) - 1) + 24
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None:
            width = min(width, int(screen.width() * 0.9))
            height = min(chrome + list_h, int(screen.height() * 0.9))
        else:
            height = chrome + list_h
        self.resize(max(360, width), height)

    def _grow_to_lists(self):
        """After a listing came in: grow to fit the added rows, never shrink
        a dialogue the user is looking at (or has resized)."""
        w, h = self.width(), self.height()
        self._size_to_lists()
        self.resize(max(w, self.width()), max(h, self.height()))

    # ── result ───────────────────────────────────────────────────────────────

    def _update_ok(self, *_):
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(all(p.values() for p in self.panels))

    def _accept(self):
        if not all(p.values() for p in self.panels):
            return
        log.debug("values dialog: %s", self.values())
        self.accept()
