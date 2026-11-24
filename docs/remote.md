# Remote (SSH)

juxt can browse image directories on a remote server over SFTP. Images are downloaded to a local cache at startup, so navigation after loading is instant — identical to local use with no per-keypress latency — and the next session only fetches what changed on the server.

## Requirements

Nothing beyond `pip install juxt`: **paramiko**, the SSH library used for the SFTP connection, comes with it.

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
2. The matching images are downloaded into the download cache (a progress dialog is shown), several at a time over the one SSH connection. A file the cache already holds with the server's size and modification time is not downloaded again.
3. The images are decoded in parallel and the viewer opens.

### Download cache

Downloads are kept in a cache per `user@host_port`, so reopening the same plots, say a few minutes later, only fetches the files that changed on the server:

| Platform | Location |
|---|---|
| Linux | `~/.cache/juxt/ssh/` (or `$XDG_CACHE_HOME/juxt/ssh/`) |
| macOS | `~/Library/Caches/juxt/ssh/` |
| Windows | `%LOCALAPPDATA%\juxt\Cache\ssh\` |

Files not used for a week are removed at the next remote session. Both that and the parallelism are set in `~/.juxt/settings.yaml`:

```yaml
loading:
  threads: 0                # images loaded at once; 0 = automatic (CPUs, at most 8)
  remote_cache_days: 7      # drop downloads unused this long; 0 = keep none
```

With `remote_cache_days: 0`, each session downloads into a temporary directory that is deleted when juxt exits, as before the cache existed. Deleting the cache directory by hand is always safe.

### Browsing the cache offline

The cache copies the remote paths below the `user@host_port` directory, so `me@box:/data/plots/A_1.png` is kept as `~/.cache/juxt/ssh/me@box_22/data/plots/A_1.png`. Add `--cached` to the command you used remotely and juxt opens that copy instead, without connecting:

```bash
juxt "me@box:/data/plots/{sensor}_{date}.png"            # downloads
juxt "me@box:/data/plots/{sensor}_{date}.png" --cached   # the same plots, offline
```

It works for every remote `PATH` form (directory, template, wildcard) and for a YAML config with `remote`, together with `--NAME VALUE` pins and the other options. A remote launch whose files are already cached says so on stderr:

```
Cached run of me@box:/data/plots/{sensor}_{date}.png detected in /home/me/.cache/juxt/ssh/me@box_22/data/plots. Use --cached to open it without connecting.
```

`--cache-dir` prints where a remote `PATH` is cached and exits, or, without a `PATH`, the cache root:

```bash
$ juxt "me@box:/data/plots/{sensor}_{date}.png" --cache-dir
/home/me/.cache/juxt/ssh/me@box_22/data/plots/{sensor}_{date}.png
```

Some things to keep in mind:

- Only what was downloaded is there. A session narrowed with pins or `--subset-interactive` cached only those files, and the files of other sessions under the same directory are there as well, so a directory scan may find more or fewer values than the remote run did.
- The cache is per `user@host_port` as written: `box:/data` and `me@box:/data` are cached apart, even when `me` is your local user name.
- A `--cached` session counts as a use, so the files it opens are not pruned for another `remote_cache_days`. To keep a set for good, copy it out of the cache.

`remote` cannot be combined with `discover` in a YAML config — use a template instead, or pass the remote directory path directly on the command line.

---

## Live updates and re-detection

### Polling for new files

Pass `--watch-interval SEC` (default 5) to poll the remote server for changed or new files. The watch indicator `●` in the status bar shows when polling is active. Use `:watch false` to disable or `:watch N` to change the interval at runtime.

### Picking up new axis values

If you add files on the server that introduce a new axis value, juxt won't see them automatically — polling only checks the known axis space. Run **`:reload`** to re-detect axes from the current template and download any new files.

### Changing the remote path at runtime

**`:pattern PATH`** lets you switch to a different remote template (or any other source) without restarting juxt and re-entering credentials. The argument is pre-filled with the current template; edit it and press `Enter`. The values you picked for axes the new template keeps can be kept or rescanned; see [Keeping the current subset](navigation.md#keeping-the-current-subset). The existing SFTP connection is reused when the host matches, and `Tab` completes remote paths using that connection.
