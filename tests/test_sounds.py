"""Two different sounds (2026-11-07, 74 A+C, 80A, 82D, 83B): each notice (added, finished, failed) has its own sound,
picked on its own line in Settings with a Play button. Windows plays a Windows sound itself, named in the notice;
a sound file of the user's goes with a silent notice and Accipio plays it, unless Windows is holding notices back
(Do not disturb, a full-screen game). So there is one sound per notice, never Windows' and Accipio's together."""
import json
import os

import pytest

from accipio.core import notices, settings, toasts
from accipio.core.settings import Settings

from tests.test_gui import FakeTray, app, world  # noqa: F401  (pytest fixtures)
from accipio.gui.settings_dialog import SettingsDialog


class FakeToaster:
    """Stands in for Windows' notices: records what would be shown; ``works`` False is Windows refusing."""
    def __init__(self, works=True):
        self.works = works
        self.shown = []

    def show(self, title, text, sound, path=""):
        self.shown.append((title, text, sound, path))
        return self.works


# ---- the settings ------------------------------------------------------------------------------------------

def test_each_notice_has_its_own_sound_by_default():
    s = Settings()
    assert (s.sound_added, s.sound_finished, s.sound_failed) == ("Notification.Default", "Notification.Mail",
                                                                 "Notification.Reminder")
    assert len({s.sound_added, s.sound_finished, s.sound_failed}) == 3
    assert s.sound_for(notices.FINISHED) == "Notification.Mail"


def test_a_sound_is_a_windows_sound_none_or_a_wav_file():
    s = Settings(sound_added=toasts.NO_SOUND, sound_finished=r"C:\Sounds\ding.wav", sound_failed="Notification.Bogus")
    assert s.sound_added == toasts.NO_SOUND and s.sound_finished == r"C:\Sounds\ding.wav"
    assert s.sound_failed == "Notification.Reminder"                       # unknown: back to the default
    assert Settings(sound_added=3).sound_added == "Notification.Default"
    assert Settings(sound_added=r"C:\music\song.mp3").sound_added == "Notification.Default"   # Windows plays .wav only


def test_settings_from_before_the_sounds_get_the_defaults(tmp_path):
    folder = str(tmp_path)
    with open(os.path.join(folder, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"notify_added": False}, f)
    loaded = settings.load_settings(folder)
    assert loaded.notify_added is False and loaded.sound_finished == "Notification.Mail"


# ---- the notice Windows is handed --------------------------------------------------------------------------

def test_a_windows_sound_is_named_in_the_notice():
    xml = toasts.toast_xml("Download finished", "a.zip\nin Comics", "Notification.Mail")
    assert '<audio src="ms-winsoundevent:Notification.Mail"/>' in xml
    assert "<text>Download finished</text><text>a.zip</text><text>in Comics</text>" in xml


def test_no_sound_and_a_sound_file_make_a_silent_notice():
    assert '<audio silent="true"/>' in toasts.toast_xml("t", "x", toasts.NO_SOUND)
    assert '<audio silent="true"/>' in toasts.toast_xml("t", "x", r"C:\Sounds\ding.wav")


def test_names_are_escaped_and_long_lists_kept_to_three_lines():
    xml = toasts.toast_xml("5 downloads added", "a<b>&c.zip\nf2.zip\nf3.zip\nand 2 more", "Notification.Default")
    assert "a&lt;b&gt;&amp;c.zip" in xml
    assert xml.count("<text>") == 3 and "<text>f2.zip\nf3.zip\nand 2 more</text>" in xml


@pytest.mark.skipif(os.name != "nt", reason="Windows' own XML reader")
def test_windows_reads_the_notice():
    winrt_dom = pytest.importorskip("winrt.windows.data.xml.dom")
    doc = winrt_dom.XmlDocument()
    doc.load_xml(toasts.toast_xml("Download failed", "b & c.zip\n404 <Not Found>", "Notification.Reminder"))
    assert doc.get_elements_by_tag_name("audio").length == 1      # nothing shown: only parsed


def test_the_build_carries_the_notices_and_the_uninstaller_forgets_them():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "accipio.spec"), encoding="utf-8") as f:
        assert 'collect_submodules("winrt")' in f.read()          # winrt loads its parts at run time
    with open(os.path.join(root, "installer", "accipio.iss"), encoding="utf-8") as f:
        assert "Software\\Classes\\AppUserModelId\\" + toasts.AUMID in f.read()
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as f:
        assert "winrt-Windows.UI.Notifications" in f.read()
    from accipio.gui import app as gui_app
    with open(gui_app.__file__, encoding="utf-8") as f:
        assert f'app_user_model_id="{toasts.AUMID}"' in f.read()   # the window and its notices are one app


