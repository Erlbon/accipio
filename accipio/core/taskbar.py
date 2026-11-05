"""What the taskbar button shows while Accipio works (picked 2026-11-05, mockup 47C).

A bar along the button for all unfinished downloads together: green while any run, yellow when all are paused,
red while a failure waits to be seen, sliding when no size is known. A badge counts the running downloads (the
paused ones when nothing runs), and the window title, shown when the button is hovered, says the same in words.
Qt-free; ``gui/taskbar_button.py`` hands the result to Windows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from accipio.core import store
from accipio.core.store import UNFINISHED, Row
from accipio.core.version import APP_NAME

NONE = "none"           # the button's modes
NORMAL = "normal"
PAUSED = "paused"
ERROR = "error"
SLIDING = "sliding"
BADGE_MAX = 9


@dataclass(frozen=True)
class Look:
    mode: str
    percent: int
    count: int        # the number on the badge; 0 = no badge
    title: str


def badge_text(count: int) -> str:
    if count <= 0:
        return ""
    return str(count) if count <= BADGE_MAX else f"{BADGE_MAX}+"


def look(rows: Iterable[Row], failed: int) -> Look:
    """The button for these rows, with ``failed`` failures nobody has seen yet."""
    rows = list(rows)
    unfinished = [r for r in rows if r.state in UNFINISHED]
    paused = sum(1 for r in unfinished if r.state == store.PAUSED)
    running = len(unfinished) - paused
    sized = [r for r in unfinished if r.total > 0]
    percent: Optional[int] = None
    if sized:
        percent = int(100 * sum(min(r.done, r.total) for r in sized) / sum(r.total for r in sized))

    if failed:
        mode = ERROR
    elif running:
        mode = NORMAL if percent is not None else SLIDING
    elif paused:
        mode = PAUSED
    else:
        return Look(NONE, 0, 0, APP_NAME)

    words = []
    if running:
        words.append(f"{running} downloading")
    if paused:
        words.append(f"{paused} paused")
    if failed:
        words.append(f"{failed} failed")
    if percent is not None:
        words.append(f"{percent}%")
    if mode == ERROR and not unfinished:
        percent = 100                                  # a full red bar: something went wrong, look
    return Look(mode, percent or 0, running or paused, f"{APP_NAME} – {', '.join(words)}")


class UnreadFailures:
    """Failures that happened while the window was not in front. Bringing it to the front reads them all;
    a failed row that is removed or retried stops counting. Failures already there at start are not news."""

    def __init__(self):
        self._states: Optional[Dict[int, str]] = None
        self._unread: set = set()

    @property
    def count(self) -> int:
        return len(self._unread)

    def observe(self, rows: Iterable[Row], looking: bool) -> None:
        rows = list(rows)
        before, self._states = self._states, {r.id: r.state for r in rows}
        if looking:
            self._unread.clear()
            return
        self._unread &= {r.id for r in rows if r.state == store.ERROR}
        if before is None:
            return
        for r in rows:
            if r.state == store.ERROR and before.get(r.id, store.ERROR) != store.ERROR:
                self._unread.add(r.id)
