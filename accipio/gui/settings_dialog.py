"""Settings: the base folder, how many downloads at once, connections per file, the speed limit, history."""
from __future__ import annotations

import dataclasses
import os

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from accipio.core.settings import Settings
from accipio.core.video import QUALITIES, find_ffmpeg


class SettingsDialog(QDialog):
    def __init__(self, current: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.current = current
        layout = QVBoxLayout(self)
        form = QFormLayout()
        folder_row = QHBoxLayout()
        self.base_folder = QLineEdit(current.base_folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.base_folder, 1)
        folder_row.addWidget(browse)
        form.addRow("Base folder", folder_row)
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

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(self, "Base folder", self.base_folder.text())
        if chosen:
            self.base_folder.setText(os.path.normpath(chosen))

    def result_settings(self) -> Settings:
        return dataclasses.replace(self.current, base_folder=self.base_folder.text().strip() or self.current.base_folder,
                                   max_downloads=self.max_downloads.value(), connections=self.connections.value(),
                                   speed_limit_kib=self.speed_limit.value(), keep_history=self.keep_history.isChecked(),
                                   video_quality=self.video_quality.currentData())
