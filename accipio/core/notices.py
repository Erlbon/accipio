"""Notifications: what to tell the user when downloads are added, finish or fail.

The window hands every look at the queue to ``Notices.observe``; it compares each row with how it was the
time before and notes what changed. Changes of one kind are held for a moment (``GATHER`` seconds) and then
told as ONE notice, so a playlist of 20 videos or a batch of links finishing together gives "20 downloads
added", not twenty pop-ups. The first look only learns what is there: nothing already in the queue at start
is announced. Qt-free; the clock is passed in.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from accipio.core.store import COMPLETE, ERROR, Row

ADDED = "added"
FINISHED = "finished"
FAILED = "failed"
KINDS = (ADDED, FINISHED, FAILED)
GATHER = 1.5             # seconds a kind waits for more of its kind before it is told
NAMES_SHOWN = 3


@dataclass
class Notice:
    kind: str
    title: str
    text: str
    path: str = ""       # the one finished file, so a click can show it; empty for a group


def display_name(row: Row) -> str:
    if row.filename:
        return row.filename
    tail = row.url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return tail or row.url


def _names(rows: List[Row]) -> str:
    names = [display_name(r) for r in rows[:NAMES_SHOWN]]
    if len(rows) > NAMES_SHOWN:
        names.append(f"and {len(rows) - NAMES_SHOWN} more")
    return "\n".join(names)


def describe(kind: str, rows: List[Row], base_folder: str = "") -> Notice:
    """One notice for one or many rows of the same kind."""
    def where(row: Row) -> str:
        if not row.folder:
            return ""
        try:
            shown = os.path.relpath(row.folder, base_folder) if base_folder else row.folder
        except ValueError:                                  # another drive
            shown = row.folder
        return row.folder if shown.startswith("..") else shown

    if len(rows) == 1:
        row = rows[0]
        name = display_name(row)
        if kind == ADDED:
            folder = where(row)
            return Notice(kind, "Download added", f"{name}\nto {folder}" if folder else name)
        if kind == FINISHED:
            folder = where(row)
            return Notice(kind, "Download finished", f"{name}\nin {folder}" if folder else name, row.path)
        return Notice(kind, "Download failed", f"{name}\n{row.error}" if row.error else name)
    word = {ADDED: "added", FINISHED: "finished", FAILED: "failed"}[kind]
    return Notice(kind, f"{len(rows)} downloads {word}", _names(rows))


class Notices:
    def __init__(self, enabled: Iterable[str] = KINDS, gather: float = GATHER):
        self.enabled = set(enabled)
        self.gather = gather
        self._seen: Optional[Dict[int, str]] = None    # id -> state at the last look; None before the first
        self._latest: Dict[int, Row] = {}
        self._waiting: Dict[str, List[int]] = {}
        self._since: Dict[str, float] = {}

    def observe(self, rows: Iterable[Row], now: float) -> None:
        rows = list(rows)
        self._latest = {r.id: r for r in rows}
        before, self._seen = self._seen, {r.id: r.state for r in rows}
        if before is None:
            return
        for row in rows:
            old = before.get(row.id)
            if old is None:
                self._note(ADDED, row.id, now)
            if row.state != old and row.state == COMPLETE:
                self._note(FINISHED, row.id, now)
            elif row.state != old and row.state == ERROR:
                self._note(FAILED, row.id, now)

    def _note(self, kind: str, row_id: int, now: float) -> None:
        if kind not in self.enabled:
            return
        self._waiting.setdefault(kind, []).append(row_id)
        self._since.setdefault(kind, now)

    def due(self, now: float, base_folder: str = "") -> List[Notice]:
        """The notices whose gathering time is over, in the order added, finished, failed. Rows removed from
        the list in the meantime are left out."""
        told = []
        for kind in KINDS:
            if kind in self._since and now - self._since[kind] >= self.gather:
                del self._since[kind]
                rows = [self._latest[i] for i in self._waiting.pop(kind) if i in self._latest]
                if rows:
                    told.append(describe(kind, rows, base_folder))
        return told
