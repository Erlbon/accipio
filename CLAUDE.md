# Accipio: working notes for Claude

The README says what this is; `docs/design.md` holds the agreed design. This file holds the decisions so a new
session does not reopen what is settled. Keep it current when a decision changes.

## Decided (the user, 2026-10-28)
- **Name:** Accipio. Repo `Erlbon/accipio`, **private until the first release, public after.**
  The PyPI name is taken by a dead package; we ship exes from GitHub releases, not PyPI.
- **Built new**, not a Persepolis fork (see the design doc for why).
- **v1 includes** yt-dlp and the Chrome extension; the user downloads by clicking in Chrome.
- **Rules** are ordered by drag-and-drop, with stop/continue; time/date conditions and tokens, **no calendar integration**.
- **Time conditions are crontab underneath** (five-field cron, OR-ed lines), edited through friendly pickers
  that read back as a sentence; raw cron only in an Advanced field. The same engine drives schedules later.
- **No FDM import.** Start fresh.
- **Script action runs any command line** (tokens filled in, details also as `ACCIPIO_*` environment variables).
- **Licence: MIT.** aria2 (GPL-2.0) and ffmpeg (GPL/LGPL builds) are shipped as separate programs next to
  the exe, never linked or imported; the release must include their licence texts and say where their
  source is. yt-dlp is Unlicense; aria2p is ISC.
- **Mockups picked:** main window B (compact: paste bar, filter chips, one table with a Rule column,
  "why here?" on hover/right-click), rules editor A (drag list left, form right, test box at the bottom).
  Described in `docs/design.md`. Add box: mockup A (one row per link, folder per row, Put all in), 2026-10-28.

## Code layout
- `accipio/core` is Qt-free and has no disk access where it can be avoided: `rules.py` (the walk, conditions,
  tokens, `place()`), `cron.py` (time windows). The GUI, the engine and the CLI are thin layers over it.
