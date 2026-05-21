# Plot Comparison Tool

A fast, reactive desktop tool for visually comparing congruent plots across multiple parameter axes (sensor, date, overpass, source, model, etc.) by cycling through them in an image viewer.

## Problem

When producing plots for different parameter sets, the easiest way to spot visual differences is to flip through congruent images rapidly in a fast image viewer. Standard viewers only support 1D navigation (left/right), but the parameter space is N-dimensional. A custom tool is needed to define each axis and navigate the resulting hypercube.

## Stack

- **Language**: Python 3
- **GUI**: PySide6 (Qt) — fast, cross-platform (Linux + Windows), looks native
- **Image canvas**: `QGraphicsView` + `QGraphicsPixmapItem` — free pan (drag) and zoom (scroll wheel) with minimal setup
- **Config**: YAML (via PyYAML)
- **Image strategy**: preload all pixmaps into a dict keyed by coordinate tuple at startup (target use case: ~100 plots, ~2–5 MB each decoded — trivial RAM)
- **Packaging**: TBD — either single-file script or proper package with `pyproject.toml`

### Stacks considered and rejected

- **Pure bash / `feh` / `sxiv`**: blazing fast but locked to 1D navigation
- **Tkinter**: cross-platform but sluggish image rendering and dated look
- **Flask + JS web app**: overkill, adds latency for local files
- **Tauri / Electron**: too heavy for an image viewer
- **Rust + egui / iced**: snappier and ships as a single binary, but 5–10× the dev time and rewrites Python tooling already in place

## Config format

Two modes, both ultimately produce the same internal representation (template + axes dict).

### Template mode (primary)

```yaml
template: "plots/{sensor}_{date}_{overpass}_{source}.png"
axes:
  sensor: [ASCAT, SMAP, SMOS]
  date: [2024-03-15, 2024-03-16]
  overpass: [AM, PM]
  source: [L2, L3]
```

### Auto-discover mode (convenience)

```yaml
discover:
  directory: plots/
  separator: "_"
```

The tool scans filenames, splits on the separator, aligns by position, and any column where values vary becomes an axis. On first run it dumps a discovered config so the user can rename `axis_0` → `sensor`, etc. Caveat: only works for cleanly and consistently named filenames.

## Navigation design

Three navigation modes are available. Switch at runtime with `:mode tap|seek|pin` (or `0|1|2`) in the config file.

### Universal keys (all modes)

| Key | Action |
|---|---|
| ←/→ | Cycle the horizontal axis (most-recently-focused) |
| ↑/↓ | Cycle the vertical axis (second-most-recently-focused) |
| Spacebar | Toggle between current and previous position — the killer comparison feature |
| Home/End | Jump to first/last value along the active (horizontal) axis |
| 1–9 | Jump to the Nth value along the active axis |
| double-click | Fit image to window |
| `0` | Reset zoom to 100% |
| `:` | Open command mode (vim-style) |
| Ctrl+Shift+H | Toggle status bar |
| Enter | Toggle fullscreen |
| Escape | Exit fullscreen / cancel command |

### Command mode

Triggered by `:`. Type a command and press Enter to execute; Escape or Backspace-to-empty cancels. The status bar shows the command being typed and matching candidates as a prefix-filtered hint list.

| Command | Action |
|---|---|
| `:q` | Quit the application |
| `:fit` | Fit image to window (both dimensions) |
| `:fit height` / `:fit-height` | Fit image height to viewport |
| `:fit width` / `:fit-width` | Fit image width to viewport |
| `:zoom N` | Set zoom to N% (e.g. `:zoom 50`, `:zoom 200`) |
| `:fullscreen` | Toggle fullscreen |
| `:mode tap\|seek\|pin` | Switch navigation mode |
| `:switch-last` | Toggle between current and previous position (same as Spacebar) |

### tap (default)

Each axis key navigates directly without first shifting focus. Lower = forward, upper = backward. Suited to workflows that cycle through many axes individually without heavy use of the arrow keys.

| Key | Action |
|---|---|
| Lowercase letter (e.g. `s`) | Navigate +1 on that axis and focus it |
| Uppercase letter (e.g. `S`) | Navigate −1 on that axis and focus it |
| Ctrl+Letter | Open interactive value picker for that axis |

Arrow keys still work and navigate the most-recently-focused axis.

### seek

Every alpha key triggers an incremental prefix search. Good when there are many axes or values and memorising per-axis letter bindings isn't worth it.

| Key | Action |
|---|---|
| Letter | Begin axis search with that letter as the initial query |
| Letter (during search) | Narrow the candidate list; auto-confirms if unique match |
| Enter | Confirm first candidate explicitly |
| Backspace | Delete last query character; on empty value query, steps back to axis search |
| Escape | Cancel selection |

After an axis is confirmed, the search transitions immediately to value selection on that axis using the same greedy prefix matching. **Greedy auto-confirm**: when exactly one candidate remains, it is selected automatically without requiring Enter.

### pin

Optimised for rapid two-axis flipping. Letter keys shift focus; the two most-recently-focused axes bind to the arrow directions. No modifier keys needed for the common path.

| Key | Action |
|---|---|
| Letter (e.g. `s`=sensor) | Focus that axis; most recent → ←/→, previous → ↑/↓ |
| Ctrl+Letter | Open interactive value picker for that axis |

### Status bar

Bottom bar. Shows current mode, coordinates (e.g. `sensor=ASCAT  date=2024-03-15`), axis→arrow bindings, and letter key assignments. While value picker or seek is active, it shows the current query and matching candidates. Toggleable with Ctrl+Shift+H.

### Navigation design notes

- **Arrow keys** work identically across all modes, always navigating the top two entries in the focus stack. In tap mode, letter-key navigation also updates the focus stack as a side-effect.
- **Uppercase/lowercase for forward/backward (tap)**: Shift adds friction for sustained rapid cycling (~50 presses), so pin mode keeps letter keys modifier-free and delegates direction to the arrow keys. Tap makes the trade-off explicit: one keypress per step per axis at the cost of Shift for reverse.
- **Incremental value picker** (Ctrl+letter in tap/pin; every letter in seek): uses case-insensitive prefix matching. Typing narrows candidates; unique match auto-confirms.

## Implementation plan

A working prototype would include:

1. Config loader supporting both template and auto-discover modes
2. Pixmap preloader with progress feedback (~100 images at startup)
3. `QGraphicsView`-based viewer implementing the navigation scheme above
4. Status overlay
5. Sample config + synthetic test PNGs for immediate testing

Launch: `juxt config.yaml`

## Open questions

- Single-file script vs. proper package with `pyproject.toml`?
- LRU cache + neighbor preloading is *not* needed for the ~100-plot use case but would be the path forward if the tool ever needs to scale to thousands of plots.
