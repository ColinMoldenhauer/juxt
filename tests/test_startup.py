"""Widget tests for juxt/startup.py using pytest-qt."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from juxt.startup import StartupDialog


def _open_button(dlg: StartupDialog):
    return dlg.buttons.button(QDialogButtonBox.StandardButton.Open)


class TestStartupDialogTabs:
    def test_has_browse_and_build_path_tabs(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Browse", "Build path"]

    def test_browse_tab_is_active_by_default(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        assert dlg.tabs.currentIndex() == 0


class TestBrowseTab:
    def test_selection_reports_current_directory(self, qtbot, nested_plot_dir):
        dlg = StartupDialog(str(nested_plot_dir))
        qtbot.addWidget(dlg)
        dlg._browse.setDirectory(str(nested_plot_dir / "A"))
        assert dlg._browse_selection() == str(nested_plot_dir / "A")

    def test_open_enabled_with_a_directory_selected(self, qtbot, nested_plot_dir):
        dlg = StartupDialog(str(nested_plot_dir))
        qtbot.addWidget(dlg)
        assert _open_button(dlg).isEnabled()

    def test_accept_sets_chosen_path_to_browsed_directory(self, qtbot, nested_plot_dir):
        dlg = StartupDialog(str(nested_plot_dir))
        qtbot.addWidget(dlg)
        dlg._browse.setDirectory(str(nested_plot_dir / "B"))
        dlg._accept()
        assert dlg.chosen_path() == str(nested_plot_dir / "B")


class TestBuildPathTab:
    def test_open_disabled_when_template_empty(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg.tabs.setCurrentIndex(1)
        dlg._path_edit.setText("")
        dlg._on_template_change("", [])
        assert not _open_button(dlg).isEnabled()

    def test_open_enabled_when_template_non_empty(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg.tabs.setCurrentIndex(1)
        dlg._path_edit.setText("plots/{sensor}.png")
        dlg._on_template_change(dlg._path_edit.text(), [])
        assert _open_button(dlg).isEnabled()

    def test_accept_sets_chosen_path_to_typed_template(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg.tabs.setCurrentIndex(1)
        dlg._path_edit.setText("plots/{sensor}_{date}.png")
        dlg._accept()
        assert dlg.chosen_path() == "plots/{sensor}_{date}.png"

    def test_add_placeholder_button_inserts_custom_braces_at_cursor(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg.tabs.setCurrentIndex(1)
        dlg._path_edit.setText("plots/.png")
        dlg._path_edit.setCursorPosition(len("plots/"))
        dlg._insert_placeholder("")
        assert dlg._path_edit.text() == "plots/{}.png"
        assert dlg._path_edit.cursorPosition() == len("plots/{")

    def test_add_placeholder_button_inserts_named_placeholder(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg.tabs.setCurrentIndex(1)
        dlg._path_edit.setText("plots/.png")
        dlg._path_edit.setCursorPosition(len("plots/"))
        dlg._insert_placeholder("date")
        assert dlg._path_edit.text() == "plots/{date}.png"

    def test_template_field_colours_placeholders(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg._path_edit.setText("plots/{sensor}.png")
        ranges = dlg._path_edit.document().firstBlock().layout().formats()
        assert any(r.format.foreground().color().name() == "#e8913a" for r in ranges)

    def test_tab_completes_an_existing_directory(self, qtbot, nested_plot_dir):
        dlg = StartupDialog(str(nested_plot_dir))
        qtbot.addWidget(dlg)
        edit = dlg._path_edit
        base = nested_plot_dir.as_posix()
        edit.setText(f"{base}/A/A")
        edit.setCursorPosition(len(edit.text()))
        edit._complete()
        assert edit.text() == f"{base}/A/AM/"

    def test_tab_after_placeholder_lists_candidates(self, qtbot, nested_plot_dir):
        dlg = StartupDialog(str(nested_plot_dir))
        qtbot.addWidget(dlg)
        edit = dlg._path_edit
        base = nested_plot_dir.as_posix()
        edit.setText(f"{base}/{{sensor}}/{{overpass}}/")
        edit.setCursorPosition(len(edit.text()))
        edit._complete()
        assert dlg._candidates_label.text() == "d1.png, d2.png"

    def test_return_pressed_accepts_dialog(self, qtbot, tmp_path):
        dlg = StartupDialog(str(tmp_path))
        qtbot.addWidget(dlg)
        dlg.tabs.setCurrentIndex(1)
        dlg._path_edit.setText("plots/{sensor}.png")
        dlg.show()
        with qtbot.waitSignal(dlg.accepted, timeout=1000):
            qtbot.keyClick(dlg._path_edit, Qt.Key.Key_Return)
        assert dlg.chosen_path() == "plots/{sensor}.png"
