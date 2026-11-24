# Configuration

juxt accepts four kinds of input on the command line:

| Input | What happens |
|---|---|
| A directory path | Auto-detect axes from filenames in that directory |
| A YAML config file | Load the config (template or discover mode) |
| A template string with `{placeholders}` | Detect axis values by globbing the local filesystem |
| A path containing a `*` wildcard | Recursively match files, then auto-detect axes the same way a directory scan does |
| A remote path (`user@host:/path`) | Auto-detect axes from a remote directory via SFTP |

## CLI reference

```
juxt [options] [PATH]

PATH                        directory to scan or YAML config file
                            (default: current directory)

-s, --separator SEP [...]   separator(s) for auto-detection
-a, --auto                  skip the axis naming prompt
    --max-depth N           maximum subdirectory search depth
    --NAME VALUE [...]      pin a {placeholder}'s values (template/wildcard
                            PATH only, must come after PATH); a value may
                            be a glob or 're:REGEX' pattern; see below
    --pin NAME VALUE [...]  the same, for a NAME that is a juxt option too
    --full-path             show the full matched path of a {**name} axis
                            instead of pruning the shared leading part
    --list-axes             run axis discovery, print every axis with all
                            its values, and exit (no window); see below
    --subset-interactive    after discovery, tick the values to load in a
                            dialogue before any image is read; see below
    --cached                open a remote PATH (or a YAML config with
                            `remote`) from the download cache, offline;
                            see remote docs
    --cache-dir             print where a remote PATH is cached (without
                            PATH: the cache root) and exit
-h, --help                  show usage and exit
```

### Listing axes without opening a window

`--list-axes` runs the same discovery as a normal launch but only prints the result: the resolved template, then one line per axis with its value count and every value. It works with any `PATH` form (directory, template, wildcard, remote, YAML) and with `--NAME VALUE` pins, never asks for axis names, and needs no display.

```bash
$ juxt "plots/{overpass}/{sensor}_{date}.png" --list-axes
template: plots/{overpass}/{sensor}_{date}.png

overpass (2):  AM  PM
sensor (3):    ASCAT  SMAP  SMOS
date (2):      2024-03-15  2024-03-16
```

Only the listing goes to stdout; errors and the SSH password prompt go elsewhere, so the output is safe to pipe. Axes with a single value are listed too (`--squeeze` does not apply).

### Picking values at startup

`--subset-interactive` runs discovery as usual, then opens the value picker (one column per axis, every discovered value ticked) before a single image is read. Untick what you do not need and press `Load`; only the remaining combinations are loaded (or downloaded, for a remote PATH). `Cancel` quits.

It is the point-and-click alternative to narrowing the hypercube with `--NAME VALUE` pins. Together with it, pins preselect instead of narrowing: discovery runs without them, the picker shows every value on disk, and only the pinned values of a pinned axis start ticked (its other values are offered unticked). A [pattern](#patterns) pin ticks the values it matches.

```bash
juxt "plots/{sensor}_{date}.png" --subset-interactive --sensor SMAP SMOS
```

opens with SMAP and SMOS ticked, the other sensors unticked, and every date ticked. A pin discovery cannot do without still narrows it: values containing `/`, a `{**name}` axis, and any pin a `*` wildcard needs (if discovery fails without the pins, it runs with them and nothing is preselected).

`--squeeze` and `--save` see the picked values. The same picker is available at runtime as `:subset-interactive`.

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

<!-- regenerate with scripts/record_demo.sh --scene detect -->
![Naming the detected axes, then reusing the saved config](assets/demo-cli-detect.gif)

In text form:

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

### Pinning placeholder values

By default every `{placeholder}` in the template is resolved by globbing the filesystem for whatever values actually occur. Pass `--NAME VALUE [VALUE ...]` (after PATH) to pin one or more placeholders to an explicit value list instead — the remaining placeholders are still discovered by globbing, once per combination of pinned values, and merged into a shared axis space:

```bash
juxt "{root}/{dir}/images/{plot}.png" \
  --root /data/run1 /data/run2 /data/run3
```

This is useful for two things a plain glob can't do:

- **Filtering** — narrow an axis to a subset of what's on disk (e.g. only two of five sensors) without hand-editing a saved config.
- **Values that don't line up with a `*` glob** — a pinned value may itself contain `/`, so unrelated directory trees (like the three `run*` roots above, which don't share a common naming pattern) can become one axis.

Each `--NAME` must match a placeholder already in the template; repeat the flag once per placeholder to pin more than one. Values keep the order given on the command line rather than being sorted.

#### Placeholders named like a juxt option

A placeholder such as `{name}`, `{grid}` or `{save}` cannot be pinned as `--name A`: that is juxt's own `--name` option (the session name), and it keeps that meaning. Pin it with `--pin NAME VALUE ...` instead, which works for any placeholder and mixes freely with `--NAME` pins:

```bash
juxt "plots/{grid}/{sensor}.png" --pin grid g1 g2 --sensor SMOS
```

If such an option is on the command line while the placeholder is not pinned with `--pin`, juxt warns on stderr that the option did not pin it. `:copy-cli-command` writes `--pin NAME` for these placeholders, and shell completion offers the placeholder names after `--pin`.

