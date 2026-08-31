"""Startup dialog shown when `juxt` is launched without a PATH argument.

Two tabs produce the same thing a CLI PATH argument would: a directory to
auto-discover, or a `{placeholder}` template string. Either is handed back to
`main()` exactly as `args.path` and flows through the existing detection
code unchanged.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .complete import (
    complete_path,
    complete_placeholder_name,
    local_listdir,
    placeholder_shapes,
    placeholder_spans,
)

_HINT = (
    "Type a path; use <code>{name}</code> for a segment that varies "
    "(<code>{}</code> for an auto-named one). "
    "<b>Tab</b> completes directories and files; placeholders are treated as wildcards."
)


class _PlaceholderHighlighter(QSyntaxHighlighter):
    """Colours each `{placeholder}` in place, matching `placeholder_html`."""

    def highlightBlock(self, text: str) -> None:
        for start, end, colour in placeholder_spans(text):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            self.setFormat(start, end - start, fmt)


class _TemplateEdit(QPlainTextEdit):
    """Single-line, placeholder-highlighted, Tab-completing path template field.

    A QPlainTextEdit rather than a QLineEdit so the placeholders can be
    colour-highlighted in place — QLineEdit only supports a single colour for
    its whole text.

    Overriding `event()` (rather than `keyPressEvent()`) is required to
    intercept Tab before Qt's own focus-traversal handling consumes it.
    """

    returnPressed = Signal()
    textEdited = Signal(str)

    def __init__(self, on_change, parent: QWidget | None = None):
        super().__init__(parent)
        self._on_change = on_change
        self._listdir = local_listdir()
        self._highlighter = _PlaceholderHighlighter(self.document())
        self._user_editing = False

        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        margin = self.document().documentMargin()
        self.setFixedHeight(round(self.fontMetrics().height() + 2 * margin + 2 * self.frameWidth() + 6))
        self.textChanged.connect(self._handle_text_changed)

    def event(self, e):
        if e.type() == QEvent.Type.KeyPress:
            if e.key() == Qt.Key.Key_Tab:
                self._complete()
                return True
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.returnPressed.emit()
                return True
            self._user_editing = True
            try:
                return super().event(e)
            finally:
                self._user_editing = False
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

    def _handle_text_changed(self):
        if self._user_editing:
            self.textEdited.emit(self.toPlainText())

    # -- QLineEdit-shaped API, so the rest of the dialog stays unchanged -------

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:
        self.setPlainText(text)

    def cursorPosition(self) -> int:
        return self.textCursor().position()

    def setCursorPosition(self, pos: int) -> None:
        cursor = self.textCursor()
        cursor.setPosition(pos)
        self.setTextCursor(cursor)


class StartupDialog(QDialog):
    """Directory picker (Browse) or `{placeholder}` template builder (Build path)."""

    def __init__(self, initial_dir: str, app_icon=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("juxt")
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resize(760, 520)

        self._chosen_path = ""
        self._template_touched = False

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_browse_tab(initial_dir), "Browse")
        self.tabs.addTab(self._build_template_tab(), "Build path")
        self.tabs.currentChanged.connect(self._update_button_state)
        self.tabs.currentChanged.connect(self._sync_template_start_path)

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

        self._path_edit = _TemplateEdit(self._on_template_change, container)
        self._path_edit.setPlaceholderText("plots/{sensor}_{date}.png")
        self._path_edit.setText(str(Path.home()) + "/")
        self._path_edit.returnPressed.connect(self._accept)
        self._path_edit.textEdited.connect(self._on_template_edited)

        edit_row = QHBoxLayout()
        edit_row.addWidget(self._path_edit, stretch=1)
        edit_row.addWidget(self._build_add_placeholder_button(container))
        layout.addLayout(edit_row)

        self._candidates_label = QLabel(container)
        self._candidates_label.setWordWrap(True)
        self._candidates_label.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self._candidates_label)
        layout.addStretch(1)

        self._on_template_change(self._path_edit.text(), [])
        return container

    def _build_add_placeholder_button(self, parent: QWidget) -> QPushButton:
        button = QPushButton("Add placeholder", parent)
        menu = QMenu(button)
        menu.addAction("{} (anonymous)", lambda: self._insert_placeholder("", land_inside=False))
        menu.addAction("Custom…", lambda: self._insert_placeholder(""))
        for name in sorted(placeholder_shapes()):
            menu.addAction(f"{{{name}}}", lambda name=name: self._insert_placeholder(name))
        button.setMenu(menu)
        return button

    def _insert_placeholder(self, name: str, *, land_inside: bool = True) -> None:
        edit = self._path_edit
        pos = edit.cursorPosition()
        text = edit.text()
        inserted = "{" + name + "}"
        edit.setText(text[:pos] + inserted + text[pos:])
        if name:
            caret = pos + len(inserted)
        else:
            # Anonymous: land inside the braces to type a name, or past them
            # to leave it auto-numbered and keep typing the rest of the path.
            caret = pos + 1 if land_inside else pos + len(inserted)
        edit.setCursorPosition(caret)
        edit.setFocus()
        self._on_template_edited(edit.text())

    def _on_template_change(self, text: str, matches: list[str]):
        self._candidates_label.setText(", ".join(matches))
        self._update_button_state()

    def _on_template_edited(self, text: str):
        self._template_touched = True
        self._on_template_change(text, [])

    def _sync_template_start_path(self, index: int):
        """Seed the Build path tab from the Browse tab's current directory.

        Only applies until the user actually types into the template field,
        so it never clobbers work in progress.
        """
        if index != 1 or self._template_touched:
            return
        selection = self._browse_selection()
        if not selection:
            return
        path = selection.rstrip("/") + "/"
        self._path_edit.setText(path)
        self._path_edit.setCursorPosition(len(path))
        self._on_template_change(path, [])

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
