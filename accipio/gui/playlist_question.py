"""The playlist question (decided 2026-11-06, 58D): a small box that asks before a playlist is downloaded.

Two cases, both from a row's ``video["ask"]`` (core/queue.py):
- "rest": a video link carried a list. The video is already downloading; the box offers the other videos.
  (Never for a YouTube Mix, which is just its video: 59B.)
- "list": a link to a list itself. Nothing downloads until the user says yes.
The safe answer is the default button, and Esc or closing the box gives it too, so a playlist never starts by
accident. "Always just the video" (only for "rest") switches the question off; Settings switches it back on.
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from accipio.core.store import Row

MIX_NAME = "YouTube Mix"


def describe(ask: dict) -> str:
    count = ask.get("count", 0)
    if ask.get("mix"):
        return f"{MIX_NAME}: {count} videos YouTube picked around this one"
    return f"{ask.get('title') or 'Playlist'}: {count} videos"


class PlaylistQuestion(QDialog):
    answered = pyqtSignal(int, bool, bool)          # row id, take the whole list, never ask again

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Playlist")
        self.setMinimumWidth(440)
        self.row_id = 0
        self.heading = QLabel()
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.never = QCheckBox("Always just the video, don't ask again (Settings can switch it back)")
        self.take_all = QPushButton()
        self.just_one = QPushButton()
        self.take_all.setAutoDefault(False)
        self.just_one.setDefault(True)
        self.take_all.clicked.connect(lambda: self._answer(True))
        self.just_one.clicked.connect(lambda: self._answer(False))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.take_all)
        buttons.addWidget(self.just_one)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        for widget in (self.heading, self.detail, self.never):
            layout.addWidget(widget)
        layout.addSpacing(4)
        layout.addLayout(buttons)

    def ask(self, row: Row) -> None:
        ask = row.video["ask"]
        self.row_id = row.id
        self.never.setChecked(False)
        count = ask.get("count", 0)
        if ask.get("kind") == "rest":
            self.heading.setText("This video is part of a playlist")
            self.detail.setText(f"{row.video.get('title') or row.filename or row.url}\nFrom {describe(ask)}")
            self.take_all.setText(f"All {count} videos")
            self.just_one.setText("Just this video")
            self.never.show()
        else:
            self.heading.setText("Download a whole playlist?")
            self.detail.setText(describe(ask))
            self.take_all.setText(f"Download all {count}")
            self.just_one.setText("Don't download")
            self.never.hide()
        self.just_one.setFocus()
        self.show()
        self.raise_()
        self.activateWindow()

    def _answer(self, take_all: bool) -> None:
        never = self.never.isChecked() and not self.never.isHidden() and not take_all
        self.hide()
        self.answered.emit(self.row_id, take_all, never)

    def reject(self) -> None:                       # Esc or the close button: the safe answer
        if self.isVisible():
            self._answer(False)
