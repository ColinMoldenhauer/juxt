from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

SETTINGS_PATH = Path.home() / ".juxt" / "settings.yaml"

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
