# Accipio

A download manager that puts each file where you want it.

Accipio ("I receive") sends every download through an ordered list of rules you set up yourself:
by domain, file type, name, size, time of day, or the channel a video comes from. Folders can rotate
by date, and missing folders are created on the fly. Direct downloads run on aria2, video sites on
yt-dlp, and a Chrome extension hands your clicked downloads straight to the app.

## Try it

```powershell
python tools\fetch_engines.py      # aria2, once per clone
python -m accipio                  # also registers the browser link for Chrome and Edge
```

Then load the extension: open `chrome://extensions` (or `edge://extensions`), switch on Developer mode,
choose **Load unpacked** and pick the `extension` folder. Clicked downloads now go to Accipio; the toolbar
button switches that off and on.

Status: in development. Direct downloads, the rules, the window, video sites and the browser link work (`python -m accipio`, after `python tools/fetch_engines.py`); the Chrome extension is in `extension`. See [docs/design.md](docs/design.md) and [CHANGELOG.md](CHANGELOG.md).
