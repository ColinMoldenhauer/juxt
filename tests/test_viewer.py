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


# ---------------------------------------------------------------------------
# :pattern — placeholder-aware Tab completion
# ---------------------------------------------------------------------------

class TestPatternCompletion:
    @staticmethod
    def _open(view, query: str, caret: int | None = None):
        """Put the view in `:pattern <query>` with the caret at *caret*."""
        view._cmd = {
            "phase": "arg", "verb": "pattern", "query": query,
            "cursor": 0, "caret": len(query) if caret is None else caret,
        }

    def test_tab_key_triggers_completion(self, image_view, nested_plot_dir):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        base = nested_plot_dir.as_posix()
        self._open(image_view, f"{base}/A/A")
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                          Qt.KeyboardModifier.NoModifier)
        assert image_view._cmd_handle_key(event) is True
        assert image_view._cmd["query"] == f"{base}/A/AM/"

    def test_completes_below_a_placeholder(self, image_view, nested_plot_dir):
        base = nested_plot_dir.as_posix()
        self._open(image_view, f"{base}/{{sensor}}/A")
        image_view._complete_path()
        assert image_view._cmd["query"] == f"{base}/{{sensor}}/AM/"
        assert image_view._cmd["caret"] == len(image_view._cmd["query"])

    def test_ambiguous_completion_fills_the_hint_list(self, image_view, nested_plot_dir):
        base = nested_plot_dir.as_posix()
        self._open(image_view, f"{base}/{{sensor}}/{{overpass}}/")
        image_view._complete_path()
        assert image_view._tab_matches == ["d1.png", "d2.png"]

    def test_completes_an_axis_name(self, image_view):
        self._open(image_view, "plots/{s")
        image_view._complete_path()
        assert image_view._cmd["query"] == "plots/{sensor}"

    def test_open_brace_lists_axis_names(self, image_view):
        self._open(image_view, "plots/{")
        image_view._complete_path()
        assert image_view._tab_matches == ["{sensor}", "{date}"]

    def test_text_after_the_caret_is_preserved(self, image_view, nested_plot_dir):
        base = nested_plot_dir.as_posix()
        query = f"{base}/{{sensor}}/A{{date}}.png"
        self._open(image_view, query, caret=len(f"{base}/{{sensor}}/A"))
        image_view._complete_path()
        assert image_view._cmd["query"] == f"{base}/{{sensor}}/AM/{{date}}.png"
        assert image_view._cmd["caret"] == len(f"{base}/{{sensor}}/AM/")

    def test_no_match_leaves_the_query_alone(self, image_view, nested_plot_dir):
        query = f"{nested_plot_dir.as_posix()}/{{sensor}}/ZZ"
        self._open(image_view, query)
        image_view._complete_path()
        assert image_view._cmd["query"] == query
        assert image_view._tab_matches == []


# ---------------------------------------------------------------------------
# Status bar: placeholder colouring
# ---------------------------------------------------------------------------

@pytest.fixture
def main_window(viewer_config, mini_pixmaps, qtbot):
    """MainWindow wired to the shared 2x2 test config."""
    from juxt.viewer import MainWindow

    w = MainWindow(viewer_config, mini_pixmaps, watch=False)
    qtbot.addWidget(w)
    w.resize(800, 600)
    return w


class TestPlaceholderColouring:
    def test_pattern_placeholders_are_coloured(self, main_window):
        from juxt.complete import PLACEHOLDER_COLORS

        v = main_window.view
        TestPatternCompletion._open(v, "plots/{sensor}_{date}.png")
        main_window._update_status()
        html = main_window._status_label._full_html
        assert f'color:{PLACEHOLDER_COLORS[0]}">{{sensor}}' in html
        assert f'color:{PLACEHOLDER_COLORS[1]}">{{date}}' in html

    def test_argument_without_placeholders_reads_as_plain_text(self, main_window):
        v = main_window.view
        TestPatternCompletion._open(v, "plots/dir")
        main_window._update_status()
        shown = main_window._status_label._full_plain
        assert "&nbsp;" not in shown          # entities must not leak as text
        assert shown.replace("\xa0", " ") == ":pattern plots/dir▌"

    def test_markup_in_the_query_is_escaped(self, main_window):
        v = main_window.view
        TestPatternCompletion._open(v, "plots/<b>/{sensor}.png")
        main_window._update_status()
        assert "&lt;b&gt;" in main_window._status_label._full_html


# ---------------------------------------------------------------------------
# Grid windowing — the layout caps how many cells exist; extra values page
# ---------------------------------------------------------------------------

