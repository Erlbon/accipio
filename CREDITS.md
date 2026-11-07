# Credits

Accipio is built on the work of a number of other projects. Accipio's own source code is MIT-licensed; because the
Windows build contains GPL-3.0 libraries (PyQt6 and redactor_common), the program as a whole is distributed under
the GPL-3.0, and the source of everything in it is public.

## Shared foundation

- **[redactor_common](https://github.com/Erlbon/redactor_common)** (GPL-3.0-or-later): the menus, theme, dialogs,
  tool finder and update check shared with the Redactor apps.

## Downloading

- **[aria2](https://aria2.github.io/)** 1.37.0 (GPL-2.0): does the direct downloads. It is a separate program
  (`aria2c.exe`), run by Accipio and never linked into it. Its licence texts are in `_internal\aria2`; its source is
  at <https://github.com/aria2/aria2/releases/tag/release-1.37.0>.
- **[yt-dlp](https://github.com/yt-dlp/yt-dlp)** (Unlicense) and **yt-dlp-ejs** (Unlicense, MIT, ISC): fetch videos
  from YouTube and other video sites.
- **[ffmpeg](https://ffmpeg.org/)** (GPL or LGPL builds): joins picture and sound from video sites and converts
  audio to mp3. It is not bundled; Accipio uses one you install (the installer can fetch it with winget).

## Libraries

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** (GPL-3.0) and **[Qt 6](https://www.qt.io/)**
  (LGPL-3.0): the window and everything in it.
- **[Python](https://www.python.org/)** 3.15 (PSF licence).
- **[PyWinRT](https://github.com/pywinrt/pywinrt)** (MIT): hands Accipio's notices to Windows, each with its own sound.
- Libraries yt-dlp uses: **requests** (Apache-2.0), **urllib3** and **brotli** (MIT), **websockets** and
  **pycryptodomex** (BSD), **certifi** (MPL-2.0), **mutagen** (GPL-2.0-or-later).

## Tools

- **[Inno Setup](https://jrsoftware.org/isinfo.php)**: builds the Windows installer.
- **[PyInstaller](https://pyinstaller.org/)**: turns the program into `Accipio.exe`.
