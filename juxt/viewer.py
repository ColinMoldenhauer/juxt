from __future__ import annotations

import html as _html
import logging
import os
import re as _re
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Collection
from enum import IntEnum
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, Qt, QFileSystemWatcher, QRectF, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDockWidget,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from . import theme as _theme
from .fuzzy import fuzzy_filter, prefix_filter
from .complete import (
    complete_path,
    complete_placeholder_name,
    local_listdir,
    expand_home,
    normalize_template,
    pin_flag,
    placeholder_html,
    reset_placeholder_shapes,
    sftp_listdir,
)
from .loader import MissingPixmap
from .config import Config
from .settings import (
    DEFAULT_HIGHLIGHT,
    DEFAULT_HIGHLIGHT_CANDIDATES,
    SETTINGS_PATH,
    Highlight,
    load_settings,
    parse_highlight,
)

log = logging.getLogger(__name__)

# Fallback highlight formats, used until settings are applied.
_HL_DEFAULT = parse_highlight(DEFAULT_HIGHLIGHT)
_HL_DEFAULT_CANDIDATES = parse_highlight(DEFAULT_HIGHLIGHT_CANDIDATES)

# Colours come from the active theme (juxt/theme.py) at paint time.  In the
# sidebars, section headers ("path", an axis name, "view", …) use the warm
# `header` colour against the blue `accent` used for anything interactive, so
# a header reads as a label rather than as something to click; key chords in
# the shortcut sidebar pick up the accent, so a chord separates from its
# description at a glance.


def _panel_css() -> str:
    """Widget stylesheet shared by both sidebars."""
    t = _theme.current()
    return (
        f"QTextEdit {{ background: {t.panel}; color: {t.text}; "
        "font-family: 'Courier New', monospace; font-size: 9pt; "
        "border: none; padding: 8px; }"
    )


def _panel_body() -> str:
    """Opening of a sidebar document, up to and including `<body>`."""
    t = _theme.current()
    return (
        "<html><body style='font-family:\"Courier New\",monospace; "
        f"font-size:9pt; color:{t.text}; background:{t.panel}; margin:8px;'>"
    )

# Floor for the shortcut sidebar's key column; the real width is measured from
# the chords actually on show (see ShortcutPanel._key_column_px).
_KEY_COL_MIN_PX = 90

# ── info-sidebar links ────────────────────────────────────────────────────────

_VALUE_HREF = "juxt:value/{axis}/{value}"
_VALUE_HREF_RE = _re.compile(r"^juxt:value/(\d+)/(\d+)$")
_AXIS_HREF = "juxt:axis/{axis}"
_AXIS_HREF_RE = _re.compile(r"^juxt:axis/(\d+)$")
_PATH_HREF = "juxt:path"
_EDIT_HREF = "juxt:edit/{axis}"
_EDIT_HREF_RE = _re.compile(r"^juxt:edit/(\d+)$")

# Width of the info sidebar's viewport that its text cannot use: the 8px body
# margin and 4px document margin on each side, plus generous slack (measured,
# the usable width came out about 7px short of the margins alone, and font
# rounding differs between platforms).  Rows are packed against what is left,
# so too small only costs a column of fill, while too large lets Qt break a
# value across lines.
_SIDEBAR_TEXT_INSET_PX = 40


def _sp(text: str) -> str:
    """Escape *text* for rich text, keeping runs of spaces intact."""
    return _html.escape(text).replace(" ", "&nbsp;")


def _hl_css(hl: Highlight) -> str:
    """Inline CSS for a parsed highlight format."""
    parts = []
    if hl.color:
        parts.append(f"color:{_theme.resolve_color(hl.color)}")
    if hl.bold:
        parts.append("font-weight:bold")
    if hl.italic:
        parts.append("font-style:italic")
    parts.append("text-decoration:underline" if hl.underline else "text-decoration:none")
    return ";".join(parts)


def _hl(text: str, hl: Highlight = _HL_DEFAULT) -> str:
    """Apply the highlight format *hl* to the already-escaped *text*."""
    if hl.raw is not None:
        return hl.raw.replace("{}", text)
    return (f'<span style="{_hl_css(hl)}">'
            f'{_sp(hl.prefix)}{text}{_sp(hl.suffix)}</span>')


def _rich(text: str) -> str:
    """Mark *text* as rich text for _ElidingLabel, even without any tag."""
    return f"<span>{text}</span>"


def _candidate_html(
    candidates: list[str], cursor: int, hl: Highlight = _HL_DEFAULT_CANDIDATES
) -> str:
    """Render a status-bar candidate list, highlighting the one at *cursor*."""
    return "&nbsp;&nbsp;".join(
        _hl(_sp(c), hl) if i == cursor else _sp(c)
        for i, c in enumerate(candidates)
    )


def _value_href(axis_idx: int, val_idx: int) -> str:
    """Anchor target encoding one (axis, value) pair for the info sidebar."""
    return _VALUE_HREF.format(axis=axis_idx, value=val_idx)


def _parse_value_href(href: str) -> tuple[int, int] | None:
    """Inverse of :func:`_value_href`; returns None for foreign hrefs."""
    m = _VALUE_HREF_RE.match(href)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _axis_href(axis_idx: int) -> str:
    """Anchor target of one axis header in the info sidebar."""
    return _AXIS_HREF.format(axis=axis_idx)


def _parse_axis_href(href: str) -> int | None:
    """Inverse of :func:`_axis_href`; returns None for foreign hrefs."""
    m = _AXIS_HREF_RE.match(href)
    return int(m.group(1)) if m else None


def _edit_href(axis_idx: int) -> str:
    """Anchor target of the [+] after one axis header, which opens its value picker."""
    return _EDIT_HREF.format(axis=axis_idx)


def _parse_edit_href(href: str) -> int | None:
    """Inverse of :func:`_edit_href`; returns None for foreign hrefs."""
    m = _EDIT_HREF_RE.match(href)
    return int(m.group(1)) if m else None


def _reveal_in_file_manager(path: str) -> bool:
    """Show *path* selected in the platform's file manager; False if that failed.

    Windows and macOS have a native "select this file" call.  Linux has none
    in general: the FileManager1 D-Bus interface (Nautilus, Dolphin, Nemo,
    Thunar, ...) does it where a session bus exists, and elsewhere the best
    on offer is opening the containing folder.  A path that does not exist
    falls back to its nearest existing parent folder.
    """
    path = os.path.abspath(path)
    target = path
    while not os.path.exists(target):
        parent = os.path.dirname(target)
        if parent == target:
            return False
        target = parent
    is_file = os.path.isfile(target)
    try:
        if sys.platform == "win32":
            # explorer reports exit status 1 even on success, so don't check it.
            arg = f"/select,{target}" if is_file else target
            subprocess.Popen(["explorer", arg])
            return True
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", target] if is_file else ["open", target],
                           check=True, timeout=5)
            return True
        if is_file and shutil.which("dbus-send"):
            uri = QUrl.fromLocalFile(target).toString()
            done = subprocess.run(
                ["dbus-send", "--session", "--print-reply",
                 "--dest=org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1.ShowItems", f"array:string:{uri}", "string:"],
                capture_output=True, timeout=5,
            )
            if done.returncode == 0:
                return True
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Could not reveal %s: %s", target, e)
    folder = target if os.path.isdir(target) else os.path.dirname(target)
    return QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


# `:pattern` flags overriding the `pattern.subset` setting for one run.
_PATTERN_FLAGS = {"--keep-subset": "keep", "--ignore-subset": "ignore"}


def _split_pattern_flags(arg: str) -> tuple[str, str | None]:
    """Split `:pattern` flags off either end of *arg*: (path, subset or None)."""
    tokens = arg.split()
    leading = trailing = None
    while tokens and tokens[0] in _PATTERN_FLAGS:
        leading = _PATTERN_FLAGS[tokens.pop(0)]
    while tokens and tokens[-1] in _PATTERN_FLAGS:
        trailing = trailing or _PATTERN_FLAGS[tokens[-1]]   # the last flag wins
        tokens.pop()
    return " ".join(tokens), trailing or leading


def _n_combos(axes) -> int:
    """Number of combinations (images) of *axes*: a dict or a list of value lists."""
    n = 1
    for vals in (axes.values() if isinstance(axes, dict) else axes):
        n *= vals if isinstance(vals, int) else len(vals)
    return n


def _new_values(keep: dict[str, list[str]], fresh, known: dict[str, list[str]]
                ) -> dict[str, list[str]]:
    """Values of the axes in *keep* that a fresh scan (*fresh*, a Config or
    an exception) finds but the old pattern did not have (*known*).

    Axes new to the pattern are skipped: they come with all their values.
    """
    if isinstance(fresh, Exception) or fresh is None:
        return {}
    out = {}
    for name, vals in keep.items():
        if name not in known:
            continue
        extra = [v for v in fresh.axes.get(name, [])
                 if v not in known[name] and v not in vals]
        if extra:
            out[name] = extra
    return out


def _same_values(a: dict[str, list[str]], b: dict[str, list[str]]) -> bool:
    """Whether two axis dicts hold the same values, in whatever order."""
    return a.keys() == b.keys() and all(set(a[n]) == set(b[n]) for n in a)


def _images_with_factor(n: int, now: int) -> str:
    """`432 images (×2)`: *n* and its ratio to the *now* images on show."""
    text = f"{n} image{'s' if n != 1 else ''}"
    if now and n != now:
        text += f" (×{n / now:.3g})"
    return text


def _axis_count_changes(current: dict[str, int], new: dict[str, int]) -> list[str]:
    """Per-axis value counts that differ between *current* and *new*, e.g.
    `dmm 3→4`, `ref new (2)`, `date gone`."""
    out = []
    for name, n in new.items():
        if name not in current:
            out.append(f"{name} new ({n})")
        elif current[name] != n:
            out.append(f"{name} {current[name]}→{n}")
    out += [f"{name} gone" for name in current if name not in new]
    return out


def _shell_quote(arg: str) -> str:
    """Quote *arg* for pasting into the user's shell (POSIX shells, cmd, PowerShell).

    On Windows anything beyond plain path characters is double-quoted: that
    is the one quoting cmd and PowerShell agree on (PowerShell would read a
    bare `'` as the start of a string and a bare `,` as an array).
    """
    if sys.platform != "win32":
        return shlex.quote(arg)
    if _re.fullmatch(r"[\w./\\:@+=-]+", arg):
        return arg
    return '"' + arg.replace('"', '\\"') + '"'


def _dispose(dialog: QWidget) -> None:
    """Close a finished dialogue and have Qt delete it.

    A dialogue parented to the window is owned by it, so without this every
    value picker or progress dialogue opened would live as long as the window.
    """
    dialog.close()
    dialog.deleteLater()


def _images_label(verb: str, n: int) -> str:
    """Progress text as on startup, e.g. "Loading 4 images…"."""
    return f"{verb} {n} image{'s' if n != 1 else ''}…"


def _shared_sftp(conn: list, remote, get_password):
    """The SFTP client of the session's shared *conn* (`[ssh, sftp]`),
    reconnecting first when there is none or its transport has died.

    The password asked at startup is cached, so a reconnect does not ask again.
    Call with the viewer's remote lock held.
    """
    ssh, sftp = conn
    transport = ssh.get_transport() if ssh is not None else None
    if sftp is None or transport is None or not transport.is_active():
        for c in (sftp, ssh):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        from .loader import _connect_sftp
        conn[0], conn[1] = _connect_sftp(remote, get_password)
    return conn[1]


def _pack_rows(widths: list[int], sep_w: int, limit: int) -> list[list[int]]:
    """Group item indices into rows, each at most *limit* pixels wide.

    *widths* are the item widths and *sep_w* the gap between neighbours in a
    row.  Items go in order, filling a row before starting the next, so the
    caller never leaves it to the text engine to decide where a value breaks.
    An item wider than *limit* still gets a row of its own.
    """
    rows: list[list[int]] = []
    used = 0
    for idx, w in enumerate(widths):
        if rows and used + sep_w + w <= limit:
            rows[-1].append(idx)
            used += sep_w + w
        else:
            rows.append([idx])
            used = w
    return rows


# ── key-binding parser ────────────────────────────────────────────────────────

_MOD_MAP: dict[str, int] = {
    "ctrl": Qt.ControlModifier.value, "control": Qt.ControlModifier.value,
    "shift": Qt.ShiftModifier.value,
    "alt": Qt.AltModifier.value,
    "meta": Qt.MetaModifier.value,
}

_KEY_MAP: dict[str, int] = {
    "return": Qt.Key_Return, "enter": Qt.Key_Return,
    "escape": Qt.Key_Escape, "esc": Qt.Key_Escape,
    "space": Qt.Key_Space, "tab": Qt.Key_Tab,
    "backspace": Qt.Key_Backspace, "delete": Qt.Key_Delete,
    "home": Qt.Key_Home, "end": Qt.Key_End,
    "left": Qt.Key_Left, "right": Qt.Key_Right,
    "up": Qt.Key_Up, "down": Qt.Key_Down,
    **{f"f{n}": getattr(Qt, f"Key_F{n}") for n in range(1, 13)},
    **{str(n): getattr(Qt, f"Key_{n}") for n in range(10)},
}


def _parse_key(s: str) -> tuple[int, int]:
    """Parse 'Ctrl+Shift+H' → (Qt.Key value, modifier flags int)."""
    mods = 0
    key: int | None = None
    for part in (p.strip() for p in s.split("+")):
        lower = part.lower()
        if lower in _MOD_MAP:
            mods |= _MOD_MAP[lower]
        elif len(part) == 1 and part.isalpha():
            key = getattr(Qt, f"Key_{part.upper()}")
        elif lower in _KEY_MAP:
            key = _KEY_MAP[lower]
        else:
            raise ValueError(f"unknown key token: {part!r}")
    if key is None:
        raise ValueError(f"no key in binding: {s!r}")
    return key, mods


def _chord_str(key: int, mods: int) -> str:
    """Reconstruct a human-readable chord like 'Ctrl+Shift+H' from parsed values."""
    parts = []
    if mods & Qt.ControlModifier.value:
        parts.append("Ctrl")
    if mods & Qt.ShiftModifier.value:
        parts.append("Shift")
    if mods & Qt.AltModifier.value:
        parts.append("Alt")
    if mods & Qt.MetaModifier.value:
        parts.append("Meta")
    if Qt.Key_A <= key <= Qt.Key_Z:
        parts.append(chr(key - Qt.Key_A + ord('A')))
    else:
        for name, val in _KEY_MAP.items():
            if val == key and not name[0].isdigit():
                parts.append(name.capitalize())
                break
        else:
            parts.append(f"0x{key:x}")
    return "+".join(parts) if parts else "?"


def _modifier_roles(swap: bool) -> tuple[Qt.KeyboardModifier, Qt.KeyboardModifier]:
    """(value-picker modifier, reverse-navigation modifier) for axis keys.

    Ctrl picks and Shift reverses by default; *swap* exchanges the two for
    users who would rather hold Shift for the picker.
    """
    if swap:
        return Qt.ShiftModifier, Qt.ControlModifier
    return Qt.ControlModifier, Qt.ShiftModifier


def _keybinding_conflicts(
    parsed_bindings: dict[tuple[int, int], str],
    axis_letters: set[str],
    swap_modifiers: bool = False,
) -> list[str]:
    """Return one human-readable description per binding that shadows navigation.

    Conflicts per mode (key type → affected modes):
      bare letter       → seek always; tap + pin if the letter is an axis key
      picker modifier   → tap + pin if letter is an axis key (value picker)
      reverse modifier  → tap if letter is an axis key (navigate -1)

    Which physical modifier plays which role follows *swap_modifiers*, so the
    warning names the chord that is actually shadowed.
    """
    picker, reverse = _modifier_roles(swap_modifiers)
    picker, reverse = picker.value, reverse.value
    conflicts = []
    for (key, mods), action in sorted(parsed_bindings.items()):
        if not (Qt.Key_A <= key <= Qt.Key_Z):
            continue
        letter = chr(key - Qt.Key_A + ord('a'))
        clashing = []
        if mods == 0:
            clashing.append("seek")
            if letter in axis_letters:
                clashing += ["tap", "pin"]
        elif mods == picker:
            if letter in axis_letters:
                clashing += ["tap", "pin"]
        elif mods == reverse:
            if letter in axis_letters:
                clashing.append("tap")
        if clashing:
            axis_note = f" (axis key '{letter}')" if letter in axis_letters else ""
            conflicts.append(
                f"{_chord_str(key, mods)} → {action!r}: shadows "
                f"{'/'.join(clashing)} mode{axis_note}"
            )
    return conflicts


_COMMANDS = [
    "axis-auto",
    "axis-h",
    "axis-v",
    "change-key",
    "copy-cli-command",
    "copy-image",
    "copy-path",
    "dim-missing",
    "fit",
    "fit-height",
    "fit-off",
    "fit-width",
    "fullscreen",
    "grid",
    "grid-dialog",
    "grid-layout",
    "grid-off",
    "grid-sharex",
    "grid-sharey",
    "info",
    "keys",
    "mode",
    "pattern",
    "quit",
    "reload",
    "remove-axis",
    "settings",
    "skip-missing",
    "remove-value",
    "reveal",
    "subset",
    "subset-interactive",
    "ungrid",
    "values",
    "version",
    "watch",
    "write",
    "swap-axes",
    "switch-last",
    "theme",
    "zoom",
]

_ALIASES: dict[str, str] = {
    "q": "quit",
    "w": "write",
}

# Discrete argument options for commands that take a value.
# Commands absent from this dict take no arguments (or free-text only).
# axis-h and axis-v completions are dynamic (axis names); handled in _cmd_candidates.
_CMD_ARGS: dict[str, list[str]] = {
    "axis-h": [],
    "axis-v": [],
    "change-key": [],
    "dim-missing": ["on", "off"],
    "grid": [],
    "grid-layout": [],
    "grid-sharex": ["on", "off"],
    "grid-sharey": ["on", "off"],
    "mode": ["tap", "seek", "pin"],
    "pattern": [],  # free-text path / template
    "remove-axis": [],
    "remove-value": [],
    "skip-missing": ["on", "off"],
    "subset": [],   # axis name; the values are then edited as free text
    "values": [],   # axis name; opens the value picker
    "theme": ["auto", "dark", "light"],
    "watch": ["true", "false"],
    "write": [],    # free-text path; empty → file dialog
    "zoom": ["50", "75", "100", "150", "200"],
}

# What a command expects as its argument, shown while the argument is empty or
# does not match any listed option.
_ARG_FORMATS: dict[str, str] = {
    "grid-layout": "NxM  (rows x columns, e.g. 2x3)",
}

# Commands whose argument is free-text — preserve case when the user types it.
# Free-text commands whose argument starts with an axis name, and what
# follows it ("value": one of its values, "key": a letter, "": nothing).
_AXIS_FIRST_ARGS = {"remove-value": "value", "change-key": "key", "remove-axis": ""}

_FREE_TEXT_ARGS = {"change-key", "pattern", "remove-axis", "remove-value", "write"}

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _is_free_text(cmd: dict | None) -> bool:
    """Whether the command prompt *cmd* is currently taking free-text input."""
    return (cmd is not None and cmd.get("phase") == "arg"
            and (cmd.get("verb") in _FREE_TEXT_ARGS or "axis" in cmd))


def format_values(values) -> str:
    """Join axis *values* into one editable line; a value with blanks is quoted."""
    return " ".join(f'"{v}"' if _re.search(r"\s", v) else v for v in values)


def _common_prefix(words: list[str]) -> str:
    """Longest common prefix of *words*, ignoring case in the comparison."""
    first = words[0]
    n = len(first)
    for w in words[1:]:
        n = min(n, len(w))
        while n and first[:n].lower() != w[:n].lower():
            n -= 1
    return first[:n]


def parse_values(text: str) -> list[str]:
    """Inverse of `format_values`: split on blanks, honouring double quotes."""
    return [q or w for q, w in _re.findall(r'"([^"]*)"|(\S+)', text)]

_COMMAND_HELP: dict[str, str] = {
    "axis-auto":    "restore dynamic axis-to-arrow assignment",
    "change-key":   "assign a key letter to an axis  (e.g. change-key sensor x)",
    "copy-image":   "copy current image to clipboard",
    "copy-path":    "copy current image file path to clipboard",
    "copy-cli-command": "copy a juxt command line that reopens this view (axes, values, grid)",
    "reveal":       "show the current image file in the system file manager",
    "dim-missing":  "dim sidebar values with no file on the last-moved axis  (on / off)",
    "axis-h":       "lock the primary (←/→) axis to a named axis",
    "axis-v":       "lock the secondary (↑/↓) axis to a named axis",
    "fit":          "fit image to window",
    "fit-height":   "fit image height to viewport",
    "fit-off":      "stop auto-fitting; keep the current zoom",
    "fit-width":    "fit image width to viewport",
    "fullscreen":   "toggle fullscreen",
    "grid":         "expand an axis into a tiled grid  (e.g. grid sensor  /  grid sensor SMAP SMOS  /  grid sensor 2x2)",
    "grid-dialog":  "open the point-and-click grid builder  (Ctrl+Shift+G)",
    "grid-layout":  "change the grid layout without exiting  (e.g. grid-layout 2x3)",
    "grid-off":     "leave grid view and return to the single image  (same as ungrid)",
    "grid-sharex":  "toggle or set synchronized horizontal pan/zoom  (on / off)",
    "grid-sharey":  "toggle or set synchronized vertical pan/zoom  (on / off)",
    "info":         "toggle the info sidebar",
    "keys":         "toggle the keyboard shortcut sidebar  (Ctrl+Shift+K)",
    "mode":         "switch navigation mode  (tap / seek / pin)",
    "pattern":      "change the template / source path without restarting  (--keep-subset / --ignore-subset)",
    "quit":         "quit juxt",
    "reload":       "re-detect axes and reload images",
    "settings":     "open ~/.juxt/settings.yaml in the default editor",
    "remove-axis":  "remove an axis (collapses to its current value)",
    "remove-value": "remove a value from an axis  (e.g. remove-value sensor SMAP)",
    "subset":       "edit the values of an axis: delete, reorder, rename, add  (e.g. subset sensor)",
    "ungrid":       "return to single-image view",
    "subset-interactive": "pick the values of every axis from those on disk: add, remove",
    "values":       "pick the values of one axis from those on disk: add, remove  (e.g. values sensor)",
    "version":      "show the juxt version",
    "write":        "write current config to a YAML file",
    "swap-axes":    "swap the primary (←/→) and secondary (↑/↓) axes",
    "skip-missing": "skip combinations with no file when cycling  (on / off)",
    "switch-last":  "toggle between current and previous position",
    "theme":        "switch the colour theme  (auto / dark / light; no argument flips dark and light)",
    "watch":        "enable / disable / configure file watching",
    "zoom":         "set zoom level  (e.g. zoom 150)",
}


