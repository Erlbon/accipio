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
  Described in `docs/design.md`.

## Code layout
- `accipio/core` is Qt-free and has no disk access where it can be avoided: `rules.py` (the walk, conditions,
  tokens, `place()`), `cron.py` (time windows). The GUI, the engine and the CLI are thin layers over it.
- Time conditions use AND for day-of-month + weekday (unlike classic cron); see the `cron.py` docstring.
- `aria2.py` is a small stdlib JSON-RPC client, not aria2p (fewer dependencies, smaller exe).
  **Never turn on aria2's `--continue`**: it appends to an existing file of the same name (a test pins this).
  `stop()` saves the session and does not wait (aria2c takes ~4 s to exit on Windows).
- `probe.py` finds the real name/type/size before a download (HEAD, else a one-byte GET).
- `store.py` (SQLite, WAL, one locked connection) + `queue.py` (probe -> rules -> engine; `refresh()` on a
  timer; sniff-and-re-place on finish via `sniff.py`). `Queue.add/preview` do network I/O: call them off
  the window's thread. Tests use a FakeEngine/FakeProber; one end-to-end test runs real aria2.
- History of finished downloads is opt-in (`keep_history`, default off); headers/cookies are wiped on finish.
- The **command action is stored but not run yet**: running server-controlled names through a command line
  (especially .bat/.cmd) needs injection-safe quoting first.
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
