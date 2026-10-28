"""The queue as a table: one row per download, sortable by every column, filtered by the chips."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionProgressBar

from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, PROBING, Row

COLUMNS = ["Name", "Size", "Progress", "Speed", "Time left", "Folder", "Rule", "Status"]
NAME, SIZE, PROGRESS, SPEED, ETA, FOLDER, RULE, STATUS = range(len(COLUMNS))
SORT_ROLE = Qt.ItemDataRole.UserRole + 1

# the filter chips: name -> the states it shows
FILTERS = {
    "All": None,
    "Active": (PROBING, ACTIVE, PAUSED),
    "Done": (COMPLETE,),
    "Failed": (ERROR,),
}
STATE_TEXT = {PROBING: "Checking link", ACTIVE: "Downloading", PAUSED: "Paused", COMPLETE: "Done", ERROR: "Failed"}


def human_size(n: Optional[int]) -> str:
    if not n:
        return ""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def human_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


class QueueModel(QAbstractTableModel):
    def __init__(self, base_folder: str = "", parent=None):
        super().__init__(parent)
        self.rows: List[Row] = []
        self.speeds: Dict[int, int] = {}
        self.base_folder = base_folder

    # ---- updating --------------------------------------------------------------------------------------------

    def set_rows(self, rows: List[Row], speeds: Dict[int, int]) -> None:
        """Replace the rows. The same downloads in the same order only repaint, so selection and scroll stay."""
        self.speeds = dict(speeds)
        if [r.id for r in rows] == [r.id for r in self.rows]:
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(COLUMNS) - 1))
            return
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def row_at(self, index: QModelIndex) -> Optional[Row]:
        return self.rows[index.row()] if index.isValid() and index.row() < len(self.rows) else None

    def counts(self) -> Dict[str, int]:
        return {name: sum(1 for r in self.rows if states is None or r.state in states) for name, states in FILTERS.items()}

    def total_speed(self) -> int:
        return sum(self.speeds.get(r.id, 0) for r in self.rows if r.state == ACTIVE)

    # ---- Qt --------------------------------------------------------------------------------------------------

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def _speed(self, row: Row) -> int:
        return self.speeds.get(row.id, 0) if row.state == ACTIVE else 0

    def _eta(self, row: Row) -> Optional[float]:
        speed = self._speed(row)
        return (row.total - row.done) / speed if speed and row.total > row.done else None

    def _folder(self, row: Row) -> str:
        """The folder, shown under the base folder when it is inside it (Archives\\2026-10)."""
        if not row.folder:
            return ""
        try:
            relative = os.path.relpath(row.folder, self.base_folder) if self.base_folder else row.folder
        except ValueError:                                  # another drive
            return row.folder
        return row.folder if relative.startswith("..") else ("" if relative == "." else relative)

    def fraction(self, row: Row) -> float:
        if row.state == COMPLETE:
            return 1.0
        return row.done / row.total if row.total else 0.0

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        row = self.row_at(index)
        if row is None:
            return None
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == NAME:
                return row.filename or row.url
            if column == SIZE:
                return human_size(row.total or row.size)
            if column == PROGRESS:
                return f"{self.fraction(row) * 100:.0f} %"
            if column == SPEED:
                speed = self._speed(row)
                return f"{human_size(speed)}/s" if speed else ""
            if column == ETA:
                return human_time(self._eta(row))
            if column == FOLDER:
                return self._folder(row)
            if column == RULE:
                return row.rules
            if column == STATUS:
                return row.error if row.state == ERROR and row.error else STATE_TEXT.get(row.state, row.state)
        if role == SORT_ROLE:
            return {NAME: (row.filename or row.url).lower(), SIZE: row.total or row.size or 0,
                    PROGRESS: self.fraction(row), SPEED: self._speed(row), ETA: self._eta(row) or 0,
                    FOLDER: self._folder(row).lower(), RULE: row.rules, STATUS: row.state}[column]
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [row.url, f"Why here: {row.why}" if row.why else ""]
            if row.state == ERROR and row.error:
                lines.append(f"Error: {row.error}")
            return "\n".join(line for line in lines if line)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in (SIZE, SPEED, ETA):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None


class QueueFilter(QSortFilterProxyModel):
    """Sorts by the model's SORT_ROLE and shows only the rows of the chosen chip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.states = None
        self.setSortRole(SORT_ROLE)

    def set_filter(self, name: str) -> None:
        self.states = FILTERS[name]
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if self.states is None:
            return True
        row = self.sourceModel().rows[source_row]
        return row.state in self.states


class ProgressDelegate(QStyledItemDelegate):
    """The Progress column as a real progress bar."""

    def paint(self, painter, option, index):
        source = index.model().mapToSource(index) if isinstance(index.model(), QSortFilterProxyModel) else index
        model = source.model()
        row = model.row_at(source)
        if row is None:
            return super().paint(painter, option, index)
        bar = QStyleOptionProgressBar()
        bar.rect = option.rect.adjusted(2, 3, -2, -3)
        bar.minimum, bar.maximum = 0, 1000
        bar.progress = int(model.fraction(row) * 1000)
        bar.text = index.data()
        bar.textVisible = True
        bar.state = option.state
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ProgressBar, bar, painter, option.widget)
