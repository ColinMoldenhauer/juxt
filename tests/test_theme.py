"""Tests for juxt/theme.py and the viewer's theme switching."""
from __future__ import annotations

from dataclasses import fields

import pytest
from PySide6.QtGui import QPalette

from juxt import theme
from juxt.settings import load_settings


class TestThemes:
    def test_both_themes_define_every_colour(self):
        for t in (theme.DARK, theme.LIGHT):
            for f in fields(theme.Theme):
                assert getattr(t, f.name), f"{t.name}.{f.name} is empty"

    def test_placeholder_palettes_cycle_the_same_length(self):
        assert len(theme.DARK.placeholders) == len(theme.LIGHT.placeholders)

    def test_accent_keyword_resolves_to_the_current_accent(self):
        theme.set_current(theme.LIGHT)
        assert theme.resolve_color("accent") == theme.LIGHT.accent
        assert theme.resolve_color("Accent") == theme.LIGHT.accent
        assert theme.resolve_color("#f80") == "#f80"
        assert theme.resolve_color(None) is None

    @pytest.mark.parametrize("value, expected", [
        ("auto", "auto"), ("dark", "dark"), (" Light ", "light"), ("blue", "auto"), (None, "auto"),
    ])
    def test_parse_theme_setting(self, value, expected):
        assert theme.parse_theme_setting(value) == expected


class TestApply:
    def test_explicit_theme_becomes_current(self, qtbot):
        assert theme.apply("light") is theme.LIGHT
        assert theme.current() is theme.LIGHT
        assert theme.apply("dark") is theme.DARK

    def test_app_palette_follows_the_theme(self, qtbot):
        from PySide6.QtWidgets import QApplication

        theme.apply("light")
        window = QApplication.instance().palette().color(QPalette.ColorRole.Window)
        assert window.name() == theme.LIGHT.surface

    def test_auto_follows_the_system(self, qtbot, monkeypatch):
        monkeypatch.setattr(theme, "_system_is_dark", lambda: False)
        assert theme.apply("auto") is theme.LIGHT
        monkeypatch.setattr(theme, "_system_is_dark", lambda: True)
        assert theme.apply("auto") is theme.DARK

    def test_auto_without_a_reported_scheme_is_dark(self, qtbot):
        # The offscreen test platform reports no colour scheme, like plain X11.
        assert theme.apply("auto") is theme.DARK


class TestSettings:
    def test_default_is_auto(self, tmp_path):
        assert load_settings(tmp_path / "settings.yaml").theme == "auto"

    def test_new_file_documents_the_setting(self, tmp_path):
        path = tmp_path / "settings.yaml"
        load_settings(path)
        assert "appearance:\n  theme: auto" in path.read_text()

    def test_reads_the_theme(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text("appearance:\n  theme: light\n")
        assert load_settings(path).theme == "light"

    def test_invalid_theme_falls_back_to_auto(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text("appearance:\n  theme: purple\n")
        assert load_settings(path).theme == "auto"

    def test_missing_section_is_added_to_an_old_file(self, tmp_path):
        path = tmp_path / "settings.yaml"
        path.write_text("seek:\n  greedy: true\n")
        load_settings(path)
        assert "appearance:" in path.read_text()


class TestPainting:
    def test_placeholder_colours_follow_the_theme(self):
        from juxt.complete import placeholder_spans

        theme.set_current(theme.LIGHT)
        spans = placeholder_spans("plots/{sensor}_{date}.png")
        assert [c for _s, _e, c in spans] == list(theme.LIGHT.placeholders[:2])

    def test_dialog_stylesheet_uses_the_current_theme(self, qtbot):
        from juxt.dialogs import GridDialog, auto_layout

        theme.apply("light")
        d = GridDialog(["sensor"], [["A", "B"]],
                       auto_layout_for=lambda n: auto_layout(n, 800, 600, 1600, 900))
        qtbot.addWidget(d)
        css = d.styleSheet()
        assert theme.LIGHT.surface in css and theme.DARK.surface not in css


@pytest.fixture
def window(viewer_config, mini_pixmaps, qtbot):
    from juxt.viewer import MainWindow

    w = MainWindow(viewer_config, mini_pixmaps, watch=False, show_info=True, show_keys=True)
    qtbot.addWidget(w)
    w.resize(1200, 700)
    return w


def _painted_with(w, t: theme.Theme) -> bool:
    """Whether every themed part of *w* currently carries *t*'s colours."""
    return (
        w.view.backgroundBrush().color().name() == t.canvas
        and t.surface in w.statusBar().styleSheet()
        and t.panel in w._info_panel.styleSheet()
        and t.panel in w._shortcut_panel.styleSheet()
        and t.header in w._info_panel.toHtml()
        and t.header in w._shortcut_panel.toHtml()
    )


class TestThemeCommand:
    def test_starts_dark_when_the_system_reports_nothing(self, window):
        assert _painted_with(window, theme.DARK)

    def test_explicit_theme_at_startup(self, viewer_config, mini_pixmaps, qtbot):
        from juxt.viewer import MainWindow

        w = MainWindow(viewer_config, mini_pixmaps, watch=False, show_info=True, theme="light")
        qtbot.addWidget(w)
        assert theme.current() is theme.LIGHT
        assert w.view.backgroundBrush().color().name() == theme.LIGHT.canvas
        assert theme.LIGHT.surface in w.statusBar().styleSheet()

    def test_theme_light_restyles_everything(self, window):
        window.view._cmd_execute("theme light")
        assert theme.current() is theme.LIGHT
        assert _painted_with(window, theme.LIGHT)

    def test_theme_without_argument_flips(self, window):
        window.view._cmd_execute("theme")
        assert theme.current() is theme.LIGHT
        window.view._cmd_execute("theme")
        assert _painted_with(window, theme.DARK)

    def test_unknown_theme_changes_nothing(self, window):
        window.view._cmd_execute("theme purple")
        assert theme.current() is theme.DARK
        assert "unknown theme" in window.view._flash_msg

    def test_grid_cells_follow_the_theme(self, window):
        window.view._cmd_execute("grid sensor")
        window.view._cmd_execute("theme light")
        cells = window.view._grid_widget._cells
        assert cells and all(
            c.backgroundBrush().color().name() == theme.LIGHT.canvas for c in cells
        )

    def test_system_change_is_followed_in_auto_mode(self, window, monkeypatch):
        monkeypatch.setattr(theme, "_system_is_dark", lambda: False)
        window._on_system_scheme_changed(None)
        assert _painted_with(window, theme.LIGHT)

    def test_system_change_is_ignored_when_pinned(self, window, monkeypatch):
        window.view._cmd_execute("theme dark")
        monkeypatch.setattr(theme, "_system_is_dark", lambda: False)
        window._on_system_scheme_changed(None)
        assert theme.current() is theme.DARK


class TestSettingsReload:
    def _reload(self, window, monkeypatch, value):
        import juxt.viewer as viewer
        from juxt.settings import Settings

        monkeypatch.setattr(viewer, "load_settings", lambda *_a, **_k: Settings(theme=value))
        window._on_settings_changed("settings.yaml")

    def test_changed_setting_applies(self, window, monkeypatch):
        self._reload(window, monkeypatch, "light")
        assert _painted_with(window, theme.LIGHT)

    def test_unchanged_setting_keeps_the_session_choice(self, window, monkeypatch):
        window.view._cmd_execute("theme light")
        self._reload(window, monkeypatch, "auto")     # same as at startup
        assert theme.current() is theme.LIGHT
