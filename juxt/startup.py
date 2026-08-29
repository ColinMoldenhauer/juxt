"""Startup dialog shown when `juxt` is launched without a PATH argument.

Two tabs produce the same thing a CLI PATH argument would: a directory to
auto-discover, or a `{placeholder}` template string. Either is handed back to
`main()` exactly as `args.path` and flows through the existing detection
code unchanged.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .complete import complete_path, complete_placeholder_name, local_listdir, placeholder_html

_HINT = (
    "Type a path; use <code>{name}</code> for a segment that varies "
    "(<code>{}</code> for an auto-named one). "
    "<b>Tab</b> completes directories and files; placeholders are treated as wildcards."
)


class _TemplateLineEdit(QLineEdit):
    """QLineEdit with Tab-driven, placeholder-aware path completion.

    Overriding `event()` (rather than `keyPressEvent()`) is required to
    intercept Tab before Qt's own focus-traversal handling consumes it.
    """

    def __init__(self, on_change, parent: QWidget | None = None):
        super().__init__(parent)
        self._on_change = on_change
        self._listdir = local_listdir()

    def event(self, e):
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Tab:
            self._complete()
            return True
        return super().event(e)

    def _complete(self):
        pos = self.cursorPosition()
        prefix = self.text()[:pos]
        comp = complete_placeholder_name(prefix, [])
        if comp is None:
            comp = complete_path(prefix, self._listdir)
        if comp.append:
            text = self.text()
            self.setText(text[:pos] + comp.append + text[pos:])
            self.setCursorPosition(pos + len(comp.append))
        self._on_change(self.text(), comp.matches)


class StartupDialog(QDialog):
    """Directory picker (Browse) or `{placeholder}` template builder (Build path)."""

    def __init__(self, initial_dir: str, app_icon=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("juxt")
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resize(760, 520)

        self._chosen_path = ""

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_browse_tab(initial_dir), "Browse")
        self.tabs.addTab(self._build_template_tab(), "Build path")
        self.tabs.currentChanged.connect(self._update_button_state)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self.buttons)

        self._update_button_state()

    # -- Browse tab ----------------------------------------------------------

    def _build_browse_tab(self, initial_dir: str) -> QWidget:
        self._browse = QFileDialog(self, "Select image directory", initial_dir)
        self._browse.setFileMode(QFileDialog.FileMode.Directory)
        self._browse.setOption(QFileDialog.Option.ShowDirsOnly, True)
        self._browse.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self._browse.setWindowFlags(Qt.WindowType.Widget)
        self._browse.currentChanged.connect(lambda *_: self._update_button_state())
        buttons = self._browse.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.hide()

        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._browse)
        return container

    def _browse_selection(self) -> str:
        chosen = self._browse.selectedFiles()
        if chosen and chosen[0]:
            return chosen[0]
        return self._browse.directory().absolutePath()

    # -- Build path tab --------------------------------------------------------

    def _build_template_tab(self) -> QWidget:
        container = QWidget(self)
        layout = QVBoxLayout(container)

        hint = QLabel(_HINT, container)
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(hint)

        self._path_edit = _TemplateLineEdit(self._on_template_change, container)
        self._path_edit.setPlaceholderText("plots/{sensor}_{date}.png")
        self._path_edit.setText(str(Path.home()) + "/")
        self._path_edit.returnPressed.connect(self._accept)
        self._path_edit.textEdited.connect(lambda t: self._on_template_change(t, []))
        layout.addWidget(self._path_edit)

        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("preview:", container))
        self._preview_label = QLabel(container)
        self._preview_label.setTextFormat(Qt.TextFormat.RichText)
        preview_row.addWidget(self._preview_label, stretch=1)
        layout.addLayout(preview_row)

        self._candidates_label = QLabel(container)
        self._candidates_label.setWordWrap(True)
        self._candidates_label.setStyleSheet("color: palette(mid);")
        layout.addWidget(self._candidates_label)
        layout.addStretch(1)

        self._on_template_change(self._path_edit.text(), [])
        return container

    def _on_template_change(self, text: str, matches: list[str]):
        self._preview_label.setText(placeholder_html(text) or "&nbsp;")
        self._candidates_label.setText(", ".join(matches))
        self._update_button_state()

    # -- shared ----------------------------------------------------------------

    def _update_button_state(self):
        open_btn = self.buttons.button(QDialogButtonBox.StandardButton.Open)
        if self.tabs.currentIndex() == 0:
            enabled = bool(self._browse_selection())
        else:
            enabled = bool(self._path_edit.text().strip())
        open_btn.setEnabled(enabled)

    def _accept(self):
        if self.tabs.currentIndex() == 0:
            self._chosen_path = self._browse_selection()
        else:
            self._chosen_path = self._path_edit.text().strip()
        if not self._chosen_path:
            return
        self.accept()

    def chosen_path(self) -> str:
        return self._chosen_path
