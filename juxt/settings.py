from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

SETTINGS_PATH = Path.home() / ".juxt" / "settings.yaml"

# ── highlight format ──────────────────────────────────────────────────────────

DEFAULT_HIGHLIGHT = "#6af:{}"
DEFAULT_HIGHLIGHT_CANDIDATES = "#6af:[{}]"

_STYLE_FLAGS = ("bold", "italic", "underline")
_RAW_MARKERS = ("html", "raw")
_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_COLOR_NAME_RE = re.compile(r"^[a-zA-Z]+$")
_SPEC_RE = re.compile(r"^(?:(?P<style>[^:{}]*):)?(?P<template>.*\{\}.*)$", re.S)


@dataclass(frozen=True)
class Highlight:
    """Parsed `[style:]template` highlight spec; `{}` stands for the value."""

    spec: str = DEFAULT_HIGHLIGHT
    color: str | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    prefix: str = ""
    suffix: str = ""
    raw: str | None = None   # verbatim rich-text template (escape hatch)


def _parse_highlight(spec: str) -> Highlight | None:
    """Parse one spec, or return None if it is malformed."""
    if "{}" not in spec:
        return None
    m = _SPEC_RE.match(spec)
    if m is None:
        return None
    style, template = m.group("style"), m.group("template")
    tokens = (style or "").split()
    if any(t.lower() in _RAW_MARKERS for t in tokens):
        # "html:" marks the rest as rich text to be used verbatim.
        return Highlight(spec=spec, raw=template)
    color: str | None = None
    flags: set[str] = set()
    for tok in tokens:
        low = tok.lower()
        if low in _STYLE_FLAGS:
            flags.add(low)
        elif _HEX_RE.match(tok) or _COLOR_NAME_RE.match(tok):
            color = color or tok
        else:
            # Not a style at all: the colon belonged to the template.
            log.warning("Unrecognised highlight style %r in %r; "
                        "treating the whole spec as a template", tok, spec)
            style, template, color, flags = "", spec, None, set()
            break
    prefix, suffix = template.split("{}", 1)
    return Highlight(
        spec=spec, color=color,
        bold="bold" in flags, italic="italic" in flags,
        underline="underline" in flags,
        prefix=prefix, suffix=suffix,
    )


def parse_highlight(spec: str, fallback: str = DEFAULT_HIGHLIGHT) -> Highlight:
    """Parse a highlight spec, falling back to *fallback* when it is malformed."""
    hl = _parse_highlight(str(spec))
    if hl is None:
        log.warning("Invalid highlight format %r (missing {} placeholder); "
                    "using %r instead", spec, fallback)
        hl = _parse_highlight(fallback) or Highlight()
    return hl

def _render_placeholders_section() -> str:
    """The placeholders block, written from the shapes juxt ships with.

    The defaults live in the user's settings file rather than in the code, so
    they can be seen and changed; this keeps the two from drifting apart.
    """
    from .complete import BUILTIN_SHAPES

    lines = []
    for name, values in BUILTIN_SHAPES.items():
        rendered = ", ".join(values)
        lines.append(f"  {name}: [{rendered}]" if len(values) > 1
                     else f"  {name}: {rendered}")
    lines.append("  # orbit: 'o\\d{5}'      # a regular expression works too")
    return _PLACEHOLDERS_HEADER + "\n".join(lines) + "\n"


_PLACEHOLDERS_HEADER = """
# Placeholder value shapes used by Tab completion.
# A placeholder whose name is listed here is completed only as far as the value
# it stands for: with `date` known, `plots/{date}<TAB>` stops at the end of the
# date (e.g. `{date}_`) instead of swallowing whatever the filenames share
# after it.  Only what stands here counts, so an entry you delete stops
# applying.
# A value is either a date-style shorthand (yyyy yy mm dd hh ss and
# separators), a regular expression, or a list of alternatives.
placeholders:
"""

_PLACEHOLDERS_SECTION = _render_placeholders_section()


