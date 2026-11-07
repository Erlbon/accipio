"""Where Accipio keeps its files, and the settings the user can change.

Everything lives in one folder: ``%APPDATA%\\Accipio`` on Windows, ``$XDG_CONFIG_HOME/accipio`` (or
``~/.config/accipio``) elsewhere, or REDACTOR_CONFIG_DIR when that is set (the release gate's scratch profile). In it: ``settings.json``, ``rules.json``, the queue database and aria2's
saved session. Files are written to a temporary name and swapped in, so a crash never leaves half a file.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional, Tuple

from redactor_common.core import app_paths

from accipio.core import rules, toasts


def config_dir() -> str:
    override = app_paths.config_override()          # REDACTOR_CONFIG_DIR: the family's scratch profile (release gate)
    if override is not None:
        return str(override)
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Accipio")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "accipio")


# When finished downloads leave the list (only the list: the files stay). Edit > Clear Finished works with each.
CLEAR_NEVER = "never"           # until cleared by hand (was "keep history" on)
CLEAR_AT_START = "start"        # at the next start (the default; was "keep history" off)
CLEAR_AFTER = "after"           # clear_after_minutes after they finished
CLEAR_AT_ONCE = "done"          # as soon as they finish
CLEAR_CHOICES = (CLEAR_NEVER, CLEAR_AT_START, CLEAR_AFTER, CLEAR_AT_ONCE)


def default_download_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads")


@dataclass
class Settings:
    base_folder: str = ""
    max_downloads: int = 3
    connections: int = 8                  # per file
    speed_limit_kib: int = 0              # total, KiB/s; 0 = no limit
    clear_finished: str = "start"         # when finished downloads leave the list: CLEAR_CHOICES (71A, 75A)
    clear_after_minutes: int = 15         # for CLEAR_AFTER
    video_quality: str = "best"           # best, 1080, 720 or audio (see video.QUALITIES)
    notify_added: bool = True             # Windows notifications (see notices.py)
    notify_finished: bool = True
    notify_failed: bool = True
    sound_added: str = "Notification.Default"      # each notice's sound (74, 83B): a toasts.WINDOWS_SOUNDS name,
    sound_finished: str = "Notification.Mail"      # toasts.NO_SOUND or a .wav file's path
    sound_failed: str = "Notification.Reminder"
    browser_connected: bool = False       # the extension has talked to Accipio once; until then a start shows how
    unpack_archives: bool = False         # the archive trial (2026-11-05): look inside zips and tars, ask what to do
    after_unpack: str = "keep"            # or "delete" (the archive goes to the Recycle Bin)
    ffmpeg_path: str = ""                 # picked with Browse… under Settings; empty = find it (video.find_ffmpeg)
    ask_playlists: bool = True            # a video link with a list: offer the rest (a list link itself always asks)
    debug_log: bool = False               # write debug.log (core/debug_log.py, 72B); off unless switched on

    def notice_kinds(self) -> List[str]:
        return [kind for kind, on in (("added", self.notify_added), ("finished", self.notify_finished),
                                      ("failed", self.notify_failed)) if on]

    def sound_for(self, kind: str) -> str:
        return getattr(self, f"sound_{kind}")

    def __post_init__(self):
        for kind in ("added", "finished", "failed"):
            if not toasts.valid(self.sound_for(kind)):
                setattr(self, f"sound_{kind}", next(f.default for f in fields(Settings) if f.name == f"sound_{kind}"))
        if not self.base_folder:
            self.base_folder = default_download_folder()
        if self.clear_finished not in CLEAR_CHOICES:
            self.clear_finished = CLEAR_AT_START
        if not isinstance(self.clear_after_minutes, int) or isinstance(self.clear_after_minutes, bool) \
                or not 1 <= self.clear_after_minutes <= 24 * 60:
            self.clear_after_minutes = 15


def write_atomically(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(temporary, path)


def load_settings(folder: str) -> Settings:
    path = os.path.join(folder, "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    if "keep_history" in data and "clear_finished" not in data:     # before 2026-11-07: one switch
        data["clear_finished"] = CLEAR_NEVER if data["keep_history"] is True else CLEAR_AT_START
    names = {f.name for f in fields(Settings)}
    return Settings(**{k: v for k, v in data.items() if k in names})


def save_settings(folder: str, settings: Settings) -> None:
    write_atomically(os.path.join(folder, "settings.json"), json.dumps(asdict(settings), indent=2, ensure_ascii=False))


def load_rules(folder: str) -> List[rules.Rule]:
    """The saved rules, or the default set when there are none yet. A broken file is kept aside, not lost."""
    path = os.path.join(folder, "rules.json")
    try:
        with open(path, encoding="utf-8") as f:
            return rules.rules_from_json(f.read())
    except FileNotFoundError:
        return rules.default_rules()
    except (OSError, ValueError, TypeError):
        os.replace(path, path + ".broken")
        return rules.default_rules()


def load_library(folder: str) -> rules.Library:
    """The folders, file groups and site groups saved beside the rules, or the built-in ones (an older
    rules.json has none)."""
    try:
        with open(os.path.join(folder, "rules.json"), encoding="utf-8") as f:
            saved = rules.library_from_json(f.read())
    except (OSError, ValueError, TypeError):
        saved = None
    return rules.default_library() if saved is None else saved


def save_rules(folder: str, rule_list: List[rules.Rule], library: Optional[rules.Library] = None) -> None:
    """Rules, folders and groups share rules.json. Without ``library`` the one already there is kept."""
    library = load_library(folder) if library is None else library
    write_atomically(os.path.join(folder, "rules.json"), rules.rules_to_json(rule_list, library))


def export_rules(path: str, rule_list: List[rules.Rule], library: rules.Library) -> None:
    """A copy of the rules, folders and groups for another PC or another person; the same format as rules.json."""
    write_atomically(path, rules.rules_to_json(rule_list, library))


def import_rules(path: str) -> Tuple[List[rules.Rule], Optional[rules.Library]]:
    """The rules and the library in an exported file (None when it has none). Raises ValueError, in words,
    for a file that is not one."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        data = json.loads(text)
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"The file cannot be read: {error}") from error
    except ValueError as error:
        raise ValueError("This is not an Accipio rules file (it is not JSON).") from error
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("This is not an Accipio rules file (it has no rules).")
    try:
        return rules.rules_from_json(text), rules.library_from_json(text)
    except (TypeError, ValueError) as error:
        raise ValueError(f"The rules in this file cannot be read: {error}") from error


