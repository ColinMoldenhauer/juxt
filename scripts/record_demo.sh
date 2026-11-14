#!/usr/bin/env bash
# Record scripted juxt CLI sessions (scripts/demo_cli.py) with asciinema and render them to GIFs with agg.
# See the docstring of scripts/demo_cli.py for how to install asciinema and agg.
#
#   scripts/record_demo.sh                    # every scene -> docs/assets/demo-cli-<scene>.gif
#   scripts/record_demo.sh --scene detect     # one scene only (completion | detect | all)
#   scripts/record_demo.sh --keep-cast        # also keep the .cast next to the GIF (e.g. for asciinema.org)
#
# Options: --out-dir DIR (default docs/assets)  --cols N  --rows N  --font-size N
#          --speed F (typing/pause speed factor, default 1)  --theme NAME (agg theme, default monokai)
#          --idle-limit SEC (cap pauses in the recording, default 2)  -h|--help
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out_dir="$repo/docs/assets"
scenes=(completion detect)
cols="" rows="" font_size=16 speed=1 theme=monokai idle_limit=2 keep_cast=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scene)      scenes=("$2"); shift ;;
    --out-dir)    out_dir="$2"; shift ;;
    --cols)       cols="$2"; shift ;;
    --rows)       rows="$2"; shift ;;
    --font-size)  font_size="$2"; shift ;;
    --speed)      speed="$2"; shift ;;
    --theme)      theme="$2"; shift ;;
    --idle-limit) idle_limit="$2"; shift ;;
    --keep-cast)  keep_cast=1 ;;
    -h|--help)    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

# agg is usually installed with cargo, which not every shell has on PATH
[[ -x "$HOME/.cargo/bin/agg" ]] && PATH="$PATH:$HOME/.cargo/bin"
for tool in asciinema agg python; do
  command -v "$tool" >/dev/null || { echo "missing: $tool (see scripts/demo_cli.py docstring)" >&2; exit 1; }
done

export TERM=xterm-256color LANG=C.UTF-8
mkdir -p "$out_dir"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# Terminal size per scene: just tall enough that nothing important scrolls away.
size_of() {
  case "$1" in
    completion) echo "92 15" ;;
    *)          echo "92 31" ;;
  esac
}

record() {  # record SCENE -> $out_dir/demo-cli-SCENE.gif
  local scene="$1" c r cast="$tmp/$1.cast" gif="$out_dir/demo-cli-$1.gif"
  read -r c r < <(size_of "$scene")
  c="${cols:-$c}" r="${rows:-$r}"
  asciinema rec "$cast" --overwrite --quiet --idle-time-limit "$idle_limit" \
    -c "python $repo/scripts/demo_cli.py --scene $scene --cols $c --rows $r --speed $speed"
  echo  # the recorded session ends mid-line
  # asciinema 2.x records the size of the terminal it runs in, not the size the demo's
  # shell was given; fix the header so agg renders exactly --cols x --rows.
  python - "$cast" "$c" "$r" <<'EOF'
import json, sys
path, cols, rows = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
with open(path) as f:
    header, *events = f.readlines()
h = json.loads(header)
h.update(width=cols, height=rows)
with open(path, "w") as f:
    f.write(json.dumps(h) + "\n")
    f.writelines(events)
EOF
  agg --quiet --font-size "$font_size" --theme "$theme" "$cast" "$gif"
  echo "wrote $gif"
  if [[ $keep_cast -eq 1 ]]; then
    cp "$cast" "${gif%.gif}.cast"
    echo "wrote ${gif%.gif}.cast"
  fi
}

for scene in "${scenes[@]}"; do
  record "$scene"
done
