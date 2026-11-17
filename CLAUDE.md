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
- Taskbar button (2026-11-05#06, mockup 47C): `core/taskbar.py` (Qt-free) turns the rows into a `Look` (mode,
  percent, badge count, title) and counts failures nobody has seen (`UnreadFailures`, cleared while the window is
  active). `gui/taskbar_button.py` hands it to ITaskbarList3 over ctypes (no Qt class for it in Qt 6) and draws the
  badge as an overlay icon; `show()` returns False until the button exists, and the window keeps retrying. Do NOT
  override `nativeEvent` on the window to catch TaskbarButtonCreated: under PyQt6 it crashed the real window at
  start (access violation; offscreen tests never see it). Tests replace `win.taskbar_button`.
- Hand-over from Chrome (2026-11-05#03): the extension's onDeterminingFilename offer carries `takeover: true`;
  `MainWindow._taken_over` answers only after `Queue.add` has probed and started the download (at most
  `TAKEOVER_WAIT` = 8 s, under ipc's 10 s). A failed check removes the row and says no, so Chrome keeps its copy; a
  timed-out one is removed when it finishes. Context-menu adds (no Chrome copy exists) still answer at once.
- Files the browser downloads itself (2026-11-17#01, the user's pick A; extension 0.1.4): a blob:/data: download, and
  one Accipio said no to, is remembered in `chrome.storage.session` (`fileLater`) and reported with a `file` message
  when `downloads.onChanged` says complete. `Queue.file_saved` moves it by the rules and adds a COMPLETE row (no
  engine, no gid); `named_by_hand` keeps a file whose name differs from the one Chrome proposed (typed in Save As).
  Chrome does not tell extensions whether Save As was used or which folder is its default, so the APP reads the
  browser's own settings (pick B, same build): `core/browser_settings.py` reads each profile's `Preferences`
  (`download.prompt_for_download`, `download.default_directory`; names from `Local State`) and the policy registry keys,
  read-only, anything odd = "not known". `Queue.browser_asks(user_agent)` (set by the window; tests set
  `win.read_browsers`) True = the user chose the place, the file stays; known only when all profiles of that browser
  agree, else the name check decides. The Settings window lists the profiles (`browsers=` argument, empty in tests).
  `Settings.file_browser_saves` (default on); with it off the native host does not start a closed Accipio for a `file`
  message. Never incognito, "Download with Chrome instead" or takeover off.
- Edge and Opera (2026-11-17#02): the same unpacked extension, no code change. Checked in real Edge 157 and Opera 140,
  hidden (`--headless=new`, scratch `--user-data-dir`, `--load-extension` + `--disable-features=
  DisableLoadExtensionCommandLineSwitch`, then a ping to the native host from the extension's service worker over the
  DevTools port): same extension ID, host found (Opera has no registry key of its own and still finds it),
  onDeterminingFilename and storage.session present. Opera says "OPR/" in its user agent; its profile is
  `%APPDATA%\Opera Software\Opera Stable` in Chrome's layout.
- Brave, Vivaldi, Opera GX (2026-11-17#03): same extension, same hidden check passed in real ones (the user had them
  installed for it). An extension cannot tell them apart (Vivaldi: UA and `userAgentData.brands` say Google Chrome;
  Opera GX says OPR/ like Opera), so `native_host.browser_from(ancestor_programs())` names the browser from the
  program that started the host (ctypes Toolhelp walk on Windows, /proc on Linux; "" when unknown, never an error)
  and puts `browser` in the relayed message and in the reply. `browser_settings.asks(profiles, browser)` takes that
  name; `browser_of(user_agent)` is only the fallback for a message without it. The scratch check script used for all
  of this: `C:\Temp\Claude\accipio-browsers\cdp_check.mjs` (not in the repo).
- Firefox (2026-11-17#04; NOT user-facing until signed): one code base, two manifests. `tools/make_firefox_extension.py`
  builds `dist/firefox` + zip from `extension/` (drops `key`, event page instead of service worker, gecko id
  `accipio@erlbon.github.io` = `browser.FIREFOX_ID`, `data_collection_permissions` required browsingActivity +
  websiteContent, min Firefox 140). `common.js` `FIREFOX`/`BROWSER`/`userAgent()` (no user agent from Firefox: it would
  be technicalAndInteraction, which Mozilla only allows as optional). No onDeterminingFilename there: `background.js`
  falls back to `downloads.onCreated` (cancel, or `removeFile` when already complete; a not-taken download that is
  already complete is reported at once). Host: `<config>\no.erlbon.accipio.firefox.json` (`allowed_extensions`) +
  `HKCU\Software\Mozilla\NativeMessagingHosts`. Settings reader: `profiles.ini` + `prefs.js`
  (`browser.download.useDownloadDir` false = asks). Tests: `tests/js/firefox.mjs` (stand-in Firefox).
  Real check used (scratch, not in the repo): `C:\Temp\Claude\accipio-browsers\run_firefox_e2e.sh` = scratch Accipio
  (`REDACTOR_CONFIG_DIR`, offscreen) + local web server + headless Firefox driven over WebDriver BiDi
  (`webExtension.install` of the folder as a temporary add-on; BiDi refuses to open moz-extension:// pages, so the
  proof is Accipio's debug log). The scratch Accipio segfaulted at `--quit` under the offscreen platform: not looked
  into. Firefox forks (LibreWolf, Zen, Waterfox) untested; LibreWolf has its own native-messaging registry key.
- The signed Firefox file (2026-11-17#06, the user's pick A): `extension-firefox/accipio-firefox.xpi` is COMMITTED
  (`*.xpi binary` in .gitattributes) and bundled by accipio.spec; `browser_help.firefox_extension_file()`. The user
  signs it himself in Mozilla's Developer Hub (add-on "Accipio", unlisted, Upload New Version > On your own) from
  `dist\accipio-firefox-<version>.zip`; never ask for his API secret in chat. A version number can be signed only
  once. `test_the_signed_firefox_file_matches_the_extension` fails while the committed file differs from `extension/`
  (version, description, permissions, every .js/.html), so ANY change under `extension/` needs: bump the manifest
  version, build the zip, he signs, the new .xpi replaces the old one, all before the tests pass again (and so before
  a push: the pre-push hook runs them). Plan extension changes with that in mind and batch them.
  Not done: Firefox does not update the installed copy by itself (no `update_url` in the manifest; the help text
  tells the user to install the file again after an Accipio update).
- Torrents (2026-11-17#07, the user's "entry step"): passed on, never downloaded. `core/torrents.py` (Qt-free):
  `Settings.torrents` off (default) / system (`os.startfile`, any client) / qbittorrent (`qbittorrent.exe
  --save-path=<rules' folder> <magnet or .torrent path>`, argv list, no shell; only `--save-path` because
  `--add-paused` became `--add-stopped` in v5). `Queue._start` sends a magnet to `_pass_magnet` BEFORE any engine:
  **a magnet must never reach aria2, which would download the torrent itself** (retry is guarded too; a test pins
  it). A magnet row is COMPLETE at once with no file (filename = the torrent's name; folder only in the qbittorrent
  way). A finished `.torrent` goes through `_pass_torrent_file` from `_finish`. `links_in` accepts `magnet:?`.
  Tests replace `queue.pass_torrent`; no real torrent program was ever started (work PC: no torrent traffic here).
  Not done: the extension's right-click on a magnet link (`isTakeable` refuses magnet:; needs a new signed version),
  registering Accipio as the magnet: handler, qBittorrent's Web API (the fit for the Accipio-to-Accipio collector).
- Accipio-to-Accipio link, stage 1 of 4: the core (2026-11-17#08; answers 93A, 94A, 95B, 99B, 101C). NOTHING uses it
  yet. `core/link.py`, Qt-free, no rules or queue: `Node(folder, keys)` = this Accipio's id + name and its paired
  `Peer`s in `link.json` (nothing secret in it); the long key of each peer is in the keyring as `link-key-<peer id>`
  through `SystemKeys` (redactor_common's secret_store, app "accipio", never its unencrypted file; `keyring` is now a
  dependency and in accipio.spec). The line: TLS-PSK from Python's `ssl` (3.13+; `available()`, `why_unavailable()`,
  `LinkUnavailable`), **pinned to TLS 1.2 + ECDHE-PSK-CHACHA20-POLY1305: do not allow TLS 1.3**, whose first packet
  lets a mere listener test pairing codes offline. Messages: JSON behind a 4-byte length, `v` + `type`
  (`hello` answered by the listener, `add` with the extension's field names incl. optional `cookies`, `status`),
  one dict back; `problem_with()` checks the shape before the handler sees it. `Listener(node, handler, addresses,
  port)` binds ONLY the given addresses (none = nothing listens; `problems` names those that failed), a thread per
  connection; `handler(message, peer)`. Sender: `send()` / `Line`; errors `Away` (unreachable: wait, 97C) vs
  `Refused` (not paired any more). Pairing: `node.begin_pairing()` gives a 12-character code in three groups (the
  user's pick over six digits, which someone in the middle could have tried out offline), single use, 5 minutes,
  3 connections right or wrong; key from the code by scrypt salted through the TLS identity hint; `pair(node, host,
  port, code)` on the sender; each side gives half of the long key. Tests (`tests/test_link.py`): real connections
  on 127.0.0.1, `tmp_path`, a `MemoryKeys` stand-in; an autouse fixture fails any test that reaches the real keyring.
  Not decided here: where the ticked addresses and the port are saved (stage 3; `DEFAULT_PORT` 47610 is a
  placeholder nobody chose). Next: stage 3 the windows, stage 4 two scratch copies. Design and answers: the
  handover files of 2026-11-17 (`C:\Temp\_AI Coding\_Claude\accipio-handover-2026-11-17.md` and `...-17b.md`).
- Accipio-to-Accipio link, stage 2 of 4: rules and queue (2026-11-17#09; answers 96A, 97C, 98B, 99B, 102A, 103A).
  Still NO window and nothing starts a `Listener` or a `Sender`; without a `Sender` attached a row for another
  Accipio just waits. `rules.Folder` has `kind` ("folder"/"accipio"), `peer`, `cookies` (default on), `away`
  ("wait" default / "here"); older rules.json loads unchanged. `rules.place(..., local_only=)`: a rule whose place is
  remote ends the walk with `Placement.remote`; `local_only` passes such rules over (102A), numbers unchanged.
  Store schema 3: states `WAITING`/`SENT` (NOT in `UNFINISHED`: no gid, no taskbar progress, untouched by
  `_reconcile`/`refresh`), a `remote` JSON column (sender: peer, place, away, cookies, torrent, ref, id, outcome;
  collector: from, from_name, ref, heard) and an `owed` table (what the sender still has to tell a collector:
  `OWE_REMOVE`, `OWE_HEARD`; survives a restart and a removed row). `Row.path` is "" without a folder.
  `Queue._start` probes first, then `_hand_over` when the placement is remote; forced local when the folder was
  chosen by hand, in `_finish`'s sniff move, `file_saved` (100A), archives, and for a row that CAME from a peer
  (never sent on to a third: no loops). Sender's own probe fails: placed by the name in the URL and still handed
  over when that is remote (my decision). Torrents: `away` is forced to "wait" and `_start` asks `is_magnet`
  before anything else, so **a magnet still never reaches aria2** on either side (pinned by a test).
  `core/handover.py` (Qt-free) does the talking: `Sender(queue, node, run=)` sets `queue.link_nudge`/`link_tick`
  (the latter called from `refresh()`); `pump()` never overlaps, runs through `Queue.in_background`, one `Line` per
  collector: send waiting rows (`link.add` with the ORIGINAL url, cookies only with the tick, a `ref` so a lost
  answer does not queue it twice) -> ask `link.status` -> settle what is owed (`link.remove`, `status(heard=)`).
  `POLL` 15 s, `RETRY` 30 s (my numbers). Queue callbacks: `handed`, `not_handed(away=)`, `outcome`. `Away` = wait
  or download here; reached-but-refused (unpaired, not a link) = the row FAILS with the reason unless "here".
  98B: finished there = COMPLETE with `remote["outcome"]` ("Finished on Collector, in Comics": the NAME of the
  collector's place) and no notice (`notices.py` skips COMPLETE rows with `remote["peer"]`); failed = ERROR
  "Failed on Collector: reason". 103A: `Queue.remove`/`retry` -> `_forget_there` owes a remove.
  `Collector(queue, run=)` is the `Listener` handler: `Queue.receive` lists the row at once (answer inside the
  line's 10 s), `run(queue.begin)` probes and starts; `report`/`heard`/`remove_for` only for rows whose
  `remote["from"]` is the asking peer; `forget_finished(keep_waiting=True)` keeps a finished row from a peer until
  heard. Row texts: `handover.status_text`, used by `queue_model` (Active chip includes the two states; the Folder
  column shows the place's name). Tests: `tests/test_handover.py`. Known gaps for stage 3: the Folders tab rebuilds
  `Folder(name, path)` and would strip a remote place on save; the Add box preview ignores `Placement.remote`; a SENT
  row whose pairing was forgotten stays "Sent"; a collector's unheard finished rows from a forgotten sender are
  cleared only by hand; a playlist LINK whose check failed on the sender waits for a yes on the collector.
- Accipio-to-Accipio link, stage 3 of 4: the windows (2026-11-17#10). Everything is off until switched on.
  `handover.Service(queue, node, run=)` is one Accipio's whole link: a `Sender` ALWAYS (also without pre-shared keys:
  its rows then fail with `link.why_unavailable()` instead of waiting for ever), a `link.Listener` + `Collector` only
  while `apply(accept, addresses, port)` is given accepting on and at least one address; same values = nothing
  happens, a change restarts it; `listening`, `problems`, `status_text()`, `reach(peer_id)` (blocking hello),
  `forget(peer_id)`, `stop()`. `open_services(..., keys=)` makes `services.node = link.Node(folder, keys)`; the window
  owns `self.link`, applies it at start and in `apply_settings`, stops it in `closeEvent` before the worker.
  Settings: `link_accept` (off), `link_addresses` (none), `link_port` (0 or invalid = `link.DEFAULT_PORT`, imported
  inside `__post_init__` because link.py imports settings.py); keys stay in the keyring. `gui/link_dialog.py`
  `LinkDialog` = Settings > Other Accipios ("Set up…" on a new Settings row): a window of its own with only Close,
  everything in it takes effect at once through `MainWindow.apply_link` (my decision: pairing and Forget cannot wait
  for OK, and a code is only of use while the listener runs); so `open_settings` takes the three link fields from
  the LIVE settings after OK, never from the dialog's copy. Network names come from `QNetworkInterface`, else from
  the address range. Pairing runs through `call_in_background`. The Folders tab is `PlacesTab`
  (`RulesDialog.places_tab`, `peers_of=`, `reach=`, `link_problem=`): Kind, and for another Accipio Machine (picked
  from the paired ones that have an address, never typed; a peer that is gone stays as "(not paired any more)"),
  "If it cannot be reached", "Send the site's cookies along"; `read_item` builds the whole `Folder`, so saving the
  window no longer strips a remote place. Forget on this PC: `Sender._talk` sends the SENT rows of a peer that is
  gone to `Queue.unpaired` (ERROR, Retry sends anew), `Queue.forget_sender` marks a forgotten sender's rows heard.
  Add box: the place's name for `Preview.placement.remote`. Rows with no folder: "Show in Folder" disabled, "Why
  here?" starts with the place. `tests/conftest.py` refuses the real keyring in EVERY test (pass `MemoryKeys`).
  Tests: `test_link_service.py`, `test_places.py`, `test_link_window.py`. Known gaps: a collector that has forgotten
  THIS PC answers `Refused`, which may also be a passing fault, so "Sent" rows stay then; `Service.apply` does not
  retry a listener that failed until a value changes; Cancel in the Pair box abandons the call but the thread may
  still finish the pairing. `DEFAULT_PORT` 47610 is the user's choice (answer 104, 2026-11-17: keep it; IANA lists
  47558-47623 as unassigned); it is only what the port box starts at. Next: stage 4, two scratch copies on one PC, then two machines
  (first use of the real credential store, Windows Firewall's question, an installer build). Design:
  `C:\Temp\_AI Coding\_Claude\accipio-handover-2026-11-17d.md`.
- Every finished file gets Windows' Mark of the Web (`core/zone.py`, `file:Zone.Identifier`, ZoneId=3 + ReferrerUrl
  + HostUrl without passwords), written in `Queue._finish` after any sniff move. Failure (no NTFS) is ignored.
- Speed limit (2026-11-05#03): `Queue.speed_limit` is the TOTAL; `refresh()` splits it with `share_speed()` by how
  many rows run in each engine and calls `set_limits(speed_limit=)` only on change. `VideoEngine` enforces its
  share for all videos together in the progress hook (`_spend`, a shared byte budget); never yt-dlp's `ratelimit`,
  which is per video. The GUI sets only `max_downloads` on the engines directly.
- Archive trial (2026-11-05#04, decisions 40B, 41, 42, 43E, 45A; the user wants it TEST-RUN before it is settled):
  `core/unpack.py` (Qt-free: `is_archive` by ending only, `contents`, `suggest`, `plan_folder`, `plan_sort`, `extract`,
  `undo`), `Queue._archive_arrived` from `_finish` when `Queue.unpack_on` (Settings.unpack_archives, default off),
  row state in the store's `unpack` JSON column (schema 2: waiting/done/kept/failed), `Rule.unpack` ("" / "folder" /
  "sort", the Rules form's "and archives are" row, shown when the trial is on), `rules.remembered_rule` for the box's
  "do this every time" tick, `gui/archive_box.py` (a box UNDER the list, not unfolded inside the row as in the
  mockup: a QTableView cannot hold it without a model rewrite). rar/7z: later via an installed 7-Zip (45C).
- History of finished downloads is opt-in (`keep_history`, default off); headers/cookies are wiped on finish.
- The **command action is stored but not run yet**: running server-controlled names through a command line
  (especially .bat/.cmd) needs injection-safe quoting first.
- Exe: `accipio.spec` + `build_exe.bat` -> `dist\Accipio\` (FOLDER build on purpose: every browser click starts the
  exe as the native host, and a one-file exe would unpack itself each time). aria2 goes to `_internal\aria2`.
  The spec does not collect all of redactor_common (its preview players pull in Qt Multimedia/Pdf) and drops
  opengl32sw, the codecs and translations: 146 MB -> 85 MB. Verified 2026-10-29: window, aria2, yt-dlp + merge,
  host ping through the windowed exe (0.3 s), single instance. Running the exe re-registers the browser host
  to it; after testing a dist build with a scratch APPDATA, re-register from the checkout.
- Rules window (picked 2026-11-04, build #02): tabs **Folders → File Groups → Site Groups → Rules** (setup order;
  the names are the user's). `rules.Library` = folders + file groups (`Group`: extensions, names, MIME) + site groups
  (`SiteGroup`: sites, where = page/file/either). Sites never go in a file group (decided: different content). Rules
  point at a `Folder` by name (`Rule.target`; `Rule.folder` is the old typed path, turned into a named folder by
  `adopt_folders`). Condition kinds: "type" = in file group (+ `exclude` extensions for this rule only), "sites" =
  from site group. Renamer = `Rule.renames`, lines of regex → template, first match wins, none = name kept (format 1's
  single rename became a ".*" line). rules.json format 3 holds rules + folders + groups + site_groups; export/import
  use the same format; "add to mine" renames clashing items "Name (imported)". GUI: `gui/library_tabs.py`
  (NamedListTab base), `gui/rules_dialog.py` (RenameEditor = mockup 15B). Rule templates (12A) not built.
- Rule editor (the user's UX review, 2026-11-04, build #05): a rule is a sentence: name; if downloaded from <site
  group chips>; files that are <file group chips, several>; go to <folder>; then rename lines. Decisions 24A narrow
  list + sentence, 25B no "except" (edit the group), 26A no Stop/Continue (first fit wins), 27A rarer conditions
  behind More conditions. No subfolder on a rule (a separate folder instead). Groups are picked, never typed
  (`ChipPicker`). `rules.modernize` converts older rules (typed path, subfolder, except, continue) on load.
  `Rule.folder/subfolder/stop` and `Condition.exclude` are read for that only.
- Default folder (the user, 2026-11-04): a new setup has ONLY the "Default" folder and NO rules; never prefill
  folders or rules. Default = settings.base_folder, shown and edited as row 0 of the Folders tab (not stored in
  Library.folders), not renamable/deletable, name reserved. Other folders are written `%default%\<name>`
  (`rules.DEFAULT_TOKEN`, `rules.folder_path`); `Rule.target == "Default"` means the base folder. The old starter
  folders/rules live on as test data in `tests/samples.py`.
- Browser connection (18B + 7C): `gui/browser_help.py` bar + ConnectDialog; the native host adds `via: "browser"`
  to what it relays, the window then sets `browser_connected`. Help page `accipio/gui/help/connect-browser.html`
  (finds the extension folder from its own location), opened by the installer. Chrome refuses chrome:// URLs from
  other programs, so nothing may try to open the extensions page.
- OPEN (2026-11-04): whether video-site downloads get their own rule list (a 5th tab), stay in one list with
  headings, or get only a folder setting. They come only from deliberate actions (popup button, page/link right-click,
  paste), never from a clicked file.
- Updates (picked 2026-11-04, 21A): `gui/updates.py` AccipioUpdates subclasses redactor_common's UpdateController;
  the asset is the installer under the fixed name `accipio-setup.exe` (build_exe.bat copies it; **attach it to every
  release** or the check finds nothing to install). Update Now = download + checksum, run it with
  `/SILENT ... /relaunch=yes`, close Accipio; the installer's `Relaunch` check starts it again. Only a copy whose
  folder is the installer's InstallLocation updates itself; zip/source copies get the download page. Daily check is
  the family's `accipio_updates.json`, switched in Preferences (off by default). Credits = CREDITS.md (bundled).
- Installer (picked 2026-11-04): Inno Setup, `installer\accipio.iss`, built by `build_exe.bat` when ISCC is found
  (version passed via `tools/installer_defines.py`). Per-user install `{autopf}` = `%LOCALAPPDATA%\Programs\Accipio`,
  no UAC; fixed AppId GUID (never change it, or updates install side by side). The browser link is written by
  `Accipio.exe --register` (same `browser.register`), removed by the uninstaller's `uninsdeletekey` entries.
  Tasks: desktop icon, "start with Windows" (HKCU Run, `--minimized`, off by default), ffmpeg via winget (shown only
  when no ffmpeg is found; `video.find_ffmpeg` also looks in winget's Links folder). Extension: setup offers the
  help page on a first install (Web Store install via the HKCU `Extensions` key once listed), and names other
  download managers it finds (FDM, IDM...) on its last page.
  Uninstall: graceful `taskkill`, waits for aria2 to exit, asks before deleting `%APPDATA%\Accipio` (default keep).
  Never remove other download managers; the README tells users to switch off a competing extension.
  Verified 2026-11-04 silently: install, update over a running Accipio, uninstall while running, all clean.
- Icon (Q63B, 2026-11-06; replaced the arrow-into-tray, which looked like a setup still running): master
  `art/accipio-glove.png` (his drawing, keyed out, outside the package so the exe does not carry it);
  `tools/make_icons.py` tilts it 35°, puts it on a teal tile and writes the .ico, `accipio-setup.ico` (with a box
  badge, the installer's only: Q62A), the 256 PNG and `extension/icons/` (all committed). The app loads only the .ico.
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
