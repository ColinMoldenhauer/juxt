"""Auto-detect image axes from a directory, local template, or remote path."""
from __future__ import annotations

import glob as _glob_mod
import logging
import math
import re
import stat
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable

from .complete import PLACEHOLDER_NAME_RE, sftp_isdir
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
    from .loader import _connect_sftp

    base = remote_dir.rstrip('/')
    client, sftp = _connect_sftp(remote, get_password)
    try:
        remote_files: list[str] = []
        stack = [base]
        seen_dirs: set[str] = set()
        while stack:
            path = stack.pop()
            try:
                entries = sftp.listdir_attr(path)
            except IOError:
                continue
            for entry in sorted(entries, key=lambda e: e.filename):
                full = f"{path}/{entry.filename}"
                if sftp_isdir(sftp, full, entry.st_mode):
                    if _unvisited(sftp, full, seen_dirs):
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


def has_bare_wildcard(path: str) -> bool:
    """True if *path* has a '*' outside of any {placeholder} (including a
    `{**name}` one), i.e. one dispatching to `resolve_wildcard_template`
    rather than the `**name` support in `_axes_from_local_template`.

    '{**name}' itself contains '*' characters, so a plain `'*' in path`
    check would misroute it.
    """
    return '*' in PLACEHOLDER_NAME_RE.sub('', path)


def _parse_remote_pattern(arg: str) -> tuple[RemoteConfig, str]:
    """Split 'user@host:/remote/template' into (RemoteConfig, remote_template_path)."""
    colon = arg.index(':')
    host_part = arg[:colon]
    remote_template = arg[colon + 1:]
    user: str | None = None
    if '@' in host_part:
        user, host_part = host_part.rsplit('@', 1)
    return RemoteConfig(host=host_part, user=user), remote_template


def _template_regex(template: str, patterns: dict[str, str] | None = None) -> str:
    """Turn a template into a regex whose groups are its placeholders, in order.

    The groups are named by position, `_g0`, `_g1`, ... (read them with
    `_captures`): a placeholder may be called `{yyyy-mm-dd}`, which is not a
    valid group name, and the groups of a pattern pin's own regex must not
    shift the numbering.

    A placeholder named `**name` (see `_axes_from_local_template`) captures
    across '/' rather than stopping at the next path separator.  *patterns*
    maps a placeholder, as written inside its braces, to the regex its value
    has to match instead (a pattern pin, see `PinPattern`).
    """
    parts = []
    for i, s in enumerate(PLACEHOLDER_NAME_RE.split(template)):
        if i % 2 == 0:
            parts.append(re.escape(s))
            continue
        if patterns and s in patterns:
            body = f"(?:{patterns[s]})"
        else:
            body = '.+' if s.startswith('**') else '[^/]+'
        parts.append(f"(?P<_g{i // 2}>{body})")
    return ''.join(parts)


def _captures(m: re.Match, names: list[str]) -> list[tuple[str, str]]:
    """Pair the placeholder *names* (in template order) with what a
    `_template_regex` match *m* captured for them."""
    return [(n, m.group(f"_g{i}")) for i, n in enumerate(names)]


def _strip_recursive_marker(name: str) -> tuple[str, bool]:
    """Split a placeholder name into (axis_name, is_recursive).

    `{**plot}` is written with a `**` marker prefix, mirroring `**` in a
    glob: the axis it names is allowed to capture across '/'.
    """
    return (name[2:], True) if name.startswith('**') else (name, False)


