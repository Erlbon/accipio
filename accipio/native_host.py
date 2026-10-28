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
    """Pass one message to Accipio, starting it first when needed (not for a mere ping)."""
    try:
        return ipc.send(folder, message)
    except OSError:
        if message.get("type") == "ping":
            return {"ok": False, "error": "Accipio is not running"}
    launch()
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        sleep(0.25)
        try:
            return ipc.send(folder, message)
        except OSError:
            continue
    return {"ok": False, "error": f"Accipio did not start within {wait:.0f} seconds"}


def main(stdin: Optional[BinaryIO] = None, stdout: Optional[BinaryIO] = None, folder: Optional[str] = None) -> int:
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
    write_message(stdout, relay(message, folder or settings.config_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
