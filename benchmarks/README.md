# Benchmarks

The scripts in this folder serve two purposes:

- checking that a change does not make the desktop viewer slower
- measuring the notebook widget end to end in a real JupyterLab

## Desktop: `bench_desktop.py`

This script builds 108 synthetic 1600×1200 plots (the size of the target use
case) and times:

- **startup**: preloading all plots into pixmaps, and building the main window
- **navigation**: the latency of each kind of keypress in a complete
  `MainWindow`. This includes key dispatch, the image swap, and the refresh of
  the status bar and info sidebar.
- **nav.navigate_direct**: a single navigate-and-refresh step, without the key event
- **nav.state_only**: the navigation bookkeeping alone, with no repaint

It only uses entry points that exist both before and after the
`juxt.core` refactor, so you can run it against any revision.

### Comparing a branch with `main`

```bash
git worktree add /tmp/juxt-main main
```

Run the two sides alternately, a few times each, so that load on the machine
affects both equally:

```bash
for i in 1 2 3; do
  (cd /tmp && PYTHONPATH=/tmp/juxt-main python "$OLDPWD/benchmarks/bench_desktop.py" --out "$OLDPWD/base_$i.json")
  python benchmarks/bench_desktop.py --out new_$i.json
done
```

```bash
python benchmarks/compare.py base_{1,2,3}.json -- new_{1,2,3}.json
```

`compare.py` takes the best median from each side's runs. It reports a
regression only when the new code is more than 10% **and** more than 20 µs
slower (change these with `--threshold` and `--min-delta-us`). It exits with
status 1 if it finds one.

On a shared or virtualised machine, separate runs of the *same* code can
differ by 10–25%. Don't trust results from a single run.

## Notebook: `bench_notebook.py`

This script writes a notebook that shows `juxt.notebook.Viewer` over the same
108 plots, starts a headless JupyterLab, and drives it with Playwright
(`notebook_e2e.mjs`). It checks that:

- the widget takes keyboard focus
- Jupyter's command-mode shortcuts (`a`, `b`, `dd`, `m`, arrows) do not touch
  the notebook while the widget has focus
- every navigation key does what it does on the desktop
- the position syncs both ways between the browser and the kernel

It then times key → new image in the browser.

```bash
pip install "juxt[notebook]" jupyterlab
```

```bash
NODE_PATH=$(npm root -g) python benchmarks/bench_notebook.py --out nb.json
```

The script needs Node.js with `playwright` installed. It exits with a non-zero
status if any check fails.

The in-page timings come from `performance.now()`, which Chromium rounds to
100 µs on pages that are not cross-origin isolated. Read the sub-millisecond
figures as approximate.