#### Patterns

A value can also be a pattern that keeps the values on disk it matches, instead of naming each one:

```bash
juxt "plots/{sensor}_{date}.png" --sensor 'S*'               # SMAP, SMOS, ...
juxt "plots/{sensor}_{date}.png" --date 're:2024-03-1[5-7]'  # a regex
juxt "plots/{sensor}_{date}.png" --sensor ASCAT 'S*'         # literals and patterns mix
```

- **Glob**: any value containing `*`, `?` or `[` is a shell-style glob. As in the shell, `*` and `?` stop at `/` (so `--root 'runs/*'` matches `runs/a` but not `runs/a/b`), and `[!...]` negates a class.
- **Regex**: a value written `re:REGEX` is a Python regular expression. A plain value is never read as a regex, since `.` is common in values (`1.5`, `v2.0`). A leading flag such as `re:(?i)smos` works. Because discovery globs one path component at a time, a regex only matches within one component (except on a `{**name}` placeholder); to match a path like `runs/a`, use a glob.

A pattern matches the whole value, case-sensitively. The matches of each pattern are sorted the way a discovered axis is, and the patterns and literal values keep their command-line order relative to each other. Only files whose value matches count, so the other axes are narrowed too: with `--sensor 'S*'`, a date that only exists for ASCAT is left out. A pattern that matches nothing is an error, since that is usually a typo.

Quote patterns, or the shell expands them against the current directory first (zsh refuses to run the command at all when nothing matches). A filename that really contains `*`, `?` or `[` can be pinned as an escaped regex: `re:x\*y`. The command line copied by `:copy-cli-command` does this for you.

With `--subset-interactive`, a pattern ticks the values it matches. On a remote PATH a glob within one path component narrows the SFTP walk just like a literal value would, whereas a regex (or a glob with a `/` in it) means the subtree below the fixed prefix is read in full.

### Wildcard matching

A `*` in the path is matched recursively: unlike a shell glob, it reaches into nested subdirectories, so `plots/*.png` finds every PNG under `plots/`, however deeply nested. The matched files are then split into axes exactly the way auto-discover mode splits a directory scan, so any part of the path (or filename) that varies becomes an `axis_N`.

```bash
juxt "plots/*.png"
```

Combine a wildcard with `--NAME VALUE ...` to pin a `{placeholder}` before it and aggregate congruent files found under each pinned value, instead of listing every directory by hand:

```bash
juxt "{root}/*.png" --root /data/run1 /data/run2
juxt "{root}/*.png" --root '/data/run*'   # every run directory, as a pattern
```

Every `{placeholder}` in the path must sit before the first `*` and be pinned; a wildcard replaces the need to name the remaining path components, so there is nothing left for a free `{placeholder}` to bind to.

### One named axis for a whole subtree: `{**name}`

Auto-splitting a wildcard match into several `axis_N`s (above) assumes every level of the matched subtree is meaningful on its own and shaped the same way everywhere. When that's not true, or you just want one named axis for "whatever's under here" regardless of how deep it goes, write `{**name}` instead of a plain `{name}`. Unlike a normal placeholder it captures across `/`:

```bash
juxt "{root}/{**plot}.png" --root /data/run1 /data/run2
```

Every value `{**plot}` captures is guaranteed collision-free, since it's the file's entire relative path below the fixed prefix, not a value chosen from partway through it: two different files can never produce the same value. By default, any leading path segments shared by *every* matched file are pruned out of the axis value and folded into the template as fixed text instead, so a `plot` axis over `dir/AM/plot.png` and `dir/PM/plot.png` shows as `AM/plot` and `PM/plot`, not the full `dir/AM/plot`. Pass `--full-path` to see the unpruned values instead.

Only one `{**name}` is allowed per template: two independent captures that can each swallow an arbitrary amount of `/`-separated text would make the match ambiguous. A `{**name}` may itself be pinned like any other placeholder (`--plot VALUE ...`), in which case its values are taken literally and never pruned.

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

## Placeholder shapes

`~/.juxt/settings.yaml` says what kind of value a placeholder name stands for. Tab completion then stops at the end of that value instead of appending whatever the matching filenames happen to share. juxt seeds the section on first run:

```yaml
placeholders:
  date: [yyyy-mm-dd, yyyy_mm_dd, yyyymmdd]
  datetime: [yyyy-mm-ddThh:mm:ss, yyyy-mm-dd_hhmmss, yyyymmdd_hhmmss]
  time: [hh:mm:ss, hh-mm-ss, hhmmss]
  year: yyyy
  month: mm
  day: dd
  doy: ddd
  # orbit: 'o\d{5}'      # a regular expression works too
```

These are defaults, not built-ins: the file is the whole truth, so an entry you delete stops applying. Values are date-style shorthands (`yyyy` `yy` `mm` `dd` `hh` `ss` `ddd` `T` and separators), regular expressions, or lists of alternatives.

A name that is itself a shorthand — `{yyyy-mm-dd}`, `{yyyymmdd}` — is recognised without an entry. See [Navigation](navigation.md#placeholders-that-stand-for-a-known-value) for what this changes at the prompt.

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
