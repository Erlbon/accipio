# Accipio: working notes for Claude

The README says what this is; `docs/design.md` holds the agreed design. This file holds the decisions so a new
session does not reopen what is settled. Keep it current when a decision changes.

## Decided (the user, 2026-10-28)
- **Name:** Accipio. Repo `Erlbon/accipio`, **private until the first release, public after.**
  The PyPI name is taken by a dead package; we ship exes from GitHub releases, not PyPI.
- **Built new**, not a Persepolis fork (see the design doc for why).
- **v1 includes** yt-dlp and the Chrome extension; the user downloads by clicking in Chrome.
- **Rules** are ordered by drag-and-drop, with stop/continue; time/date conditions and tokens, **no calendar integration**.
- **No FDM import.** Start fresh.
- Before code: mockups of the main window and the rules editor, and wait for the user's pick.

## Conventions (as in the Redactor apps)
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
