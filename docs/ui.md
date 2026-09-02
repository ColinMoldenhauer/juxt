# UI overview

A quick tour of the juxt interface and its interactive elements.

---

## Main window

![Main window with callouts](assets/screenshots/main-window-annotated.png)

| # | Element | Description |
|---|---|---|
| 1 | **Image viewport** | Displays the current image. Drag to pan, scroll to zoom, double-click to fit, right-click for the copy menu. |
| 2 | **Status bar** | One-line summary of navigation state. Toggleable with `Ctrl+Shift+H`; auto-appears while command or value-picker is active. Shown at startup unless `--no-status-bar` is passed. |
| 3 | **Title bar** | Shows `juxt \| <session name>`. The session name defaults to the config filename and can be overridden with `--name`. |

---

## Status bar

### Normal

![Status bar — normal state](assets/screenshots/statusbar-normal.png)

| # | Element | Description |
|---|---|---|
| 1 | **Mode** | `[tap]`, `[seek]`, or `[pin]` — the active navigation mode. |
| 2 | **Watch indicator** | `●` when live file-watching is active; absent otherwise. |
| 3 | **Axis values** | Current position in the hypercube, e.g. `sensor=ASCAT  date=2024-03-16`. |
| 4 | **Arrow bindings** | Which axes are mapped to `←/→` and `↑/↓`, updated as you focus different axes. |
| 5 | **Letter assignments** | Which letter key navigates which axis. Axes without an available letter are shown in red. |

### Command mode

![Status bar — command mode](assets/screenshots/statusbar-command.png)

Triggered by pressing `:`.

| # | Element | Description |
|---|---|---|
| 1 | **Cursor** | The command being typed, e.g. `:fit▌`. In a free-text argument (`:pattern`, `:write`), each `{placeholder}` is coloured by position. |
| 2 | **Suggested values** | Fuzzy-matched command candidates, best match first; the selected entry is shown in `[brackets]` and coloured like the highlighted value in the info sidebar. After `Tab` on a path argument, this row lists the matching directory entries instead. |
| 3 | **Tooltip** | One-line description of the selected command, right-aligned. |

### Seek mode

![Status bar — seek mode](assets/screenshots/statusbar-seek.png)

Triggered by pressing any letter in seek mode. The same incremental search runs for both axis selection and value selection.

| # | Element | Description |
|---|---|---|
| 1 | **Prompt** | `axis?` while selecting an axis; `<axis-name> ›` while selecting a value on that axis. |
| 2 | **Cursor** | The query being typed. |
| 3 | **Candidates** | Matching axes or values, best match first; the current selection is shown in `[brackets]` and coloured like the highlighted value in the info sidebar. Matching is fuzzy — `smp` finds `SMAP` (`seek.fuzzy`) — and a lone candidate is confirmed automatically (`seek.greedy`); both live in `~/.juxt/settings.yaml`. |

---

## Info sidebar

![Info sidebar open](assets/screenshots/info-sidebar.png)

Toggle with `Ctrl+Shift+I` or `:info`. The panel docks to the right of the viewport and lists all axes with their values; the active value on each axis is highlighted.

It starts closed; pass `--info` to open it right away. The status bar is the mirror case — shown by default, hidden at startup with `--no-status-bar`.

---

## Shortcut sidebar

Toggle with `Ctrl+Shift+K` or `:keys`, or open it at startup with `--keys`. The panel docks to the left of the viewport, opposite the info sidebar, so both can be open at once.

Everything it lists is read out of the running session rather than written down in advance:

- the `←`/`→` and `↑`/`↓` rows name the axes those arrows currently cycle
- the letter-key section is the one for the active navigation mode, and switching mode with `:mode` rewrites it
- the axis-key list is the live letter assignment, including anything moved with `:change-key`
- `Ctrl+letter` and `Shift+letter` appear in whichever role [`modifiers.swap`](navigation.md#modifier-roles) gives them
- the last section lists your own [keybindings](navigation.md#custom-keybindings) with the action each one runs

Saving `~/.juxt/settings.yaml` redraws the panel, so a rebound chord shows up without a restart.

Every value is clickable: a left click jumps straight to it, focuses that axis (so `←`/`→` cycle it) and records the previous position, so `Spacebar` flips back. Clicking the value that is already active only focuses the axis.

---

## Grid builder

![Grid builder dialogue](assets/screenshots/grid-dialog.png)

Open with `Ctrl+Shift+G` or `:grid-dialog` — a point-and-click front end for the
`:grid` command family.

| Field | Description |
|---|---|
| **Axis** | The axis to tile across the grid. Pre-selected to the current grid axis, or the `←`/`→` axis when not yet in grid view. |
| **Values** | One checkbox per value; only the ticked ones become cells. `All`, `None` and `Invert` set them in bulk. |
| **Layout** | With `auto` ticked the spin boxes preview the layout fitted to the viewport aspect ratio. Stepping either spin box unticks `auto` and hands you rows × cols; re-tick it to return to the fitted layout. Rows × cols is a hard cap — pick fewer panes than values and the leftovers stay reachable through the focused pane. |
| **Sync pan/zoom** | The per-axis equivalents of `:grid-sharex` and `:grid-sharey`. |

The grey line above the buttons previews the outcome — `3 cells → 2×2 (auto)` —
and says how many values the focused pane will cycle when an explicit layout has
fewer panes than selected values.

Click a pane to focus it; the grid axis then scrolls that pane alone, through
the values no other pane is showing.

**Show grid** applies the settings, **Exit grid** (shown only while a grid is
open) is the same as `:ungrid`, and every field is pre-filled from the active
grid, so the dialogue doubles as an editor for the current view.

---

## Highlight format

Three places show the current selection in colour: the active value in the info sidebar, the active axis in the status-bar coordinates, and the selected entry in a candidate list. All three are configurable in `~/.juxt/settings.yaml`:

```yaml
highlight:
  selected: "#6af:{}"       # info sidebar / active axis
  candidates: "#6af:[{}]"   # status-bar candidate lists
```

Each value is a single format string:

```
[style:]template
```

| Part | Meaning |
|---|---|
| `style` | A colour (`#6af`, `#66aaff` or a CSS colour name) plus any of `bold`, `italic`, `underline`, separated by spaces. Omit the whole `style:` part to leave the colour untouched. |
| `template` | Free text around `{}`, which stands for the value. The text is inserted literally, so `<{}>` really shows angle brackets. |

```yaml
highlight:
  selected: "bold #f80:» {} «"
  candidates: "underline #6af:{}"
```

Writing a single string instead of the two keys applies it to both contexts:

```yaml
highlight: "#f80:«{}»"
```

For anything the two fields cannot express, prefix the spec with `html:` and write the rich text yourself:

```yaml
highlight:
  candidates: 'html:<span style="background:#334; color:#fff">[{}]</span>'
```

A spec without a `{}` placeholder is ignored: juxt logs a warning and keeps the default. Changes take effect as soon as the settings file is saved, no restart needed.

Note that when the status bar is too narrow for its content, it falls back to plain text and the styling is dropped. Keeping visible delimiters in `candidates` means the selection stays identifiable in that case.
