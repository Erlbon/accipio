"""Make accipio.ico, accipio-setup.ico, accipio-256.png and the extension's icons from art/accipio-glove.png.

    python tools/make_icons.py

The artwork is the glove alone on a transparent background, upright. Each icon tilts it (it is tall and thin,
and tilted it fills the square), puts it on a teal tile so it reads on light and dark taskbars, and draws
it four times larger than wanted before one smooth scale-down, which keeps the small sizes as sharp as a
bitmap allows. The installer's icon adds a box badge in the corner (Q62A).

An .ico is a small directory followed by one image per size; each image here is a PNG (the form Windows
Vista and later read), so the file needs nothing but Qt.
"""
from __future__ import annotations

import os
import struct
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QTransform  # noqa: E402

from accipio.gui import appicon  # noqa: E402

EXTENSION_ICONS = os.path.join(ROOT, "extension", "icons")
ARTWORK = os.path.join(ROOT, "art", "accipio-glove.png")
TILE = QColor("#0F6E56")            # teal; the glove's beige reads on it, and it reads on light and dark taskbars
TILT = 35                           # degrees clockwise; upright the glove fills under half the square
GLOVE_SHARE = 0.80                  # of the tile's side the tilted glove may take
OVERSAMPLE = 4


def _crop_to_ink(image: QImage) -> QImage:
    """Cut away the empty corners turning leaves, so the glove, not its old rectangle, is what gets fitted."""
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    bits = image.constBits()
    bits.setsize(image.sizeInBytes())
    data = bytes(bits)
    rows = [y for y in range(height) if any(data[y * stride + 3:y * stride + 4 * width:4])]
    columns = [x for x in range(width) if any(data[x * 4 + 3 + y * stride] for y in range(rows[0], rows[-1] + 1, 2))]
    return image.copy(columns[0], rows[0], columns[-1] - columns[0] + 1, rows[-1] - rows[0] + 1)


def _glove() -> QImage:
    image = QImage(ARTWORK)
    if image.isNull():
        raise SystemExit(f"cannot read {ARTWORK}")
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return _crop_to_ink(image.transformed(QTransform().rotate(TILT), Qt.TransformationMode.SmoothTransformation))


def _badge(painter: QPainter, side: float) -> None:
    """The installer's mark: a small box (a package) on a light square in the lower right corner."""
    s = side * 0.46
    x0, y0 = side - s, side - s
    painter.setPen(QPen(QColor("#444441"), max(1.0, s * 0.06)))
    painter.setBrush(QColor("#F1EFE8"))
    painter.drawRoundedRect(QRectF(x0 + s * 0.03, y0 + s * 0.03, s * 0.94, s * 0.94), s * 0.2, s * 0.2)
    pen = QPen(QColor("#444441"), max(1.0, s * 0.07))
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    cx, top, w, h = x0 + s / 2, y0 + s * 0.22, s * 0.28, s * 0.14
    box = QPainterPath(QPointF(cx - w, top + h))
    for x, y in ((cx, top), (cx + w, top + h), (cx + w, top + h + s * 0.32), (cx, top + 2 * h + s * 0.32),
                 (cx - w, top + h + s * 0.32), (cx - w, top + h), (cx, top + 2 * h), (cx + w, top + h)):
        box.lineTo(QPointF(x, y))
    box.moveTo(QPointF(cx, top + 2 * h))
    box.lineTo(QPointF(cx, top + 2 * h + s * 0.32))
    painter.drawPath(box)


def render(size: int, badge: bool = False, glove: QImage = None) -> QImage:
    glove = glove if glove is not None else _glove()
    big = size * OVERSAMPLE
    image = QImage(big, big, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    margin = big / 64
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(TILE)
    painter.drawRoundedRect(QRectF(margin, margin, big - 2 * margin, big - 2 * margin), big / 5, big / 5)
    fit = big * GLOVE_SHARE
    scale = fit / max(glove.width(), glove.height())
    w, h = glove.width() * scale, glove.height() * scale
    painter.drawImage(QRectF((big - w) / 2, (big - h) / 2, w, h), glove)
    if badge:
        _badge(painter, big)
    painter.end()
    return image.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def png_bytes(size: int, badge: bool = False, glove: QImage = None) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render(size, badge, glove).save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: str, sizes, badge: bool = False) -> None:
    glove = _glove()
    images = [(s, png_bytes(s, badge, glove)) for s in sizes]
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
    write_ico(appicon.SETUP_ICO, appicon.SIZES, badge=True)
    glove = _glove()
    with open(appicon.PNG, "wb") as f:
        f.write(png_bytes(256, glove=glove))
    os.makedirs(EXTENSION_ICONS, exist_ok=True)
    for size in appicon.EXTENSION_SIZES:
        with open(os.path.join(EXTENSION_ICONS, f"icon-{size}.png"), "wb") as f:
            f.write(png_bytes(size, glove=glove))
    print("wrote", appicon.ICO, appicon.SETUP_ICO, appicon.PNG, "and", EXTENSION_ICONS)
    del app


if __name__ == "__main__":
    main()
