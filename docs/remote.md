# Remote (SSH)

juxt can browse image directories on a remote server over SFTP. Images are downloaded to a temporary local directory at startup, so navigation after loading is instant — identical to local use with no per-keypress latency.

## Requirements

Install the `ssh` extra:

```bash
pip install juxt[ssh]
```

This adds **paramiko**, the pure-Python SSH library used for the SFTP connection.

---

## Authentication

juxt tries the following authentication methods in order:

1. **SSH agent** — if `ssh-agent`, Pageant, or a compatible agent (e.g. 1Password SSH agent) is running and has a key loaded for the target host, no further configuration is needed.
2. **Default key files** — `~/.ssh/id_ed25519`, `~/.ssh/id_rsa`, and other standard names are tried automatically.
3. **Explicit key file** — set `key_path` in the extended config form (see below).
4. **Password** — if all key-based methods fail, juxt shows a password dialog.

---

## Usage

### Command line

Pass a remote path directly as the `PATH` argument using the same `user@host:/path` syntax as `scp`:

```bash
# Auto-detect axes from a remote directory
juxt user@myserver.example.com:/data/plots/

# Use a template string directly (axis values detected by scanning the server)
juxt "user@myserver.example.com:/data/plots/{sensor}_{date}_{overpass}.png"
```

If you omit the username, juxt uses the current local username:

```bash
juxt myserver.example.com:/data/plots/
```

Non-standard ports are not supported via the CLI syntax; use a YAML config instead.

### YAML config

Add a `remote` key to any template-mode config file. The `template` path is the **remote** path on the server.

**Simple form** — a `user@host` string:

```yaml
remote: user@myserver.example.com

template: "/data/plots/{sensor}_{date}_{overpass}_{source}.png"
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

**Extended form** — a dict with all options:

```yaml
remote:
  host: myserver.example.com
  user: colin           # optional — defaults to current local user
  port: 22              # optional — default 22
  key_path: ~/.ssh/id_ed25519   # optional — falls back to agent / default keys

template: "/data/plots/{sensor}_{date}_{overpass}_{source}.png"
axes:
  sensor:   [ASCAT, SMAP, SMOS]
  date:     [2024-03-15, 2024-03-16]
  overpass: [AM, PM]
  source:   [L2, L3]
```

The `remote` field accepts several string formats:

| String | Meaning |
|---|---|
| `myserver` | Host `myserver`, current local user, port 22 |
| `user@myserver` | Host `myserver`, user `user`, port 22 |
| `myserver:9922` | Host `myserver`, current local user, port 9922 |
| `user@myserver:9922` | Host `myserver`, user `user`, port 9922 |

---

## How it works

1. juxt connects via SFTP and lists files under the remote directory (or matches files against the template pattern).
2. All matching images are downloaded to a temporary directory (a progress dialog is shown).
3. The temporary files are loaded as pixmaps and the viewer opens.
4. The temporary directory is deleted automatically when juxt exits.

`remote` cannot be combined with `discover` in a YAML config — use a template instead, or pass the remote directory path directly on the command line.

---

## Live updates and re-detection

### Polling for new files

Pass `--watch-interval SEC` (default 5) to poll the remote server for changed or new files. The watch indicator `●` in the status bar shows when polling is active. Use `:watch false` to disable or `:watch N` to change the interval at runtime.

### Picking up new axis values

If you add files on the server that introduce a new axis value, juxt won't see them automatically — polling only checks the known axis space. Run **`:reload`** to re-detect axes from the current template and download any new files.

### Changing the remote path at runtime

**`:pattern PATH`** lets you switch to a different remote template (or any other source) without restarting juxt and re-entering credentials. The argument is pre-filled with the current template; edit it and press `Enter`. The existing SFTP connection is reused when the host matches, and `Tab` completes remote paths using that connection.
