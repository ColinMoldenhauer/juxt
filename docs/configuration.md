# Configuration

juxt accepts four kinds of input on the command line:

| Input | What happens |
|---|---|
| A directory path | Auto-detect axes from filenames in that directory |
| A YAML config file | Load the config (template or discover mode) |
| A template string with `{placeholders}` | Detect axis values by globbing the local filesystem |
| A remote path (`user@host:/path`) | Auto-detect axes from a remote directory via SFTP |

## CLI reference

```
juxt [options] [PATH]

PATH                        directory to scan or YAML config file
                            (default: current directory)

-s, --separator SEP [...]   separator(s) for auto-detection
-a, --auto                  skip the axis naming prompt
    --max-depth N           maximum subdirectory search depth
-h, --help                  show usage and exit
```

---

## Auto-discover mode

Point juxt at a directory and it figures out the axes automatically:

```bash
juxt /path/to/plots/
```

Or use a YAML config with a `discover` block:

```yaml
discover:
  directory: plots/
  separator: "_"
```

### How detection works

juxt collects all image files under the directory (recursively), takes their paths relative to the root, and splits each path on the separator characters. Any position where more than one distinct value appears across the files becomes an axis. Positions with a single value are treated as fixed text in the template.

**Default separators** are `_` (underscore) and `/` (the path separator), so directory structure is automatically included in the axis space. For example, if your plots are organized as `plots/sensor/date_overpass.png`, the directory level (`sensor`) and the filename parts (`date`, `overpass`) all become candidate axes in one pass.

### Multiple separators

Pass multiple separators via `--separator` to split on more than one character simultaneously:

```bash
juxt /path/to/plots/ --separator _ -
```

The separators are preserved in the generated template so the filenames can be reconstructed exactly.

### Depth limit

By default detection is fully recursive. Use `--max-depth` to limit how deep it searches:

```bash
juxt /path/to/plots/ --max-depth 1   # only immediate children
```

### The naming prompt

On first run, juxt shows you the detected axes and lets you rename or ignore each one:

```
Detected 48 images

  separator '_' '/' → {axis_0|axis_1|...}/{axis_2}_{axis_3}_{axis_4}.png

  axis_0:  ASCAT  SMAP  SMOS
    name (Enter to ignore): sensor
    → 'sensor'

  axis_1:  2024-03-15  2024-03-16
    name (Enter to ignore): date
    → 'date'

  axis_2:  AM  PM
    name (Enter to ignore):
    → ignored (fixed to 'AM')
  …
```

Press Enter without typing a name to ignore an axis (it is fixed to its first value and excluded from navigation). Use `-a` / `--auto` to skip the prompt entirely and use the generated `axis_0`, `axis_1`, … names directly.

---

## Template mode

For full control, write a YAML config that specifies the filename template and the exact values for each axis:

```yaml
template: "plots/{sensor}_{date}_{overpass}_{source}.png"
axes:
  sensor:   [ASCAT, SMAP, SMOS]
  date:     [2024-03-15, 2024-03-16]
  overpass: [AM, PM]
  source:   [L2, L3]
```

The template uses Python's `str.format` syntax — each `{name}` is substituted with the current axis value. Paths can be relative (resolved from the working directory where juxt is launched) or absolute.

### Passing a template directly on the command line

You can skip the YAML file and pass the template string directly. juxt detects axis values by globbing the filesystem:

```bash
juxt "plots/{sensor}_{date}_{overpass}_{source}.png"
```

---

## Key bindings

By default juxt assigns each axis the first letter of its name that isn't already taken. You can override this with a `keys` block:

```yaml
keys:
  s: sensor
  d: date
  o: overpass
  r: source
```

Keys are single lowercase letters. Each letter is bound to exactly one axis. Any axis without a key binding cannot be accessed via letter keys (only via arrow keys once it rises to the top of the focus stack).

---

## Navigation mode

Set the default navigation mode in the config file. You can still change it at runtime with `:mode`.

```yaml
mode: case-sensitive   # default
# mode: multi-select
# mode: twin
# mode: 2             # equivalent to case-sensitive
# mode: 1             # equivalent to multi-select
# mode: 0             # equivalent to twin
```

See [Navigation](navigation.md) for a description of each mode.

---

## Remote (SSH)

Add a `remote` key to load images from a server over SFTP instead of the local filesystem:

```yaml
remote: user@myserver.example.com

template: "/data/plots/{sensor}_{date}_{overpass}_{source}.png"
axes:
  sensor:   [ASCAT, SMAP, SMOS]
  date:     [2024-03-15, 2024-03-16]
  overpass: [AM, PM]
  source:   [L2, L3]
```

See [Remote (SSH)](remote.md) for the full reference.

---

## Complete config reference

| Key | Type | Required | Description |
|---|---|---|---|
| `template` | string | yes* | Filename template with `{axis}` placeholders |
| `axes` | dict | yes* | Axis names mapped to lists of values |
| `discover.directory` | string | yes* | Directory to auto-scan (alternative to template+axes) |
| `discover.separator` | string | no | Separator for auto-discover (default `_`) |
| `keys` | dict | no | Letter-to-axis bindings (auto-assigned if omitted) |
| `mode` | int or string | no | Navigation mode: `0`/`twin`, `1`/`multi-select`, `2`/`case-sensitive` (default `2`) |
| `remote` | string or dict | no | SSH connection info — see [Remote (SSH)](remote.md) |

\* Either `template` + `axes`, or `discover`, must be present.
