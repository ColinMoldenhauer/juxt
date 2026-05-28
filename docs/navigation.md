# Navigation

juxt has three navigation modes and a command mode. All modes share the same core key bindings; the modes differ only in how letter keys are interpreted.

Switch modes at runtime with `:mode tap|seek|pin`, or set a default in the [config file](configuration.md#navigation-mode).

---

## Universal keys (all modes)

These bindings work identically regardless of which mode is active.

### Image navigation

| Key | Action |
|---|---|
| `←` / `→` | Cycle the primary axis (most recently focused) |
| `↑` / `↓` | Cycle the secondary axis (second most recently focused) |
| Scroll wheel | Step forward / backward on the primary axis |
| `Shift` + scroll wheel | Step forward / backward on the **secondary** axis |
| `Space` | Toggle between current and previous position — the main comparison tool |
| `Home` / `End` | Jump to first / last value on the primary axis |
| `1` – `9` | Jump to the Nth value on the primary axis |

### View controls

| Key | Action |
|---|---|
| double-click | Fit image to window |
| `0` | Reset zoom to 100% |
| `Ctrl` + scroll wheel | Zoom in/out, anchored under the cursor |
| Click + drag | Pan the image |
| `Enter` | Toggle fullscreen |
| `Ctrl+Shift+H` | Toggle the status bar |
| `Ctrl+Shift+I` | Toggle the info sidebar |

### Other

| Key | Action |
|---|---|
| `:` | Open command mode |
| `Escape` | Exit fullscreen or cancel an active selection / command |

---

## The focus stack

Arrow keys always navigate the two most recently focused axes, regardless of mode. The focus stack tracks which axes have been touched most recently:

- **`←` / `→`** — navigate `focus_stack[0]` (the most recently focused axis)
- **`↑` / `↓`** — navigate `focus_stack[1]` (the second most recently focused axis)

Any action that selects or navigates an axis promotes it to the top of the focus stack.

---

## tap (default)

Each axis is assigned one letter key. Tap it to step forward (+1); hold Shift to step backward (−1). Every navigation is a single keypress — no prefix, no menu, no mode to enter first.

| Key | Action |
|---|---|
| Lowercase letter (e.g. `s`) | Navigate +1 on that axis and focus it |
| Uppercase letter (e.g. `S`) | Navigate −1 on that axis and focus it |
| `Ctrl` + letter | Open the value picker for that axis |

Arrow keys still work and navigate the top two entries in the focus stack, as in all modes.

**Trade-off vs. pin:** every step costs one keypress, but Shift is needed for the reverse direction. Pin removes the Shift cost for sustained cycling at the expense of an explicit focus step before navigating.

---

## seek

Every letter key starts an incremental prefix search, first for an axis name, then for a value on that axis. Good when there are many axes or you don't want to memorise per-axis letter bindings.

| Key | Action |
|---|---|
| Letter | Begin axis search with that letter as the initial query |
| Letter (during search) | Narrow the candidate list |
| `Enter` | Confirm the first candidate explicitly |
| `Backspace` | Delete the last query character; on an empty value query, step back to axis search |
| `Escape` | Cancel the search |

**Greedy auto-confirm:** when exactly one candidate remains, it is selected automatically without pressing Enter.

**Phase transition:** after confirming an axis, the mode transitions immediately into value selection on that axis using the same prefix matching. Backspace on an empty value query returns to axis selection.

---

## pin

Optimised for comparing two axes at a time with no modifier keys in the hot path.

| Key | Action |
|---|---|
| Letter (e.g. `s` for `sensor`) | Focus that axis; it becomes `←`/`→`, previous primary becomes `↑`/`↓` |
| `Ctrl` + letter | Open the value picker for that axis |

Letter keys only shift focus — they do not advance the axis. Use arrow keys after focusing to cycle through values. This keeps sustained rapid cycling (dozens of presses) free of modifier keys.

**Example workflow:** press `s` to put `sensor` on `←`/`→`, press `d` to put `date` on `←`/`↓` (sensor shifts to `↑`/`↓`), then use arrow keys freely to compare any combination.

---

## Value picker

`Ctrl` + a letter key opens an incremental value picker for that axis in any mode. Type a prefix to filter the list; the match is case-insensitive. When exactly one candidate remains it is confirmed automatically; press `Enter` to confirm the first candidate explicitly at any point; press `Escape` to cancel.

---

## Command mode

Press `:` to open command mode. The status bar shows your input and a prefix-filtered list of matching commands as you type; a short description of the highlighted command is shown right-aligned. Press `Enter` to execute, `Escape` or `Backspace`-to-empty to cancel.

| Command | Action |
|---|---|
| `:q` / `:quit` | Quit the application |
| `:fit` | Fit image to window (both dimensions) |
| `:fit-height` | Fit image height to the viewport |
| `:fit-width` | Fit image width to the viewport |
| `:zoom N` | Set zoom to N% (e.g. `:zoom 50`, `:zoom 200`) |
| `:fullscreen` | Toggle fullscreen |
| `:mode tap` | Switch to tap mode |
| `:mode seek` | Switch to seek mode |
| `:mode pin` | Switch to pin mode |
| `:switch-last` | Toggle between current and previous position (same as `Space`) |
| `:axis-h NAME` | Lock `←`/`→` to a named axis |
| `:axis-v NAME` | Lock `↑`/`↓` to a named axis |
| `:axis-auto` | Restore dynamic axis-to-arrow assignment |
| `:swap-axes` | Swap the `←`/`→` and `↑`/`↓` axis bindings |
| `:grid [AXIS]` | Expand an axis into a tiled grid of independent viewports (defaults to the active axis) |
| `:grid AXIS VAL …` | Same, but show only the listed values |
| `:grid AXIS NxM` | Same, with an explicit layout (e.g. `:grid sensor 2x2`) |
| `:grid AXIS VAL … NxM` | Values and explicit layout combined |
| `:grid-layout NxM` | Change the layout of the current grid without exiting (e.g. `:grid-layout 1x3`) |
| `:ungrid` | Return to single-image view |
| `:grid-sharex [on\|off]` | Toggle or set synchronized horizontal pan/zoom across cells (default: on) |
| `:grid-sharey [on\|off]` | Toggle or set synchronized vertical pan/zoom across cells (default: on) |
| `:reload` | Re-detect axes and reload images from the current source |
| `:copy-path` | Copy the current image's file path to the clipboard |
| `:copy-image` | Copy the current image to the clipboard |
| `:write [PATH]` / `:w [PATH]` | Write the current config to a YAML file; opens a file dialog if PATH is omitted |
| `:pattern PATH` | Change the template / source path without restarting (see below) |
| `:watch` / `:watch true` | Enable live file watching / polling |
| `:watch false` | Disable live file watching / polling |
| `:watch N` | Set remote poll interval to N seconds |
| `:info` | Toggle the info sidebar |

### :pattern — changing the source at runtime

`:pattern` accepts the same forms as the CLI `PATH` argument: a local template with `{placeholders}`, a local directory, a YAML config file, or a remote SSH path. The argument is pre-filled with the current template so you can edit it in place. A progress dialog blocks the window while images are loaded or downloaded.

Free-text arguments (`:pattern`, `:write`) support full cursor editing: `←`/`→` move the caret, `Home`/`End` jump to the ends, `Delete` removes the character at the caret. **`Tab`** completes the path: local paths use the filesystem; remote paths use the existing SFTP connection when the typed host matches the current session.

---

## Grid view

Grid view tiles all (or a subset of) values from one axis into a grid of independent viewports, letting you compare them side by side without flipping.

```
:grid sensor              # show all sensor values in a grid
:grid sensor SMAP SMOS    # show only SMAP and SMOS
:grid sensor 2x2          # explicit 2×2 layout
:grid sensor SMAP SMOS 1x2
:ungrid                   # back to single-image view
```

The layout is chosen automatically to best match the viewport's aspect ratio given the image dimensions; use `NxM` to override.

### :grid — interactive completion

`:grid` supports interactive argument completion. After pressing `Enter` to confirm the verb, the status bar shows axis name candidates:

```
:grid ▌  →  [sensor]  date  overpass  level
```

Use `←`/`→` to cycle candidates or type a prefix to narrow them. Press **`Tab`** to accept the highlighted axis and move to value selection:

```
:grid sensor ▌  →  [SMAP]  SMOS  ASCAT
```

Press **`Tab`** again on each value to append it to the filter list. Press **`Enter`** to execute with the current selection — or just type the arguments manually if you know them.

**Navigation in grid mode** works the same as normal: arrow keys and letter keys advance all other axes simultaneously — every cell updates together.

**Pan and zoom** are synchronized across all cells by default. Each cell is an independent `QGraphicsView`, so you can also pan/drag within a single cell independently when sync is off.

```
:grid-sharex off    # allow independent horizontal pan/zoom per cell
:grid-sharey off    # allow independent vertical pan/zoom per cell
```

**CLI flags** — open directly in grid view:

```bash
juxt "plots/{sensor}_{date}.png" --grid sensor
juxt "plots/{sensor}_{date}.png" --grid sensor --grid-values SMAP SMOS
juxt "plots/{sensor}_{date}.png" --grid sensor --grid-layout 1x3
juxt "plots/{sensor}_{date}.png" --grid sensor --no-sharex --no-sharey
```

The status bar shows the active grid axis in the mode indicator, e.g. `[tap  grid:sensor]`.

---

## Status bar

The status bar at the bottom shows:

- Current navigation mode — `[tap]`, `[seek]`, or `[pin]`; a `●` is appended outside the brackets when live file watching is active (e.g. `[tap]  ●`)
- Current axis values (e.g. `sensor=ASCAT  date=2024-03-15  overpass=AM  source=L2`)
- Which axes are bound to `←`/`→` and `↑`/`↓`
- Letter-to-axis assignments; axes with no available letter binding are shown in red
- Active query and candidate list when value picker or seek is active
- Command being typed in command mode; description of the highlighted command shown right-aligned

Toggle the status bar with `Ctrl+Shift+H`. Toggle the info sidebar with `Ctrl+Shift+I` or `:info`; it docks to the right side and shows the current image path and all axis values with the current value highlighted.

### Custom keybindings

Any action (including every `:command` plus `toggle-statusbar` and `toggle-info`) can be bound to a key chord in `~/.juxt/settings.yaml`:

```yaml
keybindings:
  Ctrl+Shift+H: toggle-statusbar
  F11: fullscreen
  Ctrl+R: reload
```

**Conflict warning.** Navigation keys are mode-specific:

| Chord type | Conflicts with |
|---|---|
| Bare letter (e.g. `H`) | seek mode always (any letter starts a search); tap + pin if the letter is an axis key |
| `Shift+letter` axis key | tap mode (uppercase = navigate −1) |
| `Ctrl+letter` axis key | tap + pin mode (opens value picker) |

When juxt detects a conflict it logs a warning and flashes a notice in the status bar — both at startup and whenever the settings file is saved. The binding still takes effect; the warning is advisory.
