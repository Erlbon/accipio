"""The rules window: Folders, File Groups, Site Groups and Rules tabs, and a test box under them that shows, as
you type, where a link would go.

The Rules tab (mockup 24A, 2026-11-04): a narrow list on the left in the order rules are tried (drag to
reorder; the first that fits wins), and the selected rule as a sentence: "if downloaded from <site groups>,
files that are <file groups> go to <folder>", with the rarer conditions under More conditions and the rename
lines last. Groups are picked as chips, never typed. Under More conditions the time condition is edited with
weekday buttons and an hour range and read back as a sentence; the raw cron lines are in its Advanced field.
"""
from __future__ import annotations

import copy
import datetime
import os
from typing import List, Optional

from PyQt6.QtCore import QTime, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
                             QScrollArea, QSplitter, QStackedWidget, QTabWidget, QTimeEdit, QToolButton, QVBoxLayout,
                             QWidget)

from accipio.core import cron, probe, rules
from accipio.core import settings as settings_mod
from accipio.core.rules import Condition, Download, Rule
from accipio.gui import style
from accipio.gui.library_tabs import FileGroupsTab, FoldersTab, SiteGroupsTab

KIND_LABELS = [("type", "In file group"), ("sites", "From site group"), ("domain", "Site"), ("name", "Name"),
               ("size", "Size"), ("time", "Time of day"), ("source", "Source"), ("video", "Video field")]
# Under "More conditions" (27A); file groups and site groups have their own lines in the rule's sentence.
MORE_KINDS = [(k, label) for k, label in KIND_LABELS if k not in ("type", "sites")]


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

    def __init__(self, condition: Optional[Condition] = None, parent=None, library_of=rules.default_library,
                 kinds=KIND_LABELS):
        super().__init__(parent)
        self.library_of = library_of
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        self.kind = QComboBox()
        for key, label in kinds:
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
        self.set_condition(condition or Condition(kinds[0][0], []))

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
        page("type", self.type_values, self.type_menu)

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
        self._pick(self.kind, c.kind)
        self.stack.setCurrentWidget(self.pages[self.kind.currentData()])
        joined = ", ".join(c.values)
        if c.kind == "domain":
            self.domain_values.setText(joined)
            self._pick(self.domain_where, c.where or "either")
        elif c.kind == "type":
            self.type_values.setText(joined)
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
            return Condition("type", split_values(self.type_values.text()))
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


class ChipPicker(QWidget):
    """Names picked from a list, shown as removable chips, with "+ add" for the rest (mockup 24A). Nothing is
    typed, so a name can only be one that exists; a name that no longer exists still shows, marked, until
    removed."""
    changed = pyqtSignal()

    def __init__(self, add_text: str, empty_text: str, names_of, parent=None):
        super().__init__(parent)
        self.names_of = names_of                # () -> [(name, menu label)] of what can be picked
        self.empty_text = empty_text
        self._chosen: List[str] = []
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(6)
        self.add = QToolButton()
        self.add.setText(add_text)
        self.add.setObjectName("chipAdd")
        self.add.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self.add)
        self.menu.aboutToShow.connect(self._fill_menu)
        self.add.setMenu(self.menu)
        self.hint = QLabel(empty_text)
        self.hint.setEnabled(False)
        self._redraw()

    def values(self) -> List[str]:
        return list(self._chosen)

    def set_values(self, names: List[str]) -> None:
        self._chosen = [n for n in names if n.strip()]
        self._redraw()

    def pick(self, name: str) -> None:
        if name.lower() not in {c.lower() for c in self._chosen}:
            self._chosen.append(name)
            self._redraw()
            self.changed.emit()

    def drop(self, name: str) -> None:
        self._chosen = [c for c in self._chosen if c != name]
        self._redraw()
        self.changed.emit()

    def _fill_menu(self):
        self.menu.clear()
        taken = {c.lower() for c in self._chosen}
        offered = [(n, label) for n, label in self.names_of() if n.lower() not in taken]
        for name, label in offered:
            self.menu.addAction(label, lambda n=name: self.pick(n))
        if not offered:
            self.menu.addAction("(nothing more to add: see the tabs to the left)").setEnabled(False)

    def _redraw(self):
        while self.row.count():
            item = self.row.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in (self.add, self.hint):
                widget.hide()                    # at once: until deleteLater runs it would still be drawn
                widget.setParent(None)
                widget.deleteLater()
        known = {n.lower() for n, _ in self.names_of()}
        for name in self._chosen:
            chip = QPushButton()
            style.make_chip(chip)                # the main window's filter pills, shown filled
            chip.setCheckable(True)
            chip.setChecked(True)
            missing = name.lower() not in known
            chip.setText(f"{name}  ✕" if not missing else f"{name} (missing)  ✕")
            chip.setToolTip("Remove" if not missing else "There is no group with this name any more; remove it")
            chip.clicked.connect(lambda _=False, n=name: self.drop(n))
            self.row.addWidget(chip)
        self.row.addWidget(self.add)
        self.row.addWidget(self.hint)
        self.hint.setVisible(not self._chosen)
        self.row.addStretch(1)


