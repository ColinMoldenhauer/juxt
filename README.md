# juxt


*juxt* is clipped from *juxtapose* (Latin *juxta*, "beside") and shares its meaning with παραβολή (*parabolē*, "placing beside") — the Greek root of both *parable* and *parabola*.

`juxt` is a fast desktop tool for visually comparing geospatial plots across multiple parameter axes. Define axes (sensor, date, overpass, source, …) and flip through the resulting image hypercube with keyboard navigation.

## Install

```
pip install juxt
```

## Quick start

```bash
python make_sample.py          # generate 24 synthetic test images
juxt sample_config.yaml
```

## Config

### Template mode

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

### Auto-discover mode

```yaml
discover:
  directory: plots/
  separator: "_"
```

Scans filenames, splits on the separator, and treats any column with more than one distinct value as an axis. On first run, axes are named `axis_0`, `axis_1`, … — rename them by switching to template mode.

## Navigation

| Key | Action |
|---|---|
| `←` / `→` | cycle the horizontal axis |
| `↑` / `↓` | cycle the vertical axis |
| letter key | focus that axis — most recent becomes horizontal, previous becomes vertical |
| `Space` | toggle between current and previous position |
| `Home` / `End` | jump to first / last value on the focused axis |
| `1`–`9` | jump to the Nth value on the focused axis |
| `f` or double-click | fit image to window |
| `0` | reset zoom to 100% |
| `h` | toggle status overlay |
| scroll wheel | zoom (anchored under cursor) |
| drag | pan |

The status overlay (top-left) shows the current coordinate values and which axes are bound to the arrow keys.
