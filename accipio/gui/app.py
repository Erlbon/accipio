"""Start Accipio: ``python -m accipio`` (or the exe).

``--native-host`` runs the browser's native messaging host instead of the window (the frozen exe is both).
``--register`` only points Chrome and Edge at this Accipio and quits (the installer runs it), and ``--minimized``
starts with the window minimized (the "Start with Windows" entry). A second start finds the running Accipio over the local pipe, brings it to the front and quits, so only one
Accipio ever runs. At every start the native host is registered for Chrome and Edge (only for this user).
``--quit`` asks the running Accipio to stop the way File > Exit does and waits until its process is gone: the
installer and uninstaller run it, because the window's X (and so a plain close request) only hides Accipio.
"""
from __future__ import annotations

import os
import sys
import time

from accipio.core import aria2, browser, ipc, settings, ytdlp_update
from accipio.core.version import APP_NAME

QUIT_WAIT = 30.0                               # seconds --quit waits for Accipio to finish stopping


def start_minimized(argv) -> bool:
    return "--minimized" in argv


def wait_for_exit(pid: int, timeout: float) -> bool:
    """True once process ``pid`` has ended (or never existed), False when it still runs after ``timeout``."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
        if not handle:
            return True
        try:
            return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), int(timeout * 1000)) == 0
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


def quit_running(folder: str, wait: float = QUIT_WAIT) -> int:
    """``--quit``: 0 when no Accipio runs any more, 1 when the running one did not stop in time."""
    try:
        reply = ipc.send(folder, {"type": "quit"})
    except OSError:
        return 0                               # none runs
    if not reply.get("ok"):
        return 1
    return 0 if wait_for_exit(int(reply.get("pid") or 0), wait) else 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--native-host" in argv:
        from accipio import native_host
        return native_host.main()

    folder = settings.config_dir()
    if "--quit" in argv:
        return quit_running(folder)
    if "--register" in argv:
        browser.register(folder)
        return 0
    if ipc.is_running(folder):
        ipc.send(folder, {"type": "show"})
        return 0
    ytdlp_update.activate(folder)              # before anything imports yt-dlp: an update made from Tools wins
    try:
        browser.register(folder)
    except OSError:
        pass                                   # the browser link is a convenience; never stop the app for it

    from redactor_common.gui.app_bootstrap import run_app

    def make_window():
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication, QMessageBox

        from accipio.gui.main_window import MainWindow, open_services
        from accipio.gui.style import apply_style
        apply_style(QApplication.instance())             # after run_app's theme: rounds the family look
        try:
            services = open_services(folder)
        except aria2.Aria2Error as error:
            QMessageBox.critical(None, APP_NAME, f"Accipio cannot start its download engine.\n\n{error}")
            raise SystemExit(1)
        window = MainWindow(services)
        if start_minimized(argv):
            window.setWindowState(Qt.WindowState.WindowMinimized)    # run_app's show() keeps the state
        return window

    from accipio.gui import appicon
    return run_app(APP_NAME, make_window, crash_log_path=os.path.join(folder, "crash.log"),
                   app_user_model_id="Erlbon.Accipio", icon_path=appicon.ICO, argv=argv)


if __name__ == "__main__":
    sys.exit(main())
