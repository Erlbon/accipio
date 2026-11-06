"""Examples under fields, never in them (decided 2026-11-06, 65D).

Grey example text inside an empty field ("*invoice*, *Vol.??*") read as real values, above all in the dark
theme. So a field stays empty and its example is a small grey line under it: ``with_example(field, text)``
returns the field and its hint as one widget to put in a layout. The hint is ``field.example_hint``, so a form
can change it (the Default folder has another example than a named folder). Prompts that nobody takes for a
value ("Paste a link to …") stay inside their fields as before.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget


def hint_label(text: str) -> QLabel:
    hint = QLabel(text)
    hint.setEnabled(False)                       # the palette's grey, as the form's other quiet words
    hint.setWordWrap(True)
    font = hint.font()
    font.setPointSizeF(font.pointSizeF() * 0.9)
    hint.setFont(font)
    return hint


def with_example(field: QWidget, text: str) -> QWidget:
    holder = QWidget()
    holder.setProperty("stretches", True)       # takes the row's spare width, as the field alone did
    column = QVBoxLayout(holder)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(2)
    column.addWidget(field)
    field.example_hint = hint_label(f"Example: {text}")
    column.addWidget(field.example_hint)
    return holder
