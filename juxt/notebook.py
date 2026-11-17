"""juxt as a Jupyter widget.

    from juxt.notebook import Viewer
    Viewer("config.yaml")                                  # a juxt config file
    Viewer(template="plots/{sensor}_{date}.png",
           axes={"sensor": ["ASCAT", "SMAP"], "date": ["d1", "d2"]})
    Viewer.from_function(make_plot, sensor=[...], date=[...])  # no files at all

Click the viewer to give it the keyboard, then navigate as in the desktop app:
arrows, Space, Home/End, 1–9, tap/pin/seek letters, Ctrl/Alt+letter for the
value picker, ``:`` for commands, wheel to zoom, drag to pan, double-click to
fit.  The current position is ``viewer.coords`` and can be set from Python.

Navigation runs entirely in the browser (``static/nav.js``, a port of
:class:`juxt.core.Navigator`): the kernel sends the compressed image bytes
once and is only told about position changes, so flipping never waits on a
kernel round trip.  Images travel as binary comm messages rather than synced
state, so they are not embedded in the ``.ipynb`` when widget state is saved.

This module must not import PySide6; it needs ``pip install juxt[notebook]``.
"""
from __future__ import annotations

import io
import mimetypes
import warnings
from collections.abc import Callable, Mapping, Sequence
from itertools import product
from pathlib import Path

try:
    import anywidget
    import traitlets as t
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "juxt.notebook needs anywidget. Install it with:\n"
        "    pip install juxt[notebook]"
    ) from e

from .config import Config, _auto_keys, load_config
from .core import Navigator, build_key_map, combo_paths

_STATIC = Path(__file__).parent / "static"
_MODES = ("tap", "seek", "pin")
# Images are streamed in batches of about this many bytes, so no single comm
# message gets near the size limits some front ends (and proxies) impose.
_CHUNK_BYTES = 8 * 1024 * 1024

ImageData = tuple[bytes, str]  # (encoded bytes, MIME type)


def _read_file(path: str) -> ImageData | None:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return data, mime


def _to_image(obj) -> ImageData:
    """Encode what a plotting function returned as image bytes."""
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return bytes(obj), "image/png"
    if hasattr(obj, "savefig"):  # matplotlib Figure
        buf = io.BytesIO()
        obj.savefig(buf, format="png", bbox_inches="tight")
        try:
            import matplotlib.pyplot as plt
            plt.close(obj)
        except Exception:
            pass
        return buf.getvalue(), "image/png"
    if hasattr(obj, "_repr_png_"):  # IPython display protocol, PIL, ...
        data = obj._repr_png_()
        if data:
            return data, "image/png"
    if hasattr(obj, "save"):  # PIL.Image without _repr_png_
        buf = io.BytesIO()
        obj.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    if isinstance(obj, (str, Path)):
        img = _read_file(str(obj))
        if img is None:
            raise FileNotFoundError(obj)
        return img
    raise TypeError(
        f"cannot turn {type(obj).__name__} into an image; return a matplotlib "
        "Figure, PIL image, PNG bytes or a file path"
    )


def _load_settings():
    try:
        from .settings import load_settings
        return load_settings(write=False)
    except Exception:
        return None


