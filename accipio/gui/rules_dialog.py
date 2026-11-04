"""The rules editor (mockup A): the ordered rule list on the left, the selected rule as a form on the right,
and a test box at the bottom that shows, as you type, where a link would go.

Drag a rule up or down to change its priority. Each list row shows → (continue) or ■ (stop). The time
condition is edited with weekday buttons and an hour range and read back as a sentence; the raw cron lines
are in the Advanced field for what the pickers cannot say.
"""
from __future__ import annotations

import copy
import datetime
import os
from typing import List, Optional

from PyQt6.QtCore import QTime, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QRadioButton,
                             QScrollArea, QSplitter, QStackedWidget, QTabWidget, QTimeEdit, QToolButton, QVBoxLayout,
                             QWidget)

from accipio.core import cron, probe, rules
from accipio.core import settings as settings_mod
from accipio.core.rules import Condition, Download, Rule
from accipio.gui.library_tabs import FileGroupsTab, FoldersTab, SiteGroupsTab

KIND_LABELS = [("type", "In file group"), ("sites", "From site group"), ("domain", "Site"), ("name", "Name"),
               ("size", "Size"), ("time", "Time"), ("source", "Source"), ("video", "Video field")]
KEEP_FOLDER = ""            # the folder combo's first entry: the folder so far (base folder, or an earlier rule's)


def group_summary(group: rules.Group, limit: int = 6) -> str:
    """What a file group holds, short: "mp4, mkv, webm, avi, mov, wmv …"."""
    entries = group.extensions + group.names
    shown = entries if len(entries) <= limit else entries[:limit] + ["…"]
    return ", ".join(shown) or "(empty)"
PICKER_DAYS = [(1, "Mo"), (2, "Tu"), (3, "We"), (4, "Th"), (5, "Fr"), (6, "Sa"), (0, "Su")]
MB = 1024 * 1024


def split_values(text: str) -> List[str]:
    return [v.strip() for v in text.split(",") if v.strip()]


