"""aria2c as the engine for direct downloads.

Accipio starts its own aria2c on 127.0.0.1 with a random port and secret, talks to it over JSON-RPC, and
shuts it down on exit. aria2c also watches Accipio's process (``--stop-with-process``), so a crash never
leaves it running. The queue is saved to a session file, so unfinished downloads come back after a restart.

Accipio decides the folder and the file name before a download starts (``rules.place`` and
``rules.free_name``). aria2 is told both and never renames or overwrites anything itself: a file that is
already there makes the download fail instead. ``--continue`` stays OFF: with it, aria2 treats any file of
that name as a half-finished download and appends to it, which ruins a file you already had (a test pins
this). Resuming still works, because aria2 resumes from its own ``.aria2`` control file without it.

A small stdlib client is used instead of aria2p: Accipio needs a dozen calls, and fewer dependencies keep
the exe small.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

EXE_NAME = "aria2c.exe" if sys.platform == "win32" else "aria2c"
FILE_EXISTS = "13"         # aria2's exit codes, as they come back in a status
CANNOT_RESUME = "8"
NOT_FOUND = "3"
STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "connections",
               "errorCode", "errorMessage", "dir", "files"]


class Aria2Error(RuntimeError):
    """aria2c could not start, or refused a call; the message is fit to show."""


def find_aria2c() -> Optional[str]:
    """The aria2c to use: next to the exe (a frozen build), in the checkout's ``bin/``, or on PATH."""
    places = []
    if getattr(sys, "frozen", False):
        places.append(Path(sys.executable).parent / "aria2" / EXE_NAME)
        if hasattr(sys, "_MEIPASS"):
            places.append(Path(sys._MEIPASS) / "aria2" / EXE_NAME)
    places.append(Path(__file__).resolve().parents[2] / "bin" / "aria2" / EXE_NAME)
    for place in places:
        if place.is_file():
            return str(place)
    return shutil.which("aria2c")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Status:
    gid: str
    state: str                   # active, waiting, paused, error, complete, removed
    total: int                   # bytes; 0 while unknown
    done: int
    speed: int                   # bytes per second
    connections: int
    path: str
    error_code: str = ""
    error_message: str = ""

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    @classmethod
    def from_rpc(cls, data: Dict[str, Any]) -> "Status":
        files = data.get("files") or [{}]
        return cls(gid=data.get("gid", ""), state=data.get("status", ""),
                   total=int(data.get("totalLength", 0)), done=int(data.get("completedLength", 0)),
                   speed=int(data.get("downloadSpeed", 0)), connections=int(data.get("connections", 0)),
                   path=files[0].get("path", ""), error_code=data.get("errorCode", "0") if data.get("status") == "error" else "",
                   error_message=data.get("errorMessage", ""))