class Viewer(anywidget.AnyWidget):
    """Navigate an N-dimensional hypercube of plots inside a notebook."""

    _esm = (_STATIC / "nav.js").read_text() + "\n" + (_STATIC / "widget.js").read_text()
    _css = _STATIC / "widget.css"

    axis_names = t.List(t.Unicode()).tag(sync=True)
    axis_values = t.List(t.List(t.Unicode())).tag(sync=True)
    key_to_axis = t.Dict().tag(sync=True)
    pos = t.List(t.Int()).tag(sync=True)
    mode = t.Enum(_MODES, default_value="tap").tag(sync=True)
    height = t.Int(500).tag(sync=True)
    fit = t.Unicode("image", allow_none=True).tag(sync=True)
    show_info = t.Bool(True).tag(sync=True)
    skip_missing = t.Bool(False).tag(sync=True)
    fuzzy = t.Bool(True).tag(sync=True)
    greedy = t.Bool(True).tag(sync=True)
    axis_h = t.Unicode(None, allow_none=True).tag(sync=True)
    axis_v = t.Unicode(None, allow_none=True).tag(sync=True)
    # Bumped whenever the images change, which makes every view re-request them.
    _revision = t.Int(0).tag(sync=True)

    def __init__(
        self,
        config: str | Path | Config | None = None,
        *,
        template: str | None = None,
        axes: Mapping[str, Sequence] | None = None,
        keys: Mapping[str, str] | None = None,
        mode: str | int | None = None,
        images: Mapping[tuple[int, ...], ImageData | None] | None = None,
        use_settings: bool = True,
        **kwargs,
    ):
        if isinstance(config, (str, Path)):
            config = load_config(str(config))
        if config is None:
            if axes is None or (template is None and images is None):
                raise ValueError("pass a config, or template= and axes=")
            ax = {str(k): [str(v) for v in vs] for k, vs in axes.items()}
            config = Config(template=template or "", axes=ax,
                            keys=dict(keys) if keys else _auto_keys(ax))
        elif keys:
            config.keys = dict(keys)
        if config.remote is not None:
            raise NotImplementedError(
                "remote (SSH) configs are not supported in the notebook widget; "
                "run the kernel on the machine that has the plots instead"
            )
        if not config.axes:
            raise ValueError("no axes")

        if use_settings and (s := _load_settings()) is not None:
            kwargs.setdefault("fuzzy", s.seek_fuzzy)
            kwargs.setdefault("greedy", s.seek_greedy)
            kwargs.setdefault("skip_missing", s.skip_missing)
        if mode is None:
            mode = config.mode
        if isinstance(mode, int):
            mode = _MODES[mode]

        self.config = config
        self._images = dict(images) if images is not None else None
        self._nav = Navigator(list(config.axes), list(config.axes.values()))
        super().__init__(
            axis_names=list(config.axes),
            axis_values=[list(v) for v in config.axes.values()],
            key_to_axis=build_key_map(list(config.axes), list(config.axes.values()), config.keys),
            pos=[0] * len(config.axes),
            mode=mode,
            **kwargs,
        )
        self.on_msg(self._on_msg)

    # ── construction helpers ───────────────────────────────────────────────

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., object],
        axes: Mapping[str, Sequence] | None = None,
        /,
        **axis_values: Sequence,
    ) -> "Viewer":
        """Render ``fn(**coords)`` for every combination and view the results.

        *fn* may return a matplotlib Figure (closed after rendering), a PIL
        image, PNG bytes or a file path.  If it raises or returns None, that
        combination is shown as missing.

            Viewer.from_function(plot, sensor=["ASCAT", "SMAP"], date=dates)
        """
        ax = dict(axes or {}) | axis_values
        if not ax:
            raise ValueError("give at least one axis, e.g. sensor=[...]")
        ax = {k: [str(v) for v in vs] for k, vs in ax.items()}
        originals = {k: list(vs) for k, vs in (dict(axes or {}) | axis_values).items()}
        images: dict[tuple[int, ...], ImageData | None] = {}
        names = list(ax)
        for key in product(*(range(len(v)) for v in ax.values())):
            kwargs = {n: originals[n][i] for n, i in zip(names, key)}
            try:
                out = fn(**kwargs)
                images[key] = None if out is None else _to_image(out)
            except Exception as e:  # keep going; show the hole
                images[key] = None
                warnings.warn(f"{fn.__name__}({kwargs}) failed: {e!r}", stacklevel=2)
        template = "{" + "}_{".join(names) + "}"
        return cls(template=template, axes=ax, images=images)

    # ── Python API ─────────────────────────────────────────────────────────

    @property
    def coords(self) -> dict[str, str]:
        """Current position as ``{axis: value}``; assign a dict to move."""
        return {n: self.axis_values[i][p] for i, (n, p) in enumerate(zip(self.axis_names, self.pos))}

    @coords.setter
    def coords(self, value: Mapping[str, object]) -> None:
        self.goto(**value)

    def goto(self, **coords) -> None:
        """Jump to the given values, e.g. ``viewer.goto(sensor="SMAP")``."""
        self._nav.pos = list(self.pos)
        for name, val in coords.items():
            if name not in self.axis_names:
                raise KeyError(f"unknown axis {name!r}; axes are {self.axis_names}")
            i = self.axis_names.index(name)
            vals = self.axis_values[i]
            if str(val) not in vals:
                raise ValueError(f"{val!r} is not a value of {name!r}: {vals}")
            self._nav.set_value(i, vals.index(str(val)))
        self.pos = list(self._nav.pos)

    def step(self, axis: str, delta: int = 1) -> None:
        """Move *delta* along *axis*, wrapping, as an arrow key would."""
        self._nav.pos = list(self.pos)
        self._nav.step(self.axis_names.index(axis), delta)
        self.pos = list(self._nav.pos)

    def reload(self) -> None:
        """Re-read every image from disk (template mode) and refresh the views."""
        self._revision += 1

    @property
    def path(self) -> str | None:
        """File shown at the current position (template mode only)."""
        if self._images is not None:
            return None
        return self.config.template.format(**self.coords)

    # ── comm: image transfer ───────────────────────────────────────────────

    def _iter_images(self):
        if self._images is not None:
            names = self.axis_names
            for key in product(*(range(len(v)) for v in self.axis_values)):
                label = "  ".join(f"{n}={self.axis_values[i][k]}" for i, (n, k) in enumerate(zip(names, key)))
                yield key, label, self._images.get(key)
        else:
            for key, path in combo_paths(self.config.template, self.config.axes):
                yield key, path, _read_file(path)

    def _on_msg(self, _widget, content, _buffers) -> None:
        if not isinstance(content, dict) or content.get("type") != "request_images":
            return
        rid = content.get("id")
        all_items = list(self._iter_images())
        total = len(all_items)
        items, buffers, size, done = [], [], 0, 0

        def flush(last: bool):
            nonlocal items, buffers, size
            self.send({"type": "images", "id": rid, "items": items, "last": last,
                       "done_count": done, "total": total}, buffers)
            items, buffers, size = [], [], 0

        for key, path, img in all_items:
            done += 1
            if img is None:
                items.append({"key": list(key), "path": path, "missing": True})
            else:
                data, mime = img
                items.append({"key": list(key), "path": path, "mime": mime})
                buffers.append(data)
                size += len(data)
            if size >= _CHUNK_BYTES:
                flush(last=False)
        flush(last=True)

    @t.validate("pos")
    def _valid_pos(self, proposal):
        p = list(proposal["value"])
        if self.axis_values and len(p) != len(self.axis_values):
            raise t.TraitError(f"pos needs {len(self.axis_values)} entries")
        for i, (k, vals) in enumerate(zip(p, self.axis_values)):
            if not 0 <= k < len(vals):
                raise t.TraitError(f"pos[{i}]={k} out of range for {self.axis_names[i]!r}")
        return p


__all__ = ["Viewer"]