def picker_state(lines: List[str]):
    """(weekdays, start, end) when the lines are exactly what the pickers would build, else None."""
    if not lines:
        return None
    try:
        parsed = [cron.parse(line) for line in lines]
    except cron.CronError:
        return None
    days = parsed[0].weekdays

    def spans(weekdays):
        covered = {h * 60 + m for line in parsed if line.weekdays == weekdays for h in line.hours for m in line.minutes}
        return [(a, b + 1) for a, b in cron._ranges(covered)]

    def as_time(minute):
        minute %= cron.MINUTES_PER_DAY
        return datetime.time(minute // 60, minute % 60)

    first = spans(days)
    if len(first) != 1:
        return None
    start, end = first[0]
    candidates = [(start, end)]
    following = spans(frozenset((d + 1) % 7 for d in days))
    if end == cron.MINUTES_PER_DAY and len(following) == 1 and following[0][0] == 0:
        candidates.insert(0, (start, following[0][1]))               # a window past midnight
    for a, b in candidates:
        try:
            if cron.window_lines(days, as_time(a), as_time(b)) == lines:
                return set(days), as_time(a), as_time(b)
        except cron.CronError:
            pass
    return None


class TimeEditor(QWidget):
    """Weekday buttons and an hour range on top of cron lines; Advanced shows the lines themselves."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.day_buttons = {}
        for number, label in PICKER_DAYS:
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            button.setMinimumWidth(34)
            # the theme's checked look is too faint to tell on from off
            button.setStyleSheet("QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); }")
            button.toggled.connect(self._from_pickers)
            self.day_buttons[number] = button
            row.addWidget(button)
        self.start = QTimeEdit()
        self.end = QTimeEdit()
        for edit in (self.start, self.end):
            edit.setDisplayFormat("HH:mm")
            edit.timeChanged.connect(self._from_pickers)
        row.addSpacing(8)
        row.addWidget(self.start)
        row.addWidget(QLabel("–"))
        row.addWidget(self.end)
        row.addStretch(1)
        layout.addLayout(row)
        self.sentence = QLabel()
        layout.addWidget(self.sentence)
        advanced = QHBoxLayout()
        advanced.addWidget(QLabel("Advanced (cron):"))
        self.advanced = QLineEdit()
        self.advanced.setPlaceholderText("* 8-15 * * 1-5   (several lines: separate with ;)")
        self.advanced.textEdited.connect(self._from_advanced)
        advanced.addWidget(self.advanced, 1)
        layout.addLayout(advanced)
        self._busy = False
        self.set_lines(cron.window_lines({1, 2, 3, 4, 5}, datetime.time(8), datetime.time(16)))

    def lines(self) -> List[str]:
        return [part.strip() for part in self.advanced.text().split(";") if part.strip()]

    def set_lines(self, lines: List[str]) -> None:
        self._busy = True
        self.advanced.setText("; ".join(lines))
        self._show_pickers(lines)
        self._busy = False
        self._update_sentence()

    def _show_pickers(self, lines: List[str]) -> None:
        state = picker_state(lines)
        custom = state is None
        for button in self.day_buttons.values():
            button.setEnabled(not custom)
        self.start.setEnabled(not custom)
        self.end.setEnabled(not custom)
        if state:
            days, start, end = state
            for number, button in self.day_buttons.items():
                button.setChecked(number in days)
            self.start.setTime(QTime(start.hour, start.minute))
            self.end.setTime(QTime(end.hour, end.minute))

    def _from_pickers(self, *_):
        if self._busy:
            return
        days = {n for n, b in self.day_buttons.items() if b.isChecked()}
        start, end = self.start.time(), self.end.time()
        try:
            lines = cron.window_lines(days, datetime.time(start.hour(), start.minute()), datetime.time(end.hour(), end.minute()))
        except cron.CronError:
            lines = []
        self._busy = True
        self.advanced.setText("; ".join(lines))
        self._busy = False
        self._update_sentence()
        self.changed.emit()

    def _from_advanced(self, *_):
        self._busy = True
        self._show_pickers(self.lines())
        self._busy = False
        self._update_sentence()
        self.changed.emit()

    def _update_sentence(self):
        lines = self.lines()
        problems = cron.problems(lines)
        if not lines:
            self.sentence.setText("Pick at least one weekday.")
        elif problems:
            self.sentence.setText(problems[0])
        else:
            custom = "" if picker_state(lines) else "  (set in Advanced)"
            self.sentence.setText(cron.describe(lines) + custom)


class FittingStack(QStackedWidget):
    """A stack as tall as the page it shows, not as its tallest page, so a one-line condition stays one line."""

    def sizeHint(self):
        return self.currentWidget().sizeHint() if self.currentWidget() else super().sizeHint()

    def minimumSizeHint(self):
        return self.currentWidget().minimumSizeHint() if self.currentWidget() else super().minimumSizeHint()


class ConditionRow(QFrame):
    """One condition: what kind, its values, and a remove button."""
    changed = pyqtSignal()
    removed = pyqtSignal(object)

    def __init__(self, condition: Optional[Condition] = None, parent=None, library_of=rules.default_library):
        super().__init__(parent)
        self.library_of = library_of
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self.kind = QComboBox()
        for key, label in KIND_LABELS:
            self.kind.addItem(label, key)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        layout.addWidget(self.kind, 0, Qt.AlignmentFlag.AlignTop)
        self.stack = FittingStack()
        self.stack.currentChanged.connect(lambda _: self.stack.updateGeometry())
        layout.addWidget(self.stack, 1, Qt.AlignmentFlag.AlignTop)
        remove = QToolButton()
        remove.setText("✕")
        remove.setToolTip("Remove this condition")
        remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(remove, 0, Qt.AlignmentFlag.AlignTop)
        self.pages = {}
        self._build_pages()
        self.set_condition(condition or Condition("type", []))

    def _line(self, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.textChanged.connect(self.changed)
        return edit

    def _combo(self, items) -> QComboBox:
        combo = QComboBox()
        for key, label in items:
            combo.addItem(label, key)
        combo.currentIndexChanged.connect(self.changed)
        return combo

    def _build_pages(self):
        def page(key, *widgets):
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.setAlignment(Qt.AlignmentFlag.AlignTop)
            for w in widgets:
                row.addWidget(w, 1 if isinstance(w, (QLineEdit, TimeEditor)) else 0)
            self.pages[key] = holder
            self.stack.addWidget(holder)

        self.domain_values = self._line("youtube.com, *.github.com")
        self.domain_where = self._combo([("either", "of the file or the page"), ("file", "of the file"), ("page", "of the page")])
        page("domain", self.domain_values, self.domain_where)

        self.type_values = self._line("Comics, Video, mkv")
        self.type_values.setToolTip("File groups by name (see File Groups), single extensions, or MIME types")
        self.type_menu = self._picker("File groups ▾", self.type_values, lambda: [
            (g.name, f"{g.name}  —  {group_summary(g)}") for g in self.library_of().groups])
        self.type_except = self._line("ts, m2ts")
        self.type_except.setMaximumWidth(140)
        self.type_except.setToolTip("Extensions of these groups to leave out of this rule only; the groups stay as they are")
        page("type", self.type_values, self.type_menu, QLabel("except"), self.type_except)

        self.site_values = self._line("Comic sites")
        self.site_menu = self._picker("Site groups ▾", self.site_values, lambda: [
            (g.name, f"{g.name}  —  {', '.join(g.sites[:4])}{' …' if len(g.sites) > 4 else ''}")
            for g in self.library_of().site_groups] or [("", "(no site groups yet: see the Site Groups tab)")])
        page("sites", self.site_values, self.site_menu)

        self.name_values = self._line("*S01E??*, *.iso")
        self.name_regex = QCheckBox("Regular expression")
        self.name_regex.toggled.connect(self.changed)
        page("name", self.name_values, self.name_regex)

        self.size_min = QDoubleSpinBox()
        self.size_max = QDoubleSpinBox()
        for spin, prefix in ((self.size_min, "at least "), (self.size_max, "at most ")):
            spin.setRange(0, 10_000_000)
            spin.setDecimals(1)
            spin.setSuffix(" MB")
            spin.setPrefix(prefix)
            spin.setSpecialValueText(prefix + "(any)")
            spin.valueChanged.connect(self.changed)
        page("size", self.size_min, self.size_max)

        self.time_editor = TimeEditor()
        self.time_editor.changed.connect(self.changed)
        page("time", self.time_editor)

        self.source = self._combo([("direct", "Direct download"), ("video", "Video site")])
        page("source", self.source)

        self.video_field = self._combo([(f, f.capitalize()) for f in rules.VIDEO_FIELDS])
        self.video_values = self._line("XYZ*")
        self.video_regex = QCheckBox("Regular expression")
        self.video_regex.toggled.connect(self.changed)
        page("video", self.video_field, self.video_values, self.video_regex)

    def _picker(self, text: str, field: QLineEdit, entries) -> QToolButton:
        """A drop-down that adds a name to ``field``; ``entries()`` gives (name, label) pairs, read each time it
        opens, so groups made in the other tabs show up at once."""
        button = QToolButton()
        button.setText(text)
        menu = QMenu(button)

        def fill():
            menu.clear()
            for name, label in entries():
                action = menu.addAction(label, lambda n=name: self._add_name(field, n))
                action.setEnabled(bool(name))
        menu.aboutToShow.connect(fill)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button

    @staticmethod
    def _add_name(field: QLineEdit, name: str):
        values = split_values(field.text())
        if name.lower() not in {v.lower() for v in values}:
            values.append(name)
        field.setText(", ".join(values))

    def _kind_changed(self, *_):
        self.stack.setCurrentWidget(self.pages[self.kind.currentData()])
        self.changed.emit()

    @staticmethod
    def _pick(combo: QComboBox, key: str):
        position = combo.findData(key)
        combo.setCurrentIndex(max(position, 0))

    def set_condition(self, c: Condition) -> None:
        self._pick(self.kind, c.kind if c.kind in dict(KIND_LABELS) else "type")
        self.stack.setCurrentWidget(self.pages[self.kind.currentData()])
        joined = ", ".join(c.values)
        if c.kind == "domain":
            self.domain_values.setText(joined)
            self._pick(self.domain_where, c.where or "either")
        elif c.kind == "type":
            self.type_values.setText(joined)
            self.type_except.setText(", ".join(c.exclude))
        elif c.kind == "sites":
            self.site_values.setText(joined)
        elif c.kind == "name":
            self.name_values.setText(c.values[0] if c.regex and c.values else joined)
            self.name_regex.setChecked(c.regex)
        elif c.kind == "size":
            self.size_min.setValue((c.min_size or 0) / MB)
            self.size_max.setValue((c.max_size or 0) / MB)
        elif c.kind == "time":
            self.time_editor.set_lines(c.values)
        elif c.kind == "source":
            self._pick(self.source, c.values[0] if c.values else "direct")
        elif c.kind == "video":
            self._pick(self.video_field, c.where or "channel")
            self.video_values.setText(c.values[0] if c.regex and c.values else joined)
            self.video_regex.setChecked(c.regex)

    def condition(self) -> Condition:
        kind = self.kind.currentData()
        if kind == "domain":
            where = self.domain_where.currentData()
            return Condition("domain", split_values(self.domain_values.text()), where="" if where == "either" else where)
        if kind == "type":
            return Condition("type", split_values(self.type_values.text()),
                             exclude=[e.lstrip(".").lower() for e in split_values(self.type_except.text())])
        if kind == "sites":
            return Condition("sites", split_values(self.site_values.text()))
        if kind == "name":
            regex = self.name_regex.isChecked()
            text = self.name_values.text().strip()
            return Condition("name", ([text] if text else []) if regex else split_values(text), regex=regex)
        if kind == "size":
            low, high = self.size_min.value(), self.size_max.value()
            return Condition("size", min_size=int(low * MB) if low else None, max_size=int(high * MB) if high else None)
        if kind == "time":
            return Condition("time", self.time_editor.lines())
        if kind == "source":
            return Condition("source", [self.source.currentData()])
        regex = self.video_regex.isChecked()
        text = self.video_values.text().strip()
        return Condition("video", ([text] if text else []) if regex else split_values(text),
                         where=self.video_field.currentData(), regex=regex)


class RenameEditor(QWidget):
    """The renamer, last in a rule (mockup 15B): its lines on the left, in the order they are tried (drag to
    reorder), the selected line on the right, and a name to try them on."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.list.setMaximumHeight(120)              # a few lines; long lists scroll
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.model().rowsMoved.connect(self._renumber)
        self.list.currentItemChanged.connect(self._select)
        left.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        add = QPushButton("+ Line")
        add.clicked.connect(self.add_line)
        remove = QPushButton("Delete")
        remove.clicked.connect(self.remove_line)
        buttons.addWidget(add)
        buttons.addWidget(remove)
        left.addLayout(buttons)
        layout.addLayout(left)
        right = QFormLayout()
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText(r"(.+?)[ .]S(\d\d)E(\d\d)")
        self.pattern.setToolTip("A regular expression, any case. It only has to be somewhere in the name.")
        self.to = QLineEdit()
        self.to.setPlaceholderText("{1|dots|title} S{2}E{3}.{ext}")
        self.to.setToolTip(rules.FILTER_HELP)
        for edit in (self.pattern, self.to):
            edit.textChanged.connect(self._edited)
        right.addRow("If the name contains", self.pattern)
        right.addRow("Rename to", self.to)
        self.sample = QLineEdit()
        self.sample.setPlaceholderText("the.expanse.s02e05.1080p.web.mkv")
        self.sample.textChanged.connect(self._try)
        right.addRow("Try a name", self.sample)
        self.result = QLabel()
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addRow("", self.result)
        hint = QLabel("The first line whose pattern is in the name renames the file; with none, the name is kept. "
                      "The pattern's groups are {1} {2} … (or {show} for (?P<show>…)).")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        right.addRow("", hint)
        holder = QWidget()
        holder.setLayout(right)
        layout.addWidget(holder, 1)
        self._loading = False
        self._show_selected()

    def _label(self, number: int, line: rules.Rename) -> str:
        return f"{number} · {line.pattern or '(no pattern)'}"

    def _renumber(self, *_):
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setText(self._label(i + 1, item.data(Qt.ItemDataRole.UserRole)))
        self._try()
        if not self._loading:
            self.changed.emit()

    def set_lines(self, lines: List[rules.Rename]) -> None:
        self._loading = True
        self.list.clear()
        for line in lines:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, copy.deepcopy(line))
            self.list.addItem(item)
        self._renumber()
        if self.list.count():
            self.list.setCurrentRow(0)
        self._loading = False
        self._show_selected()

    def lines(self) -> List[rules.Rename]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def add_line(self):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, rules.Rename("", "{name}.{ext}"))
        self.list.insertItem(self.list.currentRow() + 1, item)
        self._renumber()
        self.list.setCurrentItem(item)
        self.pattern.setFocus()

    def remove_line(self):
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self._renumber()
            self._show_selected()

    def _select(self, *_):
        self._show_selected()

    def _show_selected(self):
        item = self.list.currentItem()
        busy, self._loading = self._loading, True
        for edit in (self.pattern, self.to):
            edit.setEnabled(item is not None)
        line = item.data(Qt.ItemDataRole.UserRole) if item else rules.Rename("", "")
        self.pattern.setText(line.pattern)
        self.to.setText(line.to)
        self._loading = busy
        self._try()

    def _edited(self, *_):
        item = self.list.currentItem()
        if self._loading or item is None:
            return
        item.setData(Qt.ItemDataRole.UserRole, rules.Rename(self.pattern.text(), self.to.text()))
        item.setText(self._label(self.list.row(item) + 1, item.data(Qt.ItemDataRole.UserRole)))
        self._try()
        self.changed.emit()

    def _try(self, *_):
        name = self.sample.text().strip()
        if not name:
            self.result.setText("")
            return
        when = datetime.datetime.now()
        values = rules.token_values(Download("https://example.com/" + name, name, when=when), when)
        renamed, number = rules.rename_by_lines(self.lines(), name, values)
        self.result.setText(f"→ {renamed}   (line {number})" if renamed else "→ name kept: no line's pattern is in it")


