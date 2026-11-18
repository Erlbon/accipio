"""Settings: a list of pages on the left, the page on the right (answer 111B, 2026-11-17).

The pages are Downloads, Video sites, Browser, Torrents, Notifications and Program. Under each control stands one
plain sentence that says what it does; a tooltip only adds detail. Everything here waits for OK, except two things
that say so themselves: Install ffmpeg, and Delete the log (which asks first). Other Accipios is not here: it is a
window of its own under Tools (answer 112B), because it applies at once.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                             QProgressDialog, QPushButton, QRadioButton, QSizePolicy, QSpinBox, QStackedWidget,
                             QVBoxLayout, QWidget)
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background

from accipio.core import browser_settings, debug_log, toasts, torrents, video
from accipio.core.notices import ADDED, FAILED, FINISHED, KINDS
from accipio.core.settings import CLEAR_AFTER, CLEAR_AT_ONCE, CLEAR_AT_START, CLEAR_CHOICES, CLEAR_NEVER, Settings
from accipio.core.video import QUALITIES
from accipio.gui.style import ROW_HEIGHT, WARNING_COLOR, mix

PAGES = ("Downloads", "Video sites", "Browser", "Torrents", "Notifications", "Program")
SIDE_WIDTH = 150
# what each torrent choice does, shown under the drop-down for the choice that is picked (card 4)
TORRENT_NOTES = {
    torrents.OFF: "A magnet link is refused, and a .torrent file is kept as a file.",
    torrents.SYSTEM: "Works with any torrent program. That program decides where its files go.",
    torrents.QBITTORRENT: "The folder your rules choose is given to qBittorrent as the place to save.",
}


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


def note(text: str) -> QLabel:
    """The plain sentence under a control: greyed, wrapping."""
    label = QLabel(text)
    label.setWordWrap(True)
    palette = QApplication.palette()        # mixed from the palette in use, so it is quieter in dark and in light
    muted = mix(palette.color(QPalette.ColorRole.Window), palette.color(QPalette.ColorRole.WindowText), 0.62)
    label.setStyleSheet(f"color: {muted};")
    return label


def signpost(text: str) -> QLabel:
    """A line that points somewhere else, with a link that goes there (card 2)."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def narrow(*widgets: QWidget) -> QHBoxLayout:
    """Controls at the width of their content, from the left (card 3): a number box is not as wide as the window."""
    row = QHBoxLayout()
    for widget in widgets:
        widget.setSizePolicy(QSizePolicy.Policy.Fixed, widget.sizePolicy().verticalPolicy())
        row.addWidget(widget)
    row.addStretch(1)
    return row


