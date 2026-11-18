"""The queue as a table: one row per download, sortable by every column, filtered by the chips."""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from accipio.core import handover
from accipio.core.store import ACTIVE, COMPLETE, ERROR, PAUSED, PROBING, SENT, WAITING, Row
from accipio.gui import style

COLUMNS = ["Name", "Size", "Progress", "Speed", "Time left", "Folder", "Rule", "Status"]
NAME, SIZE, PROGRESS, SPEED, ETA, FOLDER, RULE, STATUS = range(len(COLUMNS))
SORT_ROLE = Qt.ItemDataRole.UserRole + 1
# How a new installation shows the columns (answer 114A): Status second, and widths that let all eight fit in the
# 1000 px the window starts at (the last one, Rule, takes what is left). The numbers above are the model's own and
# never change, so a header state saved in window.ini before this still fits its columns.
FIRST_ORDER = (NAME, STATUS, PROGRESS, SIZE, SPEED, ETA, FOLDER, RULE)
FIRST_WIDTHS = {NAME: 220, STATUS: 150, PROGRESS: 125, SIZE: 86, SPEED: 80, ETA: 70, FOLDER: 136, RULE: 100}
RIGHT_ALIGNED = (SIZE, SPEED, ETA)

# the filter chips: name -> the states it shows
FILTERS = {
    "All": None,
    "Active": (PROBING, ACTIVE, PAUSED, WAITING, SENT),
    "Done": (COMPLETE,),
    "Failed": (ERROR,),
}
# WAITING and SENT name their place when the row knows it ("Waiting for Collector": handover.status_text)
STATE_TEXT = {PROBING: "Checking link", ACTIVE: "Downloading", PAUSED: "Paused", COMPLETE: "Done", ERROR: "Failed",
              WAITING: "Waiting for the other Accipio", SENT: "Sent to the other Accipio"}


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


def files(n: int) -> str:
    return f"{n} file{'' if n == 1 else 's'}"


def archive_status(info: dict) -> str:
    """The Status column for a finished archive (the unpack trial)."""
    state = info.get("state")
    if state == "waiting":
        return f"{files(info.get('count', 0))} inside: what now?"
    if state == "done":
        how = "as a folder" if info.get("mode") == "folder" else "sorted"
        return f"Unpacked {files(info.get('count', 0))}, {how}"
    if state == "failed":
        return info.get("message") or "Could not look inside"
    return info.get("message") or STATE_TEXT[COMPLETE]


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
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            return self.alignment(section)                  # a header stands as its column does (card 17)
        return None

    @staticmethod
    def alignment(column: int) -> int:
        side = Qt.AlignmentFlag.AlignRight if column in RIGHT_ALIGNED else Qt.AlignmentFlag.AlignLeft
        return int(side | Qt.AlignmentFlag.AlignVCenter)

    @staticmethod
    def rule_shown(row: Row) -> str:
        """The Rule cell: the names of the rules that placed the download, kept with the row. A row from before
        the names were kept shows the numbers it has (card 18)."""
        return row.rule_names or row.rules

    def _speed(self, row: Row) -> int:
        return self.speeds.get(row.id, 0) if row.state == ACTIVE else 0

    def _eta(self, row: Row) -> Optional[float]:
        speed = self._speed(row)
        return (row.total - row.done) / speed if speed and row.total > row.done else None

    def _folder(self, row: Row) -> str:
        """The folder, shown under the base folder when it is inside it (Archives\\2026-10)."""
        if not row.folder:
            return str(row.remote.get("place") or "")       # the other Accipio it went to, by its place's name
        try:
            relative = os.path.relpath(row.folder, self.base_folder) if self.base_folder else row.folder
        except ValueError:                                  # another drive
            return row.folder
        return row.folder if relative.startswith("..") else ("" if relative == "." else relative)

    def _folder_shown(self, row: Row) -> str:
        """The Folder cell: the folder, and after it the Accipio the download came from ("Comics ← Laptop", 107A)."""
        folder, sender = self._folder(row), handover.came_from(row)
        return f"{folder} ← {sender}".strip() if sender else folder

    @staticmethod
    def elsewhere(row: Row) -> bool:
        """Whether another Accipio is involved: the row was sent away, or it came in from one. Such rows are
        tinted (105B, 107A); the words in Folder and Status stay, so the colour is never the only sign."""
        return bool(handover.sent_to(row) or handover.came_from(row))

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
                return self._folder_shown(row)
            if column == RULE:
                return self.rule_shown(row)
            if column == STATUS:
                if row.state == COMPLETE and row.unpack:
                    return archive_status(row.unpack)
                elsewhere = handover.status_text(row)      # waiting for, sent to or finished on another Accipio
                if elsewhere:
                    return elsewhere
                return row.error if row.state == ERROR and row.error else STATE_TEXT.get(row.state, row.state)
        if role == SORT_ROLE:
            return {NAME: (row.filename or row.url).lower(), SIZE: row.total or row.size or 0,
                    PROGRESS: self.fraction(row), SPEED: self._speed(row), ETA: self._eta(row) or 0,
                    FOLDER: self._folder(row).lower(), RULE: self.rule_shown(row).lower(), STATUS: row.state}[column]
        if role == Qt.ItemDataRole.BackgroundRole and self.elsewhere(row):
            return QBrush(style.link_tint(QApplication.palette()))
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [row.url, f"Why here: {row.why}" if row.why else ""]
            if column == RULE and row.rules:
                lines.insert(1, f"Rule {row.rules} in the list when this download was added")
            if row.state == ERROR and row.error:
                lines.append(f"Error: {row.error}")
            return "\n".join(line for line in lines if line)
        if role == Qt.ItemDataRole.TextAlignmentRole and column in RIGHT_ALIGNED:
            return self.alignment(column)
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


