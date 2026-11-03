# Accipio

A download manager that puts each file where you want it.

Accipio ("I receive") sends every download through an ordered list of rules you set up yourself:
by domain, file type, name, size, time of day, or the channel a video comes from. Folders can rotate
by date, and missing folders are created on the fly. Direct downloads run on aria2, video sites on
yt-dlp, and a Chrome/Edge extension hands your clicked downloads straight to the app.

## Install (Windows)

1. Download `Accipio-<build>-windows-x64.zip` from [Releases](https://github.com/Erlbon/accipio/releases) and
   unzip it anywhere, for example `C:\Programs\Accipio`. Keep the whole folder: `Accipio.exe` needs the
   `_internal` folder beside it.
2. Start `Accipio.exe`. The first start points Chrome and Edge at it (for your user only, no admin rights).
3. Load the extension: open `chrome://extensions` (or `edge://extensions`), switch on **Developer mode**,
   choose **Load unpacked** and pick the `_internal\extension` folder inside the Accipio folder. Clicked
   downloads now go to Accipio; the extension's toolbar button switches that off and on.
4. Set your rules under **Tools > Rules**. Every download shows which rule placed it (hover it, or
   **Downloads > Why Here?**).

If you move the Accipio folder later, start `Accipio.exe` once from the new place so the browser link follows.

### ffmpeg (for video sites)

YouTube and most big video sites send picture and sound separately; ffmpeg joins them. Accipio does not
bundle it. Without ffmpeg you get the best single file the site offers (often lower quality), and "Audio
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
points the browser link at it.

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
