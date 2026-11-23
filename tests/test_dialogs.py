"""Widget tests for juxt/dialogs.py using pytest-qt."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton

from juxt.dialogs import GridDialog, ValuesDialog, auto_layout, merge_values


AXIS_NAMES = ["sensor", "date"]
AXIS_VALUES = [["A", "B", "C"], ["d1", "d2"]]


@pytest.fixture
def dlg(qtbot):
    def _make(**kw):
        # Mirror the viewer, which always supplies auto_layout_for.
        kw.setdefault("auto_layout_for",
                      lambda n: auto_layout(n, 800, 600, 1600, 900))
        d = GridDialog(list(AXIS_NAMES), [list(v) for v in AXIS_VALUES], **kw)
        qtbot.addWidget(d)
        return d
    return _make


class TestAutoLayout:
    def test_square_image_wide_viewport(self):
        # 4 square cells in a 2:1 viewport → wider than tall
        rows, cols = auto_layout(4, 100, 100, 800, 400)
        assert cols > rows

    def test_square_image_square_viewport(self):
        assert auto_layout(4, 100, 100, 600, 600) == (2, 2)

    def test_uneven_count_rounds_up(self):
        rows, cols = auto_layout(5, 100, 100, 600, 600)
        assert rows * cols >= 5


class TestValueSelection:
    def test_all_values_checked_by_default(self, dlg):
        d = dlg()
        assert d.checked_indices() == [0, 1, 2]

    def test_preset_values_restrict_the_checks(self, dlg):
        d = dlg(values=[0, 2])
        assert d.checked_indices() == [0, 2]

    def test_none_button_clears_and_blocks_ok(self, dlg):
        d = dlg()
        d._set_all(False)
        assert d.checked_indices() == []
        assert not d.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()

    def test_invert_flips_every_check(self, dlg):
        d = dlg(values=[1])
        d._invert()
        assert d.checked_indices() == [0, 2]

    def test_switching_axis_repopulates_and_checks_all(self, dlg):
        d = dlg(values=[0])
        d.axis_box.setCurrentIndex(1)
        assert d.value_list.count() == 2
        assert d.checked_indices() == [0, 1]


class TestLayoutControls:
    def test_auto_leaves_the_spin_boxes_live(self, dlg):
        """Disabling them made the step arrows look broken; they stay usable
        and stepping one simply takes the layout off auto."""
        d = dlg()
        assert d.auto_check.isChecked()
        assert d.rows_spin.isEnabled()
        assert d.cols_spin.isEnabled()

    def test_stepping_a_spin_box_unticks_auto(self, dlg):
        d = dlg()
        seeded = d.rows_spin.value()
        d.rows_spin.stepBy(1)
        assert not d.auto_check.isChecked()
        assert d.rows_spin.value() == seeded + 1

    def test_reticking_auto_restores_the_computed_layout(self, dlg):
        d = dlg()
        seeded = (d.rows_spin.value(), d.cols_spin.value())
        d.rows_spin.stepBy(3)
        d.auto_check.setChecked(True)
        assert (d.rows_spin.value(), d.cols_spin.value()) == seeded

    def test_stepped_layout_reaches_the_spec(self, dlg):
        d = dlg()
        d.rows_spin.stepBy(1)
        d.cols_spin.stepBy(1)
        expected = (d.rows_spin.value(), d.cols_spin.value())
        d._accept()
        assert d.spec.layout == expected

    def test_explicit_layout_prefills_and_enables(self, dlg):
        d = dlg(layout=(2, 3))
        assert not d.auto_check.isChecked()
        assert (d.rows_spin.value(), d.cols_spin.value()) == (2, 3)
        assert d.rows_spin.isEnabled()

    def test_hint_explains_cycling_when_the_layout_is_smaller(self, dlg):
        d = dlg(layout=(1, 2))          # 3 values, 2 panes
        assert "2 panes" in d.hint.text()
        assert "cycles the other 1" in d.hint.text()

    def test_step_arrows_are_actually_drawn(self, dlg):
        """The dialog stylesheet suppresses Qt's native spin arrows, leaving two
        blank buttons that read as dead controls; _ArrowSpinBox paints its own."""
        d = dlg()
        d.show()
        spin = d.rows_spin
        spin.setValue(5)  # mid-range, so neither arrow is dimmed at a limit
        img = spin.grab().toImage()
        strip = range(max(0, spin.width() - 18), img.width())
        light = sum(
            1
            for y in range(img.height())
            for x in strip
            if img.pixelColor(x, y).red() > 120
        )
        # Each triangle contributes ~9 bright pixels; the unpainted stylesheet
        # rendering this replaced scored 0.
        assert light > 12, "no visible arrow glyphs in the step-button strip"


class TestResultSpec:
    def test_accept_with_all_values_yields_none_filter(self, dlg):
        d = dlg()
        d._accept()
        assert d.result() == QDialog.DialogCode.Accepted
        assert d.spec.axis == 0
        assert d.spec.values is None
        assert d.spec.layout is None

    def test_accept_with_subset_keeps_indices(self, dlg):
        d = dlg(values=[0, 2])
        d._accept()
        assert d.spec.values == [0, 2]

    def test_accept_carries_layout_and_sync_flags(self, dlg):
        d = dlg(layout=(2, 2), sharex=False, sharey=True)
        d._accept()
        assert d.spec.layout == (2, 2)
        assert d.spec.sharex is False
        assert d.spec.sharey is True

    def test_accept_is_refused_with_no_values(self, dlg):
        d = dlg()
        d._set_all(False)
        d._accept()
        assert d.spec is None

    def test_exit_button_only_exists_in_grid_mode(self, dlg):
        assert len(dlg(in_grid=False).buttons.buttons()) == 2
        assert len(dlg(in_grid=True).buttons.buttons()) == 3


class TestViewerIntegration:
    """Ctrl+Shift+G and :grid-dialog drive the real ImageView."""

    @pytest.fixture
    def view(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.viewer import ImageView

        v = ImageView(
            viewer_config, mini_pixmaps,
            keybindings={"Ctrl+Shift+G": "grid-dialog"},
        )
        qtbot.addWidget(v)
        v.resize(800, 600)
        v.show()
        return v

    @staticmethod
    def _stub(monkeypatch, result, spec=None):
        """Replace GridDialog with one that returns *result* without showing."""
        import juxt.dialogs as dialogs

        class _Stub:
            EXIT = dialogs.GridDialog.EXIT

            def __init__(self, *a, **kw):
                self.kwargs = kw
                self.spec = spec
                _Stub.last = self

            def exec(self):
                return result

        monkeypatch.setattr(dialogs, "GridDialog", _Stub)
        return _Stub

    def test_ctrl_shift_g_opens_the_dialog(self, view, qtbot, monkeypatch):
        stub = self._stub(monkeypatch, QDialog.DialogCode.Rejected)
        qtbot.keyClick(
            view, Qt.Key.Key_G,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        )
        assert hasattr(stub, "last")

    def test_dialog_is_seeded_with_the_current_state(self, view, monkeypatch):
        stub = self._stub(monkeypatch, QDialog.DialogCode.Rejected)
        view._cmd_execute("grid-dialog")
        assert stub.last.kwargs["axis"] == view._h_axis()
        assert stub.last.kwargs["in_grid"] is False

    def test_accepting_enters_grid_mode(self, view, monkeypatch):
        from juxt.dialogs import GridSpec

        spec = GridSpec(axis=1, values=[0], layout=(1, 1), sharex=False, sharey=True)
        self._stub(monkeypatch, QDialog.DialogCode.Accepted, spec)
        view._cmd_execute("grid-dialog")
        assert view._grid_axis == 1
        assert view._grid_filter == [0]
        assert view._grid_layout == (1, 1)
        assert view._grid_sharex is False
        assert view._grid_sharey is True

    def test_rejecting_leaves_grid_state_alone(self, view, monkeypatch):
        self._stub(monkeypatch, QDialog.DialogCode.Rejected)
        view._cmd_execute("grid-dialog")
        assert view._grid_axis is None

    def test_exit_button_leaves_grid_mode(self, view, monkeypatch):
        from juxt.dialogs import GridDialog

        view._cmd_execute("grid sensor")
        assert view._grid_axis is not None
        self._stub(monkeypatch, GridDialog.EXIT)
        view._cmd_execute("grid-dialog")
        assert view._grid_axis is None

    def test_dialog_reflects_an_active_grid(self, view, monkeypatch):
        view._cmd_execute("grid date d1")
        stub = self._stub(monkeypatch, QDialog.DialogCode.Rejected)
        view._cmd_execute("grid-dialog")
        assert stub.last.kwargs["axis"] == 1
        assert stub.last.kwargs["values"] == [0]
        assert stub.last.kwargs["in_grid"] is True


class TestMergeValues:
    def test_sorted_axis_takes_new_values_in_place(self):
        assert merge_values(["A", "C"], ["A", "B", "C", "D"]) == ["A", "B", "C", "D"]

    def test_numeric_axis_sorts_numerically(self):
        assert merge_values(["2", "10"], ["2", "3", "10"]) == ["2", "3", "10"]

    def test_reordered_axis_keeps_its_order_and_appends(self):
        assert merge_values(["C", "A"], ["A", "B", "C"]) == ["C", "A", "B"]

    def test_nothing_new(self):
        assert merge_values(["B", "A"], ["A", "B"]) == ["B", "A"]


def _one(qtbot, current=("A", "C"), found=("A", "B", "C", "D")):
    """A single-axis value picker and its one column; *found* None = still searching."""
    d = ValuesDialog({"sensor": list(current)},
                     None if found is None else {"sensor": list(found)})
    qtbot.addWidget(d)
    return d, d.panels[0]


@pytest.fixture
def values_dlg(qtbot):
    return lambda **kw: _one(qtbot, **kw)


def _rows(p):
    items = [p.value_list.item(i) for i in range(p.value_list.count())]
    return [(it.data(Qt.ItemDataRole.UserRole), it.text(),
             it.checkState() == Qt.CheckState.Checked) for it in items]


def _ok(d):
    return d.buttons.button(QDialogButtonBox.StandardButton.Ok)


class TestValuesDialog:
    def test_current_ticked_new_unticked(self, values_dlg):
        _d, p = values_dlg()
        assert _rows(p) == [
            ("A", "A", True), ("B", "B", False),
            ("C", "C", True), ("D", "D", False),
        ]

    def test_new_values_are_marked_by_colour(self, values_dlg):
        from PySide6.QtGui import QColor
        from juxt import theme
        _d, p = values_dlg()
        accent = QColor(theme.current().accent)
        assert p.value_list.item(1).foreground().color() == accent
        assert p.value_list.item(0).foreground().color() != accent

    def test_unchanged_returns_current(self, values_dlg):
        d, _p = values_dlg()
        assert d.values() == {"sensor": ["A", "C"]}

    def test_single_axis_has_no_column_title(self, values_dlg):
        d, _p = values_dlg()
        assert d.windowTitle() == "Values of sensor"
        assert not [l for l in d.findChildren(QLabel) if l.text() == "sensor"]

    def test_ticking_adds_in_list_order(self, values_dlg):
        d, p = values_dlg()
        p.value_list.item(1).setCheckState(Qt.CheckState.Checked)   # B
        p.value_list.item(0).setCheckState(Qt.CheckState.Unchecked)  # A
        assert d.values() == {"sensor": ["B", "C"]}
        assert p.hint.text() == "+1  −1"

    def test_value_without_files_is_flagged(self, values_dlg):
        _d, p = values_dlg(current=["A", "Z"], found=["A"])
        assert ("Z", "Z  no files", True) in _rows(p)

    def test_listing_error_shows_current_only(self, values_dlg):
        d, p = values_dlg(found=None)
        d.set_found("sensor", [], error="boom")
        assert [r[1] for r in _rows(p)] == ["A", "C"]
        assert "boom" in p.hint.text()

    def test_no_change_shows_no_hint(self, values_dlg):
        _d, p = values_dlg()
        assert p.hint.text() == ""

    def test_opens_large_enough_for_its_values(self, values_dlg):
        d, p = values_dlg(current=[f"v{i:02d}" for i in range(15)], found=[])
        d.show()
        assert d.width() >= 360
        assert p.value_list.height() >= 15 * p.value_list.sizeHintForRow(0)

    def test_buttons_never_overlap_the_list(self, values_dlg, qtbot):
        d, p = values_dlg(current=[f"v{i:02d}" for i in range(40)], found=[])
        d.show()
        d.resize(d.width(), 200)   # squeeze it
        qtbot.wait(10)
        bottom = p.value_list.mapTo(d, p.value_list.rect().bottomLeft()).y()
        for b in d.findChildren(QPushButton):
            if b.isVisible():
                assert b.mapTo(d, b.rect().topLeft()).y() >= bottom, b.text()

    def test_filter_limits_all_and_none(self, values_dlg):
        d, p = values_dlg(current=["A1", "B1"], found=["A1", "A2", "B1", "B2"])
        p.filter_edit.setText("a")
        hidden = [p.value_list.item(i).isHidden() for i in range(4)]
        assert hidden == [False, False, True, True]
        p._set_visible(True)
        assert p.values() == ["A1", "A2", "B1"]
        p._set_visible(False)
        assert p.values() == ["B1"]

    def test_apply_disabled_without_values(self, values_dlg):
        d, p = values_dlg()
        p._set_visible(False)
        assert not _ok(d).isEnabled()
        d._accept()
        assert d.result() != QDialog.DialogCode.Accepted

    def test_down_from_filter_enters_list(self, values_dlg, qtbot):
        d, p = values_dlg(current=["A1", "B1"], found=["A1", "A2", "B1", "B2"])
        d.show()
        p.filter_edit.setFocus()
        p.filter_edit.setText("b")
        qtbot.keyClick(p.filter_edit, Qt.Key.Key_Down)
        assert p.value_list.currentRow() == 2


class TestValuesDialogLoading:
    def test_opens_with_current_values_and_a_spinner(self, qtbot):
        d, p = _one(qtbot, found=None)
        assert d.loading
        assert [r[1] for r in _rows(p)] == ["A", "C"]
        assert "searching" in p.hint.text()
        assert _ok(d).isEnabled()

    def test_listing_joins_in_place_keeping_ticks_and_filter(self, qtbot):
        d, p = _one(qtbot, found=None)
        p.value_list.item(0).setCheckState(Qt.CheckState.Unchecked)   # drop A
        p.filter_edit.setText("c")
        d.set_found("sensor", ["A", "B", "C", "D"])
        assert not d.loading
        assert _rows(p) == [("A", "A", False), ("B", "B", False),
                            ("C", "C", True), ("D", "D", False)]
        assert [p.value_list.item(i).isHidden() for i in range(4)] == [True, True, False, True]
        assert p.hint.text() == "−1"

    def test_spinner_animates(self, qtbot):
        _d, p = _one(qtbot, found=None)
        first = p.hint.text()
        qtbot.waitUntil(lambda: p.hint.text() != first, timeout=1000)

    def test_does_not_shrink_when_the_listing_arrives(self, qtbot):
        d, _p = _one(qtbot, current=["A"], found=None)
        d.show()
        d.resize(600, 500)
        d.set_found("sensor", ["A", "B"])
        assert (d.width(), d.height()) == (600, 500)


class TestValuesDialogMouse:
    """Toggling several rows: selection + Space / double-click, and dragging
    from a box (or a double-click) across rows."""

    def _dialog(self, qtbot, current=("A",), found=("A", "B", "C", "D")):
        d, p = _one(qtbot, current=list(current), found=list(found))
        d.show()
        qtbot.waitExposed(d)
        return d, p

    def _pt(self, p, row, on_box=False):
        rect = p.value_list.visualItemRect(p.value_list.item(row))
        x = 4 if on_box else 60
        return rect.topLeft() + type(rect.topLeft())(x, rect.height() // 2)

    def _vp(self, p):
        return p.value_list.viewport()

    def _dclick(self, qtbot, p, row):
        """A real double-click: QTest's mouseDClick sends only the double-click
        event, without the press and release of the first click."""
        qtbot.mouseClick(self._vp(p), Qt.MouseButton.LeftButton, pos=self._pt(p, row))
        qtbot.mouseDClick(self._vp(p), Qt.MouseButton.LeftButton, pos=self._pt(p, row))

    def test_double_click_on_text_toggles(self, qtbot):
        _d, p = self._dialog(qtbot)
        self._dclick(qtbot, p, 1)
        assert p.values() == ["A", "B"]
        assert p.hint.text() == "+1"
        self._dclick(qtbot, p, 1)
        assert p.values() == ["A"]

    def test_click_on_box_toggles_once(self, qtbot):
        _d, p = self._dialog(qtbot)
        qtbot.mouseClick(self._vp(p), Qt.MouseButton.LeftButton, pos=self._pt(p, 2, True))
        assert p.values() == ["A", "C"]

    def test_drag_from_a_box_paints_its_new_state(self, qtbot):
        _d, p = self._dialog(qtbot)
        vp = self._vp(p)
        qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 1, True))
        for row in (2, 3):
            qtbot.mouseMove(vp, self._pt(p, row))
        qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 3))
        assert p.values() == ["A", "B", "C", "D"]
        assert p.hint.text() == "+3"

    def test_drag_unticks_when_the_first_box_was_ticked(self, qtbot):
        _d, p = self._dialog(qtbot, current=("A", "B", "C"))
        vp = self._vp(p)
        qtbot.mousePress(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 0, True))
        qtbot.mouseMove(vp, self._pt(p, 1))
        qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 1))
        assert p.values() == ["C"]

    def test_moving_after_release_paints_nothing(self, qtbot):
        _d, p = self._dialog(qtbot)
        vp = self._vp(p)
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 1, True))
        qtbot.mouseMove(vp, self._pt(p, 3))
        assert p.values() == ["A", "B"]

    def test_shift_click_range_then_space(self, qtbot):
        _d, p = self._dialog(qtbot)
        vp = self._vp(p)
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 1))
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ShiftModifier, self._pt(p, 3))
        assert len(p._selected()) == 3
        qtbot.keyClick(p.value_list, Qt.Key.Key_Space)
        assert p.values() == ["A", "B", "C", "D"]
        qtbot.keyClick(p.value_list, Qt.Key.Key_Space)   # all on -> all off
        assert p.values() == ["A"]

    def test_ctrl_click_then_double_click_toggles_the_selection(self, qtbot):
        _d, p = self._dialog(qtbot)
        vp = self._vp(p)
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 1))
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, self._pt(p, 3))
        self._dclick(qtbot, p, 3)
        assert p.values() == ["A", "B", "D"]
        assert {it.data(Qt.ItemDataRole.UserRole) for it in p._selected()} == {"B", "D"}

    def test_double_click_outside_the_selection_toggles_one_row(self, qtbot):
        _d, p = self._dialog(qtbot)
        vp = self._vp(p)
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 1))
        qtbot.mouseClick(vp, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.ControlModifier, self._pt(p, 2))
        self._dclick(qtbot, p, 3)
        assert p.values() == ["A", "D"]

    def test_space_skips_rows_the_filter_hides(self, qtbot):
        _d, p = self._dialog(qtbot, found=("A", "B1", "B2", "C"))
        p.value_list.selectAll()
        p.filter_edit.setText("b")
        qtbot.keyClick(p.value_list, Qt.Key.Key_Space)
        assert p.values() == ["A", "B1", "B2"]

    def test_double_click_then_drag_paints(self, qtbot):
        _d, p = self._dialog(qtbot)
        vp = self._vp(p)
        self._dclick(qtbot, p, 1)                  # B on, painting on
        qtbot.mouseMove(vp, self._pt(p, 2))
        qtbot.mouseMove(vp, self._pt(p, 3))
        qtbot.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=self._pt(p, 3))
        assert p.values() == ["A", "B", "C", "D"]

    def test_box_zone(self, qtbot):
        _d, p = self._dialog(qtbot)
        item = p.value_list.item(1)
        x = p.value_list.visualItemRect(item).left() + 4
        assert p._on_indicator(item, x)
        assert not p._on_indicator(item, x + 56)


class TestJointValuesDialog:
    AXES = {"sensor": ["A", "B"], "date": ["d1", "d2", "d3"]}

    def _dialog(self, qtbot, found=None):
        d = ValuesDialog({k: list(v) for k, v in self.AXES.items()}, found)
        qtbot.addWidget(d)
        return d

    def test_one_titled_column_per_axis(self, qtbot):
        d = self._dialog(qtbot)
        assert d.axis_names == ["sensor", "date"]
        titles = {l.text() for l in d.findChildren(QLabel)}
        assert {"sensor", "date"} <= titles
        assert d.windowTitle() == "Values"

    def test_columns_fill_in_as_listings_arrive(self, qtbot):
        d = self._dialog(qtbot)
        d.set_found("date", ["d1", "d2", "d3", "d4"])
        assert d.panel("date").value_list.count() == 4
        assert d.panel("sensor").loading
        assert d.loading
        d.set_found("sensor", ["A", "B"])
        assert not d.loading

    def test_values_per_axis(self, qtbot):
        d = self._dialog(qtbot, found={"sensor": ["A", "B", "C"], "date": ["d1", "d2", "d3"]})
        d.panel("sensor").value_list.item(2).setCheckState(Qt.CheckState.Checked)
        d.panel("date").value_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        assert d.values() == {"sensor": ["A", "B", "C"], "date": ["d2", "d3"]}

    def test_apply_needs_a_value_on_every_axis(self, qtbot):
        d = self._dialog(qtbot)
        d.panel("date")._set_visible(False)
        assert not _ok(d).isEnabled()
        d.panel("date")._set_visible(True)
        assert _ok(d).isEnabled()

    def test_counter_multiplies_the_ticked_values(self, qtbot):
        d = self._dialog(qtbot, found={"sensor": ["A", "B", "C"], "date": ["d1", "d2", "d3"]})
        assert d.image_count() == 6
        assert d.image_total() == 9
        assert d.count_label.text() == "6 of 9 images"
        d.panel("sensor").value_list.item(2).setCheckState(Qt.CheckState.Checked)
        assert d.count_label.text() == "9 images"
        d.panel("date")._set_visible(False)
        assert d.count_label.text() == "0 of 9 images"

    def test_counter_has_no_total_while_a_listing_is_out(self, qtbot):
        d = self._dialog(qtbot)
        assert d.image_total() is None
        assert d.count_label.text() == "6 images"
        d.set_found("date", ["d1", "d2", "d3", "d4"])
        assert d.count_label.text() == "6 images"
        d.set_found("sensor", ["A", "B"])
        assert d.count_label.text() == "6 of 8 images"

    def test_counter_includes_the_axes_not_on_offer(self, qtbot):
        d = ValuesDialog({"a": ["1"]}, {"a": ["1", "2"]}, other_combinations=5)
        qtbot.addWidget(d)
        assert d.count_label.text() == "5 of 10 images"

    def test_counter_says_one_image(self, qtbot):
        d = ValuesDialog({"a": ["1"]}, {"a": ["1"]})
        qtbot.addWidget(d)
        assert d.count_label.text() == "1 image"

    def test_custom_title_and_apply_text(self, qtbot):
        d = ValuesDialog({"a": ["1"]}, {"a": ["1"]}, title="pick", apply_text="Load")
        qtbot.addWidget(d)
        assert d.windowTitle() == "pick"
        assert _ok(d).text() == "Load"

    def test_many_axes_fit_the_screen(self, qtbot):
        axes = {f"axis_{i}": [f"some_long_value_{j}" for j in range(5)] for i in range(12)}
        d = ValuesDialog(axes, axes)
        qtbot.addWidget(d)
        d.show()
        assert d.width() <= d.screen().availableGeometry().width()
