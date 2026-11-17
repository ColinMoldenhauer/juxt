"""Tests for the Python side of the notebook widget (juxt.notebook)."""
from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("anywidget")

from juxt.config import Config, _auto_keys  # noqa: E402
from juxt.notebook import Viewer, _to_image  # noqa: E402

from conftest import FLAT_AXES, make_png  # noqa: E402


def _capture(v: Viewer):
    sent = []
    v.send = lambda content, buffers=None: sent.append((content, buffers or []))
    v._on_msg(v, {"type": "request_images", "id": "r1"}, [])
    return sent


@pytest.fixture
def flat_viewer(flat_config):
    return Viewer(flat_config, use_settings=False)


def test_notebook_does_not_import_qt():
    code = ("import sys, juxt.notebook; "
            "assert not any(m.startswith('PySide6') for m in sys.modules)")
    subprocess.run([sys.executable, "-c", code], check=True)


def test_from_config_object(flat_viewer):
    v = flat_viewer
    assert v.axis_names == ["sensor", "date"]
    assert v.axis_values == [["A", "B"], ["d1", "d2"]]
    assert v.pos == [0, 0]
    assert v.key_to_axis == {"s": 0, "d": 1}
    assert v.mode == "tap"


def test_from_yaml(tmp_path, flat_plot_dir):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"template: {flat_plot_dir.as_posix()}/{{sensor}}_{{date}}.png\n"
                   "axes:\n  sensor: [A, B]\n  date: [d1, d2]\nmode: pin\n")
    v = Viewer(str(cfg), use_settings=False)
    assert v.mode == "pin" and v.coords == {"sensor": "A", "date": "d1"}


def test_from_template_and_axes():
    v = Viewer(template="x/{a}_{b}.png", axes={"a": [1, 2], "b": ["p"]}, use_settings=False)
    assert v.axis_values == [["1", "2"], ["p"]]
    assert v.path == "x/1_p.png"


def test_requires_axes():
    with pytest.raises(ValueError):
        Viewer(template="x/{a}.png")


def test_remote_config_rejected():
    from juxt.config import RemoteConfig
    cfg = Config(template="/r/{a}.png", axes={"a": ["1"]}, keys={"a": "a"},
                 remote=RemoteConfig(host="h"))
    with pytest.raises(NotImplementedError):
        Viewer(cfg)


def test_goto_step_and_coords(flat_viewer):
    v = flat_viewer
    v.goto(date="d2")
    assert v.pos == [0, 1]
    v.coords = {"sensor": "B"}
    assert v.coords == {"sensor": "B", "date": "d2"}
    v.step("sensor")
    assert v.pos == [0, 1]
    with pytest.raises(KeyError):
        v.goto(nope="x")
    with pytest.raises(ValueError):
        v.goto(date="d9")


def test_pos_is_validated(flat_viewer):
    import traitlets
    with pytest.raises(traitlets.TraitError):
        flat_viewer.pos = [0, 5]
    with pytest.raises(traitlets.TraitError):
        flat_viewer.pos = [0]


def test_images_streamed_with_buffers(flat_viewer):
    sent = _capture(flat_viewer)
    assert sent[-1][0]["last"] is True
    items = [it for c, _ in sent for it in c["items"]]
    buffers = [b for _, bs in sent for b in bs]
    assert [it["key"] for it in items] == [[0, 0], [0, 1], [1, 0], [1, 1]]
    assert all(it["mime"] == "image/png" for it in items)
    assert buffers[0] == make_png()
    assert all(c["id"] == "r1" for c, _ in sent)


def test_missing_files_flagged(flat_config, flat_plot_dir):
    (flat_plot_dir / "B_d2.png").unlink()
    items = [it for c, _ in _capture(Viewer(flat_config, use_settings=False)) for it in c["items"]]
    assert [it.get("missing", False) for it in items] == [False, False, False, True]
    assert items[-1]["path"].endswith("B_d2.png")


def test_large_sets_are_chunked(flat_config, monkeypatch):
    import juxt.notebook as nb
    monkeypatch.setattr(nb, "_CHUNK_BYTES", 1)
    sent = _capture(Viewer(flat_config, use_settings=False))
    assert len(sent) >= 4 and [c["last"] for c, _ in sent][-1] is True
    assert sent[-1][0]["done_count"] == sent[-1][0]["total"] == 4


def test_ignores_unrelated_messages(flat_viewer):
    sent = []
    flat_viewer.send = lambda *a, **k: sent.append(a)
    flat_viewer._on_msg(flat_viewer, {"type": "other"}, [])
    flat_viewer._on_msg(flat_viewer, "junk", [])
    assert sent == []


def test_reload_bumps_revision(flat_viewer):
    r = flat_viewer._revision
    flat_viewer.reload()
    assert flat_viewer._revision == r + 1


def test_from_function_renders_each_combo():
    calls = []

    def plot(sensor, n):
        calls.append((sensor, n))
        if n == 2:
            return None
        return make_png(10 * n, 0, 0)

    v = Viewer.from_function(plot, sensor=["A", "B"], n=[1, 2])
    assert calls == [("A", 1), ("A", 2), ("B", 1), ("B", 2)]  # original types kept
    assert v.axis_values == [["A", "B"], ["1", "2"]]
    items = [it for c, _ in _capture(v) for it in c["items"]]
    assert [it.get("missing", False) for it in items] == [False, True, False, True]


def test_from_function_survives_exceptions():
    def plot(x):
        if x == "bad":
            raise RuntimeError("boom")
        return make_png()

    with pytest.warns(UserWarning, match="boom"):
        v = Viewer.from_function(plot, x=["ok", "bad"])
    items = [it for c, _ in _capture(v) for it in c["items"]]
    assert [it.get("missing", False) for it in items] == [False, True]


def test_to_image_matplotlib():
    mpl = pytest.importorskip("matplotlib")
    mpl.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([1, 2, 3])
    data, mime = _to_image(fig)
    assert mime == "image/png" and data[:8] == b"\x89PNG\r\n\x1a\n"
    assert not plt.get_fignums()  # figure was closed


def test_to_image_rejects_unknown():
    with pytest.raises(TypeError):
        _to_image(object())


def test_keys_override(flat_config):
    v = Viewer(flat_config, keys={"x": "date"}, use_settings=False)
    assert v.key_to_axis["x"] == 1


def test_mode_int(flat_config):
    assert Viewer(flat_config, mode=1, use_settings=False).mode == "seek"


def test_auto_keys_used_without_config():
    v = Viewer(template="{sensor}.png", axes={"sensor": ["a"]}, use_settings=False)
    assert v.key_to_axis == {ch: 0 for ch in _auto_keys({"sensor": ["a"]})}
    assert FLAT_AXES  # fixture module imported
