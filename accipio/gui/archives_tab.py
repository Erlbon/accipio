"""The Rules window's Archives tab (decided 2026-11-06, Q56A): the archive trial's switch, what happens to an
archive once its files are out, and which rules already say what to do with one. It lived under Settings before,
where nobody found it."""
from __future__ import annotations

from typing import Callable, List

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QVBoxLayout, QWidget

from accipio.core import rules
from accipio.core.rules import Rule

AFTER_UNPACK = (("keep", "Keep the archive"), ("delete", "Move the archive to the Recycle Bin"))


class ArchivesTab(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, on: bool, after: str, rules_of: Callable[[], List[Rule]], parent=None):
        super().__init__(parent)
        self.rules_of = rules_of
        layout = QVBoxLayout(self)
        hint = QLabel("A finished .zip or .tar can show what is inside and offer to unpack it as a folder, sort the "
                      "files by your rules, or keep it packed. Formats that are zips underneath (.cbz, .epub, .docx) "
                      "are never opened; .rar and .7z come later. This is a trial: it is off until you switch it on.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.unpack = QCheckBox("Look inside downloaded zips and tars and ask what to do (trial)")
        self.unpack.setChecked(on)
        self.unpack.toggled.connect(self._toggled)
        form.addRow("Archives", self.unpack)
        self.after = QComboBox()
        for key, words in AFTER_UNPACK:
            self.after.addItem(words, key)
        self.after.setCurrentIndex(max(self.after.findData(after), 0))
        self.after.setEnabled(on)
        form.addRow("Once the files are out", self.after)
        layout.addLayout(form)
        heading = QLabel("Rules that say what to do with an archive")
        heading.setStyleSheet("font-weight: 600; margin-top: 12px")
        layout.addWidget(heading)
        self.by_rules = QLabel()
        self.by_rules.setWordWrap(True)
        self.by_rules.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.by_rules)
        inside = QLabel("A rule's choice also counts for a zip or tar full of its files: a .zip of .cbz comics follows "
                        "the Comics rule when its comics are most of the zip. If a real share of the files belong to "
                        "another rule, Accipio asks.")
        inside.setWordWrap(True)
        inside.setEnabled(False)
        layout.addWidget(inside)
        layout.addStretch(1)
        self.refresh()

    def _toggled(self, on: bool) -> None:
        self.after.setEnabled(on)
        self.toggled.emit(on)

    def refresh(self) -> None:
        lines = [f"{n}  {rule.name}: {rules.UNPACK_CHOICES[rule.unpack].removeprefix('archives ')}"
                 for n, rule in enumerate(self.rules_of(), 1) if rule.unpack in rules.UNPACK_CHOICES and rule.unpack]
        self.by_rules.setText("\n".join(lines) if lines else
                              "No rule says yet. A finished archive asks, and its “do this every time” tick writes "
                              "the rule; or pick “and archives are” in a rule under 4 · Rules.")

    def result(self) -> tuple:
        """(switched on, what happens to the archive afterwards)"""
        return self.unpack.isChecked(), self.after.currentData()
