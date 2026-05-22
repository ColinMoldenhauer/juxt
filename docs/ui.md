# UI overview

A quick tour of the juxt interface and its interactive elements.

---

## Main window

![Main window with callouts](assets/screenshots/main-window-annotated.png)

| # | Element | Description |
|---|---|---|
| 1 | **Image viewport** | Displays the current image. Drag to pan, scroll to zoom, double-click to fit. |
| 2 | **Status bar** | One-line summary of navigation state. Toggleable with `Ctrl+Shift+H`; auto-appears while command or value-picker is active. |
| 3 | **Mode indicator** | `[tap]`, `[seek]`, or `[pin]` — the active navigation mode. A `●` is appended when live file-watching is active. |
| 4 | **Axis values** | Current position in the hypercube, e.g. `sensor=ASCAT  date=2024-03-16`. |
| 5 | **Arrow bindings** | Which axes are mapped to `←/→` and `↑/↓`, updated as you focus different axes. |
| 6 | **Letter assignments** | Which letter key navigates which axis. Axes without an available letter are shown in red. |

---

## Status bar states

The status bar content changes to reflect the active interaction.

### Normal

![Status bar — normal state](assets/screenshots/statusbar-normal.png)

Reading left to right: mode + watch indicator → current axis values → arrow-key bindings → letter-to-axis assignments.

### Command mode

![Status bar — command mode](assets/screenshots/statusbar-command.png)

After pressing `:`, the bar shows the text being typed followed by a prefix-matched hint list. The highlighted entry executes on `Enter`; a one-line description of that command appears right-aligned.

### Seek mode — axis search

![Status bar — seek axis search](assets/screenshots/statusbar-seek-axis.png)

In seek mode, pressing a letter opens an incremental axis search. Typing narrows the candidate list; when exactly one remains it is confirmed automatically (controlled by `seek.greedy` in `~/.juxt/settings.yaml`).

### Seek mode — value picker

![Status bar — value picker](assets/screenshots/statusbar-valuepicker.png)

Once an axis is confirmed (seek, or `Ctrl+letter` in tap/pin), the bar switches to value selection on that axis. The current value is highlighted; typing narrows to prefix-matching values.

---

## Info sidebar

![Info sidebar open](assets/screenshots/info-sidebar.png)

Toggle with `Ctrl+Shift+I` or `:info`. The panel docks to the right of the viewport and shows:

| # | Element | Description |
|---|---|---|
| 1 | **Path** | Resolved file path of the image currently on screen. |
| 2 | **Axis rows** | Each axis listed with all its values; the active value is highlighted in blue. |

---

## Screenshot guide

> The images above need to be captured from a live juxt session and saved to `docs/assets/screenshots/`. Here is the exact state and annotation for each file.

### Setup

Use the bundled sample config for a reproducible axis layout:

```bash
git clone https://github.com/ColinMoldenhauer/juxt
cd juxt
juxt sample_config.yaml
```

Axes: `sensor` (ASCAT, SMAP, SMOS) × `date` (2 values) × `overpass` (AM, PM) × `source` (L2, L3).

Maximise the window to roughly **1400 × 900 px** before capturing. Disable any OS drop-shadow if you want clean edges.

---

### `main-window-annotated.png`

**Capture:** Full juxt window — viewport + status bar visible, info sidebar hidden.

**State before capture:**
- Navigate to `sensor=SMAP  date=` (second date) `overpass=PM  source=L3` so values are non-default.
- Mode: tap (default on startup).
- File watching active (`●` visible).

**Annotations (numbered callouts — coloured circles + leader lines):**

| # | Points at |
|---|---|
| 1 | Centre of the image viewport |
| 2 | The entire status bar strip (bracket or underline the full bar) |
| 3 | `[tap]  ●` (mode + watch indicator) |
| 4 | `sensor=SMAP  date=…` (axis values) |
| 5 | `← → sensor  ↑ ↓ date` (arrow bindings) |
| 6 | `s=sensor  d=date  …` (letter assignments) |

---

### `statusbar-normal.png`

**Capture:** Crop tightly to the status bar strip only (~full width × ~22 px tall).

**State:** Same as `main-window-annotated.png`. No annotations needed.

---

### `statusbar-command.png`

**Capture:** Status bar strip only.

**State:** Press `:` and type `fi`. The bar should show something like:
```
:fi▌   fit   fit-height   fit-width                   fit — fit image to window
```
No annotations; the text is self-explanatory with the surrounding prose.

---

### `statusbar-seek-axis.png`

**Capture:** Status bar strip only.

**State:**
1. Switch to seek mode: `:mode seek` → Enter.
2. Press `s`. The bar shows an axis search with query `s` and candidate `sensor` highlighted.

---

### `statusbar-valuepicker.png`

**Capture:** Status bar strip only.

**State:** Continuing from the seek flow above, confirm `sensor` (Enter or let greedy auto-confirm). The bar now shows value candidates:
```
sensor › _   ASCAT   SMAP   SMOS
```
with the current value highlighted.

---

### `info-sidebar.png`

**Capture:** Full juxt window with the info sidebar open on the right.

**State:**
- Toggle sidebar: `Ctrl+Shift+I`.
- Navigate to any non-default position.

**Annotations:**

| # | Points at |
|---|---|
| 1 | The path line at the top of the sidebar |
| 2 | A highlighted (blue) value in one of the axis rows |