# ---- the window --------------------------------------------------------------------------------------------

def windows_notices(win, works=True, quiet=False):
    win.tray = FakeTray()
    win.toaster = FakeToaster(works)
    played = []
    win.play_sound = played.append
    win.quiet_now = lambda: quiet
    return win.toaster, played


def test_a_notice_names_its_windows_sound_and_accipio_plays_nothing(world):
    win, *_ = world
    toaster, played = windows_notices(win)
    win._show_notice(notices.Notice(notices.FINISHED, "Download finished", "a.zip", "C:\\d\\a.zip"))
    assert toaster.shown == [("Download finished", "a.zip", "Notification.Mail", "C:\\d\\a.zip")]
    assert played == [] and win.tray.messages == []


def test_a_sound_file_plays_once_beside_a_silent_notice(world):
    win, *_ = world
    toaster, played = windows_notices(win)
    win.services.settings.sound_failed = r"C:\Sounds\oops.wav"
    win._show_notice(notices.Notice(notices.FAILED, "Download failed", "b.zip"))
    assert toaster.shown[0][2] == r"C:\Sounds\oops.wav" and played == [r"C:\Sounds\oops.wav"]


def test_a_sound_file_keeps_quiet_while_windows_holds_notices_back(world):
    win, *_ = world
    toaster, played = windows_notices(win, quiet=True)
    win.services.settings.sound_added = r"C:\Sounds\new.wav"
    win._show_notice(notices.Notice(notices.ADDED, "Download added", "c.zip"))
    assert len(toaster.shown) == 1 and played == []


def test_where_windows_refuses_the_notice_the_tray_tells_it(world):
    win, *_ = world
    toaster, played = windows_notices(win, works=False)
    win._show_notice(notices.Notice(notices.ADDED, "Download added", "c.zip"))
    assert win.tray.messages == ["c.zip"] and played == []


def test_clicking_a_windows_notice_shows_the_finished_file(world, monkeypatch, tmp_path):
    win, *_ = world
    shown_file = tmp_path / "a.zip"
    shown_file.write_bytes(b"PK")
    opened = []
    monkeypatch.setattr(win, "_show_in_folder", opened.append)
    win.bridge.notice_clicked.emit(str(shown_file))           # Windows calls back on a thread of its own
    from PyQt6.QtWidgets import QApplication
    QApplication.processEvents()
    assert opened == [str(shown_file)]


# ---- Settings: one line per notice (82D) -------------------------------------------------------------------

def test_each_notice_has_a_line_with_its_sound_and_a_play_button(world):
    win, *_ = world
    dialog = SettingsDialog(win.services.settings, win)
    played = []
    dialog.play_sound = played.append
    for kind, sound in ((notices.ADDED, "Notification.Default"), (notices.FINISHED, "Notification.Mail"),
                        (notices.FAILED, "Notification.Reminder")):
        line = dialog.sound_lines[kind]
        assert line.box.isChecked() and line.sound() == sound
        line.play.click()
    assert played == ["Notification.Default", "Notification.Mail", "Notification.Reminder"]


def test_a_sound_file_is_picked_with_browse_and_saved(world):
    win, *_ = world
    dialog = SettingsDialog(win.services.settings, win)
    line = dialog.sound_lines[notices.FINISHED]
    line.ask_for_file = lambda start: r"C:\Sounds\ding.wav"
    line.combo.setCurrentIndex(line.combo.findData(toasts.PICK_FILE))
    assert line.sound() == r"C:\Sounds\ding.wav" and "ding.wav" in line.combo.currentText()
    dialog.sound_lines[notices.ADDED].combo.setCurrentIndex(
        dialog.sound_lines[notices.ADDED].combo.findData(toasts.NO_SOUND))
    win.apply_settings(dialog.result_settings())
    saved = settings.load_settings(win.services.folder)
    assert saved.sound_finished == r"C:\Sounds\ding.wav" and saved.sound_added == toasts.NO_SOUND


def test_cancelling_browse_keeps_the_sound_there_was(world):
    win, *_ = world
    dialog = SettingsDialog(win.services.settings, win)
    line = dialog.sound_lines[notices.FAILED]
    line.ask_for_file = lambda start: ""
    line.combo.setCurrentIndex(line.combo.findData(toasts.PICK_FILE))
    assert line.sound() == "Notification.Reminder"


def test_a_switched_off_notice_greys_out_its_sound(world):
    win, *_ = world
    dialog = SettingsDialog(win.services.settings, win)
    line = dialog.sound_lines[notices.ADDED]
    line.box.setChecked(False)
    assert not line.combo.isEnabled() and not line.play.isEnabled()
    assert dialog.result_settings().notify_added is False