class RuleForm(QWidget):
    """The selected rule: name, conditions, where it goes, the renamer, stop or continue, and what is wrong."""
    changed = pyqtSignal()

    def __init__(self, parent=None, library_of=rules.default_library):
        super().__init__(parent)
        self.library_of = library_of
        self._legacy_folder = ""          # a typed path from before Folders, kept as it was until saved
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Rule name")
        self.name.textChanged.connect(self.changed)
        self.enabled = QCheckBox("Enabled")
        self.enabled.toggled.connect(self.changed)
        top.addWidget(self.name, 1)
        top.addWidget(self.enabled)
        layout.addLayout(top)

        when = QGroupBox("When all of these match (no conditions: always)")
        self.condition_layout = QVBoxLayout(when)
        self.rows: List[ConditionRow] = []
        add = QPushButton("+ Add condition")
        add.clicked.connect(lambda: self.add_condition(None))
        self.condition_layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self._add_button = add
        layout.addWidget(when)

        then = QGroupBox("Put it in")
        form = QFormLayout(then)
        folder_row = QHBoxLayout()
        self.folder = QComboBox()
        self.folder.setToolTip("A folder from the Folders tab")
        self.folder.currentIndexChanged.connect(self.changed)
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(QLabel("+ subfolder"))
        self.subfolder = QLineEdit()
        self.subfolder.setPlaceholderText("{domain}   (optional)")
        self.subfolder.textChanged.connect(self.changed)
        folder_row.addWidget(self.subfolder, 1)
        form.addRow("Folder", folder_row)
        self.command = QLineEdit()
        self.command.textChanged.connect(self.changed)
        form.addRow("Run", self.command)
        form.setRowVisible(self.command, False)    # hidden until commands run (injection-safe quoting first); a
                                                   # command already saved in rules.json is kept, never wiped
        self.actions = form
        layout.addWidget(then)
        renamer = QGroupBox("Rename (last, optional)")
        renamer_layout = QVBoxLayout(renamer)
        self.renamer = RenameEditor()
        self.renamer.changed.connect(self.changed)
        renamer_layout.addWidget(self.renamer)
        layout.addWidget(renamer)
        after_box = QGroupBox("Then")
        after_form = QFormLayout(after_box)
        after = QHBoxLayout()
        self.stop = QRadioButton("Stop")
        self.go_on = QRadioButton("Continue to the rules below")
        group = QButtonGroup(self)
        group.addButton(self.stop)
        group.addButton(self.go_on)
        self.stop.toggled.connect(self.changed)
        after.addWidget(self.stop)
        after.addWidget(self.go_on)
        after.addStretch(1)
        after_form.addRow(after)
        layout.addWidget(after_box)
        self.subfolder.setToolTip(rules.FILTER_HELP)
        tokens = QLabel("Tokens: " + " ".join("{" + t + "}" for t in rules.TOKENS)
                        + ".  Filters after |, run in order: {name|dots|cut:1080p|trim} (hover a field for the list).")
        tokens.setWordWrap(True)
        tokens.setEnabled(False)
        layout.addWidget(tokens)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #c0392b")
        layout.addWidget(self.problems)
        layout.addStretch(1)
        self._loading = False
        self.changed.connect(self._check)

    def fill_folders(self, chosen: Optional[str] = None) -> None:
        """The folder drop-down: the folder so far, then every folder of the Folders tab."""
        chosen = self.folder.currentData() if chosen is None else chosen
        busy, self._loading = self._loading, True
        self.folder.clear()
        self.folder.addItem("(keep the folder so far)", KEEP_FOLDER)
        self.folder.addItem(f"{rules.DEFAULT_FOLDER}   —   where everything else goes", rules.DEFAULT_FOLDER)
        for f in self.library_of().folders:
            self.folder.addItem(f"{f.name}   —   {f.path}", f.name)
        if chosen and self.folder.findData(chosen) < 0:
            self.folder.addItem(f"{chosen}   (missing: see Folders)", chosen)
        self.folder.setCurrentIndex(max(self.folder.findData(chosen or KEEP_FOLDER), 0))
        self._loading = busy

    def add_condition(self, condition: Optional[Condition]) -> ConditionRow:
        row = ConditionRow(condition, library_of=self.library_of)
        row.changed.connect(self.changed)
        row.removed.connect(self._remove_condition)
        self.rows.append(row)
        self.condition_layout.insertWidget(self.condition_layout.count() - 1, row)
        if not self._loading:
            self.changed.emit()
        return row

    def _remove_condition(self, row: ConditionRow):
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    def load(self, rule: Rule) -> None:
        self._loading = True
        for row in list(self.rows):
            self.rows.remove(row)
            row.setParent(None)
            row.deleteLater()
        self.name.setText(rule.name)
        self.enabled.setChecked(rule.enabled)
        for c in rule.conditions:
            self.add_condition(c)
        self._legacy_folder = rule.folder
        self.fill_folders(rule.target)
        self.subfolder.setText(rule.subfolder)
        self.renamer.set_lines(rule.renames)
        self.command.setText(rule.command)
        (self.stop if rule.stop else self.go_on).setChecked(True)
        self._loading = False
        self._check()

    def rule(self) -> Rule:
        target = self.folder.currentData() or ""
        return Rule(self.name.text().strip() or "Unnamed rule", [r.condition() for r in self.rows],
                    folder="" if target else self._legacy_folder, subfolder=self.subfolder.text().strip(),
                    renames=self.renamer.lines(), target=target, command=self.command.text().strip(),
                    stop=self.stop.isChecked(), enabled=self.enabled.isChecked())

    def _check(self):
        if self._loading:
            return
        self.problems.setText("\n".join(rules.validate(self.rule(), self.library_of())))


