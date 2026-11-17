"""Compare benchmark runs and flag regressions.

    python benchmarks/compare.py base1.json base2.json -- new1.json new2.json

Each side may hold several runs of ``bench_desktop.py``; for every benchmark
the best (lowest) median across a side's runs is used, which filters out runs
disturbed by background load.  A benchmark regresses when the new value is
more than ``--threshold`` slower than the baseline *and* at least
``--min-delta-us`` slower in absolute terms (so a microbenchmark moving from
1.0µs to 1.2µs is reported, but not failed, when every keypress costs ~5ms).
Exit status 1 if any benchmark regresses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _best(paths: list[Path]) -> tuple[dict[str, float], list[str]]:
    best: dict[str, float] = {}
    revs = []
    for p in paths:
        data = json.loads(p.read_text())
        revs.append(data.get("meta", {}).get("git_rev", "?"))
        for name, s in data["bench"].items():
            if isinstance(s, dict):
                best[name] = min(best.get(name, float("inf")), s["median_us"])
    return best, revs


def _fmt(us: float) -> str:
    return f"{us:>9.2f}µs" if us < 100 else f"{us:>9.0f}µs"


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 usage="%(prog)s [options] BASE... -- NEW...  |  BASE NEW")
    ap.add_argument("--threshold", type=float, default=0.10,
                    help="relative slowdown that counts as a regression (default 0.10)")
    ap.add_argument("--min-delta-us", type=float, default=20.0,
                    help="absolute slowdown (µs) needed as well (default 20)")
    opts, files = ap.parse_known_args([a for a in argv if a != "--"])
    if "--" in argv:
        n_base = sum(1 for a in argv[:argv.index("--")] if a in files)
    elif len(files) == 2:
        n_base = 1
    else:
        ap.error("give BASE NEW, or BASE... -- NEW...")
    head = argparse.Namespace(files=[Path(f) for f in files[:n_base]],
                              threshold=opts.threshold, min_delta_us=opts.min_delta_us)
    tail = argparse.Namespace(files=[Path(f) for f in files[n_base:]])
    if not head.files or not tail.files:
        ap.error("both sides need at least one results file")
    base, base_revs = _best(head.files)
    new, new_revs = _best(tail.files)

    print(f"baseline: {', '.join(base_revs)}   new: {', '.join(new_revs)}")
    width = max(map(len, base | new))
    print(f"{'benchmark':<{width}}  {'baseline':>11}  {'new':>11}  {'change':>8}")
    regressions = []
    for name in base:
        if name not in new:
            continue
        b, n = base[name], new[name]
        change = (n - b) / b
        flag = ""
        if change > head.threshold:
            if n - b >= head.min_delta_us:
                flag = "  REGRESSION"
                regressions.append(name)
            else:
                flag = f"  (slower, but only {n - b:.2f}µs)"
        print(f"{name:<{width}}  {_fmt(b)}  {_fmt(n)}  {change:>+7.1%}{flag}")
    print()
    if regressions:
        print(f"{len(regressions)} regression(s) above {head.threshold:.0%} and "
              f"{head.min_delta_us:g}µs: {', '.join(regressions)}")
        return 1
    print(f"no regressions above {head.threshold:.0%} and {head.min_delta_us:g}µs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
