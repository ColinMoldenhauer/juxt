"""End-to-end check and benchmark of the notebook widget in headless JupyterLab.

Writes a notebook that displays ``juxt.notebook.Viewer`` over the same 108
synthetic plots as ``bench_desktop.py``, starts JupyterLab, and drives it with
Playwright (``notebook_e2e.mjs``): keyboard capture, Jupyter shortcut
suppression, navigation semantics, two-way position sync and flip latency.

    python benchmarks/bench_notebook.py --out nb.json

Needs: ``pip install juxt[notebook] jupyterlab``, Node.js with ``playwright``
and a Chromium it can launch.  Exits non-zero if any check fails.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from itertools import product
from pathlib import Path

import nbformat

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Same plot set as bench_desktop.py (kept in sync by hand so that importing
# it, and with it PySide6, is only needed to generate missing PNGs).
AXES = {
    "sensor": ["ASCAT", "SMAP", "SMOS"],
    "date": ["2024-03-15", "2024-03-16", "2024-03-17"],
    "overpass": ["AM", "PM"],
    "source": ["L2", "L3", "L4", "L5", "L6", "L7"],
}
TEMPLATE = "{sensor}_{date}_{overpass}_{source}.png"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def write_notebook(path: Path, template: str, axes: dict) -> None:
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [
        nbformat.v4.new_code_cell(
            "from juxt.notebook import Viewer\n"
            f"v = Viewer(template={template!r}, axes={axes!r}, use_settings=False, height=450)\n"
            "v"
        ),
        nbformat.v4.new_code_cell('print("POS", v.pos)'),
        nbformat.v4.new_code_cell('v.goto(sensor="SMOS", source="L4"); print("GOTO", v.pos)'),
    ]
    nbformat.write(nb, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rounds", type=int, default=300)
    ap.add_argument("--plots", type=Path, default=Path(tempfile.gettempdir()) / "juxt-bench-plots")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--screenshot", type=Path, help="save a screenshot of the widget here")
    args = ap.parse_args(argv)

    template = (args.plots / TEMPLATE).as_posix()
    names = list(AXES)
    if not all((args.plots / TEMPLATE.format(**dict(zip(names, c)))).exists()
               for c in product(*AXES.values())):
        # Drawing the synthetic PNGs needs Qt; once they exist the benchmark
        # also runs in a Qt-free (juxt-notebook) environment.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from bench_desktop import make_plots
        from PySide6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        template = make_plots(args.plots)

    work = Path(tempfile.mkdtemp(prefix="juxt-nb-"))
    write_notebook(work / "bench.ipynb", template, AXES)
    port, token = _free_port(), "juxtbench"
    server = subprocess.Popen(
        [sys.executable, "-m", "jupyter", "lab", "--no-browser", f"--port={port}",
         f"--IdentityProvider.token={token}", f"--ServerApp.root_dir={work}",
         "--ServerApp.ip=127.0.0.1", "--allow-root", "--LabApp.news_url=", "--LabApp.check_for_updates_class=jupyterlab.NeverCheckForUpdate"],
        stdout=subprocess.DEVNULL, stderr=open(work / "server.log", "w"),
    )
    try:
        base = f"http://127.0.0.1:{port}"
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(120):
            try:
                direct.open(f"{base}/api/status?token={token}", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("JupyterLab did not start; see " + str(work / "server.log"))

        env = dict(os.environ)
        env["JUXT_E2E_SCREENSHOT"] = str(work / "failure.png")
        if args.screenshot:
            env["JUXT_E2E_SCREENSHOT_OK"] = str(args.screenshot.resolve())
        url = f"{base}/lab/tree/bench.ipynb?token={token}"
        proc = subprocess.run(["node", str(HERE / "notebook_e2e.mjs"), url, str(args.rounds)],
                              capture_output=True, text=True, env=env, timeout=900)
        if proc.returncode != 0 or not proc.stdout.strip():
            print(proc.stderr, file=sys.stderr)
            raise RuntimeError("e2e script failed")
        res = json.loads(proc.stdout)
    finally:
        server.terminate()
        server.wait(timeout=30)

    res["meta"] = {"n_images": 108, "rounds": args.rounds, "workdir": str(work)}
    ok = bool(res["checks"]) and all(res["checks"].values()) and not res["errors"]
    print("checks:")
    for name, passed in res["checks"].items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    if res["errors"]:
        print("errors:")
        for e in res["errors"]:
            print("  " + e)
    if res["bench"]:
        width = max(map(len, res["bench"]))
        print(f"{'benchmark':<{width}}  {'median':>12}  {'p95':>12}")
        for name, s in res["bench"].items():
            if isinstance(s, dict):
                print(f"{name:<{width}}  {s['median_us']:>10.1f}µs  {s['p95_us']:>10.1f}µs")
            else:
                print(f"{name:<{width}}  {s:>10.0f}ms")
    if args.out:
        args.out.write_text(json.dumps(res, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
