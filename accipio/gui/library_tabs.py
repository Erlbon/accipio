"""The rules window's first three tabs: Folders, File Groups and Site Groups.

Each is a list on the left and the selected item as a form on the right, like the Rules tab. Rules point at
these by name, so renaming one here renames it in every rule, and one a rule still uses cannot be deleted
(the tab says which rules use it). Lists are comma-separated in the fields, as in the rule conditions.
"""
from __future__ import annotations

import copy
import datetime
import os
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from accipio.core import rules
from accipio.core.rules import Folder, Group, SiteGroup
from accipio.gui.example_hint import with_example

FOLDER_EXAMPLE = "%default%\\Comics   or   D:\\Comics\\_Inbox\\{yyyy}-{MM}"


def split_values(text: str) -> List[str]:
    return [v.strip() for v in text.split(",") if v.strip()]


class NamedListTab(QWidget):
    """A list of named items and a form for the selected one. Subclasses build the form and copy between it
    and an item; this class keeps the list, adding, deleting, renaming and "used by"."""
    changed = pyqtSignal()
    renamed = pyqtSignal(str, str, str)          # kind, old name, new name
    kind = ""                                    # rules.rename_references / rules_using kind
    noun = "item"
    hint = ""

    def __init__(self, items, rules_of: Callable[[], List[rules.Rule]], parent=None):
        super().__init__(parent)
        self.rules_of = rules_of
        self.current: Optional[QListWidgetItem] = None
        self._loading = False
        split = QSplitter()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._select)
        left_layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        for text, slot in self.list_buttons():
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        left_layout.addLayout(buttons)
        split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        if self.hint:
            hint = QLabel(self.hint)
            hint.setWordWrap(True)
            hint.setEnabled(False)
            right_layout.addWidget(hint)
        self.form = QFormLayout()
        self.name = QLineEdit()
        self.name.textEdited.connect(self._name_edited)
        self.form.addRow("Name", self.name)
        self.build_form()
        right_layout.addLayout(self.form)
        self.used_by = QLabel()
        self.used_by.setWordWrap(True)
        right_layout.addWidget(self.used_by)
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color: #c0392b")
        right_layout.addWidget(self.problems)
        right_layout.addStretch(1)
        split.addWidget(right)
        split.setSizes([240, 700])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        for item in items:
            self._append(copy.deepcopy(item))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._enable_form(False)

    # ---- for subclasses --------------------------------------------------------------------------------------

    def list_buttons(self):
        return [("+ New", self.new_item), ("Delete", self.delete_item)]

    def build_form(self) -> None:
        raise NotImplementedError

    def show_item(self, item) -> None:
        raise NotImplementedError

    def read_item(self, name: str):
        raise NotImplementedError

    def blank(self, name: str):
        raise NotImplementedError

    def item_problems(self, item) -> List[str]:
        return []

    # ---- the list --------------------------------------------------------------------------------------------

    def _append(self, data, at: Optional[int] = None) -> QListWidgetItem:
        item = QListWidgetItem(data.name)
        item.setData(Qt.ItemDataRole.UserRole, data)
        if at is None:
            self.list.addItem(item)
        else:
            self.list.insertItem(at, item)
        return item

    def names(self, skip: Optional[QListWidgetItem] = None) -> List[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole).name for i in range(self.list.count())
                if self.list.item(i) is not skip]

    def items(self):
        self._store()
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def set_items(self, items) -> None:
        self.current = None
        self.list.clear()
        for item in items:
            self._append(copy.deepcopy(item))
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._enable_form(False)

    def _enable_form(self, on: bool) -> None:
        for i in range(self.form.rowCount()):
            for role in (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole):
                cell = self.form.itemAt(i, role)
                if cell is not None and cell.widget() is not None:
                    cell.widget().setEnabled(on)
                elif cell is not None and cell.layout() is not None:
                    for j in range(cell.layout().count()):
                        w = cell.layout().itemAt(j).widget()
                        if w is not None:
                            w.setEnabled(on)
        if not on:
            self.used_by.setText("")
            self.problems.setText("")

    def _select(self, item, previous):
        if previous is not None and self.list.row(previous) >= 0:
            previous.setData(Qt.ItemDataRole.UserRole, self.read_item(previous.data(Qt.ItemDataRole.UserRole).name))
        self.current = item
        self._enable_form(item is not None)
        if item is None:
            return
        self._loading = True
        data = item.data(Qt.ItemDataRole.UserRole)
        self.name.setText(data.name)
        self.show_item(data)
        self._loading = False
        self.refresh()

    def _store(self) -> None:
        if self.current is not None and self.list.row(self.current) >= 0:
            self.current.setData(Qt.ItemDataRole.UserRole, self.read_item(self.current.data(Qt.ItemDataRole.UserRole).name))

    def field_changed(self, *_):
        if self._loading or self.current is None:
            return
        self._store()
        self.refresh()
        self.changed.emit()

    def _name_edited(self, text: str):
        if self.current is None:
            return
        old = self.current.data(Qt.ItemDataRole.UserRole).name
        new = text.strip()
        if not new or new.lower() in {n.lower() for n in self.names(skip=self.current)}:
            self.problems.setText(f"Give the {self.noun} a name no other {self.noun} has." if new else
                                  f"Give the {self.noun} a name.")
            return
        data = self.read_item(new)
        self.current.setData(Qt.ItemDataRole.UserRole, data)
        self.current.setText(new)
        if new != old:
            self.renamed.emit(self.kind, old, new)
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        if self.current is None:
            return
        data = self.current.data(Qt.ItemDataRole.UserRole)
        using = rules.rules_using(self.rules_of(), self.kind, data.name)
        self.used_by.setText("Used by rule " + ", ".join(map(str, using)) if using else "No rule uses it yet.")
        self.problems.setText("\n".join(self.item_problems(data)))

    def new_item(self):
        name = rules.unique_name(f"New {self.noun}", self.names())
        at = self.list.currentRow() + 1 if self.list.currentRow() >= 0 else self.list.count()
        item = self._append(self.blank(name), at)
        self.list.setCurrentItem(item)
        self.name.setFocus()
        self.name.selectAll()
        self.changed.emit()

    def delete_item(self):
        row = self.list.currentRow()
        if row < 0:
            return
        name = self.list.item(row).data(Qt.ItemDataRole.UserRole).name
        using = rules.rules_using(self.rules_of(), self.kind, name)
        if using:
            QMessageBox.information(self, "Still in use", f"Rule {', '.join(map(str, using))} uses the {self.noun} "
                                    f"“{name}”. Point {'it' if len(using) == 1 else 'them'} elsewhere first.")
            return
        self.current = None
        self.list.takeItem(row)
        if self.list.count():
            self.list.setCurrentRow(min(row, self.list.count() - 1))
        else:
            self._enable_form(False)
        self.changed.emit()


