"""Auto-detect image axes from a directory, local template, or remote path."""
from __future__ import annotations

import glob as _glob_mod
import logging
import math
import re
import sys
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable

from .complete import PLACEHOLDER_NAME_RE
from .config import Config, RemoteConfig, _auto_keys

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_DEFAULT_SEPS = ["_", "/"]   # underscore + path separator (always normalised to /)
_MAX_VALS = 3          # max values shown inline in the {v1|v2|...} path display
_MAX_VALS_DISPLAY = 10  # max values listed per axis in the naming dialogue

# Cyan, yellow, green, magenta, blue — readable on both light and dark terminals
_PALETTE = ["\033[96m", "\033[93m", "\033[92m", "\033[95m", "\033[94m"]
_RESET = "\033[0m"


def _numeric_sort(values: list[str]) -> list[str]:
    """Re-sort *values* numerically if every element parses as a finite number."""
    try:
        nums = [float(v) for v in values]
    except ValueError:
        return values
    if any(not math.isfinite(n) for n in nums):
        return values
    return [v for _, v in sorted(zip(nums, values))]


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


def _detect_from_rel_stems(
    rel_stems: list[str],
    ext: str,
    dir_prefix: str,
    separators: list[str],
) -> tuple[Config, list[str]]:
    """Core axis-detection algorithm given normalised relative stems (no extension).

    *dir_prefix* is prepended to the generated template (local dir or remote path).
    Returns (config, separators).
    """
    splits = [_split_with_seps(s, separators) for s in rel_stems]
    all_tokens = [t for t, _ in splits]
    ref_between = splits[0][1]

    n_cols = len(all_tokens[0])
    if any(len(t) != n_cols for t in all_tokens):
        raise ValueError(
            f"Filenames have inconsistent number of parts when split on {_seps_display(separators)}"
        )

    axes: dict[str, list[str]] = {}
    col_axis: dict[int, str] = {}
    for i in range(n_cols):
        values = _numeric_sort(list(dict.fromkeys(t[i] for t in all_tokens)))
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

    template = f"{dir_prefix}/{template_stem}{ext}"
    return Config(template=template, axes=axes, keys=_auto_keys(axes)), separators


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

    rel_stems = [
        str(f.relative_to(directory).with_suffix("")).replace("\\", "/")
        for f in files
    ]
    if separators is None:
        separators = _DEFAULT_SEPS
    separators = [s.replace("\\", "/") for s in separators]
    ext = files[0].suffix
    dir_str = str(directory).replace("\\", "/")
    return _detect_from_rel_stems(rel_stems, ext, dir_str, separators)


def detect_config_remote(
    remote_dir: str,
    remote: RemoteConfig,
    separators: list[str] | None = None,
    get_password: Callable[[str], str | None] | None = None,
) -> tuple[Config, list[str], int]:
    """Build a Config by analysing image filenames in a remote directory via SFTP.

    Returns (config_with_remote_set, separators, n_files).
    The template in the returned config uses absolute remote paths so that
    preload_remote can download files without modification.
    """
    import stat
    from .loader import _connect_sftp

    base = remote_dir.rstrip('/')
    client, sftp = _connect_sftp(remote, get_password)
    try:
        remote_files: list[str] = []
        stack = [base]
        while stack:
            path = stack.pop()
            try:
                entries = sftp.listdir_attr(path)
            except IOError:
                continue
            for entry in sorted(entries, key=lambda e: e.filename):
                full = f"{path}/{entry.filename}"
                if stat.S_ISDIR(entry.st_mode):
                    stack.append(full)
                elif any(entry.filename.lower().endswith(x) for x in IMAGE_EXTENSIONS):
                    remote_files.append(full)
    finally:
        sftp.close()
        client.close()

    if not remote_files:
        raise ValueError(f"No image files found on remote at {remote_dir!r}")

    ext = PurePosixPath(remote_files[0]).suffix
    rel_stems = [str(PurePosixPath(f).relative_to(base).with_suffix('')) for f in remote_files]

    if separators is None:
        separators = _DEFAULT_SEPS
    config, separators = _detect_from_rel_stems(rel_stems, ext, base, separators)
    config = Config(template=config.template, axes=config.axes,
                    keys=config.keys, remote=remote)
    return config, separators, len(remote_files)


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


def prompt_rename(
    config: Config,
    n_files: int,
    separators: list[str],
    directory: Path | None = None,
    max_depth: int | None = None,
) -> Config:
    """Interactively rename or ignore each auto-detected axis.

    If *directory* is given, first prompts to confirm or change the separator(s),
    re-detecting on change.  Without it, the separator step is skipped (useful
    for remote directories where re-detection would require another SFTP round-trip).

    Raises ValueError if every axis is ignored.
    """
    use_col = sys.stdout.isatty()

    def _make_colors(axes: dict) -> dict[str, str]:
        return (
            {name: _PALETTE[i % len(_PALETTE)] for i, name in enumerate(axes)}
            if use_col else {}
        )

    print(f"\nDetected {n_files} image{'s' if n_files != 1 else ''}\n")

    # --- Separator selection (local only) ---
    while directory is not None:
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

        if len(values) <= _MAX_VALS_DISPLAY:
            vals_display = "  ".join(values)
        else:
            vals_display = "  ".join(values[:_MAX_VALS_DISPLAY]) + f"  … ({len(values)} total)"
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

    return Config(template=template, axes=new_axes, keys=_auto_keys(new_axes),
                  mode=config.mode, remote=config.remote)


