"""Accipio's debug log (2026-11-07, decision 72B): off unless switched on in Settings (the family rule for logging).

One line per thing Accipio saw or decided, in ``debug.log`` in Accipio's folder: what the browser offered (and what the
extension left to Chrome before asking), how long each check of a link took and what it found, where the rules put it
and why, the answer sent back to Chrome, how a download ended, and what was decided for an archive. An odd case can
then be read instead of guessed.

Cookies, Authorization and other header values are never written, only that they were there. The file is kept under
MAX_BYTES with one older copy beside it; Settings can delete both.
"""
from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Any, Optional

FILE = "debug.log"
MAX_BYTES = 5 * 1024 * 1024
SECRET_KEYS = {"cookie", "cookies", "authorization", "proxy-authorization", "set-cookie", "token", "secret"}
SHOWN_HEADERS = {"referer", "user-agent", "range"}      # header names whose values may be shown


def _value(key: str, value: Any) -> str:
    if key.lower() in SECRET_KEYS and value:
        return f"<{len(str(value))} characters>"
    if isinstance(value, dict):
        shown = {k: (v if k.lower() in SHOWN_HEADERS else f"<{len(str(v))} characters>") for k, v in value.items()}
        return json.dumps(shown, ensure_ascii=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class DebugLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.path: Optional[str] = None             # None: off

    def configure(self, folder: str, on: bool) -> None:
        self.path = os.path.join(folder, FILE) if on else None
        self._folder = folder

    @property
    def on(self) -> bool:
        return self.path is not None

    def note(self, event: str, **fields: Any) -> None:
        if self.path is None:
            return
        stamp = datetime.datetime.now().isoformat(sep=" ", timespec="milliseconds")
        line = f"{stamp}  {event}  " + " ".join(f"{k}={_value(k, v)}" for k, v in fields.items())
        with self._lock:
            try:
                if os.path.exists(self.path) and os.path.getsize(self.path) > MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
                with open(self.path, "a", encoding="utf-8") as out:
                    out.write(line.replace("\n", " ") + "\n")
            except OSError:
                pass                                # the log must never stop a download

    def files(self) -> list:
        base = os.path.join(getattr(self, "_folder", ""), FILE)
        return [base + ".1", base]

    def report(self, lines: int = 400, header: str = "") -> str:
        """For "Copy for bug report": the header (version, channel) and the last `lines` lines."""
        found = []
        for path in self.files():
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    found += f.read().splitlines()
            except OSError:
                continue
        body = "\n".join(found[-lines:]) if found else "(the debug log is empty)"
        return f"{header}\n{body}" if header else body

    def delete(self) -> None:
        with self._lock:
            for path in self.files():
                try:
                    os.remove(path)
                except OSError:
                    pass


LOG = DebugLog()            # the app's one log; configured from Settings at start and when Settings change
note = LOG.note