def _parse_template_names(template: str) -> tuple[list[str], dict[str, str], str | None]:
    """Parse a template's placeholders into (names, raw_token_of, recursive_name).

    *names* are axis names with any `**` marker stripped, in template order.
    *raw_token_of* maps each axis name back to the literal text inside its
    `{...}` in *template* (e.g. `'plot': '**plot'`), needed to substitute
    pinned values or build glob patterns against the template as written.
    *recursive_name* is the one axis name allowed to capture across '/', or
    None. At most one `**name` placeholder is allowed per template, since
    more than one greedy '/'-crossing capture group makes the match ambiguous.
    """
    names: list[str] = []
    raw_token_of: dict[str, str] = {}
    recursive_name: str | None = None
    for raw in PLACEHOLDER_NAME_RE.findall(template):
        name, is_recursive = _strip_recursive_marker(raw)
        if name in raw_token_of and raw_token_of[name] != raw:
            raise ValueError(
                f"Template {template!r} uses both {{{raw_token_of[name]}}} and "
                f"{{{raw}}} for the same axis; use '**{name}' consistently or not at all"
            )
        if is_recursive:
            if recursive_name is not None and recursive_name != name:
                raise ValueError(
                    f"Template {template!r} has more than one '**name' placeholder; "
                    "only one axis may capture across '/'"
                )
            recursive_name = name
        names.append(name)
        raw_token_of[name] = raw
    return names, raw_token_of, recursive_name


def _prune_common_prefix(values: list[str]) -> tuple[str, list[str]]:
    """Strip the longest run of leading '/'-delimited segments shared by
    every value, moving it out as a literal prefix.

    At least one segment is always kept in the shortest value, so no value
    is ever pruned down to ''. Returns ('', values) unchanged when nothing
    is shared or there is only one value to compare.
    """
    if len(values) < 2:
        return '', values
    split = [v.split('/') for v in values]
    shortest = min(len(s) for s in split)
    common = 0
    for i in range(shortest - 1):
        if all(s[i] == split[0][i] for s in split):
            common += 1
        else:
            break
    if common == 0:
        return '', values
    prefix = '/'.join(split[0][:common])
    return prefix, ['/'.join(s[common:]) for s in split]


def _unvisited(sftp, path: str, seen: set[str]) -> bool:
    """Whether the remote directory *path* has not been walked yet, recording
    it if not.  Keyed by the resolved path, so a symlink pointing back up the
    tree (or two links to the same directory) can't send the walk in circles.
    """
    try:
        key = sftp.normalize(path)
    except Exception:
        key = path
    if key in seen:
        return False
    seen.add(key)
    return True


