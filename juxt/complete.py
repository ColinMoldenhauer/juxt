"""Placeholder-aware path completion for the juxt command line.

The argument of `:pattern` is a *template*, not a plain path: any path
component may be a `{placeholder}` standing in for an axis.  Completion
therefore treats every placeholder as a wildcard, so a template can be built
top-down — type `plots/{sensor}/` and Tab keeps completing below it — instead
of completing one concrete path first and deleting the parts that should vary.

Everything here is pure (no Qt, no viewer state): directory listings arrive
through a `listdir(path) -> [(name, is_dir), ...]` callback so the same code
serves the local filesystem and an open SFTP session.

The bottom of the module is the `juxt-complete` helper command, which gives the
`juxt` shell command the same completion at the bash/zsh prompt.
"""
from __future__ import annotations

import fnmatch
import html as _html
import os
import re
import stat as _stat
from dataclasses import dataclass, field

# A placeholder is either named — {sensor} — or anonymous — {} — which is
# given a generated name when the pattern is applied.
PLACEHOLDER_RE = re.compile(r"\{\w*\}")

CARET = "▌"  # the status bar caret, which may sit inside a placeholder
_HIGHLIGHT_RE = re.compile(rf"\{{[\w{CARET}]*\}}|\{{[\w{CARET}]*$")

# Cycled per placeholder, in order of appearance.
PLACEHOLDER_COLORS = ["#e8913a", "#4fc3f7", "#9ccc65", "#ce93d8", "#ffd54f"]

_DELIMS = "_-. "
_MAX_DIRS = 64      # cap on the fan-out of a wildcard directory component
_MAX_MATCHES = 24   # cap on candidates listed in the status bar


@dataclass
class Completion:
    """Text to insert at the caret, plus the candidates to show."""
    append: str = ""
    matches: list[str] = field(default_factory=list)


def has_placeholder(text: str) -> bool:
    return PLACEHOLDER_RE.search(text) is not None


def normalize_template(template: str) -> str:
    """Give every anonymous `{}` placeholder a generated axis name.

    `plots/{}/{date}.png` → `plots/{axis_1}/{date}.png`.  Generated names skip
    any name already used in the template.
    """
    taken = set(re.findall(r"\{(\w+)\}", template))
    counter = 0

    def _name(_m):
        nonlocal counter
        while True:
            counter += 1
            name = f"axis_{counter}"
            if name not in taken:
                taken.add(name)
                return f"{{{name}}}"

    return re.sub(r"\{\}", _name, template)


def placeholder_html(text: str) -> str:
    """Render *text* as rich text with each placeholder in its own colour."""
    def esc(s: str) -> str:
        return _html.escape(s).replace(" ", "&nbsp;")

    out: list[str] = []
    last = 0
    for i, m in enumerate(_HIGHLIGHT_RE.finditer(text)):
        out.append(esc(text[last:m.start()]))
        colour = PLACEHOLDER_COLORS[i % len(PLACEHOLDER_COLORS)]
        out.append(f'<span style="color:{colour}">{esc(m.group())}</span>')
        last = m.end()
    out.append(esc(text[last:]))
    return "".join(out)


# ── completion ───────────────────────────────────────────────────────────────

def complete_placeholder_name(prefix: str, axis_names) -> Completion | None:
    """Complete an unclosed `{name` against the current axis names.

    Returns None when the caret is not inside a placeholder being typed, so the
    caller can fall through to path completion.
    """
    open_brace = prefix.rfind("{")
    if open_brace < 0:
        return None
    frag = prefix[open_brace + 1:]
    if not re.fullmatch(r"\w*", frag):
        return None
    cands = [n for n in axis_names if n.startswith(frag)]
    if not cands:
        return None
    if len(cands) == 1:
        return Completion(cands[0][len(frag):] + "}")
    lcp = os.path.commonprefix(cands)
    return Completion(lcp[len(frag):], [f"{{{n}}}" for n in cands])


