"""Benchmarks for the juxt desktop viewer.

Measures the paths a user feels: preloading the hypercube at startup,
building the main window, and the latency of each navigation keypress in a
fully assembled ``MainWindow`` (status bar and info sidebar included, since
both refresh on every move).

    QT_QPA_PLATFORM=offscreen python benchmarks/bench_desktop.py --out new.json
    python benchmarks/compare.py baseline.json new.json

The script uses only juxt's public-ish entry points that exist both before and
after the core/notebook refactor, so it can be run against any revision (see
``benchmarks/README.md`` for the worktree recipe).
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from itertools import product
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

AXES = {
    "sensor": ["ASCAT", "SMAP", "SMOS"],
    "date": ["2024-03-15", "2024-03-16", "2024-03-17"],
    "overpass": ["AM", "PM"],
    "source": ["L2", "L3", "L4", "L5", "L6", "L7"],
}  # 3·3·2·6 = 108 plots, the CLAUDE.md target size
TEMPLATE = "{sensor}_{date}_{overpass}_{source}.png"
IMG_W, IMG_H = 1600, 1200


def _quiet_qt(_mode, _ctx, msg):
    # The offscreen platform warns on every window show; keep output readable.
    if "propagateSizeHints" not in msg:
        print(msg, file=sys.stderr)


def make_plots(directory: Path) -> str:
    """Write one synthetic, non-trivial PNG per combination (cached)."""
    directory.mkdir(parents=True, exist_ok=True)
    names = list(AXES)
    for i, combo in enumerate(product(*AXES.values())):
        path = directory / TEMPLATE.format(**dict(zip(names, combo)))
        if path.exists():
            continue
        img = QImage(IMG_W, IMG_H, QImage.Format_RGB32)
        img.fill(QColor(30 + (i * 37) % 200, 60, 90 + (i * 11) % 150))
        p = QPainter(img)
        p.setPen(QColor(240, 240, 240))
        for k in range(0, IMG_W, 40):  # some structure so PNG decode isn't free
            p.drawLine(k, 0, (k * 7 + i * 13) % IMG_W, IMG_H)
        p.drawText(40, 60, " ".join(combo))
        p.end()
        img.save(str(path))
    return (directory / TEMPLATE).as_posix()


def _stats(samples_s: list[float]) -> dict:
    us = sorted(s * 1e6 for s in samples_s)
    return {
        "n": len(us),
        "median_us": statistics.median(us),
        "p95_us": us[int(0.95 * (len(us) - 1))],
        "mean_us": statistics.fmean(us),
    }


def _time_keys(app, view, keys, n: int) -> dict:
    """Time *n* rounds of pressing *keys* (key, modifier) on *view*.

    Each sample covers key dispatch, ImageView navigation, the pixmap swap,
    the status-bar/sidebar refresh, and the event-loop flush.
    """
    samples = []
    for _ in range(n):
        for key, mod in keys:
            t0 = time.perf_counter()
            QTest.keyClick(view, key, mod)
            app.processEvents()
            samples.append(time.perf_counter() - t0)
    return _stats(samples)


def _git_rev(path: Path) -> str:
    """Short revision of the checkout containing *path*, '+dirty' if modified."""
    try:
        git = ["git", "-C", str(path)]
        rev = subprocess.check_output([*git, "rev-parse", "--short", "HEAD"],
                                      text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output([*git, "status", "--porcelain", "--", "."],
                                        text=True, stderr=subprocess.DEVNULL).strip()
        return rev + ("+dirty" if dirty else "")
    except Exception:
        return "unknown"


def run(rounds: int, repeats: int, plot_dir: Path) -> dict:
    import juxt
    from juxt.config import Config, _auto_keys
    from juxt.loader import preload
    from juxt.viewer import MainWindow, NavMode

    qInstallMessageHandler(_quiet_qt)
    app = QApplication.instance() or QApplication(sys.argv)
    template = make_plots(plot_dir)
    axes = {k: list(v) for k, v in AXES.items()}
    config = Config(template=template, axes=axes, keys=_auto_keys(axes))

    results: dict = {
        "meta": {
            "juxt_file": juxt.__file__,
            "git_rev": _git_rev(Path(juxt.__file__).parent),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "n_images": len(list(product(*AXES.values()))),
            "image_size": f"{IMG_W}x{IMG_H}",
            "rounds": rounds,
            "repeats": repeats,
        },
        "bench": {},
    }
    bench = results["bench"]

    # ── startup ─────────────────────────────────────────────────────────────
    t_pre = []
    for _ in range(repeats):
        gc.collect()
        t0 = time.perf_counter()
        pixmaps = preload(template, axes)
        t_pre.append(time.perf_counter() - t0)
    bench["startup.preload"] = _stats(t_pre)

    t_win = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        w = MainWindow(config, pixmaps, watch=False, show_info=True)
        w.resize(1200, 900)
        w.show()
        app.processEvents()
        t_win.append(time.perf_counter() - t0)
        w.close()
        w.deleteLater()
        app.processEvents()
    bench["startup.main_window"] = _stats(t_win)

    # ── navigation ──────────────────────────────────────────────────────────
    win = MainWindow(config, pixmaps, watch=False, show_info=True)
    win.resize(1200, 900)
    win.show()
    app.processEvents()
    view = win.view
    view.setFocus()
    letter = {name: ch for ch, name in config.keys.items()}

    def key_of(ch: str):
        return getattr(Qt, f"Key_{ch.upper()}")

    NM = Qt.NoModifier
    scenarios = {
        "nav.arrow_right": [(Qt.Key_Right, NM)],
        "nav.arrow_left": [(Qt.Key_Left, NM)],
        "nav.arrow_down": [(Qt.Key_Down, NM)],
        "nav.space_toggle": [(Qt.Key_Space, NM)],
        "nav.home_end": [(Qt.Key_Home, NM), (Qt.Key_End, NM)],
        "nav.digit_jump": [(Qt.Key_1, NM), (Qt.Key_3, NM)],
        "nav.tap_letter_fwd": [(key_of(letter["source"]), NM)],
        "nav.tap_letter_back": [(key_of(letter["source"]), Qt.ShiftModifier)],
    }
    # warm up caches / lazy imports
    _time_keys(app, view, [(Qt.Key_Right, NM)], 20)
    for name, keys in scenarios.items():
        bench[name] = _time_keys(app, view, keys, rounds)

    view.nav_mode = NavMode.PIN
    bench["nav.pin_focus_then_arrow"] = _time_keys(
        app, view, [(key_of(letter["sensor"]), NM), (Qt.Key_Right, NM),
                    (key_of(letter["source"]), NM), (Qt.Key_Right, NM)],
        rounds // 2,
    )
    view.nav_mode = NavMode.TAP

    # Bare state machine (no event dispatch): isolates the nav logic itself.
    samples = []
    h = view._h_axis()
    for _ in range(rounds * 4):
        t0 = time.perf_counter()
        view._navigate(h, 1)
        samples.append(time.perf_counter() - t0)
    bench["nav.navigate_direct"] = _stats(samples)

    # Navigation state only (no repaint): the pure-Python bookkeeping the
    # Navigator refactor touches — attribute access to pos/prev, axis lookup,
    # wrap-around step.  Timed in batches; reported per operation.
    samples = []
    batch = 200
    for _ in range(rounds):
        t0 = time.perf_counter()
        for _ in range(batch):
            a = view._h_axis()
            view.prev = list(view.pos)
            view.pos[a] = view._step(a, 1)
        samples.append((time.perf_counter() - t0) / batch)
    bench["nav.state_only"] = _stats(samples)

    win.close()
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", type=int, default=300, help="keypress rounds per scenario")
    ap.add_argument("--repeats", type=int, default=5, help="repeats for startup timings")
    ap.add_argument("--plots", type=Path,
                    default=Path(tempfile.gettempdir()) / "juxt-bench-plots",
                    help="directory for the synthetic PNGs (reused between runs)")
    ap.add_argument("--out", type=Path, help="write results JSON here")
    args = ap.parse_args(argv)

    res = run(args.rounds, args.repeats, args.plots)
    width = max(map(len, res["bench"]))
    print(f"juxt @ {res['meta']['git_rev']}  ({res['meta']['juxt_file']})")
    print(f"{'benchmark':<{width}}  {'median':>12}  {'p95':>12}")
    for name, s in res["bench"].items():
        print(f"{name:<{width}}  {s['median_us']:>10.1f}µs  {s['p95_us']:>10.1f}µs")
    if args.out:
        args.out.write_text(json.dumps(res, indent=2))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