def _split_pinned(names: list[str], pinned: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Validate *pinned* against the template's placeholder *names* and split
    them into (pinned_names, free_names), both in template order."""
    unknown = sorted(set(pinned) - set(names))
    if unknown:
        raise ValueError(
            f"--{unknown[0]} does not match any {{placeholder}} in the template. "
            f"Known placeholders: {names}"
        )
    return [n for n in names if n in pinned], [n for n in names if n not in pinned]


def _pinned_combos(pinned_names: list[str], pinned: dict[str, list]) -> list[dict]:
    """Every combination of the given axes' pinned values, as `{name: value}` dicts."""
    if not pinned_names:
        return [{}]
    return [dict(zip(pinned_names, vals)) for vals in product(*(pinned[n] for n in pinned_names))]


# ── pattern pins ─────────────────────────────────────────────────────────────
#
# A `--NAME VALUE` pin is normally a literal value.  One containing a glob
# character, or written `re:REGEX`, instead selects the values found on disk
# that it matches (in full, case-sensitively).  Glob characters hardly ever
# occur in filenames, whereas '.' does all the time (`1.5`, `v2.0`), which is
# why a regex has to be asked for explicitly.
#
# Only pins the user typed are parsed (`parse_pins`); the resolvers take a str
# literally, since they are also handed values discovered on disk, which may
# well contain a '[' of their own.

REGEX_PIN_PREFIX = "re:"
_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class PinPattern:
    """A pin that selects the discovered values it matches.

    *text* is the pin as typed.  *regex* is what a value has to match in full;
    it is also spliced into the template regex, so it may not start with
    global flags (`parse_pin` scopes them).  *glob* is the glob, with '\\'
    normalised to '/', when the pin is one: it then narrows the filesystem
    glob too.
    """
    text: str
    regex: re.Pattern
    glob: str | None = None

    def matches(self, value: str) -> bool:
        return self.regex.fullmatch(value) is not None


def _glob_regex(glob: str) -> str:
    """Translate a shell-style *glob* into a regex, with shell semantics: `*`
    and `?` stop at '/', the way the filesystem glob that a glob pin narrows
    does, and `[...]` is a character class (`[!...]` negated)."""
    out = []
    i, n = 0, len(glob)
    while i < n:
        c = glob[i]
        i += 1
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = i + 1 if i < n and glob[i] in "!^" else i
            if j < n and glob[j] == "]":
                j += 1  # a leading ']' is a member, not the end
            j = glob.find("]", j)
            if j < 0:
                out.append(re.escape(c))
                continue
            body = glob[i:j].replace("\\", "\\\\").replace("[", "\\[")
            if body[:1] in ("!", "^"):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            i = j + 1
        else:
            out.append(re.escape(c))
    return "".join(out)


def is_pin_pattern(value) -> bool:
    """Whether the pin *value* (as typed, or parsed) selects by pattern."""
    if isinstance(value, PinPattern):
        return True
    return value.startswith(REGEX_PIN_PREFIX) or bool(_GLOB_CHARS & set(value))


def parse_pin(value):
    """Parse one pin as typed: a literal value stays a str, anything else
    becomes a `PinPattern`.  Already parsed pins pass through.

    Raises ValueError for a `re:` pin that is not a valid regex.
    """
    if isinstance(value, PinPattern):
        return value
    if value.startswith(REGEX_PIN_PREFIX):
        body = value[len(REGEX_PIN_PREFIX):]
        # `(?i)abc` would be a global flag in the middle of the template
        # regex, which re rejects; `(?i:abc)` means the same and nests.
        flags = re.match(r"\(\?([aiLmsux]+)\)", body)
        if flags:
            body = f"(?{flags.group(1)}:{body[flags.end():]})"
        try:
            return PinPattern(value, re.compile(body))
        except re.error as e:
            raise ValueError(f"Invalid regex in pin {value!r}: {e}") from None
    if _GLOB_CHARS & set(value):
        glob = value.replace("\\", "/")
        return PinPattern(value, re.compile(_glob_regex(glob)), glob)
    return value


def literal_pin(value: str) -> str:
    """Write the literal *value* so that `parse_pin` reads it back as exactly
    that value: one that would read as a pattern becomes an escaped regex."""
    return REGEX_PIN_PREFIX + re.escape(value) if is_pin_pattern(value) else value


def parse_pins(pins: dict[str, list] | None) -> dict[str, list]:
    """`parse_pin` for every value of a `{name: [values...]}` pin dict."""
    return {n: [parse_pin(v) for v in vs] for n, vs in (pins or {}).items()}


def order_pinned(specs: list, found: list[str]) -> list[str]:
    """The values of a pinned axis in the order its pins name them.

    A literal pin contributes itself, a `PinPattern` the values of *found*
    it matches, sorted as a discovered axis would be.  Duplicates are dropped.
    """
    out: list[str] = []
    seen: set[str] = set()
    for spec in specs:
        if isinstance(spec, PinPattern):
            vals = _numeric_sort([v for v in found if spec.matches(v)])
        else:
            vals = [spec]
        for v in vals:
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def _settle_pattern_axes(pinned: dict[str, list], axes: dict[str, list[str]]) -> None:
    """Order every axis pinned with a pattern (see `order_pinned`), in place.

    Raises ValueError for a pattern that matched nothing: most likely a typo,
    and silently dropping it would hide that.
    """
    for n, specs in pinned.items():
        patterns = [s for s in specs if isinstance(s, PinPattern)]
        if not patterns:
            continue
        for spec in patterns:
            if not any(spec.matches(v) for v in axes[n]):
                raise ValueError(f"--{n} {spec.text!r} matches no files")
        axes[n] = order_pinned(specs, axes[n])


def _normalise_literals(pinned: dict[str, list] | None) -> dict[str, list]:
    """Turn '\\' into '/' in the literal values of *pinned*."""
    return {k: [v.replace("\\", "/") if isinstance(v, str) else v for v in vs]
            for k, vs in (pinned or {}).items()}


def _record_match(m: re.Match, open_names: list[str],
                  axes: dict[str, list[str]], seen: dict[str, set]) -> None:
    """Add the values a template match *m* captured for *open_names*."""
    for n, v in _captures(m, open_names):
        if v not in seen[n]:
            seen[n].add(v)
            axes[n].append(v)


def _axes_from_local_template(
    template: str,
    pinned: dict[str, list[str]] | None = None,
    full_path: bool = False,
) -> tuple[str, dict[str, list[str]]]:
    """Detect axis values by matching the local filesystem against *template*.

    Example: 'plots/{sensor}_{date}.png' scans for matching files and returns
    {'sensor': ['ASCAT', 'SMAP', ...], 'date': ['2024-03-15', ...]}.

    *pinned*, if given, fixes one or more placeholders to an explicit list of
    values (in the given order) instead of discovering them by globbing, e.g.
    `{'root': ['/data/run1', '/data/run2']}`. A value may also be a pattern
    (a `PinPattern`, see `parse_pin`), which keeps the values on disk that it
    matches. A str is always taken literally, so discovered values can be
    pinned again safely. Globbing then only resolves the
    remaining placeholders, once per combination of pinned values, and the
    results are merged into a shared axis space. This also lets a pinned
    value contain '/', which plain globbing can't discover since '*' never
    crosses a path separator.

    One placeholder may be written `{**name}` to capture across '/' instead
    of stopping at the next path separator, so it can stand for an entire
    (and possibly differently-shaped) subtree below it, e.g.
    '{root}/{**plot}.png' with 'root' pinned to several directories. Since
    globbing can't express a '/'-crossing match, that combination walks the
    filesystem recursively instead. Unless *full_path* is set, any leading
    path segments shared by every matched value are pruned out of the
    '**name' axis (and folded into the template as fixed text instead), so
    e.g. 'dir/AM/plot' and 'dir/PM/plot' become 'AM/plot' and 'PM/plot'.

    Returns `(resolved_template, axes)`. *resolved_template* is *template*
    unchanged unless it used `{**name}`, in which case the marker is
    stripped (and any pruned prefix spliced in) so it can be formatted like
    any other template.
    """
    names, raw_token_of, recursive_name = _parse_template_names(template)
    if not names:
        raise ValueError(f"Template {template!r} has no {{placeholder}} variables")

    # Normalise pinned values the same way the template itself is normalised
    # below — otherwise a Windows path pinned with '\' survives into the glob
    # pattern and its regex, while the matched files it's compared against
    # (via f.replace('\\', '/')) never do, and nothing matches.
    pinned = _normalise_literals(pinned)
    pinned_names, free_names = _split_pinned(names, pinned)
    norm = template.replace('\\', '/')
    recursive_free = recursive_name is not None and recursive_name in free_names

    axes: dict[str, list[str]] = {n: [] for n in names}
    seen: dict[str, set] = {n: set() for n in names}

    for combo in _pinned_combos(pinned_names, pinned):
        fixed = {n: v for n, v in combo.items() if isinstance(v, str)}
        filters = {n: v for n, v in combo.items() if isinstance(v, PinPattern)}
        for n, v in fixed.items():
            if v not in seen[n]:
                seen[n].add(v)
                axes[n].append(v)

        # In template order, one per capture group: a pattern pin is matched
        # like a free placeholder, but by its own regex.
        open_names = [n for n in names if n not in fixed]
        if not open_names:
            continue

        sub = norm
        for n, v in fixed.items():
            sub = sub.replace(f"{{{raw_token_of[n]}}}", v)
        regex = _template_regex(
            sub, {raw_token_of[n]: p.regex.pattern for n, p in filters.items()})

        if recursive_name in open_names:
            first_brace = sub.index('{')
            prefix = sub[:first_brace]
            last_slash = prefix.rfind('/')
            base_dir = prefix[:last_slash] if last_slash >= 0 else '.'
            if not Path(base_dir).is_dir():
                raise ValueError(f"{base_dir!r} is not a directory")
            matches = [str(f) for f in _iter_images(Path(base_dir))]
        else:
            glob_sub = sub
            for n, p in filters.items():
                if p.glob is not None:  # narrows the scan, e.g. to some directories
                    glob_sub = glob_sub.replace(f"{{{raw_token_of[n]}}}", p.glob)
            glob_pat = PLACEHOLDER_NAME_RE.sub('*', glob_sub)
            matches = _glob_mod.glob(glob_pat)

        # Sorted with '/' as the separator, so values come out in the same
        # order on Windows ('\\' sorts after the digits, '/' before them).
        for f in sorted(f.replace('\\', '/') for f in matches):
            m = re.fullmatch(regex, f)
            if m:
                _record_match(m, open_names, axes, seen)

    _settle_pattern_axes(pinned, axes)
    if any(not axes[n] for n in free_names):
        raise ValueError(f"No files match template {template!r}")
    axes = {k: (v if k in pinned else _numeric_sort(v)) for k, v in axes.items()}

    resolved_template = template
    if recursive_name is not None:
        # Pruning only makes sense for a discovered ('free') axis: a pinned
        # {**name} is a literal value the caller chose, not ours to rewrite.
        prefix = ''
        if recursive_free and not full_path:
            prefix, axes[recursive_name] = _prune_common_prefix(axes[recursive_name])
        replacement = (prefix + '/' if prefix else '') + '{' + recursive_name + '}'
        resolved_template = norm.replace('{' + raw_token_of[recursive_name] + '}', replacement)

    return resolved_template, axes


def _wildcard_regex(pattern: str) -> str:
    """Turn a '*'-only glob *pattern* into a regex matching it.

    Unlike a shell glob, '*' here is expected to reach into nested
    subdirectories (see `resolve_wildcard_template`), so it maps to '.*'
    rather than a segment-bound '[^/]*'.
    """
    return ".*".join(re.escape(p) for p in pattern.split("*"))


def resolve_wildcard_template(
    template: str,
    pinned: dict[str, list[str]] | None = None,
) -> tuple[str, dict[str, list[str]]]:
    """Resolve a template containing a '*' wildcard, e.g. '{root}/*.png'.

    Every {placeholder} in *template* must sit before the first '*' and be
    given a pinned value: a wildcard replaces the need to *name* the
    remaining path components, so there is nothing left for a free
    {placeholder} to bind to. Once the pinned prefix is resolved to a real
    directory, per combination of pinned values, that directory is walked
    recursively for image files ('*' crosses subdirectories, unlike a shell
    glob) and matched against the pattern after it. The matched relative
    paths are then split into axes exactly as directory auto-discovery does
    (`detect_config`): any position that varies becomes an 'axis_N'.

    Files found across every combination of pinned values are pooled before
    detecting axes, so `{root}/*.png` with `root` pinned to several
    directories aggregates them the same way a single auto-discovered
    directory would, including a column that only varies *between* pinned
    values (e.g. one date per root) rather than within any single one of
    them. Returns `(resolved_template, axes)`, where *resolved_template* has
    the '*' portion replaced by the discovered {axis_N} placeholders and can
    be formatted like any other template.
    """
    norm = template.replace("\\", "/")
    star_idx = norm.index("*")

    names = PLACEHOLDER_NAME_RE.findall(norm)
    if any(norm.index(f"{{{n}}}") > star_idx for n in names):
        raise ValueError(
            f"Template {template!r}: a {{placeholder}} cannot appear after a '*' wildcard"
        )

    pinned = _normalise_literals(pinned)
    unknown = sorted(set(pinned) - set(names))
    if unknown:
        raise ValueError(
            f"--{unknown[0]} does not match any {{placeholder}} in the template. "
            f"Known placeholders: {names}"
        )
    unpinned = sorted(set(names) - set(pinned))
    if unpinned:
        raise ValueError(
            f"Template {template!r} uses a '*' wildcard, so every {{placeholder}} "
            f"before it must be pinned with --NAME VALUE; missing: {unpinned}"
        )

    slash_idx = norm.rfind("/", 0, star_idx)
    dir_prefix = norm[:slash_idx] if slash_idx >= 0 else "."
    rest_pattern = norm[slash_idx + 1:]
    pattern_re = re.compile(_wildcard_regex(rest_pattern))

    # A pattern pin is resolved to the directories it matches first; the walk
    # below then treats them like literal ones.  Their values are found per
    # axis, so not every combination of them need be a directory.
    expanded = any(isinstance(v, PinPattern) for vs in pinned.values() for v in vs)
    if expanded:
        _, found = _axes_from_local_template(dir_prefix, pinned=pinned)
        pinned = {n: found[n] for n in pinned}

    axes: dict[str, list[str]] = {n: [] for n in names}
    seen: dict[str, set] = {n: set() for n in names}
    ext: str | None = None
    all_rel_stems: list[str] = []

    for combo in _pinned_combos(names, pinned):
        for n, v in combo.items():
            if v not in seen[n]:
                seen[n].add(v)
                axes[n].append(v)

        base_dir = dir_prefix
        for n, v in combo.items():
            base_dir = base_dir.replace(f"{{{n}}}", v)
        base = Path(base_dir)
        if not base.is_dir():
            if expanded:
                continue
            raise ValueError(f"{base_dir!r} is not a directory")

        files = [
            f for f in _iter_images(base)
            if pattern_re.fullmatch(str(f.relative_to(base)).replace("\\", "/"))
        ]
        if not files:
            continue

        if ext is None:
            ext = files[0].suffix
        all_rel_stems.extend(
            str(f.relative_to(base).with_suffix("")).replace("\\", "/") for f in files
        )

    if not all_rel_stems:
        raise ValueError(f"No files match wildcard template {template!r}")

    sub_config, _ = _detect_from_rel_stems(sorted(all_rel_stems), ext, dir_prefix, _DEFAULT_SEPS)
    for col_name, values in sub_config.axes.items():
        axes[col_name] = values

    axes = {k: (v if k in pinned else _numeric_sort(v)) for k, v in axes.items()}
    return sub_config.template, axes


def _axes_from_sftp_template(
    template: str,
    sftp,
    pinned: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Detect axis values from files matching *template* using an already-open SFTP client.

    Walks the remote directory tree starting at the fixed prefix before the
    first placeholder, then matches every file against the template pattern.
    The caller owns the SFTP session and is responsible for keeping it alive.

    *pinned* fixes one or more placeholders to explicit values instead of
    discovering them by walking the tree — see `_axes_from_local_template`
    for the semantics. One walk is done per combination of pinned values,
    since a pinned placeholder can shift where the fixed prefix (and so the
    walk's starting directory) falls. Pinning several placeholders therefore
    multiplies the walks, so the walk is pruned segment by segment and every
    listing is cached: over SFTP each listing is a round trip, and the naive
    product of combinations times subtree size is painfully slow on a large
    tree (worse still when the remote path is itself a network mount).
    """
    names = PLACEHOLDER_NAME_RE.findall(template)
    if not names:
        raise ValueError(f"Remote template {template!r} has no {{placeholder}} variables")

    pinned = pinned or {}
    pinned_names, free_names = _split_pinned(names, pinned)

    axes: dict[str, list[str]] = {n: [] for n in names}
    seen: dict[str, set] = {n: set() for n in names}

    listings: dict[str, list] = {}

    def entries(path: str) -> list:
        """`listdir_attr`, cached across combinations: their walks overlap."""
        if path not in listings:
            try:
                listings[path] = sftp.listdir_attr(path)
            except IOError:
                listings[path] = []
        return listings[path]

    for combo in _pinned_combos(pinned_names, pinned):
        fixed = {n: v for n, v in combo.items() if isinstance(v, str)}
        filters = {n: v for n, v in combo.items() if isinstance(v, PinPattern)}
        for n, v in fixed.items():
            if v not in seen[n]:
                seen[n].add(v)
                axes[n].append(v)

        open_names = [n for n in names if n not in fixed]
        if not open_names:
            continue

        sub = template
        for n, v in fixed.items():
            sub = sub.replace(f"{{{n}}}", v)

        prefix = sub[:sub.index('{')]
        last_slash = prefix.rfind('/')
        base_dir = prefix[:last_slash] if last_slash > 0 else '/'
        patterns = {n: p.regex.pattern for n, p in filters.items()}
        regex = _template_regex(sub, patterns)

        # One regex per path segment below base_dir: a directory whose name
        # can't be part of a match is skipped instead of being read out, and
        # the walk stops at the depth the files sit at. A '{**name}' spans '/'
        # and so pins neither the names nor the depth below it — there the
        # whole subtree has to be read (seg_res None).  So does a pattern pin
        # that may span '/': a regex, or a glob with a '/' in it.  A glob
        # within one segment prunes like the rest of it.
        segs = sub[len(base_dir):].lstrip('/').split('/')
        spans = any(p.glob is None or '/' in p.glob for p in filters.values())
        seg_res = (
            None if spans or any('**' in s for s in segs)
            else [re.compile(_template_regex(s, patterns)) for s in segs]
        )
        leaf = len(segs) - 1

        remote_files: list[str] = []
        stack = [(base_dir, 0)]
        seen_dirs: set[str] = set()
        while stack:
            path, depth = stack.pop()
            for entry in entries(path):
                if seg_res is not None and not seg_res[depth].fullmatch(entry.filename):
                    continue
                full = f"{path}/{entry.filename}"
                if sftp_isdir(sftp, full, entry.st_mode):
                    if seg_res is not None and depth == leaf:
                        continue  # the files sit here, so a directory is not one
                    # Only a symlink can point back into the walk's own path.
                    if not stat.S_ISLNK(entry.st_mode) or _unvisited(sftp, full, seen_dirs):
                        stack.append((full, depth + 1))
                elif seg_res is None or depth == leaf:
                    remote_files.append(full)

        for f in sorted(remote_files):
            m = re.fullmatch(regex, f)
            if m:
                _record_match(m, open_names, axes, seen)

    _settle_pattern_axes(pinned, axes)
    if any(not axes[n] for n in free_names):
        raise ValueError(f"No files on remote match template {template!r}")
    return {k: (v if k in pinned else _numeric_sort(v)) for k, v in axes.items()}


def axis_values_on_disk(
    template: str,
    axes: dict[str, list[str]],
    axis: str,
    sftp=None,
) -> list[str]:
    """Values of *axis* that have a file for some combination of the other axes.

    Every other axis is pinned to the values it has now, so only *axis* is
    searched for: the walk stays as narrow as the current session (cheap over
    SFTP, where each listing is a round trip), axes pinned on the command line
    keep their values, and a value turns up only if it would show at least one
    image. Searches the local filesystem, or the remote host when *sftp* (an
    open client) is given.
    """
    pinned = {n: list(vs) for n, vs in axes.items() if n != axis}
    if sftp is None:
        _, found = _axes_from_local_template(template, pinned)
    else:
        found = _axes_from_sftp_template(template, sftp, pinned)
    return list(found.get(axis, []))


def _axes_from_remote_template(
    template: str,
    remote: RemoteConfig,
    get_password: Callable[[str], str | None] | None = None,
    pinned: dict[str, list[str]] | None = None,
) -> dict[str, list[str]]:
    """Connect via SFTP and detect axis values from files matching the remote template."""
    from .loader import _connect_sftp
    client, sftp = _connect_sftp(remote, get_password)
    try:
        return _axes_from_sftp_template(template, sftp, pinned)
    finally:
        sftp.close()
        client.close()