def _match_entries(prefix: str, listdir, sep: str):
    """Return `(tail, [(name, is_dir, rest), ...])` for the last component.

    *rest* is the part of the filename beyond what was typed — the text a
    completion would insert.
    """
    head, _, tail = prefix.rpartition(sep)
    dirs = _resolve_dirs(head, prefix.startswith(sep), listdir, sep)
    if not dirs:
        return tail, []

    entries: list[tuple[str, bool]] = []
    seen: set[tuple[str, bool]] = set()
    for d in dirs:
        for name, is_dir in listdir(d):
            if name.startswith(".") and not tail.startswith("."):
                continue
            if (name, is_dir) in seen:
                continue
            seen.add((name, is_dir))
            entries.append((name, is_dir))
    entries.sort()

    regex = _tail_regex(tail)
    matched = []
    for name, is_dir in entries:
        m = regex.match(name)
        if m:
            matched.append((name, is_dir, name[m.end():]))
    return tail, matched


def complete_path(prefix: str, listdir, sep: str = "/") -> Completion:
    """Complete the last component of *prefix*, treating placeholders as `*`.

    Only the text *after* the last placeholder is completed, so placeholders
    already typed survive: `plots/{sensor}/2024-0` + Tab lists what every
    sensor directory has under it and extends the `2024-0` part only.
    """
    tail, matched = _match_entries(prefix, listdir, sep)
    if not matched:
        return Completion()

    if tail.endswith("}"):
        # The component ends on a placeholder: there is no partial word to
        # extend, so complete with what all candidates have in common instead.
        append = _append_after_placeholder(matched, sep)
    elif len(matched) == 1:
        name, is_dir, rest = matched[0]
        append = rest + (sep if is_dir else "")
    else:
        append = os.path.commonprefix([rest for _, _, rest in matched])

    if len(matched) == 1:
        return Completion(append)
    shown = [name + (sep if is_dir else "") for name, is_dir, _ in matched]
    if len(shown) > _MAX_MATCHES:
        extra = len(shown) - _MAX_MATCHES
        shown = shown[:_MAX_MATCHES] + [f"(+{extra} more)"]
    return Completion(append, shown)


def completion_words(prefix: str, listdir, sep: str = "/") -> list[str]:
    """Like `complete_path`, but as whole words — what a shell expects.

    Each candidate is the typed *prefix* with one match appended, so the
    placeholders in it survive the completion.
    """
    tail, matched = _match_entries(prefix, listdir, sep)
    if not matched:
        return []
    if tail.endswith("}"):
        # No partial word to extend — offer the one shared completion, if any.
        append = _append_after_placeholder(matched, sep)
        return [prefix + append] if append else []
    return [prefix + rest + (sep if is_dir else "") for _, is_dir, rest in matched]


def _append_after_placeholder(matched, sep: str) -> str:
    names = [name for name, _, _ in matched]
    if len(names) > 1:
        suffix = _delimited_suffix(_common_suffix(names))
        # Every name must be strictly longer, or the placeholder would have to
        # match the empty string for one of them.
        if suffix and all(len(n) > len(suffix) for n in names):
            return suffix
    if all(is_dir for _, is_dir, _ in matched):
        return sep
    return ""


def _common_suffix(names) -> str:
    rev = os.path.commonprefix([n[::-1] for n in names])
    return rev[::-1]


def _delimited_suffix(suffix: str) -> str:
    """Trim *suffix* to start at a delimiter, so it cuts on a token boundary."""
    for i, ch in enumerate(suffix):
        if ch in _DELIMS:
            return suffix[i:]
    return ""


def _tail_regex(tail: str) -> re.Pattern:
    """Match a filename against the typed component, placeholders being `*`."""
    parts = [_literal_regex(p) for p in PLACEHOLDER_RE.split(tail)]
    return re.compile(".*?".join(parts))


def _literal_regex(literal: str) -> str:
    return re.escape(literal).replace(r"\*", ".*").replace(r"\?", ".")


def _resolve_dirs(head: str, absolute: bool, listdir, sep: str) -> list[str]:
    """Expand the directory part of a prefix into the real directories it names."""
    current = [sep if absolute else ""]
    for part in head.split(sep):
        if not part:
            continue
        if _is_wild(part):
            pattern = PLACEHOLDER_RE.sub("*", part)
            nxt = [
                _join(d, name, sep)
                for d in current
                for name, is_dir in listdir(d)
                if is_dir and fnmatch.fnmatchcase(name, pattern)
            ]
        else:
            nxt = [_join(d, part, sep) for d in current]
        current = nxt[:_MAX_DIRS]
        if not current:
            return []
    return current