class Aria2:
    """One aria2c process owned by Accipio. Use as a context manager, or call start() and stop()."""

    def __init__(self, exe: str, session_file: str, *, max_downloads: int = 3, connections: int = 8,
                 log_file: Optional[str] = None):
        self.exe = exe
        self.session_file = session_file
        self.max_downloads = max_downloads
        self.connections = connections
        self.log_file = log_file
        self.port = 0
        self.secret = ""
        self.process: Optional[subprocess.Popen] = None
        self._ids = 0
        # never send the local RPC through a system proxy
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ---- the process -------------------------------------------------------------------------------------

    def command_line(self) -> List[str]:
        args = [
            self.exe,
            "--enable-rpc=true", "--rpc-listen-all=false",
            f"--rpc-listen-port={self.port}", f"--rpc-secret={self.secret}",
            f"--stop-with-process={os.getpid()}",
            "--continue=false", "--auto-file-renaming=false", "--allow-overwrite=false",
            "--file-allocation=none",
            f"--max-concurrent-downloads={self.max_downloads}",
            f"--max-connection-per-server={min(self.connections, 16)}", f"--split={self.connections}",
            "--min-split-size=1M",
            "--max-tries=5", "--retry-wait=5", "--connect-timeout=15", "--timeout=30",
            f"--save-session={self.session_file}", "--save-session-interval=30", "--auto-save-interval=10",
            "--summary-interval=0", "--console-log-level=warn", "--quiet=true",
        ]
        if os.path.isfile(self.session_file) and os.path.getsize(self.session_file):
            args.append(f"--input-file={self.session_file}")
        if self.log_file:
            args += [f"--log={self.log_file}", "--log-level=notice"]
        return args

    def start(self, wait: float = 10.0) -> "Aria2":
        os.makedirs(os.path.dirname(os.path.abspath(self.session_file)), exist_ok=True)
        self.port = _free_port()
        self.secret = secrets.token_hex(16)
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self.process = subprocess.Popen(self.command_line(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=flags)
        deadline = time.monotonic() + wait
        while True:
            code = self.process.poll()
            if code is not None:
                raise Aria2Error(f"aria2c stopped as it started (exit code {code})")
            try:
                self.call("getVersion")
                return self
            except (OSError, Aria2Error):
                if time.monotonic() > deadline:
                    self.process.kill()
                    raise Aria2Error(f"aria2c did not answer within {wait:.0f} seconds")
                time.sleep(0.05)

    def stop(self, wait: float = 0.0) -> None:
        """Save the session, then ask aria2 to shut down.

        aria2c on Windows takes about four seconds to exit after a shutdown request, whatever its options,
        so by default the app does not wait: the session is already saved, and aria2 finishes on its own
        (``--stop-with-process`` covers the app being gone first). With ``wait`` it is given that long,
        then killed.
        """
        if not self.process or self.process.poll() is not None:
            return
        try:
            self.call("saveSession")
            self.call("forceShutdown")
        except (OSError, Aria2Error):
            self.process.kill()
            self.process.wait(5)
            return
        if wait:
            try:
                self.process.wait(wait)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)

    def kill(self) -> None:
        """Stop at once (tests, or an app that must quit now): the session is saved first when aria2 answers."""
        if not self.process or self.process.poll() is not None:
            return
        try:
            self.call("saveSession", timeout=2)
        except (OSError, Aria2Error):
            pass
        self.process.kill()
        self.process.wait(5)

    def __enter__(self) -> "Aria2":
        return self.start() if not self.process else self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ---- JSON-RPC ----------------------------------------------------------------------------------------

    def call(self, method: str, *params: Any, timeout: float = 10.0) -> Any:
        self._ids += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._ids, "method": "aria2." + method,
                           "params": ["token:" + self.secret, *params]}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}/jsonrpc", data=body,
                                         headers={"Content-Type": "application/json"})
        try:
            with self._opener.open(request, timeout=timeout) as response:
                reply = json.load(response)
        except urllib.error.HTTPError as error:       # aria2 answers a refused call with 400 and a JSON body
            try:
                reply = json.load(error)
            except ValueError:
                raise Aria2Error(f"aria2 answered {error.code} to {method}") from None
        if "error" in reply:
            raise Aria2Error(reply["error"].get("message", f"aria2 refused {method}"))
        return reply.get("result")

    # ---- downloads ---------------------------------------------------------------------------------------

    def add(self, url: str, folder: str, filename: str, *, headers: Iterable[str] = (), referer: str = "",
            user_agent: str = "", cookies: str = "", paused: bool = False) -> str:
        """Start a download into folder/filename (the folder is created if needed). Returns aria2's id."""
        options: Dict[str, Any] = {"dir": folder, "out": filename}
        all_headers = list(headers)
        if cookies:
            all_headers.append("Cookie: " + cookies)
        if all_headers:
            options["header"] = all_headers
        if referer:
            options["referer"] = referer
        if user_agent:
            options["user-agent"] = user_agent
        if paused:
            options["pause"] = "true"
        return self.call("addUri", [url], options)

    def pause(self, gid: str) -> None:
        self.call("pause", gid)

    def resume(self, gid: str) -> None:
        self.call("unpause", gid)

    def remove(self, gid: str) -> None:
        """Stop and forget a download (a finished one is only forgotten). Its partial file stays."""
        try:
            self.call("remove", gid)
        except Aria2Error:
            pass                                      # already finished or failed
        try:
            self.call("removeDownloadResult", gid)
        except Aria2Error:
            pass

    def restart(self, gid: str) -> str:
        """Start a download again from zero, under the same name and options. Returns the new id.

        For a server that cannot resume: resuming after a pause or a restart then fails with
        ``CANNOT_RESUME``, and starting over is the only way. The half-finished file and aria2's
        ``.aria2`` control file are deleted first; nothing else is touched.
        """
        info = self.call("tellStatus", gid, ["files", "dir"])
        options = self.call("getOption", gid)
        uris = list(dict.fromkeys(u["uri"] for u in info["files"][0].get("uris", [])))
        path = info["files"][0].get("path") or os.path.join(options["dir"], options.get("out", ""))
        self.remove(gid)
        for leftover in (path, path + ".aria2"):
            if leftover and os.path.isfile(leftover):
                os.remove(leftover)
        kept = {k: v for k, v in options.items() if k in ("dir", "out", "header", "referer", "user-agent")}
        return self.call("addUri", uris, kept)

    def status(self, gid: str) -> Status:
        return Status.from_rpc(self.call("tellStatus", gid, STATUS_KEYS))

    def all(self) -> List[Status]:
        """Every download aria2 knows: running, waiting or paused, and stopped."""
        found = self.call("tellActive", STATUS_KEYS)
        found += self.call("tellWaiting", 0, 1000, STATUS_KEYS)
        found += self.call("tellStopped", 0, 1000, STATUS_KEYS)
        return [Status.from_rpc(d) for d in found]

    def set_limits(self, *, max_downloads: Optional[int] = None, speed_limit: Optional[int] = None) -> None:
        """Change how many run at once, and the total speed limit in bytes per second (0 = none)."""
        options = {}
        if max_downloads is not None:
            options["max-concurrent-downloads"] = str(max_downloads)
        if speed_limit is not None:
            options["max-overall-download-limit"] = str(speed_limit)
        if options:
            self.call("changeGlobalOption", options)

    def save_session(self) -> None:
        self.call("saveSession")