class RulesDialog(QDialog):
    """Folders, File Groups, Site Groups and Rules, in the order you set them up; the test box under them all."""

    def __init__(self, rule_list: List[Rule], base_folder: str, parent=None, library: Optional[rules.Library] = None):
        super().__init__(parent)
        self.setWindowTitle("Rules")
        self.resize(1020, 760)
        self.base_folder = base_folder
        self.current: Optional[QListWidgetItem] = None
        library = copy.deepcopy(library) if library is not None else rules.default_library()
        rule_list = copy.deepcopy(rule_list)
        rules.adopt_folders(rule_list, library)          # typed paths from before Folders get a named folder

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.folders_tab = FoldersTab(library.folders, self.result_rules_preview, base_folder)
        self.groups_tab = FileGroupsTab(library.groups, self.result_rules_preview)
        self.sites_tab = SiteGroupsTab(library.site_groups, self.result_rules_preview)
        for tab in (self.folders_tab, self.groups_tab, self.sites_tab):
            tab.changed.connect(self._library_changed)
            tab.renamed.connect(self._renamed)
        rules_page = QWidget()
        rules_layout = QVBoxLayout(rules_page)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        for number, (tab, title) in enumerate(((self.folders_tab, "Folders"), (self.groups_tab, "File Groups"),
                                                (self.sites_tab, "Site Groups"), (rules_page, "Rules")), 1):
            self.tabs.addTab(tab, f"{number} · {title}")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.model().rowsMoved.connect(self._renumber)
        self.list.currentItemChanged.connect(self._select)
        left_layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        for text, slot in (("+ New rule", self.new_rule), ("Duplicate", self.duplicate_rule), ("Delete", self.delete_rule)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        left_layout.addLayout(buttons)
        legend = QLabel("→ continue · ■ stop · drag to reorder")
        legend.setEnabled(False)
        left_layout.addWidget(legend)
        split.addWidget(left)
        self.form = RuleForm(library_of=self.result_library)
        self.form.changed.connect(self._form_changed)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.form)
        split.addWidget(scroll)
        split.setSizes([260, 740])
        rules_layout.addWidget(split, 1)

        test = QGroupBox("Test")
        test_layout = QVBoxLayout(test)
        test_row = QHBoxLayout()
        self.test_url = QLineEdit()
        self.test_url.setPlaceholderText("Paste a link to see which rules hit and where it would go")
        self.test_url.textChanged.connect(self.update_test)
        self.test_page = QLineEdit()
        self.test_page.setPlaceholderText("clicked on page (optional)")
        self.test_page.setToolTip("The page the link was clicked on, as the browser extension passes it; "
                                  "site groups that match the page need it")
        self.test_page.textChanged.connect(self.update_test)
        self.test_video = QCheckBox("Video site")
        self.test_video.toggled.connect(self.update_test)
        self.test_channel = QLineEdit()
        self.test_channel.setPlaceholderText("channel")
        self.test_channel.setMaximumWidth(160)
        self.test_channel.textChanged.connect(self.update_test)
        test_row.addWidget(self.test_url, 2)
        test_row.addWidget(self.test_page, 1)
        test_row.addWidget(self.test_video)
        test_row.addWidget(self.test_channel)
        test_layout.addLayout(test_row)
        self.test_result = QLabel()
        self.test_result.setWordWrap(True)
        self.test_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        test_layout.addWidget(self.test_result)
        layout.addWidget(test)

        bottom = QHBoxLayout()
        export = QPushButton("Export…")
        export.setToolTip("Save the rules, folders and groups to a file, for another PC or another person")
        export.clicked.connect(self.export_rules)
        load = QPushButton("Import…")
        load.setToolTip("Load rules, folders and groups from an exported file")
        load.clicked.connect(self.import_rules)
        bottom.addWidget(export)
        bottom.addWidget(load)
        bottom.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        bottom.addWidget(box)
        layout.addLayout(bottom)

        for rule in rule_list:
            self._append(rule)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.tabs.setCurrentWidget(rules_page)          # most visits are to change a rule
        for tab in (self.folders_tab, self.groups_tab, self.sites_tab):
            tab.refresh()                                # "used by", now that the rules are in
        self.update_test()

    # ---- the library tabs ------------------------------------------------------------------------------------

    def result_library(self) -> rules.Library:
        return rules.Library(self.folders_tab.items(), self.groups_tab.items(), self.sites_tab.items())

    def result_base_folder(self) -> str:
        """The Default folder's path (the base folder in the settings)."""
        return self.folders_tab.default_path().strip() or self.base_folder

    def _library_changed(self):
        self.update_test()

    def _renamed(self, kind: str, old: str, new: str):
        rule_list = self.result_rules()
        rules.rename_references(rule_list, kind, old, new)
        for i, rule in enumerate(rule_list):
            self.list.item(i).setData(Qt.ItemDataRole.UserRole, rule)
        if self.current is not None:
            self.form.load(self.current.data(Qt.ItemDataRole.UserRole))
        self.update_test()

    def _tab_changed(self, *_):
        page = self.tabs.currentWidget()
        if page in (self.folders_tab, self.groups_tab, self.sites_tab):
            page.refresh()                                       # "used by" after rule edits
        elif self.current is not None:
            self.form.load(self.current.data(Qt.ItemDataRole.UserRole))   # folder list after Folders edits

    # ---- export and import -----------------------------------------------------------------------------------

    def export_rules(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export rules", "Accipio rules.json", "Accipio rules (*.json)")
        if not path:
            return
        try:
            settings_mod.export_rules(path, self.result_rules(), self.result_library())
        except OSError as error:
            QMessageBox.warning(self, "Export rules", f"The file could not be written: {error}")
            return
        QMessageBox.information(self, "Export rules", f"Saved {len(self.result_rules())} rules with their folders "
                                f"and groups to\n{path}")

    def import_rules(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import rules", "", "Accipio rules (*.json);;All files (*)")
        if not path:
            return
        try:
            theirs = settings_mod.import_rules(path)
        except ValueError as error:
            QMessageBox.warning(self, "Import rules", str(error))
            return
        box = QMessageBox(self)
        box.setWindowTitle("Import rules")
        box.setText(f"The file has {len(theirs[0])} rules. Add them below yours, or replace yours with them?")
        box.setInformativeText("Adding keeps all of yours: a folder or group of theirs with a name you already "
                               "use comes in as “Name (imported)”. Nothing is saved until you press OK.")
        add = box.addButton("Add to mine", QMessageBox.ButtonRole.AcceptRole)
        replace = box.addButton("Replace mine", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is add:
            rule_list, library = settings_mod.merge_imported((self.result_rules(), self.result_library()), theirs)
        elif box.clickedButton() is replace:
            rule_list, library = theirs[0], theirs[1] or rules.default_library()
        else:
            return
        self.load_everything(rule_list, library)

    def load_everything(self, rule_list: List[Rule], library: rules.Library) -> None:
        rules.adopt_folders(rule_list, library)
        self.folders_tab.set_items(library.folders)
        self.groups_tab.set_items(library.groups)
        self.sites_tab.set_items(library.site_groups)
        self.current = None
        self.list.clear()
        for rule in rule_list:
            self._append(rule)
        if self.list.count():
            self.list.setCurrentRow(0)
        for tab in (self.folders_tab, self.groups_tab, self.sites_tab):
            tab.refresh()
        self.update_test()

    # ---- the list --------------------------------------------------------------------------------------------

    def _append(self, rule: Rule, at: Optional[int] = None) -> QListWidgetItem:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, rule)
        if at is None:
            self.list.addItem(item)
        else:
            self.list.insertItem(at, item)
        self._renumber()
        return item

    def _label(self, number: int, rule: Rule) -> str:
        return f"{number}  {rule.name}   {'■' if rule.stop else '→'}"

    def _renumber(self, *_):
        for position in range(self.list.count()):
            item = self.list.item(position)
            rule = item.data(Qt.ItemDataRole.UserRole)
            item.setText(self._label(position + 1, rule))
            item.setForeground(QColor("gray") if not rule.enabled else self.list.palette().text().color())
        self.update_test()

    def _select(self, item, previous):
        if previous is not None and self.list.row(previous) >= 0:
            previous.setData(Qt.ItemDataRole.UserRole, self.form.rule())
        self.current = item
        if item is not None:
            self.form.load(item.data(Qt.ItemDataRole.UserRole))
        self.form.setEnabled(item is not None)

    def _form_changed(self):
        if self.current is not None and not self.form._loading:
            self.current.setData(Qt.ItemDataRole.UserRole, self.form.rule())
            self._renumber()

    def new_rule(self):
        at = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else self.list.count()
        item = self._append(Rule("New rule", [Condition("type", [])]), at)
        self.list.setCurrentItem(item)
        self.form.name.setFocus()
        self.form.name.selectAll()

    def duplicate_rule(self):
        if self.current is None:
            return
        rule = copy.deepcopy(self.current.data(Qt.ItemDataRole.UserRole))
        rule.name += " (copy)"
        self.list.setCurrentItem(self._append(rule, self.list.currentRow() + 1))

    def delete_rule(self):
        row = self.list.currentRow()
        if row < 0:
            return
        self.current = None
        self.list.takeItem(row)
        self._renumber()
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        else:
            self.form.setEnabled(False)

    def result_rules(self) -> List[Rule]:
        if self.current is not None:
            self.current.setData(Qt.ItemDataRole.UserRole, self.form.rule())
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    # ---- the test box ----------------------------------------------------------------------------------------

    def update_test(self, *_):
        url = self.test_url.text().strip()
        if not url:
            self.test_result.setText("")
            return
        filename = probe.filename_from_url(url if "://" in url else "https://" + url) or "download"
        video = {"channel": self.test_channel.text().strip(), "uploader": self.test_channel.text().strip()}
        page = self.test_page.text().strip()
        d = Download(url=url if "://" in url else "https://" + url, filename=filename,
                     page_url=page if not page or "://" in page else "https://" + page,
                     source="video" if self.test_video.isChecked() else "direct",
                     video={k: v for k, v in video.items() if v}, when=datetime.datetime.now())
        placement = rules.place(self.result_rules_preview(), d, self.result_base_folder(), self.result_library())
        hit = {s.number for s in placement.steps}
        skipped = [str(n) for n in range(1, self.list.count() + 1) if n not in hit]
        text = f"→ {placement.path}\n{placement.why()}"
        if skipped:
            text += f"\nSkipped: rule {', '.join(skipped)} (not matching, disabled, or after a stop)"
        self.test_result.setText(text)

    def result_rules_preview(self) -> List[Rule]:
        """The rules as they stand right now, including the form's unsaved edits."""
        if not hasattr(self, "list"):
            return []                                    # the library tabs ask while the window is being built
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            out.append(self.form.rule() if item is self.current else item.data(Qt.ItemDataRole.UserRole))
        return out
