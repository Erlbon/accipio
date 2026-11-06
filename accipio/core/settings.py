"""Where Accipio keeps its files, and the settings the user can change.

Everything lives in one folder: ``%APPDATA%\\Accipio`` on Windows, ``$XDG_CONFIG_HOME/accipio`` (or
``~/.config/accipio``) elsewhere. In it: ``settings.json``, ``rules.json``, the queue database and aria2's
saved session. Files are written to a temporary name and swapped in, so a crash never leaves half a file.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from typing import Dict, List, Optional, Tuple

from accipio.core import rules


def config_dir() -> str:
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "Accipio")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "accipio")


def default_download_folder() -> str:
    return os.path.join(os.path.expanduser("~"), "Downloads")


@dataclass
class Settings:
    base_folder: str = ""
    max_downloads: int = 3
    connections: int = 8                  # per file
    speed_limit_kib: int = 0              # total, KiB/s; 0 = no limit
    keep_history: bool = False            # finished downloads are forgotten at the next start unless on
    video_quality: str = "best"           # best, 1080, 720 or audio (see video.QUALITIES)
    notify_added: bool = True             # Windows notifications (see notices.py)
    notify_finished: bool = True
    notify_failed: bool = True
    browser_connected: bool = False       # the extension has talked to Accipio once; until then a start shows how
    unpack_archives: bool = False         # the archive trial (2026-11-05): look inside zips and tars, ask what to do
    after_unpack: str = "keep"            # or "delete" (the archive goes to the Recycle Bin)
    ffmpeg_path: str = ""                 # picked with Browse… under Settings; empty = find it (video.find_ffmpeg)

    def notice_kinds(self) -> List[str]:
        return [kind for kind, on in (("added", self.notify_added), ("finished", self.notify_finished),
                                      ("failed", self.notify_failed)) if on]

    def __post_init__(self):
        if not self.base_folder:
            self.base_folder = default_download_folder()


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


