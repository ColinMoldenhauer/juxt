"""Widget tests for juxt/viewer.py using pytest-qt."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap, QTextCursor

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

    def test_arrow_keys_do_not_force_sidebar_scroll(self, image_view, qtbot):
        """Arrow-key stepping must not ask the info sidebar to jump to the
        new value -- only letter-shortcut stepping (tap mode) does that."""
        qtbot.keyClick(image_view, Qt.Key.Key_Right)
        assert image_view._sidebar_target is None


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


class TestNavigateEnsureVisible:
    """_navigate()'s ensure_visible flag: what tells the info sidebar to
    force-scroll the newly selected value into view (see TestTapMode /
    TestArrowNavigation for which key bindings pass it)."""

    def test_default_does_not_set_sidebar_target(self, image_view):
        image_view._navigate(0, 1)
        assert image_view._sidebar_target is None

    def test_ensure_visible_sets_sidebar_target_to_new_position(self, image_view):
        image_view._navigate(0, 1, ensure_visible=True)
        assert image_view._sidebar_target == (0, image_view.pos[0])

    def test_ensure_visible_target_tracks_the_navigated_axis(self, image_view):
        image_view._navigate(1, 1, ensure_visible=True)
        assert image_view._sidebar_target == (1, image_view.pos[1])


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

    def test_letter_shortcut_forces_sidebar_scroll(self, image_view, qtbot):
        """Letter-shortcut stepping asks the info sidebar to keep the newly
        selected value visible, unlike arrow-key stepping."""
        qtbot.keyClick(image_view, Qt.Key.Key_S)
        assert image_view._sidebar_target == (0, image_view.pos[0])


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

    def test_execute_fit_off_does_not_raise(self, image_view):
        image_view._cmd_execute("fit-off")

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
# Info sidebar: clickable values
# ---------------------------------------------------------------------------

class TestInfoPanelLinks:
    def _panel(self, image_view, qtbot):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.value_clicked.connect(image_view.goto_value)
        panel.refresh(image_view)
        return panel

    def test_href_roundtrip(self):
        from juxt.viewer import _parse_value_href, _value_href

        assert _parse_value_href(_value_href(2, 7)) == (2, 7)
        assert _parse_value_href("https://example.com") is None

    def test_every_value_is_a_link(self, image_view, qtbot):
        html = self._panel(image_view, qtbot).toHtml()
        for i, vals in enumerate(image_view.axis_values):
            for j in range(len(vals)):
                assert f'href="juxt:value/{i}/{j}"' in html

    def test_click_navigates(self, image_view, qtbot):
        panel = self._panel(image_view, qtbot)
        panel.value_clicked.emit(1, 1)
        assert image_view.pos == [0, 1]

    def test_click_focuses_axis(self, image_view, qtbot):
        panel = self._panel(image_view, qtbot)
        panel.value_clicked.emit(1, 1)
        assert image_view.focus_stack[0] == 1

    def test_click_stores_prev(self, image_view, qtbot):
        panel = self._panel(image_view, qtbot)
        old_pos = list(image_view.pos)
        panel.value_clicked.emit(0, 1)
        assert image_view.prev == old_pos

    def test_click_on_current_value_keeps_prev(self, image_view, qtbot):
        panel = self._panel(image_view, qtbot)
        panel.value_clicked.emit(0, 0)  # already the current value
        assert image_view.prev is None
        assert image_view.pos == [0, 0]

    def test_out_of_range_click_is_ignored(self, image_view, qtbot):
        panel = self._panel(image_view, qtbot)
        panel.value_clicked.emit(0, 99)
        panel.value_clicked.emit(9, 0)
        assert image_view.pos == [0, 0]

    def test_current_value_uses_highlight_colour(self, image_view, qtbot):
        from juxt.viewer import _HL_DEFAULT, _LINK_COLOR

        panel = self._panel(image_view, qtbot)
        colours = _anchor_colours(panel)
        cur_hrefs = {
            f"juxt:value/{i}/{image_view.pos[i]}"
            for i in range(len(image_view.axis_values))
        }
        hl = QColor(_HL_DEFAULT.color).name()
        assert cur_hrefs, "no axes to check"
        value_colours = {h: c for h, c in colours.items() if h.startswith("juxt:value/")}
        assert value_colours, "no value links found"
        for href, colour in value_colours.items():
            expected = hl if href in cur_hrefs else QColor(_LINK_COLOR).name()
            assert colour == expected, f"{href}: {colour} != {expected}"

    def test_custom_highlight_format_applies(self, image_view, qtbot):
        from juxt.settings import parse_highlight

        panel = self._panel(image_view, qtbot)
        panel.set_highlight(parse_highlight("bold #f80:«{}»"))
        panel.refresh(image_view)
        html = panel.toHtml()
        cur = image_view.axis_values[0][image_view.pos[0]]
        assert f"«{cur}»" in html
        colours = _anchor_colours(panel)
        assert colours[f"juxt:value/0/{image_view.pos[0]}"] == QColor("#f80").name()


# ---------------------------------------------------------------------------
# Info sidebar: value-list layout (inline vs. one-per-line)
# ---------------------------------------------------------------------------

@pytest.fixture
def value_list_view(qtbot):
    """ImageView with one short axis, one axis holding a spaced value, and
    one axis holding a long value — enough to exercise every list-mode trigger."""
    from itertools import product as _product

    from juxt.config import Config, _auto_keys
    from juxt.viewer import ImageView
    from PySide6.QtGui import QColor, QPixmap

    axes = {
        "short": ["A", "B"],
        "spacey": ["has space", "ok"],
        "long": ["x" * 25, "y"],
    }
    config = Config(
        template="fake/{short}_{spacey}_{long}.png",
        axes=axes, keys=_auto_keys(axes),
    )
    pixmaps = {}
    for i, j, k in _product(range(2), range(2), range(2)):
        pm = QPixmap(10, 10)
        pm.fill(QColor(10, 10, 10))
        pixmaps[(i, j, k)] = pm

    v = ImageView(config, pixmaps)
    qtbot.addWidget(v)
    return v


class TestInfoPanelValueList:
    def _panel(self, view, qtbot, separator="  ", list_larger_than=20):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.set_value_list_style(separator, list_larger_than)
        panel.refresh(view)
        return panel

    def test_short_values_stay_inline(self, value_list_view, qtbot):
        html = self._panel(value_list_view, qtbot).toHtml()
        # Both "A" and "B" links must appear on the same line (no <br> between them).
        a = html.index('href="juxt:value/0/0"')
        b = html.index('href="juxt:value/0/1"')
        assert "<br" not in html[a:b]

    def test_value_with_space_switches_to_list(self, value_list_view, qtbot):
        html = self._panel(value_list_view, qtbot).toHtml()
        a = html.index('href="juxt:value/1/0"')
        b = html.index('href="juxt:value/1/1"')
        assert "<br" in html[a:b]

    def test_value_over_threshold_switches_to_list(self, value_list_view, qtbot):
        html = self._panel(value_list_view, qtbot).toHtml()
        a = html.index('href="juxt:value/2/0"')
        b = html.index('href="juxt:value/2/1"')
        assert "<br" in html[a:b]

    def test_custom_threshold_keeps_long_value_inline(self, value_list_view, qtbot):
        html = self._panel(value_list_view, qtbot, list_larger_than=100).toHtml()
        a = html.index('href="juxt:value/2/0"')
        b = html.index('href="juxt:value/2/1"')
        assert "<br" not in html[a:b]

    def test_custom_separator_used_when_inline(self, value_list_view, qtbot):
        html = self._panel(value_list_view, qtbot, separator=" -- ").toHtml()
        a = html.index('href="juxt:value/0/0"')
        b = html.index('href="juxt:value/0/1"')
        assert "--" in html[a:b]

    def test_default_style_matches_settings_defaults(self):
        from juxt.settings import Settings

        assert Settings().sidebar_separator == "  "
        assert Settings().sidebar_list_larger_than == 20

    def test_axis_block_position_stable_across_path_lengths(self, value_list_view, qtbot):
        """The path line's reserved height must keep the first axis block in
        the same place whether the current path is short or long — this is
        what stops the sidebar from jumping when clicking a value link."""
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(160, 400)
        panel.show()
        qtbot.waitExposed(panel)

        def top_of_first_axis() -> float:
            doc = panel.document()
            cur = doc.find("short")  # first axis name's own text
            assert not cur.isNull()
            # cursorRect (not blockBoundingRect) is required here: the path
            # line and every axis row share one QTextDocument block (<br>
            # doesn't start a new block), so only a cursor-position query
            # reflects where a wrapped line actually landed.
            return panel.cursorRect(cur).top()

        value_list_view.pos = [0, 0, 0]  # long path: A_has space_xxxxxxxxxxxxxxxxxxxxxxxxx
        panel.refresh(value_list_view)
        top_long = top_of_first_axis()

        value_list_view.pos = [1, 1, 1]  # short path: B_ok_y
        panel.refresh(value_list_view)
        top_short = top_of_first_axis()

        assert top_long == top_short

    def test_reserved_lines_track_width(self, value_list_view, qtbot):
        """The reservation must actually track the widest possible path at
        the current width, not some arbitrary constant."""
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(160, 400)
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(value_list_view)

        narrow_lines = panel._worst_case_line_count(panel._worst_case_path(value_list_view))
        panel.resize(600, 400)
        panel.refresh(value_list_view)
        wide_lines = panel._worst_case_line_count(panel._worst_case_path(value_list_view))
        # A wider panel needs fewer wrapped lines for the same worst-case text.
        assert wide_lines <= narrow_lines

    def test_worst_case_path_uses_longest_value_per_axis(self, value_list_view):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        worst = panel._worst_case_path(value_list_view)
        assert worst == "fake/A_has space_" + "x" * 25 + ".png"


# ---------------------------------------------------------------------------
# Fit mode follows the image; :fit-off ends it
# ---------------------------------------------------------------------------

@pytest.fixture
def sized_view(qtbot):
    """Two sensors with differently sized images, and two dates sharing one
    size, in an 800x600 window."""
    from itertools import product as _product

    from juxt.config import Config, _auto_keys
    from juxt.viewer import ImageView
    from PySide6.QtGui import QColor, QPixmap

    axes = {"sensor": ["small", "big"], "date": ["d1", "d2"]}
    cfg = Config(template="f/{sensor}_{date}.png", axes=axes, keys=_auto_keys(axes))
    sizes = {0: (400, 200), 1: (1600, 1200)}
    pms = {}
    for i, j in _product(range(2), range(2)):
        pm = QPixmap(*sizes[i])
        pm.fill(QColor(10 + 60 * i, 10 + 60 * j, 10))
        pms[(i, j)] = pm
    v = ImageView(cfg, pms)
    qtbot.addWidget(v)
    v.resize(800, 600)
    v.show()
    return v


def _fitted_scale(v):
    """Scale at which the current image fills the viewport."""
    pm = v._item.pixmap()
    return min(v.viewport().width() / pm.width(), v.viewport().height() / pm.height())


class TestFitFollowsImage:
    def test_fit_mode_refits_when_the_image_size_changes(self, sized_view):
        v = sized_view
        v.fit_image()
        v._navigate(0, 1)                          # small -> big
        assert v.transform().m11() == pytest.approx(_fitted_scale(v), rel=0.02)
        assert v._fit == "image"

    def test_height_fit_is_kept_across_a_size_change(self, sized_view):
        v = sized_view
        v.fit_height()
        v._navigate(0, 1)
        assert v._fit == "height"
        assert v.transform().m11() == pytest.approx(v.viewport().height() / 1200, rel=0.02)

    def test_same_size_image_keeps_the_view(self, sized_view):
        """Flipping through congruent plots must not undo a pan."""
        v = sized_view
        v.fit_width()
        v._navigate(0, 1)                          # big image: taller than the viewport
        bar = v.verticalScrollBar()
        assert bar.maximum() > 0
        bar.setValue(bar.maximum())                # the bottom, not where a refit centres it
        panned = bar.value()
        assert panned != (bar.minimum() + bar.maximum()) // 2
        v._navigate(1, 1)                          # d1 -> d2, same size
        assert bar.value() == panned

    def test_no_refit_when_fit_is_off(self, sized_view):
        v = sized_view
        v.fit_image()
        v.fit_off()
        scale = v.transform().m11()
        v._navigate(0, 1)
        assert v.transform().m11() == scale

    def test_zoomed_view_is_not_refitted(self, sized_view):
        v = sized_view
        v.fit_image()
        v.set_zoom(100)
        v._navigate(0, 1)
        assert v.transform().m11() == pytest.approx(1.0)


class TestGridCommands:
    def _type(self, v, qtbot, text):
        qtbot.keyClicks(v, text)
        qtbot.keyClick(v, Qt.Key.Key_Return)

    def test_grid_off_leaves_grid_view(self, sized_view):
        v = sized_view
        v._cmd_execute("grid sensor")
        assert v._grid_axis is not None
        v._cmd_execute("grid-off")
        assert v._grid_axis is None and v._grid_widget is None
        assert v._flash_msg == "grid off"

    def test_grid_off_outside_grid_says_so(self, sized_view):
        v = sized_view
        v._cmd_execute("grid-off")
        assert v._flash_msg == "not in grid mode"

    def test_grid_off_is_listed_and_typeable(self, sized_view, qtbot):
        from juxt.viewer import _COMMAND_HELP, _COMMANDS

        assert "grid-off" in _COMMANDS and "grid-off" in _COMMAND_HELP
        v = sized_view
        v._cmd_execute("grid sensor")
        self._type(v, qtbot, ":grid-off")
        assert v._grid_axis is None

    def test_one_by_one_layout_gives_a_single_pane(self, sized_view):
        v = sized_view
        v._cmd_execute("grid sensor")
        v._cmd_execute("grid-layout 1x1")
        assert v._grid_layout == (1, 1)
        assert len(v._grid_slots) == 1
        assert (v._grid_widget._rows, v._grid_widget._cols) == (1, 1)

    def test_layout_typed_in_one_line_is_applied(self, sized_view, qtbot):
        """The space after the command name opens its argument, so the whole
        line works as typed instead of being dropped as an unknown command."""
        v = sized_view
        v._cmd_execute("grid sensor")
        self._type(v, qtbot, ":grid-layout 1x1")
        assert v._grid_layout == (1, 1)
        assert len(v._grid_slots) == 1

    def test_layout_typed_with_a_fuzzy_name(self, sized_view, qtbot):
        v = sized_view
        v._cmd_execute("grid sensor")
        self._type(v, qtbot, ":gridl 1x2")
        assert v._grid_layout == (1, 2)

    def test_space_after_a_command_opens_its_argument(self, sized_view, qtbot):
        v = sized_view
        qtbot.keyClicks(v, ":grid-layout ")
        assert v._cmd["phase"] == "arg" and v._cmd["verb"] == "grid-layout"
        assert v._cmd["query"] == ""

    def test_space_after_a_command_without_arguments_is_ignored(self, sized_view, qtbot):
        v = sized_view
        qtbot.keyClicks(v, ":fullscreen ")
        assert v._cmd["phase"] == "verb" and v._cmd["query"] == "fullscreen"

    def test_leading_space_is_ignored(self, sized_view, qtbot):
        v = sized_view
        qtbot.keyClicks(v, ": ")
        assert v._cmd["phase"] == "verb" and v._cmd["query"] == ""

    def test_empty_layout_argument_shows_the_expected_format(self, main_window, qtbot):
        v = main_window.view
        qtbot.keyClicks(v, ":grid-layout ")
        main_window._update_status()
        lbl = main_window._status_label
        text = lbl._full_html if lbl._full_html is not None else lbl._full_plain
        assert "NxM" in text

    def test_format_hint_stays_while_the_layout_is_incomplete(self, main_window, qtbot):
        v = main_window.view
        qtbot.keyClicks(v, ":grid-layout 2x")
        main_window._update_status()
        lbl = main_window._status_label
        text = lbl._full_html if lbl._full_html is not None else lbl._full_plain
        assert "NxM" in text


class TestCommandNamesSkipHyphens:
    def test_fuzzy_matches_across_the_hyphen(self):
        from juxt.fuzzy import fuzzy_filter
        from juxt.viewer import _COMMANDS

        assert fuzzy_filter("gridl", _COMMANDS)[0] == "grid-layout"
        assert fuzzy_filter("fith", _COMMANDS)[0] == "fit-height"

    def test_strict_prefix_matches_across_the_hyphen(self):
        from juxt.fuzzy import prefix_filter
        from juxt.viewer import _COMMANDS

        assert prefix_filter("gridl", _COMMANDS) == ["grid-layout"]

    def test_gridl_reaches_grid_layout_with_either_matcher(self, sized_view):
        v = sized_view
        for fuzzy in (True, False):
            v._seek_fuzzy = fuzzy
            v._cmd = {"phase": "verb", "query": "gridl", "cursor": 0}
            assert v._cmd_candidates()[0] == "grid-layout"


class TestVersionCommand:
    def test_flashes_the_installed_version(self, sized_view):
        import juxt

        v = sized_view
        v._cmd_execute("version")
        assert v._flash_msg == f"juxt {juxt.__version__}"

    def test_typed_through_the_prompt(self, sized_view, qtbot):
        import juxt

        v = sized_view
        qtbot.keyClicks(v, ":version")
        qtbot.keyClick(v, Qt.Key.Key_Return)
        assert v._flash_msg == f"juxt {juxt.__version__}"
        assert v._cmd is None

    def test_is_a_listed_command_with_help(self, sized_view):
        from juxt.viewer import _COMMAND_HELP, _COMMANDS

        assert "version" in _COMMANDS and "version" in _COMMAND_HELP
        v = sized_view
        v._cmd = {"phase": "verb", "query": "vers", "cursor": 0}
        assert v._cmd_candidates() == ["version"]


class TestFitOff:
    def test_fit_off_keeps_the_current_zoom(self, sized_view):
        v = sized_view
        v.fit_image()
        scale = v.transform().m11()
        v.fit_off()
        assert v._fit is None
        assert v.transform().m11() == scale

    def test_fit_off_survives_a_resize(self, sized_view):
        v = sized_view
        v.fit_image()
        v.fit_off()
        scale = v.transform().m11()
        v.resize(500, 400)
        assert v.transform().m11() == scale

    def test_fit_keeps_following_resizes_until_turned_off(self, sized_view):
        v = sized_view
        v.fit_image()
        v.resize(500, 400)
        assert v.transform().m11() == pytest.approx(_fitted_scale(v), rel=0.02)

    def test_command_turns_it_off(self, sized_view):
        v = sized_view
        v.fit_image()
        v._cmd_execute("fit-off")
        assert v._fit is None
        assert v._flash_msg == "fit off"

    def test_typed_through_the_prompt(self, sized_view, qtbot):
        """Keys, not _cmd_execute(): the prompt is where a command can be lost."""
        v = sized_view
        v.fit_image()
        qtbot.keyClicks(v, ":fit-off")
        qtbot.keyClick(v, Qt.Key.Key_Return)
        assert v._fit is None
        assert v._cmd is None

    def test_fit_turns_it_back_on(self, sized_view):
        v = sized_view
        v.fit_off()
        v._cmd_execute("fit")
        assert v._fit == "image"

    def test_fit_off_is_a_listed_command(self, sized_view):
        from juxt.viewer import _COMMANDS

        assert "fit-off" in _COMMANDS
        v = sized_view
        v._cmd = {"phase": "verb", "query": "fit-o", "cursor": 0}
        assert v._cmd_candidates()[0] == "fit-off"

    def test_fit_off_reaches_every_grid_cell(self, sized_view):
        v = sized_view
        v._enter_grid(0, None, (1, 2))
        v._grid_widget.fit_image()
        assert all(c._fit == "image" for c in v._grid_widget._cells)
        v._cmd_execute("fit-off")
        assert all(c._fit is None for c in v._grid_widget._cells)


# ---------------------------------------------------------------------------
# Window title
# ---------------------------------------------------------------------------

_HOME = "/home/cmoldenh"
_LONG_TEMPLATE = (_HOME + "/shares/exchange/staff/cmoldenh/work/organic_soils/sm_vs_e5l/"
                  "{filter}/{length}/{season}/{plot}.png")
# One directory more than fits the title cap once the home directory is a "~".
_OVERLONG_TEMPLATE = _LONG_TEMPLATE.replace("/work/", "/work/projects/2026/")


def _title_config(template, remote=None, **axes):
    from juxt.config import Config

    axes = axes or {"plot": ["stats", "soc_violin"]}
    return Config(template=template, axes=axes, keys={}, remote=remote)


class TestWindowTitle:
    PREFIX = "juxt | "

    def _title(self, template="/data/a/{plot}.png", **kw):
        from juxt.viewer import _window_title

        kw.setdefault("home", _HOME)
        return _window_title(_title_config(template), **kw)

    def test_short_template_is_shown_whole(self):
        assert self._title("/data/a/{plot}.png") == "juxt | /data/a/{plot}.png"

    def test_home_directory_becomes_a_tilde(self):
        assert self._title(_HOME + "/work/{plot}.png") == "juxt | ~/work/{plot}.png"

    def test_a_sibling_of_the_home_directory_is_not_collapsed(self):
        assert self._title(_HOME + "x/work/{plot}.png") == "juxt | " + _HOME + "x/work/{plot}.png"

    def test_windows_home_is_collapsed_too(self):
        from juxt.viewer import _collapse_home

        assert _collapse_home("C:\\Users\\me\\work\\{plot}.png", "C:\\Users\\me") == "~\\work\\{plot}.png"

    def test_session_name_replaces_everything(self):
        assert self._title(_LONG_TEMPLATE, session_name="soc run", pinned=["plot"]) == "juxt | soc run"

    def test_a_template_that_just_fits_is_not_cut(self):
        assert self._title(_LONG_TEMPLATE).startswith("juxt | ~/shares/")

    def test_long_template_keeps_its_end_and_marks_the_cut(self):
        title = self._title(_OVERLONG_TEMPLATE)
        assert title.startswith("juxt | …/")
        assert title.endswith("/{season}/{plot}.png")

    def test_the_first_component_shown_after_a_cut_is_whole(self):
        title = self._title(_OVERLONG_TEMPLATE)
        assert title.startswith("juxt | …/"), "nothing was cut"
        shown = title[len(self.PREFIX) + 2:]               # after "…/"
        assert "/" + shown in _OVERLONG_TEMPLATE

    @pytest.mark.parametrize("depth", range(1, 40))
    def test_text_after_the_prefix_never_passes_the_cap(self, depth):
        from juxt.viewer import _TITLE_MAX

        template = "/" + "/".join(f"dir{i:02d}" for i in range(depth)) + "/{plot}.png"
        title = self._title(template, pinned=["plot"])
        assert len(title) - len(self.PREFIX) <= _TITLE_MAX
        assert "{plot}.png" in title

    def test_your_command(self):
        title = self._title(_LONG_TEMPLATE, pinned=["plot"])
        assert title == ("juxt | …/work/organic_soils/sm_vs_e5l/"
                         "{filter}/{length}/{season}/{plot}.png [plot: stats, soc_violin]")

    def test_pins_are_not_shown_unless_asked_for(self):
        assert "[" not in self._title()

    def test_pins_show_the_values_the_axis_holds_now(self):
        from juxt.viewer import _window_title

        cfg = _title_config("/data/{plot}.png", plot=["a", "b", "c"])
        assert _window_title(cfg, pinned=["plot"], home="/h") == "juxt | /data/{plot}.png [plot: a, b, c]"
        cfg.axes["plot"] = ["a"]                            # e.g. after a reload
        assert _window_title(cfg, pinned=["plot"], home="/h") == "juxt | /data/{plot}.png [plot: a]"

    def test_a_pinned_axis_that_is_gone_is_left_out(self):
        assert "[" not in self._title(pinned=["season"])

    def test_several_pinned_axes_are_joined(self):
        from juxt.viewer import _window_title

        cfg = _title_config("/d/{a}/{b}.png", a=["x"], b=["y", "z"])
        assert _window_title(cfg, pinned=["a", "b"], home="/h").endswith("[a: x; b: y, z]")

    def test_a_long_pin_list_is_cut_and_the_path_survives(self):
        from juxt.viewer import _TITLE_MAX, _TITLE_PINS_MAX, _window_title

        cfg = _title_config(_LONG_TEMPLATE, plot=[f"value_{i}" for i in range(30)])
        title = _window_title(cfg, pinned=["plot"], home=_HOME)
        suffix = title[title.index("[plot"):]
        assert len(suffix) <= _TITLE_PINS_MAX + 2
        assert suffix.endswith("…]")
        assert title.endswith("…]") and "{plot}.png" in title
        assert len(title) - len(self.PREFIX) <= _TITLE_MAX

    def test_remote_host_leads_and_counts_towards_the_cap(self):
        from juxt.config import RemoteConfig
        from juxt.viewer import _TITLE_MAX, _window_title

        cfg = _title_config(_LONG_TEMPLATE, remote=RemoteConfig(host="hpc.example.org"))
        title = _window_title(cfg, home=_HOME)
        assert title.startswith("juxt | hpc.example.org: ")
        assert len(title) - len(self.PREFIX) <= _TITLE_MAX

    def test_remote_path_keeps_its_home_prefix(self):
        """The remote home is unknown, so only a local template collapses to ~."""
        from juxt.config import RemoteConfig
        from juxt.viewer import _window_title

        cfg = _title_config(_HOME + "/{plot}.png", remote=RemoteConfig(host="hpc"))
        assert _window_title(cfg, home=_HOME) == "juxt | hpc: " + _HOME + "/{plot}.png"


class TestElidePath:
    def test_fits_is_returned_unchanged(self):
        from juxt.viewer import _elide_path

        assert _elide_path("/a/b/c.png", 10) == "/a/b/c.png"

    def test_cut_starts_at_a_separator(self):
        from juxt.viewer import _elide_path

        out = _elide_path("/aaa/bbb/ccc/ddd.png", 14)
        assert out == "…/ccc/ddd.png"
        assert len(out) <= 14

    def test_a_cut_that_falls_between_components_keeps_the_whole_one(self):
        from juxt.viewer import _elide_path

        assert _elide_path("xx/yyyy/zzzz", 11) == "…/yyyy/zzzz"

    def test_one_component_longer_than_the_room_is_cut_inside(self):
        from juxt.viewer import _elide_path

        out = _elide_path("a" * 50, 10)
        assert out == "…" + "a" * 9

    def test_backslash_separators_work_too(self):
        from juxt.viewer import _elide_path

        path = "C:\\aaa\\bbb\\ccc.png"
        assert _elide_path(path, 12) == "…/ccc.png"          # "bbb" no longer fits
        assert _elide_path(path, 13) == "…/bbb\\ccc.png"      # a cut between components


class TestWindowTitleOnTheWindow:
    def _window(self, config, pixmaps, qtbot, **kw):
        from juxt.viewer import MainWindow

        w = MainWindow(config, pixmaps, watch=False, **kw)
        qtbot.addWidget(w)
        return w

    def test_pinned_axes_appear_in_the_title(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, pinned_axes=["sensor"])
        assert w.windowTitle().endswith("[sensor: A, B]")

    def test_title_follows_the_config_after_a_reload(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, pinned_axes=["sensor"])
        w.view.config.axes["sensor"] = ["A"]
        w.view.config_changed.emit()
        assert w.windowTitle().endswith("[sensor: A]")

    def test_no_pins_no_suffix(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot)
        assert "[" not in w.windowTitle()

    def test_name_flag_still_wins(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, session_name="mine", pinned_axes=["sensor"])
        assert w.windowTitle() == "juxt | mine"


# ---------------------------------------------------------------------------
# Info sidebar: row packing, truncation and expandable axes
# ---------------------------------------------------------------------------

@pytest.fixture
def packed_view(qtbot):
    """Axes whose inline values need several rows at sidebar widths: eleven
    short values, plus a few mid-sized ones (under the one-per-row
    threshold, so they stay inline)."""
    from itertools import product as _product

    from juxt.config import Config, _auto_keys
    from juxt.viewer import ImageView
    from PySide6.QtGui import QColor, QPixmap

    axes = {
        "sensor": ["ASCAT", "SMAP", "SMOS", "AMSR2", "SMMR", "SSMI", "TMI", "WindSat",
                   "ERS", "MetOp", "CCI"],
        "date": ["2024-03-15", "2024-03-16", "2024-03-17", "2024-03-18"],
    }
    config = Config(template="fake/{sensor}_{date}.png", axes=axes, keys=_auto_keys(axes))
    pixmaps = {}
    for i, j in _product(range(len(axes["sensor"])), range(len(axes["date"]))):
        pm = QPixmap(10, 10)
        pm.fill(QColor(10, 10, 10))
        pixmaps[(i, j)] = pm
    v = ImageView(config, pixmaps)
    qtbot.addWidget(v)
    return v


def _value_lines(panel, view):
    """Yield (href, start line top, last char line top) for every value link,
    so a test can tell whether a value sits on one visual line."""
    for i, vals in enumerate(view.axis_values):
        for j in range(len(vals)):
            span = panel._fragment_span_for_href(f"juxt:value/{i}/{j}")
            assert span is not None
            start, end = span
            first = QTextCursor(panel.document())
            first.setPosition(start)
            last = QTextCursor(panel.document())
            last.setPosition(max(start, end - 1))
            yield (f"juxt:value/{i}/{j}",
                   panel.cursorRect(first).top(), panel.cursorRect(last).top())


class TestInfoPanelRows:
    def _panel(self, view, qtbot, width):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(width, 400)
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(view)
        return panel

    @pytest.mark.parametrize("width", [150, 180, 220, 260, 301, 333, 400, 517])
    def test_no_value_is_broken_across_lines(self, packed_view, qtbot, width):
        panel = self._panel(packed_view, qtbot, width)
        for href, first_top, last_top in _value_lines(panel, packed_view):
            assert first_top == last_top, f"{href} is split across lines at width {width}"

    def test_values_wrap_between_rows_not_inside_values(self, packed_view, qtbot):
        narrow = self._panel(packed_view, qtbot, 200)
        wide = self._panel(packed_view, qtbot, 600)
        # The narrow panel needs more rows for the same values.
        assert narrow.toHtml().count("<br") > wide.toHtml().count("<br")

    def test_packing_is_stable_when_the_scrollbar_appears(self, packed_view, qtbot):
        """Rows are laid out for the width in force; a scrollbar showing up
        under that layout narrows it, and the values must still not break."""
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(170, 120)      # short enough that the document needs a scrollbar
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(packed_view)
        assert panel.verticalScrollBar().maximum() > 0
        for href, first_top, last_top in _value_lines(panel, packed_view):
            assert first_top == last_top, href

    def test_value_wider_than_a_row_is_truncated_not_broken(self, value_list_view, qtbot):
        # "x" * 25 is over the threshold, so it is listed one per row, at a
        # width where 25 characters cannot fit.
        panel = self._panel(value_list_view, qtbot, 150)
        start, end = panel._fragment_span_for_href("juxt:value/2/0")
        cursor = QTextCursor(panel.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        shown = cursor.selectedText()
        assert shown.endswith("…")
        assert len(shown) < 25
        for href, first_top, last_top in _value_lines(panel, value_list_view):
            assert first_top == last_top, href

    @staticmethod
    def _hover(panel, href, monkeypatch):
        """Send the viewport a tooltip event over the link *href*; return the
        tooltip text the panel asked to show ("" when it asked for none).

        Qt's own tooltip state is not read back: on Windows hideText() fades
        out, so isVisible() and text() still report the previous tooltip.
        """
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication

        shown: list[str] = []

        class _RecordingToolTip:
            @staticmethod
            def showText(_pos, text, _widget=None):
                shown.append(text)

            @staticmethod
            def hideText():
                shown.append("")

        monkeypatch.setattr("juxt.viewer.QToolTip", _RecordingToolTip)
        start, _end = panel._fragment_span_for_href(href)
        cursor = QTextCursor(panel.document())
        cursor.setPosition(start + 1)
        pos = panel.cursorRect(cursor).center()
        QApplication.sendEvent(
            panel.viewport(),
            QHelpEvent(QEvent.Type.ToolTip, pos, panel.viewport().mapToGlobal(pos)),
        )
        assert shown, "the panel did not answer the tooltip event"
        return shown[-1]

    def test_truncated_value_shows_its_full_text_on_hover(self, value_list_view, qtbot, monkeypatch):
        panel = self._panel(value_list_view, qtbot, 150)
        assert self._hover(panel, "juxt:value/2/0", monkeypatch) == "x" * 25

    def test_value_that_fits_has_no_tooltip(self, value_list_view, qtbot, monkeypatch):
        panel = self._panel(value_list_view, qtbot, 150)
        assert self._hover(panel, "juxt:value/2/1", monkeypatch) == ""      # "y"

    def test_tooltip_goes_away_when_the_panel_is_widened(self, value_list_view, qtbot, monkeypatch):
        panel = self._panel(value_list_view, qtbot, 150)
        panel.resize(600, 400)
        panel.refresh(value_list_view)
        assert self._hover(panel, "juxt:value/2/0", monkeypatch) == ""

    def test_value_that_fits_is_not_truncated(self, value_list_view, qtbot):
        panel = self._panel(value_list_view, qtbot, 600)
        start, end = panel._fragment_span_for_href("juxt:value/2/0")
        cursor = QTextCursor(panel.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        assert cursor.selectedText() == "x" * 25

    def test_truncated_value_is_still_clickable(self, value_list_view, qtbot):
        panel = self._panel(value_list_view, qtbot, 150)
        assert 'href="juxt:value/2/0"' in panel.toHtml()

    def test_highlight_delimiters_count_towards_the_row_width(self, value_list_view, qtbot):
        from juxt.settings import parse_highlight

        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.set_highlight(parse_highlight("bold #f80:<<<{}>>>"))
        panel.resize(150, 400)
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(value_list_view)
        for href, first_top, last_top in _value_lines(panel, value_list_view):
            assert first_top == last_top, href


class TestPackRows:
    def test_fills_a_row_before_starting_the_next(self):
        from juxt.viewer import _pack_rows

        assert _pack_rows([10, 10, 10, 10], sep_w=2, limit=26) == [[0, 1], [2, 3]]

    def test_separator_width_counts(self):
        from juxt.viewer import _pack_rows

        # 10 + 10 fits in 20 only without a separator between them.
        assert _pack_rows([10, 10], sep_w=0, limit=20) == [[0, 1]]
        assert _pack_rows([10, 10], sep_w=2, limit=20) == [[0], [1]]

    def test_oversize_item_gets_its_own_row(self):
        from juxt.viewer import _pack_rows

        assert _pack_rows([5, 50, 5], sep_w=1, limit=20) == [[0], [1], [2]]

    def test_empty(self):
        from juxt.viewer import _pack_rows

        assert _pack_rows([], sep_w=1, limit=20) == []


class TestInfoPanelExpand:
    def _panel(self, view, qtbot, width=600):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(width, 400)
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(view)
        return panel

    @staticmethod
    def _one_per_row(panel, axis, n):
        html = panel.toHtml()
        a = html.index(f'href="juxt:value/{axis}/0"')
        b = html.index(f'href="juxt:value/{axis}/{n - 1}"')
        return html[a:b].count("<br") >= n - 1

    def test_axis_href_roundtrip(self):
        from juxt.viewer import _axis_href, _parse_axis_href, _parse_value_href

        assert _parse_axis_href(_axis_href(3)) == 3
        assert _parse_axis_href("juxt:value/1/2") is None
        assert _parse_value_href(_axis_href(3)) is None

    def test_every_axis_header_is_a_link(self, packed_view, qtbot):
        html = self._panel(packed_view, qtbot).toHtml()
        for i in range(len(packed_view.axis_names)):
            assert f'href="juxt:axis/{i}"' in html

    def test_inline_axis_starts_collapsed(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        assert not self._one_per_row(panel, 0, 11)
        assert "▸ sensor" in panel.toPlainText()

    def test_toggle_expands_to_one_value_per_row(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        panel.toggle_axis(0)
        assert self._one_per_row(panel, 0, 11)
        assert "▾ sensor" in panel.toPlainText()

    def test_toggle_twice_restores_the_inline_layout(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        before = panel.toPlainText()
        panel.toggle_axis(0)
        panel.toggle_axis(0)
        assert panel.toPlainText() == before

    def test_toggle_leaves_other_axes_alone(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        panel.toggle_axis(0)
        assert not self._one_per_row(panel, 1, 4)

    def test_automatically_listed_axis_can_be_collapsed(self, value_list_view, qtbot):
        panel = self._panel(value_list_view, qtbot)
        assert self._one_per_row(panel, 1, 2)       # "has space" lists itself
        panel.toggle_axis(1)
        assert not self._one_per_row(panel, 1, 2)
        panel.toggle_axis(1)
        assert self._one_per_row(panel, 1, 2)

    def test_expansion_survives_navigation(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        panel.toggle_axis(0)
        packed_view._navigate(1, 1)
        panel.refresh(packed_view)
        assert self._one_per_row(panel, 0, 11)

    def test_returning_to_the_automatic_state_follows_the_threshold(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        panel.toggle_axis(0)
        panel.toggle_axis(0)
        assert "sensor" not in panel._expanded
        panel.set_value_list_style("  ", 3)    # now every "sensor" value is over the threshold
        panel.refresh(packed_view)
        assert self._one_per_row(panel, 0, 11)

    def test_expanded_values_are_still_links_that_navigate(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        panel.value_clicked.connect(packed_view.goto_value)
        panel.toggle_axis(0)
        panel.value_clicked.emit(0, 4)
        assert packed_view.pos[0] == 4

    def test_toggle_before_first_refresh_is_ignored(self, qtbot):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.toggle_axis(0)        # nothing to toggle yet; must not raise

    def test_toggle_out_of_range_is_ignored(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        before = panel.toPlainText()
        panel.toggle_axis(99)
        assert panel.toPlainText() == before

    def test_clicking_a_header_toggles_it(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        span = panel._fragment_span_for_href("juxt:axis/0")
        cursor = QTextCursor(panel.document())
        cursor.setPosition(span[0] + 2)
        pos = panel.cursorRect(cursor).center()
        qtbot.mouseClick(panel.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        assert self._one_per_row(panel, 0, 11)

    def test_dragging_across_a_header_selects_instead_of_toggling(self, packed_view, qtbot):
        panel = self._panel(packed_view, qtbot)
        span = panel._fragment_span_for_href("juxt:axis/0")
        cursor = QTextCursor(panel.document())
        cursor.setPosition(span[0] + 2)
        start = panel.cursorRect(cursor).center()
        cursor.setPosition(span[1])
        end = panel.cursorRect(cursor).center()
        qtbot.mousePress(panel.viewport(), Qt.MouseButton.LeftButton, pos=start)
        qtbot.mouseMove(panel.viewport(), end)
        qtbot.mouseRelease(panel.viewport(), Qt.MouseButton.LeftButton, pos=end)
        assert not self._one_per_row(panel, 0, 11)
        assert panel.textCursor().hasSelection()

    def test_toggle_does_not_chase_the_navigation_target(self, scrollable_view, qtbot):
        """A toggle is not a navigation: a leftover scroll-to target from the
        last key press must not yank the sidebar away from the header."""
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(250, 300)
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(scrollable_view)
        sb = panel.verticalScrollBar()
        sb.setValue(sb.maximum())
        bottom = sb.value()
        scrollable_view.pos = [0, 0]
        scrollable_view._sidebar_target = (1, 0)     # stale: first date value, far above
        panel.toggle_axis(0)
        assert sb.value() == bottom


def _anchor_colours(panel) -> dict[str, str]:
    """Map every anchor href in the panel document to its rendered colour."""
    colours: dict[str, str] = {}
    block = panel.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fmt = it.fragment().charFormat()
            if fmt.isAnchor() and fmt.anchorHref():
                colours[fmt.anchorHref()] = fmt.foreground().color().name()
            it += 1
        block = block.next()
    return colours


# ---------------------------------------------------------------------------
# Info sidebar: scroll position on refresh
# ---------------------------------------------------------------------------

@pytest.fixture
def scrollable_view(qtbot):
    """ImageView with an axis long enough to need a sidebar scrollbar at
    typical panel sizes."""
    from itertools import product as _product

    from juxt.config import Config, _auto_keys
    from juxt.viewer import ImageView
    from PySide6.QtGui import QColor, QPixmap

    # Long enough (>20 chars) to switch the date axis to one-value-per-line
    # rendering, so each value gets a distinct, predictable line position --
    # short values pack into one wrapped inline block instead.
    axes = {"sensor": ["A", "B"], "date": [f"date_value_number_{i:03d}" for i in range(60)]}
    config = Config(template="fake/{sensor}_{date}.png", axes=axes, keys=_auto_keys(axes))
    pixmaps = {}
    for i, j in _product(range(2), range(60)):
        pm = QPixmap(10, 10)
        pm.fill(QColor(10, 10, 10))
        pixmaps[(i, j)] = pm

    v = ImageView(config, pixmaps)
    qtbot.addWidget(v)
    return v


class TestInfoPanelScroll:
    def _panel(self, view, qtbot, width=250, height=300):
        from juxt.viewer import InfoPanel

        panel = InfoPanel()
        qtbot.addWidget(panel)
        panel.resize(width, height)
        panel.show()
        qtbot.waitExposed(panel)
        panel.refresh(view)
        return panel

    def test_repeated_refresh_does_not_duplicate_content(self, scrollable_view, qtbot):
        """Regression: setHtml() growing the document past the point where
        a scrollbar is needed can trigger a synchronous resizeEvent() that
        reenters refresh() while the outer call is still building the
        document, corrupting it into two copies of the content."""
        panel = self._panel(scrollable_view, qtbot)
        first = panel.toPlainText()
        panel.refresh(scrollable_view)
        assert panel.toPlainText() == first

    def test_plain_refresh_preserves_scroll_position(self, scrollable_view, qtbot):
        panel = self._panel(scrollable_view, qtbot)
        sb = panel.verticalScrollBar()
        assert sb.maximum() > 0, "test needs content tall enough to scroll"
        sb.setValue(sb.maximum())
        bottom = sb.value()

        scrollable_view.pos = [0, 5]
        scrollable_view._sidebar_target = None
        panel.refresh(scrollable_view)
        assert sb.value() == bottom

    def test_forced_target_already_visible_does_not_move(self, scrollable_view, qtbot):
        panel = self._panel(scrollable_view, qtbot)
        sb = panel.verticalScrollBar()
        sb.setValue(sb.maximum())
        bottom = sb.value()

        # One step from the second-to-last to the last date value, while
        # already scrolled to the bottom: both are near the end, so the
        # target is already visible and the scroll position must not move.
        scrollable_view.pos = [0, 59]
        scrollable_view._sidebar_target = (1, 59)
        panel.refresh(scrollable_view)
        assert sb.value() == bottom

    def test_forced_target_out_of_view_scrolls_to_reveal_it(self, scrollable_view, qtbot):
        panel = self._panel(scrollable_view, qtbot)
        sb = panel.verticalScrollBar()
        sb.setValue(sb.maximum())
        bottom = sb.value()

        # Jump to the first date value while scrolled to the bottom.
        scrollable_view.pos = [0, 0]
        scrollable_view._sidebar_target = (1, 0)
        panel.refresh(scrollable_view)
        assert sb.value() < bottom

    def test_stale_target_not_matching_pos_is_ignored(self, scrollable_view, qtbot):
        """_sidebar_target is only honoured when it still matches pos --
        guards a stale target from an earlier navigation against surviving
        into an unrelated refresh."""
        panel = self._panel(scrollable_view, qtbot)
        sb = panel.verticalScrollBar()
        sb.setValue(sb.maximum())
        bottom = sb.value()

        scrollable_view.pos = [0, 3]
        scrollable_view._sidebar_target = (1, 0)  # doesn't match pos[1] == 3
        panel.refresh(scrollable_view)
        assert sb.value() == bottom


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

    def test_closing_a_level_clears_the_hint_list(self, image_view, nested_plot_dir):
        # Tab on a trailing placeholder adds the separator; the values it
        # stands for must not stay on offer as if a choice remained.
        base = nested_plot_dir.as_posix()
        self._open(image_view, f"{base}/{{sensor}}/{{overpass}}")
        image_view._complete_path()
        assert image_view._cmd["query"] == f"{base}/{{sensor}}/{{overpass}}/"
        assert image_view._tab_matches == []
        # The next Tab descends and lists the level below.
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
# Status bar: candidate highlighting & placeholder colouring
# ---------------------------------------------------------------------------

@pytest.fixture
def main_window(viewer_config, mini_pixmaps, qtbot):
    """MainWindow wired to the shared 2x2 test config."""
    from juxt.viewer import MainWindow

    w = MainWindow(viewer_config, mini_pixmaps, watch=False)
    qtbot.addWidget(w)
    w.resize(1400, 800)
    return w


class TestStatusCandidateHighlight:
    def _status(self, main_window) -> str:
        main_window._update_status()
        lbl = main_window._status_label
        return lbl._full_html if lbl._full_html is not None else lbl._full_plain

    def test_candidate_html_highlights_cursor(self):
        from juxt.viewer import _HL_DEFAULT_CANDIDATES, _candidate_html

        html = _candidate_html(["alpha", "beta"], 1)
        assert f'color:{_HL_DEFAULT_CANDIDATES.color}' in html
        assert ">[beta]</span>" in html
        assert "[alpha]" not in html

    def test_candidate_html_honours_custom_format(self):
        from juxt.settings import parse_highlight
        from juxt.viewer import _candidate_html

        hl = parse_highlight("bold red:» {} «")
        html = _candidate_html(["alpha", "beta"], 0, hl)
        assert "color:red" in html and "font-weight:bold" in html
        assert "»&nbsp;alpha&nbsp;«" in html

    def test_candidate_html_raw_template(self):
        from juxt.settings import parse_highlight
        from juxt.viewer import _candidate_html

        hl = parse_highlight('html:<span style="background:#334">[{}]</span>')
        html = _candidate_html(["alpha"], 0, hl)
        assert html == '<span style="background:#334">[alpha]</span>'

    def test_candidate_html_without_cursor(self):
        from juxt.viewer import _candidate_html

        html = _candidate_html(["alpha", "beta"], -1)
        assert html == "alpha&nbsp;&nbsp;beta"

    def test_seek_candidates_use_highlight_colour(self, main_window):
        from juxt.viewer import _HL_DEFAULT_CANDIDATES

        main_window.view._sel = {
            "phase": "value", "query": "", "axis_idx": 0, "cursor": 1,
        }
        status = self._status(main_window)
        vals = main_window.view.axis_values[0]
        colour = _HL_DEFAULT_CANDIDATES.color
        assert f'color:{colour}' in status
        assert f">[{vals[1]}]</span>" in status
        assert f"[{vals[0]}]" not in status

    def test_configured_candidate_format_is_used(self, main_window):
        from juxt.settings import parse_highlight

        main_window._hl_candidates = parse_highlight("#0f0:<{}>")
        main_window.view._sel = {
            "phase": "value", "query": "", "axis_idx": 0, "cursor": 0,
        }
        status = self._status(main_window)
        vals = main_window.view.axis_values[0]
        assert "color:#0f0" in status
        assert f"&lt;{vals[0]}&gt;" in status

    def test_command_candidates_use_highlight_colour(self, main_window):
        from juxt.viewer import _HL_DEFAULT_CANDIDATES

        main_window.view._cmd = {"phase": "verb", "query": "fit", "cursor": 0}
        status = self._status(main_window)
        assert f'color:{_HL_DEFAULT_CANDIDATES.color}' in status
        assert ">[fit]</span>" in status

    def test_spaces_are_preserved_as_nbsp(self, main_window):
        main_window.view._sel = {
            "phase": "value", "query": "", "axis_idx": 0, "cursor": 0,
        }
        assert "&nbsp;" in self._status(main_window)

    def test_markup_characters_are_escaped(self, main_window):
        main_window.view._cmd = {
            "phase": "arg", "verb": "pattern", "query": "a<b>&c",
            "caret": 6, "cursor": -1,
        }
        status = self._status(main_window)
        assert "a&lt;b&gt;&amp;c" in status


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
# Grid panes — the layout caps how many cells exist; each pane holds its own
# value and the grid axis scrolls only the focused one
# ---------------------------------------------------------------------------

class TestGridPanes:
    @staticmethod
    def _view(qtbot, n_sensors=4):
        """ImageView over an axis with more values than a small grid can show."""
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
    def _panes(v):
        """(labels per pane, focused label)."""
        vals = v.axis_values[v._grid_axis]
        labels = [vals[vi] for vi in v._grid_slots]
        return labels, vals[v._grid_slots[v._grid_focus]]

    @staticmethod
    def _candidates(v):
        vals = v.axis_values[v._grid_axis]
        return [vals[i] for i in v._grid_candidates()]

    # -- layout is a hard cap ------------------------------------------------

    def test_layout_caps_the_number_of_cells(self, qtbot):
        """A 2x1 layout over 3 values must not silently become a 3x1 grid."""
        v = self._view(qtbot, n_sensors=3)
        v._enter_grid(0, None, (2, 1))
        assert v._grid_widget.capacity() == 2
        assert v._grid_widget.layout().rowCount() == 2
        assert v._grid_widget.layout().columnCount() == 1

    def test_first_values_fill_the_panes(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        assert self._panes(v) == (["A", "B"], "A")

    # -- the axis scrolls the focused pane only ------------------------------

    def test_stepping_changes_only_the_focused_pane(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._navigate(0, +1)
        assert self._panes(v) == (["C", "B"], "C")     # B untouched
        assert v._grid_focus == 0                      # focus did not jump

    def test_stepping_skips_values_other_panes_hold(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))                 # panes A, B
        assert self._candidates(v) == ["A", "C", "D"]  # never B
        v._navigate(0, +1)
        v._navigate(0, +1)
        assert self._panes(v) == (["D", "B"], "D")
        v._navigate(0, +1)                             # wraps past B
        assert self._panes(v) == (["A", "B"], "A")

    def test_stepping_backwards_walks_the_same_ring(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._navigate(0, -1)
        assert self._panes(v) == (["D", "B"], "D")
        v._navigate(0, -1)
        assert self._panes(v) == (["C", "B"], "C")

    def test_axis_is_inert_when_every_value_is_on_screen(self, qtbot):
        """Nothing is off screen, so the focused pane has nowhere to scroll."""
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 2))
        assert self._candidates(v) == ["A"]
        v._navigate(0, +1)
        assert self._panes(v) == (["A", "B", "C", "D"], "A")

    def test_other_axes_still_move_every_pane(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        before = list(v._grid_slots)
        v._navigate(1, +1)                             # the date axis
        assert v.pos[1] == 1
        assert v._grid_slots == before                 # panes unchanged

    # -- focus ---------------------------------------------------------------

    def test_clicking_a_pane_takes_focus(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._grid_widget.focus_requested.emit(1)
        assert v._grid_focus == 1
        assert self._panes(v) == (["A", "B"], "B")

    def test_focus_follows_the_axis_position(self, qtbot):
        """pos on the grid axis names the focused pane's value, so the status
        bar and the next step both talk about the pane in hand."""
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._grid_widget.focus_requested.emit(1)
        assert v.pos[0] == v._grid_slots[1]

    def test_stepping_after_a_focus_change_moves_the_new_pane(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._grid_widget.focus_requested.emit(1)
        v._navigate(0, +1)
        assert self._panes(v) == (["A", "C"], "C")     # A untouched
        assert v._grid_focus == 1

    def test_exactly_one_cell_is_marked_focused(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._grid_widget.focus_requested.emit(1)
        assert [c._focused for c in v._grid_widget._cells] == [False, True]

    def test_clicking_the_focused_pane_is_a_no_op(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._navigate(0, +1)
        before = list(v._grid_slots)
        v._grid_widget.focus_requested.emit(0)
        assert v._grid_slots == before and v._grid_focus == 0

    def test_out_of_range_focus_is_ignored(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        v._grid_set_focus(9)
        assert v._grid_focus == 0

    # -- invariants ----------------------------------------------------------

    def test_panes_never_show_the_same_value_twice(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        for _ in range(6):
            v._navigate(0, +1)
            assert len(set(v._grid_slots)) == len(v._grid_slots)

    def test_picking_a_value_another_pane_holds_swaps_them(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))                 # panes A, B
        v.pos[0] = v._grid_slots[1]                    # pick B into pane 0
        v._refresh()
        assert self._panes(v) == (["B", "A"], "B")

    def test_cell_labels_track_their_pane(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, None, (2, 1))
        assert [c._label_text for c in v._grid_widget._cells] == ["A", "B"]
        v._navigate(0, +1)
        assert [c._label_text for c in v._grid_widget._cells] == ["C", "B"]

    # -- value subsets -------------------------------------------------------

    def test_filtered_values_are_the_only_ones_offered(self, qtbot):
        v = self._view(qtbot)
        v._enter_grid(0, [0, 2, 3], (2, 1))            # A, C, D -- B excluded
        assert self._panes(v) == (["A", "C"], "A")
        assert self._candidates(v) == ["A", "D"]       # never B; C is next door
        v._navigate(0, +1)
        assert self._panes(v) == (["D", "C"], "D")

    def test_entry_anchors_focus_on_a_pane(self, qtbot):
        """Entering with the axis parked on a filtered-out value must not leave
        the focus pointing at something no pane shows."""
        v = self._view(qtbot)
        v.pos[0] = 1                                   # B, which the filter drops
        v._enter_grid(0, [0, 2, 3], (2, 1))
        assert v.pos[0] == v._grid_slots[v._grid_focus]
        assert self._panes(v) == (["A", "C"], "A")

    def test_entry_keeps_the_current_value_focused_when_shown(self, qtbot):
        v = self._view(qtbot)
        v.pos[0] = 1                                   # B
        v._enter_grid(0, None, (2, 1))                 # panes A, B
        assert v._grid_focus == 1
        assert self._panes(v) == (["A", "B"], "B")


# ---------------------------------------------------------------------------
# Initial panel visibility (--info / --status-bar)
# ---------------------------------------------------------------------------

class TestInitialPanelVisibility:
    def _window(self, viewer_config, mini_pixmaps, qtbot, **kw):
        from juxt.viewer import MainWindow

        w = MainWindow(viewer_config, mini_pixmaps, watch=False, **kw)
        qtbot.addWidget(w)
        w.show()
        return w

    def test_defaults_keep_status_bar_and_hide_info(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot)
        assert w.statusBar().isVisible()
        assert not w._info_dock.isVisible()

    def test_show_info_opens_the_sidebar(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_info=True)
        assert w._info_dock.isVisible()

    def test_sidebar_is_populated_when_opened_at_startup(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_info=True)
        assert "sensor" in w._info_panel.toPlainText()

    def test_hidden_status_bar_at_startup(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_status_bar=False)
        assert not w.statusBar().isVisible()

    def test_hidden_status_bar_still_toggles_back_on(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_status_bar=False)
        w._toggle_status_bar()
        assert w.statusBar().isVisible()

    def test_open_sidebar_still_toggles_back_off(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_info=True)
        w._toggle_info_panel()
        assert not w._info_dock.isVisible()


# ---------------------------------------------------------------------------
# Right-click copy menu
# ---------------------------------------------------------------------------

class TestImageContextMenu:
    def _menu(self, view):
        """The menu itself — built without entering QMenu.exec's event loop."""
        return view.build_image_menu()

    def test_menu_offers_both_copy_actions(self, image_view):
        labels = [a.text() for a in self._menu(image_view).actions()]
        assert labels == ["Copy image &path", "Copy &image"]

    def test_menu_shows_the_bound_shortcut(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.viewer import ImageView

        v = ImageView(viewer_config, mini_pixmaps,
                      keybindings={"Ctrl+Shift+C": "copy-path"})
        qtbot.addWidget(v)
        act = self._menu(v).actions()[0]
        assert act.shortcut().toString() == "Ctrl+Shift+C"

    def test_unbound_action_shows_no_shortcut(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.viewer import ImageView

        v = ImageView(viewer_config, mini_pixmaps, keybindings={})
        qtbot.addWidget(v)
        assert self._menu(v).actions()[0].shortcut().isEmpty()

    def test_rebinding_moves_the_shortcut_shown(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.viewer import ImageView

        v = ImageView(viewer_config, mini_pixmaps,
                      keybindings={"Ctrl+Alt+P": "copy-path"})
        qtbot.addWidget(v)
        assert self._menu(v).actions()[0].shortcut().toString() == "Ctrl+Alt+P"

    def test_copy_path_action_puts_the_current_path_on_the_clipboard(self, image_view, qtbot):
        from PySide6.QtWidgets import QApplication

        qtbot.keyClick(image_view, Qt.Key.Key_Right)   # sensor -> B
        self._menu(image_view).actions()[0].trigger()
        assert QApplication.clipboard().text() == "fake/B_d1.png"

    def test_copy_path_follows_the_focused_grid_pane(self, image_view, qtbot):
        image_view._enter_grid(0, None, (1, 2))
        image_view._grid_set_focus(1)
        self._menu(image_view).actions()[0].trigger()
        from PySide6.QtWidgets import QApplication
        assert QApplication.clipboard().text() == "fake/B_d1.png"


class TestCopyPathShortcut:
    def test_default_binding_is_wired(self):
        from juxt.settings import _DEFAULT_KEYBINDINGS

        assert _DEFAULT_KEYBINDINGS["Ctrl+Shift+C"] == "copy-path"

    def test_chord_copies_the_path(self, viewer_config, mini_pixmaps, qtbot):
        from PySide6.QtWidgets import QApplication

        from juxt.viewer import ImageView

        v = ImageView(viewer_config, mini_pixmaps,
                      keybindings={"Ctrl+Shift+C": "copy-path"})
        qtbot.addWidget(v)
        QApplication.clipboard().setText("")
        qtbot.keyClick(v, Qt.Key.Key_C,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        assert QApplication.clipboard().text() == "fake/A_d1.png"


# ---------------------------------------------------------------------------
# Ctrl+C copies a sidebar text selection
# ---------------------------------------------------------------------------

class TestSidebarCopy:
    CTRL = Qt.KeyboardModifier.ControlModifier

    def _window(self, config, pixmaps, qtbot, **kw):
        from juxt.viewer import MainWindow

        w = MainWindow(config, pixmaps, watch=False, show_info=True, **kw)
        qtbot.addWidget(w)
        w.show()
        return w

    @staticmethod
    def _select(panel, text):
        """Select the first occurrence of *text* in *panel*, as a drag would."""
        cursor = panel.document().find(text)
        assert not cursor.isNull()
        panel.setTextCursor(cursor)

    @staticmethod
    def _clipboard():
        from PySide6.QtWidgets import QApplication

        return QApplication.clipboard()

    def test_ctrl_c_copies_the_selected_text(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot)
        self._clipboard().setText("")
        self._select(w._info_panel, "sensor")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "sensor"

    def test_copy_keeps_line_breaks_and_plain_spaces(self, mini_pixmaps, qtbot):
        from juxt.config import Config, _auto_keys

        axes = {"sensor": ["A B", "C D"], "date": ["d1", "d2"]}
        cfg = Config(template="fake/{sensor}_{date}.png", axes=axes, keys=_auto_keys(axes))
        w = self._window(cfg, mini_pixmaps, qtbot)
        w._info_panel.selectAll()
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        text = self._clipboard().text()
        assert "\n" in text
        assert " " not in text and " " not in text
        assert "A B" in text

    def test_selection_beats_an_axis_key_on_the_same_chord(self, mini_pixmaps, qtbot):
        from juxt.config import Config, _auto_keys

        axes = {"color": ["red", "blue"], "date": ["d1", "d2"]}
        cfg = Config(template="fake/{color}_{date}.png", axes=axes, keys=_auto_keys(axes))
        w = self._window(cfg, mini_pixmaps, qtbot)
        assert w.view.key_to_axis["c"] == 0
        self._clipboard().setText("")
        self._select(w._info_panel, "color")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "color"
        assert w.view._sel is None

    def test_without_a_selection_the_clipboard_is_left_alone(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot)
        self._clipboard().setText("keep me")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "keep me"

    def test_without_a_selection_ctrl_c_still_cancels_command_mode(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot)
        qtbot.keyClick(w.view, Qt.Key.Key_Colon)
        assert w.view._cmd is not None
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert w.view._cmd is None

    def test_off_never_captures_ctrl_c(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, sidebar_copy="off")
        self._clipboard().setText("keep me")
        self._select(w._info_panel, "sensor")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "keep me"

    def test_off_leaves_ctrl_c_to_cancel_command_mode(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, sidebar_copy="off")
        self._select(w._info_panel, "sensor")
        qtbot.keyClick(w.view, Qt.Key.Key_Colon)
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert w.view._cmd is None

    def test_reloaded_settings_switch_the_behaviour(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.settings import Settings

        w = self._window(viewer_config, mini_pixmaps, qtbot)
        w.view.apply_settings(Settings(sidebar_copy="off"))
        self._clipboard().setText("keep me")
        self._select(w._info_panel, "sensor")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "keep me"

        w.view.apply_settings(Settings(sidebar_copy="selected"))
        self._select(w._info_panel, "sensor")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "sensor"

    def test_selection_in_a_hidden_sidebar_is_ignored(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot)
        self._select(w._info_panel, "sensor")
        w._info_dock.hide()
        self._clipboard().setText("keep me")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "keep me"

    def test_shortcut_sidebar_selection_is_copied_too(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_keys=True)
        self._select(w._shortcut_panel, "navigate")
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "navigate"

    def test_selecting_in_one_sidebar_clears_the_other(self, viewer_config, mini_pixmaps, qtbot):
        w = self._window(viewer_config, mini_pixmaps, qtbot, show_keys=True)
        self._select(w._info_panel, "sensor")
        self._select(w._shortcut_panel, "navigate")
        assert not w._info_panel.textCursor().hasSelection()
        assert w._shortcut_panel.textCursor().hasSelection()
        qtbot.keyClick(w.view, Qt.Key.Key_C, self.CTRL)
        assert self._clipboard().text() == "navigate"


# ---------------------------------------------------------------------------
# Fuzzy matching across the search surfaces
# ---------------------------------------------------------------------------

@pytest.fixture
def fuzzy_view(mini_pixmaps, qtbot):
    """ImageView whose axis values are long enough to match loosely."""
    from juxt.config import Config, _auto_keys
    from juxt.viewer import ImageView

    axes = {"sensor": ["ASCAT", "SMAP", "SMOS"], "date": ["d1", "d2"]}
    cfg = Config(template="fake/{sensor}_{date}.png", axes=axes, keys=_auto_keys(axes))
    pms = {(i, j): list(mini_pixmaps.values())[0] for i in range(3) for j in range(2)}
    v = ImageView(cfg, pms, seek_greedy=True)
    qtbot.addWidget(v)
    return v


class TestSelectionMatching:
    def test_value_picker_matches_a_subsequence(self, fuzzy_view):
        fuzzy_view._sel = {"phase": "value", "query": "smp", "axis_idx": 0, "cursor": 0}
        assert fuzzy_view._sel_candidates() == ["SMAP"]

    def test_value_picker_still_matches_a_prefix(self, fuzzy_view):
        fuzzy_view._sel = {"phase": "value", "query": "sm", "axis_idx": 0, "cursor": 0}
        assert fuzzy_view._sel_candidates() == ["SMAP", "SMOS"]

    def test_greedy_auto_confirm_survives_a_unique_fuzzy_match(self, fuzzy_view):
        fuzzy_view._sel_open_value(0, "smp")
        assert fuzzy_view._sel is None          # confirmed without Enter
        assert fuzzy_view.pos[0] == 1           # SMAP

    def test_axis_search_matches_a_subsequence(self, fuzzy_view):
        fuzzy_view._sel = {"phase": "axis", "query": "snr", "cursor": 0}
        assert fuzzy_view._sel_candidates() == ["sensor"]

    def test_strict_prefix_mode_rejects_the_subsequence(self, fuzzy_view):
        fuzzy_view._seek_fuzzy = False
        fuzzy_view._sel = {"phase": "value", "query": "smp", "axis_idx": 0, "cursor": 0}
        assert fuzzy_view._sel_candidates() == []


class TestCommandMatching:
    def _verb_candidates(self, view, query):
        view._cmd = {"phase": "verb", "query": query, "cursor": 0}
        return view._cmd_candidates()

    def test_initials_reach_a_hyphenated_command(self, image_view):
        assert self._verb_candidates(image_view, "fh")[0] == "fit-height"

    def test_prefix_matches_still_lead(self, image_view):
        assert self._verb_candidates(image_view, "fit")[0] == "fit"

    def test_aliases_are_untouched(self, image_view):
        assert self._verb_candidates(image_view, "q") == ["quit"]

    def test_empty_query_lists_every_command(self, image_view):
        from juxt.viewer import _COMMANDS

        assert self._verb_candidates(image_view, "") == _COMMANDS

    def test_strict_prefix_mode_drops_the_initials_match(self, image_view):
        image_view._seek_fuzzy = False
        assert self._verb_candidates(image_view, "fh") == []

    def test_grid_axis_argument_matches_loosely(self, fuzzy_view):
        assert fuzzy_view._grid_cmd_candidates("snr") == ["sensor"]

    def test_grid_value_argument_matches_loosely(self, fuzzy_view):
        assert fuzzy_view._grid_cmd_candidates("sensor smp") == ["SMAP"]

    def test_grid_value_argument_skips_values_already_named(self, fuzzy_view):
        assert "SMAP" not in fuzzy_view._grid_cmd_candidates("sensor SMAP s")


# ---------------------------------------------------------------------------
# Swapping the Ctrl / Shift modifier roles on axis letter keys
# ---------------------------------------------------------------------------

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


@pytest.fixture
def swapped_view(viewer_config, mini_pixmaps, qtbot):
    """ImageView with modifiers.swap on: Shift picks, Ctrl reverses."""
    from juxt.viewer import ImageView

    v = ImageView(viewer_config, mini_pixmaps, swap_modifiers=True)
    qtbot.addWidget(v)
    return v


class TestDefaultModifierRoles:
    def test_ctrl_letter_opens_the_picker(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S, CTRL)
        assert image_view._sel is not None
        assert image_view._sel["axis_idx"] == 0

    def test_shift_letter_steps_backward(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S, SHIFT)
        assert image_view.pos[0] == 1     # wrapped from 0 backwards
        assert image_view._sel is None

    def test_bare_letter_steps_forward(self, image_view, qtbot):
        qtbot.keyClick(image_view, Qt.Key.Key_S)
        assert image_view.pos[0] == 1


class TestSwappedModifierRoles:
    def test_shift_letter_opens_the_picker(self, swapped_view, qtbot):
        qtbot.keyClick(swapped_view, Qt.Key.Key_S, SHIFT)
        assert swapped_view._sel is not None
        assert swapped_view._sel["axis_idx"] == 0

    def test_ctrl_letter_steps_backward(self, swapped_view, qtbot):
        qtbot.keyClick(swapped_view, Qt.Key.Key_S, CTRL)
        assert swapped_view.pos[0] == 1
        assert swapped_view._sel is None

    def test_bare_letter_still_steps_forward(self, swapped_view, qtbot):
        qtbot.keyClick(swapped_view, Qt.Key.Key_S)
        assert swapped_view.pos[0] == 1

    def test_pin_mode_picker_follows_the_swap(self, swapped_view, qtbot):
        swapped_view.nav_mode = NavMode.PIN
        qtbot.keyClick(swapped_view, Qt.Key.Key_S, SHIFT)
        assert swapped_view._sel is not None

    def test_pin_mode_ignores_the_reverse_modifier(self, swapped_view, qtbot):
        swapped_view.nav_mode = NavMode.PIN
        qtbot.keyClick(swapped_view, Qt.Key.Key_S, CTRL)
        assert swapped_view.pos == [0, 0]     # pin never navigates on a letter
        assert swapped_view._sel is None

    def test_settings_reload_applies_the_swap(self, image_view):
        from juxt.settings import Settings

        image_view.apply_settings(Settings(swap_modifiers=True))
        assert image_view._swap_modifiers is True


class TestModifierRoleConflicts:
    def _conflicts(self, chord, swap):
        from juxt.viewer import _keybinding_conflicts, _parse_key

        key, mods = _parse_key(chord)
        return _keybinding_conflicts({(key, mods): "reload"}, {"s"}, swap)

    def test_ctrl_axis_key_shadows_the_picker_by_default(self):
        assert "tap/pin" in self._conflicts("Ctrl+S", False)[0]

    def test_shift_axis_key_shadows_only_tap_by_default(self):
        assert "tap mode" in self._conflicts("Shift+S", False)[0]

    def test_swapping_moves_the_picker_warning_to_shift(self):
        assert "tap/pin" in self._conflicts("Shift+S", True)[0]

    def test_swapping_moves_the_reverse_warning_to_ctrl(self):
        assert "tap mode" in self._conflicts("Ctrl+S", True)[0]

    def test_a_non_axis_letter_is_still_clean_under_a_modifier(self):
        assert self._conflicts("Ctrl+Z", True) == []


# ---------------------------------------------------------------------------
# Shortcut sidebar (Ctrl+Shift+K)
# ---------------------------------------------------------------------------

@pytest.fixture
def keys_window(viewer_config, mini_pixmaps, qtbot):
    """MainWindow with the shortcut sidebar open and the default chords bound."""
    from juxt.settings import _DEFAULT_KEYBINDINGS
    from juxt.viewer import MainWindow

    w = MainWindow(viewer_config, mini_pixmaps, watch=False, show_keys=True,
                   keybindings=dict(_DEFAULT_KEYBINDINGS))
    qtbot.addWidget(w)
    w.show()
    return w


class TestShortcutPanel:
    def _text(self, window) -> str:
        return window._shortcut_panel.toPlainText()

    def test_starts_closed_by_default(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.viewer import MainWindow

        w = MainWindow(viewer_config, mini_pixmaps, watch=False)
        qtbot.addWidget(w)
        w.show()
        assert not w._shortcut_dock.isVisible()

    def test_show_keys_opens_it_populated(self, keys_window):
        assert keys_window._shortcut_dock.isVisible()
        assert "navigate" in self._text(keys_window)

    def test_toggle_action_closes_and_reopens_it(self, keys_window):
        keys_window._toggle_shortcut_panel()
        assert not keys_window._shortcut_dock.isVisible()
        keys_window._toggle_shortcut_panel()
        assert keys_window._shortcut_dock.isVisible()

    def test_chord_toggles_the_panel(self, keys_window, qtbot):
        qtbot.keyClick(keys_window.view, Qt.Key.Key_K,
                       Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        assert not keys_window._shortcut_dock.isVisible()

    def test_keys_command_toggles_the_panel(self, keys_window):
        keys_window.view._cmd_execute("keys")
        assert not keys_window._shortcut_dock.isVisible()

    def test_it_names_the_live_arrow_axes(self, keys_window):
        assert "cycle sensor" in self._text(keys_window)

    def _rows(self, window) -> list[tuple[str, str]]:
        """Every (key, description) pair the panel is showing, flattened.

        Asserted against the section data rather than the rendered text: the
        two now sit in separate table cells, so toPlainText() puts them on
        separate lines and says nothing about which key goes with which
        description.
        """
        sections = window._shortcut_panel._sections(window.view)
        return [row for _title, rows in sections for row in rows]

    def test_it_lists_the_current_axis_keys(self, keys_window):
        assert ("s", "sensor") in self._rows(keys_window)

    def test_it_lists_the_bound_chords(self, keys_window):
        assert ("Ctrl+Shift+K", "toggle-keys") in self._rows(keys_window)

    def test_tap_mode_documents_both_modifiers(self, keys_window):
        rows = self._rows(keys_window)
        assert ("Shift+letter", "step −1 on that axis") in rows
        assert ("Ctrl+letter", "open the value picker for that axis") in rows

    def test_key_and_description_are_separate_cells(self, keys_window):
        html = keys_window._shortcut_panel.toHtml()
        assert "<table" in html.lower()

    def test_headers_and_keys_are_coloured_apart(self, keys_window):
        from juxt.viewer import _HEADER_COLOR, _KEY_COLOR

        html = keys_window._shortcut_panel.toHtml().lower()
        assert _HEADER_COLOR.lower() in html
        assert _KEY_COLOR.lower() in html
        assert _HEADER_COLOR.lower() != _KEY_COLOR.lower()

    def test_seek_mode_replaces_the_letter_section(self, keys_window):
        keys_window.view._cmd_execute("mode seek")
        text = self._text(keys_window)
        assert "search axes, then values" in text
        assert "step +1 on that axis" not in text

    def test_seek_mode_drops_the_axis_key_list(self, keys_window):
        keys_window.view._cmd_execute("mode seek")
        assert "axis keys" not in self._text(keys_window)

    def test_pin_mode_has_no_reverse_entry(self, keys_window):
        keys_window.view._cmd_execute("mode pin")
        text = self._text(keys_window)
        assert "focus that axis" in text
        assert "step −1 on that axis" not in text

    def test_swapped_modifiers_are_reflected(self, keys_window):
        keys_window.view._swap_modifiers = True
        keys_window._shortcut_panel.refresh(keys_window.view)
        rows = self._rows(keys_window)
        assert ("Shift+letter", "open the value picker for that axis") in rows
        assert ("Ctrl+letter", "step −1 on that axis") in rows

    def test_it_follows_navigation(self, keys_window, qtbot):
        keys_window.view._cmd_execute("mode pin")
        qtbot.keyClick(keys_window.view, Qt.Key.Key_D)   # focus date on ←/→
        assert "cycle date" in self._text(keys_window)

    def test_a_closed_panel_is_not_refreshed(self, keys_window):
        keys_window._toggle_shortcut_panel()             # close it
        before = self._text(keys_window)
        keys_window.view._cmd_execute("mode seek")
        assert self._text(keys_window) == before


class TestShortcutPanelDefaults:
    def test_ctrl_shift_k_is_bound_out_of_the_box(self):
        from juxt.settings import _DEFAULT_KEYBINDINGS

        assert _DEFAULT_KEYBINDINGS["Ctrl+Shift+K"] == "toggle-keys"

    def test_keys_is_a_command(self):
        from juxt.viewer import _COMMANDS

        assert "keys" in _COMMANDS


class TestShortcutPanelLayout:
    """The key column is measured, not fixed, so no chord wraps."""

    def _panel(self, viewer_config, mini_pixmaps, qtbot, width):
        from juxt.settings import _DEFAULT_KEYBINDINGS
        from juxt.viewer import ImageView, ShortcutPanel

        v = ImageView(viewer_config, mini_pixmaps, keybindings=dict(_DEFAULT_KEYBINDINGS))
        qtbot.addWidget(v)
        p = ShortcutPanel()
        qtbot.addWidget(p)
        p.resize(width, 600)
        p.refresh(v)
        return p, v

    def test_column_fits_the_widest_chord(self, viewer_config, mini_pixmaps, qtbot):
        p, v = self._panel(viewer_config, mini_pixmaps, qtbot, 400)
        sections = p._sections(v)
        widest = max(p.fontMetrics().horizontalAdvance(k)
                     for _t, rows in sections for k, _d in rows)
        assert p._key_column_px(sections) >= widest

    def test_column_never_takes_more_than_half_the_panel(self, viewer_config, mini_pixmaps, qtbot):
        p, v = self._panel(viewer_config, mini_pixmaps, qtbot, 200)
        assert p._key_column_px(p._sections(v)) <= max(90, p.viewport().width() // 2)

    def test_resizing_remeasures_instead_of_going_stale(self, viewer_config, mini_pixmaps, qtbot):
        p, _v = self._panel(viewer_config, mini_pixmaps, qtbot, 200)
        narrow = p.toHtml()
        p.resize(600, 600)
        qtbot.wait(10)
        assert p.toHtml() != narrow      # re-rendered against the new width

    def test_refresh_is_not_reentered_by_its_own_resize(self, viewer_config, mini_pixmaps, qtbot):
        p, _v = self._panel(viewer_config, mini_pixmaps, qtbot, 240)
        p.resize(150, 300)
        qtbot.wait(10)
        assert p.toPlainText().count("navigate") == 1   # content not duplicated

    def test_a_long_description_does_not_widen_the_key_column(self, viewer_config, mini_pixmaps, qtbot):
        p, v = self._panel(viewer_config, mini_pixmaps, qtbot, 400)
        before = p._key_column_px(p._sections(v))
        v.axis_names[0] = "an extremely long axis name that wraps for certain"
        after = p._key_column_px(p._sections(v))
        assert after == before


# ---------------------------------------------------------------------------
# skip_missing — cycling over an incomplete datacube
# ---------------------------------------------------------------------------

class TestSkipMissing:
    @pytest.fixture
    def sparse_view(self, viewer_config, qtbot):
        """3 values on axis 0 (config's axis 0 padded); the middle one is missing."""
        from juxt.loader import _error_pixmap
        from juxt.viewer import ImageView

        cfg = viewer_config
        names = list(cfg.axes)
        cfg.axes[names[0]] = ["a", "b", "c"]
        ok = QPixmap(4, 4)
        pixmaps = {}
        n1 = len(cfg.axes[names[1]])
        for i in range(3):
            for j in range(n1):
                pixmaps[(i, j)] = _error_pixmap("x") if i == 1 else ok
        v = ImageView(cfg, pixmaps, skip_missing=True)
        qtbot.addWidget(v)
        v.show()
        return v

    def test_forward_skips_missing(self, sparse_view, qtbot):
        qtbot.keyClick(sparse_view, Qt.Key.Key_Right)
        assert sparse_view.pos[0] == 2

    def test_backward_skips_missing_and_wraps(self, sparse_view, qtbot):
        qtbot.keyClick(sparse_view, Qt.Key.Key_Right)  # 0 → 2
        qtbot.keyClick(sparse_view, Qt.Key.Key_Left)   # 2 → 0
        assert sparse_view.pos[0] == 0

    def test_disabled_shows_placeholder(self, sparse_view, qtbot):
        sparse_view._skip_missing = False
        qtbot.keyClick(sparse_view, Qt.Key.Key_Right)
        assert sparse_view.pos[0] == 1

    def test_all_missing_stays_put(self, sparse_view, qtbot):
        from juxt.loader import _error_pixmap
        for k in list(sparse_view.pixmaps):
            if k[0] != 0:
                sparse_view.pixmaps[k] = _error_pixmap("x")
        qtbot.keyClick(sparse_view, Qt.Key.Key_Right)
        assert sparse_view.pos[0] == 0


class TestMissingCommandsAndDimming:
    @pytest.fixture
    def view(self, viewer_config, qtbot):
        from juxt.loader import _error_pixmap
        from juxt.viewer import ImageView

        cfg = viewer_config
        names = list(cfg.axes)
        cfg.axes[names[0]] = ["a", "b", "c"]
        n1 = len(cfg.axes[names[1]])
        pm = QPixmap(4, 4)
        pixmaps = {(i, j): _error_pixmap("x") if i == 1 else pm
                   for i in range(3) for j in range(n1)}
        v = ImageView(cfg, pixmaps)
        qtbot.addWidget(v)
        return v

    def test_skip_missing_command_toggles_and_sets(self, view):
        view._cmd_execute("skip-missing on")
        assert view._skip_missing is True
        view._cmd_execute("skip-missing off")
        assert view._skip_missing is False
        view._cmd_execute("skip-missing")
        assert view._skip_missing is True

    def test_dim_missing_command(self, view):
        view._cmd_execute("dim-missing on")
        assert view._dim_missing is True

    def test_missing_along(self, view):
        assert view._missing_along(0) == [False, True, False]

    def test_sidebar_dims_only_missing_on_focused_axis(self, view, qtbot):
        from juxt.viewer import InfoPanel, _DIM_COLOR
        panel = InfoPanel()
        qtbot.addWidget(panel)
        view._dim_missing = True
        panel.refresh(view)
        assert _DIM_COLOR in panel.toHtml().lower() or "#555555" in panel.toHtml().lower()
        view._dim_missing = False
        panel.refresh(view)
        assert "#555555" not in panel.toHtml().lower()


# ---------------------------------------------------------------------------
# :subset -- edit the values of one axis
# ---------------------------------------------------------------------------

class TestSubsetCommand:
    @pytest.fixture
    def view(self, flat_config, flat_plot_dir, qtbot):
        """Viewer over real files: sensor A,B x date d1,d2, plus C on disk."""
        from conftest import make_png
        from juxt.loader import preload

        for date in ("d1", "d2"):
            (flat_plot_dir / f"C_{date}.png").write_bytes(make_png())
        pixmaps = preload(flat_config.template, flat_config.axes)
        v = ImageView(flat_config, pixmaps)
        qtbot.addWidget(v)
        v.show()
        return v

    def _type(self, v, qtbot, text):
        for ch in text:
            qtbot.keyClick(v, Qt.Key_Space if ch == " " else getattr(Qt, f"Key_{ch.upper()}"))

    def test_format_and_parse_round_trip(self):
        from juxt.viewer import format_values, parse_values
        vals = ["A", "two words", "d-1"]
        assert format_values(vals) == 'A "two words" d-1'
        assert parse_values(format_values(vals)) == vals

    def test_delete(self, view):
        keep = view.pixmaps[(1, 0)]
        view._cmd_execute("subset sensor B")
        assert view.axis_values[0] == ["B"]
        assert view.pixmaps[(0, 0)] is keep
        assert len(view.pixmaps) == 2

    def test_reorder_carries_images(self, view):
        a, b = view.pixmaps[(0, 0)], view.pixmaps[(1, 0)]
        view._cmd_execute("subset sensor B A")
        assert view.axis_values[0] == ["B", "A"]
        assert view.pixmaps[(0, 0)] is b
        assert view.pixmaps[(1, 0)] is a

    def test_add_loads_new_value_from_disk(self, view):
        from juxt.loader import MissingPixmap
        view._cmd_execute("subset sensor A B C")
        assert view.axis_values[0] == ["A", "B", "C"]
        assert len(view.pixmaps) == 6
        assert not isinstance(view.pixmaps[(2, 1)], MissingPixmap)

    def test_modify_replaces_value(self, view):
        from juxt.loader import MissingPixmap
        view._cmd_execute("subset sensor A C")
        assert view.axis_values[0] == ["A", "C"]
        assert not isinstance(view.pixmaps[(1, 0)], MissingPixmap)

    def test_value_without_files_is_refused(self, view):
        view._cmd_execute("subset sensor A Z")
        assert view.axis_values[0] == ["A", "B"]
        assert "no files" in view._flash_msg

    def test_empty_is_refused(self, view, qtbot):
        view._cmd_execute("subset sensor")
        view._cmd["query"] = ""
        qtbot.keyClick(view, Qt.Key_Return)
        assert view.axis_values[0] == ["A", "B"]
        assert "at least one" in view._flash_msg

    def test_case_snaps_to_existing_value(self, view):
        view._cmd_execute("subset sensor b a")
        assert view.axis_values[0] == ["B", "A"]

    def test_duplicates_are_collapsed(self, view):
        view._cmd_execute("subset sensor A A B")
        assert view.axis_values[0] == ["A", "B"]

    def test_current_position_is_kept(self, view):
        view.pos = [1, 1]                       # sensor B, date d2
        view._cmd_execute("subset sensor B A")  # B moves to index 0
        assert view.pos == [0, 1]

    def test_unknown_axis_flashes_usage(self, view):
        view._cmd_execute("subset nope A")
        assert "usage" in view._flash_msg

    def test_remote_add_is_refused(self, view):
        from juxt.config import RemoteConfig
        view.config.remote = RemoteConfig(host="h")
        view._cmd_execute("subset sensor A B C")
        assert view.axis_values[0] == ["A", "B"]

    def test_axis_only_opens_editor_prefilled(self, view):
        view._cmd_execute("subset sensor")
        assert view._cmd["axis"] == "sensor"
        assert view._cmd["query"] == "A B"
        assert view._cmd["caret"] == 3

    def test_editor_via_keys(self, view, qtbot):
        qtbot.keyClick(view, Qt.Key_Colon)
        self._type(view, qtbot, "subset")
        qtbot.keyClick(view, Qt.Key_Return)         # -> axis picker
        qtbot.keyClick(view, Qt.Key_Return)         # first axis: sensor
        assert view._cmd["query"] == "A B"
        qtbot.keyClick(view, Qt.Key_Backspace)      # "A "
        qtbot.keyClicks(view, "C")
        qtbot.keyClick(view, Qt.Key_Return)
        assert view.axis_values[0] == ["A", "C"]
        assert view._cmd is None

    def test_tab_completes_from_disk(self, view, qtbot):
        view._cmd_execute("subset sensor")
        qtbot.keyClick(view, Qt.Key_Space)
        qtbot.keyClick(view, Qt.Key_Tab)            # only C is on disk but not in the line
        assert view._cmd["query"] == "A B C "

    def test_escape_leaves_axis_untouched(self, view, qtbot):
        view._cmd_execute("subset sensor")
        qtbot.keyClick(view, Qt.Key_Escape)
        assert view._cmd is None
        assert view.axis_values[0] == ["A", "B"]


class TestSubsetPrompt:
    def test_status_bar_shows_axis_and_values(self, keys_window):
        w = keys_window
        w.view._cmd_execute("subset sensor")
        w._update_status()
        text = w._status_label.text().replace("&nbsp;", " ")
        assert ":subset sensor  A B" in text
