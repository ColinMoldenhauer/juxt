"""Auto-detect image axes from a directory without a config file."""
from __future__ import annotations
import sys
from pathlib import Path

from .config import Config, _auto_keys

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_CANDIDATE_SEPS = ["_", "-", ".", " "]
_MAX_VALS = 3

# Cyan, yellow, green, magenta, blue — readable on both light and dark terminals
_PALETTE = ["\033[96m", "\033[93m", "\033[92m", "\033[95m", "\033[94m"]
_RESET = "\033[0m"


def _score(parts_list: list[list[str]]) -> int:
    """Count columns with more than one distinct value (higher = better split)."""
    if not parts_list or len({len(p) for p in parts_list}) > 1:
        return 0
    n = len(parts_list[0])
    return sum(1 for i in range(n) if len({p[i] for p in parts_list}) > 1)


def detect_config(directory: str | Path, separator: str | None = None) -> tuple[Config, str]:
    """Build a Config by analysing image filenames in *directory*.

    If *separator* is None, tries common separators and picks whichever produces
    the most variable columns.  Raises ValueError if no usable pattern is found.

    Returns (config, separator) so callers can display the detected separator.
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

    return Config(template=template, axes=axes, keys=_auto_keys(axes)), separator


def _render_pattern(
    orig_template: str,
    all_axes: dict[str, list[str]],
    resolved: dict[str, str | None],
    axis_colors: dict[str, str],
) -> str:
    """Render the pattern for display.

    Pending axes → colored {v1|v2|v3|...}
    Named axes   → colored {name}
    Ignored axes → plain fixed value
    """
    result = orig_template
    for auto_name, values in all_axes.items():
        color = axis_colors.get(auto_name, "")
        reset = _RESET if color else ""
        if auto_name not in resolved:
            if len(values) <= _MAX_VALS:
                inline = "{" + "|".join(values) + "}"
            else:
                inline = "{" + "|".join(values[:_MAX_VALS]) + "|...}"
            result = result.replace(f"{{{auto_name}}}", f"{color}{inline}{reset}")
        elif resolved[auto_name] is not None:
            result = result.replace(f"{{{auto_name}}}", f"{color}{{{resolved[auto_name]}}}{reset}")
        else:
            result = result.replace(f"{{{auto_name}}}", values[0])
    return result


def prompt_rename(config: Config, n_files: int, separator: str) -> Config:
    """Interactively rename or ignore each auto-detected axis.

    Prints an initial colored pattern overview, then for each axis shows its
    values, reads a name (or Enter to ignore), and prints the updated pattern
    as feedback.  Raises ValueError if every axis is ignored.
    """
    use_col = sys.stdout.isatty()
    axis_colors: dict[str, str] = (
        {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(config.axes)}
        if use_col else {}
    )

    print(f"\nDetected {n_files} image{'s' if n_files != 1 else ''} — separator {separator!r}\n")
    print(f"  {_render_pattern(config.template, config.axes, {}, axis_colors)}\n")

    new_axes: dict[str, list[str]] = {}
    used_names: set[str] = set()
    template = config.template
    resolved: dict[str, str | None] = {}

    for auto_name, values in config.axes.items():
        color = axis_colors.get(auto_name, "")
        reset = _RESET if color else ""

        if len(values) <= _MAX_VALS:
            vals_display = "  ".join(values)
        else:
            vals_display = "  ".join(values[:_MAX_VALS]) + f"  … ({len(values)} total)"
        print(f"  {color}{auto_name}{reset}:  {vals_display}")

        try:
            answer = input("    name (Enter to ignore): ").strip()
        except EOFError:
            answer = ""

        if answer:
            if answer in used_names:
                raise ValueError(f"Duplicate axis name {answer!r}")
            used_names.add(answer)
            new_axes[answer] = values
            resolved[auto_name] = answer
            template = template.replace(f"{{{auto_name}}}", f"{{{answer}}}")
            print(f"    → {color}{answer!r}{reset}")
        else:
            fixed = values[0]
            resolved[auto_name] = None
            template = template.replace(f"{{{auto_name}}}", fixed)
            print(f"    → ignored (fixed to {fixed!r})")

        print(f"  {_render_pattern(config.template, config.axes, resolved, axis_colors)}\n")

    if not new_axes:
        raise ValueError("All axes were ignored — nothing to navigate")

    return Config(template=template, axes=new_axes, keys=_auto_keys(new_axes), mode=config.mode)
