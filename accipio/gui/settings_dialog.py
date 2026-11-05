"""Settings: the base folder, how many downloads at once, connections per file, the speed limit, history,
and which notifications to show."""
from __future__ import annotations

import dataclasses
import os

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from accipio.core.settings import Settings
from accipio.core.video import QUALITIES, find_ffmpeg


class SettingsDialog(QDialog):
    def __init__(self, current: Settings, parent=None, check_updates: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.current = current
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
        archives = QHBoxLayout()
        self.unpack_archives = QCheckBox("Look inside downloaded zips and tars and ask what to do (trial)")
        self.unpack_archives.setToolTip("A finished .zip or .tar shows what is inside and offers to unpack it as a "
                                        "folder, sort the files by your rules, or keep it packed. Formats that are "
                                        "zips underneath (.cbz, .epub, .docx) are never opened.")
        self.unpack_archives.setChecked(current.unpack_archives)
        self.after_unpack = QComboBox()
        self.after_unpack.addItem("Keep the archive", "keep")
        self.after_unpack.addItem("Move the archive to the Recycle Bin", "delete")
        self.after_unpack.setCurrentIndex(max(self.after_unpack.findData(current.after_unpack), 0))
        self.after_unpack.setToolTip("What happens to the archive once its files are out")
        archives.addWidget(self.unpack_archives)
        archives.addWidget(self.after_unpack)
        archives.addStretch(1)
        form.addRow("Archives", archives)
        layout.addLayout(form)
        ffmpeg = find_ffmpeg()
        note = QLabel("Connections per file takes effect for downloads started after the next restart.\n"
                      + (f"ffmpeg: {ffmpeg}" if ffmpeg else
                         "ffmpeg was not found: video sites give the best single file, and audio is not converted to mp3."))
        note.setWordWrap(True)
        note.setEnabled(False)
        layout.addWidget(note)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def result_settings(self) -> Settings:
        return dataclasses.replace(self.current, max_downloads=self.max_downloads.value(), connections=self.connections.value(),
                                   speed_limit_kib=self.speed_limit.value(), keep_history=self.keep_history.isChecked(),
                                   video_quality=self.video_quality.currentData(),
                                   notify_added=self.notify_added.isChecked(),
                                   notify_finished=self.notify_finished.isChecked(),
                                   notify_failed=self.notify_failed.isChecked(),
                                   unpack_archives=self.unpack_archives.isChecked(),
                                   after_unpack=self.after_unpack.currentData())
