"""The local line between the running Accipio and the outside: the browser's native host, and a second
start of Accipio (which hands over and quits, so only one Accipio runs).

A named pipe on Windows, a Unix socket elsewhere, both reachable only from this user's session. A random key
in the config folder (``ipc.key``) must be known to connect, so another program cannot send downloads in.
Messages are small dicts; every message gets one dict back.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
import threading
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client, Listener
from typing import Callable, Dict, Optional

Handler = Callable[[Dict], Dict]


def address(folder: str) -> str:
    if sys.platform == "win32":
        tag = hashlib.sha256(os.path.abspath(folder).lower().encode()).hexdigest()[:16]
        return rf"\\.\pipe\accipio-{tag}"
    return os.path.join(folder, "ipc.sock")


def key(folder: str) -> bytes:
    """The shared key, made on first use. Only this user can read the config folder."""
    path = os.path.join(folder, "ipc.key")
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) >= 32:
            return data
    except FileNotFoundError:
        pass
    os.makedirs(folder, exist_ok=True)
    data = secrets.token_bytes(32)
    with open(path, "wb") as f:
        f.write(data)
    return data


def send(folder: str, message: Dict) -> Dict:
    """Send one message to the running Accipio and return its answer. Raises OSError when none is running."""
    try:
        with Client(address(folder), authkey=key(folder)) as connection:
            connection.send(message)
            return connection.recv()
    except (EOFError, ConnectionError, AuthenticationError) as error:
        raise OSError(f"Accipio did not answer: {error}") from None


def is_running(folder: str) -> bool:
    try:
        return bool(send(folder, {"type": "ping"}).get("ok"))
    except OSError:
        return False


class Server:
    """Accepts connections on a thread and answers each message with ``handler(message)``."""

    def __init__(self, folder: str, handler: Handler):
        self.folder = folder
        self.handler = handler
        self._listener: Optional[Listener] = None
        self._thread: Optional[threading.Thread] = None
        self._stopping = False

    def start(self) -> "Server":
        where = address(self.folder)
        if sys.platform != "win32" and os.path.exists(where):
            os.remove(where)                              # left behind by a crash; is_running() said no one is there
        self._listener = Listener(where, authkey=key(self.folder))
        self._thread = threading.Thread(target=self._serve, name="accipio-ipc", daemon=True)
        self._thread.start()
        return self

    def _serve(self) -> None:
        while not self._stopping:
            try:
                connection = self._listener.accept()
            except Exception:                             # a wrong key, a dropped client, or the listener closing
                if self._stopping:
                    return
                continue
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection) -> None:
        with connection:
            try:
                message = connection.recv()
                try:
                    reply = self.handler(message) if isinstance(message, dict) else {"ok": False, "error": "not a message"}
                except Exception as error:                # the handler's trouble is the client's answer, not a crash
                    reply = {"ok": False, "error": str(error)}
                connection.send(reply)
            except (EOFError, OSError):
                pass

    def stop(self) -> None:
        self._stopping = True
        try:
            send(self.folder, {"type": "ping"})           # wakes accept() so the thread can end
        except OSError:
            pass
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(2)
