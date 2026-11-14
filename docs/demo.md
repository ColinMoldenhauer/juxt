# Web demo

Try juxt's navigation right here — no install needed. The demo is a small JavaScript port of the viewer's core, running on example datasets you can pick from.

<iframe src="../demo-app/index.html?theme=dark" title="juxt web demo"
        style="width:100%;height:75vh;min-height:520px;border:1px solid #2c2c2c;border-radius:4px"></iframe>

[Open the demo in its own tab](../demo-app/index.html){ target=_blank } · jump straight to a scenario with `demo-app/index.html#cities`, `#sentinel2`, `#reaction` or `#random`. Click into the viewer first so it receives keystrokes. A short guided tour of the interface starts on your first visit; replay it with the *tour* button or `:tour`. The ☀/☾ button (or `:theme`) switches between dark and light; the embedded copy starts dark to match these docs.

## Scenarios

| Scenario | Axes | What it shows |
|---|---|---|
| **Cities** | city × season × time (4×4×2) | New York, London, Paris and Berlin as pixel art, in every season, by day and by night. The skyline of a city never changes — only the weather and the light do — so flipping with `Space` shows you exactly what differs. |
| **Sentinel-2 satellite imagery** | city × season × layer (4×4×4) | Real [Copernicus Sentinel-2](https://sentinels.copernicus.eu/copernicus/sentinel-2) scenes of the same four cities, the least cloudy of each season in 2024, each in four band combinations: true colour, false colour (vegetation in red), NDVI and SWIR. |
| **Reaction–diffusion** | feed × kill × step (4×4×3) | A Gray–Scott simulation computed in your browser when you open it (a few seconds). Every run starts from the same seed, so the images differ only in the parameters — spots, worms, mazes and dead states from one set of equations. |
| **Random data** | sensor × date × overpass × source (3×2×2×2) | The sample dataset from the repo (`examples/sample_config.yaml`): coloured tiles that differ subtly along every axis. A sandbox for learning the keys. |

Switch with the *scenario* button in the title bar or with `:scenario NAME` (`:scenario` alone opens the picker).

## What's in the demo

- letter keys to step along an axis (`Shift` for backwards), the arrow keys, `Space` to flip to the previous position, `Home`/`End`, `1`–`9`
- `Ctrl`+letter value picker and fuzzy matching, as in the desktop app
- commands (`:fit`, `:fit-height`, `:fit-width`, `:fit-off`, `:zoom N`, `:fullscreen`, `:switch-last`, `:info`, `:keys`, …)
- pan (drag), zoom (`Ctrl` + wheel), double-click to fit, and the status bar and info/shortcut sidebars

## What's not

The demo is not the desktop app: there are no config files, no grid view, no live reload, no SSH, and no loading of your own images. For that, [install juxt](installation.md).

Some browsers claim `Ctrl+Shift+I`/`K` for developer tools; use `:info` and `:keys` or the buttons in the demo's title bar instead.

## Adding a scenario

Each scenario is one file in `docs/demo-app/scenarios/` that registers itself with `JUXT_SCENARIOS.push({...})`: an id, a title, the axes (name, key letter, values), the image size, a `thumb` (two coordinates to compare on the start screen, and their labels), and two functions — `load(progress)` to preload every image and `get(coords)` to return the image for one coordinate. Add a `<script>` tag for it in `docs/demo-app/index.html`, then run `node scripts/make_demo_thumbs.js` (needs Playwright) to render its start-screen thumbnails, and it shows up in the picker. The satellite images are rebuilt with `scripts/make_s2_demo.py`; they are committed to the repo, so the demo itself never contacts any external service.

Sentinel-2 imagery: contains modified Copernicus Sentinel data 2024, via Element 84 / AWS Open Data.
