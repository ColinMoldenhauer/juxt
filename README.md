<p align="center">
  <img src="docs/assets/logo_bg.png" width="120" alt="juxt logo">
</p>

# juxt

`juxt` is a fast desktop tool for visually comparing plots across multiple parameter axes. Define axes (model, date, data source, ...) and flip through the resulting image hypercube with keyboard navigation.


*juxt* comes from *juxtapose* — placing things side by side to compare. That's the whole idea: flip through congruent plots fast enough to visually identify differences.

## Install

```
pip install juxt
```

## Quick start

```bash
# auto-detect axes from a directory of images
juxt /path/to/plots/

# or use a config file
juxt config.yaml
```

## Config

### Auto-discover mode

Point juxt at a directory and it figures out the axes from the filenames:

```bash
juxt /path/to/plots/
```

Or with an explicit config:

```yaml
discover:
  directory: plots/
  separator: "_"
```

Filenames are split on the separator; any position with more than one distinct value becomes an axis. Axes are initially named `axis_0`, `axis_1`, … — you'll be prompted to rename them on first run, or use `-a` to skip.

### Template mode

For full control, define the template and axes explicitly:

```yaml
template: "plots/{sensor}_{date}_{overpass}_{source}.png"
axes:
  sensor:   [ASCAT, SMAP, SMOS]
  date:     [2024-03-15, 2024-03-16]
  overpass: [AM, PM]
  source:   [L2, L3]
keys:
  s: sensor
  d: date
  o: overpass
  r: source
```

## Navigation

Default mode is **case-sensitive**: a lowercase letter navigates forward (+1) on that axis; uppercase navigates backward (−1). Arrow keys navigate the most recently used axis. To select any value of the axis, use `CTRL+[letter]`.

| Key | Action |
|---|---|
| `←` / `→` | cycle the active axis |
| `↑` / `↓` | cycle the secondary axis |
| lowercase letter | navigate +1 on that axis |
| uppercase letter | navigate −1 on that axis |
| `Ctrl`+letter | open value picker for that axis |
| `Space` | toggle between current and previous position |
| `Home` / `End` | jump to first / last value |
| `1`–`9` | jump to the Nth value |

Use `:mode twin|multi|case` in the command bar to switch navigation modes.


## Controls

#### zoom controls
| Key | Action |
|---|---|
| double-click | fit image to window |
| `0` | reset zoom to 100% |
| scroll wheel | zoom (anchored under cursor) |
#### zoom controls
| Key | Action |
|---|---|
| `Enter` | toggle fullscreen |
| `Ctrl+H` | toggle status bar |
