# PyInstaller spec file for Accipio.
#
# Build on Windows with: python -m PyInstaller accipio.spec --noconfirm   (or build_exe.bat)
# PyInstaller builds for the platform it runs ON: a Windows .exe cannot be made from Linux or macOS.
#
# A FOLDER build (dist\Accipio\Accipio.exe + _internal), not one file: every download clicked in the browser starts the
# exe again as the native messaging host (Accipio.exe --native-host), and a one-file exe would unpack itself to a temp
# folder on each of those starts. aria2c.exe (fetched by tools\fetch_engines.py) goes into _internal\aria2 with its
# licence texts, run as a separate program, never linked. ffmpeg is not bundled: one in an "ffmpeg" folder next to
# the exe, on PATH or in the usual install folders is used (see accipio\core\video.py).
import os

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

ARIA2 = os.path.join("bin", "aria2")
if not os.path.isfile(os.path.join(ARIA2, "aria2c.exe")):
    raise SystemExit("bin\\aria2\\aria2c.exe is missing: run  python tools\\fetch_engines.py  first")

a = Analysis(
    ["accipio/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[
        (ARIA2, "aria2"),                                   # aria2c.exe, COPYING, LICENSE.OpenSSL, AUTHORS
        ("extension", "extension"),                         # the browser extension, to load unpacked from here
        ("accipio/gui/appicon", "accipio/gui/appicon"),     # the program icon (window, taskbar, tray)
        ("README.md", "."),
        ("CHANGELOG.md", "."),
        ("LICENSE", "."),
    ],
    # Found by name when they are needed, so the static analysis cannot see them: Accipio's lazily loaded parts and
    # yt-dlp's site extractors (it picks them by name at run time). redactor_common is NOT collected whole: its
    # preview players would drag in Qt Multimedia and Qt Pdf; the parts Accipio imports are found as usual.
    hiddenimports=collect_submodules("accipio") + collect_submodules("yt_dlp"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Not used by the program; PyInstaller would bundle them whenever they happen to be installed on the build machine.
    excludes=["numpy", "tkinter", "unittest", "pytest", "py7zr", "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
              "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml",
              "PyQt6.QtQuick", "PyQt6.QtSvg", "PyQt6.QtOpenGL", "PyQt6.QtOpenGLWidgets"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Qt files the window never uses: the software OpenGL fallback (20 MB; widgets draw without OpenGL), the video codecs
# and the translations (Accipio is in English).
UNUSED = ("opengl32sw.dll", "avcodec-", "avformat-", "avutil-", "swscale-", "swresample-", "qt6multimedia", "qt6pdf",
          "qt6quick", "qt6qml", "qt6svg", "qt6opengl", "qpdf", "qsvg")    # the last two: plugins of what is gone
a.binaries = [b for b in a.binaries if not any(os.path.basename(b[0]).lower().startswith(u.lower()) for u in UNUSED)]
a.datas = [d for d in a.datas if "translations" not in d[0].replace("\\", "/").split("/")]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Accipio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX-packed exes trip virus scanners and start slower; the folder is small anyway
    console=False,            # a window program; the native host still talks over the pipes Chrome hands it
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="accipio/gui/appicon/accipio.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Accipio",
)
