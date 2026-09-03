"""Case-insensitive fuzzy matching for juxt's search surfaces.

Command mode, seek mode and the value picker all filter a pool of strings by
what the user has typed so far.  Prefix matching is the strictest possible
reading of that input: it cannot find ``SMAP`` from ``smp``, and it cannot
find ``fit-height`` from ``fh``.  Matching a *subsequence* instead — every
typed character appearing in order, not necessarily adjacent — finds both,
at the cost of a longer candidate list.

Ordering carries the weight that strictness used to.  Candidates are ranked
so the reading the user most likely meant comes first, which keeps the
existing keys usable: the first candidate is what Enter takes, and a query
matching exactly one candidate still auto-confirms in seek mode.
"""
from __future__ import annotations

from collections.abc import Iterable

# Characters that start a new "word", so a match just after one reads as an
# initial rather than a coincidence: fh → fit-height beats fh → fullscreen.
_BOUNDARY = "-_. /:="


def _positions(query: str, candidate: str) -> list[int] | None:
    """Leftmost index of each *query* character in *candidate*, in order.

    None when *candidate* does not contain *query* as a subsequence.  Both
    arguments are expected to be lowercase already.
    """
    out: list[int] = []
    i = 0
    for ch in query:
        i = candidate.find(ch, i)
        if i < 0:
            return None
        out.append(i)
        i += 1
    return out


def score(query: str, candidate: str) -> tuple | None:
    """Sort key for *candidate* under *query* — smaller is a better match.

    Returns None when the two do not match at all.  The key ranks, in order:

    1. exact match, then prefix match, then plain subsequence
    2. fewer jumps — a jump is a gap that does not land on a word boundary,
       so ``fh`` prefers ``fit-height`` over ``fullscreen``
    3. an earlier first match, then a tighter span, then a shorter candidate
    """
    q, c = query.lower(), candidate.lower()
    if not q:
        return (0, 0, 0, 0, len(candidate))
    pos = _positions(q, c)
    if pos is None:
        return None
    tier = 0 if c == q else 1 if c.startswith(q) else 2
    jumps = sum(
        1
        for n, p in enumerate(pos)
        if n and p != pos[n - 1] + 1 and not (p and c[p - 1] in _BOUNDARY)
    )
    return (tier, jumps, pos[0], pos[-1] - pos[0], len(candidate))


def fuzzy_filter(query: str, pool: Iterable[str]) -> list[str]:
    """The members of *pool* matching *query*, best match first.

    An empty query matches everything and preserves the pool's own order, as
    does a tie between two equally good matches.
    """
    items = list(pool)
    if not query:
        return items
    scored = [(s, c) for c in items if (s := score(query, c)) is not None]
    scored.sort(key=lambda pair: pair[0])
    return [c for _, c in scored]


def prefix_filter(query: str, pool: Iterable[str]) -> list[str]:
    """Case-insensitive prefix match — what juxt did before fuzzy matching."""
    items = list(pool)
    if not query:
        return items
    q = query.lower()
    return [c for c in items if c.lower().startswith(q)]
