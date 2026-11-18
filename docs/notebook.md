# Jupyter notebooks

!!! note "Prototype"
    The notebook widget is an early prototype. It covers the core navigation
    keys, but not every feature of the desktop app (see the
    [comparison](#compared-with-the-desktop-app) below).

juxt also runs as a widget inside Jupyter (JupyterLab, Notebook 7, and other
front ends that support [anywidget](https://anywidget.dev)).

Choose the install that fits how you'll use juxt:

```bash
pip install juxt-notebook       # notebook widget only, without Qt
```

```bash
pip install "juxt[notebook]"    # desktop app and notebook widget
```

`juxt-notebook` installs the same `juxt` package, but without PySide6 (about
200 MB). This suits JupyterHub, Colab and servers without a display. The `juxt`
desktop command is not included. Install one of the two packages, not both.

## Usage

```python
from juxt.notebook import Viewer

# Any juxt config file
Viewer("config.yaml")

# Or a template and axes, without a config file
Viewer(template="plots/{sensor}_{date}.png",
       axes={"sensor": ["ASCAT", "SMAP", "SMOS"], "date": ["2024-03-15", "2024-03-16"]})
```

Click the viewer to give it the keyboard. A blue border shows it has focus.
While it has focus, Jupyter's own command-mode shortcuts (`a`, `b`, `dd`,
arrows, …) are turned off. Press Escape or click outside the viewer to return
to the notebook.

### Plots without files

`Viewer.from_function` calls a plotting function once for each combination of
values and shows the results. You don't need to write the plots to disk first:

```python
import matplotlib.pyplot as plt

def plot(sensor, date):
    fig, ax = plt.subplots()
    ax.plot(load(sensor, date))
    ax.set_title(f"{sensor} {date}")
    return fig            # closed for you after rendering

Viewer.from_function(plot, sensor=["ASCAT", "SMAP"], date=dates)
```

The function can return a matplotlib `Figure`, a PIL image, PNG bytes or a file
path. If it returns `None` or raises an error, that combination is shown as
missing.

### Controlling it from Python

```python
v = Viewer("config.yaml")
v                             # display it

v.coords                      # {'sensor': 'SMAP', 'date': '2024-03-16'}
v.goto(sensor="SMOS")         # move the view from Python
v.step("date", -1)
v.path                        # file currently shown
v.reload()                    # re-read the files after re-plotting

v.observe(lambda ch: print(v.coords), names="pos")   # react to key presses
```

Options: `mode="tap"|"seek"|"pin"`, `height=500` (pixels), `show_info=True`,
`skip_missing`, `fuzzy`, `greedy`, `axis_h`/`axis_v` (to lock the arrow axes),
`keys={"x": "sensor"}`.

When they aren't given, `skip_missing`, `fuzzy` and `greedy` are read from
`~/.juxt/settings.yaml`. Pass `use_settings=False` to ignore that file.

## Keys

These keys work as they do in the desktop app (see [Navigation](navigation.md)):

| Key | Action |
|---|---|
| ←/→, ↑/↓ | Step the horizontal / vertical axis |
| Space | Toggle current ↔ previous position |
| Home / End, 1–9 | First / last / Nth value of the horizontal axis |
| letter, Shift+letter | tap: step the axis forward / backward · pin: focus the axis |
| Ctrl+letter **or Alt+letter** | Open the value picker. Use Alt for letters the browser keeps for itself (Ctrl+W, Ctrl+T, Ctrl+N). |
| any letter (seek mode) | Search for an axis, then a value |
| `:` | Command mode: `fit`, `fit height`, `fit width`, `fit-off`, `zoom N`, `mode tap\|seek\|pin`, `switch-last`, `swap-axes`, `fullscreen`, `info` |
| `0`, `+`, `-` | Zoom to 100%, zoom in, zoom out |
| wheel, drag, double-click | Zoom (only while focused), pan, fit |
| Enter | Fullscreen |

## How it works

When the widget is displayed, the kernel sends the compressed image files
to the browser once, in chunks of about 8 MB. All navigation then runs in the
browser, in `juxt/static/nav.js`. That file is a JavaScript port of
`juxt.core.Navigator`, which also drives the desktop viewer. Because of this,
flipping between images never waits for the kernel, even when the kernel is
remote. The new position is synced back to Python as `v.pos`.

A test (`tests/test_core.py::test_js_parity`) checks that the two
implementations behave identically. It replays 200 random navigation sequences
through both and compares them.

The images are sent as messages, not stored as widget state. Saving the
notebook with widget state therefore does not embed them in the `.ipynb` file.

## Compared with the desktop app

| | Desktop | Notebook |
|---|---|---|
| Template / discover configs | ✓ | ✓ |
| tap / seek / pin, picker, Spacebar | ✓ | ✓ |
| Pan / zoom / fit | ✓ | ✓ |
| Info sidebar | ✓ | ✓ (clickable value list) |
| Render from a Python function | – | ✓ |
| Grid view | ✓ | not yet |
| File watching | ✓ | call `v.reload()` |
| SSH remote | ✓ | not needed: run the kernel where the plots are |
| Custom keybindings, `:save`, `:pattern` | ✓ | not yet |
