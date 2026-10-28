# Accipio

A download manager that puts each file where you want it.

Accipio ("I receive") sends every download through an ordered list of rules you set up yourself:
by domain, file type, name, size, time of day, or the channel a video comes from. Folders can rotate
by date, and missing folders are created on the fly. Direct downloads run on aria2, video sites on
yt-dlp, and a Chrome extension hands your clicked downloads straight to the app.

Status: in development. Direct downloads, the rules and the window work (`python -m accipio`, after `python tools/fetch_engines.py`); yt-dlp and the Chrome extension come next. See [docs/design.md](docs/design.md) and [CHANGELOG.md](CHANGELOG.md).
