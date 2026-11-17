"""Torrents are passed on, not downloaded: a magnet link or a finished .torrent file goes to a torrent program.

Accipio is not a torrent client (the user's first step, 2026-11-17: "hand links from accipio to qbittorrent"). What
it adds is the routing: the rules choose the folder, and the torrent program is told to save there.

Three choices (``Settings.torrents``):

- ``off`` (the default): as before. A magnet link is not a link Accipio takes; a .torrent file is a file like any other.
- ``system``: opened with whatever program the system has registered for it. Works with every client; that client
  decides where the files go.
- ``qbittorrent``: qBittorrent is started with the link or the file and ``--save-path=<folder the rules chose>``.
  Only the options that exist under the same name in qBittorrent 4.6 and 5 are used (checked against its
  ``src/app/cmdoptions.cpp``, 2026-11-17: ``--save-path=<path>``; ``--add-paused`` became ``--add-stopped`` and is
  left alone). qBittorrent's own "Add New Torrent" box still opens when the user has it on, with the folder filled in.

Qt-free. Nothing here goes through a shell: the link is one argument of its own.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from typing import Callable, List, Optional

OFF, SYSTEM, QBITTORRENT = "off", "system", "qbittorrent"
MODES = (OFF, SYSTEM, QBITTORRENT)
MODE_NAMES = {OFF: "nothing", SYSTEM: "your torrent program", QBITTORRENT: "qBittorrent"}

# a link that names a torrent by its hash (v1: 40 hex or 32 base32 characters; v2: a multihash)
MAGNET = re.compile(r"magnet:\?\S*xt=urn:bt(?:ih|mh):[A-Za-z0-9]{32,}\S*", re.IGNORECASE)
TORRENT_EXTENSION = "torrent"


class TorrentError(RuntimeError):
    """The torrent could not be passed on; the message is fit to show."""


def is_magnet(url: str) -> bool:
    return bool(MAGNET.fullmatch((url or "").strip()))


def is_torrent_file(name: str) -> bool:
    return (name or "").lower().endswith("." + TORRENT_EXTENSION)


def magnet_name(url: str) -> str:
    """What the link calls its torrent (its ``dn``), else "Magnet link <start of its hash>"."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    name = " ".join((query.get("dn") or [""])[0].split())
    if name:
        return name
    found = re.search(r"urn:bt(?:ih|mh):([A-Za-z0-9]+)", url, re.IGNORECASE)
    return f"Magnet link {found.group(1)[:8].lower()}" if found else "Magnet link"


def _app_path() -> str:
    """Where Windows says qBittorrent is installed (its "App Paths" entry), or ""."""
    if sys.platform != "win32":
        return ""
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, "Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\qbittorrent.exe") as key:
                return str(winreg.QueryValue(key, None) or "").strip('"')
        except OSError:
            continue
    return ""


def find_qbittorrent(chosen: str = "", app_path: Callable[[], str] = _app_path,
                     which: Callable[[str], Optional[str]] = shutil.which) -> str:
    """The qBittorrent program: the one picked in Settings, else the installed one, else one on PATH. "" = none."""
    places: List[str] = [chosen, app_path()]
    if sys.platform == "win32":
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if root:
                places.append(os.path.join(root, "qBittorrent", "qbittorrent.exe"))
    places.append(which("qbittorrent") or "")
    return next((os.path.normpath(p) for p in places if p and os.path.isfile(p)), "")


def _open_with_system(source: str) -> None:
    if sys.platform == "win32":
        os.startfile(source)                               # the program registered for magnet: or .torrent
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", source],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _start(command: List[str]) -> None:
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


def hand_over(source: str, mode: str, *, folder: str = "", qbittorrent: str = "",
              start: Callable[[List[str]], None] = _start,
              open_with_system: Callable[[str], None] = _open_with_system) -> str:
    """Pass a magnet link or a .torrent file's path on. Returns what was done, in words for "why here?".
    Raises TorrentError when there is nothing to pass it to."""
    if not (is_magnet(source) or (is_torrent_file(source) and os.path.isfile(source))):
        raise TorrentError("this is not a magnet link or a .torrent file")
    if mode == QBITTORRENT:
        program = find_qbittorrent(qbittorrent)
        if not program:
            raise TorrentError("qBittorrent was not found. Pick it under Settings > Torrents, or choose another way.")
        command = [program]
        if folder:
            command.append(f"--save-path={folder}")
        command.append(source)
        try:
            start(command)
        except OSError as error:
            raise TorrentError(f"qBittorrent could not be started: {error}") from None
        return f"Passed on to qBittorrent, which saves its files in {folder}." if folder \
            else "Passed on to qBittorrent."
    if mode == SYSTEM:
        try:
            open_with_system(source)
        except OSError as error:
            raise TorrentError("no program on this PC opens torrents (install a torrent program, or pick "
                               f"qBittorrent under Settings > Torrents): {error}") from None
        return "Passed on to your torrent program, which decides where its files go."
    raise TorrentError("passing torrents on is switched off (Settings > Torrents)")
