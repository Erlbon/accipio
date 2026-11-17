"""Settings: the base folder, how many downloads at once, connections per file, the speed limit, history,
and which notifications to show."""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton, QRadioButton,
                             QSpinBox, QVBoxLayout, QWidget)
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background

from accipio.core import browser_settings, debug_log, toasts, torrents, video
from accipio.core.notices import ADDED, FAILED, FINISHED, KINDS
from accipio.core.settings import CLEAR_AFTER, CLEAR_AT_ONCE, CLEAR_AT_START, CLEAR_CHOICES, CLEAR_NEVER, Settings
from accipio.core.video import QUALITIES
from accipio.gui.style import WARNING_COLOR


def install_ffmpeg_with_progress(parent) -> Optional[str]:
    """Settings > Install ffmpeg: winget under a busy box whose Cancel stops it. Returns what went wrong, or None."""
    box = QProgressDialog("Installing ffmpeg with winget (this can take a minute)…", "Cancel", 0, 0, parent)
    box.setWindowTitle("Install ffmpeg")
    box.setWindowModality(Qt.WindowModality.ApplicationModal)
    box.setMinimumDuration(0)
    cancel = threading.Event()
    box.canceled.connect(cancel.set)
    box.show()
    try:
        call_in_background(video.install_ffmpeg, cancel, cancel_signal=box.canceled)
    except (BackgroundCancelled, video.FfmpegInstallCancelled):
        return None
    except video.FfmpegInstallError as error:
        return str(error)
    finally:
        box.close()
    return None


class SoundLine(QHBoxLayout):
    """One notice's line (82D): [x] when it is shown, its sound, and Play. The sounds are Windows' notification
    sounds, No sound, and a .wav file of the user's picked with "A sound file…" (core/toasts.py)."""

    def __init__(self, text: str, on: bool, sound: str, play: Callable[[str], None], parent):
        super().__init__()
        self.parent_widget = parent
        self.box = QCheckBox(text)
        self.box.setChecked(on)
        self.combo = QComboBox()
        self.combo.addItem("No sound", toasts.NO_SOUND)
        for name, label in toasts.WINDOWS_SOUNDS.items():
            self.combo.addItem(label, name)
        self.combo.addItem("A sound file…", toasts.PICK_FILE)
        self._chosen = sound
        if toasts.is_file(sound):
            self._add_file(sound)
        self.combo.setCurrentIndex(max(self.combo.findData(sound), 0))
        self.combo.currentIndexChanged.connect(self._changed)
        self.play = QPushButton("Play")
        self.play.setToolTip("Play this sound now")
        self.play.clicked.connect(lambda: play(self.sound()))
        self.box.toggled.connect(self._show_on)
        for widget in (self.box, self.combo, self.play):
            self.addWidget(widget)
        self.addStretch(1)
        self._show_on(on)

    def _add_file(self, path: str) -> None:
        """The picked file, just above "A sound file…", replacing the one picked before."""
        old = next((i for i in range(self.combo.count())
                    if self.combo.itemData(i) != toasts.PICK_FILE and toasts.is_file(self.combo.itemData(i))), None)
        if old is not None:
            self.combo.removeItem(old)
        self.combo.insertItem(self.combo.findData(toasts.PICK_FILE), os.path.basename(path), path)
        self.combo.setItemData(self.combo.findData(path), path, Qt.ItemDataRole.ToolTipRole)

    def ask_for_file(self, start: str) -> str:
        path, _ = QFileDialog.getOpenFileName(self.parent_widget, "Pick a sound", start, "Sounds (*.wav)")
        return path

    def _changed(self, index: int) -> None:
        if self.combo.itemData(index) != toasts.PICK_FILE:
            self._chosen = self.combo.itemData(index)
            return
        start = os.path.dirname(self._chosen) if toasts.is_file(self._chosen) else os.path.join(
            os.environ.get("SystemRoot", "C:\\Windows"), "Media")
        path = self.ask_for_file(start)
        self.combo.blockSignals(True)
        if path:
            self._chosen = os.path.normpath(path)
            self._add_file(self._chosen)
        self.combo.setCurrentIndex(self.combo.findData(self._chosen))
        self.combo.blockSignals(False)

    def _show_on(self, on: bool) -> None:
        self.combo.setEnabled(on)
        self.play.setEnabled(on)

    def sound(self) -> str:
        return self._chosen


