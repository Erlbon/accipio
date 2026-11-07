"""The queue on disk: one SQLite file, so downloads survive a crash or a restart.

The database runs in WAL mode and every change is its own short transaction, so a crash loses at most the
change being written. One connection is shared behind a lock: the window, the poll timer and background
workers may all call in.

Cookies and other request headers are kept only while a download is unfinished (a restart from zero needs
them) and are wiped when it completes or is removed.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = 2          # 2 (2026-11-05): the unpack column

# states a row can be in
PROBING = "probing"       # asking the server what the link is
ACTIVE = "active"         # handed to the engine (running or waiting for a slot)
PAUSED = "paused"
COMPLETE = "complete"
ERROR = "error"
UNFINISHED = (PROBING, ACTIVE, PAUSED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    url       TEXT NOT NULL,
    page_url  TEXT NOT NULL DEFAULT '',
    filename  TEXT NOT NULL DEFAULT '',
    folder    TEXT NOT NULL DEFAULT '',
    mime      TEXT NOT NULL DEFAULT '',
    size      INTEGER,
    source    TEXT NOT NULL DEFAULT 'direct',
    video     TEXT NOT NULL DEFAULT '{}',
    headers   TEXT NOT NULL DEFAULT '{}',
    state     TEXT NOT NULL,
    gid       TEXT NOT NULL DEFAULT '',
    done      INTEGER NOT NULL DEFAULT 0,
    total     INTEGER NOT NULL DEFAULT 0,
    resumable INTEGER NOT NULL DEFAULT 0,
    restarts  INTEGER NOT NULL DEFAULT 0,
    pinned    INTEGER NOT NULL DEFAULT 0,
    error     TEXT NOT NULL DEFAULT '',
    rules     TEXT NOT NULL DEFAULT '',
    why       TEXT NOT NULL DEFAULT '',
    added     TEXT NOT NULL,
    finished  TEXT NOT NULL DEFAULT '',
    unpack    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS downloads_state ON downloads(state);
"""


@dataclass
class Row:
    id: int
    url: str
    page_url: str = ""
    filename: str = ""
    folder: str = ""
    mime: str = ""
    size: Optional[int] = None
    source: str = "direct"
    video: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    state: str = PROBING
    gid: str = ""
    done: int = 0
    total: int = 0
    resumable: bool = False
    restarts: int = 0
    pinned: bool = False            # folder chosen by hand in the Add box: the rules never move it
    error: str = ""
    rules: str = ""                 # the table's Rule column, "1+5"
    why: str = ""
    added: str = ""                 # ISO time the download was added; time rules use it
    finished: str = ""
    unpack: Dict[str, Any] = field(default_factory=dict)    # a finished archive: waiting/done/kept/failed (unpack.py)

    @property
    def path(self) -> str:
        return os.path.join(self.folder, self.filename) if self.filename else ""

    @property
    def added_at(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.added)


_COLUMNS = [f.name for f in fields(Row)]
_JSON = ("video", "headers", "unpack")


class Store:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            version = self._db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"{path} was written by a newer Accipio (schema {version}); update Accipio")
            self._db.executescript(_SCHEMA)
            have = {r[1] for r in self._db.execute("PRAGMA table_info(downloads)")}
            if "unpack" not in have:                                     # a schema 1 file from before
                self._db.execute("ALTER TABLE downloads ADD COLUMN unpack TEXT NOT NULL DEFAULT '{}'")
            self._db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _to_db(values: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for key, value in values.items():
            if key not in _COLUMNS or key == "id":
                raise KeyError(f"no column {key!r}")
            if key in _JSON:
                value = json.dumps(value or {}, ensure_ascii=False)
            elif key in ("resumable", "pinned"):
                value = int(bool(value))
            out[key] = value
        return out

    @staticmethod
    def _from_db(record: sqlite3.Row) -> Row:
        data = dict(record)
        for key in _JSON:
            data[key] = json.loads(data[key] or "{}")
        data["resumable"] = bool(data["resumable"])
        data["pinned"] = bool(data["pinned"])
        return Row(**data)

    def add(self, url: str, **values: Any) -> int:
        values.setdefault("state", PROBING)
        values.setdefault("added", datetime.datetime.now().isoformat(timespec="seconds"))
        data = self._to_db({"url": url, **values})
        names = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        with self._lock:
            cursor = self._db.execute(f"INSERT INTO downloads ({names}) VALUES ({marks})", list(data.values()))
            return cursor.lastrowid

    def update(self, row_id: int, **values: Any) -> None:
        if not values:
            return
        data = self._to_db(values)
        assignments = ", ".join(f"{k} = ?" for k in data)
        with self._lock:
            self._db.execute(f"UPDATE downloads SET {assignments} WHERE id = ?", [*data.values(), row_id])

    def get(self, row_id: int) -> Optional[Row]:
        with self._lock:
            record = self._db.execute("SELECT * FROM downloads WHERE id = ?", (row_id,)).fetchone()
        return self._from_db(record) if record else None

    def rows(self, states: Optional[Iterable[str]] = None) -> List[Row]:
        query, params = "SELECT * FROM downloads", []
        if states is not None:
            states = list(states)
            query += f" WHERE state IN ({', '.join('?' for _ in states)})"
            params = states
        with self._lock:
            records = self._db.execute(query + " ORDER BY id", params).fetchall()
        return [self._from_db(r) for r in records]

    def by_gid(self, gid: str) -> Optional[Row]:
        with self._lock:
            record = self._db.execute("SELECT * FROM downloads WHERE gid = ? ORDER BY id DESC", (gid,)).fetchone()
        return self._from_db(record) if record else None

    def delete(self, row_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM downloads WHERE id = ?", (row_id,))

    def forget_finished(self, before: str = "", keep_waiting: bool = False) -> int:
        """Remove completed rows (Edit > Clear Finished: all of them). `before`: only those that finished before that
        moment (an ISO time, as `finished` holds it). `keep_waiting`: not an archive still waiting for the user's
        choice (the automatic clearing, Settings). Returns how many went."""
        with self._lock:
            found = self._db.execute("SELECT id, finished, unpack FROM downloads WHERE state = ?",
                                     (COMPLETE,)).fetchall()
            gone = []
            for row_id, finished, unpack in found:
                if before and not (finished and finished < before):
                    continue
                if keep_waiting:
                    try:
                        waiting = json.loads(unpack or "{}").get("state") == "waiting"
                    except (ValueError, AttributeError):
                        waiting = False
                    if waiting:
                        continue
                gone.append((row_id,))
            self._db.executemany("DELETE FROM downloads WHERE id = ?", gone)
            return len(gone)
