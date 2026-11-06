"""The program icon: made from the glove artwork, committed, and pointed at by the exe, the window, the extension
and (with a box badge) the installer."""
import json
import os
import struct

from accipio.gui import appicon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ico_sizes(path):
    with open(path, "rb") as f:
        data = f.read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = []
    for i in range(count):
        width, _, _, _, _, _, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert data[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and offset + length <= len(data)
        sizes.append(width or 256)
    return tuple(sizes)


def test_the_ico_holds_every_size_as_png():
    assert ico_sizes(appicon.ICO) == appicon.SIZES


def test_the_installer_has_an_icon_of_its_own():
    """2026-11-06 (Q62A): setup.exe had the program's own icon, so a running Accipio looked like a setup still
    running. The installer's is the program's with a box badge."""
    assert ico_sizes(appicon.SETUP_ICO) == appicon.SIZES
    with open(appicon.ICO, "rb") as a, open(appicon.SETUP_ICO, "rb") as b:
        assert a.read() != b.read()
    with open(os.path.join(ROOT, "installer", "accipio.iss"), encoding="utf-8") as f:
        assert "SetupIconFile=accipio\\gui\\appicon\\accipio-setup.ico" in f.read()


def test_the_master_artwork_stays_out_of_the_exe():
    """The glove (Q63B) is drawn by hand at full size; only the .ico made from it is bundled."""
    assert os.path.isfile(os.path.join(ROOT, "art", "accipio-glove.png"))
    assert not [n for n in os.listdir(appicon.HERE) if n.endswith((".svg",)) or "glove" in n]


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