def under(control, *lines) -> QVBoxLayout:
    """A control with its sentences under it; each part is a widget or a layout."""
    column = QVBoxLayout()
    column.setSpacing(4)
    if isinstance(control, QWidget):
        column.addWidget(control)
    else:
        column.addLayout(control)
    for line in lines:
        if isinstance(line, QWidget):
            column.addWidget(line)
        else:
            column.addLayout(line)
    return column


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
    """``open_rules(tab, parent)`` opens the Rules window on "places" or "archives" (the two signposts);
    ``base_folder_of()`` gives the Default folder as it is now, which Rules may have changed meanwhile."""

    def __init__(self, current: Settings, parent=None, check_updates: bool = False,
                 find_ffmpeg: Callable[[str], Optional[str]] = video.find_ffmpeg,
                 install_ffmpeg: Callable[..., Optional[str]] = install_ffmpeg_with_progress,
                 browsers: Sequence[browser_settings.BrowserProfile] = (),
                 find_qbittorrent: Callable[[str], str] = torrents.find_qbittorrent,
                 open_rules: Optional[Callable[..., None]] = None,
                 base_folder_of: Optional[Callable[[], str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.current = current
        self.ffmpeg_path = current.ffmpeg_path               # what Browse… picked; "" = find it
        self._find_ffmpeg, self._install = find_ffmpeg, install_ffmpeg
        self._ffmpeg_installed = False                       # Install ffmpeg worked in this window: the line says so
        self._find_qbittorrent = find_qbittorrent
        self._open_rules = open_rules
        self._base_folder_of = base_folder_of or (lambda: self.current.base_folder)
        self.play_sound: Callable[[str], None] = toasts.play         # tests put a fake here

        layout = QVBoxLayout(self)
        body = QHBoxLayout()
        self.side = QListWidget()
        self.side.setFixedWidth(SIDE_WIDTH)
        self.stack = QStackedWidget()
        builders = (self._build_downloads, self._build_video, self._build_browser, self._build_torrents_page,
                    self._build_notifications, self._build_program)
        self.pages = {}
        for name, build in zip(PAGES, builders):
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(12, 4, 4, 4)
            form.setVerticalSpacing(14)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            build(form, current, browsers, check_updates)
            self.pages[name] = page
            entry = QListWidgetItem(name)
            entry.setSizeHint(QSize(0, ROW_HEIGHT + 4))       # room for the stylesheet's padding round each name
            self.side.addItem(entry)
            self.stack.addWidget(page)
        self.side.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.side.setCurrentRow(0)
        body.addWidget(self.side)
        body.addWidget(self.stack, 1)
        layout.addLayout(body, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.resize(max(self.sizeHint().width(), 760), self.sizeHint().height())

    def show_page(self, name: str) -> None:
        self.side.setCurrentRow(PAGES.index(name))

    # ---- Downloads -------------------------------------------------------------------------------------------

    def _build_downloads(self, form: QFormLayout, current: Settings, *_) -> None:
        self.where = signpost("")
        self.where.linkActivated.connect(lambda tab: self._go_to_rules(tab))
        self._show_where()
        form.addRow("Downloads go to", self.where)
        self.max_downloads = QSpinBox()
        self.max_downloads.setRange(1, 20)
        self.max_downloads.setValue(current.max_downloads)
        form.addRow("Downloads at once", under(narrow(self.max_downloads),
                                               note("How many run at the same time. The rest wait their turn.")))
        self.connections = QSpinBox()
        self.connections.setRange(1, 16)
        self.connections.setValue(current.connections)
        form.addRow("Connections per file", under(
            narrow(self.connections),
            note("One file is fetched in up to this many parts at once. A change takes effect for downloads started "
                 "after the next restart.")))
        self.speed_limit = QSpinBox()
        self.speed_limit.setRange(0, 10_000_000)
        self.speed_limit.setSuffix(" KiB/s")
        self.speed_limit.setSpecialValueText("No limit")
        self.speed_limit.setValue(current.speed_limit_kib)
        form.addRow("Total speed limit", under(narrow(self.speed_limit),
                                               note("For all downloads together. 0 means no limit.")))
        form.addRow("Finished downloads", self._build_clear_finished(current))
        archives = signpost("What happens to a finished archive is set under "
                            "<a href='archives'>Rules &gt; 5 · Archives</a>.")
        archives.linkActivated.connect(lambda tab: self._go_to_rules(tab))
        form.addRow("Archives", archives)

    def _show_where(self) -> None:
        self.where.setText(f"{self._base_folder_of()}<br>The Default folder. Change it under "
                           "<a href='places'>Rules &gt; 1 · Places</a>.")

    def _go_to_rules(self, tab: str) -> None:
        """A signpost was clicked: the Rules window opens on that tab, over this one. What is changed and OK'd
        there is saved there; this window only shows the Default folder again."""
        if self._open_rules is not None:
            self._open_rules(tab, self)
            self._show_where()

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
        self.clear_note = note("Only the list: the files stay where they were saved. Downloads > Clear Finished works "
                               "with every choice, and an archive waiting for your choice stays until you have made it.")
        column.addWidget(self.clear_note)
        return column

    def _show_clear_minutes(self) -> None:
        self.clear_after_minutes.setEnabled(self.clear_buttons[CLEAR_AFTER].isChecked())

    def clear_finished_choice(self) -> str:
        return next(how for how, button in self.clear_buttons.items() if button.isChecked())

    # ---- Video sites -----------------------------------------------------------------------------------------

    def _build_video(self, form: QFormLayout, current: Settings, *_) -> None:
        self.video_quality = QComboBox()
        for key, label in QUALITIES.items():
            self.video_quality.addItem(label, key)
        self.video_quality.setCurrentIndex(max(self.video_quality.findData(current.video_quality), 0))
        form.addRow("Video quality", under(narrow(self.video_quality),
                                           note("What is asked for on YouTube and the other video sites.")))
        self.ffmpeg_label = QLabel()
        self.ffmpeg_label.setWordWrap(True)
        self.ffmpeg_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ffmpeg_install = QPushButton("Install ffmpeg")
        self.ffmpeg_install.setToolTip("Installs ffmpeg with winget (about 100 MB; accepts winget's package "
                                       "agreements), the same way Accipio's installer offers to. It is installed "
                                       "at once: Cancel in this window does not take it back.")
        self.ffmpeg_install.clicked.connect(self._install_ffmpeg)
        self.ffmpeg_browse = QPushButton("Browse…")
        self.ffmpeg_browse.setToolTip("Pick an ffmpeg.exe of your own")
        self.ffmpeg_browse.clicked.connect(self._browse_ffmpeg)
        self.ffmpeg_auto = QPushButton("Find automatically")
        self.ffmpeg_auto.setToolTip("Forget the picked ffmpeg and look for one the usual way again")
        self.ffmpeg_auto.clicked.connect(self._find_ffmpeg_automatically)
        form.addRow("ffmpeg", under(
            self.ffmpeg_label, narrow(self.ffmpeg_install, self.ffmpeg_browse, self.ffmpeg_auto),
            note("ffmpeg joins the separate picture and sound most video sites send, and makes the mp3 for "
                 "\"Audio only\".")))
        self._show_ffmpeg()
        self.ask_playlists = QCheckBox("When a video link comes from a playlist, offer the rest of the playlist")
        self.ask_playlists.setChecked(current.ask_playlists)
        form.addRow("Playlists", under(self.ask_playlists,
                                       note("Off: such a link is just that video. A link to a playlist itself always "
                                            "asks before anything downloads.")))

    def _show_ffmpeg(self) -> None:
        """The path in use, or why videos come out worse without one; Install only then, Find automatically only
        while a picked path is set. After Install ffmpeg worked the line says "installed" (card 7): that is done,
        whatever OK or Cancel does afterwards."""
        found = self._find_ffmpeg(self.ffmpeg_path)
        if found:
            self.ffmpeg_label.setText(f"{found}   (installed just now)" if self._ffmpeg_installed else found)
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
            self._ffmpeg_installed = False
            self._show_ffmpeg()

    def _find_ffmpeg_automatically(self) -> None:
        self.ffmpeg_path = ""
        self._show_ffmpeg()

    def _install_ffmpeg(self) -> None:
        problem = self._install(self)
        found = self._find_ffmpeg(self.ffmpeg_path)
        self._ffmpeg_installed = bool(found)
        self._show_ffmpeg()
        if problem and not found:                         # winget also "fails" when it is already there
            QMessageBox.warning(self, "Install ffmpeg", problem)

    # ---- Browser ---------------------------------------------------------------------------------------------

    def _build_browser(self, form: QFormLayout, current: Settings, browsers, *_) -> None:
        self.file_browser_saves = QCheckBox("Move files the browser had to download itself to where the rules say")
        self.file_browser_saves.setToolTip(
            "A file you gave a name of your own in the browser's Save As box stays where you put it. Off: such "
            "files stay where the browser saved them. Needs version 0.1.4 or newer of the browser extension.")
        self.file_browser_saves.setChecked(current.file_browser_saves)
        form.addRow("Browser's own files", under(
            self.file_browser_saves,
            note("Some files only the browser can fetch: one a page builds itself (an export), and one the site "
                 "refuses Accipio. The browser saves those in its own download folder. With this on, Accipio then "
                 "moves each one by your rules and lists it as finished.")))
        # what the browsers are set to do themselves, read from their own settings (never changed by Accipio)
        told = [browser_settings.describe(profile) for profile in browsers] or \
            ["No browser settings were found on this PC (Chrome, Edge, Firefox, Opera, Opera GX, Brave, Vivaldi)."]
        if any(profile.asks for profile in browsers):
            told.append("Where the browser asks where to save, you chose the place, so Accipio leaves the file there.")
        self.browser_note = QLabel("\n".join(told))
        self.browser_note.setWordWrap(True)
        self.browser_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Your browsers", under(
            self.browser_note,
            note("Read from each browser's own settings and your organisation's policies every time this window "
                 "opens. Accipio only reads them.")))

    # ---- Torrents: passed on to a torrent program (2026-11-17#07, core/torrents.py) ----------------------------

    def _build_torrents_page(self, form: QFormLayout, current: Settings, *_) -> None:
        form.addRow("Torrents", self._build_torrents(current))

    def _build_torrents(self, current: Settings) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(4)
        self.qbittorrent_path = current.qbittorrent_path     # what Browse… picked; "" = find it
        self.torrents = QComboBox()
        self.torrents.addItem("Leave them alone", torrents.OFF)
        self.torrents.addItem("Open them with the torrent program Windows uses", torrents.SYSTEM)
        self.torrents.addItem("Send them to qBittorrent, saving where my rules say", torrents.QBITTORRENT)
        self.torrents.setCurrentIndex(max(self.torrents.findData(current.torrents), 0))
        column.addLayout(narrow(self.torrents))
        self.torrents_note = note("")
        column.addWidget(self.torrents_note)
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
        column.addWidget(note("Magnet links (pasted into Accipio) and .torrent files (once Accipio has downloaded "
                              "them). Accipio does not download torrents itself: it passes them on."))
        self.torrents.currentIndexChanged.connect(lambda *_: self._show_qbittorrent())
        self._show_qbittorrent()
        return column

    def _show_qbittorrent(self) -> None:
        """What the picked choice does, and the qBittorrent line only when that choice is picked: where it was
        found, or that it was not."""
        self.torrents_note.setText(TORRENT_NOTES[self.torrents.currentData()])
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

    # ---- Notifications ---------------------------------------------------------------------------------------

    def _build_notifications(self, form: QFormLayout, current: Settings, *_) -> None:
        column = QVBoxLayout()
        self.sound_lines = {}
        for kind, text in ((ADDED, "A download is added"), (FINISHED, "A download finishes"),
                           (FAILED, "A download fails")):
            line = SoundLine(text, getattr(current, f"notify_{kind}"), current.sound_for(kind),
                             lambda sound: self.play_sound(sound), self)
            self.sound_lines[kind] = line
            column.addLayout(line)
        self.notify_added, self.notify_finished, self.notify_failed = (self.sound_lines[k].box for k in KINDS)
        for part in ("box", "combo"):                         # the three lines in columns, not ragged
            widest = max(getattr(line, part).sizeHint().width() for line in self.sound_lines.values())
            for line in self.sound_lines.values():
                getattr(line, part).setMinimumWidth(widest)
        column.addWidget(note("A tick shows a Windows notification when that happens. The sound beside it plays "
                              "with the notification."))
        form.addRow("Notices and sounds", column)

    # ---- Program ---------------------------------------------------------------------------------------------

    def _build_program(self, form: QFormLayout, current: Settings, browsers, check_updates: bool) -> None:
        self.check_updates = QCheckBox("Check for a new version once a day")
        self.check_updates.setToolTip("Help > Check for Updates works whether this is on or not.")
        self.check_updates.setChecked(check_updates)
        form.addRow("Updates", under(
            self.check_updates,
            note("At start, at most once a day, Accipio asks GitHub whether a newer version has been released, and "
                 "offers it if so. Nothing about you or your files is sent.")))
        debug_row = QHBoxLayout()
        self.debug_log = QCheckBox("Write a debug log")
        self.debug_log.setToolTip("What the browser offered or left to Chrome, how each link was checked and placed, "
                                  "what was decided for archives, and how each notice was shown.")
        self.debug_log.setChecked(current.debug_log)
        self.debug_delete = QPushButton("Delete the log…")
        self.debug_delete.setToolTip("Deletes debug.log now, after asking. Cancel in this window does not bring it back.")
        self.debug_delete.clicked.connect(self._delete_debug_log)
        debug_row.addWidget(self.debug_log)
        debug_row.addWidget(self.debug_delete)
        debug_row.addStretch(1)
        form.addRow("Debug log", under(
            debug_row,
            note("Writes what Accipio does to debug.log in its own folder, for a bug report. Cookies are never "
                 "written. Help > Open Debug Log shows it.")))

    def confirm_delete_log(self) -> bool:
        """The question before the log is deleted (card 7; tests replace it)."""
        answer = QMessageBox.question(self, "Delete the log", "Delete debug.log now?\n\nIt is deleted at once: Cancel "
                                      "in the Settings window does not bring it back.",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _delete_debug_log(self) -> None:
        if not self.confirm_delete_log():
            return
        debug_log.LOG.delete()
        self.debug_delete.setText("Deleted")

    # ---- the result ------------------------------------------------------------------------------------------

    def result_settings(self, base: Optional[Settings] = None) -> Settings:
        """What this window sets, laid onto ``base``: the settings as they are when OK is pressed (the main window
        passes them), so that what is not set here keeps the value it has now. Without ``base``, onto the settings
        the window opened with."""
        return dataclasses.replace(base if base is not None else self.current,
                                   max_downloads=self.max_downloads.value(), connections=self.connections.value(),
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