class FoldersTab(NamedListTab):
    """Default first (the base folder: everything goes there unless a rule picks another), then your folders,
    written "%default%\\Comics" or as a full path. Default cannot be renamed or deleted."""
    kind = "folder"
    noun = "folder"
    hint = ("Places downloads go. Everything goes to Default unless a rule picks another folder. Rules pick folders "
            "by name, so moving a folder here moves every rule that uses it. Write new ones as %default%\\Comics "
            "(under Default) or as a full path; tokens such as {yyyy} {MM} {domain} {uploader} are allowed.")

    def __init__(self, items, rules_of, base_folder: str, parent=None):
        self.base_folder = base_folder
        super().__init__([Folder(rules.DEFAULT_FOLDER, base_folder)] + list(items), rules_of, parent)

    def is_default_item(self, item) -> bool:
        return item is not None and self.list.row(item) == 0

    def items(self):
        return super().items()[1:]

    def set_items(self, items) -> None:
        super().set_items([Folder(rules.DEFAULT_FOLDER, self.default_path())] + list(items))

    def default_path(self) -> str:
        self._store()
        return self.list.item(0).data(Qt.ItemDataRole.UserRole).path if self.list.count() else self.base_folder

    def _select(self, item, previous):
        super()._select(item, previous)
        default = self.is_default_item(item)
        self.name.setReadOnly(default)
        self.path.example_hint.setText("Example: " + ("D:\\Downloads" if default else FOLDER_EXAMPLE))

    def _name_edited(self, text: str):
        if not self.is_default_item(self.current) and rules.is_default(text):
            self.problems.setText("“Default” is the folder everything goes to; give this one another name.")
            return
        super()._name_edited(text)

    def delete_item(self):
        if self.is_default_item(self.list.currentItem()):
            QMessageBox.information(self, "Default folder", "Default is where everything goes unless a rule says "
                                    "otherwise, so it cannot be deleted. Point it at another folder instead.")
            return
        super().delete_item()

    def refresh(self) -> None:
        super().refresh()
        if self.is_default_item(self.current):
            using = rules.rules_using(self.rules_of(), self.kind, rules.DEFAULT_FOLDER)
            self.used_by.setText("Everything no rule places goes here"
                                 + (f", and rule {', '.join(map(str, using))} sends downloads here too." if using else "."))

    def build_form(self):
        row = QHBoxLayout()
        self.path = QLineEdit()
        self.path.setToolTip(rules.FILTER_HELP)
        self.path.textChanged.connect(self.field_changed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(with_example(self.path, FOLDER_EXAMPLE), 1)
        row.addWidget(browse, 0, Qt.AlignmentFlag.AlignTop)
        self.form.addRow("Where", row)
        self.today = QLabel()
        self.today.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow("Today this is", self.today)

    def _browse(self):
        start = self.path.text()
        if start.lower().startswith(rules.DEFAULT_TOKEN):
            start = os.path.join(self.default_path_quiet(), start[len(rules.DEFAULT_TOKEN):].lstrip("\\/"))
        chosen = QFileDialog.getExistingDirectory(self, "Folder", start)
        if chosen:
            self.path.setText(self.written(os.path.normpath(chosen)))

    def written(self, chosen: str) -> str:
        """A folder picked inside Default is written "%default%\\..." so it follows Default when that moves."""
        if self.is_default_item(self.current):
            return chosen
        default = os.path.normpath(self.default_path_quiet())
        try:
            inside = os.path.commonpath([os.path.normcase(chosen), os.path.normcase(default)]) == os.path.normcase(default)
        except ValueError:                       # another drive
            inside = False
        if not inside:
            return chosen
        rest = os.path.relpath(chosen, default)
        return rules.DEFAULT_TOKEN if rest == "." else f"{rules.DEFAULT_TOKEN}\\{rest}"

    def show_item(self, item: Folder):
        self.path.setText(item.path)

    def read_item(self, name: str) -> Folder:
        return Folder(name, self.path.text().strip())

    def blank(self, name: str) -> Folder:
        return Folder(name, f"{rules.DEFAULT_TOKEN}\\{name}")

    def item_problems(self, item: Folder) -> List[str]:
        if rules.is_default(item.name) and self.list.count() and self.current is self.list.item(0):
            self.today.setText(item.path)
            if not item.path.strip():
                return ["Point Default at a folder: everything goes there unless a rule says otherwise."]
            return [] if os.path.isabs(item.path) else ["Give Default a full path, such as D:\\Downloads."]
        sample = rules.Download("https://example.com/file.zip", "file.zip", when=datetime.datetime.now(),
                                video={"uploader": "Uploader", "channel": "Channel"})
        placement = rules.place([rules.Rule("x", target=item.name)], sample, self.default_path_quiet(),
                                rules.Library([item], [], []))
        self.today.setText(placement.folder if item.path.strip() else "(the folder so far)")
        found = [] if item.path.strip() else ["Give the folder a path."]
        return found + [p for p in rules.validate(rules.Rule("x", folder=item.path)) if p.startswith("folder")]

    def default_path_quiet(self) -> str:
        """Default's path without storing the form first (item_problems runs while the form is being read)."""
        return self.list.item(0).data(Qt.ItemDataRole.UserRole).path if self.list.count() else self.base_folder


class FileGroupsTab(NamedListTab):
    kind = "type"
    noun = "file group"
    hint = ("What a download is. A file is in the group when any one entry fits: an extension, or a name "
            "wildcard such as *invoice*. MIME types are only used when the file's extension is in no group.")

    def list_buttons(self):
        return super().list_buttons() + [("Reset to built-in…", self.reset)]

    def build_form(self):
        self.extensions = QLineEdit()
        self.names_field = QLineEdit()
        self.mimes = QLineEdit()
        for edit in (self.extensions, self.names_field, self.mimes):
            edit.textChanged.connect(self.field_changed)
        self.form.addRow("Extensions", with_example(self.extensions, "cbz, cbr, cb7"))
        self.form.addRow("Names", with_example(self.names_field, "*invoice*, *Vol.??*"))
        self.form.addRow("MIME types", with_example(self.mimes, "application/vnd.comicbook+zip"))

    def show_item(self, item: Group):
        self.extensions.setText(", ".join(item.extensions))
        self.names_field.setText(", ".join(item.names))
        self.mimes.setText(", ".join(item.mimes))

    def read_item(self, name: str) -> Group:
        return Group(name, [e.lstrip(".").lower() for e in split_values(self.extensions.text())],
                     split_values(self.mimes.text()), split_values(self.names_field.text()))

    def blank(self, name: str) -> Group:
        return Group(name)

    def item_problems(self, item: Group) -> List[str]:
        return [] if item.extensions or item.names or item.mimes else ["Give at least one extension or name."]

    def reset(self):
        answer = QMessageBox.question(self, "Reset file groups", "Put the built-in file groups (Video, Audio, "
                                      "Images, Documents, Ebooks, Comics, Archives, Programs) back as they came? "
                                      "Your own groups are kept.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        built_in = {g.name.lower(): g for g in rules.default_groups()}
        mine = [g for g in self.items() if g.name.lower() not in built_in]
        self.set_items(list(built_in.values()) + mine)
        self.changed.emit()


class SiteGroupsTab(NamedListTab):
    kind = "sites"
    noun = "site group"
    hint = ("Where a download comes from. Optional: rules work without any. A site covers its subdomains; "
            "wildcards are allowed. Link sites (a comics blog, a forum) hand out files from hosters, so they "
            "are caught through the page you clicked on, not the file's own address.")

    def build_form(self):
        self.sites = QLineEdit()
        self.sites.textChanged.connect(self.field_changed)
        self.form.addRow("Sites", with_example(self.sites, "getcomics.org, readcomicsonline.ru"))
        self.where = QComboBox()
        for key, label in (("either", "the page you clicked on, or where the file comes from"),
                           ("page", "only the page you clicked on"), ("file", "only where the file comes from")):
            self.where.addItem(label, key)
        self.where.currentIndexChanged.connect(self.field_changed)
        self.form.addRow("Match", self.where)
        note = QLabel("A link pasted into Accipio has no page, so only where the file comes from counts for it.")
        note.setWordWrap(True)
        note.setEnabled(False)
        self.form.addRow("", note)

    def show_item(self, item: SiteGroup):
        self.sites.setText(", ".join(item.sites))
        self.where.setCurrentIndex(max(self.where.findData(item.where or "either"), 0))

    def read_item(self, name: str) -> SiteGroup:
        return SiteGroup(name, split_values(self.sites.text()), self.where.currentData())

    def blank(self, name: str) -> SiteGroup:
        return SiteGroup(name)

    def item_problems(self, item: SiteGroup) -> List[str]:
        return [] if item.sites else ["Give at least one site."]
