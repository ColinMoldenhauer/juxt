"""Auto-detect image axes from a directory without a config file."""
from __future__ import annotations
import re
import sys
from pathlib import Path

from .config import Config, _auto_keys

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_DEFAULT_SEPS = ["_", "/"]   # underscore + path separator (always normalised to /)
_MAX_VALS = 3

# Cyan, yellow, green, magenta, blue — readable on both light and dark terminals
_PALETTE = ["\033[96m", "\033[93m", "\033[92m", "\033[95m", "\033[94m"]
_RESET = "\033[0m"



def _split_with_seps(stem: str, separators: list[str]) -> tuple[list[str], list[str]]:
    """Split *stem* on any of *separators*, returning (tokens, seps_between_tokens).

    The returned seps list has len(tokens) - 1 entries, preserving which
    separator appeared between each adjacent pair so the template can be
    reconstructed exactly.
    """
    if len(separators) == 1:
        tokens = stem.split(separators[0])
        return tokens, [separators[0]] * (len(tokens) - 1)
    pattern = "(" + "|".join(re.escape(s) for s in sorted(separators, key=len, reverse=True)) + ")"
    parts = re.split(pattern, stem)
    return parts[0::2], parts[1::2]


def _seps_display(separators: list[str]) -> str:
    return " ".join(repr(s) for s in separators)


def _iter_images(directory: Path, max_depth: int | None = None) -> list[Path]:
    """Recursively collect image files, optionally limited to *max_depth* levels."""
    result: list[Path] = []

    def _walk(d: Path, depth: int) -> None:
        if max_depth is not None and depth > max_depth:
            return
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS:
                result.append(f)
            elif f.is_dir():
                _walk(f, depth + 1)

    _walk(directory, 0)
    return result


def detect_config(
    directory: str | Path,
    separators: list[str] | None = None,
    max_depth: int | None = None,
) -> tuple[Config, list[str]]:
    """Build a Config by analysing image filenames under *directory*.

    Discovery is recursive up to *max_depth* levels (None = unlimited).
    Relative paths from *directory* are used as the stems to split, so
    directory structure becomes part of the axis space automatically.

    Separators default to ['_', '/'] (underscore + path separator).
    Multiple separators split on any of the given characters, preserving
    which one sat between each token pair so the template reconstructs exactly.

    Returns (config, separators).
    """
    directory = Path(directory)
    files = _iter_images(directory, max_depth)
    if not files:
        raise ValueError(f"No image files found under {str(directory)!r}")

    # Relative paths without extension, normalised to forward slashes
    rel_stems = [
        str(f.relative_to(directory).with_suffix("")).replace("\\", "/")
        for f in files
    ]

    if separators is None:
        separators = _DEFAULT_SEPS
    # Normalise any backslash path separators supplied by the caller
    separators = [s.replace("\\", "/") for s in separators]

    ext = files[0].suffix
    splits = [_split_with_seps(s, separators) for s in rel_stems]
    all_tokens = [t for t, _ in splits]
    ref_between = splits[0][1]  # separators from the first file define the template

    n_cols = len(all_tokens[0])
    if any(len(t) != n_cols for t in all_tokens):
        raise ValueError(
            f"Filenames have inconsistent number of parts when split on {_seps_display(separators)}"
        )

    axes: dict[str, list[str]] = {}
    col_axis: dict[int, str] = {}
    for i in range(n_cols):
        values = list(dict.fromkeys(t[i] for t in all_tokens))
        if len(values) > 1:
            name = f"axis_{i}"
            axes[name] = values
            col_axis[i] = name

    if not axes:
        raise ValueError(
            "No variable columns found — all filenames look identical after splitting"
        )

    template_tokens = [
        f"{{{col_axis[i]}}}" if i in col_axis else all_tokens[0][i]
        for i in range(n_cols)
    ]
    template_stem = template_tokens[0]
    for i, between in enumerate(ref_between):
        template_stem += between + template_tokens[i + 1]

    dir_str = str(directory).replace("\\", "/")
    template = f"{dir_str}/{template_stem}{ext}"

    return Config(template=template, axes=axes, keys=_auto_keys(axes)), separators


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


def prompt_rename(config: Config, n_files: int, separators: list[str], directory: Path, max_depth: int | None = None) -> Config:
    """Interactively rename or ignore each auto-detected axis.

    First prompts to confirm or change the separator(s) (re-detecting on
    change), then for each axis shows its values, reads a name (or Enter to
    ignore), and prints the updated pattern as feedback.  Multiple separators
    are entered space-separated (e.g. `_ -`).  Raises ValueError if every axis
    is ignored.
    """
    use_col = sys.stdout.isatty()

    def _make_colors(axes: dict) -> dict[str, str]:
        return (
            {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(axes)}
            if use_col else {}
        )

    print(f"\nDetected {n_files} image{'s' if n_files != 1 else ''}\n")

    # --- Separator selection ---
    while True:
        axis_colors = _make_colors(config.axes)
        print(f"  separator {_seps_display(separators)} → {_render_pattern(config.template, config.axes, {}, axis_colors)}")
        try:
            new_sep_str = input("  Change separator(s) (Enter to keep): ").strip()
        except EOFError:
            new_sep_str = ""
        except KeyboardInterrupt:
            print()
            sys.exit(0)
        if not new_sep_str:
            print()
            break
        new_seps = new_sep_str.split()
        try:
            config, separators = detect_config(directory, new_seps, max_depth)
        except ValueError as e:
            print(f"  {e}")

    axis_colors = _make_colors(config.axes)
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
        except KeyboardInterrupt:
            print()
            sys.exit(0)

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
