"""Auto-detect image axes from a directory without a config file."""
from __future__ import annotations
from pathlib import Path

from .config import Config, _auto_keys

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_CANDIDATE_SEPS = ["_", "-", ".", " "]


def _score(parts_list: list[list[str]]) -> int:
    """Count columns with more than one distinct value (higher = better split)."""
    if not parts_list or len({len(p) for p in parts_list}) > 1:
        return 0
    n = len(parts_list[0])
    return sum(1 for i in range(n) if len({p[i] for p in parts_list}) > 1)


def detect_config(directory: str | Path, separator: str | None = None) -> Config:
    """Build a Config by analysing image filenames in *directory*.

    If *separator* is None, tries common separators and picks whichever produces
    the most variable columns.  Raises ValueError if no usable pattern is found.
    """
    directory = Path(directory)
    files = sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )
    if not files:
        raise ValueError(f"No image files found in {str(directory)!r}")

    stems = [f.stem for f in files]

    if separator is None:
        best_sep, best_score = "_", -1
        for sep in _CANDIDATE_SEPS:
            score = _score([s.split(sep) for s in stems])
            if score > best_score:
                best_score, best_sep = score, sep
        separator = best_sep

    ext = files[0].suffix
    parts_list = [s.split(separator) for s in stems]
    n_cols = len(parts_list[0])
    if any(len(p) != n_cols for p in parts_list):
        raise ValueError(
            f"Filenames have inconsistent number of parts when split on {separator!r}"
        )

    axes: dict[str, list[str]] = {}
    col_axis: dict[int, str] = {}
    for i in range(n_cols):
        values = list(dict.fromkeys(p[i] for p in parts_list))
        if len(values) > 1:
            name = f"axis_{i}"
            axes[name] = values
            col_axis[i] = name

    if not axes:
        raise ValueError(
            "No variable columns found — all filenames look identical after splitting"
        )

    template_parts = [
        f"{{{col_axis[i]}}}" if i in col_axis else parts_list[0][i]
        for i in range(n_cols)
    ]
    template = str(directory / (separator.join(template_parts) + ext))

    return Config(template=template, axes=axes, keys=_auto_keys(axes))
