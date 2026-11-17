"""The native messaging host Chrome starts for the extension: it reads one message, passes it to the running
Accipio over the local pipe, and writes Accipio's answer back.

If Accipio is not running, it is started and the message is passed on once it answers (a download clicked
with Accipio closed still arrives). If it cannot be reached at all, the answer says so and the extension lets
Chrome download the file itself.

Chrome's framing: a 4-byte length in native byte order, then that many bytes of UTF-8 JSON, both ways.
Nothing else may ever be written to stdout. Only the standard library is imported, so it starts fast.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
from typing import BinaryIO, Callable, Dict, List, Optional

from accipio.core import ipc, settings

START_WAIT = 20.0                     # seconds to wait for a freshly started Accipio to answer


def read_message(stream: BinaryIO) -> Optional[Dict]:
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    return json.loads(stream.read(length).decode("utf-8"))


def write_message(stream: BinaryIO, message: Dict) -> None:
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("=I", len(data)) + data)
    stream.flush()


def app_command() -> List[str]:
    """How to start Accipio's window: the exe when frozen, else this Python running the package."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    gui_python = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return [gui_python if os.path.isfile(gui_python) else sys.executable, "-m", "accipio"]


def launch_app() -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    subprocess.Popen(app_command(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, close_fds=True, env=env, cwd=os.path.expanduser("~"))


def relay(message: Dict, folder: str, launch: Callable[[], None] = launch_app, wait: float = START_WAIT,
          sleep: Callable[[float], None] = time.sleep) -> Dict:
    """Pass one message to Accipio, starting it first when needed (not for a mere ping, or the extension's note
    about a download it left to Chrome: that only matters to a running Accipio's debug log; and not for a file the
    browser saved itself when Settings say to leave those alone)."""
    try:
        return ipc.send(folder, message)
    except OSError:
        if message.get("type") in ("ping", "seen"):
            return {"ok": False, "error": "Accipio is not running"}
        if message.get("type") == "file" and not settings.load_settings(folder).file_browser_saves:
            return {"ok": True, "filed": False}       # switched off in Settings: nothing to start Accipio for
    launch()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        sleep(0.25)
        try:
            return ipc.send(folder, message)
        except OSError:
            continue
    return {"ok": False, "error": f"Accipio did not start within {wait:.0f} seconds"}


# ---- which browser started this host (2026-11-17#03) ---------------------------------------------------------------
# The extension cannot always say: Vivaldi calls itself Google Chrome in every way an extension can see. But the
# browser starts this host (through the launcher script), so the program a few steps up from this process is it.

BROWSER_PROGRAMS = {"chrome": "Chrome", "msedge": "Edge", "brave": "Brave", "vivaldi": "Vivaldi", "opera": "Opera",
                    "firefox": "Firefox"}


def browser_from(programs: List[str]) -> str:
    """The browser among this process's ancestors (their program paths, nearest first); "" when none is known."""
    for path in programs:
        name = os.path.basename(path.replace("\\", "/")).lower()
        for ending in (".exe", "-bin", "-stable"):
            if name.endswith(ending):
                name = name[:-len(ending)]
        browser = BROWSER_PROGRAMS.get(name)
        if browser == "Opera" and "opera gx" in path.lower():
            return "Opera GX"                                   # the same opera.exe, in its own folder
        if browser:
            return browser
    return ""


def _windows_ancestors(limit: int) -> List[str]:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.Process32FirstW.argtypes = kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                  ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)              # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return []
    parents = {}
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[entry.th32ProcessID] = (entry.th32ParentProcessID, entry.szExeFile)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    found, pid, seen = [], os.getpid(), set()
    while len(found) < limit and pid in parents and pid not in seen:
        seen.add(pid)
        pid = parents[pid][0]
        if pid not in parents:
            break
        path = parents[pid][1]
        handle = kernel.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                buffer, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    path = buffer.value
            finally:
                kernel.CloseHandle(handle)
        found.append(path)
    return found


def _proc_ancestors(limit: int) -> List[str]:
    found, pid = [], os.getpid()
    while len(found) < limit:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            pid = int(f.read().rsplit(")", 1)[1].split()[1])        # the field after the state: the parent
        if pid <= 1:
            break
        found.append(os.readlink(f"/proc/{pid}/exe"))
    return found


def ancestor_programs(limit: int = 6) -> List[str]:
    """The programs this process was started by, nearest first. [] where that cannot be read; never an error."""
    try:
        if sys.platform == "win32":
            return _windows_ancestors(limit)
        if os.path.isdir("/proc"):
            return _proc_ancestors(limit)
    except Exception:
        pass
    return []


def main(stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None, folder: Optional[str] = None,
         browser: Optional[str] = None) -> int:
    if sys.platform == "win32" and stdin is None:
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    try:
        message = read_message(stdin)
    except (ValueError, UnicodeDecodeError) as error:
        write_message(stdout, {"ok": False, "error": f"unreadable message: {error}"})
        return 1
    if message is None:
        return 0
    message = dict(message, via="browser")     # only the extension comes this way; a second Accipio pings directly
    browser = browser_from(ancestor_programs()) if browser is None else browser
    if browser:
        message["browser"] = browser           # which one: Accipio reads that browser's own settings
    reply = relay(message, folder or settings.config_dir())
    if browser and isinstance(reply, dict):
        reply = dict(reply, browser=browser)   # the extension hears which browser Accipio took it for
    write_message(stdout, reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
