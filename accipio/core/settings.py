"""Where Accipio keeps its files, and the settings the user can change.

Everything lives in one folder: ``%APPDATA%\\Accipio`` on Windows, ``$XDG_CONFIG_HOME/accipio`` (or
``~/.config/accipio``) elsewhere. In it: ``settings.json``, ``rules.json``, the queue database and aria2's
saved session. Files are written to a temporary name and swapped in, so a crash never leaves half a file.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from typing import List

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


def save_rules(folder: str, rule_list: List[rules.Rule]) -> None:
    write_atomically(os.path.join(folder, "rules.json"), rules.rules_to_json(rule_list))
