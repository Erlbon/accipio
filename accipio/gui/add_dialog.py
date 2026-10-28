"""The Add box (mockup A): paste links, see for each one its name, folder, rule and whether the server answers,
then add the ticked ones.

Each link is checked on the window's worker thread while the box is open (the same ``Queue.preview`` the queue
itself uses), so the folders shown are the real ones. A link that fails the check is shown with its error and
left out. Double-click a folder to send that one link elsewhere; "Put all in" sends every link to one folder.
A folder chosen here is kept even when the rules would move the file later.
"""
from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from accipio.core.queue import Preview
from accipio.gui.queue_model import human_size

COLUMNS = ["", "Name", "Folder", "Rule", "Status"]
CHECK, NAME, FOLDER, RULE, STATUS = range(len(COLUMNS))


@dataclass
class Entry:
    url: str
    future: Optional[concurrent.futures.Future] = None
    preview: Optional[Preview] = None
    error: str = ""
    folder: str = ""                     # chosen by hand for this link ("" = the rules')
    checked: bool = True

    @property
    def state(self) -> str:
        return "error" if self.error else ("ready" if self.preview else "checking")


class AddDialog(QDialog):
    def __init__(self, text: str, base_folder: str, submit: Callable[..., concurrent.futures.Future],
                 preview: Callable[[str], Preview], find_links: Callable[[str], List[str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add links")
        self.resize(900, 460)
        self.base_folder = base_folder
        self.submit = submit
        self.preview_fn = preview
        self.find_links = find_links
        self.entries: Dict[str, Entry] = {}
        self.order: List[str] = []

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Paste one or more links, one per line")
        self.text.setFixedHeight(70)
        self.text.setPlainText(text)
        self.text.textChanged.connect(lambda: self._debounce.start())
        layout.addWidget(self.text)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(False)                 # rows follow the pasted order
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in ((CHECK, 28), (NAME, 260), (FOLDER, 280), (RULE, 50)):
            header.resizeSection(column, width)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, 1)
        hint = QLabel("Double-click a folder to put that link somewhere else.")
        hint.setEnabled(False)
        layout.addWidget(hint)

        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Put all in:"))
        self.all_in = QLineEdit()
        self.all_in.setPlaceholderText("(where the rules say)")
        self.all_in.setClearButtonEnabled(True)
        self.all_in.textChanged.connect(lambda _: self._refresh_table())
        bottom.addWidget(self.all_in, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_all)
        bottom.addWidget(browse)
        bottom.addSpacing(16)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton("Add")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self.accept)
        bottom.addWidget(cancel)
        bottom.addWidget(self.add_button)
        layout.addLayout(bottom)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self.read_links)
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self.collect)
        self._poll.start()
        self._filling = False
        self.read_links()

    # ---- links in, checks out --------------------------------------------------------------------------------

    def read_links(self) -> None:
        """Take the links from the text; check the new ones, forget the removed ones."""
        links = self.find_links(self.text.toPlainText())
        for link in links:
            if link not in self.entries:
                entry = Entry(link)
                entry.future = self.submit(self.preview_fn, link)
                self.entries[link] = entry
        self.order = links
        self._refresh_table()

    def collect(self) -> None:
        """Pick up finished checks (the poll timer; tests call it directly)."""
        changed = False
        for entry in self.entries.values():
            if entry.future is not None and entry.future.done():
                error = entry.future.exception()
                if error is None:
                    entry.preview = entry.future.result()
                else:
                    entry.error = str(error) or type(error).__name__
                    entry.checked = False
                entry.future = None
                changed = True
        if changed:
            self._refresh_table()

    # ---- the table -------------------------------------------------------------------------------------------

    def _folder_of(self, entry: Entry) -> Tuple[str, bool]:
        """The folder this link goes to, and whether it was chosen by hand (here or by Put all in)."""
        if entry.folder:
            return entry.folder, True
        if self.all_in.text().strip():
            return os.path.normpath(self.all_in.text().strip()), True
        if entry.preview:
            return entry.preview.placement.folder, False
        return "", False

    def _shown_folder(self, folder: str) -> str:
        try:
            relative = os.path.relpath(folder, self.base_folder)
        except ValueError:
            return folder
        return folder if relative.startswith("..") else ("(base folder)" if relative == "." else relative)

    def _refresh_table(self) -> None:
        self._filling = True
        self.table.setRowCount(len(self.order))
        for position, url in enumerate(self.order):
            entry = self.entries[url]
            folder, by_hand = self._folder_of(entry)
            check = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if entry.state != "error":
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            check.setFlags(flags)
            check.setCheckState(Qt.CheckState.Checked if entry.checked else Qt.CheckState.Unchecked)
            name = entry.preview.filename if entry.preview else url
            if entry.state == "ready":
                bits = {"video": "video", "playlist": f"{entry.preview.count} videos"}.get(entry.preview.kind, "")
                size = human_size(entry.preview.size)
                status = "Ready" + "".join(f" · {b}" for b in (size, bits) if b)
            elif entry.state == "error":
                status = entry.error
            else:
                status = "Checking…"
            cells = [check, QTableWidgetItem(name), QTableWidgetItem(self._shown_folder(folder) if folder else ""),
                     QTableWidgetItem("" if by_hand or not entry.preview else entry.preview.placement.rule_numbers),
                     QTableWidgetItem(status)]
            cells[FOLDER].setToolTip(folder + ("  (chosen by hand)" if by_hand else ""))
            cells[STATUS].setToolTip(status)
            if entry.preview:
                cells[NAME].setToolTip(f"{url}\n{entry.preview.placement.why()}")
            if entry.state == "error":
                cells[STATUS].setForeground(QColor("#c0392b"))
                cells[NAME].setForeground(QColor("gray"))
            for column, item in enumerate(cells):
                if column != CHECK:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(position, column, item)
        self._filling = False
        count = len(self.chosen())
        self.add_button.setText(f"Add {count}" if count else "Add")
        self.add_button.setEnabled(count > 0)

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != CHECK:
            return
        entry = self.entries[self.order[item.row()]]
        entry.checked = item.checkState() == Qt.CheckState.Checked
        self._refresh_table()

    def _double_clicked(self, row: int, column: int) -> None:
        if column != FOLDER:
            return
        entry = self.entries[self.order[row]]
        current = self._folder_of(entry)[0] or self.base_folder
        chosen = QFileDialog.getExistingDirectory(self, "Folder for this link", current)
        if chosen:
            self.set_folder(row, chosen)

    def set_folder(self, row: int, folder: str) -> None:
        self.entries[self.order[row]].folder = os.path.normpath(folder)
        self._refresh_table()

    def _browse_all(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Put all links in", self.all_in.text() or self.base_folder)
        if chosen:
            self.all_in.setText(os.path.normpath(chosen))

    # ---- the answer ------------------------------------------------------------------------------------------

    def chosen(self) -> List[Tuple[str, str]]:
        """(link, folder chosen by hand or "") for every ticked link that did not fail its check."""
        out = []
        for url in self.order:
            entry = self.entries[url]
            if entry.checked and entry.state != "error":
                folder, by_hand = self._folder_of(entry)
                out.append((url, folder if by_hand else ""))
        return out

    def done(self, result: int) -> None:
        self._poll.stop()
        self._debounce.stop()
        super().done(result)