class TestGridWindowing:
    @staticmethod
    def _view(qtbot, n_sensors=4):
        """ImageView over an axis with enough values to overflow a small grid."""
        from PySide6.QtGui import QColor, QPixmap
        from juxt.config import Config, _auto_keys
        from juxt.viewer import ImageView

        axes = {"sensor": [chr(ord("A") + i) for i in range(n_sensors)],
                "date": ["d1", "d2"]}
        cfg = Config(template="fake/{sensor}_{date}.png", axes=axes,
                     keys=_auto_keys(axes))
        pms = {}
        for i in range(n_sensors):
            for j in range(2):
                pm = QPixmap(80, 60)
                pm.fill(QColor(40 + i * 40, 40 + j * 40, 120))
                pms[(i, j)] = pm
        v = ImageView(cfg, pms)
        qtbot.addWidget(v)
        v.resize(800, 600)
        return v

    @staticmethod
    def _shown(v):
        """(visible labels, focused label) for the current grid window."""
        window, focused = v._grid_window()
        labels = [v.axis_values[v._grid_axis][vi] for vi in window]
        return labels, (labels[focused] if 0 <= focused < len(labels) else None)

    def test_layout_caps_the_number_of_cells(self, qtbot):
        """A 2x1 layout over 3 values must not silently become a 3x1 grid."""
        v = self._view(qtbot, n_sensors=3)
        v._enter_grid(0, None, (2, 1))
        assert v._grid_widget.capacity() == 2
        assert len(v._grid_widget._cells) == 2
        assert v._grid_widget.layout().rowCount() == 2
        assert v._grid_widget.layout().columnCount() == 1

    def test_first_values_are_the_ones_shown(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        labels, focused = self._shown(v)
        assert labels == ["A", "B"]
        assert focused == "A"

    def test_stepping_within_the_window_only_moves_focus(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._navigate(0, +1)
        assert self._shown(v) == (["A", "B"], "B")
        assert v._grid_offset == 0

    def test_stepping_past_the_edge_scrolls_the_window(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._navigate(0, +1)
        v._navigate(0, +1)                      # C is not on screen yet
        assert self._shown(v) == (["B", "C"], "C")
        v._navigate(0, +1)
        assert self._shown(v) == (["C", "D"], "D")

    def test_stepping_back_scrolls_the_other_way(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        for _ in range(3):
            v._navigate(0, +1)                  # window now C D, focus D
        v._navigate(0, -1)
        assert self._shown(v) == (["C", "D"], "C")
        v._navigate(0, -1)
        assert self._shown(v) == (["B", "C"], "B")

    def test_wrapping_returns_to_the_first_window(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        for _ in range(4):                      # A B C D then wrap
            v._navigate(0, +1)
        assert self._shown(v) == (["A", "B"], "A")
        assert v._grid_offset == 0

    def test_no_paging_when_every_value_fits(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 2))
        assert v._grid_widget.capacity() == 4
        labels, focused = self._shown(v)
        assert labels == ["A", "B", "C", "D"]
        assert focused == "A"
        v._navigate(0, +1)                      # focus moves, window does not
        assert self._shown(v) == (["A", "B", "C", "D"], "B")
        assert v._grid_offset == 0

    def test_filtered_values_page_and_skip_the_rest(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, [0, 2, 3], (2, 1))     # A, C, D — B excluded
        assert self._shown(v) == (["A", "C"], "A")
        v._navigate(0, +1)
        assert self._shown(v) == (["A", "C"], "C")
        v._navigate(0, +1)                      # steps to D, never to B
        assert self._shown(v) == (["C", "D"], "D")
        v._navigate(0, +1)
        assert self._shown(v) == (["A", "C"], "A")

    def test_entry_anchors_focus_on_a_shown_value(self, qtbot):
        """Entering with the axis parked on a filtered-out value must not
        leave the focus pointing at something the grid never draws."""
        v = self._view(qtbot)
        v.pos[0] = 1                            # B, which the filter excludes
        v._enter_grid(0, [0, 2, 3], (2, 1))
        assert v.pos[0] == 0
        assert self._shown(v) == (["A", "C"], "A")

    def test_exactly_one_cell_is_focused(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._navigate(0, +1)
        assert [c._focused for c in v._grid_widget._cells] == [False, True]

    def test_cell_labels_follow_the_window(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        assert [c._label_text for c in v._grid_widget._cells] == ["A", "B"]
        for _ in range(3):
            v._navigate(0, +1)
        assert [c._label_text for c in v._grid_widget._cells] == ["C", "D"]
