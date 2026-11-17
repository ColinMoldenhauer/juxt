"""Qt-free navigation core shared by the desktop viewer and the notebook widget.

Nothing in this module may import PySide6: it is imported by
``juxt.notebook`` in kernels where Qt is not installed.

The hypercube is described by ``axis_names`` / ``axis_values``; a position is
a list of value indices, one per axis.  :class:`Navigator` holds the position,
the previous position (for the Spacebar toggle), the focus stack that decides
which axes the arrow keys drive, and optional axis locks.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from itertools import product


def combo_paths(
    template: str, axes: Mapping[str, Sequence[str]]
) -> Iterator[tuple[tuple[int, ...], str]]:
    """Yield ``(key, path)`` for every combination, in ``itertools.product`` order.

    *key* is the tuple of value indices; *path* is ``template.format(**values)``.
    """
    axis_names = list(axes.keys())
    axis_values = [list(v) for v in axes.values()]
    for combo in product(*axis_values):
        path = template.format(**dict(zip(axis_names, combo)))
        key = tuple(values.index(v) for values, v in zip(axis_values, combo))
        yield key, path


def key_to_path(template: str, axis_names: Sequence[str],
                axis_values: Sequence[Sequence[str]], key: Sequence[int]) -> str:
    """Substitute the values at *key* into *template* by plain replacement."""
    path = template
    for i, name in enumerate(axis_names):
        path = path.replace(f"{{{name}}}", axis_values[i][key[i]])
    return path


def build_key_map(
    axis_names: Sequence[str],
    axis_values: Sequence[Sequence[str]],
    user_keys: Mapping[str, str],
) -> dict[str, int]:
    """Letter → axis index, user bindings first, gaps filled by auto-assignment."""
    from .config import _auto_keys

    axes_dict = {name: list(axis_values[i]) for i, name in enumerate(axis_names)}

    # Start from auto-assigned keys so every axis gets a letter if possible
    merged: dict[str, str] = _auto_keys(axes_dict)

    # User-configured keys override auto assignments for their specific letters
    for ch, name in user_keys.items():
        if name in axis_names:
            merged[ch] = name

    # Remove any letter that ended up pointing at an axis that already has
    # a better (user-configured) letter, to avoid duplicate axis entries
    axis_to_ch: dict[str, str] = {}
    user_letters = {ch for ch in user_keys if user_keys[ch] in axis_names}
    for ch, name in list(merged.items()):
        if ch in user_letters:
            axis_to_ch[name] = ch          # user binding wins unconditionally
        elif name not in axis_to_ch:
            axis_to_ch[name] = ch          # first auto assignment wins

    names = list(axis_names)
    return {ch: names.index(name) for name, ch in axis_to_ch.items()}


class Navigator:
    """Position, history and axis focus for an N-dimensional plot hypercube.

    *is_missing* is called with a position tuple and reports whether that
    combination has no image; it is only consulted when *skip_missing* is on
    and by :meth:`missing_along`.
    """

    def __init__(
        self,
        axis_names: Sequence[str],
        axis_values: Sequence[Sequence[str]],
        *,
        is_missing: Callable[[tuple[int, ...]], bool] | None = None,
        skip_missing: bool = False,
        locked_h: int | None = None,
        locked_v: int | None = None,
    ):
        self.is_missing: Callable[[tuple[int, ...]], bool] = is_missing or (lambda _k: False)
        self.skip_missing = skip_missing
        self.reset(axis_names, axis_values)
        self.locked_h = locked_h
        self.locked_v = locked_v

    # ── structure ───────────────────────────────────────────────────────────

    def reset(
        self,
        axis_names: Sequence[str],
        axis_values: Sequence[Sequence[str]],
        pos: Sequence[int] | None = None,
    ) -> None:
        """Replace the axes; clears history, focus order and axis locks."""
        self.axis_names: list[str] = list(axis_names)
        # Shallow copy: the inner lists are shared with the caller (the
        # desktop viewer has always aliased Config.axes this way).
        self.axis_values: list[list[str]] = list(axis_values)
        self.pos: list[int] = list(pos) if pos is not None else [0] * len(self.axis_names)
        self.prev: list[int] | None = None
        # Most recently focused axis first; focus_stack[0] → ←/→, [1] → ↑/↓
        self.focus_stack: list[int] = list(range(len(self.axis_names)))
        self.locked_h: int | None = None
        self.locked_v: int | None = None

    @property
    def n_axes(self) -> int:
        return len(self.axis_names)

    @property
    def key(self) -> tuple[int, ...]:
        return tuple(self.pos)

    @property
    def coords(self) -> dict[str, str]:
        """Current position as ``{axis_name: value}``."""
        return {n: self.axis_values[i][self.pos[i]] for i, n in enumerate(self.axis_names)}

    # ── arrow-key bindings ──────────────────────────────────────────────────

    def h_axis(self) -> int:
        return self.locked_h if self.locked_h is not None else self.focus_stack[0]

    def v_axis(self) -> int | None:
        if self.locked_v is not None:
            return self.locked_v
        return self.focus_stack[1] if len(self.focus_stack) >= 2 else None

    def focus(self, axis: int) -> None:
        if axis in self.focus_stack:
            self.focus_stack.remove(axis)
        self.focus_stack.insert(0, axis)

    def swap_axes(self) -> bool:
        """Exchange the horizontal and vertical axes; False if there is no vertical."""
        h, v = self.h_axis(), self.v_axis()
        if v is None:
            return False
        if self.locked_h is not None or self.locked_v is not None:
            self.locked_h, self.locked_v = v, h
        else:
            self.focus_stack[0], self.focus_stack[1] = v, h
        return True

    # ── movement ────────────────────────────────────────────────────────────

    def missing_along(self, axis: int) -> list[bool]:
        """Per value of *axis*: is the combination (others held fixed) missing?"""
        probe = list(self.pos)
        flags = []
        for j in range(len(self.axis_values[axis])):
            probe[axis] = j
            flags.append(self.is_missing(tuple(probe)))
        return flags

    def step_target(self, axis: int, delta: int) -> int:
        """Index reached by stepping *delta* on *axis*, wrapping around.

        With skip_missing on, combinations without a file are passed over;
        if every other value on the axis is missing the position stays put.
        """
        n = len(self.axis_values[axis])
        cur = self.pos[axis]
        if not self.skip_missing:
            return (cur + delta) % n
        probe = list(self.pos)
        for i in range(1, n + 1):
            probe[axis] = (cur + delta * i) % n
            if not self.is_missing(tuple(probe)):
                return probe[axis]
        return cur

    def step(self, axis: int, delta: int) -> None:
        """Move *delta* along *axis* (wrapping), remembering the old position."""
        self.prev = list(self.pos)
        self.pos[axis] = self.step_target(axis, delta)

    def set_value(self, axis: int, val_idx: int) -> None:
        """Jump to *val_idx* on *axis*, always recording history (Home/End/digits)."""
        self.prev = list(self.pos)
        self.pos[axis] = val_idx

    def goto(self, axis: int, val_idx: int) -> bool:
        """Focus *axis* and jump to *val_idx*; history is kept only on a real move.

        Returns False (and does nothing) for out-of-range indices.
        """
        if not 0 <= axis < len(self.axis_values):
            return False
        if not 0 <= val_idx < len(self.axis_values[axis]):
            return False
        self.focus(axis)
        if self.pos[axis] != val_idx:
            self.prev = list(self.pos)
            self.pos[axis] = val_idx
        return True

    def first(self) -> None:
        self.set_value(self.h_axis(), 0)

    def last(self) -> None:
        h = self.h_axis()
        self.set_value(h, len(self.axis_values[h]) - 1)

    def jump(self, idx: int) -> bool:
        """Jump to the *idx*-th value of the horizontal axis (digit keys)."""
        h = self.h_axis()
        if idx < len(self.axis_values[h]):
            self.set_value(h, idx)
            return True
        return False

    def toggle_last(self) -> bool:
        """Swap current and previous position (Spacebar); False without history."""
        if self.prev is None:
            return False
        self.pos, self.prev = self.prev, list(self.pos)
        return True
