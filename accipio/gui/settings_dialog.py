"""Settings: the base folder, how many downloads at once, connections per file, the speed limit, history,
and which notifications to show."""
from __future__ import annotations

import dataclasses
import os
import sys
import threading
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton, QSpinBox, QVBoxLayout)
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background

from accipio.core import video
from accipio.core.settings import Settings
from accipio.core.video import QUALITIES

WARNING_COLOR = "#BA7517"           # amber, readable on light and dark backgrounds


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


class SettingsDialog(QDialog):
    def __init__(self, current: Settings, parent=None, check_updates: bool = False,
                 find_ffmpeg: Callable[[str], Optional[str]] = video.find_ffmpeg,
                 install_ffmpeg: Callable[..., Optional[str]] = install_ffmpeg_with_progress):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.current = current
        self.ffmpeg_path = current.ffmpeg_path               # what Browse… picked; "" = find it
        self._find_ffmpeg, self._install = find_ffmpeg, install_ffmpeg
        layout = QVBoxLayout(self)
        form = QFormLayout()
        where = QLabel(f"{current.base_folder}   (the Default folder: change it under Rules > Folders)")
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
        self.keep_history = QCheckBox("Keep finished downloads in the list after a restart")
        self.keep_history.setChecked(current.keep_history)
        form.addRow("History", self.keep_history)
        notify_row = QHBoxLayout()
        self.notify_added = QCheckBox("Added")
        self.notify_finished = QCheckBox("Finished")
        self.notify_failed = QCheckBox("Failed")
        for box, on in ((self.notify_added, current.notify_added), (self.notify_finished, current.notify_finished),
                        (self.notify_failed, current.notify_failed)):
            box.setChecked(on)
            notify_row.addWidget(box)
        notify_row.addStretch(1)
        form.addRow("Notify when", notify_row)
        self.check_updates = QCheckBox("Check for a new version once a day")
        self.check_updates.setToolTip("At startup, at most once a day, ask GitHub whether a newer version has been "
                                      "released, and offer it if so. Nothing about you or your files is sent. "
                                      "Help > Check for Updates works whether this is on or not.")
        self.check_updates.setChecked(check_updates)
        form.addRow("Updates", self.check_updates)
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
                                   speed_limit_kib=self.speed_limit.value(), keep_history=self.keep_history.isChecked(),
                                   video_quality=self.video_quality.currentData(),
                                   notify_added=self.notify_added.isChecked(),
                                   notify_finished=self.notify_finished.isChecked(),
                                   notify_failed=self.notify_failed.isChecked(), ffmpeg_path=self.ffmpeg_path)
