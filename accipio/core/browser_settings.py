"""What the browser itself is set to do with a download: where it saves, and whether it asks where to save.

Accipio needs this for the files the browser has to download itself (``Queue.file_saved``): when the browser asks
where to save each file, the user chose the place and Accipio leaves the file there. The Settings window also
shows it, with anything a company policy has fixed, so the user can see why a file ended up where it did.

Read-only, from two places (decided 2026-11-17, the user's pick B):

- each profile's ``Preferences`` file (JSON) under the browser's ``User Data`` folder: ``download.default_directory``
  (absent: the browser's own default, the system's Downloads folder) and ``download.prompt_for_download`` (absent:
  off). The profile names come from ``Local State``. Checked against Chrome 157 and Edge on 2026-11-17. This is not
  a documented interface: anything unreadable or unexpected counts as "not known", never as an error.
- on Windows, the policy keys an administrator sets (``Software\\Policies\\Google\\Chrome`` and
  ``...\\Microsoft\\Edge``, machine before user): ``DownloadDirectory``, ``PromptForDownloadLocation`` and
  ``DownloadRestrictions``. A policy beats the profile's own setting.

An extension is told none of this, which is why the app reads it. Qt-free; the folders and the policy reader are
passed in by the tests, which never touch a real profile or the real registry.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

CHROME, EDGE, OPERA, OPERA_GX, BRAVE, VIVALDI = "Chrome", "Edge", "Opera", "Opera GX", "Brave", "Vivaldi"
FIREFOX = "Firefox"                    # its own files: profiles.ini and each profile's prefs.js (see _firefox_profiles)
# The names are the ones native_host.browser_from gives. All six keep the same files in the same layout (Opera 140,
# Opera GX 139, Brave 1.99 and Vivaldi 8.3 seen 2026-11-17). Policies: only where the vendor documents the key.
POLICY_KEYS = {CHROME: "Software\\Policies\\Google\\Chrome", EDGE: "Software\\Policies\\Microsoft\\Edge",
               BRAVE: "Software\\Policies\\BraveSoftware\\Brave", FIREFOX: "Software\\Policies\\Mozilla\\Firefox"}
POLICY_VALUES = ("DownloadDirectory", "PromptForDownloadLocation", "DownloadRestrictions")
BLOCKS_ALL = 3                         # DownloadRestrictions: 1, 2 and 4 block only what the browser judges dangerous


@dataclass
class BrowserProfile:
    browser: str                       # CHROME or EDGE
    name: str                          # the profile's name as the browser shows it
    folder: str = ""                   # where it saves; "" = the browser's default (the system's Downloads folder)
    asks: bool = False                 # "Ask where to save each file before downloading"
    forced: List[str] = field(default_factory=list)       # what a policy has fixed, in words


def user_data_folders() -> List[Tuple[str, str]]:
    """(browser, its User Data folder) for this system; the folders need not exist."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return [(CHROME, os.path.join(local, "Google", "Chrome", "User Data")),
                (EDGE, os.path.join(local, "Microsoft", "Edge", "User Data")),
                (OPERA, os.path.join(roaming, "Opera Software", "Opera Stable")),
                (OPERA_GX, os.path.join(roaming, "Opera Software", "Opera GX Stable")),
                (BRAVE, os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")),
                (VIVALDI, os.path.join(local, "Vivaldi", "User Data")),
                (FIREFOX, os.path.join(roaming, "Mozilla", "Firefox"))]
    if sys.platform == "darwin":
        support = os.path.join(home, "Library", "Application Support")
        return [(CHROME, os.path.join(support, "Google", "Chrome")), (EDGE, os.path.join(support, "Microsoft Edge")),
                (OPERA, os.path.join(support, "com.operasoftware.Opera")),
                (OPERA_GX, os.path.join(support, "com.operasoftware.OperaGX")),
                (BRAVE, os.path.join(support, "BraveSoftware", "Brave-Browser")),
                (VIVALDI, os.path.join(support, "Vivaldi")), (FIREFOX, os.path.join(support, "Firefox"))]
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return [(CHROME, os.path.join(config, "google-chrome")), (EDGE, os.path.join(config, "microsoft-edge")),
            (OPERA, os.path.join(config, "opera")), (BRAVE, os.path.join(config, "BraveSoftware", "Brave-Browser")),
            (VIVALDI, os.path.join(config, "vivaldi")), (FIREFOX, os.path.join(home, ".mozilla", "firefox"))]


def windows_policies(browser: str) -> Dict[str, Any]:
    """The download policies set for a browser in the registry ({} when none, and off Windows)."""
    if sys.platform != "win32" or browser not in POLICY_KEYS:
        return {}
    import winreg
    found: Dict[str, Any] = {}
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):        # the machine's policy wins
        try:
            with winreg.OpenKey(hive, POLICY_KEYS[browser]) as key:
                for name in POLICY_VALUES:
                    if name in found:
                        continue
                    try:
                        found[name] = winreg.QueryValueEx(key, name)[0]
                    except OSError:
                        pass
        except OSError:
            continue
    return found


def _json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _section(data: Dict[str, Any], *names: str) -> Dict[str, Any]:
    for name in names:
        data = data.get(name) if isinstance(data, dict) else None
    return data if isinstance(data, dict) else {}


