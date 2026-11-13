# Web demo

Try juxt's navigation right here — no install needed. The demo is a small JavaScript port of the viewer's core, running on the same synthetic plots as `examples/sample_config.yaml` (3 sensors × 2 dates × 2 overpasses × 2 sources).

<iframe src="../demo-app/index.html" title="juxt web demo"
        style="width:100%;height:70vh;min-height:480px;border:1px solid #2c2c2c;border-radius:4px"></iframe>

[Open the demo in its own tab](../demo-app/index.html){ target=_blank }. Click into the viewer first so it receives keystrokes.

## What's in the demo

- all three navigation modes — `tap`, `seek`, `pin` — switchable with `:mode`
- the focus stack and arrow keys, `Space` to flip to the previous position, `Home`/`End`, `1`–`9`
- `Ctrl`+letter value picker and fuzzy matching, as in the desktop app
- command mode (`:fit`, `:fit-height`, `:fit-width`, `:fit-off`, `:zoom N`, `:fullscreen`, `:switch-last`, `:info`, `:keys`, …)
- pan (drag), zoom (`Ctrl` + wheel), double-click to fit, and the status bar and info/shortcut sidebars

## What's not

The demo is not the desktop app: there are no config files, no grid view, no live reload, no SSH, and no loading of your own images. For that, [install juxt](installation.md).

Some browsers claim `Ctrl+Shift+I`/`K` for developer tools; use `:info` and `:keys` or the buttons in the demo's title bar instead.
