# Accipio design (agreed 2026-10-28)

## Why
Free Download Manager cannot be told sensibly which folder each kind of file goes to. Persepolis was
looked at as a base to fork and rejected: about 20,000 lines, a 5,400-line main window, and the
type folders are hard-coded (with a missing comma that sends `.mobi` to Others). Accipio is built new.

## Stack
- Python + PyQt6, sharing `redactor_common` with the Redactor apps (menu skeleton, progress, self-update,
  version bump, CLI inside the exe). Code is promoted to `redactor_common` only when two or more apps need it.
- **aria2c** as the engine for direct downloads, started by the app on localhost with a random RPC secret
  and stopped when the app quits; controlled through a small stdlib JSON-RPC client (not aria2p).
- **yt-dlp** for video sites, with **ffmpeg** bundled to join separate video and audio streams.
  yt-dlp can update itself (off by default, with a button).
- **SQLite** for the queue and history, so they survive a crash.
- Windows first; keep the code portable to Linux and macOS.

## Rules
An ordered list. Priority is set by dragging rules up and down. Every download goes through the list from the top.

**Conditions** (all in one rule must match):
- domain: of the page the link was on, or of the file itself (wildcards, e.g. `*.github.com`)
- file type: extension, a named group (Video, Ebooks...), or MIME type
- name: wildcard or regex
- size: above/below
- time: weekdays, hours, date ranges
- source: direct download or video site
- video details (yt-dlp): channel, uploader, playlist

**Actions:** set folder, add subfolder, rename with a template, run a script, then **stop** or **continue**.
The script action runs **any command line** when the download finishes (decided 2026-10-28), e.g.
`powershell -File unpack.ps1 "{path}"`; tokens are filled in, and the file path, URL, domain, type, size
and video details are also passed as `ACCIPIO_*` environment variables. It may land after v1. Until it runs
(with injection-safe quoting), the rules editor hides the field; a command already saved in rules.json is kept.

**yt-dlp updates** (2026-11-02): Tools > Update yt-dlp fetches the newest wheel (and the yt-dlp-ejs it pins)
from PyPI, checks the SHA-256, and unpacks it into `%APPDATA%\Accipio\yt-dlp\<version>`. At start it goes
ahead of the bundled copy on `sys.path`, only when newer; `yt-dlp\in-use.txt` records which one loaded.
Continue lets rules combine, as in mail filters: a domain rule sets the base folder, a type rule further
down adds the subfolder.

**Tokens** in folder and name templates: `{yyyy} {MM} {dd} {week} {weekday} {domain} {uploader} {title} {ext}`.
Date tokens make folders rotate by the clock. Missing folders are created when a download starts.

No calendar (Outlook/Google) integration: time conditions and date tokens are enough (decided 2026-10-28).

**Time conditions are crontab underneath, with a kinder UI** (decided 2026-10-28). A rule's time condition
is stored as a five-field cron expression (`minute hour day-of-month month weekday`); it matches when the
moment the download starts fits the expression, so ranges describe windows: `* 8-15 * * 1-5` is weekdays
08:00-15:59. The editor never makes you type cron: weekday checkboxes, an hour range, months, day-of-month,
shown back as a sentence ("Weekdays, 08:00-16:00"). An Advanced field shows and accepts the raw cron line
for cases the pickers cannot express. Several windows are several cron lines OR-ed together.
The same cron engine will later drive schedules (start/pause the queue at set times).
The matcher is our own small module with tests; it needs no scheduler library because it only answers
"does this moment match?".

**Placing a file:**
1. Before downloading, a quick request finds the real filename (Content-Disposition, else the URL) and MIME type.
   For video sites yt-dlp supplies the details.
2. The rules choose the folder; it is shown before the download starts and can be changed per download.
3. When the download finishes, the final name is checked again; if it changed type (a `.php` link that was a zip),
   the file is moved and the move is logged.
4. Name clashes become `name (1).ext`; nothing is overwritten.

The rules editor has a test box (paste a URL, see which rules hit and the final path), and every finished
download has a "why here?" line naming the rules that placed it.

## Chrome extension (v1)
- Takes over Chrome's downloads and passes URL, referrer and cookies, so downloads behind a login work.
  A modifier key lets Chrome download it itself (never Ctrl+Alt: AltGr on Norwegian keyboards).
- Right-click "Download with Accipio" on links, images and videos.
- Toolbar button "Grab video on this page" sends the page to yt-dlp.
- Talks to the app through native messaging (the app registers its host on first start).
- Cookies come from the extension because yt-dlp can no longer read Chrome's cookies on Windows
  (app-bound encryption since Chrome 127).
- Loaded unpacked in developer mode at first; the Chrome Web Store can come later.

## Main window
Queue table (Name, Size, Progress, Speed, Time left, Folder, Status), every header resizable and sortable,
filters All / Active / Done / Failed. Add-links box takes many links at once and drag-and-drop, and previews
the folder of each. Settings: base folder, downloads at once, connections per file, speed limit, an opt-in
clipboard watcher (off by default).

**Layout picked 2026-10-28: mockup B, compact.** One table, no sidebar. On top: a paste bar ("Paste links or
drop files here" + Add) with the Rules and Settings buttons; under it filter chips All / Active / Done / Failed
with counts, and the total speed on the right. The table has a Rule column showing which rules placed the
file (e.g. `1+5`). "Why here?" (the rules that hit and the path they built) shows on hover and in the
right-click menu.

## Rules editor
**Layout picked 2026-10-28: mockup A, list + form.** Left: the ordered rule list with drag handles, each
row marked continue (→) or stop (■), and "New rule" at the bottom; disabled rules shown greyed. Right: the
selected rule as a form: name and enabled switch, "When all of these match" (one row per condition,
"Add condition"), and "Then" (folder, rename, run command, Stop/Continue). The time condition is weekday
buttons plus an hour range, read back as a sentence, with the raw cron line under Advanced.
At the bottom, across both halves, the test box: paste a URL, see which rule hit, which were skipped,
and the final path.

## Later
Schedules and queues, torrents (aria2 has them), the Chrome Web Store listing.
