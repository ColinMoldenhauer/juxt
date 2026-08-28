# Installation

## Standalone binary (no Python required)

Pre-built binaries are attached to every [GitHub Release](https://github.com/ColinMoldenhauer/juxt/releases).
Download the archive for your platform, extract it, and run — no Python or pip needed.

| Platform | File | Run |
|---|---|---|
| Windows | `juxt-windows.zip` | Extract, then double-click `juxt\juxt.exe` or run it from a command prompt |
| Linux | `juxt-linux.zip` | Extract, then `./juxt/juxt path/to/config.yaml` |
| macOS | `juxt-macos.zip` | Extract, then `open juxt.app` — **built for Apple Silicon (arm64), untested** |

**macOS only:** macOS Gatekeeper will block the unsigned app on first launch.
Right-click the app → Open → Open to bypass, or run once from the terminal:
```
xattr -cr juxt.app && open juxt.app
```

SSH support (remote image directories) is included in all binaries.

---

## Requirements (pip install)

- Python 3.10 or newer
- A working Qt installation is bundled with PySide6 — no separate Qt install needed

## Standard install

```bash
pip install juxt
```

This installs juxt and its two runtime dependencies: **PySide6** (the Qt GUI toolkit) and **PyYAML** (config parsing).

## SSH extra

To browse image directories on a remote server over SFTP, install the `ssh` extra:

```bash
pip install juxt[ssh]
```

This adds **paramiko**, the pure-Python SSH library juxt uses for remote connections. See [Remote (SSH)](remote.md) for usage.

## Development install

Clone the repo and install in editable mode:

```bash
git clone https://github.com/ColinMoldenhauer/juxt
cd juxt
pip install -e ".[docs,ssh]"
```

The `docs` extra installs `mkdocs-material` and `mkdocs-include-markdown-plugin` so you can build the documentation locally:

```bash
mkdocs serve   # live-reloading local preview
mkdocs build   # build static site to site/
```

## Shell completion (bash / zsh)

`pip install juxt` also installs `juxt-complete`, a small helper that backs Tab completion for the `juxt` command. Enable it by adding one line to `~/.bashrc`:

```bash
eval "$(juxt-complete --bash)"
```

zsh can reuse the same function through `bashcompinit`:

```zsh
autoload -U bashcompinit && bashcompinit
eval "$(juxt-complete --bash)"
```

Completion covers the command-line options and the `PATH` argument — and paths complete with `{placeholders}` left in place, the same way the `:pattern` command bar inside the app does:

```
$ juxt plots/<TAB>
ASCAT/  SMAP/  SMOS/

$ juxt plots/{sensor}/2<TAB>          # every sensor directory at once
plots/{sensor}/2024-03-15.png  plots/{sensor}/2024-03-16.png
```

Braces need no quoting: bash and zsh only expand them when they contain a comma or a range.

Two limits are worth knowing. Remote `host:/path` arguments are not completed at the shell — that needs a live SFTP session, so use `:pattern` inside the app instead. And the standalone binaries ship no `juxt-complete`; with the helper missing, the function falls back to plain filename completion. Point `JUXT_COMPLETE` at the helper if it lives outside your `PATH`, e.g. in another virtualenv.

## Debugging / logging

juxt uses Python's standard `logging` module. To enable diagnostic output, set the `JUXT_LOG_LEVEL` environment variable before launching:

```bash
JUXT_LOG_LEVEL=DEBUG juxt /path/to/plots/   # Linux / macOS
$env:JUXT_LOG_LEVEL="DEBUG"; juxt ...       # PowerShell
```

Valid values are `DEBUG`, `INFO`, `WARNING` (default), `ERROR`, and `CRITICAL`. To capture output to a file as well, set `JUXT_LOG_FILE`:

```bash
JUXT_LOG_LEVEL=DEBUG JUXT_LOG_FILE=juxt.log juxt /path/to/plots/
```

---

## Verifying the install

```bash
juxt --help
```

You should see the usage box. To open the bundled sample config immediately:

```bash
git clone https://github.com/ColinMoldenhauer/juxt
cd juxt
juxt examples/sample_config.yaml
```