_TEMPLATE = """\
# juxt user settings
# Edit this file and restart juxt for changes to take effect.
# All fields are optional; omitted fields fall back to the defaults shown here.

# Seek / value-picker behaviour
seek:
  greedy: true      # auto-confirm when exactly one candidate remains
                    # set to false to always require Enter

# Display options used in the axis-detection dialogue
display:
  max_vals: 3       # max values shown inline in the path preview  (e.g. {AM|PM|…})
  max_vals_display: 10  # max values listed per axis in the naming dialogue

# How the current selection is highlighted.  One format string per context:
#
#     [style:]template        {} is replaced by the value
#
#   style     a colour (#6af, #66aaff or a CSS colour name) plus any of
#             bold  italic  underline, separated by spaces.  Omit the whole
#             "style:" part to leave the colour untouched.
#   template  free text around {}, e.g. "{}"  "[{}]"  "» {} «"
#
# Delimiters are inserted literally, so "<{}>" shows angle brackets.  For
# anything the two fields above cannot express, prefix the spec with "html:"
# and write rich text yourself:
#     'html:<span style="background:#334">[{}]</span>'
# Invalid specs are logged and ignored.
highlight:
  selected: "#6af:{}"       # current value in the info sidebar / active axis
  candidates: "#6af:[{}]"   # highlighted entry in status-bar candidate lists

# Key bindings — map a key chord to an action name.
# Action names match any :command (fit, zoom, fullscreen, reload, …) plus the
# two UI toggles: toggle-statusbar  toggle-info
# Modifiers: Ctrl  Shift  Alt  Meta
# Keys: any letter, Return, Escape, Space, Tab, Backspace, Delete,
#       Home, End, Left, Right, Up, Down, F1–F12, 0–9
#
# Conflict warning: bare letters (e.g. H) shadow seek mode for all letters,
# and tap/pin mode for any letter that is an axis key.  Ctrl+letter and
# Shift+letter axis keys shadow tap/pin in the same way.
# juxt logs a warning and flashes a notice when conflicts are detected.
keybindings:
  Ctrl+Shift+H: toggle-statusbar   # toggle the status bar
  Ctrl+Shift+I: toggle-info        # toggle the info sidebar
  Ctrl+Shift+G: grid-dialog        # open the grid builder dialogue
  # F11: fullscreen                # example: bind F11 to fullscreen
""" + _PLACEHOLDERS_SECTION

_SECTION_TEMPLATES: dict[str, str] = {
    "seek": """
# Seek / value-picker behaviour
seek:
  greedy: true      # auto-confirm when exactly one candidate remains (false = require Enter)
""",
    "display": """
# Display options used in the axis-detection dialogue
display:
  max_vals: 3       # max values shown inline in the path preview  (e.g. {AM|PM|…})
  max_vals_display: 10  # max values listed per axis in the naming dialogue
""",
    "highlight": """
# How the current selection is highlighted.  One format string per context:
#     [style:]template        {} is replaced by the value
#   style     a colour (#6af or a CSS name) plus any of bold italic underline
#   template  free text around {}, e.g. "{}"  "[{}]"  "» {} «"
# Prefix with "html:" to write the rich text yourself, e.g.
#     'html:<span style="background:#334">[{}]</span>' 
highlight:
  selected: "#6af:{}"       # current value in the info sidebar / active axis
  candidates: "#6af:[{}]"   # highlighted entry in status-bar candidate lists
""",
    "placeholders": _PLACEHOLDERS_SECTION,
    "keybindings": """
# Key bindings — map a key chord to an action name.
# Action names match any :command plus: toggle-statusbar  toggle-info
# Modifiers: Ctrl  Shift  Alt  Meta
# Keys: any letter, Return, Escape, Space, Tab, Backspace, Delete,
#       Home, End, Left, Right, Up, Down, F1–F12, 0–9
# Warning: bare letters shadow seek mode; bare/Ctrl/Shift+letter axis keys
# also shadow tap/pin mode.  juxt warns on startup and settings reload.
keybindings:
  Ctrl+Shift+H: toggle-statusbar
  Ctrl+Shift+I: toggle-info
  Ctrl+Shift+G: grid-dialog
  # F11: fullscreen
""",
}

