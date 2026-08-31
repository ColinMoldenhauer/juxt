"""Widget tests for juxt/dialogs.py using pytest-qt."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from juxt.dialogs import GridDialog, auto_layout


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

    def test_hint_explains_paging_when_the_layout_is_smaller(self, dlg):
        d = dlg(layout=(1, 2))          # 3 values, 2 slots
        assert "2 at a time" in d.hint.text()
        assert "page" in d.hint.text()

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