def _cmd_query_window(query: str, caret: int, max_visible: int = 55) -> tuple[int, int]:
    """The `(start, end)` slice of *query* shown in at most *max_visible*
    characters, scrolled to keep *caret* roughly centred."""
    if len(query) <= max_visible:
        return 0, len(query)
    start = max(0, min(caret - max_visible // 2, len(query) - max_visible))
    return start, start + max_visible


def _cmd_query_display(query: str, caret: int, max_visible: int = 55) -> str:
    """Return a viewport into *query* with ▌ at *caret*, scrolling to keep it visible."""
    start, end = _cmd_query_window(query, caret, max_visible)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(query) else ""
    return f"{prefix}{query[start:caret]}▌{query[caret:end]}{suffix}"


def _is_word_char(ch: str) -> bool:
    """Characters Ctrl+Backspace / Ctrl+←/→ treat as one word (`sm_vs_peatclsm`)."""
    return ch.isalnum() or ch == "_"


def _word_left(text: str, pos: int) -> int:
    """Start of the word before *pos*, skipping separators first."""
    while pos > 0 and not _is_word_char(text[pos - 1]):
        pos -= 1
    while pos > 0 and _is_word_char(text[pos - 1]):
        pos -= 1
    return pos


def _word_right(text: str, pos: int) -> int:
    """End of the word after *pos*, skipping separators first."""
    while pos < len(text) and not _is_word_char(text[pos]):
        pos += 1
    while pos < len(text) and _is_word_char(text[pos]):
        pos += 1
    return pos


class _ElidingLabel(QLabel):
    """QLabel that elides its content (plain text or HTML) to fit the available width."""

    clicked = Signal(float)   # x of a left click, from the start of the text

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(event.position().x() - self.contentsRect().left())
        super().mousePressEvent(event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_plain = ""
        self._full_html: str | None = None
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        if "<" in text:
            self._full_html = text
            self._full_plain = _html.unescape(_re.sub(r"<[^>]+>", "", text))
        else:
            self._full_html = None
            self._full_plain = text
        self._recompute()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recompute()

    def _recompute(self):
        w = self.width()
        if w <= 0:
            return
        fm = self.fontMetrics()
        if fm.horizontalAdvance(self._full_plain) <= w:
            super().setText(self._full_html if self._full_html is not None else self._full_plain)
        else:
            super().setText(fm.elidedText(self._full_plain, Qt.TextElideMode.ElideRight, w))


class _Cancelled(BaseException):
    """Raised inside a pattern worker to signal user-initiated cancellation."""


class NavMode(IntEnum):
    TAP = 0
    SEEK = 1
    PIN = 2

    @property
    def label(self) -> str:
        return ("tap", "seek", "pin")[self]


# Longest title text after "juxt | ".  Past this the path gives way, from the
# left, because the end of a template is what tells two sessions apart.
_TITLE_MAX = 100
# Longest "[axis: values]" suffix; pinning many values must not push the path out.
_TITLE_PINS_MAX = 40
# Fewest path characters kept, however much else the title carries.
_TITLE_PATH_MIN = 20


def _collapse_home(path: str, home: str) -> str:
    """*path* with a leading *home* directory written as ``~``."""
    home = home.rstrip("/\\")
    if home and path.startswith(home) and path[len(home):len(home) + 1] in ("/", "\\"):
        return "~" + path[len(home):]
    return path


def _elide_path(path: str, room: int) -> str:
    """*path* cut from the left to at most *room* characters.

    Starts at a path separator where it can, so the first component shown is
    a whole one, and is marked with a leading ``…/``.
    """
    if len(path) <= room:
        return path
    tail = path[-max(room - 2, 1):]
    if path[len(path) - len(tail) - 1] in "/\\":
        return "…/" + tail                  # the cut fell between components
    cut = _re.search(r"[\\/]", tail)
    if cut is not None and cut.end() < len(tail):
        return "…/" + tail[cut.end():]      # drop the partial first component
    return "…" + path[-max(room - 1, 1):]   # one component too long on its own


def _title_pins(config: "Config", pinned: Collection[str]) -> str:
    """``[axis: a, b]`` for the axes pinned on the command line, with the
    values they hold now (a reload may have changed them); "" for none."""
    parts = [f"{name}: {', '.join(config.axes[name])}"
             for name in pinned if config.axes.get(name)]
    text = "; ".join(parts)
    if len(text) > _TITLE_PINS_MAX:
        text = text[:_TITLE_PINS_MAX - 1].rstrip(" ,;") + "…"
    return f"[{text}]" if text else ""


def _window_title(
    config: "Config",
    session_name: str | None = None,
    pinned: Collection[str] = (),
    home: str | None = None,
) -> str:
    """The window title: ``--name`` if given, else the template path.

    The template is shown whole with the home directory as ``~`` (a remote
    session leads with its host instead), followed by any pinned values, and
    is cut from the left once the text after "juxt | " would pass _TITLE_MAX.
    """
    if session_name:
        return f"juxt | {session_name}"
    path = config.template
    if config.remote:
        lead = f"{config.remote.host}: "
    else:
        lead = ""
        path = _collapse_home(path, str(Path.home()) if home is None else home)
    pins = _title_pins(config, pinned)
    reserved = len(lead) + (len(pins) + 1 if pins else 0)
    path = _elide_path(path, max(_TITLE_MAX - reserved, _TITLE_PATH_MIN))
    return f"juxt | {lead}{path}" + (f" {pins}" if pins else "")


class _CellView(QGraphicsView):
    """Single-image viewport used inside GridWidget."""

    zoomed = Signal(object)  # emits self.transform() after a wheel-zoom
    clicked = Signal()       # emits on mouse press, to claim grid focus
    menu_requested = Signal(object)  # emits the global QPoint of a right-click

    def __init__(self, label: str, parent=None):
        scene = QGraphicsScene()
        super().__init__(scene, parent)
        self._scene = scene
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        scene.addItem(self._item)
        self._fit: Literal["image", "height", "width"] | None = None
        self._label_text = label
        self._focused = False

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.apply_theme()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)

    def apply_theme(self):
        self.setBackgroundBrush(QColor(_theme.current().canvas))
        self.viewport().update()

    def set_pixmap(self, pm: QPixmap):
        self._item.setPixmap(pm)
        self._scene.setSceneRect(QRectF(pm.rect()))
        if self._fit is not None:
            getattr(self, f"fit_{self._fit}")()

    def set_label(self, text: str):
        """Cells are reused as the visible window scrolls, so labels change."""
        if text != self._label_text:
            self._label_text = text
            self.viewport().update()

    def set_focused(self, on: bool):
        if on != self._focused:
            self._focused = on
            self.viewport().update()

    def fit_image(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._fit = "image"

    def fit_height(self):
        pm = self._item.pixmap()
        if pm.isNull():
            return
        self.resetTransform()
        self.scale(self.viewport().height() / pm.height(),
                   self.viewport().height() / pm.height())
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "height"

    def fit_width(self):
        pm = self._item.pixmap()
        if pm.isNull():
            return
        self.resetTransform()
        self.scale(self.viewport().width() / pm.width(),
                   self.viewport().width() / pm.width())
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "width"

    def set_zoom(self, pct: float):
        self._fit = None
        self.resetTransform()
        self.scale(pct / 100.0, pct / 100.0)

    def reset_zoom(self):
        self._fit = None
        self.resetTransform()

    def fit_off(self):
        self._fit = None

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 ** (event.angleDelta().y() / 120)
            self.scale(factor, factor)
            self._fit = None
            self.zoomed.emit(self.transform())
        else:
            super().wheelEvent(event)

    def drawForeground(self, painter: QPainter, _rect):
        painter.save()
        painter.resetTransform()   # switch to viewport pixel coordinates
        # Focus accent, matching the :default button border in dialogs.py.
        accent = QColor(_theme.current().accent)
        if self._focused:
            # Marks the cell the grid axis is currently on — the one that
            # scrolls the window when it steps past an edge.
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.viewport().rect().adjusted(1, 1, -1, -1))
        if self._label_text:
            fm = painter.fontMetrics()
            pad_x, pad_y, margin = 5, 2, 4
            text_w = fm.horizontalAdvance(self._label_text)
            text_h = fm.height()
            bg = QRectF(margin, margin, text_w + 2 * pad_x, text_h + 2 * pad_y)
            # The label sits on the image, not on the theme's background,
            # so only the focused one takes a theme colour.
            painter.fillRect(bg, accent if self._focused else QColor(0, 0, 0, 160))
            painter.setPen(QColor(255, 255, 255) if self._focused
                           else QColor(224, 224, 224))
            painter.drawText(
                int(bg.x() + pad_x),
                int(bg.y() + pad_y + text_h - fm.descent()),
                self._label_text,
            )
        painter.restore()

    def mousePressEvent(self, event):
        # Press, not release, so claiming focus does not wait out a pan drag.
        self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, _event):
        self.fit_image()

    def contextMenuEvent(self, event):
        # The press that opened the menu already claimed grid focus, so the
        # menu speaks about this pane.
        self.menu_requested.emit(event.globalPos())
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit is not None:
            getattr(self, f"fit_{self._fit}")()


class GridWidget(QWidget):
    """Grid of independent image viewports with optional scroll/zoom sync."""

    focus_requested = Signal(int)   # cell index the user clicked into

    def __init__(
        self,
        image_view: "ImageView",
        rows: int,
        cols: int,
        labels: list[str],
        sharex: bool,
        sharey: bool,
    ):
        super().__init__()
        self._image_view = image_view
        self._sharex = sharex
        self._sharey = sharey
        self._syncing = False
        self._rows = rows
        self._cols = cols

        layout = QGridLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(0, 0, 0, 0)
        self._cells: list[_CellView] = []

        # The layout is a hard cap: with more values than slots the extra ones
        # are reached by scrolling the window, not by growing the grid.
        for i, label in enumerate(labels[: rows * cols]):
            row, col = i // cols, i % cols
            cell = _CellView(label)
            layout.addWidget(cell, row, col)
            self._cells.append(cell)
            cell.horizontalScrollBar().valueChanged.connect(
                lambda val, c=cell: self._on_hscroll(val, c)
            )
            cell.verticalScrollBar().valueChanged.connect(
                lambda val, c=cell: self._on_vscroll(val, c)
            )
            cell.zoomed.connect(lambda t, c=cell: self._on_zoom(t, c))
            cell.clicked.connect(lambda i=i: self.focus_requested.emit(i))
            cell.menu_requested.connect(self._image_view.show_image_menu)

    def apply_theme(self):
        for cell in self._cells:
            cell.apply_theme()

    # ── sync handlers ─────────────────────────────────────────────────────────

    def _on_hscroll(self, val: int, source: _CellView):
        if self._syncing or not self._sharex:
            return
        self._syncing = True
        for cell in self._cells:
            if cell is not source:
                cell.horizontalScrollBar().setValue(val)
        self._syncing = False

    def _on_vscroll(self, val: int, source: _CellView):
        if self._syncing or not self._sharey:
            return
        self._syncing = True
        for cell in self._cells:
            if cell is not source:
                cell.verticalScrollBar().setValue(val)
        self._syncing = False

    def _on_zoom(self, transform, source: _CellView):
        if self._syncing or (not self._sharex and not self._sharey):
            return
        self._syncing = True
        h = source.horizontalScrollBar().value()
        v = source.verticalScrollBar().value()
        for cell in self._cells:
            if cell is not source:
                cell.setTransform(transform)
                if self._sharex:
                    cell.horizontalScrollBar().setValue(h)
                if self._sharey:
                    cell.verticalScrollBar().setValue(v)
        self._syncing = False

    # ── public API ────────────────────────────────────────────────────────────

    def capacity(self) -> int:
        """How many values fit on screen at once."""
        return len(self._cells)

    def update_cells(
        self,
        base_pos: list,
        axis_idx: int,
        indices: list[int],
        labels: list[str],
        pixmaps: dict,
        focused: int = -1,
    ):
        """Fill the cells from *indices* — the currently visible window."""
        for slot, cell in enumerate(self._cells):
            if slot >= len(indices):
                cell.set_focused(False)
                continue
            key = list(base_pos)
            key[axis_idx] = indices[slot]
            pm = pixmaps.get(tuple(key))
            if pm and not pm.isNull():
                cell.set_pixmap(pm)
            cell.set_label(labels[slot])
            cell.set_focused(slot == focused)

    def fit_image(self):
        for cell in self._cells:
            cell.fit_image()

    def fit_height(self):
        for cell in self._cells:
            cell.fit_height()

    def fit_width(self):
        for cell in self._cells:
            cell.fit_width()

    def fit_off(self):
        for cell in self._cells:
            cell.fit_off()

    def set_zoom(self, pct: float):
        for cell in self._cells:
            cell.set_zoom(pct)

    def reset_zoom(self):
        for cell in self._cells:
            cell.reset_zoom()

    def set_sharex(self, val: bool):
        self._sharex = val

    def set_sharey(self, val: bool):
        self._sharey = val

    def keyPressEvent(self, event):
        self._image_view.keyPressEvent(event)

    def mouseDoubleClickEvent(self, _event):
        self.fit_image()


class ImageView(QGraphicsView):
    state_changed = Signal()
    toggle_bar = Signal()
    toggle_info = Signal()
    toggle_keys = Signal()
    theme_requested = Signal(str)       # "auto" / "dark" / "light", or "" to flip
    config_changed = Signal()           # emitted after reload/pattern successfully replaces config
    grid_mode_changed = Signal(object)  # emits GridWidget on enter, None on exit
    _poll_result = Signal(object)       # emitted from poll worker; carries list or Exception
    _reload_result = Signal(object)     # emitted from reload worker; carries tuple or Exception
    _pattern_found = Signal(object)     # pattern discovery: dict | Exception | None (cancelled)
    _pattern_result = Signal(object)    # emitted from pattern worker; carries dict or Exception
    _pattern_progress = Signal(int, int, str)  # value, total, label
    _values_found = Signal(object)      # value-picker listing: (axis, found | Exception); (None, None) = done
    _values_loaded = Signal(object)     # remote subset download: dict | Exception | None

    def __init__(
        self,
        config: Config,
        pixmaps: dict[tuple, QPixmap],
        parent=None,
        watch: bool = True,
        remote_tmpdir: str | None = None,
        get_password: object = None,
        poll_interval: int = 0,
        remote_mtimes: dict | None = None,
        axis_h: str | None = None,
        axis_v: str | None = None,
        seek_greedy: bool = True,
        seek_fuzzy: bool = True,
        swap_modifiers: bool = False,
        skip_missing: bool = False,
        dim_missing: bool = False,
        sidebar_copy: str = "selected",
        pattern_subset: str = "smart",
        keybindings: dict[str, str] | None = None,
        grid: str | None = None,
        grid_values: list[str] | None = None,
        grid_layout: tuple[int, int] | None = None,
        grid_sharex: bool = True,
        grid_sharey: bool = True,
    ):
        self._scene = QGraphicsScene()
        super().__init__(self._scene, parent)

        self.config = config
        self.pixmaps = pixmaps
        self.axis_names = list(config.axes.keys())
        self.axis_values = list(config.axes.values())
        self.n_axes = len(self.axis_names)

        self.pos: list[int] = [0] * self.n_axes
        self.prev: list[int] | None = None

        # Most recently focused axis first; focus_stack[0] → ←/→, [1] → ↑/↓
        self.focus_stack: list[int] = list(range(self.n_axes))

        # letter → axis index; rebuilt when mode changes
        self.key_to_axis: dict[str, int] = {}
        self._rebuild_key_to_axis()

        self.nav_mode = NavMode(config.mode)

        self._flash_msg: str | None = None
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._active_axis: int | None = None
        self._active_axis_timer = QTimer(self)
        self._active_axis_timer.setSingleShot(True)
        self._active_axis_timer.timeout.connect(self._clear_active_axis)

        # Set by _refresh() when the change that triggered it should force
        # the info sidebar to scroll the given (axis, value) into view,
        # e.g. letter-shortcut stepping in tap mode; None otherwise, which
        # keeps the sidebar's current scroll position instead.
        self._sidebar_target: tuple[int, int] | None = None

        # Read-only text widgets (the sidebars) whose selection Ctrl+C copies.
        self._copy_sources: list[QTextEdit] = []

        self._watching = False
        self._watcher: QFileSystemWatcher | None = None
        self._path_to_key: dict[str, tuple] = {}
        self._tab_matches: list[str] = []
        self._fit: Literal["image", "height", "width"] | None = None
        self._pre_fullscreen_maximized: bool | None = None
        self._seek_greedy: bool = seek_greedy
        self._seek_fuzzy: bool = seek_fuzzy
        self._swap_modifiers: bool = swap_modifiers
        self._skip_missing: bool = skip_missing
        self._dim_missing: bool = dim_missing
        self._sidebar_copy: str = sidebar_copy
        self._keybindings: dict[tuple[int, int], str] = {}
        for _ks, _action in (keybindings or {}).items():
            try:
                _k, _m = _parse_key(_ks)
                self._keybindings[(_k, _m)] = _action
            except ValueError as e:
                log.warning("Invalid keybinding %r: %s", _ks, e)

        self._remote_tmpdir: str | None = remote_tmpdir
        self._get_password: object = get_password
        self._poll_interval: int = poll_interval if poll_interval > 0 else 5
        self._poll_in_progress: bool = False
        self._reload_in_progress: bool = False
        self._busy_label: str | None = None       # status-bar text while _reload_in_progress
        # The value picker's search of the disk is running (it shows its own
        # spinner); :reload and :pattern wait for it.
        self._listing_in_progress: bool = False
        self._remote_conn: list = [None, None]   # [ssh_client, sftp_client], worker-thread only
        # Serialises workers that share _remote_conn (poll, value picker, subset
        # download): an SFTP session runs one request sequence at a time.
        self._remote_lock = threading.Lock()
        # Bumped whenever the axes change, so a worker started on the old axes
        # can tell its index-tuple keys no longer mean the same images.
        self._axes_gen: int = 0
        self._remote_mtimes: dict = remote_mtimes if remote_mtimes is not None else {}
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._start_poll_worker)
        self._poll_result.connect(self._apply_remote_poll)
        self._reload_result.connect(self._apply_reload)
        self._pattern_found.connect(self._on_pattern_found)
        self._pattern_result.connect(self._apply_pattern)
        # Pending `pattern.subset: cli` question: {"found": dict, "valid": dict}.
        self._pattern_choice: dict | None = None
        self._pattern_subset: str = pattern_subset
        self._pattern_progress.connect(self._on_pattern_progress)
        self._pattern_dlg = None
        self._values_found.connect(self._on_values_found)
        self._values_dlg = None   # the open value picker, if any
        self._values_loaded.connect(self._on_subset_downloaded)
        self._initial_fit_done = False
        self._locked_h: int | None = self.axis_names.index(axis_h) if axis_h and axis_h in self.axis_names else None
        self._locked_v: int | None = self.axis_names.index(axis_v) if axis_v and axis_v in self.axis_names else None

        # Active incremental-search state (value picker and multi-select).
        # None  = no active selection
        # axis phase:  {"phase": "axis",  "query": str}
        # value phase: {"phase": "value", "query": str, "axis_idx": int}
        self._sel: dict | None = None

        # Active command-mode state; None = not in command mode.
        # verb phase: {"phase": "verb", "query": str, "cursor": int}
        # arg  phase: {"phase": "arg",  "verb": str, "query": str, "cursor": int}
        self._cmd: dict | None = None

        self.setFocusPolicy(Qt.StrongFocus)
        QApplication.instance().installEventFilter(self)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor(_theme.current().canvas))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)

        # Grid-view state
        self._grid_axis: int | None = None
        self._grid_filter: list[int] | None = None   # None = all values
        self._grid_layout: tuple[int, int] | None = None  # explicit (rows, cols)
        self._grid_slots: list[int] = []  # value index shown in each pane
        self._grid_focus: int = 0        # pane the grid axis acts on
        self._grid_sharex: bool = grid_sharex
        self._grid_sharey: bool = grid_sharey
        self._grid_widget: GridWidget | None = None

        self._refresh()

        if grid and grid in self.axis_names:
            axis_idx = self.axis_names.index(grid)
            filter_indices: list[int] | None = None
            if grid_values:
                vals = self.axis_values[axis_idx]
                filter_indices = [i for i, v in enumerate(vals) if v in grid_values] or None
            log.debug("startup grid: axis=%r filter=%r layout=%r", grid, filter_indices, grid_layout)
            # Defer until the event loop starts so MainWindow has connected grid_mode_changed
            QTimer.singleShot(0, lambda: self._enter_grid(axis_idx, filter_indices, grid_layout))

        if watch:
            if config.remote is None:
                self._start_watching(silent=True)
            elif remote_tmpdir is not None and poll_interval > 0:
                self._start_watching(silent=True)

    # ── internal helpers ──────────────────────────────────────────────────────

    def _key(self) -> tuple:
        return tuple(self.pos)

    def _refresh(self, sidebar_target: tuple[int, int] | None = None):
        self._sidebar_target = sidebar_target
        if self._grid_axis is not None:
            self._refresh_grid()
            return
        pm = self.pixmaps.get(self._key())
        if pm:
            resized = pm.size() != self._item.pixmap().size()
            self._item.setPixmap(pm)
            self._scene.setSceneRect(QRectF(pm.rect()))
            # In fit mode an image of a different size is fitted afresh.  Same
            # size keeps the view as it is, so a pan along the fitted axis
            # survives flipping through congruent plots.
            if resized and self._fit is not None:
                getattr(self, f"fit_{self._fit}")()
        self.viewport().update()
        self.state_changed.emit()

    def _grid_indices(self) -> list[int]:
        """Value indices the grid is showing, in order (before windowing)."""
        if self._grid_axis is None:
            return []
        if self._grid_filter is not None:
            return self._grid_filter
        return list(range(len(self.axis_values[self._grid_axis])))

    def _grid_candidates(self) -> list[int]:
        """Values the focused pane may cycle through.

        Everything the grid was given, minus what the *other* panes are already
        showing — stepping the axis walks the values that aren't on screen yet
        instead of duplicating a neighbour.
        """
        others = {vi for i, vi in enumerate(self._grid_slots) if i != self._grid_focus}
        return [vi for vi in self._grid_indices() if vi not in others]

    def _grid_sync_focus(self):
        """Push ``pos`` on the grid axis into the focused pane.

        Anything that moves the axis — stepping, the value picker, Home/End —
        lands here, so the pane the user is working in is the only one that
        changes.  Panes stay distinct: picking a value another pane already
        holds exchanges the two rather than showing it twice.
        """
        if not self._grid_slots:
            return
        cur = self.pos[self._grid_axis]
        f = self._grid_focus
        if self._grid_slots[f] == cur:
            return
        if cur in self._grid_slots:
            self._grid_slots[self._grid_slots.index(cur)] = self._grid_slots[f]
        self._grid_slots[f] = cur

    def _grid_set_focus(self, slot: int):
        """Make *slot* the pane the grid axis acts on (click-to-focus)."""
        if self._grid_widget is None or not (0 <= slot < len(self._grid_slots)):
            return
        if slot == self._grid_focus:
            return
        self._grid_focus = slot
        # Follow the pane with the axis position so the status bar, the info
        # sidebar and the next step all speak about the pane in hand.
        self.pos[self._grid_axis] = self._grid_slots[slot]
        log.debug("grid: focus -> pane %d (%s)", slot,
                  self.axis_values[self._grid_axis][self._grid_slots[slot]])
        self._refresh()

    def _refresh_grid(self):
        if self._grid_widget is None:
            log.debug("_refresh_grid: no grid widget, skipping")
            return
        self._grid_sync_focus()
        labels = [self.axis_values[self._grid_axis][vi] for vi in self._grid_slots]
        log.debug("_refresh_grid: axis=%d slots=%s focus=%d pos=%s",
                  self._grid_axis, self._grid_slots, self._grid_focus, self.pos)
        self._grid_widget.update_cells(
            self.pos, self._grid_axis, self._grid_slots, labels,
            self.pixmaps, self._grid_focus)
        self.state_changed.emit()

    def _enter_grid(self, axis_idx: int, filter_indices: list[int] | None, layout: tuple[int, int] | None):
        import math
        self._grid_axis = axis_idx
        self._grid_filter = filter_indices
        self._grid_layout = layout

        all_vals = self.axis_values[axis_idx]
        indices = filter_indices if filter_indices is not None else list(range(len(all_vals)))
        n = len(indices)
        log.debug("_enter_grid: axis=%r n=%d filter=%r layout=%r", self.axis_names[axis_idx], n, filter_indices, layout)

        # Sample image dimensions for aspect-ratio-aware auto layout
        img_w, img_h = 800, 600
        for vi in indices:
            key = list(self.pos)
            key[axis_idx] = vi
            pm = self.pixmaps.get(tuple(key))
            if pm and not pm.isNull():
                img_w, img_h = pm.width(), pm.height()
                break
        log.debug("_enter_grid: image size %dx%d", img_w, img_h)

        if layout is not None:
            rows, cols = layout
        else:
            vp = self.viewport()
            vp_ar = vp.width() / max(1, vp.height())
            cols, rows = min(
                ((c, math.ceil(n / c)) for c in range(1, n + 1)),
                key=lambda cr: abs((cr[0] * img_w) / max(1, cr[1] * img_h) - vp_ar),
            )
        capacity = rows * cols
        log.info("grid: axis=%r  %d value%s  layout=%dx%d (%d cell%s)  sharex=%s  sharey=%s",
                 self.axis_names[axis_idx], n, "" if n == 1 else "s", rows, cols,
                 min(n, capacity), "" if min(n, capacity) == 1 else "s",
                 self._grid_sharex, self._grid_sharey)
        if n > capacity:
            log.info("grid: %d values do not fit %d cells — the focused pane cycles "
                     "the %d that are off screen", n, capacity, n - capacity)

        # Each pane holds its own value; the axis only ever moves the focused one.
        self._grid_slots = list(indices[:capacity])
        if self.pos[axis_idx] in self._grid_slots:
            self._grid_focus = self._grid_slots.index(self.pos[axis_idx])
        else:
            self._grid_focus = 0
            self.pos[axis_idx] = self._grid_slots[0]

        labels = [all_vals[vi] for vi in self._grid_slots]
        gw = GridWidget(self, rows, cols, labels, self._grid_sharex, self._grid_sharey)
        gw.focus_requested.connect(self._grid_set_focus)
        self._grid_widget = gw
        self._refresh_grid()
        self.grid_mode_changed.emit(gw)

    def _open_grid_dialog(self):
        """Point-and-click front end for :grid (Ctrl+Shift+G)."""
        from .dialogs import GridDialog, auto_layout

        img_w, img_h = 800, 600
        pm = self.pixmaps.get(tuple(self.pos))
        if pm and not pm.isNull():
            img_w, img_h = pm.width(), pm.height()
        vp = self.viewport()
        vp_w, vp_h = vp.width(), vp.height()

        dlg = GridDialog(
            list(self.axis_names),
            [list(v) for v in self.axis_values],
            axis=self._grid_axis if self._grid_axis is not None else self._h_axis(),
            values=self._grid_filter,
            layout=self._grid_layout,
            sharex=self._grid_sharex,
            sharey=self._grid_sharey,
            in_grid=self._grid_axis is not None,
            auto_layout_for=lambda n: auto_layout(n, img_w, img_h, vp_w, vp_h),
            parent=self.window(),
        )
        result = dlg.exec()
        self.setFocus()
        if result == GridDialog.EXIT:
            if self._grid_axis is not None:
                self._exit_grid()
            return
        if result != QDialog.DialogCode.Accepted or dlg.spec is None:
            return
        spec = dlg.spec
        self._grid_sharex = spec.sharex
        self._grid_sharey = spec.sharey
        self._enter_grid(spec.axis, spec.values, spec.layout)
        self.state_changed.emit()

    def _exit_grid(self):
        log.info("grid: exiting grid mode (was axis=%r)", self.axis_names[self._grid_axis] if self._grid_axis is not None else None)
        self._grid_axis = None
        self._grid_filter = None
        self._grid_layout = None
        self._grid_slots = []
        self._grid_focus = 0
        self._grid_widget = None
        self.grid_mode_changed.emit(None)
        self._refresh()

    def _h_axis(self) -> int:
        return self._locked_h if self._locked_h is not None else self.focus_stack[0]

    def _v_axis(self) -> int | None:
        if self._locked_v is not None:
            return self._locked_v
        return self.focus_stack[1] if len(self.focus_stack) >= 2 else None

    def _is_missing(self, pos) -> bool:
        return isinstance(self.pixmaps.get(tuple(pos)), MissingPixmap)

    def _missing_along(self, axis: int) -> list[bool]:
        """Per value of *axis*: is the combination (others held fixed) missing?"""
        probe = list(self.pos)
        flags = []
        for j in range(len(self.axis_values[axis])):
            probe[axis] = j
            flags.append(self._is_missing(probe))
        return flags

    def _step(self, axis: int, delta: int) -> int:
        """Index reached by stepping *delta* on *axis*, wrapping around.

        With skip_missing on, combinations without a file are passed over;
        if every other value on the axis is missing the position stays put.
        """
        n = len(self.axis_values[axis])
        cur = self.pos[axis]
        if not self._skip_missing:
            return (cur + delta) % n
        probe = list(self.pos)
        for i in range(1, n + 1):
            probe[axis] = (cur + delta * i) % n
            if not self._is_missing(probe):
                return probe[axis]
        return cur

    def _navigate(self, axis: int, delta: int, ensure_visible: bool = False):
        n = len(self.axis_values[axis])
        self.prev = list(self.pos)
        if axis == self._grid_axis and self._grid_widget is not None:
            # The grid axis scrolls inside the focused pane only, through the
            # values no other pane is showing.
            cands = self._grid_candidates()
            cur = self.pos[axis]
            k = cands.index(cur) if cur in cands else 0
            self.pos[axis] = cands[(k + delta) % len(cands)] if cands else cur
        else:
            self.pos[axis] = self._step(axis, delta)
        self._active_axis = axis
        self._active_axis_timer.start(1000)
        self._refresh(sidebar_target=(axis, self.pos[axis]) if ensure_visible else None)

    def _focus(self, axis: int):
        if axis in self.focus_stack:
            self.focus_stack.remove(axis)
        self.focus_stack.insert(0, axis)

    def goto_value(self, axis: int, val_idx: int):
        """Jump to *val_idx* on *axis* and focus it (info-sidebar clicks)."""
        if not 0 <= axis < len(self.axis_values):
            return
        if not 0 <= val_idx < len(self.axis_values[axis]):
            return
        self._focus(axis)
        self._active_axis = axis
        self._active_axis_timer.start(1000)
        if self.pos[axis] != val_idx:
            self.prev = list(self.pos)
            self.pos[axis] = val_idx
        self._refresh()

    # ── incremental-search (value picker / multi-select) ─────────────────────

    def _match(self, query: str, pool) -> list[str]:
        """Filter *pool* by *query*, best match first.

        Fuzzy (subsequence) unless seek.fuzzy is off, in which case the older
        case-insensitive prefix match applies.
        """
        return (fuzzy_filter if self._seek_fuzzy else prefix_filter)(query, pool)

    def _sel_candidates(self) -> list[str]:
        """Items matching the current query, best match first."""
        assert self._sel is not None
        q = self._sel["query"]
        pool = (self.axis_names if self._sel["phase"] == "axis"
                else self.axis_values[self._sel["axis_idx"]])
        return self._match(q, pool)

    def _sel_open_axis(self, initial: str = ""):
        """Enter axis-selection phase (mode 1 entry point)."""
        self._sel = {"phase": "axis", "query": initial.lower(), "cursor": 0}
        self._sel_try_commit()

    def _sel_open_value(self, axis_idx: int, initial: str = ""):
        """Enter value-selection phase for a known axis (ctrl+letter entry point)."""
        self._focus(axis_idx)
        self._sel = {"phase": "value", "query": initial.lower(), "axis_idx": axis_idx, "cursor": 0}
        self._sel_try_commit()

    def _sel_try_commit(self):
        """Auto-commit when exactly one candidate remains (greedy match).

        Controlled by seek.greedy in ~/.juxt/settings.yaml.
        """
        candidates = self._sel_candidates()
        if len(candidates) == 1 and self._seek_greedy:
            self._sel_commit(candidates[0])
        else:
            # Clamp cursor in case the candidate list shrank
            n = len(candidates)
            self._sel["cursor"] = min(self._sel["cursor"], n - 1) if n else 0
            self.state_changed.emit()

    def _sel_commit(self, value: str):
        if self._sel["phase"] == "axis":
            axis_idx = self.axis_names.index(value)
            self._focus(axis_idx)
            self._sel = {"phase": "value", "query": "", "axis_idx": axis_idx, "cursor": 0}
            self._sel_try_commit()
        else:
            axis_idx = self._sel["axis_idx"]
            self.prev = list(self.pos)
            self.pos[axis_idx] = self.axis_values[axis_idx].index(value)
            self._sel = None
            self._refresh()

    def _sel_handle_key(self, event: QKeyEvent) -> bool:
        """Handle input while a selection is active. Returns True if consumed."""
        k = event.key()

        if k == Qt.Key_Escape:
            self._sel = None
            self.state_changed.emit()
            return True

        if k == Qt.Key_Backspace:
            q = self._sel["query"]
            if q:
                self._sel["query"] = q[:-1]
                self._sel["cursor"] = 0
                self.state_changed.emit()
            elif self._sel["phase"] == "value":
                # backspace on empty value query → back to axis search
                self._sel = {"phase": "axis", "query": "", "cursor": 0}
                self.state_changed.emit()
            else:
                self._sel = None
                self.state_changed.emit()
            return True

        if k == Qt.Key_Right:
            candidates = self._sel_candidates()
            if candidates:
                self._sel["cursor"] = (self._sel["cursor"] + 1) % len(candidates)
                self.state_changed.emit()
            return True

        if k == Qt.Key_Left:
            candidates = self._sel_candidates()
            if candidates:
                self._sel["cursor"] = (self._sel["cursor"] - 1) % len(candidates)
                self.state_changed.emit()
            return True

        if Qt.Key_1 <= k <= Qt.Key_9:
            idx = k - Qt.Key_1
            candidates = self._sel_candidates()
            if idx < len(candidates):
                self._sel_commit(candidates[idx])
            return True

        if k in (Qt.Key_Return, Qt.Key_Enter):
            candidates = self._sel_candidates()
            if candidates:
                self._sel_commit(candidates[self._sel["cursor"]])
            return True

        ch = event.text()
        if ch and ch.isprintable():
            self._sel["query"] += ch.lower()
            self._sel["cursor"] = 0
            self._sel_try_commit()
            return True

        return False

    # ── command mode ─────────────────────────────────────────────────────────

    def _cmd_candidates(self) -> list[str]:
        assert self._cmd is not None
        q = self._cmd["query"].strip()
        if self._cmd["phase"] == "verb":
            if q in _ALIASES:
                return [_ALIASES[q]]
            pool = _COMMANDS
        else:
            verb = self._cmd["verb"]
            if "axis" in self._cmd:     # editing the values of one axis
                return []
            if verb in ("axis-h", "axis-v", "remove-axis", "subset", "values"):
                pool = list(self.axis_names)
            elif verb == "grid":
                return self._grid_cmd_candidates(self._cmd["query"])
            else:
                pool = _CMD_ARGS.get(verb, [])
        return self._match(q, pool)

    def _grid_cmd_candidates(self, query: str) -> list[str]:
        """Multi-token candidate list for the :grid arg phase (axis [val …] [NxM])."""
        if " " not in query:
            return self._match(query, self.axis_names)
        tokens = query.split(" ")
        axis_name = tokens[0]
        axis_idx = next((i for i, n in enumerate(self.axis_names) if n.lower() == axis_name.lower()), None)
        if axis_idx is None:
            return []
        all_vals = [str(v) for v in self.axis_values[axis_idx]]
        partial = "" if query.endswith(" ") else tokens[-1]
        used = set(t.lower() for t in (tokens[1:-1] if not query.endswith(" ") else tokens[1:]) if t)
        remaining = [v for v in all_vals if v.lower() not in used]
        return self._match(partial, remaining)

    def _cmd_handle_key(self, event: QKeyEvent) -> bool:
        """Handle input while command mode is active. Returns True if consumed."""
        k = event.key()
        is_free = _is_free_text(self._cmd)
        ctrl = bool(event.modifiers() & Qt.ControlModifier)

        if k == Qt.Key_Escape:
            self._cmd = None
            self._tab_matches = []
            self.state_changed.emit()
            return True

        # Ctrl+Backspace / Ctrl+Delete delete a word, Ctrl+←/→ jump one.
        if ctrl and k in (Qt.Key_Backspace, Qt.Key_Delete, Qt.Key_Left, Qt.Key_Right):
            q = self._cmd["query"]
            if not is_free:
                if k == Qt.Key_Backspace and q:
                    self._cmd["query"] = q[:_word_left(q, len(q))]
                    self._cmd["cursor"] = 0
                    self.state_changed.emit()
                return True
            caret = self._cmd.get("caret", len(q))
            if k == Qt.Key_Backspace:
                left = _word_left(q, caret)
                self._cmd["query"], self._cmd["caret"] = q[:left] + q[caret:], left
            elif k == Qt.Key_Delete:
                self._cmd["query"] = q[:caret] + q[_word_right(q, caret):]
            elif k == Qt.Key_Left:
                self._cmd["caret"] = _word_left(q, caret)
            else:
                self._cmd["caret"] = _word_right(q, caret)
            self._tab_matches = []
            self.state_changed.emit()
            return True

        if k == Qt.Key_Backspace:
            q = self._cmd["query"]
            if is_free:
                caret = self._cmd.get("caret", len(q))
                if caret > 0:
                    self._cmd["query"] = q[:caret - 1] + q[caret:]
                    self._cmd["caret"] = caret - 1
                    self._tab_matches = []
                    self.state_changed.emit()
            elif q:
                self._cmd["query"] = q[:-1]
                self._cmd["cursor"] = -1 if (not self._cmd["query"] and self._cmd["phase"] == "verb") else 0
                self.state_changed.emit()
            elif self._cmd["phase"] == "arg":
                verb = self._cmd["verb"]
                self._cmd = {"phase": "verb", "query": verb, "cursor": 0}
                candidates = self._cmd_candidates()
                if verb in candidates:
                    self._cmd["cursor"] = candidates.index(verb)
                self.state_changed.emit()
            else:
                self._cmd = None
                self.state_changed.emit()
            return True

        if k == Qt.Key_Delete:
            if is_free:
                q = self._cmd["query"]
                caret = self._cmd.get("caret", len(q))
                if caret < len(q):
                    self._cmd["query"] = q[:caret] + q[caret + 1:]
                    self._tab_matches = []
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Tab:
            log.debug("_cmd_handle_key: Tab  is_free=%s cmd=%s", is_free, self._cmd)
            if "axis" in self._cmd:
                self._complete_subset()
            elif is_free and self._cmd.get("verb") in _AXIS_FIRST_ARGS:
                self._complete_axis_arg()
            elif is_free:
                self._complete_path()
            elif self._cmd.get("phase") == "arg" and self._cmd.get("verb") == "grid":
                q = self._cmd["query"]
                candidates = self._cmd_candidates()
                cur = min(self._cmd["cursor"], len(candidates) - 1) if candidates else -1
                log.debug("  grid tab: q=%r candidates=%s cur=%d", q, candidates, cur)
                if cur >= 0:
                    if " " not in q:
                        self._cmd["query"] = candidates[cur] + " "
                    elif q.endswith(" "):
                        self._cmd["query"] = q + candidates[cur] + " "
                    else:
                        prefix = q.rsplit(" ", 1)[0]
                        self._cmd["query"] = prefix + " " + candidates[cur] + " "
                    self._cmd["cursor"] = 0
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Right:
            if is_free:
                q = self._cmd["query"]
                caret = self._cmd.get("caret", len(q))
                self._cmd["caret"] = min(caret + 1, len(q))
                self.state_changed.emit()
            else:
                candidates = self._cmd_candidates()
                if candidates:
                    cur = self._cmd["cursor"]
                    self._cmd["cursor"] = 0 if cur < 0 else (cur + 1) % len(candidates)
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Left:
            if is_free:
                caret = self._cmd.get("caret", len(self._cmd["query"]))
                self._cmd["caret"] = max(caret - 1, 0)
                self.state_changed.emit()
            else:
                candidates = self._cmd_candidates()
                if candidates:
                    cur = self._cmd["cursor"]
                    self._cmd["cursor"] = len(candidates) - 1 if cur < 0 else (cur - 1) % len(candidates)
                    self.state_changed.emit()
            return True

        if k == Qt.Key_Home:
            if is_free:
                self._cmd["caret"] = 0
                self.state_changed.emit()
            return True

        if k == Qt.Key_End:
            if is_free:
                self._cmd["caret"] = len(self._cmd["query"])
                self.state_changed.emit()
            return True

        if k in (Qt.Key_Return, Qt.Key_Enter):
            if self._cmd["phase"] == "verb" and self._cmd["cursor"] < 0:
                self._cmd = None
                self.state_changed.emit()
                return True
            if "axis" in self._cmd:
                axis_idx = self.axis_names.index(self._cmd["axis"])
                text = self._cmd["query"]
                self._cmd = None
                self._tab_matches = []
                self._do_subset(axis_idx, parse_values(text))
                self.state_changed.emit()
                return True
            candidates = self._cmd_candidates()
            if self._cmd["phase"] == "verb":
                if not candidates:
                    self._cmd = None
                    self.state_changed.emit()
                    return True
                verb = candidates[min(self._cmd["cursor"], len(candidates) - 1)]
                if verb in _CMD_ARGS:
                    self._cmd_open_args(verb)
                else:
                    self._cmd = None
                    self._cmd_execute(verb)
                    self.state_changed.emit()
            else:
                verb = self._cmd["verb"]
                q = self._cmd["query"]
                cur = min(self._cmd["cursor"], len(candidates) - 1) if candidates else -1
                if verb == "grid" and " " in q:
                    # multi-token: preserve already-typed tokens, complete last partial
                    if q.endswith(" "):
                        prefix = q.rstrip()
                        selected = candidates[cur] if cur >= 0 else ""
                        arg = f"{prefix} {selected}".strip() if selected else prefix
                    else:
                        prefix, partial = q.rsplit(" ", 1)
                        selected = candidates[cur] if cur >= 0 else partial
                        arg = f"{prefix} {selected}".strip()
                else:
                    arg = candidates[cur] if cur >= 0 else q
                self._cmd = None
                self._cmd_execute(f"{verb} {arg}" if arg else verb)
                self.state_changed.emit()
            return True

        ch = event.text()
        if ch == " " and self._cmd["phase"] == "verb":
            # A command name never holds a space, so it ends the name: take the
            # best match and move on to its argument, as Enter does.  Commands
            # without arguments ignore it (`:fit ` stays a prompt for `fit`).
            candidates = self._cmd_candidates()
            if candidates:
                verb = candidates[max(0, min(self._cmd["cursor"], len(candidates) - 1))]
                if verb in _CMD_ARGS:
                    self._cmd_open_args(verb)
            return True
        if ch and ch.isprintable():
            if is_free:
                q = self._cmd["query"]
                caret = self._cmd.get("caret", len(q))
                self._cmd["query"] = q[:caret] + ch + q[caret:]
                self._cmd["caret"] = caret + 1
                self._tab_matches = []
            else:
                self._cmd["query"] += ch.lower()
            self._cmd["cursor"] = 0
            self.state_changed.emit()
            return True

        return False

    def _cmd_open_args(self, verb: str):
        """Switch the command prompt from naming *verb* to typing its argument."""
        initial = self._pattern_str() if verb == "pattern" else ""
        self._cmd = {"phase": "arg", "verb": verb, "query": initial,
                     "cursor": 0, "caret": len(initial)}
        self.state_changed.emit()

    def apply_theme(self) -> None:
        """Repaint the canvas, and every grid cell, for the active theme."""
        self.setBackgroundBrush(QColor(_theme.current().canvas))
        if self._grid_widget is not None:
            self._grid_widget.apply_theme()
        self.viewport().update()

    def copy_path(self) -> None:
        """Copy the current image's file path to the clipboard."""
        path = self._key_to_path(self._key())
        QApplication.clipboard().setText(path)
        self._flash(f"copied: {path}")

    def cli_command(self) -> str:
        """A `juxt` command line that reopens the view as it is now.

        Every axis is pinned to its current values (`--NAME VALUE ...`), so a
        subset picked interactively, or edited with `:subset` and `:values`,
        comes back without a scan or a dialogue.  The locked arrow axes and
        the grid view are carried over.  What the command line cannot say is
        left out: the cursor position, custom key letters, the session name,
        and an SSH port or key file.
        """
        from .detect import literal_pin

        cfg = self.config
        path = cfg.template
        if cfg.remote is not None:
            user = f"{cfg.remote.user}@" if cfg.remote.user else ""
            path = f"{user}{cfg.remote.host}:{path}"
        parts = ["juxt", _shell_quote(path)]
        for name, vals in zip(self.axis_names, self.axis_values):
            # A value such as `x*y` must not come back as a pattern.
            # A name that is a juxt option (`{grid}`) needs `--pin grid`.
            parts += [*(_shell_quote(w) for w in pin_flag(name)),
                      *(_shell_quote(literal_pin(v)) for v in vals)]
        if self._locked_h is not None:
            parts += ["--axis-h", _shell_quote(self.axis_names[self._locked_h])]
        if self._locked_v is not None:
            parts += ["--axis-v", _shell_quote(self.axis_names[self._locked_v])]
        if self._grid_axis is not None:
            vals = self.axis_values[self._grid_axis]
            parts += ["--grid", _shell_quote(self.axis_names[self._grid_axis])]
            if self._grid_filter is not None:
                parts += ["--grid-values", *(_shell_quote(vals[i]) for i in self._grid_filter)]
            if self._grid_layout is not None:
                parts += ["--grid-layout", "{}x{}".format(*self._grid_layout)]
            if not self._grid_sharex:
                parts.append("--no-sharex")
            if not self._grid_sharey:
                parts.append("--no-sharey")
        return " ".join(parts)

    def copy_cli_command(self) -> None:
        """Copy :meth:`cli_command` to the clipboard."""
        cmd = self.cli_command()
        QApplication.clipboard().setText(cmd)
        log.info("copied CLI command: %s", cmd)
        self._flash("copied: juxt command line")

    def reveal_path(self) -> None:
        """Show the current image's file in the system file manager."""
        if self.config.remote is not None:
            self._flash("remote file: nothing local to reveal")
            return
        path = self._key_to_path(self._key())
        if _reveal_in_file_manager(path):
            self._flash(f"revealed: {path}")
        else:
            self._flash(f"could not reveal: {path}")

    def _cmd_execute(self, cmd: str):
        parts = cmd.strip().split()
        if not parts:
            return
        verb = _ALIASES.get(parts[0].lower(), parts[0].lower())
        args = [a.lower() for a in parts[1:]]

        if verb == "quit":
            QApplication.instance().quit()
        elif verb == "copy-path":
            self.copy_path()
        elif verb == "reveal":
            self.reveal_path()
        elif verb == "copy-cli-command":
            self.copy_cli_command()
        elif verb == "copy-image":
            pm = self.pixmaps.get(self._key())
            if pm and not pm.isNull():
                QApplication.clipboard().setPixmap(pm)
                self._flash("image copied to clipboard")
            else:
                self._flash("no image to copy")
        elif verb == "fit":
            sub = args[0] if args else ""
            if sub in ("height", "h"):
                self.fit_height()
            elif sub in ("width", "w"):
                self.fit_width()
            else:
                self.fit_image()
        elif verb == "fit-height":
            self.fit_height()
        elif verb == "fit-width":
            self.fit_width()
        elif verb == "fit-off":
            self.fit_off()
            self._flash("fit off")
        elif verb == "zoom":
            if args:
                try:
                    self.set_zoom(float(args[0]))
                except ValueError:
                    pass
        elif verb == "fullscreen":
            self._toggle_fullscreen()
        elif verb == "mode":
            if args:
                arg = args[0]
                if arg in ("0", "tap"):
                    self.nav_mode = NavMode.TAP
                elif arg in ("1", "seek"):
                    self.nav_mode = NavMode.SEEK
                elif arg in ("2", "pin"):
                    self.nav_mode = NavMode.PIN
                self._rebuild_key_to_axis()
                self._sel = None
                self.state_changed.emit()
        elif verb == "reload":
            self._do_reload()
        elif verb == "settings":
            from .settings import ensure_settings_file, SETTINGS_PATH
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            path = ensure_settings_file(SETTINGS_PATH)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif verb == "theme":
            arg = args[0] if args else ""
            if arg and arg not in _theme.THEME_SETTINGS:
                self._flash(f"unknown theme: {arg!r}  (auto / dark / light)")
            else:
                self.theme_requested.emit(arg)
        elif verb == "version":
            self._flash(f"juxt {__version__}", 5000)
        elif verb == "watch":
            arg = args[0] if args else ""
            if arg == "false":
                self._stop_watching()
            elif self.config.remote is not None:
                if arg and arg != "true":
                    try:
                        self._poll_interval = max(1, int(arg))
                    except ValueError:
                        self._flash(f"invalid interval: {arg!r}")
                        return
                if self._watching:
                    self._poll_timer.stop()
                self._start_watching()
            else:
                self._start_watching()
        elif verb == "write":
            # Reconstruct path from original parts to preserve case
            path_str = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if path_str:
                self._do_save(path_str)
            else:
                from PySide6.QtWidgets import QFileDialog
                path_str, _ = QFileDialog.getSaveFileName(
                    self, "Save config", "juxt.yaml", "YAML files (*.yaml *.yml)"
                )
                if path_str:
                    self._do_save(path_str)
        elif verb == "pattern":
            raw_arg = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            raw_arg, subset = _split_pattern_flags(raw_arg)
            if raw_arg:
                self._do_pattern(raw_arg, subset)
            else:
                self._flash("usage: pattern <path-or-template>")
        elif verb == "axis-h":
            if args:
                name = args[0]
                if name in self.axis_names:
                    self._locked_h = self.axis_names.index(name)
                    self.state_changed.emit()
                else:
                    self._flash(f"unknown axis: {name!r}")
        elif verb == "axis-v":
            if args:
                name = args[0]
                if name in self.axis_names:
                    self._locked_v = self.axis_names.index(name)
                    self.state_changed.emit()
                else:
                    self._flash(f"unknown axis: {name!r}")
        elif verb == "axis-auto":
            self._locked_h = None
            self._locked_v = None
            self.state_changed.emit()
        elif verb == "swap-axes":
            h, v = self._h_axis(), self._v_axis()
            if v is not None:
                if self._locked_h is not None or self._locked_v is not None:
                    self._locked_h, self._locked_v = v, h
                else:
                    self.focus_stack[0], self.focus_stack[1] = v, h
                self.state_changed.emit()
        elif verb == "switch-last":
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
                self._refresh()
        elif verb == "grid":
            raw_args = parts[1:] if len(parts) > 1 else []

            # Optional first token: axis name (case-insensitive)
            axis_idx = self._h_axis()
            if raw_args and raw_args[0].lower() in [n.lower() for n in self.axis_names]:
                axis_idx = next(i for i, n in enumerate(self.axis_names) if n.lower() == raw_args[0].lower())
                raw_args = raw_args[1:]

            # Optional last token: NxM layout override
            layout: tuple[int, int] | None = None
            if raw_args and _re.match(r'^\d+x\d+$', raw_args[-1], _re.IGNORECASE):
                nr, nc = map(int, raw_args[-1].lower().split('x'))
                if nr < 1 or nc < 1:
                    self._flash("layout rows and cols must be ≥ 1")
                    return
                layout = (nr, nc)
                raw_args = raw_args[:-1]

            # Remaining tokens: value subset (original-case match, case-insensitive)
            filter_indices: list[int] | None = None
            if raw_args:
                all_vals = self.axis_values[axis_idx]
                filter_indices = []
                for v in raw_args:
                    match = next((i for i, val in enumerate(all_vals) if val.lower() == v.lower()), None)
                    if match is None:
                        self._flash(f"unknown value: {v!r}")
                        return
                    if match not in filter_indices:
                        filter_indices.append(match)

            self._enter_grid(axis_idx, filter_indices, layout)
        elif verb == "grid-dialog":
            self._open_grid_dialog()
        elif verb in ("ungrid", "grid-off"):
            if self._grid_axis is None:
                self._flash("not in grid mode")
            else:
                self._exit_grid()
                self._flash("grid off")
        elif verb == "grid-layout":
            if not args:
                self._flash("usage: grid-layout NxM  (e.g. grid-layout 2x3)")
                return
            m = _re.match(r'^(\d+)x(\d+)$', args[0], _re.IGNORECASE)
            if not m:
                self._flash(f"invalid layout: {args[0]!r}  (expected NxM)")
                return
            nr, nc = int(m.group(1)), int(m.group(2))
            if nr < 1 or nc < 1:
                self._flash("rows and cols must be ≥ 1")
                return
            if self._grid_axis is None:
                self._flash("not in grid mode")
                return
            self._enter_grid(self._grid_axis, self._grid_filter, (nr, nc))
        elif verb in ("skip-missing", "dim-missing"):
            attr = "_" + verb.replace("-", "_")
            if not args:
                setattr(self, attr, not getattr(self, attr))
            elif args[0] in ("on", "off"):
                setattr(self, attr, args[0] == "on")
            self._flash(f"{verb} {'on' if getattr(self, attr) else 'off'}")
            self.state_changed.emit()
        elif verb == "grid-sharex":
            if not args:
                self._grid_sharex = not self._grid_sharex
            elif args[0] == "on":
                self._grid_sharex = True
            elif args[0] == "off":
                self._grid_sharex = False
            if self._grid_widget is not None:
                self._grid_widget.set_sharex(self._grid_sharex)
            self._flash(f"sharex {'on' if self._grid_sharex else 'off'}")
            self.state_changed.emit()
        elif verb == "grid-sharey":
            if not args:
                self._grid_sharey = not self._grid_sharey
            elif args[0] == "on":
                self._grid_sharey = True
            elif args[0] == "off":
                self._grid_sharey = False
            if self._grid_widget is not None:
                self._grid_widget.set_sharey(self._grid_sharey)
            self._flash(f"sharey {'on' if self._grid_sharey else 'off'}")
            self.state_changed.emit()
        elif verb == "info":
            self.toggle_info.emit()
        elif verb == "keys":
            self.toggle_keys.emit()
        elif verb == "remove-axis":
            name = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            if not name:
                name = self.axis_names[self._h_axis()]
            if name not in self.axis_names:
                self._flash(f"unknown axis: {name!r}")
            elif len(self.axis_names) <= 1:
                self._flash("cannot remove the only axis")
            else:
                self._do_remove_axis(self.axis_names.index(name))
        elif verb == "remove-value":
            raw = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            axis_name = next((n for n in self.axis_names
                              if raw == n or raw.startswith(n + " ")), None)
            if axis_name is None:
                self._flash("usage: remove-value AXIS VALUE")
                return
            val_str = raw[len(axis_name):].strip()
            if not val_str:
                self._flash(f"usage: remove-value {axis_name} VALUE")
                return
            axis_idx = self.axis_names.index(axis_name)
            vals = self.axis_values[axis_idx]
            if val_str not in vals:
                self._flash(f"unknown value: {val_str!r}")
            elif len(vals) <= 1:
                self._flash("cannot remove the only value in an axis")
            else:
                self._do_remove_value(axis_idx, vals.index(val_str))
        elif verb == "subset":
            raw = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            axis_name = next((n for n in self.axis_names
                              if raw == n or raw.startswith(n + " ")), None)
            if axis_name is None:
                self._flash("usage: subset AXIS [VALUES...]")
                return
            rest = raw[len(axis_name):].strip()
            axis_idx = self.axis_names.index(axis_name)
            if rest:
                self._do_subset(axis_idx, parse_values(rest))
            else:
                self._cmd_open_subset(axis_idx)
        elif verb == "subset-interactive":
            self.open_values_dialog()
        elif verb == "values":
            raw = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            axis_name = next((n for n in self.axis_names if n == raw), None) or next(
                (n for n in self.axis_names if n.lower() == raw.lower()), None)
            if axis_name is None:
                self._flash("usage: values AXIS")
                return
            self.open_values_dialog(self.axis_names.index(axis_name))
        elif verb == "change-key":
            raw = " ".join(parts[1:]).strip() if len(parts) > 1 else ""
            tokens = raw.rsplit(None, 1)
            if len(tokens) == 2:
                axis_name, letter = tokens
                if axis_name not in self.axis_names:
                    self._flash(f"unknown axis: {axis_name!r}")
                elif not (len(letter) == 1 and letter.isalpha()):
                    self._flash("letter must be a single a–z character")
                else:
                    self._do_change_key(axis_name, letter.lower())
            elif len(tokens) == 1:
                axis_name = tokens[0]
                if axis_name not in self.axis_names:
                    self._flash(f"unknown axis: {axis_name!r}")
                else:
                    self._do_change_key(axis_name, None)
            else:
                self._flash("usage: change-key AXIS LETTER")

    # ── subset editing ───────────────────────────────────────────────────────

    def _cmd_open_subset(self, axis_idx: int):
        """Open the value editor of an axis, prefilled with its current values."""
        initial = format_values(self.axis_values[axis_idx])
        self._cmd = {"phase": "arg", "verb": "subset", "axis": self.axis_names[axis_idx],
                     "query": initial, "cursor": 0, "caret": len(initial)}
        self._tab_matches = []
        self.state_changed.emit()

    def _subset_available(self) -> list[str]:
        """Values of the editing axis that exist on disk (found once per edit)."""
        cmd = self._cmd
        if "avail" not in cmd:
            avail: list[str] = []
            if self.config.remote is None:
                try:
                    from .detect import _axes_from_local_template
                    _, found = _axes_from_local_template(self.config.template)
                    avail = list(found.get(cmd["axis"], []))
                except Exception:
                    log.debug("subset: listing values failed", exc_info=True)
            cmd["avail"] = avail
        return cmd["avail"]

    def _complete_subset(self):
        """Tab-complete the value under the caret against the values on disk."""
        query = self._cmd["query"]
        caret = self._cmd.get("caret", len(query))
        start = max(query.rfind(" ", 0, caret) + 1, 0)
        partial = query[start:caret].lstrip('"')
        others = set(parse_values(query[:start] + query[caret:]))
        matches = [v for v in self._subset_available()
                   if v.lower().startswith(partial.lower()) and v not in others]
        if not matches:
            self._tab_matches = []
            return
        if len(matches) == 1:
            word = format_values(matches)
            tail = "" if query[caret:].startswith(" ") else " "
            self._tab_matches = []
        else:
            word = format_values([_common_prefix(matches)])
            tail = ""
            self._tab_matches = matches
        # Replace the whole token the caret sits in
        end = query.find(" ", caret)
        end = len(query) if end < 0 else end
        self._cmd["query"] = query[:start] + word + tail + query[end:]
        self._cmd["caret"] = start + len(word) + len(tail)
        self.state_changed.emit()

    # ── key assignment ───────────────────────────────────────────────────────

    def _rebuild_key_to_axis(self):
        """Recompute letter→axis-index, filling any gaps with _auto_keys."""
        from .config import _auto_keys
        axes_dict = {name: self.axis_values[i] for i, name in enumerate(self.axis_names)}

        # Start from auto-assigned keys so every axis gets a letter if possible
        merged: dict[str, str] = _auto_keys(axes_dict)

        # User-configured keys override auto assignments for their specific letters
        for ch, name in self.config.keys.items():
            if name in self.axis_names:
                merged[ch] = name

        # Remove any letter that ended up pointing at an axis that already has
        # a better (user-configured) letter, to avoid duplicate axis entries
        axis_to_ch: dict[str, str] = {}
        user_keys = {ch for ch in self.config.keys if self.config.keys[ch] in self.axis_names}
        for ch, name in list(merged.items()):
            if ch in user_keys:
                axis_to_ch[name] = ch          # user binding wins unconditionally
            elif name not in axis_to_ch:
                axis_to_ch[name] = ch          # first auto assignment wins

        self.key_to_axis = {
            ch: self.axis_names.index(name)
            for name, ch in axis_to_ch.items()
        }

    # ── flash message ────────────────────────────────────────────────────────

    def _clear_flash(self):
        self._flash_msg = None
        self.state_changed.emit()

    def _clear_active_axis(self):
        self._active_axis = None
        self.state_changed.emit()

    def apply_settings(self, settings) -> None:
        """Apply a freshly loaded Settings object without restarting."""
        self._seek_greedy = settings.seek_greedy
        self._seek_fuzzy = settings.seek_fuzzy
        self._swap_modifiers = settings.swap_modifiers
        self._skip_missing = settings.skip_missing
        self._dim_missing = settings.dim_missing
        self._sidebar_copy = settings.sidebar_copy
        self._pattern_subset = settings.pattern_subset
        from .loader import set_remote_cache_days, set_threads
        set_threads(settings.loading_threads)
        set_remote_cache_days(settings.remote_cache_days)
        new_kb: dict[tuple[int, int], str] = {}
        for ks, action in settings.keybindings.items():
            try:
                k, m = _parse_key(ks)
                new_kb[(k, m)] = action
            except ValueError as e:
                log.warning("Invalid keybinding %r: %s", ks, e)
        self._keybindings = new_kb

    def _dispatch_action(self, action: str):
        if action == "toggle-statusbar":
            self.toggle_bar.emit()
        elif action == "toggle-info":
            self.toggle_info.emit()
        elif action == "toggle-keys":
            self.toggle_keys.emit()
        else:
            self._cmd_execute(action)

    def _toggle_fullscreen(self):
        win = self.window()
        if win.isFullScreen():
            if self._pre_fullscreen_maximized:
                win.showMaximized()
            else:
                win.showNormal()
            self._pre_fullscreen_maximized = None
        else:
            self._pre_fullscreen_maximized = win.isMaximized()
            win.showFullScreen()

    def _flash(self, msg: str, ms: int = 2500):
        self._flash_msg = msg
        self._flash_timer.start(ms)
        self.state_changed.emit()

    def _do_save(self, path: str):
        from .config import dump_config
        try:
            dump_config(self.config, path)
            self._flash(f"saved → {path}")
        except Exception as e:
            self._flash(f"save failed: {e}")

    # ── file watcher ─────────────────────────────────────────────────────────

    def _key_to_path(self, key: tuple) -> str:
        path = self.config.template
        for i, name in enumerate(self.axis_names):
            path = path.replace(f"{{{name}}}", self.axis_values[i][key[i]])
        return path

    def _build_path_to_key(self) -> dict[str, tuple]:
        import itertools
        result = {}
        ranges = [range(len(vals)) for vals in self.axis_values]
        for indices in itertools.product(*ranges):
            result[self._key_to_path(indices)] = indices
        return result

    def _start_watching(self, *, silent: bool = False):
        if self.config.remote is None:
            if self._watcher is not None:
                self._watcher.fileChanged.disconnect(self._on_file_changed)
                self._watcher.deleteLater()
            self._path_to_key = self._build_path_to_key()
            self._watcher = QFileSystemWatcher(list(self._path_to_key.keys()), self)
            self._watcher.fileChanged.connect(self._on_file_changed)
            self._watching = True
            n = len(self._path_to_key)
            log.info("Watching %d local file%s", n, "s" if n != 1 else "")
            if not silent:
                self._flash(f"watching {n} file{'s' if n != 1 else ''}")
        else:
            if self._remote_tmpdir is None:
                self._flash("remote polling not available (no cache dir)")
                return
            self._poll_timer.start(self._poll_interval * 1000)
            self._watching = True
            log.info("Polling remote every %ds", self._poll_interval)
            if not silent:
                self._flash(f"polling every {self._poll_interval}s")

    def _stop_watching(self):
        if self.config.remote is None:
            if self._watcher is not None:
                self._watcher.fileChanged.disconnect(self._on_file_changed)
                self._watcher.deleteLater()
                self._watcher = None
            self._path_to_key = {}
        else:
            self._poll_timer.stop()
        self._watching = False
        log.info("File watching stopped")
        self._flash("file watching disabled")

    def _on_file_changed(self, path: str):
        key = self._path_to_key.get(path)
        if key is None:
            return
        pm = QPixmap(path)
        if not pm.isNull():
            self.pixmaps[key] = pm
        # Some OS implementations drop a path from the watcher after deletion;
        # re-add it so saves that write via a temp-rename are still tracked.
        if self._watcher is not None and path not in self._watcher.files():
            self._watcher.addPath(path)
        if tuple(self.pos) == key:
            self._refresh()

    def _start_poll_worker(self):
        if self._poll_in_progress or self._remote_tmpdir is None:
            return
        self._poll_in_progress = True

        conn = self._remote_conn       # [ssh, sftp] — mutable list, worker-owned
        tmpdir = self._remote_tmpdir
        mtimes = self._remote_mtimes   # mutable dict, updated in-place by worker
        get_pw = self._get_password
        config = self.config
        gen = self._axes_gen
        lock = self._remote_lock

        def _worker():
            with lock:
                _poll()

        def _poll():
            # Lazily connect; reconnect whenever the session is dead
            if conn[1] is None:
                try:
                    from .loader import _connect_sftp
                    conn[0], conn[1] = _connect_sftp(config.remote, get_pw)
                except Exception as e:
                    self._poll_result.emit(e)
                    return
            try:
                from .loader import poll_remote_with_sftp
                changed = poll_remote_with_sftp(
                    config.template, config.axes, tmpdir, conn[1], mtimes)
                self._poll_result.emit((gen, changed))
            except Exception as e:
                # Session likely dead — clear so next poll reconnects
                try:
                    conn[1].close()
                    conn[0].close()
                except Exception:
                    pass
                conn[0], conn[1] = None, None
                self._poll_result.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_remote_poll(self, result: object):
        self._poll_in_progress = False
        if isinstance(result, Exception):
            log.warning("Remote poll failed: %s", result)
            self._flash(f"poll failed: {result}")
            return
        # result is (gen, list[(key, local_path|None)]) for changed files only.
        # local_path is None when the remote file was deleted or moved.
        # Decode QPixmaps here on the main thread (Qt requirement).
        gen, changed = result
        if gen != self._axes_gen:
            # The axes changed while the poll ran, so its keys point at other
            # images now; the next poll compares against the new axes.
            return
        if changed:
            log.debug("Remote poll: %d file(s) changed", len(changed))
        from .loader import load_images
        self.pixmaps.update(load_images(
            (key, local_path, local_path or self._key_to_path(key))
            for key, local_path in changed))
        if any(tuple(self.pos) == key for key, _ in changed):
            self._refresh()

    # ── reload ────────────────────────────────────────────────────────────────

    def _do_reload(self):
        """Re-detect axes and download new files, then rebuild viewer state."""
        if self._reload_in_progress or self._listing_in_progress:
            return
        self._reload_in_progress = True
        self.state_changed.emit()
        # Pause polling for the duration of the reload
        if self._watching and self.config.remote is not None:
            self._poll_timer.stop()

        conn = self._remote_conn
        tmpdir = self._remote_tmpdir
        mtimes = self._remote_mtimes
        get_pw = self._get_password
        config = self.config

        def _worker():
            try:
                if config.remote is not None:
                    # Always open a fresh connection for reload so that
                    # directory listings are not stale and we don't race
                    # with a concurrent poll worker on the shared conn.
                    from .loader import _connect_sftp
                    try:
                        if conn[1] is not None:
                            conn[1].close()
                        if conn[0] is not None:
                            conn[0].close()
                    except Exception:
                        pass
                    conn[0], conn[1] = _connect_sftp(config.remote, get_pw)
                    from .detect import _axes_from_sftp_template
                    new_axes = _axes_from_sftp_template(config.template, conn[1])
                    if not new_axes:
                        raise ValueError("no images found for current template")
                    # Download only new/changed files; skip unchanged via mtimes
                    from .loader import poll_remote_with_sftp
                    poll_remote_with_sftp(
                        config.template, new_axes, tmpdir, conn[1], mtimes)
                    # Build key→local-path map for main-thread pixmap decoding
                    from itertools import product as _product
                    from .loader import local_cache_path
                    axis_names = list(new_axes.keys())
                    axis_values = list(new_axes.values())
                    key_to_path = {}
                    for combo in _product(*axis_values):
                        mapping = dict(zip(axis_names, combo))
                        rpath = config.template.format(**mapping)
                        lpath = str(local_cache_path(tmpdir, rpath))
                        key = tuple(vals.index(v) for vals, v in zip(axis_values, combo))
                        key_to_path[key] = lpath
                else:
                    from .detect import _axes_from_local_template
                    _, new_axes = _axes_from_local_template(config.template)
                    if not new_axes:
                        raise ValueError("no images found for current template")
                    from itertools import product as _product
                    axis_names = list(new_axes.keys())
                    axis_values = list(new_axes.values())
                    key_to_path = {}
                    for combo in _product(*axis_values):
                        mapping = dict(zip(axis_names, combo))
                        key_to_path[
                            tuple(vals.index(v) for vals, v in zip(axis_values, combo))
                        ] = config.template.format(**mapping)

                from .config import Config
                new_config = Config(
                    template=config.template,
                    axes=new_axes,
                    keys=config.keys,
                    mode=config.mode,
                    remote=config.remote,
                )
                self._reload_result.emit((new_config, key_to_path))
            except Exception as e:
                if config.remote is not None:
                    conn[0], conn[1] = None, None
                self._reload_result.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_reload(self, result: object):
        self._reload_in_progress = False
        if isinstance(result, Exception):
            log.warning("Reload failed: %s", result)
            self._flash(f"reload failed: {result}")
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        new_config, key_to_path = result

        # Decoded in a pool; the pixmaps are made here, on the main thread.
        from .loader import load_images
        new_pixmaps = load_images((key, path, path) for key, path in key_to_path.items())

        # Preserve position by axis value name where possible
        old_names = self.axis_names
        old_values = self.axis_values
        old_pos = self.pos
        new_axis_names = list(new_config.axes.keys())
        new_axis_values = list(new_config.axes.values())
        new_pos = []
        for name, vals in zip(new_axis_names, new_axis_values):
            if name in old_names:
                old_i = old_names.index(name)
                old_val = old_values[old_i][old_pos[old_i]]
                new_pos.append(vals.index(old_val) if old_val in vals else 0)
            else:
                new_pos.append(0)

        # Remap locked axes by name to new indices (clear if axis disappeared)
        old_locked_h_name = old_names[self._locked_h] if self._locked_h is not None and self._locked_h < len(old_names) else None
        old_locked_v_name = old_names[self._locked_v] if self._locked_v is not None and self._locked_v < len(old_names) else None

        # Exit grid mode before replacing axes (axis indices will shift)
        if self._grid_axis is not None:
            self._exit_grid()

        self.config = new_config
        self.pixmaps = new_pixmaps
        self._axes_gen += 1
        self.axis_names = new_axis_names
        self.axis_values = new_axis_values
        self.n_axes = len(new_axis_names)
        self.pos = new_pos
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._locked_h = new_axis_names.index(old_locked_h_name) if old_locked_h_name in new_axis_names else None
        self._locked_v = new_axis_names.index(old_locked_v_name) if old_locked_v_name in new_axis_names else None
        self._rebuild_key_to_axis()

        # Restart poll timer / rebuild local watcher with updated paths
        if self._watching:
            if self.config.remote is None:
                if self._watcher is not None:
                    self._watcher.fileChanged.disconnect(self._on_file_changed)
                    self._watcher.deleteLater()
                self._path_to_key = self._build_path_to_key()
                self._watcher = QFileSystemWatcher(list(self._path_to_key.keys()), self)
                self._watcher.fileChanged.connect(self._on_file_changed)
            else:
                self._poll_timer.start(self._poll_interval * 1000)

        log.info("Reload complete: %s", self.config.template)
        self._flash("reloaded")
        self._refresh()
        self.config_changed.emit()

    # ── in-place config edits ─────────────────────────────────────────────────

    def _apply_axes_change(self, new_config, new_pixmaps: dict):
        """Apply a modified config+pixmaps in-place, preserving position by value name."""
        old_names = self.axis_names
        new_axis_names = list(new_config.axes.keys())
        new_axis_values = list(new_config.axes.values())
        new_pos = []
        for name, vals in zip(new_axis_names, new_axis_values):
            if name in old_names:
                old_i = old_names.index(name)
                old_val = self.axis_values[old_i][self.pos[old_i]]
                new_pos.append(vals.index(old_val) if old_val in vals else 0)
            else:
                new_pos.append(0)
        old_h_name = old_names[self._locked_h] if self._locked_h is not None and self._locked_h < len(old_names) else None
        old_v_name = old_names[self._locked_v] if self._locked_v is not None and self._locked_v < len(old_names) else None
        if self._grid_axis is not None:
            self._exit_grid()
        self.config = new_config
        self.pixmaps = new_pixmaps
        self._axes_gen += 1
        self.axis_names = new_axis_names
        self.axis_values = new_axis_values
        self.n_axes = len(new_axis_names)
        self.pos = new_pos
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._locked_h = new_axis_names.index(old_h_name) if old_h_name in new_axis_names else None
        self._locked_v = new_axis_names.index(old_v_name) if old_v_name in new_axis_names else None
        self._sel = None
        self._rebuild_key_to_axis()
        if self._watching and self.config.remote is None:
            if self._watcher is not None:
                self._watcher.fileChanged.disconnect(self._on_file_changed)
                self._watcher.deleteLater()
            self._path_to_key = self._build_path_to_key()
            self._watcher = QFileSystemWatcher(list(self._path_to_key.keys()), self)
            self._watcher.fileChanged.connect(self._on_file_changed)
        self._refresh()

    def _do_remove_axis(self, axis_idx: int):
        j = axis_idx
        v = self.pos[j]
        removed_name = self.axis_names[j]
        new_pixmaps = {
            key[:j] + key[j + 1:]: pm
            for key, pm in self.pixmaps.items()
            if key[j] == v
        }
        new_axes = {
            name: vals
            for i, (name, vals) in enumerate(zip(self.axis_names, self.axis_values))
            if i != j
        }
        from .config import Config
        new_config = Config(
            template=self.config.template,
            axes=new_axes,
            keys={k: n for k, n in self.config.keys.items() if n != removed_name},
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._apply_axes_change(new_config, new_pixmaps)
        self._flash(f"removed axis {removed_name!r}")

    def _do_remove_value(self, axis_idx: int, val_idx: int):
        j, v = axis_idx, val_idx
        axis_name = self.axis_names[j]
        removed_val = self.axis_values[j][v]
        new_pixmaps = {}
        for key, pm in self.pixmaps.items():
            if key[j] == v:
                continue
            new_j = key[j] - 1 if key[j] > v else key[j]
            new_pixmaps[key[:j] + (new_j,) + key[j + 1:]] = pm
        new_axes = dict(self.config.axes)
        new_axes[axis_name] = self.axis_values[j][:v] + self.axis_values[j][v + 1:]
        from .config import Config
        new_config = Config(
            template=self.config.template,
            axes=new_axes,
            keys=self.config.keys,
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._apply_axes_change(new_config, new_pixmaps)
        self._flash(f"removed {axis_name}={removed_val}")

    # ── value picker (:subset-interactive, :values, the sidebar's [+]) ───────

    def open_values_dialog(self, axis_idx: int | None = None):
        """Let the user tick the values of one axis (*axis_idx*) or of every
        axis (None), out of those found on disk.

        The dialogue opens at once with the current values and a spinner per
        column; the disk is searched in a worker meanwhile (over the session's
        own SFTP connection when remote, so the password asked at startup is
        reused), one axis after the other, and each listing joins its column
        as it comes in.
        """
        if axis_idx is not None and not 0 <= axis_idx < self.n_axes:
            return
        if self._reload_in_progress or self._listing_in_progress:
            self._flash("busy, try again in a moment")
            return
        names = list(self.axis_names) if axis_idx is None else [self.axis_names[axis_idx]]
        current = {n: list(self.axis_values[self.axis_names.index(n)]) for n in names}
        # Keeps :reload and :pattern from swapping the connection under the worker.
        self._listing_in_progress = True

        config = self.config
        gen = self._axes_gen
        conn, lock, get_pw = self._remote_conn, self._remote_lock, self._get_password

        def _worker():
            from .detect import axis_values_on_disk

            def _list(sftp):
                for name in names:
                    try:
                        found = axis_values_on_disk(config.template, config.axes, name, sftp)
                    except Exception as e:
                        found = e
                    self._values_found.emit((name, found))

            try:
                if config.remote is None:
                    _list(None)
                else:
                    with lock:
                        _list(_shared_sftp(conn, config.remote, get_pw))
            except Exception as e:   # could not connect
                for name in names:
                    self._values_found.emit((name, e))
            finally:
                self._values_found.emit((None, None))

        from .dialogs import ValuesDialog
        dlg = ValuesDialog(current, parent=self.window())
        self._values_dlg = dlg
        threading.Thread(target=_worker, daemon=True).start()
        try:
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            chosen = dlg.values()
        finally:
            self._values_dlg = None
            _dispose(dlg)
        self.setFocus()
        changed = {n: v for n, v in chosen.items() if v != current[n]}
        if not accepted or not changed:
            return
        if gen != self._axes_gen or any(n not in self.axis_names for n in changed):
            self._flash("the axes changed meanwhile; open the value picker again")
            return
        self._set_values({self.axis_names.index(n): v for n, v in changed.items()},
                         snap_case=False)

    def _on_values_found(self, result: object):
        """One axis's listing is in: hand it to the value picker, if open.
        `(None, None)` marks the end of the search."""
        axis, found = result
        if axis is None:
            self._listing_in_progress = False
            return
        dlg = self._values_dlg
        if dlg is None or axis not in dlg.axis_names:
            return   # closed before the listing came in
        if isinstance(found, Exception):
            log.warning("Listing %s values failed: %s", axis, found)
            dlg.set_found(axis, [], error=str(found))
        else:
            dlg.set_found(axis, found)

    # ── subset edit ──────────────────────────────────────────────────────────

    def _do_subset(self, axis_idx: int, wanted: list[str], *, snap_case: bool = True):
        """Make *wanted* the values of one axis, in that order (see _set_values)."""
        self._set_values({axis_idx: wanted}, snap_case=snap_case)

    def _set_values(self, wanted: dict[int, list[str]], *, snap_case: bool = True):
        """Make `wanted[j]` the values of axis *j*, in that order, for every
        axis given, in one go.

        Images whose values all stay are carried along, the combinations that
        involve an added value are loaded from the template (downloaded in a
        worker when remote), and the rest are dropped. With *snap_case*, a
        typed value that differs from an existing one only in case is taken
        to be that value.
        """
        new_axis_values = [list(vals) for vals in self.axis_values]
        for j, w in wanted.items():
            old_vals = self.axis_values[j]
            lowered: dict[str, list[str]] = {}
            for v in old_vals:
                lowered.setdefault(v.lower(), []).append(v)
            new_vals: list[str] = []
            for v in w:
                if snap_case and v not in old_vals and len(lowered.get(v.lower(), [])) == 1:
                    v = lowered[v.lower()][0]
                if v not in new_vals:
                    new_vals.append(v)
            if not new_vals:
                self._flash("an axis needs at least one value")
                return
            new_axis_values[j] = new_vals

        # Per axis: old value index -> new value index, for the values kept.
        remaps = []
        for old_vals, new_vals in zip(self.axis_values, new_axis_values):
            idx = {v: i for i, v in enumerate(new_vals)}
            remaps.append({i: idx[v] for i, v in enumerate(old_vals) if v in idx})
        jobs = self._added_jobs(new_axis_values, remaps)
        if not jobs:
            self._finish_subset(new_axis_values, remaps, {}, {})
        elif self.config.remote is None:
            files = [(key, path, path) for key, path in jobs]
            dlg = self._load_dialog(_images_label("Loading", len(files)), len(files))
            loaded = self._decode_added(new_axis_values, files, dlg)
            _dispose(dlg)
            if loaded is not None:
                self._finish_subset(new_axis_values, remaps, loaded, {})
        else:
            self._download_added_remote(new_axis_values, remaps, jobs)

    def _added_jobs(self, new_axis_values: list[list[str]],
                    remaps: list[dict[int, int]]) -> list[tuple[tuple, str]]:
        """`(key, path)` for every image the edit brings in: each combination
        of *new_axis_values* with at least one value the axes do not have
        yet, keyed in the new index space."""
        from itertools import product
        kept = [set(r.values()) for r in remaps]
        jobs = []
        for key in product(*(range(len(vals)) for vals in new_axis_values)):
            if all(k in kept[j] for j, k in enumerate(key)):
                continue
            mapping = {n: new_axis_values[j][key[j]] for j, n in enumerate(self.axis_names)}
            jobs.append((key, self.config.template.format(**mapping)))
        return jobs

    def _load_dialog(self, label: str, total: int):
        """A progress dialogue like the one on the initial load."""
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(label, "Cancel", 0, total, self.window())
        dlg.setWindowTitle("juxt")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)
        return dlg

    def _decode_added(self, new_axis_values: list[list[str]],
                      files: list[tuple[tuple, str | None, str]],
                      dlg=None, offset: int = 0) -> dict[tuple, QPixmap] | None:
        """Pixmaps of `(key, local_path, shown_path)` *files*, or None (and a
        flash) when some added value has no file at all, which is how a typo
        shows. A missing file (`local_path` None or unreadable) gets the usual
        placeholder naming *shown_path*. Progress goes to the progress
        dialogue *dlg*, counted from *offset*; cancelling it returns None."""
        from .loader import MissingPixmap, load_images
        added = {(j, v) for j, vals in enumerate(new_axis_values)
                 for v in vals if v not in self.axis_values[j]}

        def _progress(done: int, _total: int):
            if dlg is not None:
                dlg.setValue(done)   # window-modal: keeps the UI painting
                if dlg.wasCanceled():
                    raise _Cancelled()

        try:
            loaded = load_images(files, _progress, offset=offset)
        except _Cancelled:
            self._flash("cancelled; the axes are unchanged")
            return None
        found: set[tuple[int, str]] = set()
        for key, pm in loaded.items():
            if not isinstance(pm, MissingPixmap):
                found.update((j, new_axis_values[j][k]) for j, k in enumerate(key))
        for j, vals in enumerate(new_axis_values):
            for v in vals:
                if (j, v) in added and (j, v) not in found:
                    self._flash(f"no files for {self.axis_names[j]}={v}")
                    return None
        return loaded

    def _download_added_remote(self, new_axis_values: list[list[str]],
                               remaps: list[dict[int, int]],
                               jobs: list[tuple[tuple, str]]):
        """Fetch the images *jobs* brings in, in a worker over the session's
        SFTP connection, then finish the edit on the main thread."""
        if self._reload_in_progress:
            self._flash("busy, try again in a moment")
            return
        if self._remote_tmpdir is None:
            self._flash("this remote session has no download cache; use :pattern")
            return
        n = len(jobs)
        self._reload_in_progress = True
        self._busy_label = "downloading new values…"
        if self._watching:
            self._poll_timer.stop()
        self.state_changed.emit()

        # Downloading fills the first half, decoding the second, as on startup.
        dlg = self._load_dialog(_images_label("Downloading", n), 2 * n)
        self._pattern_dlg = dlg
        cancel = threading.Event()
        # A lambda, not cancel.set: PySide holds a plain object's bound method
        # only weakly, so the worker dropping the last reference to the Event
        # would disconnect the slot from that thread and deadlock the GUI.
        dlg.canceled.connect(lambda: cancel.set())

        gen = self._axes_gen
        conn, lock, get_pw = self._remote_conn, self._remote_lock, self._get_password
        remote, tmpdir = self.config.remote, self._remote_tmpdir

        def _worker():
            from .loader import download_remote_files

            def _progress(i, total):
                if cancel.is_set():
                    raise _Cancelled()
                self._pattern_progress.emit(i, 2 * total, "")

            try:
                mtimes: dict = {}
                with lock:
                    sftp = _shared_sftp(conn, remote, get_pw)
                    got = download_remote_files(jobs, tmpdir, sftp, mtimes, _progress)
                files = [(key, local, path)
                         for (key, local), (_k, path) in zip(got, jobs)]
                self._values_loaded.emit({
                    "gen": gen, "values": new_axis_values, "remaps": remaps,
                    "files": files, "mtimes": mtimes,
                })
            except _Cancelled:
                self._values_loaded.emit(None)
            except Exception as e:
                self._values_loaded.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_subset_downloaded(self, result: object):
        self._reload_in_progress = False
        self._busy_label = None
        dlg, self._pattern_dlg = self._pattern_dlg, None
        if self._watching and self.config.remote is not None:
            self._poll_timer.start(self._poll_interval * 1000)
        try:
            if result is None:
                self._flash("cancelled; the axes are unchanged")
            elif isinstance(result, Exception):
                log.warning("Downloading new values failed: %s", result)
                self._flash(f"download failed: {result}")
            elif result["gen"] != self._axes_gen:
                self._flash("the axes changed meanwhile; nothing was added")
            else:
                files = result["files"]
                if dlg is not None:
                    dlg.setLabelText(_images_label("Loading", len(files)))
                loaded = self._decode_added(result["values"], files, dlg, offset=len(files))
                if loaded is not None:
                    self._finish_subset(result["values"], result["remaps"],
                                        loaded, result["mtimes"])
        finally:
            if dlg is not None:
                _dispose(dlg)
            self.state_changed.emit()

    def _finish_subset(self, new_axis_values: list[list[str]],
                       remaps: list[dict[int, int]],
                       loaded: dict[tuple, QPixmap], new_mtimes: dict):
        """Apply a subset edit: re-index the kept images, add the *loaded* ones."""
        def moved(key):
            if all(k in remaps[j] for j, k in enumerate(key)):
                return tuple(remaps[j][k] for j, k in enumerate(key))
            return None

        new_pixmaps: dict[tuple, QPixmap] = {}
        for key, pm in self.pixmaps.items():
            new_key = moved(key)
            if new_key is not None:
                new_pixmaps[new_key] = pm
        new_pixmaps.update(loaded)

        if self.config.remote is not None:
            # A fresh dict rather than an in-place edit: a poll still running
            # on the old axes writes into the old one, and its result is
            # dropped once the axes generation moves on.
            mtimes = {}
            for key, mtime in dict(self._remote_mtimes).items():
                new_key = moved(key)
                if new_key is not None:
                    mtimes[new_key] = mtime
            mtimes.update(new_mtimes)
            self._remote_mtimes = mtimes

        changes = []
        for name, old_vals, new_vals, remap in zip(
                self.axis_names, self.axis_values, new_axis_values, remaps):
            if new_vals != old_vals:
                changes.append((name, len(new_vals), len(new_vals) - len(remap),
                                len(old_vals) - len(remap)))

        from .config import Config
        new_config = Config(
            template=self.config.template,
            axes=dict(zip(self.axis_names, new_axis_values)),
            keys=self.config.keys,
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._apply_axes_change(new_config, new_pixmaps)
        if len(changes) == 1:
            name, n, added, dropped = changes[0]
            self._flash(f"{name}: {n} values ({added} added, {dropped} removed)")
        elif changes:
            self._flash("  ".join(f"{name} +{a} −{d}" for name, _n, a, d in changes))

    def _do_change_key(self, axis_name: str, letter: str | None):
        new_keys = {k: n for k, n in self.config.keys.items()
                    if n != axis_name and k != letter}
        if letter is not None:
            new_keys[letter] = axis_name
        from .config import Config
        self.config = Config(
            template=self.config.template,
            axes=self.config.axes,
            keys=new_keys,
            mode=self.config.mode,
            remote=self.config.remote,
        )
        self._rebuild_key_to_axis()
        self.state_changed.emit()
        self._flash(f"{letter} → {axis_name}" if letter else f"removed key for {axis_name!r}")

    # ── pattern change ────────────────────────────────────────────────────────

    def _on_pattern_progress(self, value: int, total: int, label: str):
        dlg = self._pattern_dlg
        if dlg is None:
            return
        if label:
            dlg.setLabelText(label)
        if dlg.maximum() != total:
            dlg.setMaximum(total)
        dlg.setValue(value)

    def set_cmd_caret(self, pos: int) -> None:
        """Move the caret of a free-text argument to *pos* (a click on it)."""
        if self._cmd is None or "caret" not in self._cmd:
            return
        self._cmd["caret"] = max(0, min(pos, len(self._cmd["query"])))
        self._tab_matches = []
        self.state_changed.emit()

    def _complete_axis_arg(self):
        """Tab in :remove-value, :remove-axis and :change-key: complete the
        axis name, then (:remove-value) one of that axis's values.

        A unique match is filled in; several extend the text to what they
        share and are listed on the right of the status bar.
        """
        verb = self._cmd["verb"]
        q = self._cmd["query"][:self._cmd.get("caret", len(self._cmd["query"]))]
        rest = self._cmd["query"][len(q):]
        axis = max((n for n in self.axis_names if q.startswith(n + " ")), key=len, default=None)
        if axis is None:
            head, partial, pool = "", q, list(self.axis_names)
            tail = " " if _AXIS_FIRST_ARGS[verb] else ""
        elif _AXIS_FIRST_ARGS[verb] == "value":
            head, partial, tail = axis + " ", q[len(axis) + 1:], ""
            pool = list(self.axis_values[self.axis_names.index(axis)])
        else:
            return
        low = partial.lower()
        matches = [p for p in pool if p.lower().startswith(low)]
        if not matches:
            self._tab_matches = ["(no match)"]
        elif len(matches) == 1:
            self._tab_matches = []
            partial = matches[0] + tail
        else:
            self._tab_matches = matches
            common = os.path.commonprefix(matches)
            if len(common) > len(partial):
                partial = common
        new = head + partial
        self._cmd["query"] = new + rest
        self._cmd["caret"] = len(new)
        self.state_changed.emit()

    def _complete_path(self):
        """Tab-complete the path portion of a free-text command argument.

        Placeholders count as wildcards, so a template can be completed from
        the top down — `plots/{sensor}/2024-0` + Tab lists what every sensor
        directory holds — instead of completing one concrete path first and
        deleting the components that should vary.
        """
        query = self._cmd["query"]
        caret = self._cmd.get("caret", len(query))
        prefix = query[:caret]
        suffix = query[caret:]

        comp = complete_placeholder_name(prefix, self.axis_names)
        if comp is None:
            from .detect import _is_remote_pattern
            if _is_remote_pattern(prefix):
                comp = self._complete_path_remote(prefix)
            else:
                comp = complete_path(prefix, local_listdir())
        if comp is None:
            return

        self._tab_matches = comp.matches
        self._cmd["query"] = prefix + comp.append + suffix
        self._cmd["caret"] = len(prefix) + len(comp.append)
        self.state_changed.emit()

    def _complete_path_remote(self, prefix: str):
        """Complete a `host:/path` prefix over the session's SFTP connection."""
        if self.config.remote is None or self._remote_conn[1] is None:
            return None
        from .detect import _parse_remote_pattern
        try:
            remote_cfg, remote_path = _parse_remote_pattern(prefix)
        except Exception:
            return None
        # Only the host we are already connected to can be listed
        if remote_cfg.host != self.config.remote.host:
            return None
        return complete_path(remote_path, sftp_listdir(self._remote_conn[1]))

    def _pattern_str(self) -> str:
        """Reconstruct the full pattern string for the current config."""
        if self.config.remote is not None:
            rc = self.config.remote
            prefix = f"{rc.user}@{rc.host}" if rc.user else rc.host
            return f"{prefix}:{self.config.template}"
        return self.config.template

    def _pattern_progress_dialog(self, label: str) -> threading.Event:
        """Open the window-modal progress dialogue of `:pattern`; the returned
        event is set when the user cancels it."""
        from PySide6.QtWidgets import QProgressDialog
        dlg = QProgressDialog(label, "Cancel", 0, 0, self.window())
        dlg.setWindowTitle("juxt")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        self._pattern_dlg = dlg
        cancel_event = threading.Event()
        # Not cancel_event.set; see _download_added_remote.
        dlg.canceled.connect(lambda: cancel_event.set())
        return cancel_event

    def _close_pattern_dlg(self) -> None:
        if self._pattern_dlg is not None:
            _dispose(self._pattern_dlg)
            self._pattern_dlg = None

    def _do_pattern(self, raw: str, subset: str | None = None):
        """Change the template/source without restarting juxt.

        Runs in three steps: discover the new axes (worker), settle what to
        do with the current subset (:meth:`_on_pattern_found`), then load or
        download the images (worker, :meth:`_pattern_load`).

        *subset* decides the fate of the values picked for axes the new
        pattern keeps, i.e. placeholders of the same name: "keep" pins them
        to their current values, "ignore" scans the disk for all of them,
        "smart" keeps them but opens the value picker when the new pattern
        finds values the old one did not have, and "gui" / "cli" ask, in a
        dialogue or in the status bar, whenever keep and ignore would differ.
        Defaults to the `pattern.subset` setting.
        """
        if self._reload_in_progress or self._listing_in_progress:
            self._flash("reload already in progress")
            return
        subset = subset or self._pattern_subset
        raw = expand_home(normalize_template(raw))  # anonymous {} → {axis_1}, …; ~ → home
        self._reload_in_progress = True
        if self._watching and self.config.remote is not None:
            self._poll_timer.stop()

        get_pw = self._get_password
        current = {n: list(vs) for n, vs in zip(self.axis_names, self.axis_values)}
        want = ["keep", "ignore"] if subset in ("gui", "cli", "smart") else [subset]
        # smart compares against every value the old pattern has on disk.
        old = (self.config.template, self.config.remote) if subset == "smart" else None
        cancel_event = self._pattern_progress_dialog("Detecting axes…")

        def _worker():
            def _check():
                if cancel_event.is_set():
                    raise _Cancelled()

            try:
                found = self._discover_pattern(raw, current, want, get_pw, _check, old)
                found["subset"] = subset
                self._pattern_found.emit(found)
            except _Cancelled:
                self._pattern_found.emit(None)
            except Exception as e:
                self._pattern_found.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _discover_pattern(self, raw: str, current: dict[str, list[str]],
                          want: list[str], get_pw, check, old=None) -> dict:
        """Find the axes of pattern *raw*, once per subset choice in *want*.

        Runs in the pattern worker.  Returns `{"raw", "options", "conn",
        "known"}`: *options* maps "keep" / "ignore" to the Config that choice
        gives, or to the exception its scan raised.  "keep" is only offered
        for a template sharing a placeholder with *current*; directories,
        YAML configs and wildcards are always scanned afresh.  With *old*,
        `(template, remote)` of the pattern being replaced, *known* holds
        every value that pattern has on disk (None if it could not be read).
        """
        from pathlib import Path
        from .detect import (
            _is_remote_pattern, _parse_remote_pattern, _parse_template_names,
            _axes_from_sftp_template, _axes_from_local_template,
            detect_config, has_bare_wildcard, resolve_wildcard_template,
        )
        from .config import Config, _auto_keys, load_config

        conn: list = [None, None]
        options: dict[str, object] = {}

        def _templated(template: str, scan, remote=None) -> None:
            names = _parse_template_names(template)[0]
            pins = {n: current[n] for n in names if n in current}
            choices = [c for c in want if c != "keep" or pins] or ["ignore"]
            for choice in choices:
                check()
                try:
                    tmpl, axes = scan(pins if choice == "keep" else None)
                    options[choice] = Config(template=tmpl, axes=axes,
                                             keys=_auto_keys(axes), remote=remote)
                except Exception as e:
                    options[choice] = e

        if _is_remote_pattern(raw):
            remote_cfg, remote_tmpl = _parse_remote_pattern(raw)
            if '{' not in remote_tmpl:
                raise ValueError(
                    "remote directory detection not supported via :pattern; "
                    "use a template with {placeholders}"
                )
            self._pattern_progress.emit(0, 0, "Connecting…")
            check()
            from .loader import _connect_sftp
            conn[0], conn[1] = _connect_sftp(remote_cfg, get_pw)
            self._pattern_progress.emit(0, 0, "Detecting axes…")
            _templated(remote_tmpl,
                       lambda pinned: (remote_tmpl,
                                       _axes_from_sftp_template(remote_tmpl, conn[1], pinned)),
                       remote=remote_cfg)
        elif Path(raw).is_dir():
            options["ignore"], _ = detect_config(Path(raw), None, None)
        elif raw.lower().endswith((".yaml", ".yml")):
            options["ignore"] = load_config(raw)
        elif has_bare_wildcard(raw):
            tmpl, axes = resolve_wildcard_template(raw)
            options["ignore"] = Config(template=tmpl, axes=axes, keys=_auto_keys(axes))
        elif '{' in raw:
            _templated(raw, lambda pinned: _axes_from_local_template(raw, pinned=pinned))
        else:
            raise ValueError(
                f"{raw!r} is not a directory, YAML config, "
                "template pattern, or remote path"
            )
        known = None
        if old is not None and not isinstance(options.get("keep", Exception()), Exception):
            check()
            old_template, old_remote = old
            new_remote = options["keep"].remote
            try:
                if old_remote is None and new_remote is None:
                    known = _axes_from_local_template(old_template)[1]
                elif (old_remote is not None and new_remote is not None
                      and (old_remote.host, old_remote.user, old_remote.port)
                      == (new_remote.host, new_remote.user, new_remote.port)):
                    known = _axes_from_sftp_template(old_template, conn[1])
            except Exception as e:
                log.info(":pattern: could not rescan the old pattern (%s)", e)
        return {"raw": raw, "options": options, "conn": conn, "known": known}

    def _pattern_abort(self, msg: str | None, conn: list | None = None) -> None:
        """End a `:pattern` run that will not replace the config."""
        self._close_pattern_dlg()
        self._pattern_choice = None
        for c in reversed(conn or []):
            try:
                if c is not None:
                    c.close()
            except Exception:
                pass
        self._reload_in_progress = False
        if self._watching and self.config.remote is not None:
            self._poll_timer.start(self._poll_interval * 1000)
        if msg:
            self._flash(msg)
        else:
            self.state_changed.emit()

    def _on_pattern_found(self, found: object):
        """The new axes are known: settle the subset choice, then load."""
        self._close_pattern_dlg()
        if found is None:
            self._pattern_abort(None)
            return
        if isinstance(found, Exception):
            log.warning("Pattern change failed: %s", found)
            self._pattern_abort(f"pattern failed: {found}")
            return
        options: dict = found["options"]
        valid = {c: cfg for c, cfg in options.items()
                 if not isinstance(cfg, Exception) and _n_combos(cfg.axes)}
        subset = found["subset"]

        if subset == "smart":
            keep = valid.get("keep")
            if keep is not None:
                current = {n: list(vs) for n, vs in zip(self.axis_names, self.axis_values)}
                new_vals = _new_values(keep.axes, options.get("ignore"),
                                       found.get("known") or current)
                if new_vals:
                    self._pick_new_values(found, keep, new_vals)
                else:
                    self._pattern_load(found, keep)
                return
            subset = "ignore"   # nothing shared, or the subset is not found there

        if subset in ("keep", "ignore"):
            choice = subset if subset in options else next(iter(options))
            if choice in valid:
                self._pattern_load(found, valid[choice])
                return
            other = "--ignore-subset" if choice == "keep" else "--keep-subset"
            err = options[choice]
            why = err if isinstance(err, Exception) else "no images found"
            self._pattern_abort(f"pattern failed ({choice} subset): {why}"
                                + (f"; try {other}" if choice == "keep" else ""),
                                found["conn"])
            return

        if not valid:
            err = options.get("ignore", options.get("keep"))
            why = err if isinstance(err, Exception) else f"no images found for {found['raw']!r}"
            self._pattern_abort(f"pattern failed: {why}", found["conn"])
            return
        if len(valid) == 1 or _same_values(valid["keep"].axes, valid["ignore"].axes):
            choice = "keep" if "keep" in valid else next(iter(valid))
            if len(options) > len(valid):
                log.info(":pattern: only the %s-subset scan found images", choice)
            self._pattern_load(found, valid[choice])
            return

        if subset == "cli":
            self._pattern_choice = {"found": found, "valid": valid}
            self.state_changed.emit()
            return
        from .dialogs import PatternSubsetDialog
        dlg = PatternSubsetDialog(
            {n: len(v) for n, v in zip(self.axis_names, self.axis_values)},
            {c: {n: len(v) for n, v in cfg.axes.items()} for c, cfg in valid.items()},
            parent=self.window(),
        )
        try:
            dlg.exec()
            choice = dlg.choice
        finally:
            _dispose(dlg)
        self.setFocus()
        self._resolve_pattern_choice(found, valid, choice)

    def _pick_new_values(self, found: dict, keep: Config,
                         new_vals: dict[str, list[str]]) -> None:
        """`pattern.subset: smart` found values the old pattern lacked: let
        the user tick which to load, in the value picker.

        The picker shows the axes with new values (current values ticked,
        new ones unticked) and the axes the new pattern adds (all ticked).
        """
        from .config import _auto_keys
        from .dialogs import ValuesDialog
        names = [n for n in keep.axes if n in new_vals or n not in self.axis_names]
        current = {n: list(keep.axes[n]) for n in names}
        on_disk = {n: current[n] + new_vals.get(n, []) for n in names}
        dlg = ValuesDialog(current, on_disk, title="New values", apply_text="Load",
                           parent=self.window())
        try:
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            chosen = dlg.values()
        finally:
            _dispose(dlg)
        self.setFocus()
        if not accepted:
            self._pattern_abort("pattern unchanged", found["conn"])
            return
        axes = {n: chosen.get(n, vals) for n, vals in keep.axes.items()}
        self._pattern_load(found, Config(template=keep.template, axes=axes,
                                         keys=_auto_keys(axes), remote=keep.remote))

    def _resolve_pattern_choice(self, found: dict, valid: dict, choice: str | None):
        """Go on with the subset *choice* ("keep" / "ignore"), or None to cancel."""
        self._pattern_choice = None
        if choice not in valid:
            self._pattern_abort("pattern unchanged", found["conn"])
            return
        self._pattern_load(found, valid[choice])

    def _pattern_choice_key(self, event: QKeyEvent) -> None:
        """Answer the status-bar question of `pattern.subset: cli`."""
        pc = self._pattern_choice
        k, mods = event.key(), event.modifiers()
        if k in (Qt.Key_K, Qt.Key_Return, Qt.Key_Enter):
            self._resolve_pattern_choice(pc["found"], pc["valid"], "keep")
        elif k == Qt.Key_I:
            self._resolve_pattern_choice(pc["found"], pc["valid"], "ignore")
        elif k == Qt.Key_Escape or (k == Qt.Key_C and mods == Qt.ControlModifier):
            self._resolve_pattern_choice(pc["found"], pc["valid"], None)

    def pattern_choice_text(self) -> tuple[str, str]:
        """Status-bar text of the pending subset question: (prompt, details)."""
        valid = self._pattern_choice["valid"]
        current = {n: len(v) for n, v in zip(self.axis_names, self.axis_values)}
        now = _n_combos(self.axis_values)
        label = {"keep": "keep subset", "ignore": "ignore subset"}
        prompt, details = [], []
        for choice, key in (("keep", "k/Enter"), ("ignore", "i")):
            axes = {n: len(v) for n, v in valid[choice].axes.items()}
            prompt.append(f"{label[choice]}: {_images_with_factor(_n_combos(axes), now)}  [{key}]")
            changes = _axis_count_changes(current, axes)
            details.append(f"{choice}: {'  '.join(changes) or 'same axes'}")
        prompt.append("cancel [Esc]")
        return ":pattern  " + "   ·   ".join(prompt), "   ·   ".join(details)

    def _pattern_load(self, found: dict, config: Config) -> None:
        """Load (local) or download (remote) the images of *config* in a worker."""
        self._pattern_choice = None
        conn = found["conn"]
        # Same host as now: download into the current cache, seeded with the
        # mtimes of the files already there, so only new or changed files are
        # fetched (and _apply_pattern can reuse their pixmaps).
        reuse_remote = None
        old = self.config.remote
        if (config.remote is not None and old is not None and self._remote_tmpdir is not None
                and (old.host, old.user, old.port)
                == (config.remote.host, config.remote.user, config.remote.port)):
            reuse_remote = (self._remote_tmpdir,
                            {self._key_to_path(k): mt for k, mt in self._remote_mtimes.items()})
        cancel_event = self._pattern_progress_dialog("Loading…")

        def _worker():
            def _check():
                if cancel_event.is_set():
                    raise _Cancelled()

            try:
                self._pattern_result.emit(
                    self._materialize_pattern(config, conn, _check, reuse_remote))
            except _Cancelled:
                self._pattern_result.emit(None)
            except Exception as e:
                self._pattern_result.emit(e)

        threading.Thread(target=_worker, daemon=True).start()

    def _materialize_pattern(self, config: Config, conn: list, check,
                             reuse_remote: tuple[str, dict[str, float]] | None = None) -> dict:
        """Map every key of *config* to a local file, downloading remote ones.

        *reuse_remote* is `(tmpdir, {remote path: mtime})` of the current
        session's cache, when the new pattern is on the same host: files
        found there unchanged are not downloaded again.  Runs in the pattern
        worker; the result goes to :meth:`_apply_pattern`.
        """
        from itertools import product as _product
        axis_names = list(config.axes.keys())
        axis_values = list(config.axes.values())
        n = _n_combos(axis_values)
        tmpdir: str | None = None
        mtimes: dict = {}
        plural = "s" if n != 1 else ""
        check()
        if config.remote is not None:
            from .loader import local_cache_path, poll_remote_with_sftp, remote_download_dir
            if reuse_remote is not None:
                tmpdir, by_path = reuse_remote
                for combo in _product(*axis_values):
                    rpath = config.template.format(**dict(zip(axis_names, combo)))
                    if rpath in by_path:
                        key = tuple(vals.index(v) for vals, v in zip(axis_values, combo))
                        mtimes[key] = by_path[rpath]
                self._pattern_progress.emit(0, n, f"Checking {n} image{plural}…")
            else:
                tmpdir = remote_download_dir(config.remote)
                self._pattern_progress.emit(0, n, f"Downloading {n} image{plural}…")

            def _dl_progress(i, _n):
                check()
                self._pattern_progress.emit(i + 1, n, "")

            poll_remote_with_sftp(config.template, config.axes, tmpdir, conn[1], mtimes,
                                  on_progress=_dl_progress)
            key_to_path = {}
            for combo in _product(*axis_values):
                rpath = config.template.format(**dict(zip(axis_names, combo)))
                key = tuple(vals.index(v) for vals, v in zip(axis_values, combo))
                key_to_path[key] = str(local_cache_path(tmpdir, rpath))
        else:
            self._pattern_progress.emit(0, n, f"Loading {n} image{plural}…")
            key_to_path = {
                tuple(vals.index(v) for vals, v in zip(axis_values, combo)):
                config.template.format(**dict(zip(axis_names, combo)))
                for combo in _product(*axis_values)
            }
        return {
            "config": config,
            "key_to_path": key_to_path,
            "conn": conn,
            "tmpdir": tmpdir,
            "mtimes": mtimes,
            "n_images": n,
        }

    def _loaded_pixmaps_by_path(self) -> dict[str, QPixmap]:
        """Every image read so far, keyed by the normalised local file it came
        from (the cache copy, for a remote session)."""
        from .loader import local_cache_path
        out: dict[str, QPixmap] = {}
        remote = self.config.remote is not None
        if remote and self._remote_tmpdir is None:
            return out
        for key, pm in self.pixmaps.items():
            if isinstance(pm, MissingPixmap) or pm.isNull():
                continue
            path = self._key_to_path(key)
            if remote:
                path = str(local_cache_path(self._remote_tmpdir, path))
            out[os.path.normpath(path)] = pm
        return out

    def _apply_pattern(self, result: object):
        self._reload_in_progress = False

        def _close_dlg():
            if self._pattern_dlg is not None:
                _dispose(self._pattern_dlg)
                self._pattern_dlg = None

        if result is None:  # cancelled by user
            _close_dlg()
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        if isinstance(result, Exception):
            log.warning("Pattern change failed: %s", result)
            _close_dlg()
            self._flash(f"pattern failed: {result}")
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        payload = result
        new_config = payload["config"]
        key_to_path = payload["key_to_path"]
        new_conn: list = payload["conn"]
        new_tmpdir: str | None = payload["tmpdir"]
        new_mtimes: dict = payload["mtimes"]
        n_images: int = payload.get("n_images", len(key_to_path))

        # Images already on show are reused rather than read again; after
        # "keep subset" that is most of them.
        loaded = self._loaded_pixmaps_by_path()
        new_pixmaps: dict[tuple, QPixmap] = {}
        todo: list[tuple[tuple, str]] = []
        for key, local_path in key_to_path.items():
            pm = loaded.get(os.path.normpath(local_path))
            if pm is not None:
                new_pixmaps[key] = pm
            else:
                todo.append((key, local_path))
        if new_pixmaps:
            n_images = len(todo)
            self._pattern_progress.emit(
                0, n_images, f"Loading {n_images} new image{'s' if n_images != 1 else ''} "
                             f"({len(new_pixmaps)} already loaded)…")

        def _progress(done: int, total: int):
            if self._pattern_dlg is not None and self._pattern_dlg.wasCanceled():
                raise _Cancelled()
            self._pattern_progress.emit(done, total, "")

        from .loader import load_images
        try:
            new_pixmaps.update(load_images(((k, p, p) for k, p in todo), _progress))
        except _Cancelled:
            _close_dlg()
            if self._watching and self.config.remote is not None:
                self._poll_timer.start(self._poll_interval * 1000)
            return

        _close_dlg()

        # Stop current watching before replacing state
        if self._watching:
            if self.config.remote is None:
                if self._watcher is not None:
                    self._watcher.fileChanged.disconnect(self._on_file_changed)
                    self._watcher.deleteLater()
                    self._watcher = None
                self._path_to_key = {}
            else:
                self._poll_timer.stop()
            self._watching = False

        # Replace remote connection/tmpdir
        try:
            if self._remote_conn[1] is not None:
                self._remote_conn[1].close()
            if self._remote_conn[0] is not None:
                self._remote_conn[0].close()
        except Exception:
            pass
        self._remote_conn = new_conn
        self._remote_tmpdir = new_tmpdir
        self._remote_mtimes = new_mtimes

        # Full viewer state reset (axes may be completely different)
        if self._grid_axis is not None:
            self._exit_grid()
        self.config = new_config
        self.pixmaps = new_pixmaps
        self._axes_gen += 1
        self.axis_names = list(new_config.axes.keys())
        self.axis_values = list(new_config.axes.values())
        self.n_axes = len(self.axis_names)
        self.pos = [0] * self.n_axes
        self.prev = None
        self.focus_stack = list(range(self.n_axes))
        self._locked_h = None
        self._locked_v = None
        self._rebuild_key_to_axis()

        # Restart watching with the new config
        if new_config.remote is not None and new_tmpdir is not None:
            self._start_watching(silent=True)
        elif new_config.remote is None:
            self._start_watching(silent=True)

        log.info("Pattern changed to %s", self.config.template)
        self._flash("pattern updated")
        self._refresh()
        self.config_changed.emit()

    # ── public ────────────────────────────────────────────────────────────────

    def fit_image(self):
        if self._grid_widget is not None:
            self._grid_widget.fit_image()
            return
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        self._fit = "image"

    def fit_height(self):
        if self._grid_widget is not None:
            self._grid_widget.fit_height()
            return
        pm = self._item.pixmap()
        if pm.isNull():
            return
        factor = self.viewport().height() / pm.height()
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "height"

    def fit_width(self):
        if self._grid_widget is not None:
            self._grid_widget.fit_width()
            return
        pm = self._item.pixmap()
        if pm.isNull():
            return
        factor = self.viewport().width() / pm.width()
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(self._scene.sceneRect().center())
        self._fit = "width"

    def fit_off(self):
        """Stop fitting: keep the view as it is, and leave it alone when the
        window is resized or the image changes.  Zooming also ends it."""
        if self._grid_widget is not None:
            self._grid_widget.fit_off()
            return
        self._fit = None

    def reset_zoom(self):
        if self._grid_widget is not None:
            self._grid_widget.reset_zoom()
            return
        self._fit = None
        self.resetTransform()

    def set_zoom(self, pct: float):
        if self._grid_widget is not None:
            self._grid_widget.set_zoom(pct)
            return
        self._fit = None
        self.resetTransform()
        self.scale(pct / 100.0, pct / 100.0)

    # ── Qt overrides ──────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        # Tab is consumed by Qt's focus traversal before keyPressEvent sees it.
        # Intercept it at the application level so it reaches _cmd_handle_key
        # regardless of which widget currently holds focus (e.g. a _CellView in grid mode).
        # Only intercept during command mode — normal Tab behaviour is preserved elsewhere.
        if (event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key_Tab
                and self._cmd is not None):
            log.debug("eventFilter: Tab intercepted (cmd=%s)", self._cmd)
            self._cmd_handle_key(event)
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QKeyEvent):
        k = event.key()
        mods = event.modifiers()

        # A pending :pattern subset question takes every key until answered.
        if self._pattern_choice is not None:
            self._pattern_choice_key(event)
            return

        # Ctrl+C copies a sidebar text selection when there is one.  Checked
        # before everything else so it wins over an axis key or a binding on
        # the same chord; without a selection (or with sidebar.copy: off) the
        # chord falls through as usual.
        if (k == Qt.Key_C and mods == Qt.ControlModifier
                and self._sidebar_copy == "selected" and self._copy_selection()):
            return

        # Configurable keybindings — checked first, work in every state
        bound = self._keybindings.get((k, mods.value))
        if bound is not None:
            self._dispatch_action(bound)
            return
        # Ctrl+C cancels any active command or selection mode
        if k == Qt.Key_C and mods == Qt.ControlModifier:
            if self._cmd is not None or self._sel is not None:
                self._cmd = None
                self._sel = None
                self.state_changed.emit()
            return

        # Command mode intercepts all input while active
        if self._cmd is not None:
            if not self._cmd_handle_key(event):
                super().keyPressEvent(event)
            return

        # Semi-global shortcuts — work in normal mode and selection mode
        if k == Qt.Key_0 and mods == Qt.NoModifier:
            self.reset_zoom()
            return

        # Incremental-search intercepts everything while active
        if self._sel is not None:
            if not self._sel_handle_key(event):
                super().keyPressEvent(event)
            return

        # Colon opens command mode
        if event.text() == ":":
            self._cmd = {"phase": "verb", "query": "", "cursor": -1}
            self.state_changed.emit()
            return

        # Universal navigation keys (all modes)
        if k == Qt.Key_Right:
            self._navigate(self._h_axis(), 1)
            return
        if k == Qt.Key_Left:
            self._navigate(self._h_axis(), -1)
            return
        if k == Qt.Key_Down:
            v = self._v_axis()
            if v is not None:
                self._navigate(v, 1)
            return
        if k == Qt.Key_Up:
            v = self._v_axis()
            if v is not None:
                self._navigate(v, -1)
            return
        if k == Qt.Key_Space:
            if self.prev is not None:
                self.pos, self.prev = self.prev, list(self.pos)
                self._refresh()
            return
        if k == Qt.Key_Home:
            self.prev = list(self.pos)
            self.pos[self._h_axis()] = 0
            self._refresh()
            return
        if k == Qt.Key_End:
            h = self._h_axis()
            self.prev = list(self.pos)
            self.pos[h] = len(self.axis_values[h]) - 1
            self._refresh()
            return
        if Qt.Key_1 <= k <= Qt.Key_9:
            idx = k - Qt.Key_1
            h = self._h_axis()
            if idx < len(self.axis_values[h]):
                self.prev = list(self.pos)
                self.pos[h] = idx
                self._refresh()
            return
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self._toggle_fullscreen()
            return
        if k == Qt.Key_Escape:
            win = self.window()
            if win.isFullScreen():
                self._toggle_fullscreen()
            return

        # Mode-specific letter handling
        ch = event.text()
        ch_lower = ch.lower()
        # When Ctrl is held, event.text() is a control character (\x01–\x1a).
        # Recover the letter from the Qt key code instead.
        if mods == Qt.ControlModifier and Qt.Key_A <= k <= Qt.Key_Z:
            ch_lower = chr(k).lower()

        picker_mod, reverse_mod = _modifier_roles(self._swap_modifiers)

        if self.nav_mode == NavMode.PIN:
            if mods == picker_mod and ch_lower in self.key_to_axis:
                self._sel_open_value(self.key_to_axis[ch_lower])
            elif mods == Qt.NoModifier and ch_lower in self.key_to_axis:
                self._focus(self.key_to_axis[ch_lower])
                self.state_changed.emit()
            else:
                super().keyPressEvent(event)

        elif self.nav_mode == NavMode.SEEK:
            if mods == Qt.NoModifier and ch.isalpha():
                self._sel_open_axis(ch)
            else:
                super().keyPressEvent(event)

        elif self.nav_mode == NavMode.TAP:
            if mods == picker_mod and ch_lower in self.key_to_axis:
                self._sel_open_value(self.key_to_axis[ch_lower])
            elif ch_lower in self.key_to_axis and mods in (Qt.NoModifier, reverse_mod):
                # The modifier decides the direction, not the letter's case:
                # with the roles swapped an uppercase letter opens the picker.
                axis = self.key_to_axis[ch_lower]
                self._focus(axis)
                self._navigate(axis, -1 if mods == reverse_mod else 1,
                               ensure_visible=True)
            else:
                super().keyPressEvent(event)

    # ── sidebar text selection ───────────────────────────────────────────────

    def add_copy_source(self, widget: QTextEdit) -> None:
        """Let Ctrl+C copy whatever is selected in *widget*.

        The sidebars never take keyboard focus, so Qt's own copy shortcut
        cannot reach them; the view relays it instead.  A selection in one
        source clears the others, so the chord never has two candidates.
        """
        self._copy_sources.append(widget)
        widget.selectionChanged.connect(lambda w=widget: self._on_source_selection(w))

    def _on_source_selection(self, widget: QTextEdit) -> None:
        if not widget.textCursor().hasSelection():
            return
        for other in self._copy_sources:
            if other is not widget and other.textCursor().hasSelection():
                cursor = other.textCursor()
                cursor.clearSelection()
                other.setTextCursor(cursor)

    def _copy_selection(self) -> bool:
        """Copy the selected sidebar text; False when nothing is selected."""
        for src in self._copy_sources:
            cursor = src.textCursor()
            # A hidden dock can still hold a selection; it was never in sight.
            if src.isVisible() and cursor.hasSelection():
                # The fragment, not selectedText(): that one uses U+2029 for
                # line breaks and keeps the non-breaking spaces in the values.
                text = cursor.selection().toPlainText()
                QApplication.clipboard().setText(text)
                self._flash(f"copied selection ({len(text)} characters)")
                return True
        return False

    # ── context menu ─────────────────────────────────────────────────────────

    def _shortcut_for(self, action: str) -> str:
        """The chord currently bound to *action*, or "" when it is unbound."""
        for (key, mods), bound in self._keybindings.items():
            if bound == action:
                return _chord_str(key, mods)
        return ""

    def build_image_menu(self) -> QMenu:
        """The right-click menu for the image currently on screen.

        Also serves the grid panes: the press that opened the menu has already
        moved grid focus, so the current position names the pane in hand.
        Each entry carries the chord bound to its action, so rebinding
        copy-path in settings.yaml moves the shortcut shown here too.
        """
        menu = QMenu(self)
        for label, action in (("Copy image &path", "copy-path"),
                              ("Copy &image", "copy-image"),
                              ("&Reveal in file manager", "reveal")):
            act = menu.addAction(label)
            chord = self._shortcut_for(action)
            if chord:
                act.setShortcut(chord)
            act.triggered.connect(lambda _checked=False, a=action: self._cmd_execute(a))
        return menu

    def show_image_menu(self, global_pos):
        self.build_image_menu().exec(global_pos)

    def contextMenuEvent(self, event):
        self.show_image_menu(event.globalPos())
        event.accept()

    def mouseDoubleClickEvent(self, _event):
        self.fit_image()

    def wheelEvent(self, event):
        mods = event.modifiers()
        delta = event.angleDelta().y()
        if mods == Qt.ControlModifier:
            factor = 1.15 if delta > 0 else 1 / 1.15
            self.scale(factor, factor)
            self._fit = None    # reset _fit state
        elif mods == Qt.ShiftModifier:
            v = self._v_axis()
            if delta != 0 and v is not None:
                self._navigate(v, 1 if delta > 0 else -1)
        else:
            if delta != 0:
                self._navigate(self._h_axis(), 1 if delta > 0 else -1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._fit is not None:
            _fitter = getattr(self, f"fit_{self._fit}")
            _fitter()   # keep fit state after resize


class InfoPanel(QTextEdit):
    """Read-only sidebar listing every axis; values are clickable links."""

    value_clicked = Signal(int, int)  # axis index, value index
    path_clicked = Signal()
    path_reveal_clicked = Signal()    # Ctrl+click on the path
    edit_clicked = Signal(int)        # axis index: open its value picker

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumWidth(180)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._style_for_theme()
        self.viewport().setMouseTracking(True)
        self._highlight = _HL_DEFAULT
        self._separator = "  "
        self._list_larger_than = 20
        self._last_view: "ImageView | None" = None
        self._path_lines_cache_key: tuple[str, int] | None = None
        self._path_lines_cache: int = 1
        self._refreshing = False
        # Axis name → expanded, for axes the user flipped away from the
        # automatic choice (see _is_expanded); keyed by name so it survives
        # a reload that reorders the axes.
        self._expanded: dict[str, bool] = {}
        self._press_axis: int | None = None
        self._press_path = False
        self._press_reveal = False
        self._press_edit: int | None = None
        # href → full text, for every value the last layout had to elide.
        self._elided: dict[str, str] = {}

    def _style_for_theme(self) -> None:
        self.setStyleSheet(_panel_css())
        # Anchors carry the value links; keep them looking like plain values.
        self.document().setDefaultStyleSheet(
            f"a {{ color: {_theme.current().text}; text-decoration: none; }}"
        )

    def apply_theme(self) -> None:
        """Restyle for the active theme, keeping the scroll position."""
        self._style_for_theme()
        if self._last_view is not None:
            self.refresh(self._last_view, follow_target=False)

    def set_highlight(self, hl: Highlight) -> None:
        """Set the format used for the current value on each axis."""
        self._highlight = hl

    def set_value_list_style(self, separator: str, list_larger_than: int) -> None:
        """Set the inline-value separator and the one-per-line length threshold."""
        self._separator = separator
        self._list_larger_than = list_larger_than

    def mousePressEvent(self, event):
        href = self.anchorAt(event.position().toPoint())
        super().mousePressEvent(event)
        w = self.window()
        if hasattr(w, "view"):
            w.view.setFocus()
        self._press_axis = None
        self._press_path = False
        self._press_reveal = False
        self._press_edit = None
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = _parse_value_href(href)
        if target is not None:
            self.value_clicked.emit(*target)
        elif href == _PATH_HREF:
            self._press_path = True
            # Qt maps the Command key to ControlModifier on macOS.
            self._press_reveal = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        elif _parse_edit_href(href) is not None:
            self._press_edit = _parse_edit_href(href)
        else:
            self._press_axis = _parse_axis_href(href)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        # A header toggles (and the path copies) on release, and only for a
        # plain click, so that dragging across one to select its text does
        # not rebuild the document under the selection.
        axis, self._press_axis = self._press_axis, None
        on_path, self._press_path = self._press_path, False
        reveal, self._press_reveal = self._press_reveal, False
        edit, self._press_edit = self._press_edit, None
        if (event.button() != Qt.MouseButton.LeftButton
                or self.textCursor().hasSelection()):
            return
        href = self.anchorAt(event.position().toPoint())
        if axis is not None and _parse_axis_href(href) == axis:
            self.toggle_axis(axis)
        elif on_path and href == _PATH_HREF:
            (self.path_reveal_clicked if reveal else self.path_clicked).emit()
        elif edit is not None and _parse_edit_href(href) == edit:
            self.edit_clicked.emit(edit)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        href = self.anchorAt(event.position().toPoint())
        over_link = (_parse_value_href(href) is not None
                     or _parse_axis_href(href) is not None
                     or _parse_edit_href(href) is not None
                     or href == _PATH_HREF)
        self.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if over_link
            else Qt.CursorShape.IBeamCursor
        )

    def viewportEvent(self, event):
        # Qt's own tooltip lookup did not reliably reach the viewport, so the
        # panel answers it: a value cut short shows its full text, the path
        # says what a click does, anything else shows nothing.
        if event.type() == QEvent.Type.ToolTip:
            href = self.anchorAt(event.pos())
            if href == _PATH_HREF:
                full = "click to copy  ·  Ctrl+click to reveal in file manager"
            elif _parse_edit_href(href) is not None:
                full = "add or remove values  (:values)"
            else:
                full = self._elided.get(href)
            if full:
                QToolTip.showText(event.globalPos(), full, self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    def _auto_expanded(self, vals: list[str]) -> bool:
        """Whether an axis starts out one value per row: any value with a
        space, or longer than the threshold, would read badly run together."""
        return any(" " in v or len(v) > self._list_larger_than for v in vals)

    def _is_expanded(self, name: str, vals: list[str]) -> bool:
        if name in self._expanded:
            return self._expanded[name]
        return self._auto_expanded(vals)

    def toggle_axis(self, axis: int) -> None:
        """Flip one axis between several values per row and one per row."""
        view = self._last_view
        if view is None or not 0 <= axis < len(view.axis_names):
            return
        name, vals = view.axis_names[axis], view.axis_values[axis]
        expanded = not self._is_expanded(name, vals)
        if expanded == self._auto_expanded(vals):
            self._expanded.pop(name, None)   # back to automatic, follows settings
        else:
            self._expanded[name] = expanded
        self.refresh(view, follow_target=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._last_view is not None:
            self.refresh(self._last_view)

    def _worst_case_path(self, view: "ImageView") -> str:
        """The longest path *view*'s template could ever produce: every
        placeholder resolved to its longest value on that axis.

        Not necessarily a real combination, but its length is a true upper
        bound on any actual path's length, which is all a wrap-height
        reservation needs.
        """
        path = view.config.template
        for name, vals in zip(view.axis_names, view.axis_values):
            path = path.replace(f"{{{name}}}", max(vals, key=len))
        return path

    def _worst_case_line_count(self, worst_case_path: str) -> int:
        """Number of lines the widest possible path wraps to at the current
        width, cached per (text, width) so the common case — same config,
        same size — costs nothing beyond a dict lookup."""
        cache_key = (worst_case_path, self.viewport().width())
        if cache_key != self._path_lines_cache_key:
            self._path_lines_cache = self._line_count(worst_case_path)
            self._path_lines_cache_key = cache_key
        return self._path_lines_cache

    def _line_count(self, text: str) -> int:
        """Number of visual lines *text* wraps to at the panel's current
        width. Renders *text* alone through this widget's own document —
        reusing the real font and margins instead of guessing at them — and
        never paints on screen since it's always immediately overwritten
        within the same refresh() call."""
        self.setHtml(
            "<html><body style='font-family:\"Courier New\",monospace; "
            "font-size:9pt; margin:8px;'>" + _html.escape(text) + "</body></html>"
        )
        return max(1, self.document().begin().layout().lineCount())

    def _fragment_span_for_href(self, href: str) -> tuple[int, int] | None:
        """(start, end) document positions of the text fragment whose anchor
        href is exactly *href*, or None if it isn't in the document.

        Walks fragments directly rather than searching rendered text, since
        the target value's text isn't necessarily unique across the sidebar
        (e.g. the same value string on two different axes).
        """
        block = self.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().anchorHref() == href:
                    return frag.position(), frag.position() + frag.length()
                it += 1
            block = block.next()
        return None

    def _value_link(
        self, axis: int, val: int, text: str, *, current: bool, dim: bool,
        room: int, fm: QFontMetrics, bold_fm: QFontMetrics,
    ) -> tuple[str, int]:
        """(html, pixel width) of one value's link, elided to fit *room*.

        A value wider than a whole row is cut short with an ellipsis instead
        of being broken across lines; its full text is kept for the tooltip.
        """
        hl = self._highlight
        # The highlight's own delimiters take room too; a raw html: template
        # cannot be measured, so it is assumed not to add any.
        decor = hl.prefix + hl.suffix if current and hl.raw is None else ""
        metrics = bold_fm if current and hl.bold else fm
        decor_w = metrics.horizontalAdvance(decor)
        limit = max(room - decor_w, fm.horizontalAdvance("…"))
        shown, href = text, _value_href(axis, val)
        if metrics.horizontalAdvance(text) > limit:
            shown = metrics.elidedText(text, Qt.TextElideMode.ElideRight, limit)
            self._elided[href] = text
        if current:
            inner = _hl(_sp(shown), hl)
        elif dim:
            inner = f"<span style='color:{_theme.current().dim}'>{_sp(shown)}</span>"
        else:
            inner = _sp(shown)
        width = metrics.horizontalAdvance(shown) + decor_w
        return f'<a href="{href}">{inner}</a>', width

    def _axes_html(self, view: "ImageView", text_px: int) -> str:
        """The axis sections, laid out for *text_px* of usable line width.

        Rows are packed here, by measured width, and joined with explicit
        breaks.  The values and their separators are non-breaking, so left
        to Qt a whole axis is one unbreakable word that gets cut at an
        arbitrary character.
        """
        self._elided.clear()
        fm = self.fontMetrics()
        bold_font = self.font()
        bold_font.setBold(True)
        bold_fm = QFontMetrics(bold_font)
        text_px = max(text_px, 4 * fm.horizontalAdvance("M"))
        bullet_w = fm.horizontalAdvance("• ")
        sep_w = fm.horizontalAdvance(self._separator)
        sep_html = _sp(self._separator)

        dim_axis = view.focus_stack[0] if view._dim_missing else None
        parts = []
        for i, (name, vals) in enumerate(zip(view.axis_names, view.axis_values)):
            gaps = view._missing_along(i) if i == dim_axis else None
            expanded = self._is_expanded(name, vals)
            room = text_px - (bullet_w if expanded else 0)
            links = [
                self._value_link(
                    i, j, v, current=(j == view.pos[i]), dim=bool(gaps and gaps[j]),
                    room=room, fm=fm, bold_fm=bold_fm,
                )
                for j, v in enumerate(vals)
            ]
            if expanded:
                val_html = "<br>".join(f"&bull;&nbsp;{h}" for h, _w in links)
            else:
                rows = _pack_rows([w for _h, w in links], sep_w, text_px)
                val_html = "<br>".join(
                    sep_html.join(links[k][0] for k in row) for row in rows
                )
            marker = "▾" if expanded else "▸"
            header = _theme.current().header
            parts.append(
                f'<a href="{_axis_href(i)}">'
                f"<span style='color:{header}'>{marker}&nbsp;{_html.escape(name)}</span>"
                f'</a>&nbsp;&nbsp;<a href="{_edit_href(i)}">'
                f"<span style='color:{_theme.current().muted}'>[+]</span></a>"
                f"<br>{val_html}<br><br>"
            )
        return "".join(parts)

    def refresh(self, view: "ImageView", follow_target: bool = True):
        # setHtml() below can grow or shrink the document enough to toggle
        # the scrollbar's visibility, which changes the viewport's width and
        # so fires a synchronous resizeEvent() *while still inside* this same
        # setHtml() call. resizeEvent() calls refresh() again to reflow for
        # the new width, reentering here before the outer setHtml() has
        # returned, which corrupts the document (observed as its content
        # appearing twice). The nested call is skipped; the outer one packs
        # its rows again below if the width did change under it.
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self._last_view = view
            self.ensurePolished()
            saved_scroll = self.verticalScrollBar().value()
            e = _html.escape
            path = view._key_to_path(view._key())
            # Pad the path with blank lines up to the widest path this config
            # could ever show, so the axis list below never shifts when the
            # current path's own wrapped height changes between navigations
            # (most noticeable — and most disorienting — when it's a click on
            # a sidebar value that triggers the change).
            worst_lines = self._worst_case_line_count(self._worst_case_path(view))
            pad = max(0, worst_lines - self._line_count(path))
            path_html = (
                f"<span style='color:{_theme.current().header}'>path</span><br>"
                f'<a href="{_PATH_HREF}">{e(path)}</a>' + "<br>" * pad + "<br><br>"
            )
            # The rows depend on the viewport width, which the scrollbar
            # takes a slice of once the document outgrows it.  Lay out for
            # the width in force, and if showing the result changed that
            # width, lay out again for the new one (it settles in a pass or
            # two: a narrower layout is only ever taller).
            width = self.viewport().width()
            for _attempt in range(3):
                self.setHtml(
                    _panel_body()
                    + path_html
                    + self._axes_html(view, width - _SIDEBAR_TEXT_INSET_PX)
                    + "</body></html>"
                )
                if self.viewport().width() == width:
                    break
                width = self.viewport().width()
            # setHtml() always resets the scrollbar to the top; put it back
            # where the user left it first, so a forced-visible target
            # (below) is judged against the scroll position the user
            # actually had, not against the fresh-document top setHtml()
            # just reset it to.
            self.verticalScrollBar().setValue(saved_scroll)

            # A refresh triggered by e.g. tap-mode letter-shortcut stepping
            # asks to keep a specific value visible. ensureCursorVisible()
            # scrolls the minimum needed for that, or not at all when it's
            # already on screen. A value long enough to word-wrap spans more
            # than one document position, so ensure the end is visible
            # first and the start second, since the second call wins if the
            # whole span is taller than the viewport.
            target = view._sidebar_target if follow_target else None
            if (
                target is not None
                and 0 <= target[0] < len(view.axis_names)
                and target[1] == view.pos[target[0]]
            ):
                span = self._fragment_span_for_href(_value_href(*target))
                if span is not None:
                    start, end = span
                    for pos in (end, start):
                        cursor = QTextCursor(self.document())
                        cursor.setPosition(pos)
                        self.setTextCursor(cursor)
                        self.ensureCursorVisible()
        finally:
            self._refreshing = False


class ShortcutPanel(QTextEdit):
    """Read-only sidebar listing the shortcuts that are live right now.

    Everything here is read back out of the view rather than hard-coded: the
    navigation mode decides which letter-key section applies, the axis keys
    are the ones currently assigned, the modifier roles follow modifiers.swap,
    and the chords come from the user's own keybindings.  A panel that lists
    keys the running juxt does not have would be worse than no panel at all.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumWidth(240)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(_panel_css())
        self._last_view: "ImageView | None" = None
        self._refreshing = False

    def apply_theme(self) -> None:
        """Restyle for the active theme."""
        self.setStyleSheet(_panel_css())
        if self._last_view is not None:
            self.refresh(self._last_view)

    def resizeEvent(self, event):
        # The key column is measured against the viewport, which is not laid
        # out yet at construction; re-measure whenever the dock is resized.
        super().resizeEvent(event)
        if self._last_view is not None:
            self.refresh(self._last_view)

    @staticmethod
    def _sections(view: "ImageView") -> list[tuple[str, list[tuple[str, str]]]]:
        picker_mod, reverse_mod = _modifier_roles(view._swap_modifiers)
        picker = "Ctrl" if picker_mod == Qt.ControlModifier else "Shift"
        reverse = "Ctrl" if reverse_mod == Qt.ControlModifier else "Shift"

        navigate = [
            ("← / →", f"cycle {view.axis_names[view._h_axis()]}"),
            ("↑ / ↓", f"cycle {view.axis_names[view._v_axis()]}"
                     if view._v_axis() is not None else "cycle the second axis"),
            ("Space", "flip to the previous position"),
            ("Home / End", "first / last value on the primary (←/→) axis"),
            ("1–9", "Nth value on the primary (←/→) axis"),
            (":", "command mode"),
            ("Enter", "toggle fullscreen"),
            ("Escape", "leave fullscreen / cancel"),
        ]

        if view.nav_mode == NavMode.TAP:
            mode = [
                ("letter", "step +1 on that axis"),
                (f"{reverse}+letter", "step −1 on that axis"),
                (f"{picker}+letter", "open the value picker for that axis"),
            ]
        elif view.nav_mode == NavMode.PIN:
            mode = [
                ("letter", "focus that axis on ←/→"),
                (f"{picker}+letter", "open the value picker for that axis"),
            ]
        else:
            mode = [
                ("letter", "search axes, then values"),
                ("Enter", "confirm the first candidate"),
                ("Backspace", "undo one character / step back"),
            ]

        view_controls = [
            ("0", "zoom back to 100%"),
            ("double-click", "fit the image to the window"),
            ("drag", "pan"),
            ("wheel", "step the primary (←/→) axis"),
            ("Shift+wheel", "step the secondary (↑/↓) axis"),
            ("Ctrl+wheel", "zoom under the cursor"),
            ("right-click", "copy path / image menu"),
        ]

        sections = [
            ("navigate", navigate),
            (f"{view.nav_mode.label} mode", mode),
        ]
        if view.nav_mode != NavMode.SEEK:
            axis_keys = [(ch, view.axis_names[i])
                         for ch, i in sorted(view.key_to_axis.items())]
            if axis_keys:
                sections.append(("axis keys", axis_keys))
        sections.append(("view", view_controls))

        bound = sorted(
            (_chord_str(k, m), action)
            for (k, m), action in view._keybindings.items()
        )
        if bound:
            sections.append(("bound to actions", bound))
        return sections

    def _key_column_px(self, sections) -> int:
        """Width the key column needs for the widest chord on show.

        Measured rather than fixed: the panel's font is whatever the platform
        resolves 'Courier New' to, so a hard-coded width wraps chords like
        Ctrl+Shift+H on some systems and leaves a gap on others.  Capped at
        half the viewport so a stray long entry cannot squeeze the
        descriptions out.
        """
        fm = self.fontMetrics()
        widest = max(
            (fm.horizontalAdvance(key) for _title, rows in sections for key, _d in rows),
            default=0,
        )
        ceiling = max(_KEY_COL_MIN_PX, self.viewport().width() // 2)
        return min(max(widest + 12, _KEY_COL_MIN_PX), ceiling)

    def refresh(self, view: "ImageView"):
        # quote=False: this lands in cell content, and Qt's rich-text reader
        # leaves &#x27; standing where a plain apostrophe reads fine.
        def e(s: str) -> str:
            return _html.escape(s, quote=False)

        # setHtml() can toggle the scrollbar, which resizes the viewport and
        # re-enters here through resizeEvent() before the outer call returns —
        # the same reentrancy InfoPanel guards against, with the same fix.
        if self._refreshing:
            return
        self._refreshing = True
        self._last_view = view
        sections = self._sections(view)
        key_col = self._key_column_px(sections)
        t = _theme.current()
        parts = []
        for title, rows in sections:
            # A table, not two spans on one line: a description long enough to
            # wrap continues in its own column instead of running back under
            # the chord, where the two were impossible to tell apart.
            cells = "".join(
                "<tr>"
                f"<td valign='top' width='{key_col}' style='color:{t.accent}'>"
                # The chord itself must not break across lines, so its spaces
                # ("Home / End") stay non-breaking.
                f"{e(key).replace(' ', '&nbsp;')}</td>"
                # The description keeps ordinary spaces, so it wraps at word
                # boundaries within the column.
                f"<td valign='top'>{e(desc)}</td>"
                "</tr>"
                for key, desc in rows
            )
            parts.append(
                f"<div style='color:{t.header}; font-weight:bold'>{e(title)}</div>"
                f"<table cellspacing='0' cellpadding='1' width='100%'>{cells}</table>"
                "<br>"
            )
        try:
            self.setHtml(
                _panel_body()
                + "".join(parts)
                + "</body></html>"
            )
        finally:
            self._refreshing = False


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: Config,
        pixmaps: dict,
        watch: bool = True,
        remote_tmpdir: str | None = None,
        get_password: object = None,
        poll_interval: int = 0,
        remote_mtimes: dict | None = None,
        axis_h: str | None = None,
        axis_v: str | None = None,
        session_name: str | None = None,
        pinned_axes: Collection[str] = (),
        seek_greedy: bool = True,
        seek_fuzzy: bool = True,
        swap_modifiers: bool = False,
        skip_missing: bool = False,
        dim_missing: bool = False,
        sidebar_copy: str = "selected",
        pattern_subset: str = "smart",
        keybindings: dict[str, str] | None = None,
        highlight: Highlight | None = None,
        highlight_candidates: Highlight | None = None,
        sidebar_separator: str = "  ",
        sidebar_list_larger_than: int = 20,
        grid: str | None = None,
        grid_values: list[str] | None = None,
        grid_layout: tuple[int, int] | None = None,
        grid_sharex: bool = True,
        grid_sharey: bool = True,
        show_info: bool = False,
        show_status_bar: bool = True,
        show_keys: bool = False,
        theme: str = "auto",
    ):
        super().__init__()
        # The theme asked for this session (:theme changes it), and the one
        # settings.yaml last named, so a reload only overrides :theme when
        # the file's own value changed.
        self._theme_setting = self._theme_file_setting = theme
        self._applying_theme = False
        _theme.apply(theme)
        self._session_name = session_name
        self._pinned_axes = tuple(pinned_axes)
        self._hl = highlight or _HL_DEFAULT
        self._hl_candidates = highlight_candidates or _HL_DEFAULT_CANDIDATES
        self.setWindowTitle(_window_title(config, session_name, self._pinned_axes))
        self.view = ImageView(
            config, pixmaps, self,
            watch=watch,
            remote_tmpdir=remote_tmpdir,
            get_password=get_password,
            poll_interval=poll_interval,
            remote_mtimes=remote_mtimes,
            axis_h=axis_h,
            axis_v=axis_v,
            seek_greedy=seek_greedy,
            seek_fuzzy=seek_fuzzy,
            swap_modifiers=swap_modifiers,
            skip_missing=skip_missing,
            dim_missing=dim_missing,
            sidebar_copy=sidebar_copy,
            pattern_subset=pattern_subset,
            keybindings=keybindings,
            grid=grid,
            grid_values=grid_values,
            grid_layout=grid_layout,
            grid_sharex=grid_sharex,
            grid_sharey=grid_sharey,
        )
        self._stack = QStackedWidget()
        self._stack.addWidget(self.view)
        self.setCentralWidget(self._stack)

        bar = self.statusBar()
        self._status_label = _ElidingLabel()
        bar.addWidget(self._status_label, 1)
        self._help_label = QLabel()
        bar.addPermanentWidget(self._help_label)
        # What the prompt shows of a free-text argument, for placing the caret
        # on a click: (text before it, query, caret, start, end).
        self._prompt_map: tuple[str, str, int, int, int] | None = None
        self._status_label.clicked.connect(self._on_status_clicked)
        self._style_status_bar()
        bar.setVisible(show_status_bar)

        self._info_panel = InfoPanel()
        self._info_panel.set_highlight(self._hl)
        self._info_panel.set_value_list_style(sidebar_separator, sidebar_list_larger_than)
        self._info_dock = QDockWidget("Axes", self)
        self._info_cli_button = QPushButton("copy CLI command")
        self._info_cli_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._info_cli_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._info_cli_button.setToolTip(
            "copy a juxt command line that reopens this view  (:copy-cli-command)")
        self._info_cli_button.clicked.connect(self.view.copy_cli_command)
        info_box = QWidget()
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(0)
        info_layout.addWidget(self._info_panel, 1)
        info_layout.addWidget(self._info_cli_button)
        self._info_box = info_box
        self._style_info_button()
        self._info_dock.setWidget(info_box)
        self._info_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self._info_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._info_dock)
        self._info_dock.setVisible(show_info)
        if show_info:
            self._info_panel.refresh(self.view)
        self._info_panel.value_clicked.connect(self.view.goto_value)
        self._info_panel.path_clicked.connect(self.view.copy_path)
        self._info_panel.path_reveal_clicked.connect(self.view.reveal_path)
        self._info_panel.edit_clicked.connect(self.view.open_values_dialog)

        # Docked opposite the info sidebar so the two can be open at once.
        self._shortcut_panel = ShortcutPanel()
        self._shortcut_dock = QDockWidget("Shortcuts", self)
        self._shortcut_dock.setWidget(self._shortcut_panel)
        self._shortcut_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self._shortcut_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable |
            QDockWidget.DockWidgetFeature.DockWidgetMovable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._shortcut_dock)
        self._shortcut_dock.setVisible(show_keys)
        if show_keys:
            self._shortcut_panel.refresh(self.view)

        self.view.add_copy_source(self._info_panel)
        self.view.add_copy_source(self._shortcut_panel)

        self.view.state_changed.connect(self._update_status)
        self.view.state_changed.connect(self._refresh_info_panel)
        self.view.state_changed.connect(self._refresh_shortcut_panel)
        self.view.toggle_bar.connect(self._toggle_status_bar)
        self.view.toggle_info.connect(self._toggle_info_panel)
        self.view.toggle_keys.connect(self._toggle_shortcut_panel)
        self.view.config_changed.connect(
            lambda: self.setWindowTitle(
                _window_title(self.view.config, self._session_name, self._pinned_axes))
        )
        self.view.grid_mode_changed.connect(self._on_grid_mode_changed)
        self.view.theme_requested.connect(self._on_theme_requested)
        hints = QApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):      # Qt 6.5+
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

        self._bar_auto_shown = False
        self._bar_hide_timer = QTimer(self)
        self._bar_hide_timer.setSingleShot(True)
        self._bar_hide_timer.setInterval(50)
        self._bar_hide_timer.timeout.connect(self._auto_hide_bar)

        self._spinner_frame = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(100)
        self._spinner_timer.timeout.connect(self._tick_spinner)

        self._settings_watcher = QFileSystemWatcher(self)
        if SETTINGS_PATH.exists():
            self._settings_watcher.addPath(str(SETTINGS_PATH))
        self._settings_watcher.fileChanged.connect(self._on_settings_changed)

        self._update_status()
        self._warn_keybinding_conflicts(flash=False)

    # ── theme ─────────────────────────────────────────────────────────────────

    def _style_status_bar(self) -> None:
        t = _theme.current()
        font = "font-family: 'Courier New', monospace; font-size: 9pt; "
        self.statusBar().setStyleSheet(
            f"QStatusBar {{ background: {t.surface}; color: {t.text}; {font}"
            f"border-top: 1px solid {t.border}; }}"
            "QStatusBar::item { border: none; }"
        )
        self._status_label.setStyleSheet(
            f"color: {t.text}; padding: 2px 8px; {font}white-space: pre;"
        )
        self._help_label.setStyleSheet(
            f"color: {t.muted}; padding: 2px 16px 2px 8px; {font}white-space: pre;"
        )

    def _apply_theme(self) -> None:
        """Resolve the session's theme setting and restyle everything."""
        # Resolving "auto" hands the colour scheme back to the platform,
        # which can emit colorSchemeChanged and call back in here.
        if self._applying_theme:
            return
        self._applying_theme = True
        try:
            _theme.apply(self._theme_setting)
            self._style_status_bar()
            self.view.apply_theme()
            self._info_panel.apply_theme()
            self._style_info_button()
            self._shortcut_panel.apply_theme()
            self._update_status()
        finally:
            self._applying_theme = False

    def _style_info_button(self) -> None:
        t = _theme.current()
        self._info_cli_button.setStyleSheet(
            f"QPushButton {{ background: {t.panel}; color: {t.dim}; border: none; "
            "padding: 4px 12px; text-align: left; "
            "font-family: 'Courier New', monospace; font-size: 8pt; }"
            f"QPushButton:hover {{ color: {t.muted}; }}"
        )

    def _on_theme_requested(self, setting: str) -> None:
        if not setting:     # flip between the two
            setting = "light" if _theme.current() is _theme.DARK else "dark"
        self._theme_setting = setting
        self._apply_theme()
        shown = _theme.current().name
        self.view._flash(f"theme {setting}" + (f" ({shown})" if setting == "auto" else ""))

    def _on_system_scheme_changed(self, _scheme) -> None:
        if self._theme_setting == "auto":
            self._apply_theme()

    def _tick_spinner(self):
        self._spinner_frame += 1
        self._update_status()

    def _update_status(self):
        v = self.view
        sb = self.statusBar()
        interactive = (v._cmd is not None or v._sel is not None
                       or v._pattern_choice is not None)
        if interactive:
            self._bar_hide_timer.stop()
            if not sb.isVisible():
                self._bar_auto_shown = True
                sb.show()
        elif self._bar_auto_shown and not self._bar_hide_timer.isActive():
            self._bar_hide_timer.start()
        self._set_help(self._image_count_text())
        self._prompt_map = None

        if v._pattern_choice is not None:
            if self._spinner_timer.isActive():
                self._spinner_timer.stop()
            prompt, details = v.pattern_choice_text()
            self._status_label.setText(prompt)
            self._set_help(details)
            return

        if v._reload_in_progress:
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
            self._status_label.setText(
                f"{_SPINNER[self._spinner_frame % len(_SPINNER)]}  "
                f"{v._busy_label or 'reloading…'}"
            )
            return

        if self._spinner_timer.isActive():
            self._spinner_timer.stop()
            self._spinner_frame = 0

        if v._flash_msg is not None:
            self._status_label.setText(v._flash_msg)
            return

        grid_str = ""
        if v._grid_axis is not None:
            grid_str = f"  grid:{v.axis_names[v._grid_axis]}"
            n_panes = len(v._grid_slots)
            if n_panes > 1:    # say which pane the axis is acting on
                grid_str += f"[pane {v._grid_focus + 1}/{n_panes}"
                hidden = len(v._grid_indices()) - n_panes
                grid_str += f" +{hidden}]" if hidden > 0 else "]"
        mode_str = f"[{v.nav_mode.label}{grid_str}]{'  ●' if v._watching else ''}"

        if v._cmd is not None:
            candidates = v._cmd_candidates()
            query = v._cmd["query"]
            raw = v._cmd["cursor"]
            cursor = raw if raw < 0 else (min(raw, len(candidates) - 1) if candidates else 0)
            prompt_html = None
            if v._cmd["phase"] == "verb":
                prompt = f":{query}▌"
                hlit = candidates[min(cursor, len(candidates) - 1)] if candidates and cursor >= 0 else None
                desc = _COMMAND_HELP.get(hlit, "") if hlit else ""
            else:
                verb = v._cmd["verb"]
                desc = _COMMAND_HELP.get(verb, "")
                help_text = ("  ".join(v._tab_matches) if v._tab_matches
                             else f"( {desc} )" if desc else "")
                caret = v._cmd.get("caret", len(query))
                if "axis" in v._cmd:
                    head = f":{verb} {v._cmd['axis']}  "
                    prompt = head + self._shown_query(head, query, caret, help_text)
                elif verb in _FREE_TEXT_ARGS:
                    head = f":{verb} "
                    shown = self._shown_query(head, query, caret, help_text)
                    prompt = head + shown
                    # Colour each {placeholder} so a multi-axis template stays
                    # readable while it is being typed.
                    prompt_html = _sp(head) + placeholder_html(shown)
                else:
                    prompt = f":{verb} {query}▌"
            share = self._HELP_SHARE_EDITING if self._prompt_map is not None else None
            if v._tab_matches:
                self._set_help("  ".join(v._tab_matches), share)
            elif desc:
                self._set_help(f"( {desc} )", share)
            if candidates:
                cand_html = _candidate_html(candidates, cursor, self._hl_candidates)
                head = prompt_html if prompt_html is not None else _sp(prompt)
                self._status_label.setText(
                    _rich(f"{head}&nbsp;&nbsp;→&nbsp;&nbsp;{cand_html}")
                )
            elif v._cmd["phase"] == "verb" and query:
                self._status_label.setText(
                    _rich(f"{_sp(prompt)}&nbsp;&nbsp;(unknown&nbsp;command)")
                )
            elif v._cmd["phase"] == "arg" and v._cmd["verb"] in _ARG_FORMATS:
                self._status_label.setText(_rich(
                    f"{_sp(prompt)}&nbsp;&nbsp;→&nbsp;&nbsp;{_sp(_ARG_FORMATS[v._cmd['verb']])}"
                ))
            else:
                self._status_label.setText(
                    _rich(prompt_html if prompt_html is not None else _sp(prompt))
                )
            return

        if v._sel is not None:
            phase = v._sel["phase"]
            query = v._sel["query"]
            cursor = v._sel["cursor"]
            candidates = v._sel_candidates()
            if phase == "axis":
                prompt = f"axis? {query}▌"
            else:
                axis_name = v.axis_names[v._sel["axis_idx"]]
                prompt = f"{axis_name}? {query}▌"
            cand_html = (
                _candidate_html(candidates, cursor, self._hl_candidates) if candidates
                else _sp("(no match)")
            )
            self._status_label.setText(_rich(
                f"{_sp(mode_str)}&nbsp;&nbsp;|&nbsp;&nbsp;{_sp(prompt)}"
                f"&nbsp;&nbsp;→&nbsp;&nbsp;{cand_html}"
            ))
            return

        h_idx = v._h_axis()
        v_idx = v._v_axis()

        coord_parts = [
            f"{name}={v.axis_values[i][v.pos[i]]:<{max(len(val) for val in v.axis_values[i])}}"
            for i, name in enumerate(v.axis_names)
        ]
        coord_str = "  ".join(coord_parts)
        h_name = v.axis_names[h_idx]
        v_name = v.axis_names[v_idx] if v_idx is not None else "—"
        bind_str = f"→/← {h_name}  ↑/↓ {v_name}"
        sep = "  |  "
        sp = _sp
        sep_html = "&nbsp;&nbsp;|&nbsp;&nbsp;"
        active = v._active_axis
        coord_html = (
            "&nbsp;&nbsp;".join(
                _hl(sp(p), self._hl) if i == active else sp(p)
                for i, p in enumerate(coord_parts)
            ) if active is not None else None
        )
        if v.nav_mode == NavMode.SEEK:
            key_hints = "  ".join(v.axis_names)
            if coord_html is not None:
                parts_html = [sp(mode_str), coord_html, sp(bind_str)]
                if key_hints:
                    parts_html.append(sp(key_hints))
                self._status_label.setText(sep_html.join(parts_html))
            else:
                parts = [mode_str, coord_str, bind_str]
                if key_hints:
                    parts.append(key_hints)
                self._status_label.setText(sep.join(parts))
        else:
            name_to_ch = {v.axis_names[idx]: ch for ch, idx in v.key_to_axis.items()}
            unbound = [n for n in v.axis_names if n not in name_to_ch]
            bound_items = [
                f"[{ch}] {v.axis_names[v.key_to_axis[ch]]}"
                for ch in sorted(v.key_to_axis)
            ]
            if unbound or coord_html is not None:
                bound_html = "&nbsp;&nbsp;".join(sp(b) for b in bound_items)
                unbound_html = "&nbsp;&nbsp;".join(
                    f'<span style="color:{_theme.current().error}">{sp(n)}</span>'
                    for n in unbound
                )
                key_hints_html = "&nbsp;&nbsp;".join(filter(None, [bound_html, unbound_html]))
                c = coord_html if coord_html is not None else sp(coord_str)
                parts_html = sep_html.join([sp(mode_str), c, sp(bind_str)])
                label = parts_html + sep_html + key_hints_html if key_hints_html else parts_html
                self._status_label.setText(label)
            else:
                key_hints = "  ".join(bound_items)
                parts = [mode_str, coord_str, bind_str]
                if key_hints:
                    parts.append(key_hints)
                self._status_label.setText(sep.join(parts))

    # Share of the status bar the right-hand hint may take, so the prompt
    # on the left keeps most of the room; less while a path or other free
    # text is being edited, which wants all the width it can get.
    _HELP_SHARE = 0.4
    _HELP_SHARE_EDITING = 0.25
    # Horizontal padding of the hint label (see _style_status_bar), and the
    # status-bar width the prompt's text cannot use besides the hint: its
    # own padding, the bar's margins and spacing, the size grip (measured
    # 48px) plus slack for font rounding on other platforms.
    _HELP_PAD_PX = 24
    _BAR_INSET_PX = 64

    def _elided_help(self, text: str, share: float | None = None) -> str:
        if not text:
            return ""
        cap = max(80, int(self.statusBar().width() * (share or self._HELP_SHARE)))
        return self._help_label.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, cap)

    def _set_help(self, text: str, share: float | None = None) -> None:
        """Show *text* on the right of the status bar, cut to its *share*."""
        self._help_label.setText(self._elided_help(text, share))

    def _shown_query(self, head: str, query: str, caret: int, help_text: str) -> str:
        """The part of a free-text *query* that fits after *head*, with the
        caret drawn in; as wide as the status bar allows next to *help_text*."""
        fm = self._status_label.fontMetrics()
        shown_help = self._elided_help(help_text, self._HELP_SHARE_EDITING)
        help_w = self._help_label.fontMetrics().horizontalAdvance(shown_help) + self._HELP_PAD_PX
        budget = self.statusBar().width() - help_w - self._BAR_INSET_PX

        def fits(n: int) -> bool:   # measured, not estimated from a glyph width
            return fm.horizontalAdvance(head + _cmd_query_display(query, caret, n)) <= budget

        n = len(query)
        if not fits(n):
            lo, hi = 1, n          # the most characters that still fit
            while lo < hi:
                mid = (lo + hi + 1) // 2
                lo, hi = (mid, hi) if fits(mid) else (lo, mid - 1)
            n = max(lo, 20)
        start, end = _cmd_query_window(query, caret, n)
        self._prompt_map = (head, query, caret, start, end)
        return _cmd_query_display(query, caret, n)

    def _on_status_clicked(self, x: float) -> None:
        """A click in a free-text argument moves the caret there."""
        m = self._prompt_map
        if m is None or self.view._cmd is None:
            return
        head, query, caret, start, end = m
        fm = self._status_label.fontMetrics()
        ellipsis = "…" if start > 0 else ""

        def x_before(i: int) -> int:   # x of the gap before query[i]
            shown = (query[start:i] if i <= caret
                     else query[start:caret] + "▌" + query[caret:i])
            return fm.horizontalAdvance(head + ellipsis + shown)

        self.view.set_cmd_caret(min(range(start, end + 1), key=lambda i: abs(x_before(i) - x)))

    def _image_count_text(self) -> str:
        """`216 images`, or `210/216 images` while some have no file."""
        v = self.view
        total = _n_combos(v.axis_values)
        loaded = min(total, sum(1 for pm in v.pixmaps.values()
                                if not isinstance(pm, MissingPixmap) and not pm.isNull()))
        noun = "image" if total == 1 else "images"
        return f"{loaded} {noun}" if loaded == total else f"{loaded}/{total} {noun}"

    def showEvent(self, event):
        super().showEvent(event)
        if not self.view._initial_fit_done:
            self.view._initial_fit_done = True
            QTimer.singleShot(0, self.view.fit_image)

    def _on_grid_mode_changed(self, grid_widget):
        if grid_widget is not None:
            self._stack.addWidget(grid_widget)
            self._stack.setCurrentWidget(grid_widget)
            grid_widget.setFocus()
            QTimer.singleShot(0, grid_widget.fit_image)
        else:
            old = self._stack.widget(1) if self._stack.count() > 1 else None
            self._stack.setCurrentWidget(self.view)
            if old is not None:
                self._stack.removeWidget(old)
                old.deleteLater()
            self.view.setFocus()
            QTimer.singleShot(0, self.view.fit_image)

    def _toggle_status_bar(self):
        sb = self.statusBar()
        self._bar_auto_shown = False
        self._bar_hide_timer.stop()
        sb.setVisible(not sb.isVisible())

    def _auto_hide_bar(self):
        self._bar_auto_shown = False
        self.statusBar().hide()

    def _toggle_info_panel(self):
        if self._info_dock.isVisible():
            self._info_dock.hide()
        else:
            self._info_panel.refresh(self.view)
            self._info_dock.show()

    def _refresh_info_panel(self):
        if self._info_dock.isVisible():
            self._info_panel.refresh(self.view)

    def _toggle_shortcut_panel(self):
        if self._shortcut_dock.isVisible():
            self._shortcut_dock.hide()
        else:
            self._shortcut_panel.refresh(self.view)
            self._shortcut_dock.show()

    def _refresh_shortcut_panel(self):
        if self._shortcut_dock.isVisible():
            self._shortcut_panel.refresh(self.view)

    def _warn_keybinding_conflicts(self, *, flash: bool = True) -> bool:
        """Log (and optionally flash) warnings for bindings that shadow navigation."""
        axis_letters = set(self.view.key_to_axis.keys())
        conflicts = _keybinding_conflicts(
            self.view._keybindings, axis_letters, self.view._swap_modifiers)
        for msg in conflicts:
            log.warning("Keybinding conflict: %s", msg)
        if flash and conflicts:
            if len(conflicts) == 1:
                self.view._flash(f"keybinding conflict: {conflicts[0]}", ms=4000)
            else:
                self.view._flash(
                    f"{len(conflicts)} keybinding conflict(s) — see log", ms=4000
                )
        return bool(conflicts)

    def _on_settings_changed(self, path: str):
        # Some editors save via a temp-rename; re-add so we keep getting events.
        if not self._settings_watcher.files():
            self._settings_watcher.addPath(str(SETTINGS_PATH))
        settings = load_settings(SETTINGS_PATH)
        self.view.apply_settings(settings)
        if settings.theme != self._theme_file_setting:
            self._theme_file_setting = self._theme_setting = settings.theme
            self._apply_theme()
        self._hl = settings.highlight
        self._hl_candidates = settings.highlight_candidates
        self._info_panel.set_highlight(self._hl)
        self._info_panel.set_value_list_style(
            settings.sidebar_separator, settings.sidebar_list_larger_than
        )
        self._refresh_info_panel()
        self._refresh_shortcut_panel()
        self._update_status()
        reset_placeholder_shapes()  # {date} & friends may have been re-configured
        import juxt.detect as _detect
        _detect._MAX_VALS = settings.max_vals
        _detect._MAX_VALS_DISPLAY = settings.max_vals_display
        log.info("Settings reloaded from %s", path)
        had_conflicts = self._warn_keybinding_conflicts()
        if not had_conflicts:
            self.view._flash("settings reloaded")