- Templates (`rules.parse_template`): `{key|filter:arg|...}`, filters in `FILTERS`, run left to right; regex
  groups of a rule's name/video conditions are tokens for that rule only (`captures`); a rename updates
  `{name}`/`{ext}` for the rules below. Keep `FILTER_HELP` (the editor's tooltip) in step with `FILTERS`.
- Time conditions use AND for day-of-month + weekday (unlike classic cron); see the `cron.py` docstring.
- `aria2.py` is a small stdlib JSON-RPC client, not aria2p (fewer dependencies, smaller exe).
  **Never turn on aria2's `--continue`**: it appends to an existing file of the same name (a test pins this).
  `stop()` saves the session and does not wait (aria2c takes ~4 s to exit on Windows).
- `probe.py` finds the real name/type/size before a download (HEAD, else a one-byte GET).
- `store.py` (SQLite, WAL, one locked connection) + `queue.py` (probe -> rules -> engine; `refresh()` on a
  timer; sniff-and-re-place on finish via `sniff.py`). `Queue.add/preview` do network I/O: call them off
  the window's thread. Tests use a FakeEngine/FakeProber; one end-to-end test runs real aria2.
- GUI in `accipio/gui`: `main_window.py` (MainWindow + `open_services`, all queue calls on ONE worker thread via
  `run()`; `flush()` waits for it in tests), `queue_model.py`, `rules_dialog.py`, `settings_dialog.py`, `app.py`.
  Menus use redactor_common's `build_standard_menu_bar`; a test runs `lint_menu_bar` (canonical labels too).
  Window layout is kept in `window.ini` in the config folder, never QSettings' registry default.
- GUI tests run offscreen (`QT_QPA_PLATFORM=offscreen`, set in `tests/test_gui.py`); offscreen has no fonts,
  so for screenshots render with the normal platform and `widget.grab()` without showing the window.
- `video.py`: `is_video_site` (yt-dlp extractors minus Generic), `probe_video`, `VideoEngine` (same calls as
  `Aria2`: add/pause/resume/remove/restart/status/all/set_limits/stop; one thread per download, max N running).
  The queue picks the engine by `row.source` ("direct"/"video") via `engine_of(row)`. Tests never reach a real
  site: the engine is tested with a stand-in downloader and with real yt-dlp's generic extractor on the local
  server. Format sort prefers H.264 at equal resolution (AV1 does not play on stock Windows).
- ffmpeg: found through redactor_common's `tool_locator.find_tool` (as videoredactor does): bundled
  `bin/ffmpeg` / `ffmpeg/` next to the exe, PATH, install folders. Bundling it for releases is not decided yet.
- Browser link: `extension/` (MV3; fixed ID `ongnefecmhlaofpaaoehcdmdinnlgble` from the public `key` in its
  manifest; no private key is kept or needed for unpacked loading; a test checks key and ID agree).
  `accipio/native_host.py` is what Chrome starts (stdin/stdout framing); it relays over `core/ipc.py` (named
  pipe / Unix socket + key file) to the running app, starting the app if needed. `core/browser.py` registers
  the host (HKCU Chrome + Edge keys) at every app start; tests inject a fake registry, never the real one.
  `extension/package.json` only marks the JS as ES modules for `node --check` (the pre-commit hook).
- No modifier key for "let Chrome download it": extensions are not told about held keys on downloads.
  Instead: a "Download with Chrome instead" link menu item and the popup's takeover switch.
- Notifications (picked 2026-10-29: Windows notifications via a tray icon, not in-window toasts): `core/notices.py`
  diffs the rows on every `reload()` and groups each kind for `GATHER` seconds; the window shows them through
  `QSystemTrayIcon.showMessage` (`_show_notice`, which tests replace). Offscreen tests have no tray (`tray` is None).
- History of finished downloads is opt-in (`keep_history`, default off); headers/cookies are wiped on finish.
- The **command action is stored but not run yet**: running server-controlled names through a command line
  (especially .bat/.cmd) needs injection-safe quoting first.
- Exe: `accipio.spec` + `build_exe.bat` -> `dist\Accipio\` (FOLDER build on purpose: every browser click starts the
  exe as the native host, and a one-file exe would unpack itself each time). aria2 goes to `_internal\aria2`.
  The spec does not collect all of redactor_common (its preview players pull in Qt Multimedia/Pdf) and drops
  opengl32sw, the codecs and translations: 146 MB -> 85 MB. Verified 2026-10-29: window, aria2, yt-dlp + merge,
  host ping through the windowed exe (0.3 s), single instance. Running the exe re-registers the browser host
  to it; after testing a dist build with a scratch APPDATA, re-register from the checkout.
- Installer (picked 2026-11-04): Inno Setup, `installer\accipio.iss`, built by `build_exe.bat` when ISCC is found
  (version passed via `tools/installer_defines.py`). Per-user install `{autopf}` = `%LOCALAPPDATA%\Programs\Accipio`,
  no UAC; fixed AppId GUID (never change it, or updates install side by side). The browser link is written by
  `Accipio.exe --register` (same `browser.register`), removed by the uninstaller's `uninsdeletekey` entries.
  Tasks: desktop icon, "start with Windows" (HKCU Run, `--minimized`, off by default), ffmpeg via winget (shown only
  when no ffmpeg is found; `video.find_ffmpeg` also looks in winget's Links folder). Extension: setup opens the
  extensions page + the folder on a first install (Web Store install via the HKCU `Extensions` key once listed).
  Uninstall: graceful `taskkill`, waits for aria2 to exit, asks before deleting `%APPDATA%\Accipio` (default keep).
  Never remove other download managers; the README tells users to switch off a competing extension.
  Verified 2026-11-04 silently: install, update over a running Accipio, uninstall while running, all clean.
- Icon (mockup A, picked 2026-10-29): master `accipio/gui/appicon/accipio.svg`; `tools/make_icons.py` writes the
  .ico, the 256 PNG and `extension/icons/` (all committed). The app loads only the .ico, so the exe needs no QtSvg.
- Look (mockup 1 "soft", picked 2026-10-29): `gui/style.py` is one stylesheet applied in `app.py` after the
  family theme. Shades are mixed from the palette (`@border`, `@hover`...), never `palette(mid)` (near-black in
  light mode). Keep radii <= half the widget height or Qt draws square corners. A styled QComboBox loses its
  arrow: `appicon/chevron-down(.png, @2x.png)` from `tools/make_icons.py` replaces it. Progress bars are painted
  in `queue_model.ProgressDelegate`. Check visual changes by rendering real windows (normal platform +
  `grab()`), dark AND light.
- Engines live in `bin/` (gitignored), fetched by `python tools/fetch_engines.py` with pinned SHA-256s.
  The aria2 tests are skipped where aria2c is missing, so fetch it on a new clone.

## Conventions (as in the Redactor apps)
- **Version bump at check-in:** build number `YYYY-MM-DD#NN` in `accipio/core/version.py`. Every commit that
  changes real code runs `python bump_version.py` and adds a matching `CHANGELOG.md` section in the same
  check-in. Never edit `APP_VERSION` by hand; a test fails when the changelog has no section for it.
- Sync (`git pull --rebase`) before editing or pushing.
- Slow operations show progress (`redactor_common` `run_with_progress()`).
- Every table header resizable and sortable. No Ctrl+Alt shortcuts.
- Data logging (history, clipboard watching) is opt-in where it is not essential.
- Secrets only through the system keyring, never in config files.
- Commits use the GitHub noreply address, never a work email.
- No GitHub Actions while the repo is private: the local hooks are the CI
  (`git config core.hooksPath .githooks` per clone).
- Edit code with the Edit/Write tools, not heredocs or patch scripts (backslash escapes have broken releases).
- Tests are plain `pytest` using `tmp_path`, never fixed or Unix-style paths.
