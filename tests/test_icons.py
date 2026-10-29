"""The program icon: made from the SVG, committed, and pointed at by the exe, the window and the extension."""
import json
import os
import struct

from accipio.gui import appicon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_ico_holds_every_size_as_png():
    with open(appicon.ICO, "rb") as f:
        data = f.read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, len(appicon.SIZES))
    sizes = []
    for i in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(data)
        sizes.append(width or 256)
    assert tuple(sizes) == appicon.SIZES


def test_the_extension_icons_exist():
    with open(os.path.join(ROOT, "extension", "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    paths = list(manifest["icons"].values()) + list(manifest["action"]["default_icon"].values())
    for path in paths:
        with open(os.path.join(ROOT, "extension", path), "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n", path


def test_the_exe_and_the_window_use_the_icon():
    with open(os.path.join(ROOT, "accipio.spec"), encoding="utf-8") as f:
        spec = f.read()
    assert 'icon="accipio/gui/appicon/accipio.ico"' in spec and '"accipio/gui/appicon"' in spec
    with open(os.path.join(ROOT, "accipio", "gui", "app.py"), encoding="utf-8") as f:
        assert "icon_path=appicon.ICO" in f.read()
