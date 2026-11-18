"""Tests for the Qt-free navigation core (juxt.core) and its JS port."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from juxt.core import Navigator, build_key_map, combo_paths, key_to_path
from juxt.fuzzy import fuzzy_filter, prefix_filter

ROOT = Path(__file__).resolve().parent.parent
# via the imported package, so the test also runs against an installed wheel
NAV_JS = Path(__import__("juxt").__file__).parent / "static" / "nav.js"


def nav3(**kw) -> Navigator:
    return Navigator(["a", "b", "c"], [["a0", "a1", "a2"], ["b0", "b1"], ["c0", "c1", "c2", "c3"]], **kw)


# ── import hygiene ──────────────────────────────────────────────────────────

def test_core_does_not_import_qt():
    code = "import sys, juxt.core; assert not any(m.startswith('PySide6') for m in sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True)


# ── Navigator semantics ─────────────────────────────────────────────────────

class TestNavigator:
    def test_initial_state(self):
        n = nav3()
        assert n.pos == [0, 0, 0] and n.prev is None
        assert n.h_axis() == 0 and n.v_axis() == 1
        assert n.coords == {"a": "a0", "b": "b0", "c": "c0"}
        assert n.key == (0, 0, 0) and n.n_axes == 3

    def test_step_wraps_and_records_prev(self):
        n = nav3()
        n.step(0, -1)
        assert n.pos == [2, 0, 0] and n.prev == [0, 0, 0]
        n.step(0, 1)
        assert n.pos == [0, 0, 0]

    def test_focus_reorders_arrow_axes(self):
        n = nav3()
        n.focus(2)
        assert n.focus_stack == [2, 0, 1]
        assert (n.h_axis(), n.v_axis()) == (2, 0)

    def test_single_axis_has_no_vertical(self):
        n = Navigator(["x"], [["1", "2"]])
        assert n.v_axis() is None
        assert n.swap_axes() is False

    def test_toggle_last(self):
        n = nav3()
        assert n.toggle_last() is False
        n.step(2, 1)
        assert n.toggle_last() and n.pos == [0, 0, 0] and n.prev == [0, 0, 1]
        assert n.toggle_last() and n.pos == [0, 0, 1]

    def test_first_last_jump_follow_h_axis(self):
        n = nav3()
        n.focus(2)
        n.last()
        assert n.pos == [0, 0, 3]
        n.first()
        assert n.pos == [0, 0, 0]
        assert n.jump(2) and n.pos == [0, 0, 2]
        assert not n.jump(4) and n.pos == [0, 0, 2]

    def test_set_value_records_prev_even_without_move(self):
        n = nav3()
        n.set_value(0, 0)
        assert n.prev == [0, 0, 0]

    def test_goto_keeps_history_when_not_moving(self):
        n = nav3()
        n.step(0, 1)
        n.goto(0, 1)
        assert n.prev == [0, 0, 0]
        assert n.goto(1, 1) and n.prev == [1, 0, 0] and n.focus_stack[0] == 1

    @pytest.mark.parametrize("axis,idx", [(-1, 0), (3, 0), (0, 3), (0, -1)])
    def test_goto_out_of_range_is_noop(self, axis, idx):
        n = nav3()
        assert n.goto(axis, idx) is False
        assert n.pos == [0, 0, 0] and n.prev is None

    def test_locks_override_focus(self):
        n = nav3(locked_h=2, locked_v=0)
        n.focus(1)
        assert (n.h_axis(), n.v_axis()) == (2, 0)
        assert n.swap_axes() and (n.h_axis(), n.v_axis()) == (0, 2)

    def test_swap_without_locks_swaps_focus(self):
        n = nav3()
        assert n.swap_axes() and n.focus_stack[:2] == [1, 0]

    def test_skip_missing(self):
        holes = {(1, 0, 0), (2, 0, 0)}
        n = nav3(is_missing=lambda k: k in holes, skip_missing=True)
        n.step(0, 1)
        assert n.pos == [0, 0, 0]  # every other value missing → stay put
        n.skip_missing = False
        n.step(0, 1)
        assert n.pos == [1, 0, 0]
        assert n.missing_along(0) == [False, True, True]

    def test_reset_clears_state(self):
        n = nav3(locked_h=1)
        n.step(0, 1)
        n.reset(["x"], [["1", "2"]], pos=[1])
        assert n.pos == [1] and n.prev is None and n.focus_stack == [0]
        assert n.locked_h is None


# ── helpers ─────────────────────────────────────────────────────────────────

def test_combo_paths_order_and_keys():
    out = list(combo_paths("{s}/{d}.png", {"s": ["A", "B"], "d": ["1", "2"]}))
    assert out == [((0, 0), "A/1.png"), ((0, 1), "A/2.png"), ((1, 0), "B/1.png"), ((1, 1), "B/2.png")]


def test_key_to_path_matches_combo_paths():
    axes = {"s": ["A", "B"], "d": ["1", "2", "3"]}
    for key, path in combo_paths("p/{s}_{d}.png", axes):
        assert key_to_path("p/{s}_{d}.png", list(axes), list(axes.values()), key) == path


def test_build_key_map_user_binding_wins():
    names, values = ["sensor", "source"], [["a"], ["b"]]
    assert build_key_map(names, values, {}) == {"s": 0, "o": 1}
    km = build_key_map(names, values, {"x": "source"})
    assert km["x"] == 1 and 1 not in [v for k, v in km.items() if k != "x"]


# ── JS parity ───────────────────────────────────────────────────────────────

def _random_scenarios(seed: int, count: int) -> list[dict]:
    rng = random.Random(seed)
    scenarios = []
    for _ in range(count):
        sizes = [rng.randint(1, 5) for _ in range(rng.randint(1, 4))]
        n_ax = len(sizes)
        all_keys = [[rng.randrange(s) for s in sizes] for _ in range(rng.randint(0, 6))]
        ops = []
        for _ in range(60):
            kind = rng.choice(["step", "step", "step", "focus", "goto", "first", "last",
                               "jump", "toggle", "swap", "lock", "missing_along"])
            ax = rng.randrange(n_ax)
            if kind == "step":
                ops.append(["step", ax, rng.choice([-2, -1, 1, 1, 3])])
            elif kind == "focus":
                ops.append(["focus", ax])
            elif kind == "goto":
                ops.append(["goto", rng.randint(-1, n_ax), rng.randint(-1, 5)])
            elif kind == "jump":
                ops.append(["jump", rng.randint(0, 8)])
            elif kind == "lock":
                pick = lambda: rng.choice([None, rng.randrange(n_ax)])  # noqa: E731
                ops.append(["lock", pick(), pick()])
            elif kind == "missing_along":
                ops.append(["missing_along", ax])
            else:
                ops.append([kind])
        scenarios.append({"sizes": sizes, "missing": all_keys,
                          "skip_missing": rng.random() < 0.5, "ops": ops})
    return scenarios


def _run_py(sc: dict) -> list[dict]:
    holes = {tuple(k) for k in sc["missing"]}
    values = [[f"v{i}" for i in range(s)] for s in sc["sizes"]]
    n = Navigator([f"ax{i}" for i in range(len(values))], values,
                  is_missing=lambda k: k in holes, skip_missing=sc["skip_missing"])
    trace = []
    for op, *a in sc["ops"]:
        r = None
        if op == "step":
            n.step(*a)
        elif op == "focus":
            n.focus(*a)
        elif op == "goto":
            r = n.goto(*a)
        elif op == "first":
            n.first()
        elif op == "last":
            n.last()
        elif op == "jump":
            r = n.jump(*a)
        elif op == "toggle":
            r = n.toggle_last()
        elif op == "swap":
            r = n.swap_axes()
        elif op == "lock":
            n.locked_h, n.locked_v = a
        elif op == "missing_along":
            r = n.missing_along(*a)
        trace.append({"r": r, "pos": list(n.pos), "prev": n.prev and list(n.prev),
                      "h": n.h_axis(), "v": n.v_axis(), "stack": list(n.focus_stack)})
    return trace


FILTER_CASES = [
    ("fh", ["fullscreen", "fit-height", "fit-width", "fit"]),
    ("sm", ["SMAP", "SMOS", "ASCAT", "sm"]),
    ("smp", ["SMAP", "SMOS", "ASCAT"]),
    ("", ["b", "a"]),
    ("2024", ["2024-03-15", "2023-2024", "x2024"]),
    ("am", ["AM", "PM", "amsr2", "gamma"]),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_parity():
    """The browser Navigator and filters behave exactly like the Python ones."""
    scenarios = _random_scenarios(seed=1234, count=200)
    payload = json.dumps({"scenarios": scenarios, "filters": FILTER_CASES})
    proc = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "run_nav.mjs"), str(NAV_JS)],
        input=payload, capture_output=True, text=True, check=True,
    )
    js = json.loads(proc.stdout)
    for i, sc in enumerate(scenarios):
        py = _run_py(sc)
        for step, (p, j) in enumerate(zip(py, js["traces"][i])):
            assert p == j, f"scenario {i} op {step} {sc['ops'][step]}: py={p} js={j}"
    for (q, pool), (jf, jp) in zip(FILTER_CASES, js["filters"]):
        assert jf == fuzzy_filter(q, pool), (q, pool)
        assert jp == prefix_filter(q, pool), (q, pool)
