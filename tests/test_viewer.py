"""Widget tests for juxt/viewer.py using pytest-qt."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

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
        for href, colour in colours.items():
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
