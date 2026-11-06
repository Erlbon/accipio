# Changelog

Build numbers have the form `YYYY-MM-DD#NN`, as in the Redactor apps. Each check-in that changes real code runs `python bump_version.py` and adds a section here.

## 2026-11-06#01 -- File > Add Links works again

- **File > Add Links… (Ctrl+N) crashed** with "setPlainText … unexpected type 'bool'": the menu passed its on/off state along as the text of the Add box. It opens the box again, with the paste bar's links or the clipboard's.

## 2026-11-05#06 -- The taskbar button shows the downloads

- **A progress bar on Accipio's taskbar button** for all unfinished downloads together. It is in your Windows accent colour while they run (Windows 11 picks the colour), yellow when all are paused, and red when a download failed while the window was in the background. It stays red until you bring Accipio to the front. When no size is known, as with some videos, the bar slides.
- **A badge on the button** counts the running downloads, or the paused ones when nothing runs, up to "9+".
- **The window title says the same in words**, so hovering the taskbar button or the tray icon shows "Accipio – 3 downloading, 45%", "2 paused" or "1 failed".

## 2026-11-05#05 -- Release

Everything since 2026-11-04#06, in one release:

- No Save As box for downloads Accipio takes (#01).
- Series of links, such as `saga-[001-150].cbz`, in the Add box (#02).
- A download Chrome hands over is checked before Chrome lets go of it; downloaded files carry Windows' "from the internet" mark; the speed limit is a real total (#03).
- Archives: look inside, then decide, as a trial switched on under Settings > Archives (#04).

After updating, press the reload button on Accipio's card in `chrome://extensions` so Chrome picks up extension 0.1.2.

## 2026-11-05#04 -- Archives: look inside, then decide (trial)

- **A trial, off until you switch it on:** Settings > Archives, "Look inside downloaded zips and tars and ask what to do".
- **A finished .zip or .tar shows what is inside**, in a box under the list: "tool.zip: 3 files inside. What now?", where each file would go, and three buttons: **Unpack as a folder in** (a folder you pick; everything stays together in `<folder>\<archive name>\`, a program with its readme), **Sort them** (each file goes through your rules as if downloaded on its own; what no rule fits goes to `Default\<archive name>\`) and **Keep packed**. Hover a button to see where the files would go.
- **The suggested button comes first**, chosen from the contents: a program inside means "as a folder" (in the folder your rules give the program); files your rules all know mean "sort them"; anything else stays packed until you say.
- **"Do this every time for Archives from <site group>"** turns your choice into an ordinary rule at the top of your rules. A rule can also say it in the Rules tab: *go to* a folder *and archives are* kept packed / unpacked as a folder there / sorted by the rules. Such archives are unpacked at once, and the box then says what happened, with **Undo**.
- **Undo** (also in the row's right-click menu) removes the unpacked files and the folders it made, and the archive waits for a choice again. A file you changed since is kept.
- **After unpacking** the archive is kept, or moved to the Recycle Bin (Settings > Archives).
- **Only real archive endings are opened**: .zip, .tar, .tar.gz/.tgz, .tar.bz2, .tar.xz. Files that are zips underneath (.cbz, .epub, .docx, .jar, .apk) never are. rar and 7z come later, through 7-Zip.
- **Safe by design:** no file inside can land outside its folder (`..\`, absolute paths and drive letters are taken apart), nothing is overwritten, an archive that would unpack to more than 50 GB or 20,000 files is left packed, a password-protected or damaged one says so on its row, a failure halfway leaves nothing behind, and every unpacked file carries Windows' "from the internet" mark as the archive did.

## 2026-11-05#03 -- Safer hand-over from Chrome, Windows' download warning, one speed limit

- **Downloaded files carry Windows' "from the internet" mark**, as files Chrome saves do. A program downloaded through Accipio now gets the SmartScreen warning before it runs, and a downloaded Office document opens in Protected View. Before this, a program Accipio took from Chrome ran without any warning. Drives that cannot hold the mark (FAT32 sticks, some network shares) keep the file as it is, as with Chrome.
- **A download Chrome hands over is checked before Chrome lets go of it.** Accipio used to say yes as soon as the link was in its list, and Chrome then threw its own copy away; if Accipio's own request failed (a link that works only once, a download made by a form, a site that refuses anyone but the browser) the file was gone and only an error row was left. Now Accipio answers only once its own request worked; otherwise Chrome simply downloads the file itself. If the check takes more than 8 seconds, Chrome keeps the download too.
- **The total speed limit is a total.** Each video used to get the whole limit on its own, on top of the direct downloads; three videos and a file could run at four times the limit. The limit is now shared between direct downloads and videos by how many run in each.
- The extension is version 0.1.2: reload Accipio's card in `chrome://extensions` after updating.

## 2026-11-05#02 -- Series of links: saga-[001-150].cbz

- **One link can stand for a whole series**, as in curl and DownThemAll: `page[1-20].jpg`, `img[001-150].png` (leading zeros kept), `v[1-100:5].zip` (a step), `[a-z].pdf`, `{intro,main,outro}.mp3`, and combinations such as `s[1-3]/e[01-10].mkv`. Ordinary links that happen to hold brackets (an IPv6 address, `?ids[]=4`) stay one link.
- **A series opens the Add box**, from the paste bar, Edit > Paste Links or a drop, so nothing starts unseen. It shows as one folded row with one tick, one folder and a summary ("148 ready · 2 failed"); click its name to unfold it and untick single files, double-click its folder to send the whole series elsewhere. Links that do not exist are left out by themselves. The links are checked one at a time, so a site is not flooded.
- **A series of more than 1,000 links asks first**, since one typo can make millions.
- In the Add box, links going to the Default folder say "Default" (it said "base folder").

## 2026-11-05#01 -- No Save As box for downloads Accipio takes

- With Chrome's "Ask where to save each file before downloading" switched on, Chrome opened its Save As box even though Accipio had already taken the download. The extension now takes downloads over before Chrome picks a name, so the box no longer opens for them. A download Accipio does not take (Accipio not running, a link it cannot use, the takeover switched off) goes on in Chrome as before, Save As included.
- The extension is version 0.1.1. After updating Accipio, press the reload button on Accipio's card in `chrome://extensions` (or restart Chrome) so Chrome picks up the new version.

## 2026-11-04#06 -- One folder per older rule, whichever version it came through

- A rule with a typed folder and a subfolder that already went through 2026-11-04#04 (which named the folder but kept the subfolder) now ends with one folder, the named one extended by the subfolder, instead of that folder plus a second one beside it. A folder another rule also uses is left alone and the rule gets a folder of its own.

## 2026-11-04#05 -- A rule is one sentence

- **The Rules tab reads as a sentence:** *Rule* name; *if downloaded from* site groups; *files that are* file groups; *go to* a folder; then *after downloading, rename*. Site groups and file groups are picked from a list and shown as chips (several allowed, ✕ to remove); nothing is typed. No site group means anywhere, no file group means any file.
- **More conditions** (name, size, time of day, source, video channel and the like) are folded away until you open them.
- **Rename lines** sit right in the rule, one row each (pattern → new name, with ↑ ↓ ✕ for their order), with **Try a name** under them.
- **The first rule that fits wins.** "Stop / Continue to the rules below" is gone, and so are the marks for it in the list.
- **No more "+ subfolder" or "except" on a rule.** A different place is a folder of its own in the Folders tab; leaving extensions out is done in the file group.
- **The rule list is narrower**, so the rule itself gets the room.
- **Older rules are brought up to date** when the Rules window or Accipio opens, keeping where their files land: a subfolder becomes a folder of its own, an "except" list becomes a copy of the file group without those extensions ("Video without ts"), and Continue goes.

## 2026-11-04#04 -- One Default folder to start with

- **A new setup starts with one folder, Default, and no rules**: everything goes to Default until you write a rule. No folders (Video, Books, Comics...) or rules are filled in for you any more. The built-in file groups are still there to use.
- **Default is set in Rules > Folders**, at the top. It is the folder that used to be "Base folder" in Preferences (Preferences now just says where it is). It cannot be renamed or deleted, and no other folder may be called Default.
- **New folders start as `%default%\<name>`**, under Default, so they follow it when Default moves. `%default%` alone is Default itself; a plain relative path still means the same as `%default%\...`. Browsing to a folder inside Default writes it this way too.
- A rule can pick **Default** as its folder.
- Setups that already have rules or folders keep them as they are.

## 2026-11-04#03 -- Check for Updates and Credits

- **Help > Check for Updates** works (it was greyed out). It asks GitHub for the newest release and, when there is one, shows what changed with **Update Now**, **Skip This Version** and **Not Now**. Update Now downloads the new installer, checks it against the checksum GitHub lists, and runs it: Accipio closes, is updated and starts again, and downloads carry on. A copy run from the zip or from source gets **Open Download Page** instead.
- **Preferences > Updates:** "Check for a new version once a day" (off unless you switch it on). Nothing about you or your files is sent.
- **Help > Credits** works: what Accipio is built on (aria2, yt-dlp, PyQt6 and Qt, Python and the rest) and their licences.
- Each release also carries the installer as `accipio-setup.exe`, the name the update check looks for. `build_exe.bat` makes that copy.

## 2026-11-04#02 -- Folders, file groups, site groups and a renamer

- **The Rules window has four tabs**, in the order you set things up: **Folders**, **File Groups**, **Site Groups** and **Rules**. The test box sits under all four and has a new field for the page a link was clicked on.
- **Folders** are named places, with tokens (`D:\Comics\_Inbox\{yyyy}-{MM}`); a relative one is under the base folder. A rule picks a folder from a list, so changing a folder moves every rule that uses it. Each folder shows where it points today and which rules use it. A folder still in use cannot be deleted.
- **File Groups** are what a download is: extensions (`cbz, cbr`) and name wildcards (`*invoice*`); any one is enough. The built-in groups (Video, Audio, Images, Documents, Ebooks, Comics, Archives, Programs) can be edited (move `iso` out of Archives, say), and **Reset to built-in** puts them back.
- **Site Groups** (optional) are where a download comes from. A site matches the page you clicked on, the file's own address, or either, so a link site whose files sit on hosters is caught through its page.
- **Rules** say "in file group Comics, except cbt", "from site group Comic sites", and so on. Renaming a folder or group renames it in every rule.
- **The renamer** is the last part of a rule: lines of "if the name contains *regular expression*, rename to *template*", with the pattern's groups as `{1}`, `{2}`… The first line that fits renames the file; with none, the name is kept (before, a rule's rename changed every file it caught). A **Try a name** field shows the result as you type. A rename from an older version becomes a line that fits every name, so it keeps working.
- **Books and comics have their own folders** by default (Books and Comics).
- **Export… and Import…** in the Rules window save the rules, folders and groups to a file and load them on another PC. Importing can add to yours (a folder or group of theirs with a name you use arrives as "Name (imported)") or replace yours.
- Rules from older versions that typed a folder path get a named folder for it the first time this version starts.
- **Connecting the browser:** until the extension has talked to Accipio once, a bar across the window says so and opens a box with the three steps and buttons that copy `chrome://extensions` and the extension folder. **Tools > Connect Browser** opens it any time. The extension now greets Accipio when it is installed and when the browser starts, so the bar goes by itself.
- **The installer** no longer tries to open Chrome's extensions page (Chrome refuses that to other programs, so it opened a plain window): it offers a help page with the steps and copy buttons instead. When Free Download Manager, Internet Download Manager or a similar program is installed, its last page says to switch one of the two browser extensions off. It never changes the other program.
- **Tools > Update yt-dlp** tries again for a few seconds when Windows briefly refuses to move the new copy into place (a virus scanner still reading the fresh files); it used to fail at once.

## 2026-11-04#01 -- A Windows installer

- **`Accipio-<build>-windows-x64-setup.exe`** installs Accipio for your user only (`%LOCALAPPDATA%\Programs\Accipio`, no admin rights), with a Start menu entry and an optional desktop icon, and points Chrome and Edge at it. The zip stays for those who would rather not install.
- **Options in the installer:** start Accipio (minimized) when you sign in to Windows, off unless ticked; and install ffmpeg with winget, offered only when no ffmpeg is found.
- **The browser extension:** the last page offers to open the extensions page and the extension's folder, and says what to click there. Shown on a first install only.
- **Updating:** run a newer installer. It closes a running Accipio (and its aria2), replaces the program folder and keeps your rules, settings and downloads.
- **Uninstalling** (Windows' Settings > Apps) closes Accipio, removes the browser link and the sign-in entry, and asks whether to delete your rules, settings and download list as well (kept unless you say yes).
- `Accipio.exe --register` points the browsers at Accipio and quits (the installer runs it); `--minimized` starts with the window minimized.
- ffmpeg installed by winget is found even when Accipio was started before winget changed PATH.
- `build_exe.bat` also builds the installer when Inno Setup 6 is installed (`installer\accipio.iss`).

## 2026-11-02#01 -- Update yt-dlp from the Tools menu

- **Tools > Update yt-dlp…** fetches the newest yt-dlp from PyPI, so video sites keep working without a new Accipio build. It also fetches the yt-dlp-ejs version that yt-dlp asks for (it handles YouTube's JavaScript checks). Each file is checked against PyPI's SHA-256 before anything is unpacked. A progress box with a working Cancel shows while it runs; a failure is told in plain words and changes nothing.
- The new yt-dlp goes into `yt-dlp\<version>` in Accipio's folder and is used from the next start; Accipio offers to restart (downloads carry on afterwards). It is used only while it is newer than the copy inside the exe: a later Accipio build with a newer yt-dlp wins, and the old update is deleted.
- **Help > About** shows the yt-dlp version in use, and `yt-dlp\in-use.txt` says which one was loaded and from where.
- **The "Run" field in the rules editor is hidden** until commands actually run. A command already saved with a rule is kept.
- **The build works again:** `build_exe.bat` failed at `pip install .` because setuptools found both `accipio` and `extension` as packages; only `accipio` is installed now. The exe carries yt-dlp's version information so updates can be compared with it.

## 2026-10-29#05 -- A softer look

- **Rounded corners everywhere:** buttons, the paste bar, text fields, drop-downs and spin boxes, the tables and lists (in a rounded frame with a flat header), the group boxes in the rules editor, menus and tooltips. The filter buttons are pills, filled when chosen.
- **Progress is a rounded bar** with the percentage beside it: the accent colour while downloading, green when done, red when failed.
- **Quieter lines:** borders, hover shades and the slim rounded scrollbar are mixed from the theme's own background and text colours, so light mode is as soft as dark (the theme's standard border colour is near-black in light mode). Rows are a little taller.
- Every window gets it: the main window, the Add box, the rules editor and Preferences.

## 2026-10-29#04 -- Accipio's icon

- **The icon:** a white arrow landing in a tray, on a blue tile. It is on the exe, the window, the taskbar, the notification-area icon and the notifications, and on the browser extension's toolbar button and extensions page.
- `accipio/gui/appicon/accipio.svg` is the master; `python tools/make_icons.py` makes the .ico (seven sizes, each drawn from the SVG so the small ones stay sharp), a 256-pixel PNG and the extension's icons. They are committed, so a build needs no extra step.
- The exe no longer carries two Qt plugins whose libraries it leaves out.

## 2026-10-29#03 -- The Windows build

- **`build_exe.bat`** makes `dist\Accipio\Accipio.exe` with PyInstaller (fetching aria2 first if it is missing). Copy the whole `Accipio` folder; the exe needs the `_internal` folder beside it.
- A folder, not one file, on purpose: every download clicked in the browser starts the exe again as the browser link, and a one-file exe would unpack itself to a temp folder on each click. The link answers in about 0.3 seconds.
- About 85 MB: the Qt parts Accipio never uses (video codecs, PDF, software OpenGL, translations) are left out.
- aria2 and its licence texts are inside; ffmpeg is not bundled (one in an `ffmpeg` folder next to the exe, on PATH or in the usual install folders is used).
- Tested on the built exe: the window, a direct download (aria2), a YouTube video with picture and sound joined (yt-dlp + ffmpeg), the browser link, and a second start handing over to the first.

## 2026-10-29#02 -- Renaming: filters and pieces of the old name

- **Filters on any token**, after a `|`, run left to right so a messy name can be trimmed step by step: `{name|strip:www.SiteRips.org|dots|cut:\b(1080p|WEB-DL|x264-GRP)\b|trim|title}.{ext}` turns `www.SiteRips.org_-_the_last_ship.2014.1080p.WEB-DL.x264-GRP.mkv` into `The Last Ship 2014.mkv`. The filters: `lower`, `upper`, `title`, `dots` (dots and underscores to spaces), `trim`, `replace:old:new`, `strip:a:b:…`, `cut:regex`, `sub:regex:new` (with `\1`), `before:text`, `after:text`, `max:80`, `default:text`. They work in folders and subfolders too. Hover the Folder, Subfolder or Rename field in the rules editor for the list.
- **Pieces of the old name as tokens:** a regular expression in a rule's name (or video) condition hands its groups to that rule as `{1}`, `{2}`, … or by name: with `^(?P<show>.+?)[ ._]S(?P<season>\d+)E(\d+)`, the folder `TV\{show|dots}\Season {season}` and the name `{show|dots} - S{season}E{3}.{ext}` file `The.Show.S01E05.1080p.WEB.mkv` as `TV\The Show\Season 01\The Show - S01E05.mkv`.
- **Renames chain:** a rule that renames and continues hands the new name to the rules below, so one rule can clean names and the next add a date.
- The rules editor says what is wrong in plain words: an unknown filter, the wrong number of arguments, a broken regular expression, or a piece no pattern captures. The test box shows the final name as you type.
- A `|` inside brackets, as in `(1080p|720p)`, needs no escape; write `\:` for a `:` inside an argument.

## 2026-10-29#01 -- Notifications: added, finished, failed

- **Windows notifications** tell you when downloads are added, finish or fail, so you need not keep the window open. Accipio puts an icon in the notification area for them (click it to bring up the window; right-click for Show Accipio and Exit).
- **Batches become one notice:** downloads of one kind that happen within a second and a half of each other are told together ("5 downloads added", with the first three names), so a playlist or a pile of links never floods the screen.
- **Click a "Download finished" notice** to see the file in its folder; any other notice brings up the window.
- "Download added" is not shown while Accipio's window is in front, since you just added it there. Nothing already in the list at start is announced.
- **Tools > Preferences > Notify when:** Added, Finished, Failed, each on or off (all on by default).

## 2026-10-28#08 -- Fix: the browser link could hang

- **A connection that stalled while proving its key held up every connection after it**, so the browser, a second start of Accipio, or Accipio's own tests could wait forever (it froze one test run). The key is now checked on each connection's own thread, and a caller gives up after 10 seconds instead of waiting for ever. A test that hangs on the old code pins it.
- A test stuck for two minutes now prints where every thread is, instead of hanging silently.

## 2026-10-28#07 -- The Add box: check links before adding them

- **File > Add Links… (Ctrl+N), or Shift+Enter in the paste bar**, opens the Add box with the links from the bar (or the clipboard). Plain Enter still adds at once.
- Every link is checked with its server or video site while the box is open, and gets a row: name, folder (shown under the base folder), the rule that placed it, and Ready with the size (and "video" or "N videos" for a playlist), or the server's error. Links that fail are greyed out and left out; untick any you do not want. Hover a name for "why here?".
- **Double-click a folder** to send that one link somewhere else, or **Put all in** one folder for the whole lot. A folder chosen here is kept even if the rules would move the file later.
- Edit the links in the box and only the new ones are checked; removed ones disappear. The button says how many will be added.

## 2026-10-28#06 -- The Chrome extension: clicked downloads go to Accipio

- **The extension** (folder `extension`, loaded in Chrome or Edge as an unpacked extension) takes over the downloads you click: Chrome's download is paused, offered to Accipio, and cancelled only once Accipio has it. If Accipio cannot be reached, Chrome simply carries on, so a click is never lost. Cookies, the page address and the browser's identity go along, so downloads behind a login work. Incognito downloads and `blob:`/`data:` links stay with the browser.
- **Right-click:** "Download with Accipio" on links, pictures, video and audio; "Download this page's video with Accipio" anywhere on a page; and "Download with Chrome instead" on a link, for the times you want the browser to keep one (extensions are not told which keys were held during a download, so this menu item and the toolbar switch take the place of a modifier key).
- **The toolbar button** shows whether Accipio is ready, switches the takeover on and off, and sends the page you are on to yt-dlp.
- **Accipio starts itself:** a download clicked while Accipio is closed starts it and arrives once it is up.
- **Only one Accipio:** starting it a second time brings the running one to the front.
- At every start Accipio registers its native messaging host for Chrome and Edge, for this user only (no admin). The browser reaches the app over a local pipe that needs a key from Accipio's own folder, so no other program can send downloads in.
- ffmpeg is now found the same way the Redactor apps find it (bundled copy, PATH, or the usual install folders).

## 2026-10-28#05 -- Video sites through yt-dlp

- **Paste a YouTube (or Vimeo, or any of yt-dlp's sites) link and it downloads the video.** Accipio asks the site first for the title, channel, uploader and playlist, so the file is named after the title and the rules can place it: the default rule puts it in `Video\{uploader}`, and a rule can test the channel, uploader, playlist or title, or use them in a folder (`Video\{channel}\{yyyy}`). A plain file link still goes to aria2; only real video sites go to yt-dlp.
- **Playlists:** a playlist link becomes one download per video, and `{playlist}` can name their folder.
- **Quality** (Tools > Preferences > Video sites): Best, Up to 1080p, Up to 720p, or Audio only (mp3). At the same resolution H.264 in mp4 is preferred, because it plays everywhere; YouTube otherwise often hands out AV1, which Windows cannot play without an extra codec.
- **ffmpeg** joins the separate picture and sound most sites send. Accipio uses one in `bin\ffmpeg`, or the one on PATH; without it you get the best single file and audio is not converted to mp3 (the Preferences say which).
- **Pause and resume** work for videos too (yt-dlp continues its `.part` file), and video downloads still running or paused when Accipio closes pick up again at the next start. Removing an unfinished video deletes only its half-finished parts.
- Tested for real on YouTube ("Me at the zoo": probed in 2.6 s, merged picture and sound, placed in `Video\jawed`).

## 2026-10-28#04 -- The window and the rules editor: Accipio can be used

- **Start it with `python -m accipio`.** Its settings, rules, queue and window layout live in `%APPDATA%\Accipio`.
- **The main window** (the compact layout): paste one or many links in the bar (or drop them on the window) and press Enter or Add. The filter buttons All / Active / Done / Failed show counts, the total speed is on the right, and the table has Name, Size, Progress, Speed, Time left, Folder (shown under the base folder), Rule and Status. Every column can be resized and sorted, and the widths are remembered.
- **"Why here?"** shows when you hover a download (and in Downloads > Why Here?, Ctrl+I): the rules that placed it, and the error if it failed. Double-click a finished download to open it; Downloads > Show in Folder opens its folder.
- **Pause / resume** with Space, **Retry** with Ctrl+R, **Remove from List** with Delete (an unfinished download asks first, and finished files are never deleted), **Clear Finished** in the File menu.
- **The rules editor** (Tools > Rules…, or the Rules… button): the rules on the left, dragged into order, each marked → (continue) or ■ (stop); New, Duplicate, Delete. On the right the selected rule: name, enabled, one row per condition (domain, file type with a Groups menu, name, size, time, source, video field), then folder with Browse, subfolder, rename, stop or continue, and what is wrong with the rule in plain words. The **time condition** has weekday buttons and an hour range that read back as a sentence ("Weekdays, 08:00–16:00"), with the cron lines in an Advanced field.
- **The test box** at the bottom shows, as you type a link, the final path, the rules that hit and the ones skipped, using your unsaved edits. Saved rules apply to links added afterwards.
- **Settings** (Tools > Preferences…, Ctrl+,): base folder, downloads at once, connections per file, total speed limit, and history (off by default).
- The window never freezes: every queue operation runs on one worker thread in order. The menus follow the family's standard layout (File, Edit, View, Downloads, Tools, Help) and pass its menu check.

## 2026-10-28#03 -- The download queue (no window yet)

- **The queue ties it together:** a link is checked with the server, placed by the rules and handed to aria2; a timer brings progress, speed and state up to date. It lives in one SQLite file, so it survives a crash or a restart.
- **No two downloads share a file:** a name is free only if no file has it AND no other unfinished download is heading for it, so two `setup.exe` downloads at once become `setup.exe` and `setup (1).exe`.
- **Files whose type was unknown are looked at when they finish.** A `download` or `file.bin` whose first bytes show a PDF, ZIP, EXE, picture, video, epub, comic (cbz) or Office file gets that extension and goes through the rules again, which may move it (from Other to Documents, say); "why here?" says so. A file with a known extension is never second-guessed, and a folder chosen by hand in the Add box is kept.
- **A server that cannot resume** is started over by itself after a pause, up to three times, before it shows as an error.
- **Retry** a failed download: one that never got a name is checked and placed again; one whose file name was taken in the meantime gets a free name.
- **Removing** an unfinished download deletes its half-finished file, but only when aria2's control file beside it proves it is Accipio's. A finished file is never touched.
- **After a restart**, downloads the engine no longer has, or that were being checked when Accipio closed, show as errors you can retry.
- **Privacy:** cookies and other headers sent with a download are wiped when it finishes. Finished downloads are forgotten at the next start unless history is turned on (off by default).

## 2026-10-28#02 -- The download engine (aria2) and the link check (no window yet)

- **aria2 runs the direct downloads.** Accipio starts its own aria2c on 127.0.0.1 with a random port and secret, and aria2c stops by itself if Accipio crashes. Downloads go to the folder and name the rules chose; the folder is created when needed. Pause, resume, remove, the number of downloads at once and a total speed limit work while downloads run. Unfinished downloads are saved and come back after a restart.
- **Nothing is ever overwritten.** A file that is already there makes the download fail instead. aria2's own "continue" mode stays off: with it on, aria2 took a file you already had for a half-finished download and wrote the new file into it (caught by a test before any release).
- **Servers that cannot resume:** resuming one after a pause fails with "resume not supported"; the engine can then start it over from zero, removing only its own half-finished file.
- **Closing is instant:** aria2c on Windows takes about four seconds to exit, so Accipio saves the queue first and lets aria2 finish on its own.
- **The link check** asks the server, before downloading, for the real file name, type and size: the name from the server's Content-Disposition (including UTF-8 names like `æøå.epub`), else from the address after redirects. A script link gets the extension of what it serves (`get.php` that is a zip becomes `get.zip`), and a name with folders in it is cut to the name. Servers that refuse a HEAD request are asked for one byte instead.
- `python tools/fetch_engines.py` fetches aria2 1.37.0 from its official release into `bin/`, checked against a pinned SHA-256, with its licence texts.

## 2026-10-28#01 -- The rules engine and cron time windows (no window yet)

- **Rules decide where a download goes.** An ordered list; a rule whose conditions all match sets a folder, adds a subfolder, renames the file and/or queues a command, then stops or lets the rules below add to it (a domain rule sets the base folder, a type rule adds the subfolder).
- **Conditions:** domain (of the file or of the page it was clicked on; `example.com` covers its subdomains), file type (groups such as Video or Ebooks, extensions, MIME types; a known extension wins over the MIME type the server sends), name (wildcards or regular expressions), size, time, direct download or video site, and the video's channel, uploader, playlist or title.
- **Time windows are cron lines underneath.** The picker's "weekdays 08:30–16:15" becomes exact cron lines, and lines read back as a sentence ("Weekdays, 08:30–16:15"). A window past midnight carries on into the next day. Unlike classic cron, a day-of-month AND a weekday must both match, so "Monday, the 1st to the 7th" means the first Monday.
- **Folder tokens** `{yyyy} {MM} {dd} {week} {weekday} {domain} {pagedomain} {name} {ext} {channel} {uploader} {playlist} {title}` make folders rotate by date. A token's value is always one safe folder name: a channel called "AC/DC" or ".." cannot climb out of its folder, and Windows' reserved names are avoided.
- **"Why here?"** lists the rules that hit and what each did; the Rule column shows them as `1+5`. Name clashes become `name (1).ext`, never an overwrite.
- **The default rule set** sorts video sites by uploader, then Video, Audio, Images, Books (ebooks and comics), Documents, Archives, Programs, and Other.
- Rules are stored as JSON; keys from a newer version are skipped rather than refused. The editor can ask a rule what is wrong with it in plain words.
