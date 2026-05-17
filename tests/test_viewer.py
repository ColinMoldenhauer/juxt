"""Widget tests for juxt/viewer.py using pytest-qt."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from juxt.viewer import ImageView, NavMode


# ---------------------------------------------------------------------------
# Universal navigation — these work identically in all three modes
# ---------------------------------------------------------------------------

class TestArrowNavigation:
    def test_right_advances_h_axis(self, image_view, qtbot):
        assert image_view.pos == [0, 0]
        qtbot.keyClick(image_view, Qt.Key.Key_Right)
        assert image_view.pos == [1, 0]

    def test_left_retreats_h_axis(self, image_view, qtbot):
        # From index 0, going left wraps to index 1 (last value)
        qtbot.keyClick(image_view, Qt.Key.Key_Left)
        assert image_view.pos[0] == 1

    def test_right_wraps_at_boundary(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)  # 0 → 1
        qtbot.keyClick(image_view, Qt.Key.Key_Right)  # 1 → 0 (wrap)
        assert image_view.pos[0] == 0

    def test_down_advances_v_axis(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Down)
        assert image_view.pos == [0, 1]

    def test_up_retreats_v_axis(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Up)
        assert image_view.pos[1] == 1  # wraps from 0 to last


class TestSpacebarToggle:
    def test_spacebar_toggles_to_prev(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # pos=[1,0], prev=[0,0]
        qtbot.keyClick(image_view, Qt.Key.Key_Space)   # pos=[0,0], prev=[1,0]
        assert image_view.pos == [0, 0]

    def test_spacebar_is_reversible(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # pos=[1,0]
        qtbot.keyClick(image_view, Qt.Key.Key_Space)   # back to [0,0]
        qtbot.keyClick(image_view, Qt.Key.Key_Space)   # back to [1,0]
        assert image_view.pos == [1, 0]

    def test_spacebar_noop_without_prev(self, image_view, qtbot):
        assert image_view.prev is None
        qtbot.keyClick(image_view, Qt.Key.Key_Space)
        assert image_view.pos == [0, 0]  # unchanged


class TestHomeEndJump:
    def test_home_jumps_to_first(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # pos=[1,0]
        qtbot.keyClick(image_view, Qt.Key.Key_Home)
        assert image_view.pos[0] == 0

    def test_end_jumps_to_last(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_End)
        last = len(image_view.axis_values[image_view._h_axis()]) - 1
        assert image_view.pos[0] == last


class TestDigitJump:
    def test_key_1_jumps_to_index_0(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # pos=[1,0]
        qtbot.keyClick(image_view, Qt.Key.Key_1)
        assert image_view.pos[0] == 0

    def test_key_2_jumps_to_index_1(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_2)
        assert image_view.pos[0] == 1

    def test_out_of_range_digit_is_noop(self, image_view, qtbot):
        # sensor axis has 2 values; key 9 (index 8) is out of range
        qtbot.keyClick(image_view, Qt.Key.Key_9)
        assert image_view.pos[0] == 0


class TestStateChangedSignal:
    def test_right_arrow_emits_state_changed(self, image_view, qtbot):
        with qtbot.waitSignal(image_view.state_changed, timeout=1000):
            qtbot.keyClick(image_view, Qt.Key.Key_Right)

    def test_spacebar_emits_state_changed_after_navigate(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)
        with qtbot.waitSignal(image_view.state_changed, timeout=1000):
            qtbot.keyClick(image_view, Qt.Key.Key_Space)


# ---------------------------------------------------------------------------
# Mode 0 — CASE_SENSITIVE (default)
# ---------------------------------------------------------------------------

class TestTapMode:
    def test_lowercase_advances_axis(self, image_view, qtbot):
        assert image_view.nav_mode == NavMode.TAP  # default mode
        qtbot.keyClick(image_view, Qt.Key.Key_S)
        assert image_view.pos[0] == 1  # sensor advanced

    def test_uppercase_retreats_axis(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # pos[0]=1
        qtbot.keyClick(image_view, Qt.Key.Key_S, Qt.ShiftModifier)  # sensor back
        assert image_view.pos[0] == 0

    def test_lowercase_also_updates_focus(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_D)   # date axis
        assert image_view._h_axis() == 1           # date is now h_axis

    def test_ctrl_letter_opens_value_picker(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S, Qt.ControlModifier)
        assert image_view._sel is not None
        assert image_view._sel["phase"] == "value"
        assert image_view._sel["axis_idx"] == 0  # sensor


# ---------------------------------------------------------------------------
# Mode 2 — TWIN
# ---------------------------------------------------------------------------

class TestPinMode:
    @pytest.fixture(autouse=True)
    def set_pin(self, image_view):
        image_view.nav_mode = NavMode.PIN

    def test_letter_key_focuses_axis_without_moving(self, image_view, qtbot):
        initial_pos = list(image_view.pos)
        qtbot.keyClick(image_view, Qt.Key.Key_D)   # focus date
        assert image_view.pos == initial_pos       # no navigation

    def test_letter_key_updates_h_axis_binding(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_D)   # focus date (axis 1)
        assert image_view._h_axis() == 1

    def test_right_arrow_follows_focus(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_D)   # focus date
        qtbot.keyClick(image_view, Qt.Key.Key_Right)
        assert image_view.pos[1] == 1              # date moved
        assert image_view.pos[0] == 0              # sensor unchanged

    def test_refocus_changes_arrow_binding(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_D)   # h=date, v=sensor
        qtbot.keyClick(image_view, Qt.Key.Key_S)   # h=sensor, v=date
        assert image_view._h_axis() == 0           # back to sensor

    def test_ctrl_letter_opens_value_picker(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_D, Qt.ControlModifier)
        assert image_view._sel is not None
        assert image_view._sel["axis_idx"] == 1    # date


# ---------------------------------------------------------------------------
# Mode 1 — MULTI_SELECT
# ---------------------------------------------------------------------------

class TestSeekMode:
    @pytest.fixture(autouse=True)
    def set_seek(self, image_view):
        image_view.nav_mode = NavMode.SEEK

    def test_letter_opens_axis_search(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S)
        # "sensor" is the unique match for "s" → auto-commits, enters value phase
        assert image_view._sel is not None
        assert image_view._sel["phase"] == "value"
        assert image_view._sel["axis_idx"] == 0

    def test_second_letter_completes_value_selection(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S)    # → value phase for sensor
        qtbot.keyClick(image_view, Qt.Key.Key_B)    # "b" matches "B" uniquely → commit
        assert image_view._sel is None
        assert image_view.pos[0] == 1               # "B" is index 1

    def test_escape_cancels_selection(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S)    # opens search
        qtbot.keyClick(image_view, Qt.Key.Key_Escape)
        assert image_view._sel is None

    def test_backspace_on_empty_value_query_returns_to_axis(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S)    # → value phase, query=""
        qtbot.keyClick(image_view, Qt.Key.Key_Backspace)
        assert image_view._sel["phase"] == "axis"


# ---------------------------------------------------------------------------
# Command mode
# ---------------------------------------------------------------------------

class TestCommandMode:
    def test_colon_opens_command_mode(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Colon)
        assert image_view._cmd is not None
        assert image_view._cmd["phase"] == "verb"

    def test_escape_closes_command_mode(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Colon)
        qtbot.keyClick(image_view, Qt.Key.Key_Escape)
        assert image_view._cmd is None

    def test_ctrl_c_cancels_command_mode(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Colon)
        qtbot.keyClick(image_view, Qt.Key.Key_C, Qt.ControlModifier)
        assert image_view._cmd is None

    def test_execute_zoom_sets_transform(self, image_view):
        image_view._cmd_execute("zoom 50")
        assert abs(image_view.transform().m11() - 0.5) < 1e-6

    def test_execute_zoom_100_is_identity(self, image_view):
        image_view._cmd_execute("zoom 50")   # set to 50%
        image_view._cmd_execute("zoom 100")  # back to 100%
        assert abs(image_view.transform().m11() - 1.0) < 1e-6

    def test_execute_mode_pin(self, image_view):
        image_view._cmd_execute("mode pin")
        assert image_view.nav_mode == NavMode.PIN

    def test_execute_mode_seek(self, image_view):
        image_view._cmd_execute("mode seek")
        assert image_view.nav_mode == NavMode.SEEK

    def test_execute_mode_tap(self, image_view):
        image_view._cmd_execute("mode pin")   # change away first
        image_view._cmd_execute("mode tap")
        assert image_view.nav_mode == NavMode.TAP

    def test_execute_switch_last(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # pos=[1,0], prev=[0,0]
        image_view._cmd_execute("switch-last")
        assert image_view.pos == [0, 0]

    def test_execute_fit_does_not_raise(self, image_view):
        image_view._cmd_execute("fit")   # just ensure no exception

    def test_execute_fit_height_does_not_raise(self, image_view):
        image_view._cmd_execute("fit height")

    def test_execute_fit_width_does_not_raise(self, image_view):
        image_view._cmd_execute("fit width")

    def test_execute_q_alias(self, image_view):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        # ':q' is an alias for 'quit' — patch quit so the test process doesn't exit
        with patch.object(QApplication.instance(), "quit") as mock_quit:
            image_view._cmd_execute("q")
            mock_quit.assert_called_once()


# ---------------------------------------------------------------------------
# Focus stack
# ---------------------------------------------------------------------------

class TestFocusStack:
    def test_initial_focus_stack(self, image_view):
        assert image_view.focus_stack == [0, 1]

    def test_h_axis_is_first_in_stack(self, image_view):
        assert image_view._h_axis() == image_view.focus_stack[0]

    def test_v_axis_is_second_in_stack(self, image_view):
        assert image_view._v_axis() == image_view.focus_stack[1]

    def test_navigate_stores_prev(self, image_view, qtbot):
        old_pos = list(image_view.pos)
        qtbot.keyClick(image_view, Qt.Key.Key_Right)
        assert image_view.prev == old_pos
