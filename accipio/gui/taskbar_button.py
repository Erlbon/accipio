"""Hands ``core.taskbar.Look`` to Windows: the progress bar on the taskbar button (ITaskbarList3) and the count
badge (an overlay icon drawn here). Qt 6 has no taskbar class, so this talks to the shell over ctypes. Anywhere
else, or when the shell refuses, it does nothing: the button is a convenience, never a reason to fail.
"""
from __future__ import annotations

import ctypes
import sys
from typing import Optional

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter

from accipio.core import taskbar

CLSID_TASKBAR_LIST = "{56FDF344-FD6D-11d0-958A-006097C9A090}"
IID_TASKBAR_LIST3 = "{ea1afb91-9e28-4b86-90e9-9e9f8a5eefaf}"
STATES = {taskbar.NONE: 0, taskbar.SLIDING: 1, taskbar.NORMAL: 2, taskbar.ERROR: 4, taskbar.PAUSED: 8}
HR_INIT, SET_PROGRESS_VALUE, SET_PROGRESS_STATE, SET_OVERLAY_ICON = 3, 9, 10, 18     # vtable slots
BADGE_SIZE = 32
BADGE_COLOURS = {                     # fill, text: the mockup's blue, amber and red
    taskbar.NORMAL: ("#E6F1FB", "#0C447C"), taskbar.SLIDING: ("#E6F1FB", "#0C447C"),
    taskbar.PAUSED: ("#FAEEDA", "#633806"), taskbar.ERROR: ("#FCEBEB", "#791F1F"),
}


def badge_image(text: str, mode: str) -> QImage:
    """The count in a round badge, for the corner of the taskbar button."""
    fill, ink = BADGE_COLOURS.get(mode, BADGE_COLOURS[taskbar.NORMAL])
    image = QImage(BADGE_SIZE, BADGE_SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor(ink))
    painter.setBrush(QColor(fill))
    painter.drawEllipse(QRectF(1, 1, BADGE_SIZE - 2, BADGE_SIZE - 2))
    font = QFont()
    font.setPixelSize(20 if len(text) == 1 else 15)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, BADGE_SIZE, BADGE_SIZE), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return image


class _Guid(ctypes.Structure):
    _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_Guid":
        guid = cls()
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
        return guid


class TaskbarButton:
    def __init__(self, window):
        self.window = window
        self._list: Optional[ctypes.c_void_p] = None
        self._shown: Optional[tuple] = None             # (hwnd, look) Windows last accepted
        self._icon = 0                                   # the badge Windows holds now; freed when replaced
        if sys.platform == "win32":
            try:
                self._list = self._create()
            except OSError:
                self._list = None

    @staticmethod
    def _create() -> ctypes.c_void_p:
        ole32 = ctypes.windll.ole32
        ole32.CoInitialize(None)                          # Qt has done it already; this only says "yes, still"
        pointer = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_Guid.of(CLSID_TASKBAR_LIST)), None, 1,
                                    ctypes.byref(_Guid.of(IID_TASKBAR_LIST3)), ctypes.byref(pointer))
        if hr != 0 or not pointer.value:
            raise OSError(f"no taskbar list ({hr:#x})")
        TaskbarButton._call(pointer, HR_INIT)
        return pointer

    @staticmethod
    def _call(pointer, slot: int, *args) -> int:
        """Call one method of the COM object; returns its HRESULT (0 = done)."""
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        types = [ctypes.c_void_p] + [type(a) for a in args]
        method = ctypes.WINFUNCTYPE(ctypes.c_long, *types)(table[slot])
        return method(pointer, *args)

    def show(self, look: taskbar.Look) -> bool:
        """True once Windows has it, or when there is no taskbar to tell; False means try again later (the
        window has no button yet)."""
        if self._list is None:
            return True
        if not self.window.isVisible():
            return False
        hwnd = ctypes.c_void_p(int(self.window.winId()))
        if self._shown == (hwnd.value, look):
            return True
        done = 0
        if look.mode in (taskbar.NORMAL, taskbar.PAUSED, taskbar.ERROR):
            done |= self._call(self._list, SET_PROGRESS_VALUE, hwnd, ctypes.c_ulonglong(look.percent),
                               ctypes.c_ulonglong(100))
        done |= self._call(self._list, SET_PROGRESS_STATE, hwnd, ctypes.c_int(STATES[look.mode]))
        done |= self._badge(hwnd, look)
        self._shown = (hwnd.value, look) if done == 0 else None
        return done == 0

    def _badge(self, hwnd, look: taskbar.Look) -> int:
        text = taskbar.badge_text(look.count)
        icon = int(badge_image(text, look.mode).toHICON()) if text else 0
        hr = self._call(self._list, SET_OVERLAY_ICON, hwnd, ctypes.c_void_p(icon or None),
                        ctypes.c_wchar_p(look.title if text else None))
        old, self._icon = (self._icon, icon) if hr == 0 else (icon, self._icon)
        if old:
            ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(old))
        return hr

    def close(self) -> None:
        if self._icon:
            ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(self._icon))
            self._icon = 0
        if self._list is not None:
            self._call(self._list, 2)                   # Release
            self._list = None
