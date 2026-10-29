"""Make accipio.ico, accipio-256.png and the extension's icons from accipio/gui/appicon/accipio.svg.

    python tools/make_icons.py

An .ico is a small directory followed by one image per size; each image here is a PNG (the form Windows
Vista and later read), so the file needs nothing but Qt. Every size is drawn from the SVG, never scaled down
from a bigger bitmap, so the small ones stay sharp.
"""
from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PyQt6.QtSvg import QSvgRenderer  # noqa: E402

from accipio.gui import appicon  # noqa: E402

EXTENSION_ICONS = os.path.join(ROOT, "extension", "icons")


def render(size: int) -> QImage:
    with open(appicon.SVG, "rb") as f:
        renderer = QSvgRenderer(QByteArray(f.read()))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(size: int) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render(size).save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: str, sizes) -> None:
    images = [(s, png_bytes(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    directory, data = b"", b""
    for size, png in images:
        side = 0 if size >= 256 else size                       # 0 means 256 in the directory
        directory += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    with open(path, "wb") as f:
        f.write(header + directory + data)


def write_chevron(path: str, scale: int) -> None:
    """The drop-down arrow for combo boxes (the stylesheet hides Fusion's own): 10 x 6, mid gray, which reads
    on the light and the dark palette alike."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtGui import QColor, QPen
    image = QImage(10 * scale, 6 * scale, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#8a8a8a"), 1.6 * scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.drawPolyline([QPointF(1.2 * scale, 1.2 * scale), QPointF(5 * scale, 4.8 * scale),
                          QPointF(8.8 * scale, 1.2 * scale)])
    painter.end()
    image.save(path, "PNG")


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    write_chevron(os.path.join(appicon.HERE, "chevron-down.png"), 1)
    write_chevron(os.path.join(appicon.HERE, "chevron-down@2x.png"), 2)
    write_ico(appicon.ICO, appicon.SIZES)
    with open(appicon.PNG, "wb") as f:
        f.write(png_bytes(256))
    os.makedirs(EXTENSION_ICONS, exist_ok=True)
    for size in appicon.EXTENSION_SIZES:
        with open(os.path.join(EXTENSION_ICONS, f"icon-{size}.png"), "wb") as f:
            f.write(png_bytes(size))
    print("wrote", appicon.ICO, appicon.PNG, "and", EXTENSION_ICONS)
    del app


if __name__ == "__main__":
    main()
