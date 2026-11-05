"""The Add box (mockup A): paste links, see for each one its name, folder, rule and whether the server answers,
then add the ticked ones.

Each link is checked on the window's worker thread while the box is open (the same ``Queue.preview`` the queue
itself uses), so the folders shown are the real ones; the worker takes one at a time, so a long series never
hammers a site. A link that fails the check is shown with its error and left out. Double-click a folder to send
that one link elsewhere; "Put all in" sends every link to one folder. A folder chosen here is kept even when the
rules would move the file later.

A link pattern (``saga-[001-150].cbz``, see ``core.ranges``; mockup 30B, 2026-11-05) is one folded row with one
tick, one folder and a summary ("148 ready · 2 failed"); click its name to unfold it and untick single files.
A pattern that makes more than ``ranges.LIMIT`` links is only expanded after asking.
"""
from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from accipio.core import ranges
from accipio.core.queue import Preview
from accipio.gui import style
from accipio.gui.queue_model import human_size

COLUMNS = ["", "Name", "Folder", "Rule", "Status"]
CHECK, NAME, FOLDER, RULE, STATUS = range(len(COLUMNS))
ERROR_COLOUR = QColor("#c0392b")


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


@dataclass
class Group:
    """What one pasted link became: itself, or every link its pattern stands for."""
    text: str
    urls: List[str] = field(default_factory=list)
    pattern: bool = False
    expanded: bool = False
    error: str = ""                      # the pattern could not be (or was not) expanded


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
        self.groups: List[Group] = []
        self.rows: List[Tuple[Group, Optional[str]]] = []      # one per table row: (group, url) or (group, None)
        self._many_answers: Dict[str, bool] = {}

        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Paste one or more links, one per line. A series: saga-[001-150].cbz, "
                                     "page[1-20:2].jpg, [a-z].pdf, {intro,main}.mp3")
        self.text.setFixedHeight(70)
        self.text.setPlainText(text)
        self.text.textChanged.connect(lambda: self._debounce.start())
        layout.addWidget(self.text)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(style.ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(False)                 # rows follow the pasted order
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        for column, width in ((CHECK, 28), (NAME, 260), (FOLDER, 280), (RULE, 50)):
            header.resizeSection(column, width)
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table, 1)
        hint = QLabel("Double-click a folder to put that link somewhere else. Click a series (▸) to see its links.")
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

    def confirm_many(self, link: str, total: int) -> bool:
        """Asked once per pattern that makes more than ranges.LIMIT links (tests replace it)."""
        answer = QMessageBox.question(self, "A long series", f"This pattern makes {total:,} links:\n\n{link}\n\n"
                                      "Check and list them all?")
        return answer == QMessageBox.StandardButton.Yes

    def _group_for(self, link: str, before: Dict[str, Group]) -> Group:
        if link in before:
            return before[link]
        if not ranges.has_pattern(link):
            return Group(link, [link])
        group = Group(link, pattern=True)
        total = ranges.count(link)
        if total > ranges.LIMIT:
            if link not in self._many_answers:
                self._many_answers[link] = self.confirm_many(link, total)
            if not self._many_answers[link]:
                group.error = f"{total:,} links: not expanded"
                return group
        try:
            group.urls = list(dict.fromkeys(ranges.expand(link)))
        except ranges.PatternError as error:
            group.error = str(error)
        return group

    def read_links(self) -> None:
        """Take the links from the text; check the new ones, forget the removed ones."""
        before = {g.text: g for g in self.groups}
        self.groups = [self._group_for(link, before) for link in self.find_links(self.text.toPlainText())]
        for group in self.groups:
            for url in group.urls:
                if url not in self.entries:
                    entry = Entry(url)
                    entry.future = self.submit(self.preview_fn, url)
                    self.entries[url] = entry
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
        return folder if relative.startswith("..") else ("Default" if relative == "." else relative)

    def _status(self, entry: Entry) -> str:
        if entry.state == "ready":
            bits = {"video": "video", "playlist": f"{entry.preview.count} videos"}.get(entry.preview.kind, "")
            return "Ready" + "".join(f" · {b}" for b in (human_size(entry.preview.size), bits) if b)
        return entry.error if entry.state == "error" else "Checking…"

    def _url_cells(self, url: str, child: bool) -> List[QTableWidgetItem]:
        entry = self.entries[url]
        folder, by_hand = self._folder_of(entry)
        check = QTableWidgetItem()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if entry.state != "error":
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        check.setFlags(flags)
        check.setCheckState(Qt.CheckState.Checked if entry.checked else Qt.CheckState.Unchecked)
        name = entry.preview.filename if entry.preview else (url.rsplit("/", 1)[-1] if child else url)
        status = self._status(entry)
        cells = [check, QTableWidgetItem(("      " if child else "") + name),
                 QTableWidgetItem(self._shown_folder(folder) if folder else ""),
                 QTableWidgetItem("" if by_hand or not entry.preview else entry.preview.placement.rule_numbers),
                 QTableWidgetItem(status)]
        cells[FOLDER].setToolTip(folder + ("  (chosen by hand)" if by_hand else ""))
        cells[STATUS].setToolTip(status)
        cells[NAME].setToolTip(url + (f"\n{entry.preview.placement.why()}" if entry.preview else ""))
        if entry.state == "error":
            cells[STATUS].setForeground(ERROR_COLOUR)
            cells[NAME].setForeground(QColor("gray"))
        return cells

    def _group_cells(self, group: Group) -> List[QTableWidgetItem]:
        entries = [self.entries[u] for u in group.urls]
        usable = [e for e in entries if e.state != "error"]
        check = QTableWidgetItem()
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if usable:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        check.setFlags(flags)
        ticked = sum(e.checked for e in usable)
        check.setCheckState(Qt.CheckState.Checked if usable and ticked == len(usable)
                            else Qt.CheckState.PartiallyChecked if ticked else Qt.CheckState.Unchecked)
        folders = {self._folder_of(e) for e in entries if self._folder_of(e)[0]}
        rules = {e.preview.placement.rule_numbers for e in entries if e.preview and not self._folder_of(e)[1]}
        folder = self._shown_folder(next(iter(folders))[0]) if len(folders) == 1 else ("(several)" if folders else "")
        ready = sum(e.state == "ready" for e in entries)
        failed = sum(e.state == "error" for e in entries)
        checking = len(entries) - ready - failed
        status = group.error or " · ".join(part for part in (f"{ready} ready" if ready else "",
                                                              f"{failed} failed" if failed else "",
                                                              f"checking {checking} of {len(entries)}…" if checking else "")
                                           if part)
        tail = group.text.rsplit("/", 1)[-1] or group.text
        cells = [check, QTableWidgetItem(f"{'▾' if group.expanded else '▸'} {tail}   ({len(entries)} links)"),
                 QTableWidgetItem(folder), QTableWidgetItem(next(iter(rules)) if len(rules) == 1 else ""),
                 QTableWidgetItem(status)]
        cells[NAME].setToolTip(group.text + "\nClick to show or hide its links")
        bold = cells[NAME].font()
        bold.setBold(True)
        cells[NAME].setFont(bold)
        if group.error:
            cells[STATUS].setForeground(ERROR_COLOUR)
        return cells

    def _refresh_table(self) -> None:
        self._filling = True
        self.rows = []
        for group in self.groups:
            if not group.pattern:
                self.rows += [(group, url) for url in group.urls]
                continue
            self.rows.append((group, None))
            if group.expanded:
                self.rows += [(group, url) for url in group.urls]
        self.table.setRowCount(len(self.rows))
        for position, (group, url) in enumerate(self.rows):
            cells = self._group_cells(group) if url is None else self._url_cells(url, child=group.pattern)
            for column, item in enumerate(cells):
                if column != CHECK:
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(position, column, item)
        self._filling = False
        total = len(self.chosen())
        self.add_button.setText(f"Add {total}" if total else "Add")
        self.add_button.setEnabled(total > 0)

    def _urls_of_row(self, row: int) -> List[str]:
        group, url = self.rows[row]
        return group.urls if url is None else [url]

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() != CHECK:
            return
        on = item.checkState() != Qt.CheckState.Unchecked
        for url in self._urls_of_row(item.row()):
            entry = self.entries[url]
            if entry.state != "error":
                entry.checked = on
        self._refresh_table()

    def _clicked(self, row: int, column: int) -> None:
        group, url = self.rows[row]
        if url is None and column == NAME:
            group.expanded = not group.expanded
            self._refresh_table()

    def _double_clicked(self, row: int, column: int) -> None:
        if column != FOLDER:
            return
        first = self.entries.get(self._urls_of_row(row)[0]) if self._urls_of_row(row) else None
        current = (self._folder_of(first)[0] if first else "") or self.base_folder
        chosen = QFileDialog.getExistingDirectory(self, "Folder for these links", current)
        if chosen:
            self.set_folder(row, chosen)

    def set_folder(self, row: int, folder: str) -> None:
        """Send the link on this row (or every link of the series on it) to ``folder``."""
        for url in self._urls_of_row(row):
            self.entries[url].folder = os.path.normpath(folder)
        self._refresh_table()

    def _browse_all(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Put all links in", self.all_in.text() or self.base_folder)
        if chosen:
            self.all_in.setText(os.path.normpath(chosen))

    # ---- the answer ------------------------------------------------------------------------------------------

    def chosen(self) -> List[Tuple[str, str]]:
        """(link, folder chosen by hand or "") for every ticked link that did not fail its check, in order."""
        out, seen = [], set()
        for group in self.groups:
            for url in group.urls:
                entry = self.entries[url]
                if url in seen or not entry.checked or entry.state == "error":
                    continue
                seen.add(url)
                folder, by_hand = self._folder_of(entry)
                out.append((url, folder if by_hand else ""))
        return out

    def done(self, result: int) -> None:
        self._poll.stop()
        self._debounce.stop()
        super().done(result)