def _with_policies(profile: BrowserProfile, policies: Dict[str, Any]) -> BrowserProfile:
    folder = policies.get("DownloadDirectory")
    if isinstance(folder, str) and folder:
        profile.folder = folder
        profile.forced.append("the download folder")
    asks = policies.get("PromptForDownloadLocation")
    if isinstance(asks, int):
        profile.asks = bool(asks)
        profile.forced.append("that it asks where to save each file" if asks else "that it never asks where to save")
    limit = policies.get("DownloadRestrictions")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit:
        profile.forced.append("that all downloads are blocked" if limit == BLOCKS_ALL
                              else "that downloads it judges dangerous are blocked")
    return profile


_FIREFOX_PREF = re.compile(r'^user_pref\("(browser\.download\.[A-Za-z_.]+)",\s*(.+)\);\s*$')


def _firefox_prefs(path: str) -> Optional[Dict[str, Any]]:
    """The download settings in a Firefox profile's prefs.js (lines of ``user_pref("name", value);``, written only
    for what differs from the default). None when the profile has no such file: it was never used."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    found: Dict[str, Any] = {}
    for line in lines:
        match = _FIREFOX_PREF.match(line.strip())
        if match:
            try:
                found[match.group(1)] = json.loads(match.group(2))
            except ValueError:
                pass
    return found


def _firefox_profiles(root: str) -> List[BrowserProfile]:
    """Firefox's profiles (``profiles.ini``) with what each one's prefs.js says: it asks where to save when
    ``browser.download.useDownloadDir`` is false, and saves to ``browser.download.dir`` when ``folderList`` is 2 (a
    folder of the user's own; otherwise the system's Downloads folder, or the desktop). Seen in Firefox 160."""
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    try:
        parser.read(os.path.join(root, "profiles.ini"), encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return []
    found = []
    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        path = parser.get(section, "Path", fallback="")
        if not path:
            continue
        folder = os.path.join(root, path) if parser.get(section, "IsRelative", fallback="1") == "1" else path
        prefs = _firefox_prefs(os.path.join(folder, "prefs.js"))
        if prefs is None:
            continue
        saves_to = prefs.get("browser.download.dir") if prefs.get("browser.download.folderList") == 2 else ""
        found.append(BrowserProfile(FIREFOX, parser.get(section, "Name", fallback=section),
                                    saves_to if isinstance(saves_to, str) else "",
                                    prefs.get("browser.download.useDownloadDir") is False))
    return found


def read(folders: Optional[List[Tuple[str, str]]] = None,
         policies: Callable[[str], Dict[str, Any]] = windows_policies) -> List[BrowserProfile]:
    """Every profile of every browser found, with its download settings. [] when no browser is installed."""
    found = []
    for browser, root in user_data_folders() if folders is None else folders:
        if browser == FIREFOX:
            profiles = _firefox_profiles(root)
            if profiles:
                try:
                    fixed = policies(browser)
                except OSError:
                    fixed = {}
                found += [_with_policies(profile, fixed) for profile in profiles]
            continue
        listed = _section(_json(os.path.join(root, "Local State")), "profile", "info_cache")
        fixed = None
        for folder_name in sorted(listed):
            path = os.path.join(root, folder_name, "Preferences")
            if not os.path.isfile(path):
                continue
            download = _section(_json(path), "download")
            shown = listed[folder_name].get("name") if isinstance(listed[folder_name], dict) else None
            saves_to = download.get("default_directory")
            profile = BrowserProfile(browser, shown if isinstance(shown, str) and shown else folder_name,
                                     saves_to if isinstance(saves_to, str) else "",
                                     download.get("prompt_for_download") is True)
            if fixed is None:
                try:
                    fixed = policies(browser)
                except OSError:
                    fixed = {}
            found.append(_with_policies(profile, fixed))
    return found


def browser_of(user_agent: str) -> str:
    """Which browser sent a message, guessed from its user agent (Edge says "Edg/", Opera "OPR/"; "" = not known).
    Only the fallback for a message without the native host's own answer (``browser``, from the program that started
    it): Brave and Vivaldi call themselves Chrome here, and Opera GX says the same as Opera."""
    if not user_agent:
        return ""
    return EDGE if "Edg/" in user_agent else OPERA if "OPR/" in user_agent else CHROME


def asks(profiles: List[BrowserProfile], browser: str = "") -> Optional[bool]:
    """Whether the browser that saved a file (its name as in this module; "" = not known which) asks where to save
    each one. The extension cannot say which profile it runs in, so this is only known when every profile of that
    browser agrees; None = not known."""
    answers = {p.asks for p in profiles if not browser or p.browser == browser}
    return answers.pop() if len(answers) == 1 else None


def describe(profile: BrowserProfile) -> str:
    """One line for the Settings window."""
    where = f"saves to {profile.folder}" if profile.folder else "saves to its usual Downloads folder"
    how = "asks where to save each file" if profile.asks else "does not ask where to save"
    line = f"{profile.browser} ({profile.name}): {where} and {how}."
    if profile.forced:
        line += " Fixed by your organisation: " + "; ".join(profile.forced) + "."
    return line
