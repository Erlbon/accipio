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
from multiprocessing.connection import Client, Listener, answer_challenge, deliver_challenge
from typing import Callable, Dict, Optional

Handler = Callable[[Dict], Dict]
TIMEOUT = 10.0                       # seconds; the browser waits on the native host, which waits on this


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


def send(folder: str, message: Dict, timeout: float = TIMEOUT) -> Dict:
    """Send one message to the running Accipio and return its answer. Raises OSError when none is running,
    or when it does not answer within ``timeout`` seconds (the exchange runs on a thread of its own, because
    multiprocessing's key handshake and ``recv`` have no time limit and a stuck peer would hang the caller)."""
    result: Dict = {}

    def exchange():
        try:
            with Client(address(folder), authkey=key(folder)) as connection:
                connection.send(message)
                if not connection.poll(timeout):
                    raise TimeoutError("no answer")
                result["reply"] = connection.recv()
        except BaseException as error:                    # handed to the caller below
            result["error"] = error
    worker = threading.Thread(target=exchange, name="accipio-ipc-send", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise OSError(f"Accipio did not answer within {timeout:.0f} seconds")
    error = result.get("error")
    if isinstance(error, (EOFError, OSError, AuthenticationError)):
        raise OSError(f"Accipio did not answer: {error}") from None
    if error is not None:
        raise error
    return result["reply"]


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
        # no authkey here: the key check happens per connection in _answer, so a client that stalls in the
        # handshake can never block accept() for everyone after it
        self._listener = Listener(where)
        self._key = key(self.folder)
        self._thread = threading.Thread(target=self._serve, name="accipio-ipc", daemon=True)
        self._thread.start()
        return self

    def _serve(self) -> None:
        while not self._stopping:
            try:
                connection = self._listener.accept()
            except Exception:                             # a dropped client, or the listener closing
                if self._stopping:
                    return
                continue
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection) -> None:
        with connection:
            try:
                deliver_challenge(connection, self._key)  # the same mutual check Listener(authkey=...) does
                answer_challenge(connection, self._key)
                if not connection.poll(TIMEOUT):
                    return
                message = connection.recv()
                try:
                    reply = self.handler(message) if isinstance(message, dict) else {"ok": False, "error": "not a message"}
                except Exception as error:                # the handler's trouble is the client's answer, not a crash
                    reply = {"ok": False, "error": str(error)}
                connection.send(reply)
            except (EOFError, OSError, AuthenticationError):
                pass                                      # a wrong key or a client that went away

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