DONE_GREEN = QColor("#3B8C4A")         # readable on both the light and the dark palette
FAILED_RED = QColor("#C0392B")
BAR_HEIGHT = 12.0
LABEL_WIDTH = 46


class ProgressDelegate(style.TintDelegate):
    """The Progress column as a rounded bar with the percentage beside it: the accent colour while running (and
    only then: a paused bar is grey, card 14), green when done, red when failed. A download for another Accipio (106B): filled in the link colour when it
    finished there (green always means "the file is on this PC"), a hollow bar outlined in the link colour while
    it is sent and not finished, and a hollow bar outlined in red when it failed there (nothing was downloaded
    here, so there is no part to fill); empty while it waits."""

    @staticmethod
    def outline(row: Row, palette: QPalette):
        """The colour of a hollow bar's outline, or None for an ordinary bar."""
        if not handover.sent_to(row):
            return None
        if row.state == SENT:
            return style.link_bar(palette)
        return FAILED_RED if row.state == ERROR and not row.done else None

    @staticmethod
    def fill(row: Row, palette: QPalette) -> QColor:
        if row.state == COMPLETE:
            return style.link_bar(palette) if handover.sent_to(row) else DONE_GREEN
        if row.state == PAUSED:
            return style.paused_bar(palette)
        return FAILED_RED if row.state == ERROR else palette.color(QPalette.ColorRole.Highlight)

    def paint(self, painter, option, index):
        source = index.model().mapToSource(index) if isinstance(index.model(), QSortFilterProxyModel) else index
        model = source.model()
        row = model.row_at(source)
        if row is None:
            return super().paint(painter, option, index)
        cell_option = QStyleOptionViewItem(option)          # the row's background and selection, without text
        self.initStyleOption(cell_option, index)
        cell_option.text = ""
        self.paint_tint(painter, option, index)             # the wash of a row that belongs to another Accipio
        widget_style = option.widget.style() if option.widget else QApplication.style()
        widget_style.drawControl(QStyle.ControlElement.CE_ItemViewItem, cell_option, painter, option.widget)
        pal = option.palette
        cell = QRectF(option.rect.adjusted(6, 0, -6, 0))
        width = max(cell.width() - LABEL_WIDTH, BAR_HEIGHT)
        track = QRectF(cell.x(), cell.center().y() - BAR_HEIGHT / 2, width, BAR_HEIGHT)
        radius = BAR_HEIGHT / 2
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = self.outline(row, pal)
        if outline is not None:
            painter.setPen(QPen(outline, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(0.75, 0.75, -0.75, -0.75), radius, radius)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            empty = QColor(pal.color(QPalette.ColorRole.Mid))
            empty.setAlpha(80)
            painter.setBrush(empty)
            painter.drawRoundedRect(track, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        fraction = model.fraction(row)
        if fraction > 0 and outline is None:
            painter.setBrush(self.fill(row, pal))
            painter.drawRoundedRect(QRectF(track.x(), track.y(), max(BAR_HEIGHT, width * fraction), BAR_HEIGHT),
                                    radius, radius)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(pal.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text))
        painter.drawText(QRectF(track.right() + 6, option.rect.y(), LABEL_WIDTH, option.rect.height()),
                         int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), index.data() or "")
        painter.restore()
