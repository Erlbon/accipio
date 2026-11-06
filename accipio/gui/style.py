"""Accipio's look on top of the family theme: soft, rounded corners (mockup 1 "soft", picked 2026-10-29).

redactor_common's ``apply_theme`` sets Fusion and the light/dark palette; this adds one stylesheet that only
rounds and quiets things. Its lines and hover shades are worked out from the palette in use (a little of the
text colour mixed into the background), because the palette's own "mid" is near-black in light mode and
would make every border hard again. Qt drops a corner radius larger than half a widget's height, so the radii
stay small (6 px on 26 px controls; chips 11 px on 22 px). The progress bar is drawn by
``queue_model.ProgressDelegate``, not here.
"""
from __future__ import annotations

import os

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

from accipio.gui import appicon

WARNING_COLOR = "#BA7517"           # amber, readable on light and dark backgrounds
ROW_HEIGHT = 26                # table rows, a little taller than Fusion's default
CHEVRON = os.path.join(appicon.HERE, "chevron-down.png")    # made by tools/make_icons.py, as the icons are

STYLESHEET = """
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QTimeEdit {
    border: 1px solid @border; border-radius: 6px; padding: 3px 8px; background: palette(base); }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus {
    border: 1px solid palette(highlight); }
QPushButton { border: 1px solid @border; border-radius: 6px; padding: 4px 14px; min-height: 18px;
    background: palette(button); }
QPushButton:hover { background: @hover; }
QPushButton:pressed { background: @pressed; }
QPushButton:default { border: 1px solid palette(highlight); }
QPushButton[suggested="true"] { background: palette(highlight); color: palette(highlighted-text);
    border: 1px solid palette(highlight); }
QPushButton:disabled { color: @handle; }
QPushButton[chip="true"] { border: none; border-radius: 11px; padding: 3px 12px; min-height: 16px;
    background: transparent; }
QPushButton[chip="true"]:hover { background: @hover; }
QPushButton[chip="true"]:checked { background: palette(highlight); color: palette(highlighted-text); }
QToolButton { border: 1px solid transparent; border-radius: 6px; padding: 2px 6px; }
QToolButton:hover { border: 1px solid @border; background: @hover; }
QComboBox { border: 1px solid @border; border-radius: 6px; padding: 3px 8px; background: palette(button); }
QComboBox:hover { background: @hover; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url(@chevron); width: 10px; height: 6px; }
QTableView, QListView { border: 1px solid @border; border-radius: 8px; background: palette(base);
    gridline-color: transparent; }
QTableView::item, QListView::item { border: none; padding: 0 6px; }
QListView::item { padding: 4px 6px; border-radius: 5px; }
QListView::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QHeaderView { background: transparent; }
QHeaderView::section { background: palette(base); border: none; border-bottom: 1px solid @border;
    padding: 5px 8px; font-weight: normal; }
QHeaderView::section:hover { background: @hover; }
QTableCornerButton::section { background: palette(base); border: none; }
QGroupBox { border: 1px solid @border; border-radius: 8px; margin-top: 10px; padding: 10px 8px 8px 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QScrollBar:horizontal { height: 12px; background: transparent; margin: 3px 4px; }
QScrollBar:vertical { width: 12px; background: transparent; margin: 4px 3px; }
QScrollBar::handle { background: @handle; border-radius: 3px; min-width: 30px; min-height: 30px; }
QScrollBar::handle:hover { background: @handlehover; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QMenu { border: 1px solid @border; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 5px 22px; border-radius: 5px; }
QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }
QToolTip { border: 1px solid @border; border-radius: 6px; padding: 4px 6px; }
"""


def mix(under: QColor, over: QColor, amount: float) -> str:
    """``over`` laid on ``under`` at ``amount`` (0..1), as #rrggbb."""
    def channel(a: int, b: int) -> int:
        return round(a + (b - a) * amount)
    return QColor(channel(under.red(), over.red()), channel(under.green(), over.green()),
                  channel(under.blue(), over.blue())).name()


def stylesheet(palette: QPalette) -> str:
    background = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.Text)
    shades = {"@handlehover": mix(background, text, 0.45), "@handle": mix(background, text, 0.28),
              "@pressed": mix(background, text, 0.16), "@border": mix(background, text, 0.20),
              "@hover": mix(palette.color(QPalette.ColorRole.Button), text, 0.08)}
    sheet = STYLESHEET.replace("@chevron", CHEVRON.replace(os.sep, "/"))   # Qt wants / in url(); @2x is found
    for token, color in shades.items():                 # longest first: @handlehover before @handle
        sheet = sheet.replace(token, color)
    return sheet


def apply_style(app: QApplication) -> None:
    app.setStyleSheet(stylesheet(app.palette()))


def make_chip(button: QWidget) -> None:
    """A filter chip: a pill that is filled when checked."""
    button.setProperty("chip", True)
