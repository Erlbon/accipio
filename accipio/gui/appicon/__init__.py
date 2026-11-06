"""The program icon: a work glove reaching to catch, tilted, on a teal tile (picked 2026-11-06, Q63B).

The old icon (an arrow landing in a tray) looked like a setup program still running. The master artwork is
``art/accipio-glove.png`` at the top of the repository, outside the package so the exe does not carry it.
``accipio.ico`` (seven sizes, for the exe, the window, the taskbar and the tray), ``accipio-256.png``,
``accipio-setup.ico`` (the same with a box badge, for the installer only: Q62A) and the extension's
``icons/icon-*.png`` are made from it by ``tools/make_icons.py`` and committed, so a build needs no extra step.
"""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "accipio.ico")
SETUP_ICO = os.path.join(HERE, "accipio-setup.ico")
PNG = os.path.join(HERE, "accipio-256.png")
SIZES = (16, 24, 32, 48, 64, 128, 256)
EXTENSION_SIZES = (16, 32, 48, 128)