def _is_wild(part: str) -> bool:
    return has_placeholder(part) or "*" in part or "?" in part


def _join(directory: str, name: str, sep: str) -> str:
    if not directory:
        return name
    if directory.endswith(sep):
        return directory + name
    return directory + sep + name


# ── directory listers ────────────────────────────────────────────────────────

def local_listdir():
    """Return a caching `listdir` over the local filesystem."""
    cache: dict[str, list[tuple[str, bool]]] = {}

    def _listdir(path: str) -> list[tuple[str, bool]]:
        if path in cache:
            return cache[path]
        target = os.path.expanduser(path) if path else "."
        if re.fullmatch(r"[A-Za-z]:", target):  # bare Windows drive → its root
            target += "/"
        try:
            with os.scandir(target) as it:
                entries = [(e.name, e.is_dir()) for e in it]
        except OSError:
            entries = []
        cache[path] = entries
        return entries

    return _listdir


def sftp_listdir(sftp):
    """Return a caching `listdir` over an open paramiko SFTP session."""
    cache: dict[str, list[tuple[str, bool]]] = {}

    def _listdir(path: str) -> list[tuple[str, bool]]:
        if path in cache:
            return cache[path]
        try:
            entries = [
                (e.filename, e.st_mode is not None and _stat.S_ISDIR(e.st_mode))
                for e in sftp.listdir_attr(path or ".")
            ]
        except Exception:
            entries = []
        cache[path] = entries
        return entries

    return _listdir


# ── shell completion ─────────────────────────────────────────────────────────
#
# `juxt-complete` backs the bash function in juxt/completion.bash.  It must stay
# import-light — no Qt — so pressing Tab at a shell prompt costs milliseconds.

# Mirrors the parser in juxt/__main__.py; kept in sync by a test.
CLI_OPTIONS = [
    "-a", "--auto",
    "-h", "--help",
    "-s", "--separator",
    "--axis-h", "--axis-v",
    "--grid", "--grid-layout", "--grid-values",
    "--max-depth",
    "--name",
    "--no-sharex", "--no-sharey", "--no-watch",
    "--save",
    "--squeeze",
    "--watch-interval",
]

# Options taking a value juxt cannot know before the images are scanned.
_OPAQUE_VALUE_OPTIONS = {
    "-s", "--separator",
    "--axis-h", "--axis-v",
    "--grid", "--grid-layout", "--grid-values",
    "--max-depth",
    "--name",
    "--watch-interval",
}

# Options whose value is a path, completed like PATH itself.
_PATH_OPTIONS = {"--save"}


def cli_complete(cur: str, prev: str = "") -> list[str]:
    """Candidates for the word *cur*, given the word before it."""
    if prev in _OPAQUE_VALUE_OPTIONS:
        return []
    if prev in (":", "="):
        # COMP_WORDBREAKS split a host:path or --option=value in two.
        return []
    if cur.startswith("-") and prev not in _PATH_OPTIONS:
        return [o for o in CLI_OPTIONS if o.startswith(cur)]

    from .detect import _is_remote_pattern
    if _is_remote_pattern(cur):
        return []  # listing a remote needs a live SFTP session — app only
    return completion_words(cur, local_listdir())


def bash_script() -> str:
    """The bash completion function, for `eval "$(juxt-complete --bash)"`."""
    from pathlib import Path
    return (Path(__file__).parent / "completion.bash").read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Entry point of the `juxt-complete` helper command."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--":
        args = args[1:]
    if args and args[0] in ("--bash", "--zsh"):
        sys.stdout.write(bash_script())
        return 0
    cur = args[-1] if args else ""
    prev = args[-2] if len(args) > 1 else ""
    for word in cli_complete(cur, prev):
        print(word)
    return 0


if __name__ == "__main__":  # python -m juxt.complete, when the script is absent
    raise SystemExit(main())