class RenameRow(QFrame):
    """One rename line: pattern → template, with ↑ ↓ ✕ (the order is the order they are tried in)."""
    changed = pyqtSignal()
    action = pyqtSignal(object, str)             # self, "up" / "down" / "remove"

    def __init__(self, line: rules.Rename, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.number = QLabel()
        self.number.setMinimumWidth(16)
        self.pattern = QLineEdit(line.pattern)
        self.pattern.setPlaceholderText(r"(.+?)[ .]S(\d\d)E(\d\d)")
        self.pattern.setToolTip("A regular expression, any case. It only has to be somewhere in the name.")
        self.to = QLineEdit(line.to)
        self.to.setPlaceholderText("{1|dots|title} S{2}E{3}.{ext}")
        self.to.setToolTip(rules.FILTER_HELP)
        for edit in (self.pattern, self.to):
            edit.textChanged.connect(self.changed)
        row.addWidget(self.number)
        row.addWidget(self.pattern, 1)
        row.addWidget(QLabel("→"))
        row.addWidget(self.to, 1)
        for text, what, tip in (("↑", "up", "Try this line earlier"), ("↓", "down", "Try this line later"),
                                ("✕", "remove", "Remove this line")):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.clicked.connect(lambda _=False, w=what: self.action.emit(self, w))
            row.addWidget(button)

    def line(self) -> rules.Rename:
        return rules.Rename(self.pattern.text(), self.to.text())


class RenameEditor(QWidget):
    """"After downloading, rename": the lines in the order they are tried, each pattern → template, and a name
    to try them on. The first line whose pattern is in the name renames the file; with none, it is kept."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(4)
        layout.addLayout(self.rows_layout)
        self.rows: List[RenameRow] = []
        bottom = QHBoxLayout()
        self.add_button = QPushButton("+ Rename line")
        self.add_button.clicked.connect(self.add_line)
        bottom.addWidget(self.add_button)
        bottom.addSpacing(12)
        bottom.addWidget(QLabel("Try a name"))
        self.sample = QLineEdit()
        self.sample.setPlaceholderText("the.expanse.s02e05.1080p.web.mkv")
        self.sample.textChanged.connect(self._try)
        bottom.addWidget(self.sample, 1)
        layout.addLayout(bottom)
        self.result = QLabel()
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.result)
        self._loading = False

    def set_lines(self, lines: List[rules.Rename]) -> None:
        self._loading = True
        for row in list(self.rows):
            self._drop(row)
        for line in lines:
            self._add(copy.deepcopy(line))
        self._loading = False
        self._renumber()

    def lines(self) -> List[rules.Rename]:
        return [row.line() for row in self.rows]

    def add_line(self) -> RenameRow:
        row = self._add(rules.Rename("", "{name}.{ext}"))
        self._renumber()
        row.pattern.setFocus()
        self.changed.emit()
        return row

    def _add(self, line: rules.Rename) -> RenameRow:
        row = RenameRow(line)
        row.changed.connect(self._edited)
        row.action.connect(self._act)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        return row

    def _drop(self, row: RenameRow) -> None:
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def _act(self, row: RenameRow, what: str) -> None:
        at = self.rows.index(row)
        if what == "remove":
            self._drop(row)
        else:
            to = at - 1 if what == "up" else at + 1
            if not 0 <= to < len(self.rows):
                return
            self.rows[at], self.rows[to] = self.rows[to], self.rows[at]
            for r in self.rows:
                self.rows_layout.removeWidget(r)
            for r in self.rows:
                self.rows_layout.addWidget(r)
        self._renumber()
        self.changed.emit()

    def _renumber(self):
        for number, row in enumerate(self.rows, 1):
            row.number.setText(str(number))
        self._try()

    def _edited(self, *_):
        self._try()
        if not self._loading:
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
    """The selected rule as one sentence (mockup 24A): name; if downloaded from <site groups>, files that are
    <file groups> go to <folder>; more conditions folded away; after downloading, rename. The first rule that
    fits wins, so there is no stop/continue."""
    changed = pyqtSignal()

    def __init__(self, parent=None, library_of=rules.default_library):
        super().__init__(parent)
        self.library_of = library_of
        self._legacy_folder = ""          # a typed path from before Folders, kept as it was until saved
        layout = QVBoxLayout(self)
        sentence = QGridLayout()
        sentence.setHorizontalSpacing(12)
        sentence.setVerticalSpacing(10)

        def label(text):
            words = QLabel(text)
            words.setEnabled(False)
            return words

        name_row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Comics")
        self.name.textChanged.connect(self.changed)
        self.enabled = QCheckBox("On")
        self.enabled.setToolTip("Switch the rule off without deleting it")
        self.enabled.toggled.connect(self.changed)
        name_row.addWidget(self.name, 1)
        name_row.addWidget(self.enabled)
        sentence.addWidget(label("Rule"), 0, 0)
        sentence.addLayout(name_row, 0, 1)

        self.sites = ChipPicker("+ site group", "anywhere", lambda: [
            (g.name, f"{g.name}  —  {', '.join(g.sites[:4])}{' …' if len(g.sites) > 4 else ''}")
            for g in self.library_of().site_groups])
        self.sites.changed.connect(self.changed)
        sentence.addWidget(label("If downloaded from"), 1, 0)
        sentence.addWidget(self.sites, 1, 1)

        self.types = ChipPicker("+ file group", "any file", lambda: [
            (g.name, f"{g.name}  —  {group_summary(g)}") for g in self.library_of().groups])
        self.types.changed.connect(self.changed)
        sentence.addWidget(label("files that are"), 2, 0)
        sentence.addWidget(self.types, 2, 1)

        folder_row = QHBoxLayout()
        self.folder = QComboBox()
        self.folder.setToolTip("A folder from the Folders tab")
        self.folder.currentIndexChanged.connect(self.changed)
        self.folder.currentIndexChanged.connect(self._show_where)
        self.where = QLabel()
        self.where.setEnabled(False)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(self.where, 1)
        sentence.addWidget(label("go to"), 3, 0)
        sentence.addLayout(folder_row, 3, 1)
        sentence.setColumnStretch(1, 1)
        layout.addLayout(sentence)

        self.more_button = QToolButton()
        self.more_button.setCheckable(True)
        self.more_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.more_button.setArrowType(Qt.ArrowType.RightArrow)
        self.more_button.setAutoRaise(True)
        self.more_button.toggled.connect(self._show_more)
        layout.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.more = QWidget()
        self.condition_layout = QVBoxLayout(self.more)
        self.condition_layout.setContentsMargins(18, 0, 0, 0)
        self.rows: List[ConditionRow] = []
        add = QPushButton("+ Add condition")
        add.clicked.connect(lambda: self.add_condition(None))
        self.condition_layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)
        self._add_button = add
        layout.addWidget(self.more)

        rule_line = QFrame()
        rule_line.setFrameShape(QFrame.Shape.HLine)
        rule_line.setEnabled(False)
        layout.addWidget(rule_line)
        layout.addWidget(label("After downloading, rename (the first line that fits; none fits: the name is kept)"))
        self.renamer = RenameEditor()
        self.renamer.changed.connect(self.changed)
        layout.addWidget(self.renamer)

        self.command = QLineEdit()                 # hidden until commands run (injection-safe quoting first); a
        self.command.hide()                        # command already saved in rules.json is kept, never wiped
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
        self._show_more(False)
        self.changed.connect(self._check)

    def _show_more(self, on: bool) -> None:
        self.more.setVisible(on)
        self.more_button.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)
        count = len(self.rows)
        self.more_button.setText("More conditions" + (f" ({count})" if count else "")
                                 + ("" if on else "   name, size, time of day, source, video channel…"))

    def fill_folders(self, chosen: Optional[str] = None) -> None:
        """The folder drop-down: Default, then every folder of the Folders tab."""
        chosen = self.folder.currentData() if chosen is None else chosen
        busy, self._loading = self._loading, True
        self.folder.clear()
        self.folder.addItem(rules.DEFAULT_FOLDER, rules.DEFAULT_FOLDER)
        for f in self.library_of().folders:
            self.folder.addItem(f.name, f.name)
        if chosen and not rules.is_default(chosen) and self.folder.findData(chosen) < 0:
            self.folder.addItem(f"{chosen} (missing: see Folders)", chosen)
        self.folder.setCurrentIndex(max(self.folder.findData(chosen or rules.DEFAULT_FOLDER), 0))
        self._loading = busy
        self._show_where()

    def _show_where(self, *_):
        named = rules.find_named(self.library_of().folders, self.folder.currentData() or "")
        self.where.setText(named.path if named else "where everything else goes")

    def add_condition(self, condition: Optional[Condition]) -> ConditionRow:
        row = ConditionRow(condition, library_of=self.library_of, kinds=MORE_KINDS)
        row.changed.connect(self.changed)
        row.removed.connect(self._remove_condition)
        self.rows.append(row)
        self.condition_layout.insertWidget(self.condition_layout.count() - 1, row)
        if not self._loading:
            self.more_button.setChecked(True)
            self._show_more(True)
            self.changed.emit()
        return row

    def _remove_condition(self, row: ConditionRow):
        self.rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._show_more(self.more_button.isChecked())
        self.changed.emit()

    def load(self, rule: Rule) -> None:
        self._loading = True
        for row in list(self.rows):
            self.rows.remove(row)
            row.setParent(None)
            row.deleteLater()
        self.name.setText(rule.name)
        self.enabled.setChecked(rule.enabled)
        sites, types, more = [], [], []
        for c in rule.conditions:
            if c.kind == "sites":
                sites += [v for v in c.values if v.strip()]
            elif c.kind == "type":
                types += [v for v in c.values if v.strip()]
            else:
                more.append(c)
        self.sites.set_values(sites)
        self.types.set_values(types)
        for c in more:
            self.add_condition(c)
        self._legacy_folder = rule.folder
        self.fill_folders(rule.target or rules.DEFAULT_FOLDER)
        self.renamer.set_lines(rule.renames)
        self.command.setText(rule.command)
        self.more_button.setChecked(bool(more))
        self._loading = False
        self._show_more(bool(more))
        self._check()

    def rule(self) -> Rule:
        conditions = []
        if self.sites.values():
            conditions.append(Condition("sites", self.sites.values()))
        if self.types.values():
            conditions.append(Condition("type", self.types.values()))
        conditions += [row.condition() for row in self.rows]
        target = self.folder.currentData() or rules.DEFAULT_FOLDER
        return Rule(self.name.text().strip() or "Unnamed rule", conditions,
                    folder="" if target else self._legacy_folder, renames=self.renamer.lines(), target=target,
                    command=self.command.text().strip(), enabled=self.enabled.isChecked())

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
        rules.modernize(rule_list, library)          # older rules: named folders, first fit

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
        for text, slot, tip in (("+", self.new_rule, "New rule"), ("Copy", self.duplicate_rule, "Copy this rule"),
                                ("−", self.delete_rule, "Delete this rule")):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        left_layout.addLayout(buttons)
        legend = QLabel("The first rule that fits wins. Drag to reorder.")
        legend.setWordWrap(True)
        legend.setEnabled(False)
        left_layout.addWidget(legend)
        left.setMaximumWidth(200)                        # a few short names; the rule itself needs the room
        split.addWidget(left)
        self.form = RuleForm(library_of=self.result_library)
        self.form.changed.connect(self._form_changed)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.form)
        split.addWidget(scroll)
        split.setSizes([170, 850])
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
        rules.modernize(rule_list, library)
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
        return f"{number}  {rule.name}"

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
        item = self._append(Rule("New rule", target=rules.DEFAULT_FOLDER), at)
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
            text += f"\nSkipped: rule {', '.join(skipped)} (not matching, switched off, or after the first rule that fits)"
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
