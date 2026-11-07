"""Windows' notices, each with the sound picked for it (2026-11-07: 74 A+C, 80A, 82D, 83B).

Qt's tray messages always let Windows play its one notification sound, so "added" and "finished" sounded the same
and a sound of Accipio's own would have come on top of it. Accipio therefore hands its notices to Windows itself
(the ``winrt`` packages, about 1.2 MB): a Windows sound is named in the notice and Windows plays it, keeping to Do not
disturb and to the per-app switches under Settings > System > Notifications. A sound file of the user's (.wav, the
only kind Windows plays without a player) goes with a silent notice and Accipio plays it, unless Windows is holding
notices back. Nothing here is required: where the notice cannot be shown (not Windows, the packages missing,
Windows refusing) ``Toaster.show`` says so and the window falls back to the tray's message.

Windows shows a notice only for a known app: ``register`` names Accipio under
HKCU\\Software\\Classes\\AppUserModelId\\Erlbon.Accipio (the same id the window has, see gui/app.py), which works for
the installed copy, the zip and a source run alike. The uninstaller removes the key.
"""
from __future__ import annotations

import collections
import os
import sys
from typing import Callable, Dict
from xml.sax.saxutils import escape

AUMID = "Erlbon.Accipio"
REGISTRY_KEY = "Software\\Classes\\AppUserModelId\\" + AUMID
NO_SOUND = "none"
PICK_FILE = "file"                 # Settings' "A sound file…" item; never stored
# The sounds Windows' notices can name (ms-winsoundevent:...), with Settings' words for them. The others Windows
# knows (alarms and calls) repeat until dismissed: not for a download.
WINDOWS_SOUNDS: Dict[str, str] = {
    "Notification.Default": "Windows' notification",
    "Notification.IM": "Instant message",
    "Notification.Mail": "New mail",
    "Notification.Reminder": "Reminder",
}                                  # Notification.SMS is left out: Windows gives it the instant message's sound
TEXT_LINES = 2                     # under the title; Windows shows at most three texts
KEEP = 20                          # notices kept alive for their click; Windows forgets the oldest anyway


def is_file(sound: str) -> bool:
    return sound != NO_SOUND and sound not in WINDOWS_SOUNDS


def valid(sound) -> bool:
    return isinstance(sound, str) and (sound == NO_SOUND or sound in WINDOWS_SOUNDS or sound.lower().endswith(".wav"))


def toast_xml(title: str, text: str, sound: str) -> str:
    """The notice as Windows reads it: the title, the text in at most two lines, and its sound or silence."""
    lines = text.split("\n")
    lines = lines[:TEXT_LINES - 1] + ["\n".join(lines[TEXT_LINES - 1:])] if len(lines) > TEXT_LINES else lines
    texts = "".join(f"<text>{escape(line)}</text>" for line in [title] + [line for line in lines if line])
    audio = (f'<audio src="ms-winsoundevent:{sound}"/>' if sound in WINDOWS_SOUNDS else '<audio silent="true"/>')
    return f'<toast><visual><binding template="ToastGeneric">{texts}</binding></visual>{audio}</toast>'


def register(icon: str = "") -> None:
    """Name Accipio to Windows, with its icon. Written before every notice: the release gate's scratch install
    removes the key when it is uninstalled, and the icon's path is the copy's own."""
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Accipio")
        if icon and os.path.isfile(icon):
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)


class Toaster:
    """Shows notices through Windows. ``clicked(path)`` is called, on a thread of Windows', when one is clicked
    while Accipio runs; ``path`` is the one finished file, or ""."""

    def __init__(self, icon: str = "", clicked: Callable[[str], None] = lambda path: None):
        self.icon = icon
        self.clicked = clicked
        self._shown: collections.deque = collections.deque(maxlen=KEEP)     # a notice let go cannot be clicked

    def show(self, title: str, text: str, sound: str, path: str = "") -> bool:
        if sys.platform != "win32":
            return False
        try:
            from winrt.windows.data.xml.dom import XmlDocument
            from winrt.windows.ui.notifications import ToastNotification, ToastNotificationManager
            register(self.icon)
            doc = XmlDocument()
            doc.load_xml(toast_xml(title, text, sound))
            toast = ToastNotification(doc)
            toast.add_activated(lambda *_: self.clicked(path))
            ToastNotificationManager.create_toast_notifier(AUMID).show(toast)
        except Exception:             # ImportError, OSError from Windows: the tray tells it instead
            return False
        self._shown.append(toast)
        return True


def play(sound: str) -> None:
    """Play a sound now, without waiting: Settings' Play button, and a sound file beside its silent notice. A Windows
    sound is played by its name, as the notice would."""
    if sys.platform != "win32" or sound == NO_SOUND:
        return
    import winsound
    flags = winsound.SND_ASYNC | winsound.SND_NODEFAULT
    try:
        if sound in WINDOWS_SOUNDS:
            winsound.PlaySound(sound, flags | winsound.SND_ALIAS)
        else:
            winsound.PlaySound(sound, flags | winsound.SND_FILENAME)
    except RuntimeError:              # the file is gone or not a .wav Windows can play
        pass


QUNS_ACCEPTS_NOTIFICATIONS = 5


def quiet_now() -> bool:
    """True while Windows holds notices back (Do not disturb, a full-screen game or presentation), so a sound file
    keeps quiet as Windows' own sounds do."""
    if sys.platform != "win32":
        return False
    import ctypes
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (AttributeError, OSError):
        return False
    return state.value != QUNS_ACCEPTS_NOTIFICATIONS
