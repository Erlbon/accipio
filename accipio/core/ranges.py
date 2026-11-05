"""Link patterns that stand for many links, as in curl's URL globbing and DownThemAll's batch downloads.

    https://x.org/page[1-20].jpg          page1 ... page20
    https://x.org/img[001-150].png        img001 ... img150 (the leading zeros are kept)
    https://x.org/v[1-100:5].zip          v1, v6, v11 ... (a step after a colon; [20-1] counts down)
    https://x.org/[a-z].pdf               a ... z (or [A-Z])
    https://x.org/{intro,main,outro}.mp3  the three names
    https://x.org/s[1-3]/e[01-10].mkv     every combination, the leftmost changing slowest

Only well-formed pieces count, so ordinary links that happen to hold brackets or braces stay one link: an IPv6
host (``http://[::1]/``), a query such as ``?ids[]=4``, a brace pair without a comma. Plain functions, no Qt,
no network.
"""
from __future__ import annotations

import itertools
import re
from typing import List

LIMIT = 1000                    # more links than this from one pattern: ask before expanding (one typo can make millions)

_PIECE = re.compile(r"\[(\d+)-(\d+)(?::(\d+))?\]|\[([a-z])-([a-z])\]|\[([A-Z])-([A-Z])\]|\{([^{}\[\]/]*,[^{}\[\]/]*)\}")


class PatternError(ValueError):
    pass


def _numbers(first: str, last: str, step: str) -> List[str]:
    start, end = int(first), int(last)
    every = int(step) if step else 1
    if every <= 0:
        raise PatternError(f"the step in [{first}-{last}:{step}] must be 1 or more")
    width = len(first) if first.startswith("0") and len(first) > 1 else 0
    direction = 1 if end >= start else -1
    return [str(n).zfill(width) for n in range(start, end + direction, every * direction)]


def _letters(first: str, last: str) -> List[str]:
    direction = 1 if last >= first else -1
    return [chr(c) for c in range(ord(first), ord(last) + direction, direction)]


def _choices(match: "re.Match") -> List[str]:
    if match.group(1) is not None:
        return _numbers(match.group(1), match.group(2), match.group(3) or "")
    if match.group(4) is not None:
        return _letters(match.group(4), match.group(5))
    if match.group(6) is not None:
        return _letters(match.group(6), match.group(7))
    return match.group(8).split(",")


def has_pattern(link: str) -> bool:
    return _PIECE.search(link) is not None


def _parts(link: str):
    """The link cut into fixed text and lists of choices, alternately: ["a/", [1, 2], ".jpg"]."""
    parts, at = [], 0
    for match in _PIECE.finditer(link):
        parts.append(link[at:match.start()])
        parts.append(_choices(match))
        at = match.end()
    parts.append(link[at:])
    return parts


def count(link: str) -> int:
    """How many links the pattern stands for (1 for a plain link), without making them."""
    total = 1
    for part in _parts(link)[1::2]:
        total *= len(part)
    return total


def expand(link: str) -> List[str]:
    """Every link the pattern stands for, in order; a plain link gives itself. Raises PatternError for a
    pattern that cannot work (a step of 0)."""
    parts = _parts(link)
    fixed, choices = parts[0::2], parts[1::2]
    out = []
    for picked in itertools.product(*choices):
        out.append(fixed[0] + "".join(p + f for p, f in zip(picked, fixed[1:])))
    return out
