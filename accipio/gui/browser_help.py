"""Getting the browser extension in (picked 2026-11-04: 18B, a bar across the main window, opening 7C's box).

Until the extension is in the Chrome Web Store it is loaded by hand, once: Developer mode, Load unpacked, pick
a folder. Chrome will not let another program open its extensions page, so the box gives the address and the
folder to copy instead. The bar shows until the extension has talked to Accipio once (settings
``browser_connected``); Tools > Connect Browser opens the box any time.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                             QToolButton, QVBoxLayout)

EXTENSIONS_PAGE = "chrome://extensions"
HELP_PAGE = "connect-browser.html"


def resource_dir() -> str:
    """Where the program's own files are: _internal in the exe, the checkout otherwise."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def extension_folder() -> str:
    return os.path.join(resource_dir(), "extension")


def help_page() -> str:
    return os.path.join(resource_dir(), "accipio", "gui", "help", HELP_PAGE)


class ConnectDialog(QDialog):
    """The three steps, each with what to copy."""

    def __init__(self, parent=None, folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Connect your browser")
        self.folder = folder or extension_folder()
        layout = QVBoxLayout(self)
        intro = QLabel("One step is left, once: add Accipio's extension to Chrome or Edge. Then the downloads you "
                       "click there come to Accipio.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self.copy_page = QPushButton("Copy")
        self.copy_page.clicked.connect(lambda: self._copy(EXTENSIONS_PAGE, self.copy_page))
        self.copy_folder = QPushButton("Copy folder")
        self.copy_folder.clicked.connect(lambda: self._copy(self.folder, self.copy_folder))
        steps = [
            (f"Paste <b>{EXTENSIONS_PAGE}</b> into the browser's address bar (Edge: edge://extensions).", self.copy_page),
            ("Switch on <b>Developer mode</b> (top right of that page).", None),
            ("Click <b>Load unpacked</b> and paste this folder into the box that opens:<br>"
             f"<code>{self.folder}</code>", self.copy_folder),
        ]
        for row, (text, button) in enumerate(steps):
            number = QLabel(f"<b>{row + 1}</b>")
            number.setAlignment(Qt.AlignmentFlag.AlignTop)
            label = QLabel(text)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(number, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label, row, 1, Qt.AlignmentFlag.AlignTop)
            if button is not None:
                grid.addWidget(button, row, 2, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
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
        self.resize(620, self.sizeHint().height())

    def _copy(self, text: str, button: QPushButton) -> None:
        QGuiApplication.clipboard().setText(text)
        button.setText("Copied")


class BrowserBar(QFrame):
    """"Clicked downloads don't reach Accipio yet. Connect your browser…" with a ✕ for this session."""
    connect_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("browserBar")
        self.setStyleSheet("#browserBar { background: #5c4a14; border-radius: 6px; } "
                           "#browserBar QLabel { color: #fbe9b7; } #browserBar QToolButton { color: #fbe9b7; }")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 4, 4)
        self.text = QLabel("Clicked downloads don't reach Accipio yet. "
                           "<a href='connect' style='color:#fbe9b7'>Connect your browser…</a>")
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.linkActivated.connect(lambda _: self.connect_clicked.emit())
        layout.addWidget(self.text, 1)
        close = QToolButton()
        close.setText("✕")
        close.setToolTip("Hide until Accipio starts again")
        close.setAutoRaise(True)
        close.clicked.connect(self.hide)
        layout.addWidget(close)
