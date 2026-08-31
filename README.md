<p align="center">
  <img src="https://raw.githubusercontent.com/ColinMoldenhauer/juxt/main/docs/assets/logo_bg.png" width="120" alt="juxt logo">
</p>

# juxt

[![PyPI version](https://img.shields.io/pypi/v/juxt.svg)](https://pypi.org/project/juxt/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/juxt.svg)](https://pypi.org/project/juxt/)
[![Tests](https://github.com/ColinMoldenhauer/juxt/actions/workflows/tests.yml/badge.svg)](https://github.com/ColinMoldenhauer/juxt/actions/workflows/tests.yml)
[![Docs](https://readthedocs.org/projects/juxt/badge/?version=latest)](https://juxt.readthedocs.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`juxt` is a fast desktop tool for visually comparing plots across multiple parameter axes. Define axes (model, date, data source, ...) and flip through the resulting image hypercube with keyboard navigation.

*juxt* comes from *juxtapose* — placing things side by side to compare. That's the whole idea: flip through congruent plots fast enough to visually identify differences.

## Install

```
pip install juxt
```

For SSH remote loading:

```
pip install juxt[ssh]
```

## Quick start

`juxt` accepts several forms as its first argument:

```bash
juxt /path/to/plots/                        # auto-detect axes from a directory of images
juxt "plots/{sensor}_{date}.png"            # explicit local template
juxt myhost:/path/to/plots/                 # remote directory over SSH
juxt "myhost:/path/{sensor}_{date}.png"     # remote template over SSH
juxt config.yaml                            # explicit config file
```

For directory and remote-directory modes, filenames are split on separators (`_` and `/` by default) and any position with more than one distinct value becomes an axis. You'll be prompted to name the axes on first run; use `-a` to skip.

Run `juxt` with no argument and it opens a dialog instead: a **Browse** tab for picking a directory (auto-discover), and a **Build path** tab for typing a `{placeholder}` template by hand, with `Tab` completing real directories and files along the way.

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

### Remote mode (SSH)

Images are downloaded over SFTP at startup and cached locally; navigation afterwards is identical to local use. Auth uses your SSH agent or `~/.ssh/config` key, with a password fallback.

```yaml
remote: myhost               # or user@myhost, or user@myhost:port
template: "/data/plots/{sensor}_{date}.png"
axes:
  sensor: [ASCAT, SMAP]
  date:   [2024-03-15, 2024-03-16]
```

Or pass the remote path directly on the command line — juxt will detect axes from the remote filenames automatically:

```bash
juxt myhost:/data/plots/
```

Requires the SSH extra: `pip install juxt[ssh]`



## Navigation

Default mode is **tap**. Each axis is assigned one letter key — tap it to step forward, hold Shift to step backward. Every navigation is a single keypress, no modifiers, no menus.

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

Use `:mode tap|seek|pin` in the command bar to switch navigation modes.

## Command mode

Press `:` to open the command bar (vim-style). Tab-completion narrows candidates as you type.

Path arguments complete with `{placeholders}` in place: `:pattern plots/{sensor}/2⇥` lists what every sensor directory holds, so a template is built top-down instead of completing a concrete path and deleting the parts that should vary. `{` + `Tab` completes an existing axis name, and anonymous `{}` placeholders are numbered automatically.

## Shell completion

The same completion works at the shell prompt. Add one line to `~/.bashrc`:

```bash
eval "$(juxt --bash-completion)"
```

zsh needs `bashcompinit` first:

```zsh
autoload -U bashcompinit && bashcompinit
eval "$(juxt --bash-completion)"
```

Then `juxt plots/{sensor}/2⇥` completes at the prompt, as do the command-line options. ble.sh is handled as well, through its own hook. Reload the shell (`source ~/.bashrc`, or open a new terminal) for it to take effect.

Point `JUXT_COMPLETE` at `juxt-complete` if juxt lives in a virtualenv you do not activate:

```bash
export JUXT_COMPLETE=~/env/juxt/bin/juxt-complete
```

Placeholders standing for a known kind of value complete only as far as that value: `plots/{date}⇥` stops at `plots/{date}_` rather than swallowing the rest of the filename. The names that count are the ones listed under `placeholders:` in `~/.juxt/settings.yaml`, which juxt seeds with `date datetime time year month day doy` — edit or delete them, and completion follows. Date-style shorthands such as `{yyyy-mm-dd}` are recognised on sight, with or without an entry.

| Command | Action |
|---|---|
| `:q` | quit |
| `:fit` | fit image to window |
| `:fit height` / `:fit width` | fit to height or width |
| `:zoom N` | set zoom to N% |
| `:fullscreen` | toggle fullscreen |
| `:mode tap\|seek\|pin` | switch navigation mode |
| `:grid [AXIS]` | tile an axis into a grid of viewports |
| `:grid AXIS VAL …` | grid with a value subset |
| `:grid-layout NxM` | change grid layout without exiting |
| `:ungrid` | return to single-image view |
| `:grid-sharex` / `:grid-sharey` | toggle synchronized pan/zoom |

## Controls

#### Zoom
| Key | Action |
|---|---|
| double-click | fit image to window |
| `0` | reset zoom to 100% |
| scroll wheel | step forward / backward on active axis |
| `Shift` + scroll wheel | step backward / forward on active axis |
| `Ctrl` + scroll wheel | zoom (anchored under cursor) |
| drag | pan |

#### View
| Key | Action |
|---|---|
| `Enter` | toggle fullscreen |
| `Ctrl+H` | toggle status bar |
