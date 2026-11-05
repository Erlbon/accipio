"""The box under the download list for a finished archive (the unpack trial, decision 43E, mockup 2026-11-05).

A waiting archive: "tool.zip: 3 files inside. What now?", where each file would go, three buttons with the
suggested one first and default (Unpack as a folder in <folder> / Sort them / Keep packed; hovering one shows its
plan), and "Do this every time for <file group> from <site group>", which makes the choice a rule.

An archive a rule unpacked by itself: what happened, with Undo and OK.

The box only shows and asks; the main window does the work on its worker thread.
"""
from __future__ import annotations

import os
from typing import Callable, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from accipio.core import unpack
from accipio.core.store import Row
from accipio.gui.queue_model import files

PREVIEW_LINES = 8
WHY = {unpack.FOLDER: "Suggested because there's a program inside.",
       unpack.SORT: "Suggested because your rules know the files inside.",
       unpack.KEEP: "Suggested because your rules don't know all the files inside."}


class ArchiveBox(QFrame):
    chosen = pyqtSignal(int, str, str, object)        # row id, mode, folder name, (file group, site group) or None
    undo = pyqtSignal(int)
    seen = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.row_id: Optional[int] = None
        self.key: Optional[Tuple] = None
        self.suggestion = unpack.KEEP
        self.plan: Callable[[str, str], List[unpack.Move]] = lambda mode, folder: []
        self.base_folder = ""
        layout = QVBoxLayout(self)
        self.title = QLabel()
        bold = self.title.font()
        bold.setBold(True)
        self.title.setFont(bold)
        layout.addWidget(self.title)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        mono = self.preview.font()
        mono.setFamily("Consolas")
        self.preview.setFont(mono)
        layout.addWidget(self.preview)

        self.choices = QHBoxLayout()
        self.folder_button = QPushButton("Unpack as a folder in")
        self.folder_choice = QComboBox()
        folder_part = QWidget()
        folder_layout = QHBoxLayout(folder_part)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.setSpacing(4)
        folder_layout.addWidget(self.folder_button)
        folder_layout.addWidget(self.folder_choice)
        self.sort_button = QPushButton("Sort them")
        self.keep_button = QPushButton("Keep packed")
        self._parts = {unpack.FOLDER: folder_part, unpack.SORT: self.sort_button, unpack.KEEP: self.keep_button}
        self._buttons = {unpack.FOLDER: self.folder_button, unpack.SORT: self.sort_button, unpack.KEEP: self.keep_button}
        for mode, button in self._buttons.items():
            button.clicked.connect(lambda _=False, m=mode: self._choose(m))
            button.installEventFilter(self)
        self.folder_choice.currentIndexChanged.connect(lambda _: self.show_plan(unpack.FOLDER))
        self.choices.addStretch(1)
        layout.addLayout(self.choices)
        self.hint = QLabel()
        self.hint.setEnabled(False)
        layout.addWidget(self.hint)

        remember_row = QHBoxLayout()
        self.remember = QCheckBox("Do this every time for")
        self.group_choice = QComboBox()
        self.site_choice = QComboBox()
        self.from_label = QLabel("from")
        for widget in (self.remember, self.group_choice, self.from_label, self.site_choice):
            remember_row.addWidget(widget)
        remember_row.addStretch(1)
        self.remember_part = QWidget()
        self.remember_part.setLayout(remember_row)
        remember_row.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.remember_part)

        done_row = QHBoxLayout()
        self.undo_button = QPushButton("Undo")
        self.undo_button.clicked.connect(lambda: self.row_id is not None and self.undo.emit(self.row_id))
        self.ok_button = QPushButton("OK")
        self.ok_button.clicked.connect(lambda: self.row_id is not None and self.seen.emit(self.row_id))
        done_row.addWidget(self.undo_button)
        done_row.addWidget(self.ok_button)
        done_row.addStretch(1)
        self.done_part = QWidget()
        self.done_part.setLayout(done_row)
        done_row.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.done_part)
        self.buttons: List[QPushButton] = []
        self.hide()

    # ---- what it shows ---------------------------------------------------------------------------------------

    def show_waiting(self, row: Row, plan: Callable[[str, str], List[unpack.Move]], base_folder: str,
                     folders: Sequence[str], together: str, groups: Sequence[str], group: str,
                     site_groups: Sequence[str], site_group: str) -> None:
        self.row_id, self.plan, self.base_folder = row.id, plan, base_folder
        self.key = (row.id, "waiting")
        info = row.unpack
        self.title.setText(f"{row.filename}: {files(info.get('count', 0))} inside. What now?")
        self._fill(self.folder_choice, [(name, name) for name in folders], together)
        self._fill(self.group_choice, [(name, name) for name in groups], group)
        self._fill(self.site_choice, [("any site", "")] + [(name, name) for name in site_groups], site_group)
        suggestion = info.get("suggest") or unpack.KEEP
        order = [suggestion] + [m for m in (unpack.FOLDER, unpack.SORT, unpack.KEEP) if m != suggestion]
        while self.choices.count() > 1:                      # the stretch stays last
            self.choices.takeAt(0)
        for position, mode in enumerate(order):
            self.choices.insertWidget(position, self._parts[mode])
            button = self._buttons[mode]
            button.setDefault(mode == suggestion)
            button.setAutoDefault(mode == suggestion)
            button.setProperty("suggested", mode == suggestion)      # filled, as in the mockup (style.py)
            button.style().unpolish(button)
            button.style().polish(button)
        self.buttons = [self._buttons[m] for m in order]
        self.suggestion = suggestion
        self.hint.setText(WHY[suggestion] + " Hover a button to see where the files would go.")
        self.remember.setChecked(False)
        for part in (self.preview, self.hint, self.remember_part, *self._parts.values()):
            part.show()
        self.done_part.hide()
        self.show_plan(suggestion if suggestion != unpack.KEEP else unpack.SORT)
        self.show()

    def show_done(self, row: Row) -> None:
        self.row_id, self.key = row.id, (row.id, "done")
        info = row.unpack
        how = "as a folder" if info.get("mode") == unpack.FOLDER else "sorted by your rules"
        self.title.setText(f"{row.filename}: its rule unpacked {files(info.get('count', 0))}, {how}.")
        for part in (self.preview, self.hint, self.remember_part, *self._parts.values()):
            part.hide()
        self.done_part.show()
        self.show()

    def clear(self) -> None:
        self.row_id = self.key = None
        self.hide()

    # ---- inside ----------------------------------------------------------------------------------------------

    @staticmethod
    def _fill(combo: QComboBox, items, chosen: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        for label, data in items:
            combo.addItem(label, data)
        combo.setCurrentIndex(max(combo.findData(chosen), 0))
        combo.blockSignals(False)

    def _shown(self, path: str) -> str:
        folder = os.path.dirname(path)
        try:
            relative = os.path.relpath(folder, self.base_folder) if self.base_folder else folder
        except ValueError:
            return folder
        return folder if relative.startswith("..") else ("Default" if relative == "." else relative)

    def show_plan(self, mode: str) -> None:
        """Where each file would go with this choice (shown for the suggestion, and while a button is hovered)."""
        if self.row_id is None:
            return
        if mode == unpack.KEEP:
            self.preview.setText("The archive stays as it is.")
            return
        try:
            moves = self.plan(mode, self.folder_choice.currentData() or "")
        except unpack.UnpackError as error:
            self.preview.setText(str(error))
            return
        names = [m.entry.replace(chr(92), "/").rsplit("/", 1)[-1] for m in moves[:PREVIEW_LINES]]
        width = max((len(n) for n in names), default=0)
        lines = [f"{n.ljust(width)}  →  {self._shown(m.target)}" for n, m in zip(names, moves)]
        if len(moves) > PREVIEW_LINES:
            lines.append(f"… and {files(len(moves) - PREVIEW_LINES)} more")
        self.preview.setText("\n".join(lines))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            for mode, button in self._buttons.items():
                if watched is button:
                    self.show_plan(mode)
        elif event.type() == QEvent.Type.Leave and watched in self._buttons.values():
            self.show_plan(self.suggestion if self.suggestion != unpack.KEEP else unpack.SORT)
        return super().eventFilter(watched, event)

    def _choose(self, mode: str) -> None:
        if self.row_id is None:
            return
        remember = (self.group_choice.currentData() or "", self.site_choice.currentData() or "") \
            if self.remember.isChecked() else None
        row_id = self.row_id
        self.clear()
        self.chosen.emit(row_id, mode, self.folder_choice.currentData() or "", remember)
