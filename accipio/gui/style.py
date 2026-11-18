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

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPalette
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QWidget

from accipio.gui import appicon

WARNING_COLOR = "#BA7517"           # amber, readable on light and dark backgrounds
LINK_COLOR = "#8E5BD9"              # violet: "another Accipio is involved" (answers 108A, 109A); see link_tint
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
QMenu::item:disabled { color: @disabled; }
QMenu::item:disabled:selected { background: transparent; color: @disabled; }
QMenu::separator { height: 1px; background: @border; margin: 4px 8px; }
QToolTip { border: 1px solid @border; border-radius: 6px; padding: 4px 6px; }
"""


def mix(under: QColor, over: QColor, amount: float) -> str:
    """``over`` laid on ``under`` at ``amount`` (0..1), as #rrggbb."""
    def channel(a: int, b: int) -> int:
        return round(a + (b - a) * amount)
    return QColor(channel(under.red(), over.red()), channel(under.green(), over.green()),
                  channel(under.blue(), over.blue())).name()


def link_tint(palette: QPalette) -> QColor:
    """The faint wash behind a row or a list item that belongs to another Accipio (answer 105B): a little of the
    link colour mixed into the palette's own background, so it reads in dark and in light."""
    return QColor(mix(palette.color(QPalette.ColorRole.Base), QColor(LINK_COLOR), 0.16))


def link_bar(palette: QPalette) -> QColor:
    """The link colour for a progress bar (answer 106B): a little of the text colour mixed in, which lifts it
    on the dark palette and deepens it on the light one."""
    return QColor(mix(QColor(LINK_COLOR), palette.color(QPalette.ColorRole.Text), 0.12))


def paused_bar(palette: QPalette) -> QColor:
    """The fill of a paused download's progress bar (card 14): grey, mixed from the palette, so the accent colour
    always means "moving now"."""
    return QColor(mix(palette.color(QPalette.ColorRole.Base), palette.color(QPalette.ColorRole.Text), 0.42))


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def notice_colors(palette: QPalette) -> tuple:
    """(background, text) of a notice bar, as #rrggbb (card 16): a pale amber wash with the palette's own text in
    light mode, the deep brown with pale text in dark mode."""
    if is_dark(palette):
        return "#5c4a14", "#fbe9b7"
    return mix(palette.color(QPalette.ColorRole.Window), QColor("#E6A817"), 0.30), \
        palette.color(QPalette.ColorRole.Text).name()


def plain_page(page: QWidget) -> QWidget:
    """A tab page painted in the window's own colour. Fusion paints a tab pane a shade lighter than the window in
    dark mode, so pages that show the pane looked different from a page that fills itself (card 21)."""
    page.setAutoFillBackground(True)
    page.setBackgroundRole(QPalette.ColorRole.Window)
    return page


class TintDelegate(QStyledItemDelegate):
    """Paints an item's own background (its BackgroundRole brush) before the item itself. The stylesheet below
    styles ``::item``, and Qt then no longer paints that brush, so a tinted row would stay plain without this.
    A selected item keeps the normal selection colour. ``radius`` rounds the wash, for the lists' rounded items."""

    def __init__(self, parent=None, radius: float = 0.0):
        super().__init__(parent)
        self.radius = radius

    def paint_tint(self, painter, option, index) -> None:
        brush = index.data(Qt.ItemDataRole.BackgroundRole)
        if not isinstance(brush, QBrush) or brush.style() == Qt.BrushStyle.NoBrush \
                or option.state & QStyle.StateFlag.State_Selected:
            return
        painter.save()
        if self.radius:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(brush)
            painter.drawRoundedRect(QRectF(option.rect), self.radius, self.radius)
        else:
            painter.fillRect(option.rect, brush)
        painter.restore()

    def paint(self, painter, option, index):
        self.paint_tint(painter, option, index)
        super().paint(painter, option, index)


def stylesheet(palette: QPalette) -> str:
    background = palette.color(QPalette.ColorRole.Base)
    text = palette.color(QPalette.ColorRole.Text)
    shades = {"@handlehover": mix(background, text, 0.45), "@handle": mix(background, text, 0.28),
              "@disabled": mix(palette.color(QPalette.ColorRole.Window), text, 0.38),   # a switched-off menu line (card 8)
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
