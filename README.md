# Accipio

A download manager that puts each file where you want it.

Accipio ("I receive") sends every download through an ordered list of rules you set up yourself:
by domain, file type, name, size, time of day, or the channel a video comes from. Folders can rotate
by date, and missing folders are created on the fly. Direct downloads run on aria2, video sites on
yt-dlp, and a Chrome/Edge extension hands your clicked downloads straight to the app.

## Install (Windows)

1. Download `Accipio-<build>-windows-x64-setup.exe` from [Releases](https://github.com/Erlbon/accipio/releases)
   and run it. It installs for your user only (`%LOCALAPPDATA%\Programs\Accipio`, no admin rights), points
   Chrome and Edge at Accipio, and can also start Accipio when you sign in and install ffmpeg (below).
2. Load the extension, once: tick **Add the browser extension now** on the last page of the installer. It
   opens the extensions page and the extension's folder. Switch on **Developer mode**, choose **Load
   unpacked** and pick that folder (`_internal\extension` in the Accipio folder). Clicked downloads now go to
   Accipio; the extension's toolbar button switches that off and on.
3. Set your rules under **Tools > Rules**. Every download shows which rule placed it (hover it, or
   **Downloads > Why Here?**).

To update, run the newer installer: it closes Accipio, replaces the program and keeps your rules, settings and
downloads (they live in `%APPDATA%\Accipio`). Uninstall from Windows' Settings > Apps; it asks whether to delete
those too.

**Without installing:** `Accipio-<build>-windows-x64.zip` holds the same program. Unzip it anywhere, keep the whole
folder (`Accipio.exe` needs `_internal` beside it), start `Accipio.exe` and load the extension as above. If you move
that folder later, start `Accipio.exe` once from the new place so the browser link follows.

If Free Download Manager, Internet Download Manager or another download manager has a browser extension, switch
that extension off (or Accipio's): both try to take the same downloads.

### ffmpeg (for video sites)

YouTube and most big video sites send picture and sound separately; ffmpeg joins them. Accipio does not
bundle it, but the installer offers to fetch it with winget when none is found. Without ffmpeg you get the best single file the site offers (often lower quality), and "Audio
only" keeps the site's own sound format instead of mp3. To get full quality, install ffmpeg once, for
example with `winget install Gyan.FFmpeg`, or put `ffmpeg.exe` in a folder named `ffmpeg` next to
`Accipio.exe`. Accipio also finds it on PATH and in the usual install folders.

### Keeping video sites working

Video sites change often. **Tools > Update yt-dlp** fetches the newest yt-dlp without a new Accipio
release; it is used from the next start.

## Run from source

```powershell
python tools\fetch_engines.py      # aria2, once per clone
python -m accipio                  # also registers the browser link for Chrome and Edge
```

Then load the `extension` folder unpacked as above.

## Build the exe

Run `build_exe.bat` (Windows). It installs what the build needs, fetches aria2 if it is missing, and makes
`dist\Accipio\Accipio.exe`: a folder build (about 85 MB), so copy the whole `Accipio` folder. Starting the exe
points the browser link at it. When Inno Setup 6 is installed (`winget install JRSoftware.InnoSetup`) it also
makes the installer, `dist\Accipio-<build>-windows-x64-setup.exe`, from `installer\accipio.iss`.

## Status

Direct downloads, the rules, the window, video sites, the browser link, notifications and yt-dlp updates work.
Not yet: running a command when a download finishes (the rules editor hides that field until it does),
schedules, torrents, and a Chrome Web Store listing. See [docs/design.md](docs/design.md) and
[CHANGELOG.md](CHANGELOG.md).

## Licences

Accipio's own source code is MIT-licensed (see [LICENSE](LICENSE)). The Windows build also contains
GPL-3.0 libraries (PyQt6 and redactor_common), so the exe as a whole is distributed under the GPL-3.0; the
source of everything in it is public. The build includes:

- **aria2** 1.37.0 (GPL-2.0), run as a separate program, never linked. Its licence texts are in
  `_internal\aria2`; source: <https://github.com/aria2/aria2/releases/tag/release-1.37.0>.
- **PyQt6** (GPL-3.0) and **Qt 6** (LGPL-3.0), <https://www.riverbankcomputing.com/software/pyqt/>.
- **redactor_common** (GPL-3.0-or-later), <https://github.com/Erlbon/redactor_common>.
- **yt-dlp** (Unlicense), <https://github.com/yt-dlp/yt-dlp>, and **yt-dlp-ejs** (Unlicense, MIT, ISC).
- Python 3.15 (PSF licence) and smaller libraries yt-dlp uses: requests (Apache-2.0), urllib3 and brotli
  (MIT), websockets and pycryptodomex (BSD), certifi (MPL-2.0), mutagen (GPL-2.0-or-later).