_EXPECTED_SECTIONS = set(_SECTION_TEMPLATES)

_DEFAULT_KEYBINDINGS: dict[str, str] = {
    "Ctrl+Shift+H": "toggle-statusbar",
    "Ctrl+Shift+I": "toggle-info",
    "Ctrl+Shift+G": "grid-dialog",
}


@dataclass
class Settings:
    seek_greedy: bool = True
    max_vals: int = 3
    max_vals_display: int = 10
    highlight: Highlight = field(
        default_factory=lambda: parse_highlight(DEFAULT_HIGHLIGHT)
    )
    highlight_candidates: Highlight = field(
        default_factory=lambda: parse_highlight(DEFAULT_HIGHLIGHT_CANDIDATES)
    )
    keybindings: dict[str, str] = field(
        default_factory=lambda: dict(_DEFAULT_KEYBINDINGS)
    )
    placeholders: dict[str, object] = field(default_factory=dict)


def _write_missing_sections(path: Path, missing: set[str]) -> None:
    """Append commented blocks for any missing sections to an existing file."""
    additions = "".join(_SECTION_TEMPLATES[s] for s in sorted(missing))
    with open(path, "a", encoding="utf-8") as f:
        f.write(additions)
    log.info("Added missing settings sections to %s: %s", path, sorted(missing))


def load_settings(path: Path = SETTINGS_PATH, write: bool = True) -> Settings:
    """Read the user settings.

    With *write* false the file is only read, never created or extended, which
    is what the shell completion helper wants: it runs on every Tab press.
    """
    if not path.exists():
        if write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_TEMPLATE, encoding="utf-8")
            log.info("Created default settings file at %s", path)
        return Settings()
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        s = Settings()
        seek = data.get("seek") or {}
        if isinstance(seek, dict) and "greedy" in seek:
            s.seek_greedy = bool(seek["greedy"])
        display = data.get("display") or {}
        if isinstance(display, dict):
            if "max_vals" in display:
                s.max_vals = int(display["max_vals"])
            if "max_vals_display" in display:
                s.max_vals_display = int(display["max_vals_display"])
        hl = data.get("highlight")
        if isinstance(hl, str):          # one string sets both contexts
            s.highlight = parse_highlight(hl)
            s.highlight_candidates = parse_highlight(hl)
        elif isinstance(hl, dict):
            if hl.get("selected") is not None:
                s.highlight = parse_highlight(hl["selected"], DEFAULT_HIGHLIGHT)
            if hl.get("candidates") is not None:
                s.highlight_candidates = parse_highlight(
                    hl["candidates"], DEFAULT_HIGHLIGHT_CANDIDATES
                )
        kb = data.get("keybindings") or {}
        if isinstance(kb, dict):
            s.keybindings = {**_DEFAULT_KEYBINDINGS, **{str(k): str(v) for k, v in kb.items()}}
        ph = data.get("placeholders") or {}
        if isinstance(ph, dict):
            # Values stay as written: a shorthand, a regex, or a list of either.
            s.placeholders = {str(k): v for k, v in ph.items()}
        log.debug("Loaded settings from %s: %s", path, s)
        missing = _EXPECTED_SECTIONS - data.keys()
        if write and missing:
            _write_missing_sections(path, missing)
        return s
    except Exception as e:
        log.warning("Could not load settings from %s: %s", path, e)
        return Settings()


def ensure_settings_file(path: Path = SETTINGS_PATH) -> Path:
    """Ensure the settings file exists, creating it with defaults if needed."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_TEMPLATE, encoding="utf-8")
        log.info("Created default settings file at %s", path)
    return path
