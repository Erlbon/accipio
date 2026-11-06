"""Help > Check for Updates, and the optional daily check (picked 2026-11-04: 21A).

The family's UpdateController asks GitHub, shows "Update Available" with the release notes and downloads with a
checksum check. What differs is the install: the Redactors swap one exe in place, but Accipio is a folder
installed by its installer. So the download is the new installer (every release carries it under the fixed name
``accipio-setup.exe``), and "Update Now" runs it quietly with ``/relaunch=yes``: it closes Accipio (and aria2,
which saves its session, so downloads carry on), updates the folder and starts Accipio again. A copy that was not
installed with the installer (the zip, a checkout) gets "Open Download Page" instead.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox

from redactor_common.core import selfupdate
from redactor_common.core.selfupdate import Release, UpdateError
from redactor_common.gui.progress import run_work_with_progress
from redactor_common.gui.update_controller import OPEN_PAGE, SKIP, UPDATE, UpdateController, UpdateDialog

from accipio.core.version import APP_NAME, APP_REPO_URL, APP_VERSION

SETUP_SLUG = "accipio-setup"              # the release asset accipio-setup.exe (selfupdate.asset_name adds .exe)
INSTALLER_APP_ID = "{6C0E5D8A-3F7B-4B8E-9A51-2D4C7E1A9B30}"     # installer\accipio.iss AppId; a test keeps them equal
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{INSTALLER_APP_ID}_is1"
QUIET = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=yes"]


def installed_dir() -> str:
    """The folder the installer put Accipio in (for this user), or "" when it never ran here."""
    if sys.platform != "win32":
        return ""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return str(winreg.QueryValueEx(key, "InstallLocation")[0])
    except OSError:
        return ""


def _launch_detached(command: List[str]) -> None:
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    subprocess.Popen(command, creationflags=flags, close_fds=True, cwd=os.path.dirname(command[0]))


class AccipioUpdates(UpdateController):
    def __init__(self, window, settings_path, *, fetch=None, frozen: Optional[bool] = None, exe=None,
                 installed_dir: Callable[[], str] = installed_dir,
                 launch: Optional[Callable[[List[str]], None]] = None):
        # installed=False: Accipio runs its own installer flow (can_update, offer, update_to below). Left to itself,
        # redactor_common (since 2026-11-06-01) sees unins000.exe, appends "-setup" to the slug and looks for
        # "accipio-setup-setup.exe", finds nothing to verify, and an installed Accipio only got the download page.
        super().__init__(window, APP_NAME, APP_VERSION, APP_REPO_URL, settings_path, fetch=fetch, frozen=frozen,
                         exe=exe, installed=False)
        self.slug = SETUP_SLUG
        self._installed_dir = installed_dir
        self._launch = launch or _launch_detached

    def can_update(self) -> Tuple[bool, str]:
        """(True, "") for a copy the installer put here; else why not, for the dialog."""
        if not self._frozen:
            return False, "This copy runs from source; update it with git pull."
        where = self._installed_dir()
        if not where or os.path.normcase(os.path.normpath(where)) != os.path.normcase(os.path.normpath(str(self._exe.parent))):
            return False, ("This copy was not installed with the installer (the zip, say), so it cannot update "
                           "itself; get the new version from the download page.")
        return True, ""

    def offer(self, release: Release, automatic: bool = False) -> str:
        can, reason = self.can_update()
        if can and not release.downloadable:
            can, reason = False, "This release has no verifiable installer; use the download page."
        dialog = UpdateDialog(self.app_name, self.app_version, release, can, reason, self.window, automatic)
        dialog.exec()
        choice = dialog.choice
        if choice == SKIP:
            self.settings.update(skipped=release.tag)
        elif choice == OPEN_PAGE and release.page_url:
            QDesktopServices.openUrl(QUrl(release.page_url))
        elif choice == UPDATE:
            self.update_to(release)
        return choice

    def update_to(self, release: Release) -> bool:
        """Download the new installer (checked against GitHub's checksum), start it, and close Accipio for it."""
        target = Path(tempfile.gettempdir()) / "Accipio update" / f"{SETUP_SLUG}.exe"
        target.parent.mkdir(parents=True, exist_ok=True)

        def work(progress, cancel):
            return selfupdate.download(release, target, progress, cancel)

        try:
            got = run_work_with_progress(self.window, f"Downloading {self.app_name} {release.display}...", work,
                                         release.asset_size, title="Update")
        except UpdateError as exc:
            QMessageBox.warning(self.window, "Update", str(exc))
            return False
        if got is None:
            return False                     # cancelled; the partial file is already gone
        try:
            self._launch([str(got)] + QUIET)
        except OSError as exc:
            QMessageBox.warning(self.window, "Update", f"The installer could not be started: {exc}")
            return False
        self._quit()
        return True

    def _quit(self) -> None:
        """Close the window the normal way (aria2 saves its session) so the installer finds Accipio gone."""
        if self.window.close():
            QApplication.instance().quit()