# (attribute of Library, Rule reference kind for rename_references)
_LIBRARY_PARTS = (("folders", "folder"), ("groups", "type"), ("site_groups", "sites"))


def merge_imported(mine: Tuple[List[rules.Rule], rules.Library],
                   theirs: Tuple[List[rules.Rule], Optional[rules.Library]]) -> Tuple[List[rules.Rule], rules.Library]:
    """"Add to mine": their rules go below mine. A folder or group of theirs whose name I already use, with
    different content, is added as "<name> (imported)" and their rules are pointed at that name, so nothing of
    mine changes; an identical one is not doubled."""
    my_rules, my_library = mine
    their_rules, their_library = theirs
    merged = dataclasses.replace(my_library, folders=list(my_library.folders), groups=list(my_library.groups),
                                 site_groups=list(my_library.site_groups))
    for attribute, kind in _LIBRARY_PARTS:
        target_list = getattr(merged, attribute)
        for item in getattr(their_library, attribute) if their_library else []:
            same_name = rules.find_named(target_list, item.name)
            if same_name == item:
                continue
            if same_name is None:
                target_list.append(item)
                continue
            new = rules.unique_name(f"{item.name} (imported)", [x.name for x in target_list])
            rules.rename_references(their_rules, kind, item.name, new)
            target_list.append(dataclasses.replace(item, name=new))
    return my_rules + their_rules, merged