class SettingsDialog(QDialog):
    def __init__(self, current: Settings, parent=None, check_updates: bool = False,
                 find_ffmpeg: Callable[[str], Optional[str]] = video.find_ffmpeg,
                 install_ffmpeg: Callable[..., Optional[str]] = install_ffmpeg_with_progress,
                 browsers: Sequence[browser_settings.BrowserProfile] = (),
                 find_qbittorrent: Callable[[str], str] = torrents.find_qbittorrent,
                 link_status: Optional[Callable[[], str]] = None,
                 open_link: Optional[Callable[[QWidget], None]] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.current = current
        self.ffmpeg_path = current.ffmpeg_path               # what Browse… picked; "" = find it
        self._find_ffmpeg, self._install = find_ffmpeg, install_ffmpeg
        self._find_qbittorrent = find_qbittorrent
        layout = QVBoxLayout(self)
        form = QFormLayout()
        where = QLabel(f"{current.base_folder}   (the Default folder: change it under Rules > Places)")
        where.setEnabled(False)
        form.addRow("Downloads go to", where)
        self.max_downloads = QSpinBox()
        self.max_downloads.setRange(1, 20)
        self.max_downloads.setValue(current.max_downloads)
        form.addRow("Downloads at once", self.max_downloads)
        self.connections = QSpinBox()
        self.connections.setRange(1, 16)
        self.connections.setValue(current.connections)
        form.addRow("Connections per file", self.connections)
        self.speed_limit = QSpinBox()
        self.speed_limit.setRange(0, 10_000_000)
        self.speed_limit.setSuffix(" KiB/s")
        self.speed_limit.setSpecialValueText("No limit")
        self.speed_limit.setValue(current.speed_limit_kib)
        form.addRow("Total speed limit", self.speed_limit)
        self.video_quality = QComboBox()
        for key, label in QUALITIES.items():
            self.video_quality.addItem(label, key)
        self.video_quality.setCurrentIndex(max(self.video_quality.findData(current.video_quality), 0))
        form.addRow("Video sites", self.video_quality)
        self.ffmpeg_label = QLabel()
        self.ffmpeg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ffmpeg_install = QPushButton("Install ffmpeg")
        self.ffmpeg_install.setToolTip("Installs ffmpeg with winget (about 100 MB; accepts winget's package "
                                       "agreements), the same way Accipio's installer offers to")
        self.ffmpeg_install.clicked.connect(self._install_ffmpeg)
        self.ffmpeg_browse = QPushButton("Browse…")
        self.ffmpeg_browse.setToolTip("Pick an ffmpeg.exe of your own")
        self.ffmpeg_browse.clicked.connect(self._browse_ffmpeg)
        self.ffmpeg_auto = QPushButton("Find automatically")
        self.ffmpeg_auto.setToolTip("Forget the picked ffmpeg and look for one the usual way again")
        self.ffmpeg_auto.clicked.connect(self._find_ffmpeg_automatically)
        ffmpeg_row = QHBoxLayout()
        for widget in (self.ffmpeg_label, self.ffmpeg_install, self.ffmpeg_browse, self.ffmpeg_auto):
            ffmpeg_row.addWidget(widget)
        ffmpeg_row.addStretch(1)
        form.addRow("ffmpeg", ffmpeg_row)
        self._show_ffmpeg()
        self.ask_playlists = QCheckBox("When a video link comes from a playlist, offer the rest of the playlist")
        self.ask_playlists.setToolTip("Off: such a link is just that video. A link to a playlist itself always asks "
                                      "before anything downloads.")
        self.ask_playlists.setChecked(current.ask_playlists)
        form.addRow("Playlists", self.ask_playlists)
        self.file_browser_saves = QCheckBox("Move files the browser had to download itself to where the rules say")
        self.file_browser_saves.setToolTip(
            "Some files only the browser can fetch: one a page builds itself (an export, a \"blob:\" link), and one "
            "the site refuses Accipio. The browser saves those in its own download folder. With this on, Accipio then "
            "moves each one by your rules and lists it as finished. A file you gave a name of your own in the "
            "browser's Save As box stays where you put it. Off: such files stay where the browser saved them. "
            "Needs version 0.1.4 or newer of the browser extension.")
        self.file_browser_saves.setChecked(current.file_browser_saves)
        browser_column = QVBoxLayout()
        browser_column.addWidget(self.file_browser_saves)
        # what the browsers are set to do themselves, read from their own settings (never changed by Accipio)
        told = [browser_settings.describe(profile) for profile in browsers] or \
            ["No browser settings were found on this PC (Chrome, Edge, Firefox, Opera, Opera GX, Brave, Vivaldi)."]
        if any(profile.asks for profile in browsers):
            told.append("Where the browser asks where to save, you chose the place, so Accipio leaves the file there.")
        self.browser_note = QLabel("\n".join(told))
        self.browser_note.setWordWrap(True)
        self.browser_note.setEnabled(False)
        self.browser_note.setToolTip("Read from the browser's own settings and your organisation's policies each time "
                                     "this window opens. Accipio only reads them.")
        browser_column.addWidget(self.browser_note)
        form.addRow("Browser's own files", browser_column)
        form.addRow("Torrents", self._build_torrents(current))
        form.addRow("Finished", self._build_clear_finished(current))
        self.play_sound: Callable[[str], None] = toasts.play         # tests put a fake here
        notify_column = QVBoxLayout()
        self.sound_lines = {}
        for kind, text in ((ADDED, "A download is added"), (FINISHED, "A download finishes"),
                           (FAILED, "A download fails")):
            line = SoundLine(text, getattr(current, f"notify_{kind}"), current.sound_for(kind),
                             lambda sound: self.play_sound(sound), self)
            self.sound_lines[kind] = line
            notify_column.addLayout(line)
        self.notify_added, self.notify_finished, self.notify_failed = (self.sound_lines[k].box for k in KINDS)
        form.addRow("Notify when", notify_column)
        form.addRow("Other Accipios", self._build_link_row(link_status, open_link))
        self.check_updates = QCheckBox("Check for a new version once a day")
        self.check_updates.setToolTip("At startup, at most once a day, ask GitHub whether a newer version has been "
                                      "released, and offer it if so. Nothing about you or your files is sent. "
                                      "Help > Check for Updates works whether this is on or not.")
        self.check_updates.setChecked(check_updates)
        form.addRow("Updates", self.check_updates)
        debug_row = QHBoxLayout()
        self.debug_log = QCheckBox("Write a debug log")
        self.debug_log.setToolTip("What the browser offered or left to Chrome, how each link was checked and placed, "
                                  "what was decided for archives, and how each notice was shown, in debug.log in Accipio's folder. Cookies are "
                                  "never written. Help > Open Debug Log shows it.")
        self.debug_log.setChecked(current.debug_log)
        self.debug_delete = QPushButton("Delete the log")
        self.debug_delete.clicked.connect(self._delete_debug_log)
        debug_row.addWidget(self.debug_log)
        debug_row.addWidget(self.debug_delete)
        debug_row.addStretch(1)
        form.addRow("Debug log", debug_row)
        archives = QLabel("Under Rules > 5 · Archives")
        archives.setEnabled(False)
        form.addRow("Archives", archives)
        layout.addLayout(form)
        note = QLabel("Connections per file takes effect for downloads started after the next restart.")
        note.setWordWrap(True)
        note.setEnabled(False)
        layout.addWidget(note)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    # ---- other Accipios (the link): the window that sets it up is opened from here -----------------------------

    def _build_link_row(self, link_status, open_link) -> QHBoxLayout:
        self._link_status, self._open_link = link_status, open_link
        row = QHBoxLayout()
        self.link_label = QLabel()
        self.link_label.setWordWrap(True)
        self.link_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.link_button = QPushButton("Set up…")
        self.link_button.setToolTip("Pair with another Accipio, to send downloads to it or to collect for it. "
                                    "Changes there take effect at once, not only after OK.")
        self.link_button.setEnabled(open_link is not None)
        self.link_button.clicked.connect(self._set_up_link)
        row.addWidget(self.link_label, 1)
        row.addWidget(self.link_button, 0, Qt.AlignmentFlag.AlignTop)
        self._show_link()
        return row

    def _show_link(self) -> None:
        self.link_label.setText(self._link_status() if self._link_status is not None else "Not available here.")

    def _set_up_link(self) -> None:
        if self._open_link is not None:
            self._open_link(self)
            self._show_link()                                # what that window did shows here when it closes

    # ---- torrents are passed on to a torrent program (2026-11-17#07, core/torrents.py) --------------------------

    def _build_torrents(self, current: Settings) -> QVBoxLayout:
        column = QVBoxLayout()
        self.qbittorrent_path = current.qbittorrent_path     # what Browse… picked; "" = find it
        self.torrents = QComboBox()
        self.torrents.addItem("Leave them alone", torrents.OFF)
        self.torrents.addItem("Open them with the torrent program Windows uses", torrents.SYSTEM)
        self.torrents.addItem("Send them to qBittorrent, saving where my rules say", torrents.QBITTORRENT)
        self.torrents.setCurrentIndex(max(self.torrents.findData(current.torrents), 0))
        self.torrents.setToolTip(
            "Magnet links (pasted into Accipio) and .torrent files (once Accipio has downloaded them). Accipio does "
            "not download torrents itself: it passes them on.\n"
            "Leave them alone: a magnet link is refused, a .torrent file is kept as a file.\n"
            "The torrent program Windows uses: works with any torrent program; that program decides where its files go.\n"
            "qBittorrent: the folder your rules choose is given to qBittorrent as the place to save.")
        column.addWidget(self.torrents)
        row = QHBoxLayout()
        self.qbittorrent_label = QLabel()
        self.qbittorrent_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.qbittorrent_browse = QPushButton("Browse…")
        self.qbittorrent_browse.setToolTip("Pick qbittorrent.exe yourself")
        self.qbittorrent_browse.clicked.connect(self._browse_qbittorrent)
        row.addWidget(self.qbittorrent_label)
        row.addWidget(self.qbittorrent_browse)
        row.addStretch(1)
        column.addLayout(row)
        self.torrents.currentIndexChanged.connect(lambda *_: self._show_qbittorrent())
        self._show_qbittorrent()
        return column

    def _show_qbittorrent(self) -> None:
        """The qBittorrent line only when that choice is picked: where it was found, or that it was not."""
        wanted = self.torrents.currentData() == torrents.QBITTORRENT
        found = self._find_qbittorrent(self.qbittorrent_path) if wanted else ""
        self.qbittorrent_label.setVisible(wanted)
        self.qbittorrent_browse.setVisible(wanted)
        if wanted:
            self.qbittorrent_label.setText(found or "qBittorrent was not found: pick qbittorrent.exe, or torrents "
                                                    "cannot be passed on")
            self.qbittorrent_label.setStyleSheet("" if found else f"color: {WARNING_COLOR};")

    def _ask_for_qbittorrent(self) -> str:
        start = os.path.dirname(self.qbittorrent_path or self._find_qbittorrent("") or "")
        name = "qbittorrent.exe" if sys.platform == "win32" else "qbittorrent"
        path, _ = QFileDialog.getOpenFileName(self, "Pick qBittorrent", start, f"qBittorrent ({name});;All files (*)")
        return path

    def _browse_qbittorrent(self) -> None:
        path = self._ask_for_qbittorrent()
        if path:
            self.qbittorrent_path = os.path.normpath(path)
            self._show_qbittorrent()

    # ---- when finished downloads leave the list (2026-11-07, 71A + 75A: radio buttons) ------------------------

    def _build_clear_finished(self, current: Settings) -> QVBoxLayout:
        column = QVBoxLayout()
        self.clear_choice = QButtonGroup(self)
        self.clear_after_minutes = QSpinBox()
        self.clear_after_minutes.setRange(1, 24 * 60)
        self.clear_after_minutes.setValue(current.clear_after_minutes)
        texts = {CLEAR_NEVER: "Keep them until I clear them", CLEAR_AT_START: "Clear them at the next start",
                 CLEAR_AFTER: "Clear them", CLEAR_AT_ONCE: "Clear them as soon as they finish"}
        self.clear_buttons = {}
        for index, how in enumerate(CLEAR_CHOICES):
            button = QRadioButton(texts[how])
            self.clear_choice.addButton(button, index)
            self.clear_buttons[how] = button
            if how == CLEAR_AFTER:
                row = QHBoxLayout()
                row.addWidget(button)
                row.addWidget(self.clear_after_minutes)
                row.addWidget(QLabel("minutes after they finish"))
                row.addStretch(1)
                column.addLayout(row)
            else:
                column.addWidget(button)
        self.clear_buttons[current.clear_finished].setChecked(True)
        self.clear_choice.idToggled.connect(lambda *_: self._show_clear_minutes())
        self._show_clear_minutes()
        note = QLabel("Only the list: the files stay where they were saved. Edit > Clear Finished works with every "
                      "choice, and an archive waiting for your choice stays until you have made it.")
        note.setWordWrap(True)
        note.setEnabled(False)
        column.addWidget(note)
        return column

    def _show_clear_minutes(self) -> None:
        self.clear_after_minutes.setEnabled(self.clear_buttons[CLEAR_AFTER].isChecked())

    def clear_finished_choice(self) -> str:
        return next(how for how, button in self.clear_buttons.items() if button.isChecked())

    def _delete_debug_log(self) -> None:
        debug_log.LOG.delete()
        self.debug_delete.setText("Deleted")

    # ---- ffmpeg ----------------------------------------------------------------------------------------------

    def _show_ffmpeg(self) -> None:
        """The path in use, or why videos come out worse without one; Install only then, Find automatically only
        while a picked path is set."""
        found = self._find_ffmpeg(self.ffmpeg_path)
        if found:
            self.ffmpeg_label.setText(found)
            self.ffmpeg_label.setStyleSheet("")
        else:
            self.ffmpeg_label.setText("Not found: video sites give lower quality, and audio is not converted to mp3")
            self.ffmpeg_label.setStyleSheet(f"color: {WARNING_COLOR};")
        self.ffmpeg_install.setVisible(not found and sys.platform == "win32")
        self.ffmpeg_auto.setVisible(bool(self.ffmpeg_path))

    def _ask_for_ffmpeg(self) -> str:
        start = os.path.dirname(self.ffmpeg_path or self._find_ffmpeg("") or "")
        name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
        path, _ = QFileDialog.getOpenFileName(self, "Pick ffmpeg", start, f"ffmpeg ({name});;All files (*)")
        return path

    def _browse_ffmpeg(self) -> None:
        path = self._ask_for_ffmpeg()
        if path:
            self.ffmpeg_path = os.path.normpath(path)
            self._show_ffmpeg()

    def _find_ffmpeg_automatically(self) -> None:
        self.ffmpeg_path = ""
        self._show_ffmpeg()

    def _install_ffmpeg(self) -> None:
        problem = self._install(self)
        self._show_ffmpeg()
        if problem and not self._find_ffmpeg(self.ffmpeg_path):       # winget also "fails" when it is already there
            QMessageBox.warning(self, "Install ffmpeg", problem)

    def result_settings(self) -> Settings:
        return dataclasses.replace(self.current, max_downloads=self.max_downloads.value(), connections=self.connections.value(),
                                   speed_limit_kib=self.speed_limit.value(),
                                   clear_finished=self.clear_finished_choice(),
                                   clear_after_minutes=self.clear_after_minutes.value(),
                                   video_quality=self.video_quality.currentData(),
                                   notify_added=self.notify_added.isChecked(),
                                   notify_finished=self.notify_finished.isChecked(),
                                   notify_failed=self.notify_failed.isChecked(),
                                   sound_added=self.sound_lines[ADDED].sound(),
                                   sound_finished=self.sound_lines[FINISHED].sound(),
                                   sound_failed=self.sound_lines[FAILED].sound(), ffmpeg_path=self.ffmpeg_path,
                                   ask_playlists=self.ask_playlists.isChecked(),
                                   file_browser_saves=self.file_browser_saves.isChecked(),
                                   torrents=self.torrents.currentData(), qbittorrent_path=self.qbittorrent_path,
                                   debug_log=self.debug_log.isChecked())
