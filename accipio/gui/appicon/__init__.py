"""The program icon: a white arrow landing in a tray on a blue tile (mockup A, picked 2026-10-29).

``accipio.svg`` is the master. ``accipio.ico`` (seven sizes, for the exe, the window, the taskbar and the tray),
``accipio-256.png`` and the extension's ``icons/icon-*.png`` are made from it by ``tools/make_icons.py`` and
committed, so a build needs no extra step. The program itself only reads the .ico, so the exe needs no Qt Svg.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
SVG = os.path.join(HERE, "accipio.svg")
ICO = os.path.join(HERE, "accipio.ico")
PNG = os.path.join(HERE, "accipio-256.png")
SIZES = (16, 24, 32, 48, 64, 128, 256)
EXTENSION_SIZES = (16, 32, 48, 128)
