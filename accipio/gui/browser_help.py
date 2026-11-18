"""Getting the browser extension in (picked 2026-11-04: 18B, a bar across the main window, opening 7C's box).

Until the extension is in the Chrome Web Store it is loaded by hand, once: Developer mode, Load unpacked, pick
a folder. Chrome will not let another program open its extensions page, so the box gives the address and the
folder to copy instead. The bar shows until the extension has talked to Accipio once (settings
``browser_connected``); Tools > Connect Browser opens the box any time.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QEvent, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QToolButton, QVBoxLayout)

from accipio.gui import style

EXTENSIONS_PAGE = "chrome://extensions"
HELP_PAGE = "connect-browser.html"
WIDTH = 620                 # the Connect window's width; its height follows from the text at that width
BUTTON_TEXT_DROP = 5        # px from a button's top edge to its text: a paragraph beside it starts that far down


def resource_dir() -> str:
    """Where the program's own files are: _internal in the exe, the checkout otherwise."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def extension_folder() -> str:
    return os.path.join(resource_dir(), "extension")


def firefox_extension_file() -> str:
    """The extension as Mozilla signed it (extension-firefox/, committed and bundled: the user's pick A, 2026-11-17).
    Firefox installs it from this file and keeps its own copy."""
    return os.path.join(resource_dir(), "extension-firefox", "accipio-firefox.xpi")


def help_page() -> str:
    return os.path.join(resource_dir(), "accipio", "gui", "help", HELP_PAGE)


class ConnectDialog(QDialog):
    """The three steps, each with what to copy."""

    def __init__(self, parent=None, folder: str = "", firefox_file: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Connect your browser")
        self.folder = folder or extension_folder()
        layout = QVBoxLayout(self)
        intro = QLabel("One step is left, once: add Accipio's extension to your browser (each one you download "
                       "with). Then the downloads you click there come to Accipio. The three steps are for Chrome, "
                       "Edge, Opera, Opera GX, Brave and Vivaldi.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.copy_page = QPushButton("Copy")
        self.copy_page.clicked.connect(lambda: self._copy(EXTENSIONS_PAGE, self.copy_page))
        self.copy_folder = QPushButton("Copy folder")
        self.copy_folder.clicked.connect(lambda: self._copy(self.folder, self.copy_folder))
        steps = [
            (f"Paste <b>{EXTENSIONS_PAGE}</b> into the browser's address bar (it works in all of "
             "these browsers).", self.copy_page),
            ("Switch on <b>Developer mode</b> (top right of that page).", None),
            ("Click <b>Load unpacked</b> and paste this folder into the box that opens:<br>"
             f"<code>{self.folder}</code>", self.copy_folder),
        ]
        grid.setVerticalSpacing(10)
        for row, (text, button) in enumerate(steps):
            number = QLabel(f"<b>{row + 1}</b>")
            grid.addWidget(self._beside_button(number), row, 0)
            grid.addWidget(self._beside_button(self._paragraph(text)), row, 1)     # fills its cell: it wraps to the width
            if button is not None:
                grid.addWidget(button, row, 2, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        # Firefox: one signed file instead of the three steps
        self.firefox_file = firefox_file or firefox_extension_file()
        self.copy_firefox = QPushButton("Copy file")
        self.copy_firefox.clicked.connect(lambda: self._copy(self.firefox_file, self.copy_firefox))
        firefox = self._paragraph(
            "<b>Firefox</b> takes one file instead: open the Add-ons page (Ctrl+Shift+A), click the "
            "cogwheel, choose <b>Install Add-on From File</b> and pick this file:<br>"
            f"<code>{self.firefox_file}</code><br>After an Accipio update, do this again: Firefox keeps "
            "its own copy of the file.")
        self.firefox_row_shown = os.path.isfile(self.firefox_file)
        if self.firefox_row_shown:                        # a copy without the signed file says nothing about Firefox
            row = grid.rowCount()                         # in the same grid: its button lines up with the others
            grid.addWidget(self._beside_button(firefox), row, 0, 1, 2)
            grid.addWidget(self.copy_firefox, row, 2, Qt.AlignmentFlag.AlignTop)
        else:
            self.copy_firefox.setParent(self)
            self.copy_firefox.hide()
        after = QLabel("Accipio then appears in the list of extensions. Its toolbar button switches the hand-over "
                       "off and on. If Free Download Manager or another download manager has an extension, switch "
                       "that one off: both would try to take the same downloads.")
        after.setWordWrap(True)
        after.setEnabled(False)
        layout.addWidget(after)
        buttons = QDialogButtonBox()
        later = buttons.addButton("Later", QDialogButtonBox.ButtonRole.RejectRole)
        later.clicked.connect(self.reject)
        page = buttons.addButton("Open help page", QDialogButtonBox.ButtonRole.ActionRole)
        page.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(help_page())))
        page.setEnabled(os.path.isfile(help_page()))
        layout.addWidget(buttons)
        # the width first, then the height the wrapped text needs at that width (card 22): the other way round the
        # text was measured narrow and the window had empty bands
        layout.activate()
        width = max(WIDTH, layout.totalMinimumSize().width())      # a long folder path cannot wrap: it sets the width
        self.resize(width, max(layout.totalHeightForWidth(width), layout.totalMinimumSize().height()))

    @staticmethod
    def _paragraph(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        return label

    @staticmethod
    def _beside_button(label: QLabel) -> QLabel:
        """Text in a row that has a button: pushed down so its first line is level with the button's own text."""
        label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label.setContentsMargins(0, BUTTON_TEXT_DROP, 0, 0)
        return label

    def _copy(self, text: str, button: QPushButton) -> None:
        QGuiApplication.clipboard().setText(text)
        button.setText("Copied")


class BrowserBar(QFrame):
    """"Clicked downloads don't reach Accipio yet. Connect your browser…" with a ✕ for this session."""
    connect_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("browserBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 4, 4)
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self._colouring = False
        self.colour()
        self.text.linkActivated.connect(lambda _: self.connect_clicked.emit())
        layout.addWidget(self.text, 1)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Hide until Accipio starts again")
        close.setAutoRaise(True)
        close.clicked.connect(self.hide)
        layout.addWidget(close)

    def colour(self) -> None:
        """The bar's colours from the palette in use (card 16): a pale amber wash in light mode, the deep brown in
        dark mode. Fixed colours made it the heaviest thing in a light window."""
        background, text = style.notice_colors(QApplication.palette())
        self._colouring = True
        self.setStyleSheet(f"#browserBar {{ background: {background}; border-radius: 6px; }} "
                           f"#browserBar QLabel {{ color: {text}; }} #browserBar QToolButton {{ color: {text}; }}")
        self.text.setText("Clicked downloads don't reach Accipio yet. "
                          f"<a href='connect' style='color:{text}'>Connect your browser…</a>")
        self._colouring = False

    def changeEvent(self, event):
        if event.type() == QEvent.Type.ApplicationPaletteChange and not self._colouring:
            self.colour()                                 # Windows switched between light and dark
        super().changeEvent(event)