# ── Smart path dispatch ────────────────────────────────────────────────────────

def _is_remote_pattern(arg: str) -> bool:
    """True if *arg* looks like a remote path (user@host:/path or host:/path).

    A single uppercase letter followed by ':' is treated as a Windows drive,
    not a hostname, so 'C:/foo' is not remote.
    """
    # Explicit user@host: form — unambiguous
    if re.match(r'^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:', arg):
        return True
    # host:/path — require 2+ letter hostname to exclude Windows drive letters
    if re.match(r'^[A-Za-z]{2}[A-Za-z0-9._-]*:/', arg):
        return True
    return False


def _parse_remote_pattern(arg: str) -> tuple[RemoteConfig, str]:
    """Split 'user@host:/remote/template' into (RemoteConfig, remote_template_path)."""
    colon = arg.index(':')
    host_part = arg[:colon]
    remote_template = arg[colon + 1:]
    user: str | None = None
    if '@' in host_part:
        user, host_part = host_part.rsplit('@', 1)
    return RemoteConfig(host=host_part, user=user), remote_template


def _template_regex(template: str) -> str:
    """Turn a template into a regex whose groups are its placeholders, in order.

    Groups are numbered rather than named: a placeholder may be called
    `{yyyy-mm-dd}`, which is not a valid Python group name.
    """
    segs = PLACEHOLDER_NAME_RE.split(template)
    return ''.join(
        re.escape(s) if i % 2 == 0 else '([^/]+)'
        for i, s in enumerate(segs)
    )


def _axes_from_local_template(template: str) -> dict[str, list[str]]:
    """Detect axis values by globbing the local filesystem against *template*.

    Example: 'plots/{sensor}_{date}.png' scans for matching files and returns
    {'sensor': ['ASCAT', 'SMAP', ...], 'date': ['2024-03-15', ...]}.
    """
    names = PLACEHOLDER_NAME_RE.findall(template)
    if not names:
        raise ValueError(f"Template {template!r} has no {{placeholder}} variables")

    norm = template.replace('\\', '/')
    glob_pat = PLACEHOLDER_NAME_RE.sub('*', norm)
    raw_files = _glob_mod.glob(glob_pat)
    if not raw_files:
        raise ValueError(f"No files match template {template!r}")

    regex = _template_regex(norm)

    axes: dict[str, list[str]] = {n: [] for n in names}
    seen: dict[str, set] = {n: set() for n in names}
    for f in sorted(raw_files):
        m = re.fullmatch(regex, f.replace('\\', '/'))
        if m:
            for i, n in enumerate(names):
                v = m.group(i + 1)
                if v not in seen[n]:
                    seen[n].add(v)
                    axes[n].append(v)

    axes = {k: _numeric_sort(v) for k, v in axes.items() if v}
    if not axes:
        raise ValueError(f"Could not extract axis values from files matching {template!r}")
    return axes


def _axes_from_sftp_template(template: str, sftp) -> dict[str, list[str]]:
    """Detect axis values from files matching *template* using an already-open SFTP client.

    Walks the remote directory tree starting at the fixed prefix before the
    first placeholder, then matches every file against the template pattern.
    The caller owns the SFTP session and is responsible for keeping it alive.
    """
    import stat

    names = PLACEHOLDER_NAME_RE.findall(template)
    if not names:
        raise ValueError(f"Remote template {template!r} has no {{placeholder}} variables")

    prefix = template[:template.index('{')]
    last_slash = prefix.rfind('/')
    base_dir = prefix[:last_slash] if last_slash > 0 else '/'

    regex = _template_regex(template)

    remote_files: list[str] = []
    stack = [base_dir]
    while stack:
        path = stack.pop()
        try:
            entries = sftp.listdir_attr(path)
        except IOError:
            continue
        for entry in entries:
            full = f"{path}/{entry.filename}"
            if stat.S_ISDIR(entry.st_mode):
                stack.append(full)
            else:
                remote_files.append(full)

    axes: dict[str, list[str]] = {n: [] for n in names}
    seen: dict[str, set] = {n: set() for n in names}
    for f in sorted(remote_files):
        m = re.fullmatch(regex, f)
        if m:
            for i, n in enumerate(names):
                v = m.group(i + 1)
                if v not in seen[n]:
                    seen[n].add(v)
                    axes[n].append(v)

    axes = {k: _numeric_sort(v) for k, v in axes.items() if v}
    if not axes:
        raise ValueError(f"No files on remote match template {template!r}")
    return axes


def _axes_from_remote_template(
    template: str,
    remote: RemoteConfig,
    get_password: Callable[[str], str | None] | None = None,
) -> dict[str, list[str]]:
    """Connect via SFTP and detect axis values from files matching the remote template."""
    from .loader import _connect_sftp
    client, sftp = _connect_sftp(remote, get_password)
    try:
        return _axes_from_sftp_template(template, sftp)
    finally:
        sftp.close()
        client.close()
