"""Dark and light colour themes.

Every colour juxt paints itself comes from the active `Theme`, so the viewer,
sidebars, status bar and dialogues switch together.  The module imports no Qt
at load time: `complete.py` reads the placeholder colours from here, and the
shell completion helper imports that on every Tab press.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

THEME_SETTINGS = ("auto", "dark", "light")

# The colour word a highlight spec uses for "the theme's accent", so the same
# setting reads well on both backgrounds.
ACCENT = "accent"


@dataclass(frozen=True)
class Theme:
    name: str
    canvas: str          # behind the image, and behind each grid cell
    panel: str           # sidebar background
    text: str            # body text in the sidebars and status bar
    surface: str         # status bar and dialogue background
    control: str         # buttons and spin-box steps
    hover: str           # a control under the mouse
    border: str
    border_strong: str   # checkbox and button outlines
    muted: str           # hints next to the main text
    disabled: str        # text of a disabled control
    dim: str             # sidebar values with no file behind them
    header: str          # section headers in the sidebars
    accent: str          # anything interactive or selected
    selection: str       # background of selected list entries
    selection_text: str
    arrow: str           # spin-box step arrows
    error: str           # axes with no key in the status bar
    placeholders: tuple[str, ...]  # one colour per `{placeholder}`, cycled


DARK = Theme(
    name="dark",
    canvas="#191919",
    panel="#111111",
    text="#dddddd",
    surface="#1a1a1a",
    control="#222222",
    hover="#2c2c2c",
    border="#333333",
    border_strong="#555555",
    muted="#888888",
    disabled="#666666",
    dim="#555555",
    header="#d8a657",
    accent="#66aaff",
    selection="#2a4a6a",
    selection_text="#ffffff",
    arrow="#cccccc",
    error="#cc4444",
    placeholders=("#e8913a", "#4fc3f7", "#9ccc65", "#ce93d8", "#ffd54f"),
)

LIGHT = Theme(
    name="light",
    canvas="#e6e6e6",
    panel="#fafafa",
    text="#222222",
    surface="#f0f0f0",
    control="#e2e2e2",
    hover="#d6d6d6",
    border="#c4c4c4",
    border_strong="#8a8a8a",
    muted="#666666",
    disabled="#9a9a9a",
    dim="#a8a8a8",
    header="#8a5a00",
    accent="#1a5fb4",
    selection="#c5d9f3",
    selection_text="#000000",
    arrow="#444444",
    error="#c01c28",
    placeholders=("#b35400", "#0068a0", "#3f7a1c", "#8e24aa", "#8a6d00"),
)

THEMES = {t.name: t for t in (DARK, LIGHT)}

_current: Theme = DARK


def current() -> Theme:
    """The theme everything is painted with right now."""
    return _current


def set_current(theme: Theme) -> None:
    global _current
    _current = theme


def parse_theme_setting(value: object) -> str:
    """Normalise a `theme` setting to one of `THEME_SETTINGS`."""
    word = str(value).strip().lower()
    if word in THEME_SETTINGS:
        return word
    log.warning("Invalid theme %r (expected auto, dark or light); using auto", value)
    return "auto"


def resolve_color(color: str | None) -> str | None:
    """A highlight colour as CSS: the accent keyword becomes the theme's accent."""
    if color is not None and color.lower() == ACCENT:
        return _current.accent
    return color


# ── Qt glue ───────────────────────────────────────────────────────────────────

def _system_is_dark() -> bool:
    """Whether the platform reports a dark colour scheme.  A platform that
    reports none (plain X11, X forwarding over SSH) gets juxt's look from
    before themes existed: dark.  Its palette is no guide there, as it is
    only Qt's built-in light default."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    hints = QGuiApplication.styleHints()
    if not hasattr(hints, "colorScheme"):     # Qt < 6.5
        return True
    return hints.colorScheme() != Qt.ColorScheme.Light


def _palette(t: Theme):
    """A full application palette for *t*, so native widgets (dock titles,
    scroll bars, menus, tooltips, the startup window) match what juxt paints."""
    from PySide6.QtGui import QColor, QPalette

    R, G = QPalette.ColorRole, QPalette.ColorGroup
    p = QPalette()
    for role, color in (
        (R.Window, t.surface), (R.WindowText, t.text),
        (R.Base, t.panel), (R.AlternateBase, t.control),
        (R.Text, t.text), (R.Button, t.control), (R.ButtonText, t.text),
        (R.BrightText, t.selection_text),
        (R.Highlight, t.selection), (R.HighlightedText, t.selection_text),
        (R.ToolTipBase, t.surface), (R.ToolTipText, t.text),
        (R.PlaceholderText, t.muted),
        (R.Link, t.accent), (R.LinkVisited, t.accent),
        (R.Light, t.hover), (R.Midlight, t.control), (R.Mid, t.border),
        (R.Dark, t.border_strong), (R.Shadow, "#000000"),
    ):
        p.setColor(role, QColor(color))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G.Disabled, role, QColor(t.disabled))
    return p


def apply(setting: str) -> Theme:
    """Resolve *setting* ("auto", "dark" or "light"), make the result current
    and theme the whole application with it.  Needs a QApplication."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    hints = app.styleHints()
    # Qt 6.8+ can also switch the native style's own drawing (Windows, macOS).
    # Auto hands the choice back to the platform first: an explicit request
    # would both mask the system scheme read below and stop Qt reporting
    # the system's own changes.
    can_request = hasattr(hints, "setColorScheme")
    if setting == "auto":
        if can_request:
            hints.setColorScheme(Qt.ColorScheme.Unknown)
        theme = DARK if _system_is_dark() else LIGHT
    else:
        theme = THEMES[setting]
        if can_request:
            hints.setColorScheme(
                Qt.ColorScheme.Dark if theme is DARK else Qt.ColorScheme.Light
            )
    app.setPalette(_palette(theme))
    set_current(theme)
    return theme
